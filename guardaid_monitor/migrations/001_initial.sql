PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS locations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    building TEXT,
    floor TEXT,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    firebase_uid TEXT NOT NULL UNIQUE,
    email_cache TEXT,
    display_name TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    assigned_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TEXT NOT NULL,
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    installation_id TEXT,
    fcm_registration_value TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL CHECK (platform IN ('android','ios','web')),
    device_label TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_files (
    id TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    original_filename TEXT,
    media_kind TEXT NOT NULL CHECK (media_kind IN ('camera_source','evidence_clip','evidence_image')),
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    sha256 TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cameras (
    id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    status TEXT NOT NULL CHECK (status IN ('ready','analysing','paused','error')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS camera_sources (
    id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    media_file_id TEXT REFERENCES media_files(id) ON DELETE RESTRICT,
    source_type TEXT NOT NULL CHECK (source_type IN ('uploaded_video','rtsp')),
    rtsp_endpoint_ref TEXT,
    duration_seconds REAL NOT NULL DEFAULT 0 CHECK (duration_seconds >= 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at TEXT NOT NULL,
    CHECK (
        (source_type = 'uploaded_video' AND media_file_id IS NOT NULL AND rtsp_endpoint_ref IS NULL)
        OR (source_type = 'rtsp' AND media_file_id IS NULL AND rtsp_endpoint_ref IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_camera_active_source
ON camera_sources(camera_id) WHERE is_active = 1;

CREATE TABLE IF NOT EXISTS model_versions (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    model_name TEXT NOT NULL,
    version TEXT NOT NULL,
    framework TEXT,
    quantization TEXT,
    checkpoint_ref TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(task, model_name, version)
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    version TEXT NOT NULL UNIQUE,
    content_sha256 TEXT,
    content TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config_versions (
    id TEXT PRIMARY KEY,
    version_number INTEGER NOT NULL UNIQUE CHECK (version_number >= 1),
    default_sensitivity TEXT NOT NULL CHECK (default_sensitivity IN ('sensitive','moderate','certain')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS violence_configs (
    config_version_id TEXT PRIMARY KEY REFERENCES config_versions(id) ON DELETE CASCADE,
    segment_seconds REAL NOT NULL,
    stride_seconds REAL NOT NULL,
    detector_input_frames INTEGER NOT NULL,
    sensitive_threshold REAL NOT NULL,
    moderate_threshold REAL NOT NULL,
    certain_threshold REAL NOT NULL,
    cooldown_seconds INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS weapon_configs (
    config_version_id TEXT PRIMARY KEY REFERENCES config_versions(id) ON DELETE CASCADE,
    sample_fps REAL NOT NULL,
    confidence_threshold REAL NOT NULL,
    n_required INTEGER NOT NULL,
    m_window INTEGER NOT NULL,
    cooldown_seconds INTEGER NOT NULL,
    CHECK (n_required >= 1 AND n_required <= m_window)
);

CREATE TABLE IF NOT EXISTS vlm_configs (
    config_version_id TEXT PRIMARY KEY REFERENCES config_versions(id) ON DELETE CASCADE,
    model_id TEXT NOT NULL,
    quantization TEXT NOT NULL,
    input_fps REAL NOT NULL,
    video_token_budget INTEGER NOT NULL,
    max_output_tokens INTEGER NOT NULL,
    borderline_review_enabled INTEGER NOT NULL CHECK (borderline_review_enabled IN (0,1)),
    scene_description_enabled INTEGER NOT NULL CHECK (scene_description_enabled IN (0,1)),
    binary_prompt_version TEXT NOT NULL,
    scene_prompt_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL REFERENCES cameras(id) ON DELETE RESTRICT,
    incident_type TEXT NOT NULL CHECK (incident_type IN ('violence','physical_contact_bullying','weapon')),
    status TEXT NOT NULL CHECK (status IN ('open','in_review','resolved','dismissed','escalated')),
    sensitivity_mode TEXT NOT NULL CHECK (sensitivity_mode IN ('sensitive','moderate','certain')),
    is_borderline_promoted INTEGER NOT NULL DEFAULT 0 CHECK (is_borderline_promoted IN (0,1)),
    opening_config_version_id TEXT NOT NULL REFERENCES config_versions(id) ON DELETE RESTRICT,
    opening_model_version_id TEXT NOT NULL REFERENCES model_versions(id) ON DELETE RESTRICT,
    binary_prompt_version_id TEXT REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS detection_events (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    model_version_id TEXT NOT NULL REFERENCES model_versions(id) ON DELETE RESTRICT,
    config_version_id TEXT NOT NULL REFERENCES config_versions(id) ON DELETE RESTRICT,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    decision_source TEXT NOT NULL CHECK (decision_source IN ('threshold','vlm_promoted','weapon_n_of_m')),
    source_start_seconds REAL,
    source_end_seconds REAL,
    detected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS borderline_reviews (
    id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL REFERENCES cameras(id) ON DELETE RESTRICT,
    alert_id TEXT REFERENCES alerts(id) ON DELETE SET NULL,
    model_version_id TEXT NOT NULL REFERENCES model_versions(id) ON DELETE RESTRICT,
    prompt_version_id TEXT NOT NULL REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    config_version_id TEXT NOT NULL REFERENCES config_versions(id) ON DELETE RESTRICT,
    detector_confidence REAL NOT NULL,
    raise_alert INTEGER NOT NULL CHECK (raise_alert IN (0,1)),
    uncertainty TEXT CHECK (uncertainty IN ('low','medium','high')),
    brief_reason TEXT,
    source_start_seconds REAL,
    source_end_seconds REAL,
    reviewed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_records (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    media_file_id TEXT NOT NULL UNIQUE REFERENCES media_files(id) ON DELETE RESTRICT,
    evidence_type TEXT NOT NULL CHECK (evidence_type IN ('violence_clip','bullying_clip','weapon_screenshot')),
    source_start_seconds REAL,
    source_end_seconds REAL,
    captured_at TEXT NOT NULL,
    delete_after TEXT
);

CREATE TABLE IF NOT EXISTS vlm_descriptions (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    model_version_id TEXT NOT NULL REFERENCES model_versions(id) ON DELETE RESTRICT,
    prompt_version_id TEXT REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('pending','ready','failed','disabled')),
    structured_json TEXT,
    display_text TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    data_payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id TEXT PRIMARY KEY,
    notification_id TEXT NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('queued','sent','failed','invalid_device')),
    provider_message_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    error_code TEXT,
    UNIQUE(notification_id, device_id)
);

CREATE TABLE IF NOT EXISTS alert_actions (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK (action IN ('acknowledge','review','dismiss','resolve','escalate')),
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    detail_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_cameras_location ON cameras(location_id);
CREATE INDEX IF NOT EXISTS ix_alerts_camera_status ON alerts(camera_id, status);
CREATE INDEX IF NOT EXISTS ix_detection_alert_time ON detection_events(alert_id, detected_at);
CREATE INDEX IF NOT EXISTS ix_borderline_camera_time ON borderline_reviews(camera_id, reviewed_at);
CREATE INDEX IF NOT EXISTS ix_evidence_alert ON evidence_records(alert_id);
CREATE INDEX IF NOT EXISTS ix_vlm_description_alert_time ON vlm_descriptions(alert_id, created_at);
CREATE INDEX IF NOT EXISTS ix_device_user_enabled ON devices(user_id, enabled);
CREATE INDEX IF NOT EXISTS ix_delivery_status ON notification_deliveries(status);

INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
VALUES (1, 'normalized_sqlite_initial', strftime('%Y-%m-%dT%H:%M:%fZ','now'));

INSERT OR IGNORE INTO roles(id, name, description, created_at) VALUES
('role_admin', 'admin', 'System administration', strftime('%Y-%m-%dT%H:%M:%fZ','now')),
('role_reviewer', 'reviewer', 'Review and resolve alerts', strftime('%Y-%m-%dT%H:%M:%fZ','now')),
('role_principal', 'principal', 'Authorised school leadership access', strftime('%Y-%m-%dT%H:%M:%fZ','now')),
('role_security_staff', 'security_staff', 'Operational alert response', strftime('%Y-%m-%dT%H:%M:%fZ','now'));

