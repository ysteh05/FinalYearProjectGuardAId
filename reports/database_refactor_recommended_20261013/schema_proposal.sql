-- PLANNING ARTIFACT: create in a NEW EMPTY database only. NOT a migration.

-- Recommended targeted refactor: 30 business/operational tables + schema_migrations.

-- SQLite >=3.37 with JSON built-in. Schema ledger is populated only by real migration execution.

PRAGMA foreign_keys=ON;

BEGIN;

-- 账号身份、状态、显示资料及所属个人Borderline偏好

CREATE TABLE users (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    firebase_uid TEXT NOT NULL,
    email_cache TEXT,
    display_name TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','active','disabled')),
    borderline_enabled INTEGER CHECK(borderline_enabled IN (0,1)),
    borderline_source TEXT CHECK(borderline_source IN ('migration','role_default','role_bulk','individual')),
    borderline_updated_by_user_id TEXT,
    borderline_updated_at TEXT CHECK(borderline_updated_at IS NULL OR (length(borderline_updated_at)=27 AND borderline_updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(borderline_updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(borderline_updated_at,1,10),'+0 days')=substr(borderline_updated_at,1,10),0) AND CAST(substr(borderline_updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(borderline_updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(borderline_updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    updated_at TEXT NOT NULL CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    UNIQUE(firebase_uid),
    CHECK(updated_at >= created_at),
    CHECK((borderline_enabled IS NULL AND borderline_source IS NULL AND borderline_updated_at IS NULL) OR (borderline_enabled IS NOT NULL AND borderline_source IS NOT NULL AND borderline_updated_at IS NOT NULL)),
    FOREIGN KEY (borderline_updated_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
    CHECK(length(firebase_uid) BETWEEN 1 AND 128)
) STRICT;

-- 角色授权能力、通知敏感度及角色Borderline默认值

CREATE TABLE roles (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    display_name TEXT CHECK(length(display_name) <= 60),
    description TEXT CHECK(length(description) <= 200),
    sensitivity_mode TEXT NOT NULL DEFAULT 'moderate' CHECK(sensitivity_mode IN ('sensitive','moderate','certain')),
    is_system INTEGER NOT NULL DEFAULT 0 CHECK(is_system IN (0,1)),
    can_manage_roles INTEGER NOT NULL DEFAULT 0 CHECK(can_manage_roles IN (0,1)),
    can_manage_users INTEGER NOT NULL DEFAULT 0 CHECK(can_manage_users IN (0,1)),
    can_manage_borderline_settings INTEGER NOT NULL DEFAULT 0 CHECK(can_manage_borderline_settings IN (0,1)),
    borderline_default_enabled INTEGER NOT NULL DEFAULT 0 CHECK(borderline_default_enabled IN (0,1)),
    borderline_updated_by_user_id TEXT,
    borderline_updated_at TEXT CHECK(borderline_updated_at IS NULL OR (length(borderline_updated_at)=27 AND borderline_updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(borderline_updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(borderline_updated_at,1,10),'+0 days')=substr(borderline_updated_at,1,10),0) AND CAST(substr(borderline_updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(borderline_updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(borderline_updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    compat_borderline_selected INTEGER NOT NULL DEFAULT 0 CHECK(compat_borderline_selected IN (0,1)),
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    updated_at TEXT NOT NULL CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    UNIQUE(name),
    CHECK(updated_at >= created_at),
    FOREIGN KEY (borderline_updated_by_user_id) REFERENCES users(id) ON DELETE RESTRICT
) STRICT;

-- 明确的用户↔角色分配关系

CREATE TABLE user_roles (
    user_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    assigned_by_user_id TEXT,
    assigned_at TEXT NOT NULL CHECK(assigned_at IS NULL OR (length(assigned_at)=27 AND assigned_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(assigned_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(assigned_at,1,10),'+0 days')=substr(assigned_at,1,10),0) AND CAST(substr(assigned_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(assigned_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(assigned_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    PRIMARY KEY(user_id,role_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE RESTRICT,
    FOREIGN KEY (assigned_by_user_id) REFERENCES users(id) ON DELETE RESTRICT
) STRICT;

-- 当前设备归属、FCM注册值、启用及在线状态

CREATE TABLE devices (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    user_id TEXT NOT NULL,
    installation_id TEXT CHECK(length(installation_id) <= 512),
    fcm_registration_value TEXT NOT NULL CHECK(length(fcm_registration_value) BETWEEN 20 AND 4096),
    platform TEXT NOT NULL CHECK(platform IN ('android','ios','web')),
    device_label TEXT CHECK(length(device_label) <= 100),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    last_seen_at TEXT NOT NULL CHECK(last_seen_at IS NULL OR (length(last_seen_at)=27 AND last_seen_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(last_seen_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(last_seen_at,1,10),'+0 days')=substr(last_seen_at,1,10),0) AND CAST(substr(last_seen_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(last_seen_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(last_seen_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    updated_at TEXT NOT NULL CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    UNIQUE(fcm_registration_value),
    CHECK(updated_at >= created_at),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) STRICT;

-- 摄像头共享的地点主数据

CREATE TABLE locations (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    name TEXT NOT NULL CHECK(length(trim(name)) > 0 AND length(name) <= 120),
    normalized_name TEXT NOT NULL CHECK(length(trim(normalized_name)) > 0),
    building TEXT,
    floor TEXT,
    description TEXT,
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    UNIQUE(normalized_name)
) STRICT;

-- 稳定摄像头身份、地点及分析状态

CREATE TABLE cameras (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    location_id TEXT NOT NULL,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0 AND length(name) <= 80),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    status TEXT NOT NULL CHECK(status IN ('ready','analysing','paused','error')),
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    updated_at TEXT NOT NULL CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE RESTRICT,
    CHECK(updated_at >= created_at)
) STRICT;

-- 实际媒体文件的唯一元数据

CREATE TABLE media_files (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    relative_path TEXT NOT NULL CHECK(length(trim(relative_path)) > 0),
    original_filename TEXT,
    media_kind TEXT NOT NULL CHECK(media_kind IN ('camera_source','evidence_clip','evidence_image')),
    content_type TEXT NOT NULL CHECK(length(trim(content_type)) > 0),
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    sha256 TEXT CHECK(sha256 IS NULL OR (length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*')),
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    UNIQUE(relative_path),
    CHECK(substr(relative_path,1,1)<>'/' AND instr(relative_path,':')=0 AND instr(relative_path,char(92))=0 AND instr(relative_path,char(0))=0 AND instr('/'||relative_path||'/','/../')=0 AND instr('/'||relative_path||'/','/./')=0 AND instr(relative_path,'//')=0)
) STRICT;

-- 摄像头输入来源及替换历史

CREATE TABLE camera_sources (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    camera_id TEXT NOT NULL,
    media_file_id TEXT,
    source_type TEXT NOT NULL CHECK(source_type IN ('uploaded_video','rtsp')),
    rtsp_endpoint_ref TEXT,
    duration_seconds REAL NOT NULL DEFAULT 0 CHECK(duration_seconds >= 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE CASCADE,
    FOREIGN KEY (media_file_id) REFERENCES media_files(id) ON DELETE RESTRICT,
    CHECK((source_type='uploaded_video' AND media_file_id IS NOT NULL AND rtsp_endpoint_ref IS NULL) OR (source_type='rtsp' AND media_file_id IS NULL AND rtsp_endpoint_ref IS NOT NULL))
) STRICT;

-- 按任务保存不可变模型版本身份与部署来源

CREATE TABLE model_versions (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    task TEXT NOT NULL CHECK(task IN ('violence','physical_contact_bullying','weapon','borderline_binary','scene_description')),
    model_name TEXT NOT NULL CHECK(length(trim(model_name)) > 0),
    version TEXT NOT NULL CHECK(length(trim(version)) > 0),
    framework TEXT,
    quantization TEXT,
    checkpoint_ref TEXT,
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    UNIQUE(task,model_name,version)
) STRICT;

-- 按用途保存提示词版本、内容及摘要

CREATE TABLE prompt_versions (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    task TEXT NOT NULL CHECK(task IN ('borderline_binary','scene_description')),
    version TEXT NOT NULL CHECK(length(trim(version)) > 0),
    capture_state TEXT NOT NULL DEFAULT 'metadata_only' CHECK(capture_state IN ('metadata_only','frozen')),
    content TEXT,
    content_sha256 TEXT CHECK(content_sha256 IS NULL OR (length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*')),
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    UNIQUE(version),
    CHECK(capture_state='metadata_only' OR (content IS NOT NULL AND content_sha256 IS NOT NULL))
) STRICT;

-- 同一版本的完整运行策略快照

CREATE TABLE config_versions (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    version_number INTEGER NOT NULL CHECK(version_number >= 1),
    created_by TEXT NOT NULL CHECK(length(trim(created_by)) > 0),
    created_by_user_id TEXT,
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    validation_profile TEXT NOT NULL DEFAULT 'runtime' CHECK(validation_profile IN ('runtime','historical')),
    detection_debug_mode_enabled INTEGER NOT NULL DEFAULT 0 CHECK(detection_debug_mode_enabled IN (0,1)),
    default_sensitivity TEXT NOT NULL CHECK(default_sensitivity IN ('sensitive','moderate','certain')),
    violence_segment_seconds REAL NOT NULL CHECK(violence_segment_seconds >= 1 AND violence_segment_seconds <= 60),
    violence_stride_seconds REAL NOT NULL CHECK(violence_stride_seconds >= 0 AND violence_stride_seconds <= 60),
    violence_detector_input_frames INTEGER NOT NULL CHECK(violence_detector_input_frames >= 4 AND violence_detector_input_frames <= 128),
    violence_sensitive_threshold REAL NOT NULL CHECK(violence_sensitive_threshold >= 0 AND violence_sensitive_threshold <= 1),
    violence_moderate_threshold REAL NOT NULL CHECK(violence_moderate_threshold >= 0 AND violence_moderate_threshold <= 1),
    violence_certain_threshold REAL NOT NULL CHECK(violence_certain_threshold >= 0 AND violence_certain_threshold <= 1),
    violence_cooldown_seconds INTEGER NOT NULL CHECK(violence_cooldown_seconds >= 0 AND violence_cooldown_seconds <= 3600),
    violence_merge_gap_seconds REAL NOT NULL CHECK(violence_merge_gap_seconds >= 0 AND violence_merge_gap_seconds <= 120),
    weapon_sample_fps REAL NOT NULL CHECK(weapon_sample_fps >= 0.25 AND weapon_sample_fps <= 30),
    weapon_confidence_threshold REAL NOT NULL CHECK(weapon_confidence_threshold >= 0 AND weapon_confidence_threshold <= 1),
    weapon_nms_candidate_floor REAL NOT NULL CHECK(weapon_nms_candidate_floor >= 0 AND weapon_nms_candidate_floor <= 0.1),
    weapon_nms_iou_threshold REAL NOT NULL CHECK(weapon_nms_iou_threshold >= 0.1 AND weapon_nms_iou_threshold <= 0.95),
    weapon_n_required INTEGER NOT NULL CHECK(weapon_n_required >= 1 AND weapon_n_required <= 120),
    weapon_m_window INTEGER NOT NULL CHECK(weapon_m_window >= 1 AND weapon_m_window <= 120),
    weapon_cooldown_seconds INTEGER NOT NULL CHECK(weapon_cooldown_seconds >= 0 AND weapon_cooldown_seconds <= 3600),
    weapon_merge_gap_seconds REAL NOT NULL CHECK(weapon_merge_gap_seconds >= 0 AND weapon_merge_gap_seconds <= 120),
    weapon_class_thresholds_json TEXT NOT NULL CHECK(weapon_class_thresholds_json IS NULL OR (CASE WHEN json_valid(weapon_class_thresholds_json) THEN json_type(weapon_class_thresholds_json)='object' ELSE 0 END)),
    vlm_model_id TEXT NOT NULL  CHECK(length(trim(vlm_model_id))>0),
    vlm_quantization TEXT NOT NULL  CHECK(length(trim(vlm_quantization))>0),
    vlm_input_fps REAL NOT NULL CHECK(vlm_input_fps >= 0.25 AND vlm_input_fps <= 12),
    vlm_video_token_budget INTEGER NOT NULL CHECK(vlm_video_token_budget >= 256 AND vlm_video_token_budget <= 16384),
    vlm_max_output_tokens INTEGER NOT NULL CHECK(vlm_max_output_tokens >= 32 AND vlm_max_output_tokens <= 2048),
    vlm_scene_description_frames INTEGER NOT NULL CHECK(vlm_scene_description_frames >= 4 AND vlm_scene_description_frames <= 128),
    vlm_borderline_review_enabled INTEGER NOT NULL CHECK(vlm_borderline_review_enabled IN (0,1)),
    vlm_scene_description_enabled INTEGER NOT NULL CHECK(vlm_scene_description_enabled IN (0,1)),
    binary_prompt_version_id TEXT NOT NULL,
    scene_prompt_version_id TEXT NOT NULL,
    same_audience_repeat_after_seconds INTEGER NOT NULL CHECK(same_audience_repeat_after_seconds >= 15 AND same_audience_repeat_after_seconds <= 3600),
    all_users_escalation_after_seconds INTEGER NOT NULL CHECK(all_users_escalation_after_seconds >= 30 AND all_users_escalation_after_seconds <= 7200),
    UNIQUE(version_number),
    FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY (binary_prompt_version_id) REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY (scene_prompt_version_id) REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    CHECK(violence_stride_seconds>0 AND violence_stride_seconds<=violence_segment_seconds),
    CHECK(violence_sensitive_threshold<=violence_moderate_threshold AND violence_moderate_threshold<=violence_certain_threshold),
    CHECK(weapon_n_required<=weapon_m_window),
    CHECK(vlm_scene_description_frames%2=0),
    CHECK(all_users_escalation_after_seconds>same_audience_repeat_after_seconds)
) STRICT;

-- 稳定事件及其当前唯一手机处理状态

CREATE TABLE alerts (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    camera_id TEXT NOT NULL,
    incident_type TEXT NOT NULL CHECK(incident_type IN ('violence','physical_contact_bullying','weapon')),
    workflow_status TEXT NOT NULL CHECK(workflow_status IN ('awaiting_confirmation','confirmed_awaiting_handler','in_progress','resolved','dismissed','legacy_in_review','legacy_escalated')),
    sensitivity_mode TEXT NOT NULL CHECK(sensitivity_mode IN ('sensitive','moderate','certain')),
    is_borderline_promoted INTEGER NOT NULL DEFAULT 0 CHECK(is_borderline_promoted IN (0,1)),
    is_provisional INTEGER NOT NULL DEFAULT 0 CHECK(is_provisional IN (0,1)),
    borderline_review_status TEXT NOT NULL CHECK(borderline_review_status IN ('not_applicable','disabled','queued','running','positive','negative','failed','cancelled','legacy_unknown')),
    evidence_status TEXT NOT NULL CHECK(evidence_status IN ('preparing','available','failed','legacy_unknown')),
    opening_config_version_id TEXT NOT NULL,
    opening_model_version_id TEXT NOT NULL,
    binary_prompt_version_id TEXT,
    handler_user_id TEXT,
    active_phase_id TEXT,
    broadcast_role_snapshot_json TEXT NOT NULL DEFAULT '[]' CHECK(broadcast_role_snapshot_json IS NULL OR (CASE WHEN json_valid(broadcast_role_snapshot_json) THEN json_type(broadcast_role_snapshot_json)='array' ELSE 0 END)),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    workflow_updated_at TEXT NOT NULL CHECK(workflow_updated_at IS NULL OR (length(workflow_updated_at)=27 AND workflow_updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(workflow_updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(workflow_updated_at,1,10),'+0 days')=substr(workflow_updated_at,1,10),0) AND CAST(substr(workflow_updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(workflow_updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(workflow_updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    resolved_at TEXT CHECK(resolved_at IS NULL OR (length(resolved_at)=27 AND resolved_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(resolved_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(resolved_at,1,10),'+0 days')=substr(resolved_at,1,10),0) AND CAST(substr(resolved_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(resolved_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(resolved_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    UNIQUE(id,camera_id),
    FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE RESTRICT,
    FOREIGN KEY (opening_config_version_id) REFERENCES config_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY (opening_model_version_id) REFERENCES model_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY (binary_prompt_version_id) REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY (handler_user_id) REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY (active_phase_id,id) REFERENCES notification_phases(id,alert_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    CHECK(workflow_updated_at>=created_at),
    CHECK(resolved_at IS NULL OR resolved_at>=created_at),
    CHECK(workflow_status<>'in_progress' OR handler_user_id IS NOT NULL)
) STRICT;

-- 一次被警报接纳的检测事实

CREATE TABLE detection_events (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    alert_id TEXT NOT NULL,
    model_version_id TEXT NOT NULL,
    config_version_id TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    decision_source TEXT NOT NULL CHECK(decision_source IN ('threshold','vlm_promoted','weapon_n_of_m')),
    source_start_seconds REAL CHECK(source_start_seconds >= 0),
    source_end_seconds REAL CHECK(source_end_seconds >= 0),
    detected_at TEXT NOT NULL CHECK(detected_at IS NULL OR (length(detected_at)=27 AND detected_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(detected_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(detected_at,1,10),'+0 days')=substr(detected_at,1,10),0) AND CAST(substr(detected_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(detected_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(detected_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    qualifications_json TEXT NOT NULL DEFAULT '{}' CHECK(qualifications_json IS NULL OR (CASE WHEN json_valid(qualifications_json) THEN json_type(qualifications_json)='object' ELSE 0 END)),
    weapon_class_scores_json TEXT NOT NULL DEFAULT '{}' CHECK(weapon_class_scores_json IS NULL OR (CASE WHEN json_valid(weapon_class_scores_json) THEN json_type(weapon_class_scores_json)='object' ELSE 0 END)),
    FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
    FOREIGN KEY (model_version_id) REFERENCES model_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY (config_version_id) REFERENCES config_versions(id) ON DELETE RESTRICT,
    CHECK(source_start_seconds IS NULL OR source_end_seconds IS NULL OR source_end_seconds>=source_start_seconds)
) STRICT;

-- 一次Borderline复核的输入、排队执行、结论及补充通知资格

CREATE TABLE borderline_reviews (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    record_origin TEXT NOT NULL CHECK(record_origin IN ('task','audit_only')),
    legacy_review_id TEXT,
    camera_id TEXT NOT NULL,
    alert_id TEXT,
    detection_event_id TEXT,
    model_version_id TEXT NOT NULL,
    prompt_version_id TEXT NOT NULL,
    config_version_id TEXT NOT NULL,
    detector_confidence REAL NOT NULL CHECK(detector_confidence >= 0 AND detector_confidence <= 1),
    source_start_seconds REAL CHECK(source_start_seconds >= 0),
    source_end_seconds REAL CHECK(source_end_seconds >= 0),
    context_path TEXT,
    status TEXT NOT NULL CHECK(status IN ('queued','running','positive','negative','failed','cancelled')),
    attempt_count INTEGER CHECK(attempt_count >= 0 AND attempt_count <= 3),
    raise_alert INTEGER CHECK(raise_alert IN (0,1)),
    uncertainty TEXT CHECK(uncertainty IN ('low','medium','high')),
    brief_reason TEXT,
    error_code TEXT,
    vlm_settings_json TEXT CHECK(vlm_settings_json IS NULL OR (CASE WHEN json_valid(vlm_settings_json) THEN json_type(vlm_settings_json)='object' ELSE 0 END)),
    supplemental_notification_status TEXT NOT NULL DEFAULT 'not_applicable' CHECK(supplemental_notification_status IN ('not_applicable','eligible','handled','suppressed_disabled')),
    created_at TEXT CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    started_at TEXT CHECK(started_at IS NULL OR (length(started_at)=27 AND started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(started_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(started_at,1,10),'+0 days')=substr(started_at,1,10),0) AND CAST(substr(started_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(started_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(started_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    completed_at TEXT CHECK(completed_at IS NULL OR (length(completed_at)=27 AND completed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(completed_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(completed_at,1,10),'+0 days')=substr(completed_at,1,10),0) AND CAST(substr(completed_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(completed_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(completed_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    reviewed_at TEXT CHECK(reviewed_at IS NULL OR (length(reviewed_at)=27 AND reviewed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(reviewed_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(reviewed_at,1,10),'+0 days')=substr(reviewed_at,1,10),0) AND CAST(substr(reviewed_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(reviewed_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(reviewed_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    UNIQUE(detection_event_id),
    UNIQUE(legacy_review_id),
    FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE RESTRICT,
    FOREIGN KEY (alert_id,camera_id) REFERENCES alerts(id,camera_id) ON DELETE RESTRICT,
    FOREIGN KEY (detection_event_id) REFERENCES detection_events(id) ON DELETE RESTRICT,
    FOREIGN KEY (model_version_id) REFERENCES model_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY (prompt_version_id) REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY (config_version_id) REFERENCES config_versions(id) ON DELETE RESTRICT,
    CHECK(source_start_seconds IS NULL OR source_end_seconds IS NULL OR source_end_seconds>=source_start_seconds),
    CHECK((record_origin='task' AND alert_id IS NOT NULL AND detection_event_id IS NOT NULL AND context_path IS NOT NULL AND length(trim(context_path))>0 AND vlm_settings_json IS NOT NULL AND attempt_count IS NOT NULL AND source_start_seconds IS NOT NULL AND source_end_seconds IS NOT NULL AND created_at IS NOT NULL) OR (record_origin='audit_only' AND detection_event_id IS NULL AND context_path IS NULL AND vlm_settings_json IS NULL AND attempt_count IS NULL AND created_at IS NULL AND started_at IS NULL AND completed_at IS NULL AND reviewed_at IS NOT NULL AND status IN ('positive','negative') AND supplemental_notification_status='not_applicable')),
    CHECK(status NOT IN ('positive','negative') OR raise_alert IS NOT NULL),
    CHECK(status<>'positive' OR raise_alert=1),
    CHECK(status<>'negative' OR raise_alert=0),
    CHECK(supplemental_notification_status='not_applicable' OR (record_origin='task' AND status='positive')),
    CHECK(started_at IS NULL OR (created_at IS NOT NULL AND started_at>=created_at)),
    CHECK(completed_at IS NULL OR (created_at IS NOT NULL AND completed_at>=created_at AND (started_at IS NULL OR completed_at>=started_at)))
) STRICT;

-- 警报证据关系、来源区间及保留期限

CREATE TABLE evidence_records (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    alert_id TEXT NOT NULL,
    media_file_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL CHECK(evidence_type IN ('violence_clip','bullying_clip','weapon_screenshot')),
    source_start_seconds REAL CHECK(source_start_seconds >= 0),
    source_end_seconds REAL CHECK(source_end_seconds >= 0),
    captured_at TEXT NOT NULL CHECK(captured_at IS NULL OR (length(captured_at)=27 AND captured_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(captured_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(captured_at,1,10),'+0 days')=substr(captured_at,1,10),0) AND CAST(substr(captured_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(captured_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(captured_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    delete_after TEXT CHECK(delete_after IS NULL OR (length(delete_after)=27 AND delete_after GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(delete_after,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(delete_after,1,10),'+0 days')=substr(delete_after,1,10),0) AND CAST(substr(delete_after,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(delete_after,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(delete_after,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    UNIQUE(media_file_id),
    FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
    FOREIGN KEY (media_file_id) REFERENCES media_files(id) ON DELETE RESTRICT,
    CHECK(source_start_seconds IS NULL OR source_end_seconds IS NULL OR source_end_seconds>=source_start_seconds),
    CHECK(delete_after IS NULL OR delete_after>=captured_at)
) STRICT;

-- 场景描述生成状态、输出及重新生成历史

CREATE TABLE vlm_descriptions (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    alert_id TEXT NOT NULL,
    model_version_id TEXT NOT NULL,
    prompt_version_id TEXT,
    config_version_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending','ready','failed','disabled')),
    output_format TEXT NOT NULL DEFAULT 'none' CHECK(output_format IN ('none','summary','raw_json','text')),
    structured_json TEXT CHECK(structured_json IS NULL OR (CASE WHEN json_valid(structured_json) THEN 1 ELSE 0 END)),
    display_text TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    completed_at TEXT CHECK(completed_at IS NULL OR (length(completed_at)=27 AND completed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(completed_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(completed_at,1,10),'+0 days')=substr(completed_at,1,10),0) AND CAST(substr(completed_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(completed_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(completed_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
    FOREIGN KEY (model_version_id) REFERENCES model_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY (prompt_version_id) REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY (config_version_id) REFERENCES config_versions(id) ON DELETE RESTRICT,
    CHECK(completed_at IS NULL OR completed_at>=created_at),
    CHECK((output_format='none' AND structured_json IS NULL AND display_text IS NULL) OR (output_format IN ('summary','raw_json') AND structured_json IS NOT NULL) OR (output_format='text' AND structured_json IS NULL AND display_text IS NOT NULL)),
    CHECK(status<>'ready' OR output_format<>'none')
) STRICT;

-- 用户对事件的人工处理历史及幂等请求

CREATE TABLE alert_actions (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    alert_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('acknowledge','review','dismiss','resolve','escalate','dismiss_false_alarm','confirm_only','confirm_and_handle','confirm_resolved','take_ownership','simulated_emergency_call')),
    record_origin TEXT NOT NULL CHECK(record_origin IN ('legacy','workflow')),
    note TEXT,
    broadcast_role_snapshot_json TEXT CHECK(broadcast_role_snapshot_json IS NULL OR (CASE WHEN json_valid(broadcast_role_snapshot_json) THEN json_type(broadcast_role_snapshot_json)='array' ELSE 0 END)),
    idempotency_key TEXT,
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE RESTRICT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
    CHECK(record_origin='legacy' OR (idempotency_key IS NOT NULL AND length(idempotency_key) BETWEEN 8 AND 120 AND broadcast_role_snapshot_json IS NOT NULL AND (note IS NULL OR length(note)<=1000)))
) STRICT;

-- 每用户每警报的首次浏览状态

CREATE TABLE alert_reads (
    user_id TEXT NOT NULL,
    alert_id TEXT NOT NULL,
    read_at TEXT NOT NULL CHECK(read_at IS NULL OR (length(read_at)=27 AND read_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(read_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(read_at,1,10),'+0 days')=substr(read_at,1,10),0) AND CAST(substr(read_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(read_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(read_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    PRIMARY KEY(user_id,alert_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE
) STRICT;

-- 面向用户的逻辑通知意图、内容和稳定幂等身份

CREATE TABLE notifications (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    alert_id TEXT NOT NULL,
    message_kind TEXT NOT NULL CHECK(message_kind IN ('initial','broadcast','decision_update','phase_reminder','late_repeat','manual','legacy')),
    event_key TEXT,
    visibility_policy TEXT NOT NULL DEFAULT 'restricted' CHECK(visibility_policy IN ('restricted','broadcast')),
    phase_id TEXT,
    reminder_stage INTEGER CHECK(reminder_stage >= 1 AND reminder_stage <= 2),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    data_payload_json TEXT NOT NULL CHECK(data_payload_json IS NULL OR (CASE WHEN json_valid(data_payload_json) THEN json_type(data_payload_json)='object' ELSE 0 END)),
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    UNIQUE(event_key),
    UNIQUE(id,alert_id),
    FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
    FOREIGN KEY (phase_id,alert_id) REFERENCES notification_phases(id,alert_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    CHECK((message_kind IN ('phase_reminder','late_repeat') AND phase_id IS NOT NULL AND reminder_stage IS NOT NULL) OR (message_kind NOT IN ('phase_reminder','late_repeat') AND phase_id IS NULL AND reminder_stage IS NULL))
) STRICT;

-- 某条通知的持久发送任务、领取与重试状态

CREATE TABLE notification_outbox (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    notification_id TEXT NOT NULL,
    channel_id TEXT NOT NULL CHECK(length(trim(channel_id)) > 0),
    status TEXT NOT NULL CHECK(status IN ('pending','dispatching','sent','partial','failed','no_recipients')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    next_attempt_at TEXT NOT NULL CHECK(next_attempt_at IS NULL OR (length(next_attempt_at)=27 AND next_attempt_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(next_attempt_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(next_attempt_at,1,10),'+0 days')=substr(next_attempt_at,1,10),0) AND CAST(substr(next_attempt_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(next_attempt_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(next_attempt_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    claimed_at TEXT CHECK(claimed_at IS NULL OR (length(claimed_at)=27 AND claimed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(claimed_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(claimed_at,1,10),'+0 days')=substr(claimed_at,1,10),0) AND CAST(substr(claimed_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(claimed_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(claimed_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    last_error_code TEXT,
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    updated_at TEXT NOT NULL CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    UNIQUE(notification_id),
    FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE,
    CHECK(updated_at>=created_at)
) STRICT;

-- 通知展示名单与实际排队用户快照

CREATE TABLE notification_recipients (
    id INTEGER PRIMARY KEY,
    notification_id TEXT NOT NULL,
    user_id TEXT,
    role_id TEXT,
    feed_visible INTEGER NOT NULL DEFAULT 1 CHECK(feed_visible IN (0,1)),
    queued_target INTEGER NOT NULL DEFAULT 0 CHECK(queued_target IN (0,1)),
    FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE,
    CHECK((user_id IS NOT NULL AND role_id IS NULL) OR (user_id IS NULL AND role_id IS NOT NULL)),
    CHECK(feed_visible=1 OR queued_target=1),
    CHECK(role_id IS NULL OR queued_target=0)
) STRICT;

-- 每通知每设备的最新实际发送结果

CREATE TABLE notification_deliveries (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    notification_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    recipient_user_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('queued','sent','failed','invalid_device')),
    provider_message_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    last_attempt_at TEXT CHECK(last_attempt_at IS NULL OR (length(last_attempt_at)=27 AND last_attempt_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(last_attempt_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(last_attempt_at,1,10),'+0 days')=substr(last_attempt_at,1,10),0) AND CAST(substr(last_attempt_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(last_attempt_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(last_attempt_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    error_code TEXT,
    updated_at TEXT CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    UNIQUE(notification_id,device_id),
    FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE RESTRICT,
    FOREIGN KEY (recipient_user_id) REFERENCES users(id) ON DELETE RESTRICT,
    CHECK(status NOT IN ('sent','failed','invalid_device') OR last_attempt_at IS NOT NULL)
) STRICT;

-- 每用户每消息的首次已读关系

CREATE TABLE notification_reads (
    notification_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    read_at TEXT NOT NULL CHECK(read_at IS NULL OR (length(read_at)=27 AND read_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(read_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(read_at,1,10),'+0 days')=substr(read_at,1,10),0) AND CAST(substr(read_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(read_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(read_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    PRIMARY KEY(notification_id,user_id),
    FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) STRICT;

-- 每警报每用户的初次通知资格及已送达事实

CREATE TABLE alert_notification_audience (
    alert_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    qualification_reasons_json TEXT NOT NULL DEFAULT '[]' CHECK(qualification_reasons_json IS NULL OR (CASE WHEN json_valid(qualification_reasons_json) THEN json_type(qualification_reasons_json)='array' ELSE 0 END)),
    first_notification_id TEXT,
    first_queued_at TEXT NOT NULL CHECK(first_queued_at IS NULL OR (length(first_queued_at)=27 AND first_queued_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(first_queued_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(first_queued_at,1,10),'+0 days')=substr(first_queued_at,1,10),0) AND CAST(substr(first_queued_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(first_queued_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(first_queued_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    first_sent_at TEXT CHECK(first_sent_at IS NULL OR (length(first_sent_at)=27 AND first_sent_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(first_sent_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(first_sent_at,1,10),'+0 days')=substr(first_sent_at,1,10),0) AND CAST(substr(first_sent_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(first_sent_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(first_sent_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    PRIMARY KEY(alert_id,user_id),
    FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (first_notification_id) REFERENCES notifications(id) ON DELETE SET NULL,
    CHECK(first_sent_at IS NULL OR first_sent_at>=first_queued_at)
) STRICT;

-- 事件Phase A/B的计时快照及旧计划归档

CREATE TABLE notification_phases (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    alert_id TEXT NOT NULL,
    phase_kind TEXT NOT NULL CHECK(phase_kind IN ('awaiting_decision','awaiting_handler')),
    phase_status TEXT NOT NULL CHECK(phase_status IN ('active','cancelled','completed','archived')),
    started_at TEXT NOT NULL CHECK(started_at IS NULL OR (length(started_at)=27 AND started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(started_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(started_at,1,10),'+0 days')=substr(started_at,1,10),0) AND CAST(substr(started_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(started_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(started_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    first_after_seconds INTEGER NOT NULL CHECK(first_after_seconds >= 15 AND first_after_seconds <= 3600),
    all_after_seconds INTEGER NOT NULL CHECK(all_after_seconds >= 30 AND all_after_seconds <= 7200),
    first_due_at TEXT NOT NULL CHECK(first_due_at IS NULL OR (length(first_due_at)=27 AND first_due_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(first_due_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(first_due_at,1,10),'+0 days')=substr(first_due_at,1,10),0) AND CAST(substr(first_due_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(first_due_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(first_due_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    all_due_at TEXT NOT NULL CHECK(all_due_at IS NULL OR (length(all_due_at)=27 AND all_due_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(all_due_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(all_due_at,1,10),'+0 days')=substr(all_due_at,1,10),0) AND CAST(substr(all_due_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(all_due_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(all_due_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    broadcast_role_snapshot_json TEXT NOT NULL DEFAULT '[]' CHECK(broadcast_role_snapshot_json IS NULL OR (CASE WHEN json_valid(broadcast_role_snapshot_json) THEN json_type(broadcast_role_snapshot_json)='array' ELSE 0 END)),
    initiated_by_user_id TEXT,
    source_notification_id TEXT,
    cancel_reason TEXT,
    completed_at TEXT CHECK(completed_at IS NULL OR (length(completed_at)=27 AND completed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(completed_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(completed_at,1,10),'+0 days')=substr(completed_at,1,10),0) AND CAST(substr(completed_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(completed_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(completed_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    is_legacy INTEGER NOT NULL DEFAULT 0 CHECK(is_legacy IN (0,1)),
    UNIQUE(id,alert_id),
    FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
    FOREIGN KEY (initiated_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY (source_notification_id) REFERENCES notifications(id) ON DELETE RESTRICT,
    CHECK(all_after_seconds>first_after_seconds),
    CHECK(first_due_at<all_due_at AND first_due_at>started_at),
    CHECK(completed_at IS NULL OR completed_at>=started_at),
    CHECK(phase_status<>'active' OR completed_at IS NULL),
    CHECK(phase_status<>'archived' OR is_legacy=1),
    CHECK(COALESCE(CAST(strftime('%s',substr(first_due_at,1,19)||'Z') AS INTEGER)=CAST(strftime('%s',substr(started_at,1,19)||'Z') AS INTEGER)+first_after_seconds,0) AND substr(first_due_at,21,6)=substr(started_at,21,6)),
    CHECK(COALESCE(CAST(strftime('%s',substr(all_due_at,1,19)||'Z') AS INTEGER)=CAST(strftime('%s',substr(started_at,1,19)||'Z') AS INTEGER)+all_after_seconds,0) AND substr(all_due_at,21,6)=substr(started_at,21,6))
) STRICT;

-- Phase B当时选择的live Broadcast Roles

CREATE TABLE notification_phase_roles (
    phase_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    PRIMARY KEY(phase_id,role_id),
    FOREIGN KEY (phase_id) REFERENCES notification_phases(id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
) STRICT;

-- 一个phase-stage的领取、防重与完成结果

CREATE TABLE notification_phase_dispatches (
    phase_id TEXT NOT NULL,
    stage INTEGER NOT NULL CHECK(stage >= 1 AND stage <= 2),
    status TEXT NOT NULL CHECK(status IN ('dispatching','queued','sent','partial','failed','no_recipients','skipped_stopped','skipped_superseded','skipped_responded')),
    notification_id TEXT,
    recipient_count_unit TEXT CHECK(recipient_count_unit IN ('user','device')),
    recipient_count INTEGER NOT NULL DEFAULT 0 CHECK(recipient_count >= 0),
    sent_count INTEGER NOT NULL DEFAULT 0 CHECK(sent_count >= 0),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_count >= 0),
    error_code TEXT,
    claimed_at TEXT NOT NULL CHECK(claimed_at IS NULL OR (length(claimed_at)=27 AND claimed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(claimed_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(claimed_at,1,10),'+0 days')=substr(claimed_at,1,10),0) AND CAST(substr(claimed_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(claimed_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(claimed_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    completed_at TEXT CHECK(completed_at IS NULL OR (length(completed_at)=27 AND completed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(completed_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(completed_at,1,10),'+0 days')=substr(completed_at,1,10),0) AND CAST(substr(completed_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(completed_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(completed_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    PRIMARY KEY(phase_id,stage),
    UNIQUE(notification_id),
    FOREIGN KEY (phase_id) REFERENCES notification_phases(id) ON DELETE CASCADE,
    FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE SET NULL,
    CHECK(completed_at IS NULL OR completed_at>=claimed_at)
) STRICT;

-- 每阶段、每用户、每stage的发送防重与结果

CREATE TABLE notification_phase_audience (
    phase_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    stage INTEGER NOT NULL CHECK(stage >= 1 AND stage <= 2),
    status TEXT NOT NULL CHECK(status IN ('queued','sent','failed','skipped')),
    notification_id TEXT,
    updated_at TEXT NOT NULL CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    PRIMARY KEY(phase_id,user_id,stage),
    FOREIGN KEY (phase_id) REFERENCES notification_phases(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE SET NULL
) STRICT;

-- 全局管理偏好、Borderline并发revision与模拟通话不变量

CREATE TABLE mobile_settings (
    id TEXT PRIMARY KEY NOT NULL CHECK(id='global'),
    borderline_alert_enabled INTEGER NOT NULL DEFAULT 1 CHECK(borderline_alert_enabled IN (0,1)),
    borderline_revision INTEGER NOT NULL DEFAULT 1 CHECK(borderline_revision >= 1),
    emergency_call_simulation_only INTEGER NOT NULL DEFAULT 1 CHECK(emergency_call_simulation_only=1),
    updated_by_user_id TEXT,
    updated_at TEXT NOT NULL CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    FOREIGN KEY (updated_by_user_id) REFERENCES users(id) ON DELETE RESTRICT
) STRICT;

-- 行政、安全与配置操作的不可变审计事实

CREATE TABLE audit_logs (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0),
    user_id TEXT,
    action TEXT NOT NULL CHECK(length(trim(action)) > 0),
    entity_type TEXT NOT NULL CHECK(length(trim(entity_type)) > 0),
    entity_id TEXT,
    detail_json TEXT CHECK(detail_json IS NULL OR (CASE WHEN json_valid(detail_json) THEN 1 ELSE 0 END)),
    created_at TEXT NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
) STRICT;

-- 数据库结构升级的持久账表

CREATE TABLE schema_migrations (
    version INTEGER NOT NULL CHECK(version >= 1),
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    applied_at TEXT NOT NULL CHECK(applied_at IS NULL OR (length(applied_at)=27 AND applied_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(applied_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(applied_at,1,10),'+0 days')=substr(applied_at,1,10),0) AND CAST(substr(applied_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(applied_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(applied_at,18,2) AS INTEGER) BETWEEN 0 AND 59)),
    PRIMARY KEY(version)
) STRICT;

CREATE INDEX ix_users_email_lookup ON users(lower(trim(email_cache))) WHERE email_cache IS NOT NULL;

CREATE INDEX ix_user_roles_role ON user_roles(role_id,user_id);

CREATE INDEX ix_devices_user_enabled ON devices(user_id,enabled);

CREATE INDEX ix_cameras_location ON cameras(location_id);

CREATE UNIQUE INDEX ux_active_camera_source ON camera_sources(camera_id) WHERE is_active=1;

CREATE INDEX ix_camera_source_file ON camera_sources(media_file_id) WHERE media_file_id IS NOT NULL;

CREATE INDEX ix_config_creator ON config_versions(created_by_user_id) WHERE created_by_user_id IS NOT NULL;

CREATE INDEX ix_config_binary_prompt ON config_versions(binary_prompt_version_id);

CREATE INDEX ix_config_scene_prompt ON config_versions(scene_prompt_version_id);

CREATE INDEX ix_alert_camera_workflow ON alerts(camera_id,workflow_status);

CREATE INDEX ix_alert_active_phase ON alerts(active_phase_id) WHERE active_phase_id IS NOT NULL;

CREATE INDEX ix_alert_opening_config ON alerts(opening_config_version_id);

CREATE INDEX ix_alert_opening_model ON alerts(opening_model_version_id);

CREATE INDEX ix_alert_handler ON alerts(handler_user_id) WHERE handler_user_id IS NOT NULL;

CREATE INDEX ix_alert_prompt ON alerts(binary_prompt_version_id) WHERE binary_prompt_version_id IS NOT NULL;

CREATE INDEX ix_detection_alert_time ON detection_events(alert_id,detected_at);

CREATE INDEX ix_detection_model ON detection_events(model_version_id);

CREATE INDEX ix_detection_config ON detection_events(config_version_id);

CREATE INDEX ix_review_queue ON borderline_reviews(status,created_at) WHERE record_origin='task' AND status IN ('queued','running');

CREATE INDEX ix_review_alert_time ON borderline_reviews(alert_id,created_at);

CREATE INDEX ix_review_camera ON borderline_reviews(camera_id);

CREATE INDEX ix_review_model ON borderline_reviews(model_version_id);

CREATE INDEX ix_review_prompt ON borderline_reviews(prompt_version_id);

CREATE INDEX ix_review_config ON borderline_reviews(config_version_id);

CREATE INDEX ix_evidence_alert ON evidence_records(alert_id);

CREATE INDEX ix_evidence_retention ON evidence_records(delete_after) WHERE delete_after IS NOT NULL;

CREATE INDEX ix_description_alert_time ON vlm_descriptions(alert_id,created_at DESC);

CREATE INDEX ix_description_model ON vlm_descriptions(model_version_id);

CREATE INDEX ix_description_prompt ON vlm_descriptions(prompt_version_id) WHERE prompt_version_id IS NOT NULL;

CREATE INDEX ix_description_config ON vlm_descriptions(config_version_id) WHERE config_version_id IS NOT NULL;

CREATE UNIQUE INDEX ux_alert_action_idempotency ON alert_actions(alert_id,idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE INDEX ix_alert_actions_alert_time ON alert_actions(alert_id,created_at DESC);

CREATE INDEX ix_alert_actions_user ON alert_actions(user_id);

CREATE INDEX ix_alert_reads_alert ON alert_reads(alert_id);

CREATE INDEX ix_notifications_alert_time ON notifications(alert_id,created_at DESC);

CREATE INDEX ix_notifications_phase ON notifications(phase_id,reminder_stage) WHERE phase_id IS NOT NULL;

CREATE UNIQUE INDEX ux_main_stage_message ON notifications(phase_id,reminder_stage) WHERE message_kind='phase_reminder';

CREATE INDEX ix_outbox_due ON notification_outbox(status,next_attempt_at);

CREATE UNIQUE INDEX ux_notification_recipient_user ON notification_recipients(notification_id,user_id) WHERE user_id IS NOT NULL;

CREATE UNIQUE INDEX ux_notification_recipient_role ON notification_recipients(notification_id,role_id) WHERE role_id IS NOT NULL;

CREATE INDEX ix_notification_recipient_user ON notification_recipients(user_id,feed_visible);

CREATE INDEX ix_notification_recipient_role ON notification_recipients(role_id,feed_visible);

CREATE INDEX ix_delivery_device ON notification_deliveries(device_id);

CREATE INDEX ix_delivery_recipient ON notification_deliveries(recipient_user_id) WHERE recipient_user_id IS NOT NULL;

CREATE INDEX ix_notification_reads_user ON notification_reads(user_id,read_at);

CREATE INDEX ix_alert_audience_user ON alert_notification_audience(user_id,alert_id);

CREATE INDEX ix_alert_audience_notification ON alert_notification_audience(first_notification_id) WHERE first_notification_id IS NOT NULL;

CREATE UNIQUE INDEX ux_one_active_phase ON notification_phases(alert_id) WHERE phase_status='active';

CREATE INDEX ix_phase_due ON notification_phases(phase_status,first_due_at,all_due_at);

CREATE INDEX ix_phase_initiator ON notification_phases(initiated_by_user_id) WHERE initiated_by_user_id IS NOT NULL;

CREATE INDEX ix_phase_source_notice ON notification_phases(source_notification_id) WHERE source_notification_id IS NOT NULL;

CREATE INDEX ix_phase_roles_role ON notification_phase_roles(role_id,phase_id);

CREATE INDEX ix_phase_dispatch_status ON notification_phase_dispatches(status,claimed_at);

CREATE INDEX ix_phase_audience_user ON notification_phase_audience(user_id,phase_id);

CREATE INDEX ix_phase_audience_notice ON notification_phase_audience(notification_id) WHERE notification_id IS NOT NULL;

CREATE INDEX ix_audit_actor_time ON audit_logs(user_id,created_at DESC);

CREATE INDEX ix_audit_subject_time ON audit_logs(entity_type,entity_id,created_at DESC);

CREATE TRIGGER config_prompt_tasks_insert BEFORE INSERT ON config_versions
BEGIN
    SELECT CASE WHEN NEW.binary_prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.binary_prompt_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'binary_prompt_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.scene_prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.scene_prompt_version_id AND r.task='scene_description') THEN RAISE(ABORT,'scene_prompt_version_id: wrong reference task/context') END;
END;

CREATE TRIGGER config_prompt_tasks_update BEFORE UPDATE ON config_versions
BEGIN
    SELECT CASE WHEN NEW.binary_prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.binary_prompt_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'binary_prompt_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.scene_prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.scene_prompt_version_id AND r.task='scene_description') THEN RAISE(ABORT,'scene_prompt_version_id: wrong reference task/context') END;
END;

CREATE TRIGGER alert_model_prompt_phase_insert BEFORE INSERT ON alerts
BEGIN
    SELECT CASE WHEN NEW.opening_model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.opening_model_version_id AND (r.task=NEW.incident_type OR (NEW.incident_type='physical_contact_bullying' AND r.task='violence'))) THEN RAISE(ABORT,'opening_model_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.binary_prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.binary_prompt_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'binary_prompt_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.active_phase_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notification_phases r WHERE r.id=NEW.active_phase_id AND r.alert_id=NEW.id AND r.phase_status='active' AND ((r.phase_kind='awaiting_decision' AND NEW.workflow_status='awaiting_confirmation') OR (r.phase_kind='awaiting_handler' AND NEW.workflow_status='confirmed_awaiting_handler'))) THEN RAISE(ABORT,'active_phase_id: wrong reference task/context') END;
END;

CREATE TRIGGER alert_model_prompt_phase_update BEFORE UPDATE ON alerts
BEGIN
    SELECT CASE WHEN NEW.opening_model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.opening_model_version_id AND (r.task=NEW.incident_type OR (NEW.incident_type='physical_contact_bullying' AND r.task='violence'))) THEN RAISE(ABORT,'opening_model_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.binary_prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.binary_prompt_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'binary_prompt_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.active_phase_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notification_phases r WHERE r.id=NEW.active_phase_id AND r.alert_id=NEW.id AND r.phase_status='active' AND ((r.phase_kind='awaiting_decision' AND NEW.workflow_status='awaiting_confirmation') OR (r.phase_kind='awaiting_handler' AND NEW.workflow_status='confirmed_awaiting_handler'))) THEN RAISE(ABORT,'active_phase_id: wrong reference task/context') END;
END;

CREATE TRIGGER detection_model_task_insert BEFORE INSERT ON detection_events
BEGIN
    SELECT CASE WHEN NEW.model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.model_version_id AND EXISTS(SELECT 1 FROM alerts a WHERE a.id=NEW.alert_id AND (r.task=a.incident_type OR (a.incident_type='physical_contact_bullying' AND r.task='violence')))) THEN RAISE(ABORT,'model_version_id: wrong reference task/context') END;
END;

CREATE TRIGGER detection_model_task_update BEFORE UPDATE ON detection_events
BEGIN
    SELECT CASE WHEN NEW.model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.model_version_id AND EXISTS(SELECT 1 FROM alerts a WHERE a.id=NEW.alert_id AND (r.task=a.incident_type OR (a.incident_type='physical_contact_bullying' AND r.task='violence')))) THEN RAISE(ABORT,'model_version_id: wrong reference task/context') END;
END;

CREATE TRIGGER review_references_insert BEFORE INSERT ON borderline_reviews
BEGIN
    SELECT CASE WHEN NEW.model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.model_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'model_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.prompt_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'prompt_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.detection_event_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM detection_events e JOIN alerts a ON a.id=e.alert_id WHERE e.id=NEW.detection_event_id AND e.alert_id=NEW.alert_id AND a.camera_id=NEW.camera_id) THEN RAISE(ABORT,'review event must belong to its alert/camera') END;
END;

CREATE TRIGGER review_references_update BEFORE UPDATE ON borderline_reviews
BEGIN
    SELECT CASE WHEN NEW.model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.model_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'model_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.prompt_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'prompt_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.detection_event_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM detection_events e JOIN alerts a ON a.id=e.alert_id WHERE e.id=NEW.detection_event_id AND e.alert_id=NEW.alert_id AND a.camera_id=NEW.camera_id) THEN RAISE(ABORT,'review event must belong to its alert/camera') END;
END;

CREATE TRIGGER description_version_tasks_insert BEFORE INSERT ON vlm_descriptions
BEGIN
    SELECT CASE WHEN NEW.model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.model_version_id AND r.task='scene_description') THEN RAISE(ABORT,'model_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.prompt_version_id AND r.task='scene_description') THEN RAISE(ABORT,'prompt_version_id: wrong reference task/context') END;
END;

CREATE TRIGGER description_version_tasks_update BEFORE UPDATE ON vlm_descriptions
BEGIN
    SELECT CASE WHEN NEW.model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.model_version_id AND r.task='scene_description') THEN RAISE(ABORT,'model_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.prompt_version_id AND r.task='scene_description') THEN RAISE(ABORT,'prompt_version_id: wrong reference task/context') END;
END;

CREATE TRIGGER source_file_kind_insert BEFORE INSERT ON camera_sources
BEGIN
    SELECT CASE WHEN NEW.media_file_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM media_files r WHERE r.id=NEW.media_file_id AND r.media_kind='camera_source') THEN RAISE(ABORT,'media_file_id: wrong reference task/context') END;
END;

CREATE TRIGGER source_file_kind_update BEFORE UPDATE ON camera_sources
BEGIN
    SELECT CASE WHEN NEW.media_file_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM media_files r WHERE r.id=NEW.media_file_id AND r.media_kind='camera_source') THEN RAISE(ABORT,'media_file_id: wrong reference task/context') END;
END;

CREATE TRIGGER evidence_file_kind_insert BEFORE INSERT ON evidence_records
BEGIN
    SELECT CASE WHEN NEW.media_file_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM media_files r WHERE r.id=NEW.media_file_id AND r.media_kind=CASE WHEN NEW.evidence_type='weapon_screenshot' THEN 'evidence_image' ELSE 'evidence_clip' END) THEN RAISE(ABORT,'media_file_id: wrong reference task/context') END;
END;

CREATE TRIGGER evidence_file_kind_update BEFORE UPDATE ON evidence_records
BEGIN
    SELECT CASE WHEN NEW.media_file_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM media_files r WHERE r.id=NEW.media_file_id AND r.media_kind=CASE WHEN NEW.evidence_type='weapon_screenshot' THEN 'evidence_image' ELSE 'evidence_clip' END) THEN RAISE(ABORT,'media_file_id: wrong reference task/context') END;
END;

CREATE TRIGGER phase_source_alert_insert BEFORE INSERT ON notification_phases
BEGIN
    SELECT CASE WHEN NEW.source_notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.source_notification_id AND r.alert_id=NEW.alert_id) THEN RAISE(ABORT,'source_notification_id: wrong reference task/context') END;
END;

CREATE TRIGGER phase_source_alert_update BEFORE UPDATE ON notification_phases
BEGIN
    SELECT CASE WHEN NEW.source_notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.source_notification_id AND r.alert_id=NEW.alert_id) THEN RAISE(ABORT,'source_notification_id: wrong reference task/context') END;
END;

CREATE TRIGGER phase_role_kind_insert BEFORE INSERT ON notification_phase_roles
BEGIN
    SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM notification_phases p WHERE p.id=NEW.phase_id AND p.phase_kind='awaiting_handler') THEN RAISE(ABORT,'live broadcast role belongs to Phase B') END;
END;

CREATE TRIGGER phase_role_kind_update BEFORE UPDATE ON notification_phase_roles
BEGIN
    SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM notification_phases p WHERE p.id=NEW.phase_id AND p.phase_kind='awaiting_handler') THEN RAISE(ABORT,'live broadcast role belongs to Phase B') END;
END;

CREATE TRIGGER phase_dispatch_notice_insert BEFORE INSERT ON notification_phase_dispatches
BEGIN
    SELECT CASE WHEN NEW.notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.notification_id AND r.message_kind='phase_reminder' AND r.phase_id=NEW.phase_id AND r.reminder_stage=NEW.stage) THEN RAISE(ABORT,'notification_id: wrong reference task/context') END;
END;

CREATE TRIGGER phase_dispatch_notice_update BEFORE UPDATE ON notification_phase_dispatches
BEGIN
    SELECT CASE WHEN NEW.notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.notification_id AND r.message_kind='phase_reminder' AND r.phase_id=NEW.phase_id AND r.reminder_stage=NEW.stage) THEN RAISE(ABORT,'notification_id: wrong reference task/context') END;
END;

CREATE TRIGGER phase_user_notice_insert BEFORE INSERT ON notification_phase_audience
BEGIN
    SELECT CASE WHEN NEW.notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.notification_id AND r.message_kind IN ('phase_reminder','late_repeat') AND r.phase_id=NEW.phase_id AND r.reminder_stage=NEW.stage) THEN RAISE(ABORT,'notification_id: wrong reference task/context') END;
END;

CREATE TRIGGER phase_user_notice_update BEFORE UPDATE ON notification_phase_audience
BEGIN
    SELECT CASE WHEN NEW.notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.notification_id AND r.message_kind IN ('phase_reminder','late_repeat') AND r.phase_id=NEW.phase_id AND r.reminder_stage=NEW.stage) THEN RAISE(ABORT,'notification_id: wrong reference task/context') END;
END;

CREATE TRIGGER alert_audience_notice_insert BEFORE INSERT ON alert_notification_audience
BEGIN
    SELECT CASE WHEN NEW.first_notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.first_notification_id AND r.alert_id=NEW.alert_id) THEN RAISE(ABORT,'first_notification_id: wrong reference task/context') END;
END;

CREATE TRIGGER alert_audience_notice_update BEFORE UPDATE ON alert_notification_audience
BEGIN
    SELECT CASE WHEN NEW.first_notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.first_notification_id AND r.alert_id=NEW.alert_id) THEN RAISE(ABORT,'first_notification_id: wrong reference task/context') END;
END;

CREATE TRIGGER outbox_has_stable_event_insert BEFORE INSERT ON notification_outbox
BEGIN
    SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM notifications n WHERE n.id=NEW.notification_id AND n.event_key IS NOT NULL) THEN RAISE(ABORT,'queued notification requires stable event key') END;
END;

CREATE TRIGGER outbox_has_stable_event_update BEFORE UPDATE ON notification_outbox
BEGIN
    SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM notifications n WHERE n.id=NEW.notification_id AND n.event_key IS NOT NULL) THEN RAISE(ABORT,'queued notification requires stable event key') END;
END;

CREATE TRIGGER config_weapon_profiles_insert BEFORE INSERT ON config_versions
BEGIN
    SELECT CASE WHEN NOT json_valid(NEW.weapon_class_thresholds_json) THEN RAISE(ABORT,'malformed threshold JSON') END;
    SELECT CASE WHEN (SELECT count(*) FROM json_each(NEW.weapon_class_thresholds_json))<>3 THEN RAISE(ABORT,'three sensitivity profiles required') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p WHERE p.key NOT IN ('sensitive','moderate','certain') OR p.type<>'object') THEN RAISE(ABORT,'invalid threshold profile') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p WHERE NOT EXISTS(SELECT 1 FROM json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key='knife') OR NOT EXISTS(SELECT 1 FROM json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key IN ('gun','long_gun'))) THEN RAISE(ABORT,'knife and gun/legacy long_gun thresholds required') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p JOIN json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.type NOT IN ('integer','real') OR c.value<0 OR c.value>1) THEN RAISE(ABORT,'threshold values must be probabilities') END;
    SELECT CASE WHEN NEW.validation_profile='runtime' AND EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p JOIN json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key NOT IN ('knife','gun') OR abs(c.value*10000-round(c.value*10000))>0.00000001) THEN RAISE(ABORT,'runtime classes/precision must follow current API') END;
END;

CREATE TRIGGER config_weapon_profiles_update BEFORE UPDATE ON config_versions
BEGIN
    SELECT CASE WHEN NOT json_valid(NEW.weapon_class_thresholds_json) THEN RAISE(ABORT,'malformed threshold JSON') END;
    SELECT CASE WHEN (SELECT count(*) FROM json_each(NEW.weapon_class_thresholds_json))<>3 THEN RAISE(ABORT,'three sensitivity profiles required') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p WHERE p.key NOT IN ('sensitive','moderate','certain') OR p.type<>'object') THEN RAISE(ABORT,'invalid threshold profile') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p WHERE NOT EXISTS(SELECT 1 FROM json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key='knife') OR NOT EXISTS(SELECT 1 FROM json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key IN ('gun','long_gun'))) THEN RAISE(ABORT,'knife and gun/legacy long_gun thresholds required') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p JOIN json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.type NOT IN ('integer','real') OR c.value<0 OR c.value>1) THEN RAISE(ABORT,'threshold values must be probabilities') END;
    SELECT CASE WHEN NEW.validation_profile='runtime' AND EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p JOIN json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key NOT IN ('knife','gun') OR abs(c.value*10000-round(c.value*10000))>0.00000001) THEN RAISE(ABORT,'runtime classes/precision must follow current API') END;
END;

CREATE TRIGGER detection_class_scores_insert BEFORE INSERT ON detection_events
BEGIN
    SELECT CASE WHEN NOT json_valid(NEW.weapon_class_scores_json) THEN RAISE(ABORT,'malformed score JSON') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_scores_json) WHERE type NOT IN ('integer','real') OR value<0 OR value>1) THEN RAISE(ABORT,'scores must be numeric probabilities') END;
END;

CREATE TRIGGER detection_class_scores_update BEFORE UPDATE ON detection_events
BEGIN
    SELECT CASE WHEN NOT json_valid(NEW.weapon_class_scores_json) THEN RAISE(ABORT,'malformed score JSON') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_scores_json) WHERE type NOT IN ('integer','real') OR value<0 OR value>1) THEN RAISE(ABORT,'scores must be numeric probabilities') END;
END;

CREATE TRIGGER strings_alerts_insert BEFORE INSERT ON alerts
BEGIN
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND NOT json_valid(NEW.broadcast_role_snapshot_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.broadcast_role_snapshot_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;

CREATE TRIGGER strings_alerts_update BEFORE UPDATE ON alerts
BEGIN
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND NOT json_valid(NEW.broadcast_role_snapshot_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.broadcast_role_snapshot_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;

CREATE TRIGGER strings_alert_actions_insert BEFORE INSERT ON alert_actions
BEGIN
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND NOT json_valid(NEW.broadcast_role_snapshot_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.broadcast_role_snapshot_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;

CREATE TRIGGER strings_alert_actions_update BEFORE UPDATE ON alert_actions
BEGIN
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND NOT json_valid(NEW.broadcast_role_snapshot_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.broadcast_role_snapshot_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;

CREATE TRIGGER strings_notification_phases_insert BEFORE INSERT ON notification_phases
BEGIN
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND NOT json_valid(NEW.broadcast_role_snapshot_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.broadcast_role_snapshot_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;

CREATE TRIGGER strings_notification_phases_update BEFORE UPDATE ON notification_phases
BEGIN
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND NOT json_valid(NEW.broadcast_role_snapshot_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.broadcast_role_snapshot_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;

CREATE TRIGGER strings_alert_notification_audience_insert BEFORE INSERT ON alert_notification_audience
BEGIN
    SELECT CASE WHEN NEW.qualification_reasons_json IS NOT NULL AND NOT json_valid(NEW.qualification_reasons_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.qualification_reasons_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.qualification_reasons_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;

CREATE TRIGGER strings_alert_notification_audience_update BEFORE UPDATE ON alert_notification_audience
BEGIN
    SELECT CASE WHEN NEW.qualification_reasons_json IS NOT NULL AND NOT json_valid(NEW.qualification_reasons_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.qualification_reasons_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.qualification_reasons_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;

CREATE TRIGGER native_confirm_roles BEFORE INSERT ON alert_actions
WHEN NEW.record_origin='workflow' AND NEW.action='confirm_only'
BEGIN SELECT CASE WHEN json_array_length(NEW.broadcast_role_snapshot_json) NOT BETWEEN 1 AND 20
THEN RAISE(ABORT,'confirm-only selects 1..20 roles') END; END;

CREATE TRIGGER review_vlm_snapshot_insert BEFORE INSERT ON borderline_reviews
BEGIN
    SELECT CASE WHEN NEW.vlm_settings_json IS NOT NULL AND NOT json_valid(NEW.vlm_settings_json) THEN RAISE(ABORT,'malformed VLM snapshot') END;
    SELECT CASE WHEN NEW.record_origin='task' AND NOT (COALESCE(json_type(NEW.vlm_settings_json,'$.model_id')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.model_id')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.quantization')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.quantization')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.binary_prompt_version')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.binary_prompt_version')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.scene_prompt_version')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.scene_prompt_version')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.input_fps') IN ('integer','real') AND json_extract(NEW.vlm_settings_json,'$.input_fps') BETWEEN 0.25 AND 12,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.video_token_budget') IN ('integer') AND json_extract(NEW.vlm_settings_json,'$.video_token_budget') BETWEEN 256 AND 16384,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.max_output_tokens') IN ('integer') AND json_extract(NEW.vlm_settings_json,'$.max_output_tokens') BETWEEN 32 AND 2048,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.scene_description_frames') IN ('integer') AND json_extract(NEW.vlm_settings_json,'$.scene_description_frames') BETWEEN 4 AND 128,0) AND COALESCE(json_extract(NEW.vlm_settings_json,'$.scene_description_frames')%2=0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.borderline_review_enabled') IN ('true','false'),0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.scene_description_enabled') IN ('true','false'),0)) THEN RAISE(ABORT,'VLM snapshot field schema failed') END;
END;

CREATE TRIGGER review_vlm_snapshot_update BEFORE UPDATE ON borderline_reviews
BEGIN
    SELECT CASE WHEN NEW.vlm_settings_json IS NOT NULL AND NOT json_valid(NEW.vlm_settings_json) THEN RAISE(ABORT,'malformed VLM snapshot') END;
    SELECT CASE WHEN NEW.record_origin='task' AND NOT (COALESCE(json_type(NEW.vlm_settings_json,'$.model_id')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.model_id')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.quantization')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.quantization')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.binary_prompt_version')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.binary_prompt_version')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.scene_prompt_version')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.scene_prompt_version')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.input_fps') IN ('integer','real') AND json_extract(NEW.vlm_settings_json,'$.input_fps') BETWEEN 0.25 AND 12,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.video_token_budget') IN ('integer') AND json_extract(NEW.vlm_settings_json,'$.video_token_budget') BETWEEN 256 AND 16384,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.max_output_tokens') IN ('integer') AND json_extract(NEW.vlm_settings_json,'$.max_output_tokens') BETWEEN 32 AND 2048,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.scene_description_frames') IN ('integer') AND json_extract(NEW.vlm_settings_json,'$.scene_description_frames') BETWEEN 4 AND 128,0) AND COALESCE(json_extract(NEW.vlm_settings_json,'$.scene_description_frames')%2=0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.borderline_review_enabled') IN ('true','false'),0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.scene_description_enabled') IN ('true','false'),0)) THEN RAISE(ABORT,'VLM snapshot field schema failed') END;
END;

CREATE TRIGGER description_summary_schema_insert BEFORE INSERT ON vlm_descriptions
BEGIN
    SELECT CASE WHEN NEW.structured_json IS NOT NULL AND NOT json_valid(NEW.structured_json) THEN RAISE(ABORT,'malformed scene JSON') END;
    SELECT CASE WHEN NEW.output_format='summary' AND NOT (COALESCE(json_type(NEW.structured_json)='object',0) AND COALESCE(json_type(NEW.structured_json,'$.description')='text' AND length(json_extract(NEW.structured_json,'$.description')) BETWEEN 1 AND 120,0) AND COALESCE(json_type(NEW.structured_json,'$.visible_evidence')='array' AND json_array_length(NEW.structured_json,'$.visible_evidence')<=2,0) AND COALESCE(json_extract(NEW.structured_json,'$.uncertainty') IN ('low','medium','high'),0) AND NOT EXISTS(SELECT 1 FROM json_each(NEW.structured_json,'$.visible_evidence') WHERE type<>'text')) THEN RAISE(ABORT,'SceneSummary schema failed') END;
END;

CREATE TRIGGER description_summary_schema_update BEFORE UPDATE ON vlm_descriptions
BEGIN
    SELECT CASE WHEN NEW.structured_json IS NOT NULL AND NOT json_valid(NEW.structured_json) THEN RAISE(ABORT,'malformed scene JSON') END;
    SELECT CASE WHEN NEW.output_format='summary' AND NOT (COALESCE(json_type(NEW.structured_json)='object',0) AND COALESCE(json_type(NEW.structured_json,'$.description')='text' AND length(json_extract(NEW.structured_json,'$.description')) BETWEEN 1 AND 120,0) AND COALESCE(json_type(NEW.structured_json,'$.visible_evidence')='array' AND json_array_length(NEW.structured_json,'$.visible_evidence')<=2,0) AND COALESCE(json_extract(NEW.structured_json,'$.uncertainty') IN ('low','medium','high'),0) AND NOT EXISTS(SELECT 1 FROM json_each(NEW.structured_json,'$.visible_evidence') WHERE type<>'text')) THEN RAISE(ABORT,'SceneSummary schema failed') END;
END;

CREATE TRIGGER immutable_config_versions BEFORE UPDATE ON config_versions BEGIN SELECT RAISE(ABORT,'config_versions: append-only fact/version'); END;

CREATE TRIGGER immutable_model_versions BEFORE UPDATE ON model_versions BEGIN SELECT RAISE(ABORT,'model_versions: append-only fact/version'); END;

CREATE TRIGGER immutable_prompt_versions BEFORE UPDATE ON prompt_versions BEGIN SELECT RAISE(ABORT,'prompt_versions: append-only fact/version'); END;

CREATE TRIGGER immutable_detection_events BEFORE UPDATE ON detection_events BEGIN SELECT RAISE(ABORT,'detection_events: append-only fact/version'); END;

CREATE TRIGGER immutable_alert_actions BEFORE UPDATE ON alert_actions BEGIN SELECT RAISE(ABORT,'alert_actions: append-only fact/version'); END;

CREATE TRIGGER immutable_schema_migrations BEFORE UPDATE ON schema_migrations BEGIN SELECT RAISE(ABORT,'schema_migrations: append-only fact/version'); END;

CREATE TRIGGER retain_config_versions BEFORE DELETE ON config_versions BEGIN SELECT RAISE(ABORT,'config_versions: retain version/history'); END;

CREATE TRIGGER retain_model_versions BEFORE DELETE ON model_versions BEGIN SELECT RAISE(ABORT,'model_versions: retain version/history'); END;

CREATE TRIGGER retain_prompt_versions BEFORE DELETE ON prompt_versions BEGIN SELECT RAISE(ABORT,'prompt_versions: retain version/history'); END;

CREATE TRIGGER retain_alert_actions BEFORE DELETE ON alert_actions BEGIN SELECT RAISE(ABORT,'alert_actions: retain version/history'); END;

CREATE TRIGGER retain_schema_migrations BEFORE DELETE ON schema_migrations BEGIN SELECT RAISE(ABORT,'schema_migrations: retain version/history'); END;

CREATE TRIGGER immutable_audit BEFORE UPDATE ON audit_logs WHEN NEW.id IS NOT OLD.id OR NEW.action IS NOT OLD.action OR NEW.entity_type IS NOT OLD.entity_type OR NEW.entity_id IS NOT OLD.entity_id OR NEW.detail_json IS NOT OLD.detail_json OR NEW.created_at IS NOT OLD.created_at OR (NEW.user_id IS NOT OLD.user_id AND NEW.user_id IS NOT NULL) BEGIN SELECT RAISE(ABORT,'audit is append-only except actor FK deletion cleanup'); END;

CREATE TRIGGER retain_audit BEFORE DELETE ON audit_logs BEGIN SELECT RAISE(ABORT,'retain administrative audit'); END;

CREATE TRIGGER immutable_users_id BEFORE UPDATE OF id ON users WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_roles_id BEFORE UPDATE OF id ON roles WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_devices_id BEFORE UPDATE OF id ON devices WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_locations_id BEFORE UPDATE OF id ON locations WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_cameras_id BEFORE UPDATE OF id ON cameras WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_media_files_id BEFORE UPDATE OF id ON media_files WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_camera_sources_id BEFORE UPDATE OF id ON camera_sources WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_alerts_id BEFORE UPDATE OF id ON alerts WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_borderline_reviews_id BEFORE UPDATE OF id ON borderline_reviews WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_evidence_records_id BEFORE UPDATE OF id ON evidence_records WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_vlm_descriptions_id BEFORE UPDATE OF id ON vlm_descriptions WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_notifications_id BEFORE UPDATE OF id ON notifications WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_notification_outbox_id BEFORE UPDATE OF id ON notification_outbox WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_notification_recipients_id BEFORE UPDATE OF id ON notification_recipients WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_notification_deliveries_id BEFORE UPDATE OF id ON notification_deliveries WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_notification_phases_id BEFORE UPDATE OF id ON notification_phases WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER immutable_mobile_settings_id BEFORE UPDATE OF id ON mobile_settings WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;

CREATE TRIGGER clear_stopped_phase AFTER UPDATE OF phase_status ON notification_phases
WHEN NEW.phase_status<>'active'
BEGIN UPDATE alerts SET active_phase_id=NULL WHERE active_phase_id=NEW.id; END;

CREATE TRIGGER keep_notification_identity BEFORE UPDATE ON notifications
WHEN NEW.alert_id IS NOT OLD.alert_id OR NEW.event_key IS NOT OLD.event_key OR NEW.phase_id IS NOT OLD.phase_id OR NEW.reminder_stage IS NOT OLD.reminder_stage OR NEW.message_kind IS NOT OLD.message_kind
BEGIN SELECT RAISE(ABORT,'notification identity/context is immutable'); END;

CREATE TRIGGER freeze_dispatched_content BEFORE UPDATE OF title,body,data_payload_json ON notifications
WHEN (NEW.title IS NOT OLD.title OR NEW.body IS NOT OLD.body OR NEW.data_payload_json IS NOT OLD.data_payload_json)
AND (EXISTS(SELECT 1 FROM notification_outbox o WHERE o.notification_id=OLD.id AND o.attempt_count>0)
     OR EXISTS(SELECT 1 FROM notification_deliveries d WHERE d.notification_id=OLD.id AND d.attempt_count>0))
BEGIN SELECT RAISE(ABORT,'content is frozen after dispatch begins'); END;

CREATE TRIGGER keep_notification_outbox_context BEFORE UPDATE ON notification_outbox WHEN NEW.notification_id IS NOT OLD.notification_id OR NEW.created_at IS NOT OLD.created_at BEGIN SELECT RAISE(ABORT,'context snapshot is immutable'); END;

CREATE TRIGGER keep_notification_deliveries_context BEFORE UPDATE ON notification_deliveries WHEN NEW.notification_id IS NOT OLD.notification_id OR NEW.device_id IS NOT OLD.device_id OR NEW.recipient_user_id IS NOT OLD.recipient_user_id BEGIN SELECT RAISE(ABORT,'context snapshot is immutable'); END;

CREATE TRIGGER keep_borderline_reviews_context BEFORE UPDATE ON borderline_reviews WHEN NEW.record_origin IS NOT OLD.record_origin OR NEW.camera_id IS NOT OLD.camera_id OR NEW.alert_id IS NOT OLD.alert_id OR NEW.detection_event_id IS NOT OLD.detection_event_id OR NEW.model_version_id IS NOT OLD.model_version_id OR NEW.prompt_version_id IS NOT OLD.prompt_version_id OR NEW.config_version_id IS NOT OLD.config_version_id OR NEW.detector_confidence IS NOT OLD.detector_confidence OR NEW.source_start_seconds IS NOT OLD.source_start_seconds OR NEW.source_end_seconds IS NOT OLD.source_end_seconds OR NEW.context_path IS NOT OLD.context_path OR NEW.vlm_settings_json IS NOT OLD.vlm_settings_json BEGIN SELECT RAISE(ABORT,'context snapshot is immutable'); END;

CREATE TRIGGER keep_notification_phases_context BEFORE UPDATE ON notification_phases WHEN NEW.alert_id IS NOT OLD.alert_id OR NEW.phase_kind IS NOT OLD.phase_kind OR NEW.started_at IS NOT OLD.started_at OR NEW.first_after_seconds IS NOT OLD.first_after_seconds OR NEW.all_after_seconds IS NOT OLD.all_after_seconds OR NEW.first_due_at IS NOT OLD.first_due_at OR NEW.all_due_at IS NOT OLD.all_due_at BEGIN SELECT RAISE(ABORT,'context snapshot is immutable'); END;

COMMIT;
