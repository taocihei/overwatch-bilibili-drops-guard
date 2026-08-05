import assert from "node:assert/strict";
import test from "node:test";

import { resolveSponsorCallbackUrl } from "../lib/callback-url.ts";

test("uses the fixed public callback URL when configured", () => {
  assert.equal(
    resolveSponsorCallbackUrl(
      "https://api.codeboc.cn/yungouos/callback",
      "https://example.chatgpt.site/api/sponsor/orders",
    ),
    "https://api.codeboc.cn/yungouos/callback",
  );
});

test("falls back to the current service callback when not configured", () => {
  assert.equal(
    resolveSponsorCallbackUrl(
      undefined,
      "https://example.chatgpt.site/api/sponsor/orders",
    ),
    "https://example.chatgpt.site/api/sponsor/callback",
  );
});

for (const invalidUrl of [
  "http://api.codeboc.cn/yungouos/callback",
  "https://api.codeboc.cn:8443/yungouos/callback",
  "https://api.codeboc.cn/yungouos/callback?token=value",
  "https://api.codeboc.cn/yungouos/callback#fragment",
]) {
  test(`rejects unsupported callback URL: ${invalidUrl}`, () => {
    assert.throws(
      () =>
        resolveSponsorCallbackUrl(
          invalidUrl,
          "https://example.chatgpt.site/api/sponsor/orders",
        ),
      /PAYMENT_CALLBACK_URL_INVALID/,
    );
  });
}
