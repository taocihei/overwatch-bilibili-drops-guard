/** Cloudflare Worker entry point for the vinext-starter template. */
import { handleImageOptimization, DEFAULT_DEVICE_SIZES, DEFAULT_IMAGE_SIZES } from "vinext/server/image-optimization";
import handler from "vinext/server/app-router-entry";

interface Env {
  ASSETS: Fetcher;
  DB: D1Database;
  SPONSOR_POOL_REFILL_TOKEN?: string;
  IMAGES: {
    input(stream: ReadableStream): {
      transform(options: Record<string, unknown>): {
        output(options: { format: string; quality: number }): Promise<{ response(): Response }>;
      };
    };
  };
}

interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
  passThroughOnException(): void;
}

// Image security config. SVG sources with .svg extension auto-skip the
// optimization endpoint on the client side (served directly, no proxy).
// To route SVGs through the optimizer (with security headers), set
// dangerouslyAllowSVG: true in next.config.js and uncomment below:
// const imageConfig: ImageConfig = { dangerouslyAllowSVG: true };

const worker = {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/_vinext/image") {
      const allowedWidths = [...DEFAULT_DEVICE_SIZES, ...DEFAULT_IMAGE_SIZES];
      return handleImageOptimization(request, {
        fetchAsset: (path) => env.ASSETS.fetch(new Request(new URL(path, request.url))),
        transformImage: async (body, { width, format, quality }) => {
          const result = await env.IMAGES.input(body).transform(width > 0 ? { width } : {}).output({ format, quality });
          return result.response();
        },
      }, allowedWidths);
    }

    const response = await handler.fetch(request, env, ctx);
    if (
      request.method === "POST" &&
      url.pathname === "/api/sponsor/orders" &&
      response.ok &&
      env.SPONSOR_POOL_REFILL_TOKEN
    ) {
      const refillRequest = new Request(
        new URL("/api/sponsor/pool/refill", request.url),
        {
          method: "POST",
          headers: {
            authorization: `Bearer ${env.SPONSOR_POOL_REFILL_TOKEN}`,
            "content-type": "application/json",
          },
          body: "{}",
        },
      );
      // Refill happens after the client already has its QR response.  It never
      // adds latency to the current checkout and prepares the next launch.
      ctx.waitUntil(
        handler.fetch(refillRequest, env, ctx).then(async (refillResponse) => {
          if (!refillResponse.ok) {
            console.warn("SPONSOR_POOL_REFILL_DEFERRED", refillResponse.status);
          }
        }),
      );
    }
    return response;
  },
};

export default worker;
