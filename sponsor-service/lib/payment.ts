import { createHash } from "node:crypto";

const YUNGOUOS_NATIVE_PAY_URL =
  "https://api.pay.yungouos.com/api/pay/wxpay/nativePay";

type ProviderResponse = {
  code?: number | string;
  msg?: string;
  data?: unknown;
};

export type CreatePaymentInput = {
  providerOrderNo: string;
  amount: string;
  body: string;
  merchantId: string;
  paymentKey: string;
  notifyUrl: string;
  attach: string;
};

export function paymentSign(
  params: Record<string, string>,
  paymentKey: string,
): string {
  const payload = Object.entries(params)
    .filter(([, value]) => value !== "")
    .sort(([left], [right]) => compareAscii(left, right))
    .map(([key, value]) => `${key}=${value}`)
    .concat(`key=${paymentKey}`)
    .join("&");

  return createHash("md5").update(payload, "utf8").digest("hex").toUpperCase();
}

export function safeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

export function verifyPaymentCallback(
  values: URLSearchParams,
  merchantId: string,
  paymentKey: string,
): boolean {
  const sign = values.get("sign")?.trim().toUpperCase() ?? "";
  const callbackMerchantId = values.get("mchId")?.trim() ?? "";
  if (!sign || callbackMerchantId !== merchantId) return false;

  const required = [
    "code",
    "orderNo",
    "outTradeNo",
    "payNo",
    "money",
    "mchId",
  ] as const;
  const params: Record<string, string> = {};

  for (const key of required) {
    const value = values.get(key)?.trim() ?? "";
    if (!value) return false;
    params[key] = value;
  }

  return safeEqual(paymentSign(params, paymentKey), sign);
}

export async function createNativePayment(
  input: CreatePaymentInput,
): Promise<string> {
  const requiredParams = {
    body: input.body,
    mch_id: input.merchantId,
    out_trade_no: input.providerOrderNo,
    total_fee: input.amount,
  };
  const form = new URLSearchParams({
    ...requiredParams,
    attach: input.attach,
    notify_url: input.notifyUrl,
    sign: paymentSign(requiredParams, input.paymentKey),
    type: "2",
  });

  const response = await fetch(YUNGOUOS_NATIVE_PAY_URL, {
    method: "POST",
    headers: {
      accept: "application/json",
      "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
    },
    body: form,
    signal: AbortSignal.timeout(12_000),
  });

  if (!response.ok) {
    throw new Error(`PAYMENT_PROVIDER_HTTP_${response.status}`);
  }

  const payload = (await response.json()) as ProviderResponse;
  if (String(payload.code) !== "0") {
    throw new Error(
      `PAYMENT_PROVIDER_REJECTED:${sanitizeProviderMessage(payload.msg)}`,
    );
  }

  const qrUrl = extractQrUrl(payload.data);
  return normalizeProviderQrUrl(qrUrl);
}

function extractQrUrl(data: unknown): string {
  if (typeof data === "string" && data.trim()) return data.trim();
  if (!data || typeof data !== "object") {
    throw new Error("PAYMENT_PROVIDER_MISSING_QR");
  }

  const record = data as Record<string, unknown>;
  for (const key of ["qr_url", "qrUrl", "url", "codeUrl", "code_url"]) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }

  throw new Error("PAYMENT_PROVIDER_MISSING_QR");
}

function normalizeProviderQrUrl(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("PAYMENT_PROVIDER_INVALID_QR");
  }

  const hostname = url.hostname.toLowerCase();
  if (hostname !== "yungouos.com" && !hostname.endsWith(".yungouos.com")) {
    throw new Error("PAYMENT_PROVIDER_UNTRUSTED_QR");
  }

  if (url.protocol === "http:") url.protocol = "https:";
  if (url.protocol !== "https:") {
    throw new Error("PAYMENT_PROVIDER_INVALID_QR_PROTOCOL");
  }
  return url.toString();
}

function sanitizeProviderMessage(value: unknown): string {
  if (typeof value !== "string") return "unknown";
  return value.replace(/[\r\n\t]/g, " ").slice(0, 120);
}

function compareAscii(left: string, right: string): number {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}
