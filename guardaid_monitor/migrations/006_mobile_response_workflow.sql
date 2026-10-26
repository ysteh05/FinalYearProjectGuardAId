PRAGMA foreign_keys = ON;

ALTER TABLE roles ADD COLUMN sensitivity_mode TEXT NOT NULL DEFAULT 'moderate'
CHECK (sensitivity_mode IN ('sensitive','moderate','certain'));

ALTER TABLE roles ADD COLUMN is_system INTEGER NOT NULL DEFAULT 0
CHECK (is_system IN (0,1));

INSERT OR IGNORE INTO roles(id, name, description, sensitivity_mode, is_system, created_at) VALUES
('role_school_administrator', 'school_administrator', 'Manage users, roles, thresholds and incident response.', 'moderate', 1, strftime('%Y-%m-%dT%H:%M:%fZ','now')),
('role_security_personnel', 'security_personnel', 'Receive, review and escalate operational alerts.', 'sensitive', 1, strftime('%Y-%m-%dT%H:%M:%fZ','now')),
('role_teacher', 'teacher', 'Receive relevant alerts and support incident review.', 'certain', 1, strftime('%Y-%m-%dT%H:%M:%fZ','now'));

CREATE TABLE notification_recipients (
    notification_id TEXT NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    role_id TEXT REFERENCES roles(id) ON DELETE CASCADE,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    CHECK ((role_id IS NOT NULL AND user_id IS NULL) OR (role_id IS NULL AND user_id IS NOT NULL)),
    UNIQUE(notification_id, role_id, user_id)
);

CREATE TABLE notification_reads (
    notification_id TEXT NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    read_at TEXT NOT NULL,
    PRIMARY KEY (notification_id, user_id)
);

CREATE TABLE mobile_settings (
    id TEXT PRIMARY KEY CHECK (id = 'global'),
    borderline_alert_enabled INTEGER NOT NULL DEFAULT 1 CHECK (borderline_alert_enabled IN (0,1)),
    borderline_recipient_roles_json TEXT NOT NULL,
    emergency_call_simulation_only INTEGER NOT NULL DEFAULT 1 CHECK (emergency_call_simulation_only = 1),
    updated_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO mobile_settings(
    id, borderline_alert_enabled, borderline_recipient_roles_json,
    emergency_call_simulation_only, updated_at
) VALUES (
    'global', 1,
    '["role_school_administrator","role_security_personnel"]',
    1, strftime('%Y-%m-%dT%H:%M:%fZ','now')
);

ALTER TABLE alert_actions RENAME TO alert_actions_v5;

CREATE TABLE alert_actions (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK (
        action IN ('acknowledge','review','dismiss','resolve','escalate','simulated_emergency_call')
    ),
    note TEXT,
    created_at TEXT NOT NULL
);

INSERT INTO alert_actions(id, alert_id, user_id, action, note, created_at)
SELECT id, alert_id, user_id, action, note, created_at FROM alert_actions_v5;

DROP TABLE alert_actions_v5;

CREATE INDEX ix_notification_recipient_role ON notification_recipients(role_id, notification_id);
CREATE INDEX ix_notification_recipient_user ON notification_recipients(user_id, notification_id);
CREATE INDEX ix_mobile_user_role ON user_roles(role_id, user_id);
CREATE INDEX ix_alert_actions_alert_time ON alert_actions(alert_id, created_at);

INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
VALUES (6, 'mobile_response_workflow', strftime('%Y-%m-%dT%H:%M:%fZ','now'));
