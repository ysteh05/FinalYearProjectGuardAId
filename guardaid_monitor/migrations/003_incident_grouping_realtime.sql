PRAGMA foreign_keys = ON;

ALTER TABLE violence_configs ADD COLUMN incident_merge_gap_seconds REAL NOT NULL DEFAULT 5.0;
ALTER TABLE weapon_configs ADD COLUMN incident_merge_gap_seconds REAL NOT NULL DEFAULT 3.0;

INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
VALUES (3, 'incident_grouping_and_realtime_pipeline', strftime('%Y-%m-%dT%H:%M:%fZ','now'));
