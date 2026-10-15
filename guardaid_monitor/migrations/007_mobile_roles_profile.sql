PRAGMA foreign_keys = ON;

ALTER TABLE roles ADD COLUMN display_name TEXT;
ALTER TABLE roles ADD COLUMN can_manage_roles INTEGER NOT NULL DEFAULT 0 CHECK (can_manage_roles IN (0,1));
ALTER TABLE roles ADD COLUMN can_manage_users INTEGER NOT NULL DEFAULT 0 CHECK (can_manage_users IN (0,1));
ALTER TABLE roles ADD COLUMN can_manage_borderline_settings INTEGER NOT NULL DEFAULT 0 CHECK (can_manage_borderline_settings IN (0,1));
ALTER TABLE roles ADD COLUMN updated_at TEXT NOT NULL DEFAULT '';

UPDATE roles
SET display_name = CASE name
    WHEN 'school_administrator' THEN 'School Administrator'
    WHEN 'security_personnel' THEN 'Security Personnel'
    WHEN 'teacher' THEN 'Teacher'
    WHEN 'admin' THEN 'Administrator'
    ELSE replace(replace(name, '_', ' '), '-', ' ')
END,
updated_at = COALESCE(NULLIF(updated_at, ''), created_at),
can_manage_roles = CASE WHEN name IN ('school_administrator','admin') THEN 1 ELSE can_manage_roles END,
can_manage_users = CASE WHEN name IN ('school_administrator','admin') THEN 1 ELSE can_manage_users END,
can_manage_borderline_settings = CASE WHEN name IN ('school_administrator','admin') THEN 1 ELSE can_manage_borderline_settings END;

INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
VALUES (7, 'mobile_roles_profile', strftime('%Y-%m-%dT%H:%M:%fZ','now'));
