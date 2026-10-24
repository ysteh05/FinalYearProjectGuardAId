PRAGMA foreign_keys = ON;

-- Keep the constrained legacy alerts.status column intact. New workflow state is
-- stored alongside it so existing alerts, evidence and foreign keys survive.
CREATE TABLE IF NOT EXISTS mobile_alert_workflows (
    alert_id TEXT PRIMARY KEY REFERENCES alerts(id) ON DELETE CASCADE,
    workflow_status TEXT NOT NULL CHECK (workflow_status IN (
        'awaiting_confirmation', 'confirmed_awaiting_handler', 'in_progress',
        'resolved', 'dismissed', 'legacy_in_review', 'legacy_escalated'
    )),
    handler_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    active_phase_id TEXT,
    broadcast_role_ids_json TEXT NOT NULL DEFAULT '[]',
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mobile_alert_workflow_events (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    actor_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'dismiss_false_alarm', 'confirm_only', 'confirm_and_handle',
        'confirm_resolved', 'take_ownership', 'resolve', 'simulated_emergency_call'
    )),
    comment TEXT,
    broadcast_role_ids_json TEXT NOT NULL DEFAULT '[]',
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(alert_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_mobile_alert_workflow_events_alert_time
    ON mobile_alert_workflow_events(alert_id, created_at DESC);

CREATE TABLE IF NOT EXISTS mobile_alert_reminder_phases (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    phase_kind TEXT NOT NULL CHECK (phase_kind IN ('awaiting_decision','awaiting_handler')),
    phase_status TEXT NOT NULL CHECK (phase_status IN ('active','cancelled','completed')),
    started_at TEXT NOT NULL,
    first_after_seconds INTEGER NOT NULL CHECK (first_after_seconds BETWEEN 15 AND 3600),
    all_after_seconds INTEGER NOT NULL CHECK (all_after_seconds BETWEEN 30 AND 7200),
    first_due_at TEXT NOT NULL,
    all_due_at TEXT NOT NULL,
    broadcast_role_ids_json TEXT NOT NULL DEFAULT '[]',
    initiated_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    source_notification_id TEXT REFERENCES notifications(id) ON DELETE SET NULL,
    cancel_reason TEXT,
    completed_at TEXT,
    CHECK (all_after_seconds > first_after_seconds)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_mobile_alert_one_active_reminder_phase
    ON mobile_alert_reminder_phases(alert_id) WHERE phase_status='active';
CREATE INDEX IF NOT EXISTS ix_mobile_alert_reminder_phase_due
    ON mobile_alert_reminder_phases(phase_status, first_due_at, all_due_at);

-- Create reminder phases before populating workflow rows. This ordering also
-- recovers safely if a prior migration attempt left only the workflow table,
-- whose active_phase_id may already have an FK to this not-yet-created table.
INSERT OR IGNORE INTO mobile_alert_workflows(
    alert_id, workflow_status, handler_user_id, broadcast_role_ids_json, revision, updated_at
)
SELECT a.id,
       CASE a.status
         WHEN 'open' THEN 'awaiting_confirmation'
         WHEN 'in_review' THEN 'legacy_in_review'
         WHEN 'resolved' THEN 'resolved'
         WHEN 'dismissed' THEN 'dismissed'
         WHEN 'escalated' THEN 'legacy_escalated'
       END,
       NULL, '[]', 0, a.created_at
FROM alerts a;

CREATE TABLE IF NOT EXISTS mobile_alert_reminder_dispatches (
    phase_id TEXT NOT NULL REFERENCES mobile_alert_reminder_phases(id) ON DELETE CASCADE,
    stage INTEGER NOT NULL CHECK (stage IN (1,2)),
    status TEXT NOT NULL CHECK (status IN (
        'dispatching','queued','sent','partial','failed','no_recipients',
        'skipped_stopped','skipped_superseded','skipped_responded'
    )),
    notification_id TEXT REFERENCES notifications(id) ON DELETE SET NULL,
    recipient_count INTEGER NOT NULL DEFAULT 0 CHECK (recipient_count >= 0),
    sent_count INTEGER NOT NULL DEFAULT 0 CHECK (sent_count >= 0),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    error_code TEXT,
    claimed_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (phase_id, stage)
);

-- Durable push outbox. Recipients are users; device delivery is tracked
-- separately so one user with multiple devices receives one copy per device.
CREATE TABLE IF NOT EXISTS mobile_notification_outbox (
    id TEXT PRIMARY KEY,
    notification_id TEXT NOT NULL UNIQUE REFERENCES notifications(id) ON DELETE CASCADE,
    event_key TEXT NOT NULL UNIQUE,
    channel_id TEXT NOT NULL,
    data_payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'pending','dispatching','sent','partial','failed','no_recipients'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at TEXT NOT NULL,
    claimed_at TEXT,
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mobile_notification_outbox_due
    ON mobile_notification_outbox(status, next_attempt_at);

CREATE TABLE IF NOT EXISTS mobile_notification_outbox_recipients (
    outbox_id TEXT NOT NULL REFERENCES mobile_notification_outbox(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (outbox_id, user_id)
);

CREATE TABLE IF NOT EXISTS mobile_notification_outbox_deliveries (
    outbox_id TEXT NOT NULL REFERENCES mobile_notification_outbox(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('sent','failed')),
    provider_message_id TEXT,
    error_code TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 1 CHECK (attempt_count >= 1),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (outbox_id, device_id)
);

-- Preserve unfinished legacy schedules as Phase A. Existing dispatch attempts
-- are copied so a restart cannot replay a reminder stage already attempted.
INSERT OR IGNORE INTO mobile_alert_reminder_phases(
    id, alert_id, phase_kind, phase_status, started_at,
    first_after_seconds, all_after_seconds, first_due_at, all_due_at,
    broadcast_role_ids_json, source_notification_id
)
SELECT 'legacy_phase_' || s.alert_id, s.alert_id, 'awaiting_decision', 'active',
       s.initial_notified_at, s.same_audience_repeat_after_seconds,
       s.all_users_escalation_after_seconds, s.same_audience_repeat_due_at,
       s.all_users_escalation_due_at, '[]', s.initial_notification_id
FROM alert_notification_escalation_schedules s
JOIN alerts a ON a.id=s.alert_id
WHERE a.status='open'
  AND NOT EXISTS (
      SELECT 1 FROM alert_actions aa
      WHERE aa.alert_id=a.id AND aa.action IN ('dismiss','resolve','escalate')
  );

INSERT OR IGNORE INTO mobile_alert_reminder_dispatches(
    phase_id, stage, status, notification_id, recipient_count, sent_count,
    failed_count, error_code, claimed_at, completed_at
)
SELECT 'legacy_phase_' || d.alert_id, d.stage, d.status, d.notification_id,
       d.recipient_count, d.sent_count, d.failed_count, d.error_code,
       d.claimed_at, d.completed_at
FROM alert_notification_escalation_dispatches d
JOIN mobile_alert_reminder_phases p ON p.id='legacy_phase_' || d.alert_id;

UPDATE mobile_alert_workflows
SET active_phase_id=(
    SELECT p.id FROM mobile_alert_reminder_phases p
    WHERE p.alert_id=mobile_alert_workflows.alert_id AND p.phase_status='active'
    ORDER BY p.started_at DESC LIMIT 1
)
WHERE EXISTS (
    SELECT 1 FROM mobile_alert_reminder_phases p
    WHERE p.alert_id=mobile_alert_workflows.alert_id AND p.phase_status='active'
);

INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
VALUES (11, 'mobile_alert_response_workflow', strftime('%Y-%m-%dT%H:%M:%fZ','now'));
