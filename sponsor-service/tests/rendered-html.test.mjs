import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { access, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test, { after, before } from "node:test";

const templateRoot = new URL("../", import.meta.url);
const previewRoot = new URL("../app/_sites-preview/", import.meta.url);
const baseUrl = "http://localhost:3217";
let server;

before(async () => {
  const cli = fileURLToPath(
    new URL("../node_modules/vinext/dist/cli.js", import.meta.url),
  );
  server = spawn(process.execPath, [cli, "dev", "--port", "3217"], {
    cwd: fileURLToPath(templateRoot),
    env: { ...process.env, WRANGLER_LOG_PATH: ".wrangler/test.log" },
    stdio: ["ignore", "pipe", "pipe"],
  });

  let diagnostics = "";
  server.stdout.on("data", (chunk) => {
    diagnostics += chunk.toString();
  });
  server.stderr.on("data", (chunk) => {
    diagnostics += chunk.toString();
  });

  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (server.exitCode !== null) {
      throw new Error(`vinext start exited early:\n${diagnostics}`);
    }
    try {
      const response = await fetch(`${baseUrl}/api/health`);
      if (response.ok) return;
    } catch {
      // The server has not opened its socket yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 125));
  }

  throw new Error(`vinext start did not become ready:\n${diagnostics}`);
});

after(() => {
  server?.kill();
});

function request(pathname = "/", init = {}) {
  return fetch(`${baseUrl}${pathname}`, init);
}

test("server-renders the sponsor service landing page", async () => {
  const response = await request("/", {
    headers: { accept: "text/html" },
  });
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<html lang="zh-CN">/i);
  assert.match(html, /赞助支付服务/);
  assert.match(html, /服务运行中/);
  assert.match(html, /不保存 Cookie/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/i);
});

test("removes all disposable starter preview artifacts", async () => {
  const [page, layout, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.doesNotMatch(page, /_sites-preview|SkeletonPreview/);
  assert.doesNotMatch(layout, /Starter Project|codex-preview/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  await assert.rejects(access(previewRoot));
  await assert.rejects(access(new URL("public/_sites-preview", templateRoot)));
});

test("health endpoint reports the deployed service version", async () => {
  const response = await request("/api/health");
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    ok: true,
    service: "overwatch-bilibili-drops-sponsor",
    version: "0.5.20",
  });
});

test("order endpoint rejects unsupported amounts before touching secrets", async () => {
  const response = await request("/api/sponsor/orders", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ amount: "0.01", provider: "yungouos" }),
  });
  assert.equal(response.status, 400);
  const payload = await response.json();
  assert.equal(payload.ok, false);
  assert.match(payload.error, /1–9999 元/);
});
