BEGIN IMMEDIATE;

ALTER TABLE alerts ADD COLUMN workflow_revision INTEGER NOT NULL DEFAULT 0 CHECK(workflow_revision >= 0);
UPDATE alerts SET workflow_revision=revision;

CREATE TRIGGER alert_decision_revision AFTER UPDATE OF workflow_status,handler_user_id,broadcast_role_snapshot_json ON alerts
WHEN OLD.workflow_status IS NOT NEW.workflow_status
  OR OLD.handler_user_id IS NOT NEW.handler_user_id
  OR OLD.broadcast_role_snapshot_json IS NOT NEW.broadcast_role_snapshot_json
BEGIN
    UPDATE alerts SET workflow_revision=OLD.workflow_revision+1 WHERE id=NEW.id;
END;

INSERT INTO schema_migrations(version,name,applied_at)
VALUES(17,'workflow_decision_revision',strftime('%Y-%m-%dT%H:%M:%f','now') || '000Z');
COMMIT;
