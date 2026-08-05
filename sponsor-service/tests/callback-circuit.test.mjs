import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  chooseSponsorCallback,
  resolveSponsorCallbackHealthUrl,
} from "../lib/callback-circuit.ts";

const preferred = "https://api.codeboc.cn/yungouos/callback";
const fallback = "https://sponsor.example/api/sponsor/callback";

function row(overrides = {}) {
  return {
    callback_url: preferred,
    state: "closed",
    failure_count: 0,
    opened_until: 0,
    probe_lease_until: 0,
    last_checked_at: 100,
    last_success_at: 90,
    ...overrides,
  };
}

test("derives the Beijing health endpoint from the callback origin", () => {
  assert.equal(
    resolveSponsorCallbackHealthUrl(undefined, preferred),
    "https://api.codeboc.cn/yungouos/health",
  );
});

test("rejects a circuit probe on another origin", () => {
  assert.throws(
    () =>
      resolveSponsorCallbackHealthUrl(
        "https://attacker.example/health",
        preferred,
      ),
    /PAYMENT_CALLBACK_HEALTH_URL_INVALID/,
  );
});

test("open circuit routes new orders to the local callback", () => {
  const decision = chooseSponsorCallback(
    preferred,
    fallback,
    row({ state: "open", failure_count: 2, opened_until: 80_000 }),
    20_000,
  );
  assert.equal(decision.url, fallback);
  assert.equal(decision.fallbackActive, true);
  assert.equal(decision.state, "open");
});

test("expired open interval performs a half-open recovery attempt", () => {
  const decision = chooseSponsorCallback(
    preferred,
    fallback,
    row({ state: "open", failure_count: 2, opened_until: 10_000 }),
    20_000,
  );
  assert.equal(decision.url, preferred);
  assert.equal(decision.fallbackActive, false);
});

test("worker probes in the background without delaying the order response", async () => {
  const source = await readFile(new URL("../worker/index.ts", import.meta.url), "utf8");
  const responseIndex = source.indexOf("const response = await handler.fetch");
  const waitUntilIndex = source.indexOf("probeSponsorCallbackCircuit", responseIndex);
  assert.ok(responseIndex >= 0);
  assert.ok(waitUntilIndex > responseIndex);
  assert.match(source, /ctx\.waitUntil\(/);
});
