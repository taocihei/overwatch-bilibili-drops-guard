import { ensureSponsorSchema } from "@/lib/database";
import { json, serviceError } from "@/lib/responses";
import QRCode from "qrcode";

type QrRow = {
  qr_url: string;
};

export async function GET(request: Request): Promise<Response> {
  try {
    const orderId = getLastPathSegment(request.url);
    if (!/^[a-f0-9]{32}$/.test(orderId)) {
      return json({ ok: false, error: "订单号无效。" }, { status: 400 });
    }

    const database = await ensureSponsorSchema();
    const order = await database
      .prepare("SELECT qr_url FROM sponsor_orders WHERE id = ? LIMIT 1")
      .bind(orderId)
      .first<QrRow>();
    if (!order) {
      return json({ ok: false, error: "二维码不存在。" }, { status: 404 });
    }

    if (order.qr_url.toLowerCase().startsWith("weixin://")) {
      const png = await QRCode.toBuffer(order.qr_url, {
        errorCorrectionLevel: "M",
        margin: 3,
        type: "png",
        width: 360,
      });
      return new Response(new Uint8Array(png), {
        status: 200,
        headers: {
          "cache-control": "private, no-store",
          "content-type": "image/png",
          "content-security-policy": "default-src 'none'",
          "x-content-type-options": "nosniff",
        },
      });
    }

    let providerUrl: URL;
    try {
      providerUrl = new URL(order.qr_url);
    } catch {
      return json({ ok: false, error: "二维码地址无效。" }, { status: 502 });
    }
    const hostname = providerUrl.hostname.toLowerCase();
    if (
      providerUrl.protocol !== "https:" ||
      (hostname !== "yungouos.com" &&
        !hostname.endsWith(".yungouos.com") &&
        hostname !== "yungouos.oss-cn-shanghai.aliyuncs.com")
    ) {
      return json({ ok: false, error: "二维码地址无效。" }, { status: 502 });
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 4_000);
    let upstream: Response;
    try {
      upstream = await fetch(providerUrl, {
        headers: { accept: "image/png,image/jpeg,image/webp,image/*;q=0.8" },
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }
    if (!upstream.ok || !upstream.body) {
      return json({ ok: false, error: "二维码加载失败。" }, { status: 502 });
    }

    const contentType = upstream.headers.get("content-type") ?? "";
    if (!contentType.toLowerCase().startsWith("image/")) {
      return json({ ok: false, error: "二维码格式无效。" }, { status: 502 });
    }

    return new Response(upstream.body, {
      status: 200,
      headers: {
        "cache-control": "private, no-store",
        "content-type": contentType,
        "content-security-policy": "default-src 'none'",
        "x-content-type-options": "nosniff",
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
