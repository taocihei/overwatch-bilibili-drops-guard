CREATE TABLE `sponsor_callback_circuit` (
	`id` text PRIMARY KEY NOT NULL,
	`callback_url` text NOT NULL,
	`state` text DEFAULT 'closed' NOT NULL,
	`failure_count` integer DEFAULT 0 NOT NULL,
	`opened_until` integer DEFAULT 0 NOT NULL,
	`probe_lease_until` integer DEFAULT 0 NOT NULL,
	`last_checked_at` integer DEFAULT 0 NOT NULL,
	`last_success_at` integer DEFAULT 0 NOT NULL
);
