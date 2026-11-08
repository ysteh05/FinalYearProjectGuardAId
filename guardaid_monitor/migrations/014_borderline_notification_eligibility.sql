PRAGMA foreign_keys = ON;

ALTER TABLE borderline_review_tasks
ADD COLUMN supplemental_notification_status TEXT NOT NULL DEFAULT 'not_applicable'
CHECK (supplemental_notification_status IN (
    'not_applicable','eligible','handled','suppressed_disabled'
));

-- Existing positive reviews predate this delivery marker. Leave them terminal
-- so deploying the migration never replays historical Borderline notifications.

INSERT INTO schema_migrations(version,name,applied_at)
VALUES (14,'borderline_notification_eligibility',strftime('%Y-%m-%dT%H:%M:%fZ','now'));
