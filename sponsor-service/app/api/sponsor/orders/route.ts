import { ensureSponsorSchema } from "@/lib/database";
import { createNativePayment } from "@/lib/payment";
import { json, serviceError } from "@/lib/responses";
import { getPaymentCredentials } from "@/lib/runtime";

const ALLOWED_AMOUNTS = new Map([
  ["3.00", 300],
  ["6.00", 600],
  ["10.00", 1000],
]);
const ORDER_LIFETIME_MS = 15 * 60 * 1000;

type SponsorOrderRequest = {
  amount?: unknown;
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
    const amount = normalizeAmount(payload.amount);
    const amountCents = ALLOWED_AMOUNTS.get(amount);
    if (!amountCents) {
      return json(
        { ok: false, error: "仅支持 3、6 或 10 元赞助档位。" },
        { status: 400 },
      );
    }

    if (
      payload.provider !== undefined &&
      String(payload.provider).toLowerCase() !== "yungouos"
    ) {
      return json({ ok: false, error: "不支持该支付通道。" }, { status: 400 });
    }

    const { merchantId, paymentKey } = getPaymentCredentials();
    const database = await ensureSponsorSchema();
    const id = crypto.randomUUID().replaceAll("-", "");
    const providerOrderNo = createProviderOrderNo();
    const now = Date.now();
    const expiresAt = now + ORDER_LIFETIME_MS;
    const appVersion = safeText(payload.app_version, 24) || "unknown";
    const notifyUrl = new URL("/api/sponsor/callback", request.url).toString();

    const providerQrUrl = await createNativePayment({
      providerOrderNo,
      amount,
      body: "守望先锋B站直播挂宝赞助",
      merchantId,
      paymentKey,
      notifyUrl,
      attach: `sponsor:${id}`,
    });

    await database
      .prepare(
        `INSERT INTO sponsor_orders (
          id, provider_order_no, amount_cents, status, qr_url,
          app_version, created_at, expires_at
        ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)`,
      )
      .bind(
        id,
        providerOrderNo,
        amountCents,
        providerQrUrl,
        appVersion,
        now,
        expiresAt,
      )
      .run();

    const qrUrl = new URL(
      `/api/sponsor/qr/${encodeURIComponent(id)}`,
      request.url,
    ).toString();

    return json(
      {
        ok: true,
        data: {
          order_id: id,
          qr_url: qrUrl,
          expires_at: new Date(expiresAt).toISOString(),
        },
      },
      { status: 201 },
    );
  } catch (error) {
    return serviceError(error);
  }
}

function normalizeAmount(value: unknown): string {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "";
  return amount.toFixed(2);
}

function safeText(value: unknown, maxLength: number): string {
  if (typeof value !== "string") return "";
  return value.trim().replace(/[\r\n\t]/g, " ").slice(0, maxLength);
}

function createProviderOrderNo(): string {
  const stamp = new Date()
    .toISOString()
    .replace(/\D/g, "")
    .slice(0, 14);
  const random = crypto
    .randomUUID()
    .replaceAll("-", "")
    .slice(0, 12)
    .toUpperCase();
  return `SP${stamp}${random}`;
}
