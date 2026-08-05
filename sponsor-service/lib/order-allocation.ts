export const PRESET_SPONSOR_AMOUNTS_CENTS = [500, 1_000, 2_000, 5_000, 10_000] as const;

export type SponsorOrderStatus =
  | "creating"
  | "pending"
  | "paid"
  | "expired"
  | "closed"
  | "failed";

export type SponsorOrderRow = {
  id: string;
  provider_order_no: string;
  amount_cents: number;
  status: SponsorOrderStatus | string;
  qr_url: string;
  app_version: string;
  created_at: number;
  expires_at: number;
  install_id: string | null;
  checkout_intent_id: string | null;
  reserved_at: number | null;
  state_version: number;
};

export type SponsorReservation = {
  installId: string;
  checkoutIntentId: string;
  amountCents: number;
  amount: string;
  appVersion: string;
};

export type NewSponsorOrder = SponsorReservation & {
  id: string;
  providerOrderNo: string;
  createdAt: number;
  expiresAt: number;
};

export interface SponsorOrderStore {
  findReserved(input: SponsorReservation): Promise<SponsorOrderRow | null>;
  claimPooled(
    input: SponsorReservation,
    reservedAt: number,
    minimumExpiresAt: number,
  ): Promise<SponsorOrderRow | null>;
  beginCreating(input: NewSponsorOrder): Promise<boolean>;
  completeCreating(id: string, qrValue: string): Promise<SponsorOrderRow>;
  failCreating(id: string): Promise<void>;
  deleteFailed(input: SponsorReservation): Promise<void>;
}

export type PaymentFactory = (input: NewSponsorOrder) => Promise<string>;

export type ReserveOptions = {
  now?: () => number;
  createId?: () => string;
  createProviderOrderNo?: () => string;
  orderLifetimeMs: number;
  expiryGuardMs: number;
  waitForConcurrentMs?: number;
  waitIntervalMs?: number;
};

export type ReservedSponsorOrder = {
  order: SponsorOrderRow;
  source: "existing" | "pool" | "created" | "concurrent";
};

/**
 * Reserves exactly one payment order for one installation and checkout intent.
 *
 * The store performs the pool claim and the creation placeholder insert with
 * atomic SQL.  This makes the checkout intent the idempotency boundary: two
 * concurrent requests from the same installation converge on one order, while
 * different installations can never receive the same pooled order.
 */
export async function reserveSponsorOrder(
  store: SponsorOrderStore,
  input: SponsorReservation,
  createPayment: PaymentFactory,
  options: ReserveOptions,
): Promise<ReservedSponsorOrder> {
  const now = options.now ?? Date.now;
  let existing = await store.findReserved(input);
  if (existing?.status === "failed") {
    // A transient provider failure must not poison this idempotency key
    // forever.  Remove only the failed placeholder, then let the normal
    // atomic claim/create path elect one retrying request as the owner.
    await store.deleteFailed(input);
    existing = await store.findReserved(input);
  }
  if (existing) {
    return resolveExisting(store, input, existing, options, now);
  }

  try {
    const claimed = await store.claimPooled(
      input,
      now(),
      now() + options.expiryGuardMs,
    );
    if (claimed) return { order: claimed, source: "pool" };
  } catch {
    // A concurrent claim for the same unique checkout intent can lose on the
    // unique index.  Re-read that exact intent instead of allocating a second
    // payment order.
    const winner = await store.findReserved(input);
    if (winner) {
      return resolveExisting(store, input, winner, options, now);
    }
    throw new Error("SPONSOR_POOL_CLAIM_FAILED");
  }

  const createdAt = now();
  const draft: NewSponsorOrder = {
    ...input,
    id: (options.createId ?? defaultId)(),
    providerOrderNo: (options.createProviderOrderNo ?? defaultProviderOrderNo)(),
    createdAt,
    expiresAt: createdAt + options.orderLifetimeMs,
  };
  const ownsCreation = await store.beginCreating(draft);
  if (!ownsCreation) {
    const concurrent = await waitForReadyOrder(store, input, options, now);
    return { order: concurrent, source: "concurrent" };
  }

  try {
    const qrValue = await createPayment(draft);
    if (!qrValue) throw new Error("PAYMENT_PROVIDER_MISSING_QR");
    return {
      order: await store.completeCreating(draft.id, qrValue),
      source: "created",
    };
  } catch (error) {
    await store.failCreating(draft.id);
    throw error;
  }
}

export async function reserveSponsorOrders(
  store: SponsorOrderStore,
  inputs: readonly SponsorReservation[],
  createPayment: PaymentFactory,
  options: ReserveOptions,
): Promise<ReservedSponsorOrder[]> {
  return Promise.all(
    inputs.map((input) => reserveSponsorOrder(store, input, createPayment, options)),
  );
}

async function resolveExisting(
  store: SponsorOrderStore,
  input: SponsorReservation,
  existing: SponsorOrderRow,
  options: ReserveOptions,
  now: () => number,
): Promise<ReservedSponsorOrder> {
  if (existing.status === "creating") {
    return {
      order: await waitForReadyOrder(store, input, options, now),
      source: "concurrent",
    };
  }
  if (existing.status === "failed") {
    throw new Error("SPONSOR_ORDER_CREATION_FAILED");
  }
  return { order: existing, source: "existing" };
}

async function waitForReadyOrder(
  store: SponsorOrderStore,
  input: SponsorReservation,
  options: ReserveOptions,
  now: () => number,
): Promise<SponsorOrderRow> {
  const timeoutMs = options.waitForConcurrentMs ?? 5_000;
  const intervalMs = options.waitIntervalMs ?? 35;
  const deadline = now() + timeoutMs;
  do {
    const order = await store.findReserved(input);
    if (order && order.status !== "creating") {
      if (order.status === "failed") {
        throw new Error("SPONSOR_ORDER_CREATION_FAILED");
      }
      return order;
    }
    await sleep(intervalMs);
  } while (now() < deadline);
  throw new Error("SPONSOR_ORDER_CREATION_TIMEOUT");
}

function defaultId(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

function defaultProviderOrderNo(): string {
  const stamp = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
  const random = crypto.randomUUID().replaceAll("-", "").slice(0, 12).toUpperCase();
  return `SP${stamp}${random}`;
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
