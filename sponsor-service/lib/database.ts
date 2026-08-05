import { getD1 } from "./runtime";

let schemaReady: Promise<void> | null = null;

export async function ensureSponsorSchema(): Promise<D1Database> {
  const database = getD1();
  schemaReady ??= initializeSchema(database);
  await schemaReady;
  return database;
}

async function initializeSchema(database: D1Database): Promise<void> {
  await database
    .prepare(
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
        payment_no TEXT,
        install_id TEXT,
        checkout_intent_id TEXT,
        reserved_at INTEGER,
        state_version INTEGER NOT NULL DEFAULT 0,
        provider_checked_at INTEGER NOT NULL DEFAULT 0
      )`,
    )
    .run();

  const tableInfo = await database
    .prepare("PRAGMA table_info(sponsor_orders)")
    .all<{ name: string }>();
  const columns = new Set((tableInfo.results ?? []).map((column) => column.name));
  const migrations: D1PreparedStatement[] = [];
  if (!columns.has("install_id")) {
    migrations.push(
      database.prepare("ALTER TABLE sponsor_orders ADD COLUMN install_id TEXT"),
    );
  }
  if (!columns.has("checkout_intent_id")) {
    migrations.push(
      database.prepare(
        "ALTER TABLE sponsor_orders ADD COLUMN checkout_intent_id TEXT",
      ),
    );
  }
  if (!columns.has("reserved_at")) {
    migrations.push(
      database.prepare("ALTER TABLE sponsor_orders ADD COLUMN reserved_at INTEGER"),
    );
  }
  if (!columns.has("state_version")) {
    migrations.push(
      database.prepare(
        "ALTER TABLE sponsor_orders ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0",
      ),
    );
  }
  if (!columns.has("provider_checked_at")) {
    migrations.push(
      database.prepare(
        "ALTER TABLE sponsor_orders ADD COLUMN provider_checked_at INTEGER NOT NULL DEFAULT 0",
      ),
    );
  }
  if (migrations.length) await database.batch(migrations);

  await database.batch([
    database.prepare("DROP INDEX IF EXISTS sponsor_orders_reuse_idx"),
    database.prepare(
      "CREATE INDEX IF NOT EXISTS sponsor_orders_status_idx ON sponsor_orders (status)",
    ),
    database.prepare(
      "CREATE INDEX IF NOT EXISTS sponsor_orders_expires_at_idx ON sponsor_orders (expires_at)",
    ),
    database.prepare(
      "CREATE INDEX IF NOT EXISTS sponsor_orders_pool_idx ON sponsor_orders (amount_cents, status, install_id, expires_at)",
    ),
    database.prepare(
      "CREATE UNIQUE INDEX IF NOT EXISTS sponsor_orders_checkout_unique ON sponsor_orders (install_id, checkout_intent_id, amount_cents)",
    ),
    database.prepare(
      `CREATE TABLE IF NOT EXISTS sponsor_provider_state (
        key TEXT PRIMARY KEY NOT NULL,
        checked_at INTEGER NOT NULL DEFAULT 0
      )`,
    ),
    database.prepare(
      "INSERT OR IGNORE INTO sponsor_provider_state (key, checked_at) VALUES ('order-query', 0)",
    ),
  ]);
}
