import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  createNativePayment,
  paymentSign,
  queryPaymentOrder,
  verifyPaymentCallback,
} from "../lib/payment.ts";

test("requests YunGouOS type=1 code_url for local QR rendering", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (_input, init) => {
    const form = new URLSearchParams(String(init.body));
    assert.equal(form.get("type"), "1");
    assert.equal(form.get("out_trade_no"), "SP001");
    return Response.json({ code: 0, data: "weixin://wxpay/native-code" });
  };
  try {
    assert.deepEqual(
      await createNativePayment({
        providerOrderNo: "SP001",
        amount: "5.00",
        body: "sponsor",
        merchantId: "merchant",
        paymentKey: "secret",
        notifyUrl: "https://api.codeboc.cn/yungouos/callback",
        attach: "sponsor:order",
      }),
      { qrContent: "weixin://wxpay/native-code", qrImageUrl: "" },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("signs parameters in ASCII order with the provider key suffix", () => {
  const expected = createHash("md5")
    .update("mch_id=merchant&out_trade_no=SP001&key=secret", "utf8")
    .digest("hex")
    .toUpperCase();
  assert.equal(
    paymentSign({ out_trade_no: "SP001", mch_id: "merchant" }, "secret"),
    expected,
  );
});

test("verifies the six signed YunGouOS callback fields", () => {
  const signed = {
    code: "1",
    orderNo: "Y001",
    outTradeNo: "SP001",
    payNo: "WX001",
    money: "1.00",
    mchId: "merchant",
  };
  const values = new URLSearchParams({
    ...signed,
    sign: paymentSign(signed, "secret"),
    payChannel: "wxpay",
  });
  assert.equal(verifyPaymentCallback(values, "merchant", "secret"), true);
  values.set("money", "2.00");
  assert.equal(verifyPaymentCallback(values, "merchant", "secret"), false);
});

test("queries paid order and validates provider identity and amount", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    assert.equal(
      url.origin + url.pathname,
      "https://api.pay.yungouos.com/api/system/order/getPayOrderInfo",
    );
    assert.equal(url.searchParams.get("out_trade_no"), "SP001");
    assert.equal(url.searchParams.get("mch_id"), "merchant");
    assert.equal(
      url.searchParams.get("sign"),
      paymentSign({ out_trade_no: "SP001", mch_id: "merchant" }, "secret"),
    );
    return Response.json({
      code: 0,
      msg: "查询成功",
      data: {
        orderNo: "Y001",
        outTradeNo: "SP001",
        payNo: "WX001",
        money: "1.00",
        mchid: "merchant",
        payStatus: 1,
      },
    });
  };

  try {
    assert.deepEqual(
      await queryPaymentOrder({
        providerOrderNo: "SP001",
        merchantId: "merchant",
        paymentKey: "secret",
      }),
      {
        paid: true,
        providerOrderNo: "SP001",
        paymentNo: "WX001",
        amountCents: 100,
        rawStatus: "1",
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects a provider response for another merchant", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    Response.json({
      code: 0,
      data: {
        outTradeNo: "SP001",
        money: "1.00",
        mchid: "another-merchant",
        payStatus: 1,
      },
    });
  try {
    await assert.rejects(
      queryPaymentOrder({
        providerOrderNo: "SP001",
        merchantId: "merchant",
        paymentKey: "secret",
      }),
      /PAYMENT_PROVIDER_QUERY_ORDER_MISMATCH/,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
