CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE locations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    building TEXT,
    floor TEXT,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE users (
    id TEXT PRIMARY KEY,
    firebase_uid TEXT NOT NULL UNIQUE,
    email_cache TEXT,
    display_name TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE roles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT NOT NULL
, sensitivity_mode TEXT NOT NULL DEFAULT 'moderate'
CHECK (sensitivity_mode IN ('sensitive','moderate','certain')), is_system INTEGER NOT NULL DEFAULT 0
CHECK (is_system IN (0,1)), display_name TEXT, can_manage_roles INTEGER NOT NULL DEFAULT 0 CHECK (can_manage_roles IN (0,1)), can_manage_users INTEGER NOT NULL DEFAULT 0 CHECK (can_manage_users IN (0,1)), can_manage_borderline_settings INTEGER NOT NULL DEFAULT 0 CHECK (can_manage_borderline_settings IN (0,1)), updated_at TEXT NOT NULL DEFAULT '');

CREATE TABLE user_roles (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    assigned_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TEXT NOT NULL,
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE devices (
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

CREATE TABLE media_files (
    id TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    original_filename TEXT,
    media_kind TEXT NOT NULL CHECK (media_kind IN ('camera_source','evidence_clip','evidence_image')),
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    sha256 TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE cameras (
    id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    status TEXT NOT NULL CHECK (status IN ('ready','analysing','paused','error')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE camera_sources (
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

CREATE TABLE model_versions (
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

CREATE TABLE prompt_versions (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    version TEXT NOT NULL UNIQUE,
    content_sha256 TEXT,
    content TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE config_versions (
    id TEXT PRIMARY KEY,
    version_number INTEGER NOT NULL UNIQUE CHECK (version_number >= 1),
    default_sensitivity TEXT NOT NULL CHECK (default_sensitivity IN ('sensitive','moderate','certain')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
, detection_debug_mode_enabled INTEGER NOT NULL DEFAULT 0
CHECK (detection_debug_mode_enabled IN (0, 1)));

CREATE TABLE violence_configs (
    config_version_id TEXT PRIMARY KEY REFERENCES config_versions(id) ON DELETE CASCADE,
    segment_seconds REAL NOT NULL,
    stride_seconds REAL NOT NULL,
    detector_input_frames INTEGER NOT NULL,
    sensitive_threshold REAL NOT NULL,
    moderate_threshold REAL NOT NULL,
    certain_threshold REAL NOT NULL,
    cooldown_seconds INTEGER NOT NULL
, incident_merge_gap_seconds REAL NOT NULL DEFAULT 5.0);

CREATE TABLE weapon_configs (
    config_version_id TEXT PRIMARY KEY REFERENCES config_versions(id) ON DELETE CASCADE,
    sample_fps REAL NOT NULL,
    confidence_threshold REAL NOT NULL,
    n_required INTEGER NOT NULL,
    m_window INTEGER NOT NULL,
    cooldown_seconds INTEGER NOT NULL, nms_candidate_floor REAL NOT NULL DEFAULT 0.001, nms_iou_threshold REAL NOT NULL DEFAULT 0.45, class_thresholds_json TEXT NOT NULL DEFAULT '{"sensitive":{"parang_machete":0.24,"knife":0.24,"baseball_bat":0.34,"long_gun":0.21,"gun":0.24},"moderate":{"parang_machete":0.42,"knife":0.42,"baseball_bat":0.5,"long_gun":0.54,"gun":0.42},"certain":{"parang_machete":0.42,"knife":0.63,"baseball_bat":0.65,"long_gun":0.66,"gun":0.57}}', incident_merge_gap_seconds REAL NOT NULL DEFAULT 3.0,
    CHECK (n_required >= 1 AND n_required <= m_window)
);

CREATE TABLE vlm_configs (
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
, scene_description_frames INTEGER NOT NULL DEFAULT 20
CHECK (
    scene_description_frames >= 4
    AND scene_description_frames <= 128
    AND scene_description_frames % 2 = 0
));

CREATE TABLE alerts (
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
, is_provisional INTEGER NOT NULL DEFAULT 0
CHECK (is_provisional IN (0,1)), borderline_review_status TEXT NOT NULL DEFAULT 'not_applicable'
    CHECK (borderline_review_status IN (
        'not_applicable','disabled','queued','running','positive','negative',
        'failed','cancelled','legacy_unknown'
    )), evidence_status TEXT NOT NULL DEFAULT 'legacy_unknown'
    CHECK (evidence_status IN ('preparing','available','failed','legacy_unknown')));

CREATE TABLE detection_events (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    model_version_id TEXT NOT NULL REFERENCES model_versions(id) ON DELETE RESTRICT,
    config_version_id TEXT NOT NULL REFERENCES config_versions(id) ON DELETE RESTRICT,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    decision_source TEXT NOT NULL CHECK (decision_source IN ('threshold','vlm_promoted','weapon_n_of_m')),
    source_start_seconds REAL,
    source_end_seconds REAL,
    detected_at TEXT NOT NULL
, qualifications_json TEXT NOT NULL DEFAULT '{}', weapon_class_scores_json TEXT NOT NULL DEFAULT '{}');

CREATE TABLE borderline_reviews (
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

CREATE TABLE evidence_records (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    media_file_id TEXT NOT NULL UNIQUE REFERENCES media_files(id) ON DELETE RESTRICT,
    evidence_type TEXT NOT NULL CHECK (evidence_type IN ('violence_clip','bullying_clip','weapon_screenshot')),
    source_start_seconds REAL,
    source_end_seconds REAL,
    captured_at TEXT NOT NULL,
    delete_after TEXT
);

CREATE TABLE vlm_descriptions (
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

CREATE TABLE notifications (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    data_payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE notification_deliveries (
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

CREATE TABLE audit_logs (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    detail_json TEXT,
    created_at TEXT NOT NULL
);

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

CREATE TABLE alert_reads (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    read_at TEXT NOT NULL,
    PRIMARY KEY (user_id, alert_id)
);

CREATE TABLE alert_notification_escalation_configs (
    config_version_id TEXT PRIMARY KEY REFERENCES config_versions(id) ON DELETE CASCADE,
    same_audience_repeat_after_seconds INTEGER NOT NULL DEFAULT 60
        CHECK (same_audience_repeat_after_seconds BETWEEN 15 AND 3600),
    all_users_escalation_after_seconds INTEGER NOT NULL DEFAULT 120
        CHECK (all_users_escalation_after_seconds BETWEEN 30 AND 7200),
    CHECK (all_users_escalation_after_seconds > same_audience_repeat_after_seconds)
);

CREATE TABLE alert_notification_escalation_schedules (
    alert_id TEXT PRIMARY KEY REFERENCES alerts(id) ON DELETE CASCADE,
    initial_notification_id TEXT NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    same_audience_repeat_after_seconds INTEGER NOT NULL
        CHECK (same_audience_repeat_after_seconds BETWEEN 15 AND 3600),
    all_users_escalation_after_seconds INTEGER NOT NULL
        CHECK (all_users_escalation_after_seconds BETWEEN 30 AND 7200),
    initial_notified_at TEXT NOT NULL,
    same_audience_repeat_due_at TEXT NOT NULL,
    all_users_escalation_due_at TEXT NOT NULL,
    CHECK (all_users_escalation_after_seconds > same_audience_repeat_after_seconds)
);

CREATE TABLE alert_notification_escalation_dispatches (
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    stage INTEGER NOT NULL CHECK (stage IN (1, 2)),
    status TEXT NOT NULL CHECK (
        status IN (
            'dispatching', 'sent', 'partial', 'failed', 'no_recipients',
            'skipped_responded', 'skipped_superseded'
        )
    ),
    notification_id TEXT REFERENCES notifications(id) ON DELETE SET NULL,
    recipient_count INTEGER NOT NULL DEFAULT 0 CHECK (recipient_count >= 0),
    sent_count INTEGER NOT NULL DEFAULT 0 CHECK (sent_count >= 0),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    error_code TEXT,
    claimed_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (alert_id, stage)
);

CREATE TABLE mobile_alert_workflows (
    alert_id TEXT PRIMARY KEY REFERENCES alerts(id) ON DELETE CASCADE,
    workflow_status TEXT NOT NULL CHECK (workflow_status IN (
        'awaiting_confirmation', 'confirmed_awaiting_handler', 'in_progress',
        'resolved', 'dismissed', 'legacy_in_review', 'legacy_escalated'
    )),
    handler_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    active_phase_id TEXT REFERENCES mobile_alert_reminder_phases(id) ON DELETE SET NULL,
    broadcast_role_ids_json TEXT NOT NULL DEFAULT '[]',
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE mobile_alert_workflow_events (
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

CREATE TABLE mobile_alert_reminder_phases (
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

CREATE TABLE mobile_alert_reminder_dispatches (
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

CREATE TABLE mobile_notification_outbox (
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

CREATE TABLE mobile_notification_outbox_recipients (
    outbox_id TEXT NOT NULL REFERENCES mobile_notification_outbox(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (outbox_id, user_id)
);

CREATE TABLE mobile_notification_outbox_deliveries (
    outbox_id TEXT NOT NULL REFERENCES mobile_notification_outbox(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('sent','failed')),
    provider_message_id TEXT,
    error_code TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 1 CHECK (attempt_count >= 1),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (outbox_id, device_id)
);

CREATE TABLE borderline_role_settings (
    role_id TEXT PRIMARY KEY REFERENCES roles(id) ON DELETE CASCADE,
    default_enabled INTEGER NOT NULL DEFAULT 0 CHECK (default_enabled IN (0,1)),
    updated_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE borderline_user_settings (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
    setting_source TEXT NOT NULL CHECK (setting_source IN ('migration','role_default','role_bulk','individual')),
    updated_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE borderline_settings_revision (
    id TEXT PRIMARY KEY CHECK (id='global'),
    revision INTEGER NOT NULL CHECK (revision >= 1)
);

CREATE TABLE borderline_review_tasks (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    camera_id TEXT NOT NULL REFERENCES cameras(id) ON DELETE RESTRICT,
    detection_event_key TEXT NOT NULL UNIQUE REFERENCES detection_events(id) ON DELETE CASCADE,
    detector_confidence REAL NOT NULL CHECK (detector_confidence BETWEEN 0 AND 1),
    source_start_seconds REAL NOT NULL CHECK (source_start_seconds >= 0),
    source_end_seconds REAL NOT NULL CHECK (source_end_seconds >= source_start_seconds),
    context_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','running','positive','negative','failed','cancelled')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 3),
    raise_alert INTEGER CHECK (raise_alert IN (0,1)),
    uncertainty TEXT CHECK (uncertainty IN ('low','medium','high')),
    brief_reason TEXT,
    error_code TEXT,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    config_version INTEGER NOT NULL,
    vlm_settings_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
, supplemental_notification_status TEXT NOT NULL DEFAULT 'not_applicable'
CHECK (supplemental_notification_status IN (
    'not_applicable','eligible','handled','suppressed_disabled'
)));

CREATE TABLE alert_notification_audience (
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    qualification_reasons_json TEXT NOT NULL DEFAULT '[]',
    first_notification_id TEXT REFERENCES notifications(id) ON DELETE SET NULL,
    first_queued_at TEXT NOT NULL,
    first_sent_at TEXT,
    PRIMARY KEY(alert_id,user_id)
);

CREATE TABLE mobile_alert_reminder_audience (
    alert_id TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    phase_id TEXT NOT NULL REFERENCES mobile_alert_reminder_phases(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stage INTEGER NOT NULL CHECK (stage IN (1,2)),
    status TEXT NOT NULL CHECK (status IN ('queued','sent','failed','skipped')),
    outbox_id TEXT REFERENCES mobile_notification_outbox(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(alert_id,phase_id,user_id,stage)
);

CREATE UNIQUE INDEX ux_camera_active_source
ON camera_sources(camera_id) WHERE is_active = 1;

CREATE INDEX ix_cameras_location ON cameras(location_id);

CREATE INDEX ix_alerts_camera_status ON alerts(camera_id, status);

CREATE INDEX ix_detection_alert_time ON detection_events(alert_id, detected_at);

CREATE INDEX ix_borderline_camera_time ON borderline_reviews(camera_id, reviewed_at);

CREATE INDEX ix_evidence_alert ON evidence_records(alert_id);

CREATE INDEX ix_vlm_description_alert_time ON vlm_descriptions(alert_id, created_at);

CREATE INDEX ix_device_user_enabled ON devices(user_id, enabled);

CREATE INDEX ix_delivery_status ON notification_deliveries(status);

CREATE INDEX ix_notification_recipient_role ON notification_recipients(role_id, notification_id);

CREATE INDEX ix_notification_recipient_user ON notification_recipients(user_id, notification_id);

CREATE INDEX ix_mobile_user_role ON user_roles(role_id, user_id);

CREATE INDEX ix_alert_actions_alert_time ON alert_actions(alert_id, created_at);

CREATE INDEX ix_alert_reads_alert ON alert_reads(alert_id);

CREATE INDEX ix_alert_notification_repeat_due
    ON alert_notification_escalation_schedules(same_audience_repeat_due_at);

CREATE INDEX ix_alert_notification_all_users_due
    ON alert_notification_escalation_schedules(all_users_escalation_due_at);

CREATE INDEX ix_mobile_alert_workflow_events_alert_time
    ON mobile_alert_workflow_events(alert_id, created_at DESC);

CREATE UNIQUE INDEX ux_mobile_alert_one_active_reminder_phase
    ON mobile_alert_reminder_phases(alert_id) WHERE phase_status='active';

CREATE INDEX ix_mobile_alert_reminder_phase_due
    ON mobile_alert_reminder_phases(phase_status, first_due_at, all_due_at);

CREATE INDEX ix_mobile_notification_outbox_due
    ON mobile_notification_outbox(status, next_attempt_at);

CREATE INDEX ix_borderline_review_tasks_status_created
    ON borderline_review_tasks(status,created_at);

CREATE INDEX ix_borderline_review_tasks_alert
    ON borderline_review_tasks(alert_id,created_at DESC);

CREATE INDEX ix_alert_notification_audience_user
    ON alert_notification_audience(user_id,alert_id);
