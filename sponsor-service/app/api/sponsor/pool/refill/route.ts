import { ensureSponsorSchema } from "@/lib/database";
import { createNativePayment } from "@/lib/payment";
import { json, serviceError } from "@/lib/responses";
import {
  getPaymentCredentials,
  getSponsorCallbackUrl,
  getSponsorPoolRefillToken,
} from "@/lib/runtime";

const PRESET_AMOUNTS = [
  { amount: "5.00", amountCents: 500 },
  { amount: "10.00", amountCents: 1_000 },
  { amount: "20.00", amountCents: 2_000 },
  { amount: "50.00", amountCents: 5_000 },
  { amount: "100.00", amountCents: 10_000 },
] as const;
const TARGET_PER_AMOUNT = 2;
const ORDER_LIFETIME_MS = 110 * 60 * 1000;
const EXPIRY_GUARD_MS = 5 * 60 * 1000;
const REFILL_INTERVAL_MS = 15_000;

export async function POST(request: Request): Promise<Response> {
  try {
    if (!authorized(request)) {
      return json({ ok: false, error: "not found" }, { status: 404 });
    }
    const database = await ensureSponsorSchema();
    const now = Date.now();
    if (!(await acquireRefillSlot(database, now))) {
      return json({ ok: true, data: { created: 0, deferred: true } });
    }

    await database
      .prepare(
        `UPDATE sponsor_orders SET status = 'expired', state_version = state_version + 1
         WHERE status = 'pending' AND install_id IS NULL AND expires_at <= ?`,
      )
      .bind(now)
      .run();

    const counts = await Promise.all(
      PRESET_AMOUNTS.map(async ({ amountCents }) => {
        const row = await database
          .prepare(
            `SELECT COUNT(*) AS count FROM sponsor_orders
             WHERE amount_cents = ? AND status = 'pending'
               AND install_id IS NULL AND checkout_intent_id IS NULL
               AND expires_at > ?`,
          )
          .bind(amountCents, now + EXPIRY_GUARD_MS)
          .first<{ count: number }>();
        return Number(row?.count ?? 0);
      }),
    );

    const drafts = PRESET_AMOUNTS.flatMap((preset, index) =>
      Array.from(
        { length: Math.max(0, TARGET_PER_AMOUNT - counts[index]) },
        () => ({ ...preset, id: crypto.randomUUID().replaceAll("-", "") }),
      ),
    );
    const { merchantId, paymentKey } = getPaymentCredentials();
    const notifyUrl = getSponsorCallbackUrl(request.url);
    const results = await Promise.allSettled(
      drafts.map(async (draft) => {
        const providerOrderNo = createProviderOrderNo();
        const payment = await createNativePayment({
          providerOrderNo,
          amount: draft.amount,
          body: "守望先锋B站直播挂宝赞助",
          merchantId,
          paymentKey,
          notifyUrl,
          attach: `sponsor:${draft.id}`,
        });
        const qrValue = payment.qrContent || payment.qrImageUrl;
        if (!qrValue) throw new Error("PAYMENT_PROVIDER_MISSING_QR");
        await database
          .prepare(
            `INSERT INTO sponsor_orders (
               id, provider_order_no, amount_cents, status, qr_url,
               app_version, created_at, expires_at, install_id,
               checkout_intent_id, reserved_at, state_version
             ) VALUES (?, ?, ?, 'pending', ?, 'pool-v0.5.23', ?, ?, NULL, NULL, NULL, 0)`,
          )
          .bind(
            draft.id,
            providerOrderNo,
            draft.amountCents,
            qrValue,
            now,
            now + ORDER_LIFETIME_MS,
          )
          .run();
      }),
    );
    const created = results.filter((result) => result.status === "fulfilled").length;
    return json({
      ok: true,
      data: { created, failed: results.length - created, target: TARGET_PER_AMOUNT },
    });
  } catch (error) {
    return serviceError(error);
  }
}

function authorized(request: Request): boolean {
  const header = request.headers.get("authorization") ?? "";
  const expected = `Bearer ${getSponsorPoolRefillToken()}`;
  if (header.length !== expected.length) return false;
  let difference = 0;
  for (let index = 0; index < expected.length; index += 1) {
    difference |= header.charCodeAt(index) ^ expected.charCodeAt(index);
  }
  return difference === 0;
}

async function acquireRefillSlot(
  database: D1Database,
  now: number,
): Promise<boolean> {
  await database
    .prepare(
      "INSERT OR IGNORE INTO sponsor_provider_state (key, checked_at) VALUES ('pool-refill', 0)",
    )
    .run();
  const result = await database
    .prepare(
      `UPDATE sponsor_provider_state SET checked_at = ?
       WHERE key = 'pool-refill' AND checked_at <= ?`,
    )
    .bind(now, now - REFILL_INTERVAL_MS)
    .run();
  return Number(result.meta?.changes ?? 0) > 0;
}

function createProviderOrderNo(): string {
  const stamp = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
  const random = crypto.randomUUID().replaceAll("-", "").slice(0, 12).toUpperCase();
  return `SP${stamp}${random}`;
}
