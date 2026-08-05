import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  reserveSponsorOrder,
  reserveSponsorOrders,
} from "../lib/order-allocation.ts";

const baseOptions = {
  now: () => 1_000_000,
  orderLifetimeMs: 6_600_000,
  expiryGuardMs: 60_000,
  waitForConcurrentMs: 1_000,
  waitIntervalMs: 1,
};

class MemoryOrderStore {
  constructor(rows = []) {
    this.rows = rows.map((row) => ({ ...row }));
  }

  async findReserved(input) {
    return (
      this.rows.find(
        (row) =>
          row.install_id === input.installId &&
          row.checkout_intent_id === input.checkoutIntentId &&
          row.amount_cents === input.amountCents,
      ) ?? null
    );
  }

  async claimPooled(input, reservedAt, minimumExpiresAt) {
    if (await this.findReserved(input)) {
      throw new Error("UNIQUE constraint failed");
    }
    const row = this.rows.find(
      (candidate) =>
        candidate.amount_cents === input.amountCents &&
        candidate.status === "pending" &&
        candidate.install_id === null &&
        candidate.checkout_intent_id === null &&
        candidate.expires_at > minimumExpiresAt,
    );
    if (!row) return null;
    row.install_id = input.installId;
    row.checkout_intent_id = input.checkoutIntentId;
    row.reserved_at = reservedAt;
    row.app_version = input.appVersion;
    row.state_version += 1;
    return { ...row };
  }

  async beginCreating(input) {
    if (
      this.rows.some(
        (row) =>
          row.install_id === input.installId &&
          row.checkout_intent_id === input.checkoutIntentId &&
          row.amount_cents === input.amountCents,
      )
    ) {
      return false;
    }
    this.rows.push({
      id: input.id,
      provider_order_no: input.providerOrderNo,
      amount_cents: input.amountCents,
      status: "creating",
      qr_url: "",
      app_version: input.appVersion,
      created_at: input.createdAt,
      expires_at: input.expiresAt,
      install_id: input.installId,
      checkout_intent_id: input.checkoutIntentId,
      reserved_at: input.createdAt,
      state_version: 0,
    });
    return true;
  }

  async completeCreating(id, qrValue) {
    const row = this.rows.find((candidate) => candidate.id === id);
    assert.ok(row);
    row.qr_url = qrValue;
    if (row.status === "creating") row.status = "pending";
    row.state_version += 1;
    return { ...row };
  }

  async failCreating(id) {
    const row = this.rows.find((candidate) => candidate.id === id);
    if (row?.status === "creating") {
      row.status = "failed";
      row.state_version += 1;
    }
  }

  async deleteFailed(input) {
    this.rows = this.rows.filter(
      (row) =>
        !(
          row.install_id === input.installId &&
          row.checkout_intent_id === input.checkoutIntentId &&
          row.amount_cents === input.amountCents &&
          row.status === "failed"
        ),
    );
  }
}

function reservation(overrides = {}) {
  return {
    installId: "install-12345678",
    checkoutIntentId: "checkout-12345678",
    amount: "5.00",
    amountCents: 500,
    appVersion: "0.5.22",
    ...overrides,
  };
}

function pooledOrder(id) {
  return {
    id,
    provider_order_no: `provider-${id}`,
    amount_cents: 500,
    status: "pending",
    qr_url: `weixin://wxpay/${id}`,
    app_version: "pool",
    created_at: 900_000,
    expires_at: 2_000_000,
    install_id: null,
    checkout_intent_id: null,
    reserved_at: null,
    state_version: 0,
  };
}

test("atomically assigns different pooled orders to different installations", async () => {
  const store = new MemoryOrderStore([pooledOrder("pool-a"), pooledOrder("pool-b")]);
  const createPayment = async () => {
    throw new Error("pool should avoid provider call");
  };

  const [left, right] = await Promise.all([
    reserveSponsorOrder(store, reservation(), createPayment, baseOptions),
    reserveSponsorOrder(
      store,
      reservation({
        installId: "install-87654321",
        checkoutIntentId: "checkout-87654321",
      }),
      createPayment,
      baseOptions,
    ),
  ]);

  assert.equal(left.source, "pool");
  assert.equal(right.source, "pool");
  assert.notEqual(left.order.id, right.order.id);
  assert.equal(left.order.install_id, "install-12345678");
  assert.equal(right.order.install_id, "install-87654321");
});

test("concurrent retries for one checkout intent create exactly one provider order", async () => {
  const store = new MemoryOrderStore();
  let providerCalls = 0;
  let sequence = 0;
  const options = {
    ...baseOptions,
    createId: () => `order-${++sequence}`,
    createProviderOrderNo: () => `provider-${sequence}`,
  };
  const createPayment = async () => {
    providerCalls += 1;
    await new Promise((resolve) => setTimeout(resolve, 15));
    return "weixin://wxpay/idempotent";
  };

  const [left, right] = await Promise.all([
    reserveSponsorOrder(store, reservation(), createPayment, options),
    reserveSponsorOrder(store, reservation(), createPayment, options),
  ]);

  assert.equal(providerCalls, 1);
  assert.equal(left.order.id, right.order.id);
  assert.deepEqual(new Set([left.source, right.source]), new Set(["created", "concurrent"]));
});

test("five-tier batch starts provider reservations concurrently", async () => {
  const store = new MemoryOrderStore();
  const cents = [500, 1_000, 2_000, 5_000, 10_000];
  let started = 0;
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const createPayment = async (draft) => {
    started += 1;
    if (started === cents.length) release();
    await gate;
    return `weixin://wxpay/${draft.amountCents}`;
  };
  let sequence = 0;
  const results = await reserveSponsorOrders(
    store,
    cents.map((amountCents) =>
      reservation({
        amountCents,
        amount: (amountCents / 100).toFixed(2),
      }),
    ),
    createPayment,
    {
      ...baseOptions,
      createId: () => `batch-order-${++sequence}`,
      createProviderOrderNo: () => `batch-provider-${sequence}`,
    },
  );

  assert.equal(started, 5);
  assert.deepEqual(
    results.map(({ source }) => source),
    ["created", "created", "created", "created", "created"],
  );
  assert.deepEqual(
    results.map(({ order }) => order.amount_cents),
    cents,
  );
});

test("a transient provider failure can be retried with the same checkout intent", async () => {
  const store = new MemoryOrderStore();
  let providerCalls = 0;
  let sequence = 0;
  const options = {
    ...baseOptions,
    createId: () => `retry-order-${++sequence}`,
    createProviderOrderNo: () => `retry-provider-${sequence}`,
  };

  await assert.rejects(
    reserveSponsorOrder(
      store,
      reservation(),
      async () => {
        providerCalls += 1;
        throw new Error("temporary provider outage");
      },
      options,
    ),
    /temporary provider outage/,
  );

  const retried = await reserveSponsorOrder(
    store,
    reservation(),
    async () => {
      providerCalls += 1;
      return "weixin://wxpay/recovered";
    },
    options,
  );

  assert.equal(providerCalls, 2);
  assert.equal(retried.source, "created");
  assert.equal(retried.order.status, "pending");
  assert.equal(retried.order.qr_url, "weixin://wxpay/recovered");
});

test("migration and D1 store enforce checkout uniqueness and atomic pool claim", async () => {
  const [migration, storeSource, callbackSource] = await Promise.all([
    readFile(new URL("../drizzle/0002_acoustic_mystique.sql", import.meta.url), "utf8"),
    readFile(new URL("../lib/order-store.ts", import.meta.url), "utf8"),
    readFile(
      new URL("../app/api/sponsor/callback/route.ts", import.meta.url),
      "utf8",
    ),
  ]);
  assert.match(migration, /ADD `install_id` text/);
  assert.match(migration, /ADD `checkout_intent_id` text/);
  assert.match(migration, /CREATE UNIQUE INDEX `sponsor_orders_checkout_unique`/);
  assert.match(storeSource, /UPDATE sponsor_orders[\s\S]+RETURNING/);
  assert.match(storeSource, /install_id IS NULL AND checkout_intent_id IS NULL/);
  assert.match(callbackSource, /WHERE provider_order_no = \? LIMIT 1/);
  assert.match(callbackSource, /state_version = state_version \+ 1/);
});

test("worker refills an authenticated unbound preset pool after responding", async () => {
  const [workerSource, refillSource, runtimeSource] = await Promise.all([
    readFile(new URL("../worker/index.ts", import.meta.url), "utf8"),
    readFile(
      new URL("../app/api/sponsor/pool/refill/route.ts", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../lib/runtime.ts", import.meta.url), "utf8"),
  ]);
  assert.match(workerSource, /ctx\.waitUntil\(/);
  assert.match(workerSource, /\/api\/sponsor\/pool\/refill/);
  assert.match(workerSource, /SPONSOR_POOL_REFILL_TOKEN/);
  assert.match(refillSource, /TARGET_PER_AMOUNT = 2/);
  assert.match(refillSource, /NULL, NULL, NULL, 0/);
  assert.match(refillSource, /Promise\.allSettled/);
  assert.match(runtimeSource, /value\.length < 32/);
});

test("paid-state reconciliation is rate limited per order instead of globally", async () => {
  const [routeSource, migration] = await Promise.all([
    readFile(
      new URL("../app/api/sponsor/orders/[orderId]/route.ts", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../drizzle/0003_eminent_maginty.sql", import.meta.url), "utf8"),
  ]);
  assert.match(routeSource, /SET provider_checked_at = \?/);
  assert.match(routeSource, /WHERE id = \? AND status = 'pending'/);
  assert.doesNotMatch(routeSource, /key = 'order-query'/);
  assert.match(migration, /ADD `provider_checked_at` integer DEFAULT 0 NOT NULL/);
});
