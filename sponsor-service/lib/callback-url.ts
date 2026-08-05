const CALLBACK_PATH = "/api/sponsor/callback";

export function resolveSponsorCallbackUrl(
  configuredUrl: string | undefined,
  requestUrl: string,
): string {
  const candidate = configuredUrl?.trim();
  if (!candidate) {
    return new URL(CALLBACK_PATH, requestUrl).toString();
  }

  const callbackUrl = new URL(candidate);
  if (
    callbackUrl.protocol !== "https:" ||
    callbackUrl.username ||
    callbackUrl.password ||
    callbackUrl.port ||
    callbackUrl.search ||
    callbackUrl.hash
  ) {
    throw new Error("PAYMENT_CALLBACK_URL_INVALID");
  }

  return callbackUrl.toString();
}
