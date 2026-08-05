import { resolveSponsorCallbackUrl } from "./callback-url.ts";

const CIRCUIT_KEY = "primary";
const FAILURE_THRESHOLD = 2;
const OPEN_INTERVAL_MS = 60_000;
const PROBE_INTERVAL_MS = 15_000;
const PROBE_TIMEOUT_MS = 1_800;

export type CallbackCircuitState = "closed" | "open";

type CallbackCircuitRow = {
  callback_url: string;
  state: CallbackCircuitState | string;
  failure_count: number;
  opened_until: number;
  probe_lease_until: number;
  last_checked_at: number;
  last_success_at: number;
};

export type CallbackCircuitSnapshot = {
  state: CallbackCircuitState;
  failureCount: number;
  fallbackActive: boolean;
  lastCheckedAt: number;
  lastSuccessAt: number;
};

export type CallbackDecision = CallbackCircuitSnapshot & {
  url: string;
};

export function resolveSponsorCallbackHealthUrl(
  configuredHealthUrl: string | undefined,
  callbackUrl: string,
): string {
  const callback = new URL(callbackUrl);
  const candidate = configuredHealthUrl?.trim()
    ? new URL(configuredHealthUrl.trim())
    : new URL("/yungouos/health", callback);
  if (
    candidate.protocol !== "https:" ||
    candidate.username ||
    candidate.password ||
    candidate.port ||
    candidate.search ||
    candidate.hash ||
    candidate.origin !== callback.origin
  ) {
    throw new Error("PAYMENT_CALLBACK_HEALTH_URL_INVALID");
  }
  return candidate.toString();
}

export function chooseSponsorCallback(
  preferredUrl: string,
  fallbackUrl: string,
  row: CallbackCircuitRow | null,
  now = Date.now(),
): CallbackDecision {
  const state: CallbackCircuitState = row?.state === "open" ? "open" : "closed";
  const sameTarget = preferredUrl === fallbackUrl;
  const fallbackActive =
    !sameTarget &&
    state === "open" &&
    Number(row?.opened_until ?? 0) > now;
  return {
    url: fallbackActive ? fallbackUrl : preferredUrl,
    state,
    failureCount: Math.max(0, Number(row?.failure_count ?? 0)),
    fallbackActive,
    lastCheckedAt: Math.max(0, Number(row?.last_checked_at ?? 0)),
    lastSuccessAt: Math.max(0, Number(row?.last_success_at ?? 0)),
  };
}

export async function selectSponsorCallback(input: {
  database: D1Database;
  configuredUrl?: string;
  requestUrl: string;
  now?: number;
}): Promise<CallbackDecision> {
  const preferredUrl = resolveSponsorCallbackUrl(input.configuredUrl, input.requestUrl);
  const fallbackUrl = resolveSponsorCallbackUrl(undefined, input.requestUrl);
  if (preferredUrl === fallbackUrl) {
    return chooseSponsorCallback(preferredUrl, fallbackUrl, null, input.now);
  }
  await ensureCircuitRow(input.database, preferredUrl);
  const row = await readCircuitRow(input.database, preferredUrl);
  return chooseSponsorCallback(preferredUrl, fallbackUrl, row, input.now);
}

export async function getSponsorCallbackCircuitSnapshot(input: {
  database: D1Database;
  configuredUrl?: string;
  requestUrl: string;
  now?: number;
}): Promise<CallbackCircuitSnapshot> {
  const decision = await selectSponsorCallback(input);
  return {
    state: decision.state,
    failureCount: decision.failureCount,
    fallbackActive: decision.fallbackActive,
    lastCheckedAt: decision.lastCheckedAt,
    lastSuccessAt: decision.lastSuccessAt,
  };
}

export async function probeSponsorCallbackCircuit(input: {
  database: D1Database;
  configuredUrl?: string;
  configuredHealthUrl?: string;
  requestUrl: string;
  fetcher?: typeof fetch;
  now?: () => number;
}): Promise<void> {
  const preferredUrl = resolveSponsorCallbackUrl(input.configuredUrl, input.requestUrl);
  const fallbackUrl = resolveSponsorCallbackUrl(undefined, input.requestUrl);
  if (preferredUrl === fallbackUrl) return;

  const now = input.now ?? Date.now;
  const checkedAt = now();
  await ensureCircuitRow(input.database, preferredUrl);
  const lease = await input.database
    .prepare(
      `UPDATE sponsor_callback_circuit
       SET probe_lease_until = ?
       WHERE id = ? AND callback_url = ? AND probe_lease_until <= ?
       RETURNING callback_url`,
    )
    .bind(
      checkedAt + PROBE_INTERVAL_MS,
      CIRCUIT_KEY,
      preferredUrl,
      checkedAt,
    )
    .first<{ callback_url: string }>();
  if (!lease) return;

  const healthUrl = resolveSponsorCallbackHealthUrl(
    input.configuredHealthUrl,
    preferredUrl,
  );
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  let healthy = false;
  try {
    const response = await (input.fetcher ?? fetch)(healthUrl, {
      method: "GET",
      headers: { accept: "text/plain, application/json" },
      cache: "no-store",
      signal: controller.signal,
    });
    const body = (await response.text()).trim().slice(0, 128);
    healthy =
      response.ok &&
      (body === "OK" ||
        body === "SUCCESS" ||
        /^\{\s*"ok"\s*:\s*true\s*[,}]/.test(body));
  } catch {
    healthy = false;
  } finally {
    clearTimeout(timeout);
  }

  const completedAt = now();
  if (healthy) {
    await input.database
      .prepare(
        `UPDATE sponsor_callback_circuit
         SET state = 'closed', failure_count = 0, opened_until = 0,
             probe_lease_until = ?, last_checked_at = ?, last_success_at = ?
         WHERE id = ? AND callback_url = ?`,
      )
      .bind(
        completedAt + PROBE_INTERVAL_MS,
        completedAt,
        completedAt,
        CIRCUIT_KEY,
        preferredUrl,
      )
      .run();
    return;
  }

  await input.database
    .prepare(
      `UPDATE sponsor_callback_circuit
       SET failure_count = failure_count + 1,
           state = CASE
             WHEN failure_count + 1 >= ? THEN 'open'
             ELSE state
           END,
           opened_until = CASE
             WHEN failure_count + 1 >= ? THEN ?
             ELSE opened_until
           END,
           probe_lease_until = ?, last_checked_at = ?
       WHERE id = ? AND callback_url = ?`,
    )
    .bind(
      FAILURE_THRESHOLD,
      FAILURE_THRESHOLD,
      completedAt + OPEN_INTERVAL_MS,
      completedAt + PROBE_INTERVAL_MS,
      completedAt,
      CIRCUIT_KEY,
      preferredUrl,
    )
    .run();
}

async function ensureCircuitRow(
  database: D1Database,
  callbackUrl: string,
): Promise<void> {
  await database
    .prepare(
      `INSERT INTO sponsor_callback_circuit (
         id, callback_url, state, failure_count, opened_until,
         probe_lease_until, last_checked_at, last_success_at
       ) VALUES (?, ?, 'closed', 0, 0, 0, 0, 0)
       ON CONFLICT(id) DO UPDATE SET
         callback_url = excluded.callback_url,
         state = CASE
           WHEN sponsor_callback_circuit.callback_url = excluded.callback_url
           THEN sponsor_callback_circuit.state ELSE 'closed' END,
         failure_count = CASE
           WHEN sponsor_callback_circuit.callback_url = excluded.callback_url
           THEN sponsor_callback_circuit.failure_count ELSE 0 END,
         opened_until = CASE
           WHEN sponsor_callback_circuit.callback_url = excluded.callback_url
           THEN sponsor_callback_circuit.opened_until ELSE 0 END,
         probe_lease_until = CASE
           WHEN sponsor_callback_circuit.callback_url = excluded.callback_url
           THEN sponsor_callback_circuit.probe_lease_until ELSE 0 END,
         last_checked_at = CASE
           WHEN sponsor_callback_circuit.callback_url = excluded.callback_url
           THEN sponsor_callback_circuit.last_checked_at ELSE 0 END,
         last_success_at = CASE
           WHEN sponsor_callback_circuit.callback_url = excluded.callback_url
           THEN sponsor_callback_circuit.last_success_at ELSE 0 END`,
    )
    .bind(CIRCUIT_KEY, callbackUrl)
    .run();
}

async function readCircuitRow(
  database: D1Database,
  callbackUrl: string,
): Promise<CallbackCircuitRow | null> {
  return database
    .prepare(
      `SELECT callback_url, state, failure_count, opened_until,
              probe_lease_until, last_checked_at, last_success_at
       FROM sponsor_callback_circuit
       WHERE id = ? AND callback_url = ?`,
    )
    .bind(CIRCUIT_KEY, callbackUrl)
    .first<CallbackCircuitRow>();
}
