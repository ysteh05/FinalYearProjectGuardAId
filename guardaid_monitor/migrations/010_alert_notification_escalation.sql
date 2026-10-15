CREATE TABLE IF NOT EXISTS alert_notification_escalation_configs (
    config_version_id TEXT PRIMARY KEY REFERENCES config_versions(id) ON DELETE CASCADE,
    same_audience_repeat_after_seconds INTEGER NOT NULL DEFAULT 60
        CHECK (same_audience_repeat_after_seconds BETWEEN 15 AND 3600),
    all_users_escalation_after_seconds INTEGER NOT NULL DEFAULT 120
        CHECK (all_users_escalation_after_seconds BETWEEN 30 AND 7200),
    CHECK (all_users_escalation_after_seconds > same_audience_repeat_after_seconds)
);

INSERT OR IGNORE INTO alert_notification_escalation_configs(
    config_version_id, same_audience_repeat_after_seconds, all_users_escalation_after_seconds
)
SELECT id, 60, 120 FROM config_versions;

CREATE TABLE IF NOT EXISTS alert_notification_escalation_schedules (
    alert_id TEXT PRIMARY KEY REFERENCES alerts(id) ON DELETE CASCADE,
    initial_notification_id TEXT NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    same_audience_repeat_after_seconds INTEGER NOT NULL
        CHECK (same_audience_repeat_after_seconds BETWEEN 15 AND 3600),
    all_users_escalation_after_seconds INTEGER NOT NULL
        CHECK (all_users_escalation_after_seconds BETWEEN 30 AND 7200),
    initial_notified_at TEXT NOT NULL,
    same_audience_repeat_due_at TEXT NOT NULL,
    all_users_escalation_due_at TEXT NOT NULL,
    CHECK (all_users_escalation_after_seconds > same_audience_repeat_after_seconds)
);

CREATE TABLE IF NOT EXISTS alert_notification_escalation_dispatches (
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    stage INTEGER NOT NULL CHECK (stage IN (1, 2)),
    status TEXT NOT NULL CHECK (
        status IN (
            'dispatching', 'sent', 'partial', 'failed', 'no_recipients',
            'skipped_responded', 'skipped_superseded'
        )
    ),
    notification_id TEXT REFERENCES notifications(id) ON DELETE SET NULL,
    recipient_count INTEGER NOT NULL DEFAULT 0 CHECK (recipient_count >= 0),
    sent_count INTEGER NOT NULL DEFAULT 0 CHECK (sent_count >= 0),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    error_code TEXT,
    claimed_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (alert_id, stage)
);

CREATE INDEX IF NOT EXISTS ix_alert_notification_repeat_due
    ON alert_notification_escalation_schedules(same_audience_repeat_due_at);
CREATE INDEX IF NOT EXISTS ix_alert_notification_all_users_due
    ON alert_notification_escalation_schedules(all_users_escalation_due_at);

INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
VALUES (10, 'alert_notification_escalation', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
