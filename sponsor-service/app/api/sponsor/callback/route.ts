import { ensureSponsorSchema } from "@/lib/database";
import { verifyPaymentCallback } from "@/lib/payment";
import { getPaymentCredentials } from "@/lib/runtime";

type CallbackOrderRow = {
  id: string;
  amount_cents: number;
  status: string;
};

export async function POST(request: Request): Promise<Response> {
  try {
    const contentType = request.headers.get("content-type")?.toLowerCase() ?? "";
    if (!contentType.includes("application/x-www-form-urlencoded")) {
      return callbackResponse("FAIL", 415);
    }

    const values = new URLSearchParams(await request.text());
    const { merchantId, paymentKey } = getPaymentCredentials();
    if (!verifyPaymentCallback(values, merchantId, paymentKey)) {
      return callbackResponse("FAIL", 400);
    }

    const providerOrderNo = values.get("outTradeNo")?.trim() ?? "";
    const database = await ensureSponsorSchema();
    const order = await database
      .prepare(
        "SELECT id, amount_cents, status FROM sponsor_orders WHERE provider_order_no = ? LIMIT 1",
      )
      .bind(providerOrderNo)
      .first<CallbackOrderRow>();
    if (!order) {
      return callbackResponse("FAIL", 404);
    }

    const paidAmount = moneyToCents(values.get("money"));
    if (paidAmount !== order.amount_cents) {
      return callbackResponse("FAIL", 400);
    }

    if (values.get("code")?.trim() === "1" && order.status !== "paid") {
      await database
        .prepare(
          `UPDATE sponsor_orders
           SET status = 'paid', paid_at = ?, payment_no = ?
           WHERE id = ? AND status != 'paid'`,
        )
        .bind(Date.now(), values.get("payNo")?.trim() ?? "", order.id)
        .run();
    }

    return callbackResponse("SUCCESS", 200);
  } catch {
    return callbackResponse("FAIL", 500);
  }
}

function moneyToCents(value: string | null): number {
  if (!value || !/^\d+(?:\.\d{1,2})?$/.test(value.trim())) return -1;
  return Math.round(Number(value) * 100);
}

function callbackResponse(body: string, status: number): Response {
  return new Response(body, {
    status,
    headers: {
      "cache-control": "no-store",
      "content-type": "text/plain; charset=utf-8",
      "x-content-type-options": "nosniff",
    },
  });
}
