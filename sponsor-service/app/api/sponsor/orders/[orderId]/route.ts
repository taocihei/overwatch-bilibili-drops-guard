import { ensureSponsorSchema } from "@/lib/database";
import { queryPaymentOrder } from "@/lib/payment";
import { json, serviceError } from "@/lib/responses";
import { getPaymentCredentials } from "@/lib/runtime";

type OrderRow = {
  id: string;
  provider_order_no: string;
  amount_cents: number;
  status: string;
  expires_at: number;
  paid_at: number | null;
  payment_no: string | null;
  state_version: number;
  provider_checked_at: number;
};

const PROVIDER_QUERY_INTERVAL_MS = 11_000;

export async function GET(request: Request): Promise<Response> {
  try {
    const orderId = getLastPathSegment(request.url);
    if (!/^[a-f0-9]{32}$/.test(orderId)) {
      return json({ ok: false, error: "订单号无效。" }, { status: 400 });
    }

    const database = await ensureSponsorSchema();
    let order = await database
      .prepare(
        `SELECT id, provider_order_no, amount_cents, status,
                expires_at, paid_at, payment_no, state_version,
                provider_checked_at
         FROM sponsor_orders WHERE id = ? LIMIT 1`,
      )
      .bind(orderId)
      .first<OrderRow>();

    if (!order) {
      return json({ ok: false, error: "订单不存在。" }, { status: 404 });
    }

    if (order.status === "pending") {
      order = await reconcilePendingOrder(database, order);
    }

    if (order.status === "pending" && Date.now() >= order.expires_at) {
      await database
        .prepare(
          `UPDATE sponsor_orders
           SET status = 'expired', state_version = state_version + 1
           WHERE id = ? AND status = 'pending'`,
        )
        .bind(orderId)
        .run();
      order = {
        ...order,
        status: "expired",
        state_version: order.state_version + 1,
      };
    }

    return json({
      ok: true,
      data: {
        status: normalizeStatus(order.status),
        message: statusMessage(order.status),
        state_version: order.state_version,
        paid_at: order.paid_at
          ? new Date(order.paid_at).toISOString()
          : null,
      },
    });
  } catch (error) {
    return serviceError(error);
  }
}

async function reconcilePendingOrder(
  database: D1Database,
  order: OrderRow,
): Promise<OrderRow> {
  if (!(await acquireProviderQuerySlot(database, order.id))) return order;

  try {
    const { merchantId, paymentKey } = getPaymentCredentials();
    const providerOrder = await queryPaymentOrder({
      providerOrderNo: order.provider_order_no,
      merchantId,
      paymentKey,
    });
    if (!providerOrder.paid) return order;
    if (providerOrder.amountCents !== order.amount_cents) {
      console.warn("SPONSOR_RECONCILE_REJECTED", "AMOUNT_MISMATCH", order.id);
      return order;
    }

    const paidAt = Date.now();
    await database
      .prepare(
        `UPDATE sponsor_orders
         SET status = 'paid', paid_at = ?, payment_no = ?,
             state_version = state_version + 1
         WHERE id = ? AND status = 'pending'`,
      )
      .bind(paidAt, providerOrder.paymentNo, order.id)
      .run();
    return {
      ...order,
      status: "paid",
      paid_at: paidAt,
      payment_no: providerOrder.paymentNo,
      state_version: order.state_version + 1,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "UNKNOWN_ERROR";
    console.warn("SPONSOR_RECONCILE_DEFERRED", message, order.id);
    return order;
  }
}

async function acquireProviderQuerySlot(
  database: D1Database,
  orderId: string,
): Promise<boolean> {
  const now = Date.now();
  const result = await database
    .prepare(
      `UPDATE sponsor_orders
       SET provider_checked_at = ?
       WHERE id = ? AND status = 'pending' AND provider_checked_at <= ?`,
    )
    .bind(now, orderId, now - PROVIDER_QUERY_INTERVAL_MS)
    .run();
  return Number(result.meta?.changes ?? 0) > 0;
}

function getLastPathSegment(url: string): string {
  const pathname = new URL(url).pathname;
  return decodeURIComponent(pathname.split("/").filter(Boolean).at(-1) ?? "");
}

function normalizeStatus(
  value: string,
): "pending" | "paid" | "expired" | "closed" {
  if (value === "paid" || value === "expired" || value === "closed") {
    return value;
  }
  return "pending";
}

function statusMessage(value: string): string {
  if (value === "paid") return "支付成功";
  if (value === "expired") return "二维码已过期";
  if (value === "closed") return "订单已关闭";
  return "等待扫码支付";
}
