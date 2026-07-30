CREATE TABLE `sponsor_orders` (
	`id` text PRIMARY KEY NOT NULL,
	`provider_order_no` text NOT NULL,
	`amount_cents` integer NOT NULL,
	`status` text DEFAULT 'pending' NOT NULL,
	`qr_url` text NOT NULL,
	`app_version` text DEFAULT 'unknown' NOT NULL,
	`created_at` integer NOT NULL,
	`expires_at` integer NOT NULL,
	`paid_at` integer,
	`payment_no` text
);
--> statement-breakpoint
CREATE UNIQUE INDEX `sponsor_orders_provider_order_no_unique` ON `sponsor_orders` (`provider_order_no`);--> statement-breakpoint
CREATE INDEX `sponsor_orders_status_idx` ON `sponsor_orders` (`status`);--> statement-breakpoint
CREATE INDEX `sponsor_orders_expires_at_idx` ON `sponsor_orders` (`expires_at`);