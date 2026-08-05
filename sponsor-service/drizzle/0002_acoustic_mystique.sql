DROP INDEX `sponsor_orders_reuse_idx`;--> statement-breakpoint
ALTER TABLE `sponsor_orders` ADD `install_id` text;--> statement-breakpoint
ALTER TABLE `sponsor_orders` ADD `checkout_intent_id` text;--> statement-breakpoint
ALTER TABLE `sponsor_orders` ADD `reserved_at` integer;--> statement-breakpoint
ALTER TABLE `sponsor_orders` ADD `state_version` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
CREATE INDEX `sponsor_orders_pool_idx` ON `sponsor_orders` (`amount_cents`,`status`,`install_id`,`expires_at`);--> statement-breakpoint
CREATE UNIQUE INDEX `sponsor_orders_checkout_unique` ON `sponsor_orders` (`install_id`,`checkout_intent_id`,`amount_cents`);