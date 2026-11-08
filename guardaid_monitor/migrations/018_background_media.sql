BEGIN IMMEDIATE;
CREATE TABLE media_tasks (
    id TEXT PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    alert_id TEXT NOT NULL REFERENCES alerts(id),
    camera_id TEXT NOT NULL REFERENCES cameras(id),
    kind TEXT NOT NULL CHECK(kind IN ('weapon_evidence','weapon_context','violence_context','final_incident')),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    priority INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','failed','cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    available_at REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    error_code TEXT
);
CREATE INDEX media_tasks_pending ON media_tasks(status,priority,available_at,created_at);
CREATE INDEX media_tasks_camera ON media_tasks(camera_id,status);
INSERT INTO schema_migrations(version,name,applied_at) VALUES (18,'background_media',strftime('%Y-%m-%dT%H:%M:%f','now') || '000Z');
COMMIT;
