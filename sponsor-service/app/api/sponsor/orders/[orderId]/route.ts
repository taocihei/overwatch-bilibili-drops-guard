import { ensureSponsorSchema } from "@/lib/database";
import { json, serviceError } from "@/lib/responses";

type OrderRow = {
  status: string;
  expires_at: number;
  paid_at: number | null;
};

export async function GET(request: Request): Promise<Response> {
  try {
    const orderId = getLastPathSegment(request.url);
    if (!/^[a-f0-9]{32}$/.test(orderId)) {
      return json({ ok: false, error: "订单号无效。" }, { status: 400 });
    }

    const database = await ensureSponsorSchema();
    let order = await database
      .prepare(
        "SELECT status, expires_at, paid_at FROM sponsor_orders WHERE id = ? LIMIT 1",
      )
      .bind(orderId)
      .first<OrderRow>();

    if (!order) {
      return json({ ok: false, error: "订单不存在。" }, { status: 404 });
    }

    if (order.status === "pending" && Date.now() >= order.expires_at) {
      await database
        .prepare(
          "UPDATE sponsor_orders SET status = 'expired' WHERE id = ? AND status = 'pending'",
        )
        .bind(orderId)
        .run();
      order = { ...order, status: "expired" };
    }

    return json({
      ok: true,
      data: {
        status: normalizeStatus(order.status),
        message: statusMessage(order.status),
        paid_at: order.paid_at
          ? new Date(order.paid_at).toISOString()
          : null,
      },
    });
  } catch (error) {
    return serviceError(error);
  }
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
