CREATE TABLE IF NOT EXISTS `activity_daily` (
	`day` text NOT NULL,
	`kind` text NOT NULL,
	`area` text NOT NULL,
	`label` text NOT NULL,
	`count` integer DEFAULT 0 NOT NULL,
	`misses` integer DEFAULT 0 NOT NULL,
	PRIMARY KEY(`day`, `kind`, `area`, `label`)
);

CREATE TABLE IF NOT EXISTS `owner_activity_daily` (
	`day` text NOT NULL,
	`kind` text NOT NULL,
	`area` text NOT NULL,
	`label` text NOT NULL,
	`count` integer DEFAULT 0 NOT NULL,
	`misses` integer DEFAULT 0 NOT NULL,
	PRIMARY KEY(`day`, `kind`, `area`, `label`)
);

CREATE TABLE IF NOT EXISTS `feedback` (
	`id` text PRIMARY KEY NOT NULL,
	`kind` text NOT NULL,
	`area` text NOT NULL,
	`title` text NOT NULL,
	`detail` text NOT NULL,
	`context` text NOT NULL,
	`status` text DEFAULT 'new' NOT NULL,
	`created` text NOT NULL
);
