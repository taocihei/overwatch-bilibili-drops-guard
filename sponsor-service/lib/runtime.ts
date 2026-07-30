import { env } from "cloudflare:workers";

type RuntimeBindings = {
  DB?: D1Database;
  YUNGOUOS_MCH_ID?: string;
  YUNGOUOS_PAY_KEY?: string;
};

export function getRuntimeBindings(): RuntimeBindings {
  return env as unknown as RuntimeBindings;
}

export function getPaymentCredentials(): {
  merchantId: string;
  paymentKey: string;
} {
  const runtime = getRuntimeBindings();
  const merchantId = runtime.YUNGOUOS_MCH_ID?.trim() ?? "";
  const paymentKey = runtime.YUNGOUOS_PAY_KEY?.trim() ?? "";

  if (!merchantId || !paymentKey) {
    throw new Error("PAYMENT_SERVICE_NOT_CONFIGURED");
  }

  return { merchantId, paymentKey };
}

export function getD1(): D1Database {
  const database = getRuntimeBindings().DB;
  if (!database) {
    throw new Error("PAYMENT_DATABASE_NOT_CONFIGURED");
  }
  return database;
}
