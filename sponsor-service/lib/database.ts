import { getD1 } from "./runtime";

let schemaReady: Promise<void> | null = null;

export async function ensureSponsorSchema(): Promise<D1Database> {
  const database = getD1();
  schemaReady ??= initializeSchema(database);
  await schemaReady;
  return database;
}

async function initializeSchema(database: D1Database): Promise<void> {
  await database.batch([
    database.prepare(
      `CREATE TABLE IF NOT EXISTS sponsor_orders (
        id TEXT PRIMARY KEY NOT NULL,
        provider_order_no TEXT NOT NULL UNIQUE,
        amount_cents INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        qr_url TEXT NOT NULL,
        app_version TEXT NOT NULL DEFAULT 'unknown',
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        paid_at INTEGER,
        payment_no TEXT
      )`,
    ),
    database.prepare(
      "CREATE INDEX IF NOT EXISTS sponsor_orders_status_idx ON sponsor_orders (status)",
    ),
    database.prepare(
      "CREATE INDEX IF NOT EXISTS sponsor_orders_expires_at_idx ON sponsor_orders (expires_at)",
    ),
  ]);
}
