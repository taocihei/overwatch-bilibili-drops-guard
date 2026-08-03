export function json(payload: unknown, init: ResponseInit = {}): Response {
  const headers = new Headers(init.headers);
  headers.set("content-type", "application/json; charset=utf-8");
  headers.set("cache-control", "no-store");
  headers.set("x-content-type-options", "nosniff");
  headers.set("referrer-policy", "no-referrer");
  return Response.json(payload, { ...init, headers });
}

export function serviceError(error: unknown): Response {
  const message = error instanceof Error ? error.message : "";
  console.error("SPONSOR_SERVICE_ERROR", message || "UNKNOWN_ERROR");
  if (
    message === "PAYMENT_SERVICE_NOT_CONFIGURED" ||
    message === "PAYMENT_DATABASE_NOT_CONFIGURED"
  ) {
    return json(
      { ok: false, error: "赞助服务暂未完成配置，请稍后再试。" },
      { status: 503 },
    );
  }

  if (message.startsWith("PAYMENT_PROVIDER_")) {
    return json(
      { ok: false, error: "支付通道暂时繁忙，请稍后重试。" },
      { status: 502 },
    );
  }

  return json(
    { ok: false, error: "服务暂时繁忙，请稍后重试。" },
    { status: 500 },
  );
}
