import type {
  NewSponsorOrder,
  SponsorOrderRow,
  SponsorOrderStore,
  SponsorReservation,
} from "./order-allocation";

const ORDER_COLUMNS = `
  id, provider_order_no, amount_cents, status, qr_url, app_version,
  created_at, expires_at, install_id, checkout_intent_id, reserved_at,
  state_version`;

export class D1SponsorOrderStore implements SponsorOrderStore {
  constructor(private readonly database: D1Database) {}

  async findReserved(input: SponsorReservation): Promise<SponsorOrderRow | null> {
    return this.database
      .prepare(
        `SELECT ${ORDER_COLUMNS}
         FROM sponsor_orders
         WHERE install_id = ? AND checkout_intent_id = ? AND amount_cents = ?
         LIMIT 1`,
      )
      .bind(input.installId, input.checkoutIntentId, input.amountCents)
      .first<SponsorOrderRow>();
  }

  async claimPooled(
    input: SponsorReservation,
    reservedAt: number,
    minimumExpiresAt: number,
  ): Promise<SponsorOrderRow | null> {
    return this.database
      .prepare(
        `UPDATE sponsor_orders
         SET install_id = ?, checkout_intent_id = ?, reserved_at = ?,
             app_version = ?, state_version = state_version + 1
         WHERE id = (
           SELECT id
           FROM sponsor_orders
           WHERE amount_cents = ? AND status = 'pending'
             AND install_id IS NULL AND checkout_intent_id IS NULL
             AND expires_at > ?
           ORDER BY created_at ASC
           LIMIT 1
         )
           AND install_id IS NULL AND checkout_intent_id IS NULL
         RETURNING ${ORDER_COLUMNS}`,
      )
      .bind(
        input.installId,
        input.checkoutIntentId,
        reservedAt,
        input.appVersion,
        input.amountCents,
        minimumExpiresAt,
      )
      .first<SponsorOrderRow>();
  }

  async beginCreating(input: NewSponsorOrder): Promise<boolean> {
    const result = await this.database
      .prepare(
        `INSERT OR IGNORE INTO sponsor_orders (
           id, provider_order_no, amount_cents, status, qr_url, app_version,
           created_at, expires_at, install_id, checkout_intent_id, reserved_at,
           state_version
         ) VALUES (?, ?, ?, 'creating', '', ?, ?, ?, ?, ?, ?, 0)`,
      )
      .bind(
        input.id,
        input.providerOrderNo,
        input.amountCents,
        input.appVersion,
        input.createdAt,
        input.expiresAt,
        input.installId,
        input.checkoutIntentId,
        input.createdAt,
      )
      .run();
    return Number(result.meta?.changes ?? 0) === 1;
  }

  async completeCreating(id: string, qrValue: string): Promise<SponsorOrderRow> {
    const order = await this.database
      .prepare(
        `UPDATE sponsor_orders
         SET qr_url = ?,
             status = CASE WHEN status = 'creating' THEN 'pending' ELSE status END,
             state_version = state_version + 1
         WHERE id = ?
         RETURNING ${ORDER_COLUMNS}`,
      )
      .bind(qrValue, id)
      .first<SponsorOrderRow>();
    if (!order) throw new Error("SPONSOR_ORDER_FINALIZE_FAILED");
    return order;
  }

  async failCreating(id: string): Promise<void> {
    await this.database
      .prepare(
        `UPDATE sponsor_orders
         SET status = 'failed', state_version = state_version + 1
         WHERE id = ? AND status = 'creating'`,
      )
      .bind(id)
      .run();
  }

  async deleteFailed(input: SponsorReservation): Promise<void> {
    await this.database
      .prepare(
        `DELETE FROM sponsor_orders
         WHERE install_id = ? AND checkout_intent_id = ? AND amount_cents = ?
           AND status = 'failed'`,
      )
      .bind(input.installId, input.checkoutIntentId, input.amountCents)
      .run();
  }
}
