PRAGMA foreign_keys = ON;

ALTER TABLE alerts ADD COLUMN evidence_status TEXT NOT NULL DEFAULT 'legacy_unknown'
    CHECK (evidence_status IN ('preparing','available','failed','legacy_unknown'));

UPDATE alerts
SET evidence_status = CASE
    WHEN EXISTS (SELECT 1 FROM evidence_records e WHERE e.alert_id=alerts.id)
        THEN 'available'
    ELSE 'legacy_unknown'
END;

INSERT INTO schema_migrations(version,name,applied_at)
VALUES (13,'alert_evidence_state',strftime('%Y-%m-%dT%H:%M:%fZ','now'));
