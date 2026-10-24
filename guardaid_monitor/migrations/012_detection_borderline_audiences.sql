PRAGMA foreign_keys = ON;

ALTER TABLE alerts ADD COLUMN borderline_review_status TEXT NOT NULL DEFAULT 'not_applicable'
    CHECK (borderline_review_status IN (
        'not_applicable','disabled','queued','running','positive','negative',
        'failed','cancelled','legacy_unknown'
    ));
UPDATE alerts
SET borderline_review_status='legacy_unknown'
WHERE is_provisional=1 AND borderline_review_status='not_applicable';

ALTER TABLE detection_events ADD COLUMN qualifications_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE detection_events ADD COLUMN weapon_class_scores_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE borderline_role_settings (
    role_id TEXT PRIMARY KEY REFERENCES roles(id) ON DELETE CASCADE,
    default_enabled INTEGER NOT NULL DEFAULT 0 CHECK (default_enabled IN (0,1)),
    updated_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE borderline_user_settings (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
    setting_source TEXT NOT NULL CHECK (setting_source IN ('migration','role_default','role_bulk','individual')),
    updated_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE borderline_settings_revision (
    id TEXT PRIMARY KEY CHECK (id='global'),
    revision INTEGER NOT NULL CHECK (revision >= 1)
);
INSERT INTO borderline_settings_revision(id,revision) VALUES ('global',1);

INSERT INTO borderline_role_settings(role_id,default_enabled,updated_at)
SELECT r.id,
       CASE WHEN m.borderline_alert_enabled=1
                  AND EXISTS (
                      SELECT 1 FROM json_each(m.borderline_recipient_roles_json) j
                      WHERE j.value=r.id
                  )
            THEN 1 ELSE 0 END,
       m.updated_at
FROM roles r CROSS JOIN mobile_settings m
WHERE m.id='global';

INSERT INTO borderline_user_settings(user_id,enabled,setting_source,updated_at)
SELECT u.id,
       CASE WHEN m.borderline_alert_enabled=1
                  AND EXISTS (
                      SELECT 1 FROM user_roles ur
                      JOIN json_each(m.borderline_recipient_roles_json) j ON j.value=ur.role_id
                      WHERE ur.user_id=u.id
                  )
            THEN 1 ELSE 0 END,
       'migration',m.updated_at
FROM users u CROSS JOIN mobile_settings m
WHERE m.id='global'
  AND EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id=u.id);

CREATE TABLE borderline_review_tasks (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    camera_id TEXT NOT NULL REFERENCES cameras(id) ON DELETE RESTRICT,
    detection_event_key TEXT NOT NULL UNIQUE REFERENCES detection_events(id) ON DELETE CASCADE,
    detector_confidence REAL NOT NULL CHECK (detector_confidence BETWEEN 0 AND 1),
    source_start_seconds REAL NOT NULL CHECK (source_start_seconds >= 0),
    source_end_seconds REAL NOT NULL CHECK (source_end_seconds >= source_start_seconds),
    context_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','running','positive','negative','failed','cancelled')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 3),
    raise_alert INTEGER CHECK (raise_alert IN (0,1)),
    uncertainty TEXT CHECK (uncertainty IN ('low','medium','high')),
    brief_reason TEXT,
    error_code TEXT,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    config_version INTEGER NOT NULL,
    vlm_settings_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
CREATE INDEX ix_borderline_review_tasks_status_created
    ON borderline_review_tasks(status,created_at);
CREATE INDEX ix_borderline_review_tasks_alert
    ON borderline_review_tasks(alert_id,created_at DESC);

CREATE TABLE alert_notification_audience (
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    qualification_reasons_json TEXT NOT NULL DEFAULT '[]',
    first_notification_id TEXT REFERENCES notifications(id) ON DELETE SET NULL,
    first_queued_at TEXT NOT NULL,
    first_sent_at TEXT,
    PRIMARY KEY(alert_id,user_id)
);
CREATE INDEX ix_alert_notification_audience_user
    ON alert_notification_audience(user_id,alert_id);

CREATE TABLE mobile_alert_reminder_audience (
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    phase_id TEXT NOT NULL REFERENCES mobile_alert_reminder_phases(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stage INTEGER NOT NULL CHECK (stage IN (1,2)),
    status TEXT NOT NULL CHECK (status IN ('queued','sent','failed','skipped')),
    outbox_id TEXT REFERENCES mobile_notification_outbox(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(alert_id,phase_id,user_id,stage)
);

-- Preserve exact user-level recipients from already queued initial-alert pushes.
INSERT OR IGNORE INTO alert_notification_audience(
    alert_id,user_id,qualification_reasons_json,first_notification_id,first_queued_at,first_sent_at
)
SELECT DISTINCT n.alert_id,nr.user_id,'["legacy"]',n.id,n.created_at,
       CASE WHEN EXISTS (
           SELECT 1 FROM notification_deliveries nd
           JOIN devices d ON d.id=nd.device_id
           WHERE nd.notification_id=n.id AND nd.status='sent' AND d.user_id=nr.user_id
       ) THEN n.created_at ELSE NULL END
FROM notifications n
JOIN notification_recipients nr ON nr.notification_id=n.id AND nr.user_id IS NOT NULL
WHERE json_extract(n.data_payload_json,'$.source')='alert_created';

INSERT OR IGNORE INTO schema_migrations(version,name,applied_at)
VALUES (12,'detection_borderline_audiences',strftime('%Y-%m-%dT%H:%M:%fZ','now'));
