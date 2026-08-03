import { createHash } from "node:crypto";

const YUNGOUOS_NATIVE_PAY_URL =
  "https://api.pay.yungouos.com/api/pay/wxpay/nativePay";

type ProviderResponse = {
  code?: number | string;
  msg?: string;
  data?: unknown;
};

export type NativePaymentResult = {
  qrContent: string;
  qrImageUrl: string;
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
): Promise<NativePaymentResult> {
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
    // YunGouOS 官方 SDK：type=1 返回微信 code_url，由接入方生成二维码。
    // 不再依赖支付平台临时图片域名，桌面端和代理接口都能稳定渲染。
    type: "1",
  });

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12_000);
  let response: Response;
  try {
    response = await fetch(YUNGOUOS_NATIVE_PAY_URL, {
      method: "POST",
      headers: {
        accept: "application/json",
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
      },
      body: form,
      signal: controller.signal,
    });
  } catch {
    throw new Error(
      controller.signal.aborted
        ? "PAYMENT_PROVIDER_TIMEOUT"
        : "PAYMENT_PROVIDER_FETCH_FAILED",
    );
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    throw new Error(`PAYMENT_PROVIDER_HTTP_${response.status}`);
  }

  const responseText = (await response.text()).replace(/^\uFEFF/, "").trim();
  let payload: ProviderResponse;
  try {
    payload = JSON.parse(responseText) as ProviderResponse;
  } catch {
    throw new Error("PAYMENT_PROVIDER_INVALID_RESPONSE");
  }
  if (String(payload.code) !== "0") {
    throw new Error(
      `PAYMENT_PROVIDER_REJECTED:${sanitizeProviderMessage(payload.msg)}`,
    );
  }

  return normalizeProviderQr(extractQrValue(payload.data));
}

function extractQrValue(data: unknown): string {
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

function normalizeProviderQr(value: string): NativePaymentResult {
  const normalized = value.trim();
  if (
    normalized.length <= 4096 &&
    normalized.toLowerCase().startsWith("weixin://") &&
    !/[\u0000-\u001f\u007f]/.test(normalized)
  ) {
    return { qrContent: normalized, qrImageUrl: "" };
  }

  let url: URL;
  try {
    url = new URL(normalized);
  } catch {
    throw new Error("PAYMENT_PROVIDER_INVALID_QR");
  }

  const hostname = url.hostname.toLowerCase();
  const trustedImageHost =
    hostname === "yungouos.com" ||
    hostname.endsWith(".yungouos.com") ||
    hostname === "yungouos.oss-cn-shanghai.aliyuncs.com";
  if (!trustedImageHost) {
    throw new Error("PAYMENT_PROVIDER_UNTRUSTED_QR");
  }

  if (url.protocol === "http:") url.protocol = "https:";
  if (url.protocol !== "https:") {
    throw new Error("PAYMENT_PROVIDER_INVALID_QR_PROTOCOL");
  }
  return { qrContent: "", qrImageUrl: url.toString() };
}

function sanitizeProviderMessage(value: unknown): string {
  if (typeof value !== "string") return "unknown";
  return value.replace(/[\r\n\t]/g, " ").slice(0, 120);
}

function compareAscii(left: string, right: string): number {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}
