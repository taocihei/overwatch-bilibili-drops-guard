import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const sponsorOrders = sqliteTable(
  "sponsor_orders",
  {
    id: text("id").primaryKey(),
    providerOrderNo: text("provider_order_no").notNull().unique(),
    amountCents: integer("amount_cents").notNull(),
    status: text("status").notNull().default("pending"),
    qrUrl: text("qr_url").notNull(),
    appVersion: text("app_version").notNull().default("unknown"),
    createdAt: integer("created_at").notNull(),
    expiresAt: integer("expires_at").notNull(),
    paidAt: integer("paid_at"),
    paymentNo: text("payment_no"),
  },
  (table) => [
    index("sponsor_orders_status_idx").on(table.status),
    index("sponsor_orders_expires_at_idx").on(table.expiresAt),
  ],
);