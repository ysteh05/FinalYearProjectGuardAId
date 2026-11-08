PRAGMA foreign_keys = ON;

ALTER TABLE vlm_configs
ADD COLUMN scene_description_frames INTEGER NOT NULL DEFAULT 20
CHECK (
    scene_description_frames >= 4
    AND scene_description_frames <= 128
    AND scene_description_frames % 2 = 0
);

INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
VALUES (5, 'scene_description_uniform_frames', strftime('%Y-%m-%dT%H:%M:%fZ','now'));
