PRAGMA foreign_keys = ON;

ALTER TABLE alerts ADD COLUMN is_provisional INTEGER NOT NULL DEFAULT 0
CHECK (is_provisional IN (0,1));

INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
VALUES (4, 'provisional_urgent_alerts', strftime('%Y-%m-%dT%H:%M:%fZ','now'));
