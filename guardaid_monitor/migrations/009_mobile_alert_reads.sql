CREATE TABLE IF NOT EXISTS alert_reads (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    read_at TEXT NOT NULL,
    PRIMARY KEY (user_id, alert_id)
);

CREATE INDEX IF NOT EXISTS ix_alert_reads_alert ON alert_reads(alert_id);

INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
VALUES (9, 'mobile_alert_reads', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
