import { ensureSponsorSchema } from "@/lib/database";
import {
  reserveSponsorOrders,
  type ReservedSponsorOrder,
  type SponsorReservation,
} from "@/lib/order-allocation";
import { D1SponsorOrderStore } from "@/lib/order-store";
import { createNativePayment } from "@/lib/payment";
import { json, serviceError } from "@/lib/responses";
import { getPaymentCredentials, getSponsorCallbackUrl } from "@/lib/runtime";

const MIN_AMOUNT_CENTS = 100;
const MAX_AMOUNT_CENTS = 999_900;
const MAX_BATCH_SIZE = 5;
// WeChat Native code_url is valid for two hours; retain a ten-minute safety margin.
const ORDER_LIFETIME_MS = 110 * 60 * 1000;
const RESERVATION_EXPIRY_GUARD_MS = 60 * 1000;

type SponsorOrderRequest = {
  amount?: unknown;
  amounts?: unknown;
  install_id?: unknown;
  checkout_intent_id?: unknown;
  app_version?: unknown;
  product?: unknown;
  provider?: unknown;
};

export async function POST(request: Request): Promise<Response> {
  try {
    const contentLength = Number(request.headers.get("content-length") ?? "0");
    if (contentLength > 4096) {
      return json({ ok: false, error: "请求内容过大。" }, { status: 413 });
    }

    const payload = (await request.json()) as SponsorOrderRequest;
    const amounts = normalizeAmounts(payload);
    if (amounts.error) {
      return json({ ok: false, error: amounts.error }, { status: 400 });
    }

    if (
      payload.provider !== undefined &&
      String(payload.provider).toLowerCase() !== "yungouos"
    ) {
      return json({ ok: false, error: "不支持该支付通道。" }, { status: 400 });
    }

    const installId = normalizeClientKey(payload.install_id, 16, 128);
    if (!installId) {
      return json(
        { ok: false, error: "缺少有效的客户端安装标识。" },
        { status: 400 },
      );
    }
    const checkoutIntentId = normalizeClientKey(
      payload.checkout_intent_id,
      8,
      128,
    );
    if (!checkoutIntentId) {
      return json(
        { ok: false, error: "缺少有效的结算意图标识。" },
        { status: 400 },
      );
    }

    const appVersion = safeText(payload.app_version, 24) || "unknown";
    const database = await ensureSponsorSchema();
    const store = new D1SponsorOrderStore(database);
    const { merchantId, paymentKey } = getPaymentCredentials();
    const notifyUrl = getSponsorCallbackUrl(request.url);
    const reservations: SponsorReservation[] = amounts.values.map(
      ({ amount, amountCents }) => ({
        installId,
        checkoutIntentId,
        amount,
        amountCents,
        appVersion,
      }),
    );

    // All requested amounts are reserved concurrently.  A five-tier desktop
    // preload therefore costs one provider round trip rather than five serial
    // round trips, while the unique checkout key keeps retries idempotent.
    const reserved = await reserveSponsorOrders(
      store,
      reservations,
      async (draft) => {
        const paymentQr = await createNativePayment({
          providerOrderNo: draft.providerOrderNo,
          amount: draft.amount,
          body: "守望先锋B站直播挂宝赞助",
          merchantId,
          paymentKey,
          notifyUrl,
          attach: `sponsor:${draft.id}`,
        });
        return paymentQr.qrContent || paymentQr.qrImageUrl;
      },
      {
        orderLifetimeMs: ORDER_LIFETIME_MS,
        expiryGuardMs: RESERVATION_EXPIRY_GUARD_MS,
      },
    );

    const responseStatus = reserved.some(({ source }) => source === "created")
      ? 201
      : 200;
    const responseOrders = reserved.map((result) =>
      sponsorOrderData(request.url, result),
    );

    if (!Array.isArray(payload.amounts)) {
      return json({ ok: true, data: responseOrders[0] }, { status: responseStatus });
    }
    return json(
      {
        ok: true,
        data: {
          checkout_intent_id: checkoutIntentId,
          orders: responseOrders,
        },
      },
      { status: responseStatus },
    );
  } catch (error) {
    return serviceError(error);
  }
}

function sponsorOrderData(requestUrl: string, result: ReservedSponsorOrder) {
  const { order } = result;
  const qrContent = order.qr_url.toLowerCase().startsWith("weixin://")
    ? order.qr_url
    : "";
  const qrImageUrl = qrContent ? "" : order.qr_url;
  const proxyQrUrl = new URL(
    `/api/sponsor/qr/${encodeURIComponent(order.id)}`,
    requestUrl,
  ).toString();

  return {
    order_id: order.id,
    amount: (order.amount_cents / 100).toFixed(2),
    qr_url: qrContent ? proxyQrUrl : qrImageUrl,
    fallback_qr_url: qrImageUrl ? proxyQrUrl : "",
    // YunGouOS type=1 code_url is preserved so the desktop client can render
    // locally without waiting for an image proxy.
    qr_content: qrContent,
    expires_at: new Date(order.expires_at).toISOString(),
    expires_in_seconds: Math.max(
      0,
      Math.floor((order.expires_at - Date.now()) / 1000),
    ),
    state_version: order.state_version,
    allocation: result.source,
  };
}

function normalizeAmounts(payload: SponsorOrderRequest): {
  values: { amount: string; amountCents: number }[];
  error: string;
} {
  const isBatch = Array.isArray(payload.amounts);
  const rawValues = isBatch ? payload.amounts : [payload.amount];
  if (rawValues.length === 0 || rawValues.length > MAX_BATCH_SIZE) {
    return { values: [], error: "每次最多可预留 5 个赞助金额。" };
  }

  const values: { amount: string; amountCents: number }[] = [];
  const seen = new Set<number>();
  for (const rawValue of rawValues) {
    const normalized = normalizeAmount(rawValue);
    if (!normalized) {
      return { values: [], error: "赞助金额需要在 1–9999 元之间。" };
    }
    if (seen.has(normalized.amountCents)) {
      return { values: [], error: "批量预留金额不能重复。" };
    }
    seen.add(normalized.amountCents);
    values.push(normalized);
  }
  return { values, error: "" };
}

function normalizeAmount(
  value: unknown,
): { amount: string; amountCents: number } | null {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return null;
  const amountCents = Math.round(amount * 100);
  if (amountCents < MIN_AMOUNT_CENTS || amountCents > MAX_AMOUNT_CENTS) {
    return null;
  }
  return { amount: (amountCents / 100).toFixed(2), amountCents };
}

function normalizeClientKey(
  value: unknown,
  minimumLength: number,
  maximumLength: number,
): string {
  if (typeof value !== "string") return "";
  const normalized = value.trim();
  if (
    normalized.length < minimumLength ||
    normalized.length > maximumLength ||
    !/^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(normalized)
  ) {
    return "";
  }
  return normalized;
}

function safeText(value: unknown, maxLength: number): string {
  if (typeof value !== "string") return "";
  return value.trim().replace(/[\r\n\t]/g, " ").slice(0, maxLength);
}
