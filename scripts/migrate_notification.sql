-- Upgrades a notification_db created before last_error was added (why the
-- latest send attempt failed). Fresh databases don't need this: the service
-- creates the new schema on startup.
-- Safe to run more than once.
-- Run as a PostgreSQL superuser:  psql -U postgres -f scripts/migrate_notification.sql

\connect notification_db

ALTER TABLE notifications ADD COLUMN IF NOT EXISTS last_error VARCHAR(255);
