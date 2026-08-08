import { ensureSponsorSchema } from "@/lib/database";
import { getSponsorCallbackCircuitSnapshot } from "@/lib/callback-circuit";
import { json, serviceError } from "@/lib/responses";
import { getSponsorCallbackSettings } from "@/lib/runtime";

export async function GET(request: Request): Promise<Response> {
  try {
    const database = await ensureSponsorSchema();
    const settings = getSponsorCallbackSettings();
    const callbackCircuit = await getSponsorCallbackCircuitSnapshot({
      database,
      configuredUrl: settings.configuredUrl,
      requestUrl: request.url,
    });
    return json({
      ok: true,
      service: "overwatch-bilibili-drops-sponsor",
      version: "0.5.25",
      callback_circuit: {
        state: callbackCircuit.state,
        failure_count: callbackCircuit.failureCount,
        fallback_active: callbackCircuit.fallbackActive,
        last_checked_at: callbackCircuit.lastCheckedAt,
        last_success_at: callbackCircuit.lastSuccessAt,
      },
    });
  } catch (error) {
    return serviceError(error);
  }
}
