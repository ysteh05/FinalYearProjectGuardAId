ALTER TABLE config_versions
ADD COLUMN detection_debug_mode_enabled INTEGER NOT NULL DEFAULT 0
CHECK (detection_debug_mode_enabled IN (0, 1));

INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
VALUES (8, 'detection_debug_mode', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
