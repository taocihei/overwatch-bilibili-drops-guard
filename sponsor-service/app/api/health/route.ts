import { ensureSponsorSchema } from "@/lib/database";
import { json, serviceError } from "@/lib/responses";

export async function GET(): Promise<Response> {
  try {
    await ensureSponsorSchema();
    return json({
      ok: true,
      service: "overwatch-bilibili-drops-sponsor",
      version: "0.5.22",
    });
  } catch (error) {
    return serviceError(error);
  }
}
