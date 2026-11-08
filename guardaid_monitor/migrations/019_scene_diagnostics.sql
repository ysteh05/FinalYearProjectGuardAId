BEGIN IMMEDIATE;
CREATE TABLE scene_generation_tasks (
    alert_id TEXT PRIMARY KEY REFERENCES alerts(id) ON DELETE CASCADE,
    state_json TEXT NOT NULL CHECK(json_valid(state_json)),
    diagnostic_json TEXT NOT NULL CHECK(json_valid(diagnostic_json))
);
INSERT INTO schema_migrations(version,name,applied_at) VALUES (19,'scene_diagnostics',strftime('%Y-%m-%dT%H:%M:%f','now') || '000Z');
COMMIT;
