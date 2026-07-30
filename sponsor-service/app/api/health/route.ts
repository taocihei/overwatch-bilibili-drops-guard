import { json } from "@/lib/responses";

export async function GET(): Promise<Response> {
  return json({
    ok: true,
    service: "overwatch-bilibili-drops-sponsor",
    version: "0.5.4",
  });
}
