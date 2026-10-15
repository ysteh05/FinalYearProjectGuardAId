# 原44表逐属性迁移去向

这是规划映射，未执行数据迁移。所有原值（包括历史NULL和精度）都有明确去向。时间列无损规范化UTC微秒Z；公开ID保留。审计归档不是默默丢弃：必须保存原表/原ID/原属性、差异及新映射，并在验收中核对。

## alert_actions

| 原attribute | 去向与条件 |
|---|---|
| `id` | alert_actions.id |
| `alert_id` | alert_actions.alert_id |
| `user_id` | alert_actions.user_id |
| `action` | alert_actions.action |
| `note` | alert_actions.note |
| `created_at` | alert_actions.created_at；保留时刻及微秒，无损统一UTC格式。 |
## alert_notification_audience

| 原attribute | 去向与条件 |
|---|---|
| `alert_id` | alert_notification_audience.alert_id |
| `user_id` | alert_notification_audience.user_id |
| `qualification_reasons_json` | alert_notification_audience.qualification_reasons_json |
| `first_notification_id` | alert_notification_audience.first_notification_id |
| `first_queued_at` | alert_notification_audience.first_queued_at；保留时刻及微秒，无损统一UTC格式。 |
| `first_sent_at` | alert_notification_audience.first_sent_at；保留时刻及微秒，无损统一UTC格式。 |
## alert_notification_escalation_configs

| 原attribute | 去向与条件 |
|---|---|
| `config_version_id` | config_versions.id：五个同版本行合并；缺行/重复阻断。 |
| `same_audience_repeat_after_seconds` | config_versions.same_audience_repeat_after_seconds |
| `all_users_escalation_after_seconds` | config_versions.all_users_escalation_after_seconds |
## alert_notification_escalation_dispatches

| 原attribute | 去向与条件 |
|---|---|
| `alert_id` | notification_phases.alert_id → 对应notification_phase_dispatches.phase_id：原alert-stage映射存migration audit。 |
| `stage` | notification_phase_dispatches.stage：重复相同事实合并，差异保留独立归档phase-stage。 |
| `status` | notification_phase_dispatches.status：重复相同事实合并，差异保留独立归档phase-stage。 |
| `notification_id` | notification_phase_dispatches.notification_id：重复相同事实合并，差异保留独立归档phase-stage。 |
| `recipient_count` | notification_phase_dispatches.recipient_count：重复相同事实合并，差异保留独立归档phase-stage。 |
| `sent_count` | notification_phase_dispatches.sent_count：重复相同事实合并，差异保留独立归档phase-stage。 |
| `failed_count` | notification_phase_dispatches.failed_count：重复相同事实合并，差异保留独立归档phase-stage。 |
| `error_code` | notification_phase_dispatches.error_code：重复相同事实合并，差异保留独立归档phase-stage。 |
| `claimed_at` | notification_phase_dispatches.claimed_at：重复相同事实合并，差异保留独立归档phase-stage。；保留时刻及微秒，无损统一UTC格式。 |
| `completed_at` | notification_phase_dispatches.completed_at：重复相同事实合并，差异保留独立归档phase-stage。；保留时刻及微秒，无损统一UTC格式。 |
## alert_notification_escalation_schedules

| 原attribute | 去向与条件 |
|---|---|
| `alert_id` | notification_phases.alert_id：对应已证明同一phase，或独立legacy归档phase；原对应关系保存在迁移审计。 |
| `initial_notification_id` | notification_phases.source_notification_id |
| `same_audience_repeat_after_seconds` | notification_phases.first_after_seconds |
| `all_users_escalation_after_seconds` | notification_phases.all_after_seconds |
| `initial_notified_at` | notification_phases.started_at；保留时刻及微秒，无损统一UTC格式。 |
| `same_audience_repeat_due_at` | notification_phases.first_due_at；保留时刻及微秒，无损统一UTC格式。 |
| `all_users_escalation_due_at` | notification_phases.all_due_at；保留时刻及微秒，无损统一UTC格式。 |
## alert_reads

| 原attribute | 去向与条件 |
|---|---|
| `user_id` | alert_reads.user_id |
| `alert_id` | alert_reads.alert_id |
| `read_at` | alert_reads.read_at；保留时刻及微秒，无损统一UTC格式。 |
## alerts

| 原attribute | 去向与条件 |
|---|---|
| `id` | alerts.id |
| `camera_id` | alerts.camera_id |
| `incident_type` | alerts.incident_type |
| `status` | 派生：alerts.workflow_status → 固定status映射，保留原API；冲突阻断。 |
| `sensitivity_mode` | alerts.sensitivity_mode |
| `is_borderline_promoted` | alerts.is_borderline_promoted |
| `opening_config_version_id` | alerts.opening_config_version_id |
| `opening_model_version_id` | alerts.opening_model_version_id |
| `binary_prompt_version_id` | alerts.binary_prompt_version_id |
| `created_at` | alerts.created_at；保留时刻及微秒，无损统一UTC格式。 |
| `resolved_at` | alerts.resolved_at；保留时刻及微秒，无损统一UTC格式。 |
| `is_provisional` | alerts.is_provisional |
| `borderline_review_status` | alerts.borderline_review_status |
| `evidence_status` | alerts.evidence_status |
## audit_logs

| 原attribute | 去向与条件 |
|---|---|
| `id` | audit_logs.id |
| `user_id` | audit_logs.user_id |
| `action` | audit_logs.action |
| `entity_type` | audit_logs.entity_type |
| `entity_id` | audit_logs.entity_id |
| `detail_json` | audit_logs.detail_json |
| `created_at` | audit_logs.created_at；保留时刻及微秒，无损统一UTC格式。 |
## borderline_review_tasks

| 原attribute | 去向与条件 |
|---|---|
| `id` | borderline_reviews.id |
| `alert_id` | borderline_reviews.alert_id |
| `camera_id` | borderline_reviews.camera_id |
| `detection_event_key` | borderline_reviews.detection_event_id：实际detector ID，校验同alert/camera。 |
| `detector_confidence` | borderline_reviews.detector_confidence |
| `source_start_seconds` | borderline_reviews.source_start_seconds |
| `source_end_seconds` | borderline_reviews.source_end_seconds |
| `context_path` | borderline_reviews.context_path |
| `status` | borderline_reviews.status |
| `attempt_count` | borderline_reviews.attempt_count |
| `raise_alert` | borderline_reviews.raise_alert |
| `uncertainty` | borderline_reviews.uncertainty |
| `brief_reason` | borderline_reviews.brief_reason |
| `error_code` | borderline_reviews.error_code |
| `model_version` | borderline_reviews.model_version_id：按原task+版本唯一登记映射；原标签可由FK查询，无法唯一映射则阻断。 |
| `prompt_version` | borderline_reviews.prompt_version_id：按唯一原版本标签映射，不伪造内容。 |
| `config_version` | borderline_reviews.config_version_id：按config_versions.version_number唯一映射。 |
| `vlm_settings_json` | borderline_reviews.vlm_settings_json |
| `created_at` | borderline_reviews.created_at；保留时刻及微秒，无损统一UTC格式。 |
| `started_at` | borderline_reviews.started_at；保留时刻及微秒，无损统一UTC格式。 |
| `completed_at` | borderline_reviews.completed_at；保留时刻及微秒，无损统一UTC格式。 |
| `supplemental_notification_status` | borderline_reviews.supplemental_notification_status |
## borderline_reviews

| 原attribute | 去向与条件 |
|---|---|
| `id` | borderline_reviews.id；已证明同一task镜像时保存为legacy_review_id，否则保留原id的audit_only行。 |
| `camera_id` | borderline_reviews.camera_id |
| `alert_id` | borderline_reviews.alert_id |
| `model_version_id` | borderline_reviews.model_version_id |
| `prompt_version_id` | borderline_reviews.prompt_version_id |
| `config_version_id` | borderline_reviews.config_version_id |
| `detector_confidence` | borderline_reviews.detector_confidence |
| `raise_alert` | borderline_reviews.raise_alert |
| `uncertainty` | borderline_reviews.uncertainty |
| `brief_reason` | borderline_reviews.brief_reason |
| `source_start_seconds` | borderline_reviews.source_start_seconds |
| `source_end_seconds` | borderline_reviews.source_end_seconds |
| `reviewed_at` | borderline_reviews.reviewed_at；保留时刻及微秒，无损统一UTC格式。 |
## borderline_role_settings

| 原attribute | 去向与条件 |
|---|---|
| `role_id` | roles.id：按PK合并，不改变role身份。 |
| `default_enabled` | roles.borderline_default_enabled |
| `updated_by_user_id` | roles.borderline_updated_by_user_id |
| `updated_at` | roles.borderline_updated_at：与roles.updated_at分开。；保留时刻及微秒，无损统一UTC格式。 |
## borderline_settings_revision

| 原attribute | 去向与条件 |
|---|---|
| `id` | mobile_settings.id=global：将原单例身份和revision映射记入migration audit。 |
| `revision` | mobile_settings.borderline_revision |
## borderline_user_settings

| 原attribute | 去向与条件 |
|---|---|
| `user_id` | users.id：按PK合并，不改变user身份。 |
| `enabled` | users.borderline_enabled：无旧行保持NULL，不填false。 |
| `setting_source` | users.borderline_source |
| `updated_by_user_id` | users.borderline_updated_by_user_id |
| `updated_at` | users.borderline_updated_at：与users.updated_at分开。；保留时刻及微秒，无损统一UTC格式。 |
## camera_sources

| 原attribute | 去向与条件 |
|---|---|
| `id` | camera_sources.id |
| `camera_id` | camera_sources.camera_id |
| `media_file_id` | camera_sources.media_file_id |
| `source_type` | camera_sources.source_type |
| `rtsp_endpoint_ref` | camera_sources.rtsp_endpoint_ref |
| `duration_seconds` | camera_sources.duration_seconds |
| `is_active` | camera_sources.is_active |
| `created_at` | camera_sources.created_at；保留时刻及微秒，无损统一UTC格式。 |
## cameras

| 原attribute | 去向与条件 |
|---|---|
| `id` | cameras.id |
| `location_id` | cameras.location_id |
| `name` | cameras.name |
| `enabled` | cameras.enabled |
| `status` | cameras.status |
| `created_at` | cameras.created_at；保留时刻及微秒，无损统一UTC格式。 |
| `updated_at` | cameras.updated_at；保留时刻及微秒，无损统一UTC格式。 |
## config_versions

| 原attribute | 去向与条件 |
|---|---|
| `id` | config_versions.id |
| `version_number` | config_versions.version_number |
| `default_sensitivity` | config_versions.default_sensitivity |
| `created_by` | config_versions.created_by |
| `created_at` | config_versions.created_at；保留时刻及微秒，无损统一UTC格式。 |
| `detection_debug_mode_enabled` | config_versions.detection_debug_mode_enabled |
## detection_events

| 原attribute | 去向与条件 |
|---|---|
| `id` | detection_events.id |
| `alert_id` | detection_events.alert_id |
| `model_version_id` | detection_events.model_version_id |
| `config_version_id` | detection_events.config_version_id |
| `confidence` | detection_events.confidence |
| `decision_source` | detection_events.decision_source |
| `source_start_seconds` | detection_events.source_start_seconds |
| `source_end_seconds` | detection_events.source_end_seconds |
| `detected_at` | detection_events.detected_at；保留时刻及微秒，无损统一UTC格式。 |
| `qualifications_json` | detection_events.qualifications_json |
| `weapon_class_scores_json` | detection_events.weapon_class_scores_json |
## devices

| 原attribute | 去向与条件 |
|---|---|
| `id` | devices.id |
| `user_id` | devices.user_id |
| `installation_id` | devices.installation_id |
| `fcm_registration_value` | devices.fcm_registration_value |
| `platform` | devices.platform |
| `device_label` | devices.device_label |
| `enabled` | devices.enabled |
| `last_seen_at` | devices.last_seen_at；保留时刻及微秒，无损统一UTC格式。 |
| `created_at` | devices.created_at；保留时刻及微秒，无损统一UTC格式。 |
| `updated_at` | devices.updated_at；保留时刻及微秒，无损统一UTC格式。 |
## evidence_records

| 原attribute | 去向与条件 |
|---|---|
| `id` | evidence_records.id |
| `alert_id` | evidence_records.alert_id |
| `media_file_id` | evidence_records.media_file_id |
| `evidence_type` | evidence_records.evidence_type |
| `source_start_seconds` | evidence_records.source_start_seconds |
| `source_end_seconds` | evidence_records.source_end_seconds |
| `captured_at` | evidence_records.captured_at；保留时刻及微秒，无损统一UTC格式。 |
| `delete_after` | evidence_records.delete_after；保留时刻及微秒，无损统一UTC格式。 |
## locations

| 原attribute | 去向与条件 |
|---|---|
| `id` | locations.id |
| `name` | locations.name |
| `normalized_name` | locations.normalized_name |
| `building` | locations.building |
| `floor` | locations.floor |
| `description` | locations.description |
| `created_at` | locations.created_at；保留时刻及微秒，无损统一UTC格式。 |
## media_files

| 原attribute | 去向与条件 |
|---|---|
| `id` | media_files.id |
| `relative_path` | media_files.relative_path |
| `original_filename` | media_files.original_filename |
| `media_kind` | media_files.media_kind |
| `content_type` | media_files.content_type |
| `size_bytes` | media_files.size_bytes |
| `sha256` | media_files.sha256 |
| `created_at` | media_files.created_at；保留时刻及微秒，无损统一UTC格式。 |
## mobile_alert_reminder_audience

| 原attribute | 去向与条件 |
|---|---|
| `alert_id` | 由notification_phases.alert_id派生；迁移先验证与原值一致。 |
| `phase_id` | notification_phase_audience.phase_id |
| `user_id` | notification_phase_audience.user_id |
| `stage` | notification_phase_audience.stage |
| `status` | notification_phase_audience.status |
| `outbox_id` | notification_phase_audience.notification_id，按原outbox唯一映射；原outbox ID仍保留notification_outbox.id。 |
| `updated_at` | notification_phase_audience.updated_at；保留时刻及微秒，无损统一UTC格式。 |
## mobile_alert_reminder_dispatches

| 原attribute | 去向与条件 |
|---|---|
| `phase_id` | notification_phase_dispatches.phase_id |
| `stage` | notification_phase_dispatches.stage |
| `status` | notification_phase_dispatches.status |
| `notification_id` | notification_phase_dispatches.notification_id |
| `recipient_count` | notification_phase_dispatches.recipient_count |
| `sent_count` | notification_phase_dispatches.sent_count |
| `failed_count` | notification_phase_dispatches.failed_count |
| `error_code` | notification_phase_dispatches.error_code |
| `claimed_at` | notification_phase_dispatches.claimed_at；保留时刻及微秒，无损统一UTC格式。 |
| `completed_at` | notification_phase_dispatches.completed_at；保留时刻及微秒，无损统一UTC格式。 |
## mobile_alert_reminder_phases

| 原attribute | 去向与条件 |
|---|---|
| `id` | notification_phases.id |
| `alert_id` | notification_phases.alert_id |
| `phase_kind` | notification_phases.phase_kind |
| `phase_status` | notification_phases.phase_status |
| `started_at` | notification_phases.started_at；保留时刻及微秒，无损统一UTC格式。 |
| `first_after_seconds` | notification_phases.first_after_seconds |
| `all_after_seconds` | notification_phases.all_after_seconds |
| `first_due_at` | notification_phases.first_due_at；保留时刻及微秒，无损统一UTC格式。 |
| `all_due_at` | notification_phases.all_due_at；保留时刻及微秒，无损统一UTC格式。 |
| `broadcast_role_ids_json` | notification_phases.broadcast_role_snapshot_json；存在的角色展开notification_phase_roles，删除角色仍保留原快照。 |
| `initiated_by_user_id` | notification_phases.initiated_by_user_id |
| `source_notification_id` | notification_phases.source_notification_id |
| `cancel_reason` | notification_phases.cancel_reason |
| `completed_at` | notification_phases.completed_at；保留时刻及微秒，无损统一UTC格式。 |
## mobile_alert_workflow_events

| 原attribute | 去向与条件 |
|---|---|
| `id` | alert_actions.id |
| `alert_id` | alert_actions.alert_id |
| `actor_user_id` | alert_actions.user_id |
| `event_type` | alert_actions.action |
| `comment` | alert_actions.note：旧/新差异值原样保留。 |
| `broadcast_role_ids_json` | alert_actions.broadcast_role_snapshot_json |
| `idempotency_key` | alert_actions.idempotency_key |
| `created_at` | alert_actions.created_at；保留时刻及微秒，无损统一UTC格式。 |
## mobile_alert_workflows

| 原attribute | 去向与条件 |
|---|---|
| `alert_id` | alerts.id：一对一主键合并，不生成新ID。 |
| `workflow_status` | alerts.workflow_status |
| `handler_user_id` | alerts.handler_user_id |
| `active_phase_id` | alerts.active_phase_id |
| `broadcast_role_ids_json` | alerts.broadcast_role_snapshot_json：完整历史数组；live关系属于phase。 |
| `revision` | alerts.revision |
| `updated_at` | alerts.workflow_updated_at：不要覆盖alerts.created_at。；保留时刻及微秒，无损统一UTC格式。 |
## mobile_notification_outbox

| 原attribute | 去向与条件 |
|---|---|
| `id` | notification_outbox.id |
| `notification_id` | notification_outbox.notification_id |
| `event_key` | notifications.event_key：保持原值及唯一性。 |
| `channel_id` | notification_outbox.channel_id |
| `data_payload_json` | notifications.data_payload_json：先核对双份一致；不一致归档双方并阻断。 |
| `status` | notification_outbox.status |
| `attempt_count` | notification_outbox.attempt_count |
| `next_attempt_at` | notification_outbox.next_attempt_at；保留时刻及微秒，无损统一UTC格式。 |
| `claimed_at` | notification_outbox.claimed_at；保留时刻及微秒，无损统一UTC格式。 |
| `last_error_code` | notification_outbox.last_error_code |
| `created_at` | notification_outbox.created_at；保留时刻及微秒，无损统一UTC格式。 |
| `updated_at` | notification_outbox.updated_at；保留时刻及微秒，无损统一UTC格式。 |
## mobile_notification_outbox_deliveries

| 原attribute | 去向与条件 |
|---|---|
| `outbox_id` | notification_deliveries.notification_id：按原outbox唯一映射；outbox ID保留。 |
| `device_id` | notification_deliveries.device_id：镜像逐字段一致才合并，次数不相加。 |
| `status` | notification_deliveries.status：镜像逐字段一致才合并，次数不相加。 |
| `provider_message_id` | notification_deliveries.provider_message_id：镜像逐字段一致才合并，次数不相加。 |
| `error_code` | notification_deliveries.error_code：镜像逐字段一致才合并，次数不相加。 |
| `attempt_count` | notification_deliveries.attempt_count：镜像逐字段一致才合并，次数不相加。 |
| `updated_at` | notification_deliveries.updated_at：与delivery.last_attempt_at分别保存；先核对原镜像。；保留时刻及微秒，无损统一UTC格式。 |
## mobile_notification_outbox_recipients

| 原attribute | 去向与条件 |
|---|---|
| `outbox_id` | notification_recipients.notification_id：通过保留原ID的outbox关联；queued_target=1。 |
| `user_id` | notification_recipients.user_id；feed标记与queue标记独立，不能用并集扩大推送。 |
## mobile_settings

| 原attribute | 去向与条件 |
|---|---|
| `id` | mobile_settings.id |
| `borderline_alert_enabled` | mobile_settings.borderline_alert_enabled |
| `borderline_recipient_roles_json` | roles.compat_borderline_selected：原集合展开；不存在/被删role ID完整原数组存migration audit。 |
| `emergency_call_simulation_only` | mobile_settings.emergency_call_simulation_only |
| `updated_by_user_id` | mobile_settings.updated_by_user_id |
| `updated_at` | mobile_settings.updated_at；保留时刻及微秒，无损统一UTC格式。 |
## model_versions

| 原attribute | 去向与条件 |
|---|---|
| `id` | model_versions.id |
| `task` | model_versions.task |
| `model_name` | model_versions.model_name |
| `version` | model_versions.version |
| `framework` | model_versions.framework |
| `quantization` | model_versions.quantization |
| `checkpoint_ref` | model_versions.checkpoint_ref |
| `created_at` | model_versions.created_at；保留时刻及微秒，无损统一UTC格式。 |
## notification_deliveries

| 原attribute | 去向与条件 |
|---|---|
| `id` | notification_deliveries.id |
| `notification_id` | notification_deliveries.notification_id |
| `device_id` | notification_deliveries.device_id |
| `status` | notification_deliveries.status |
| `provider_message_id` | notification_deliveries.provider_message_id |
| `attempt_count` | notification_deliveries.attempt_count |
| `last_attempt_at` | notification_deliveries.last_attempt_at；updated_at有outbox镜像才复制，独立旧行未记录updated_at，保持NULL，不能用last_attempt_at代替。；保留时刻及微秒，无损统一UTC格式。 |
| `error_code` | notification_deliveries.error_code |
## notification_reads

| 原attribute | 去向与条件 |
|---|---|
| `notification_id` | notification_reads.notification_id |
| `user_id` | notification_reads.user_id |
| `read_at` | notification_reads.read_at；保留时刻及微秒，无损统一UTC格式。 |
## notification_recipients

| 原attribute | 去向与条件 |
|---|---|
| `notification_id` | notification_recipients.notification_id |
| `role_id` | notification_recipients.role_id |
| `user_id` | notification_recipients.user_id |
## notifications

| 原attribute | 去向与条件 |
|---|---|
| `id` | notifications.id |
| `alert_id` | notifications.alert_id |
| `title` | notifications.title |
| `body` | notifications.body |
| `data_payload_json` | notifications.data_payload_json |
| `created_at` | notifications.created_at；保留时刻及微秒，无损统一UTC格式。 |
## prompt_versions

| 原attribute | 去向与条件 |
|---|---|
| `id` | prompt_versions.id |
| `task` | prompt_versions.task |
| `version` | prompt_versions.version |
| `content_sha256` | prompt_versions.content_sha256 |
| `content` | prompt_versions.content |
| `created_at` | prompt_versions.created_at；保留时刻及微秒，无损统一UTC格式。 |
## roles

| 原attribute | 去向与条件 |
|---|---|
| `id` | roles.id |
| `name` | roles.name |
| `description` | roles.description |
| `created_at` | roles.created_at；保留时刻及微秒，无损统一UTC格式。 |
| `sensitivity_mode` | roles.sensitivity_mode |
| `is_system` | roles.is_system |
| `display_name` | roles.display_name |
| `can_manage_roles` | roles.can_manage_roles |
| `can_manage_users` | roles.can_manage_users |
| `can_manage_borderline_settings` | roles.can_manage_borderline_settings |
| `updated_at` | roles.updated_at；保留时刻及微秒，无损统一UTC格式。 |
## schema_migrations

| 原attribute | 去向与条件 |
|---|---|
| `version` | schema_migrations.version |
| `name` | schema_migrations.name |
| `applied_at` | schema_migrations.applied_at；保留时刻及微秒，无损统一UTC格式。 |
## user_roles

| 原attribute | 去向与条件 |
|---|---|
| `user_id` | user_roles.user_id |
| `role_id` | user_roles.role_id |
| `assigned_by_user_id` | user_roles.assigned_by_user_id |
| `assigned_at` | user_roles.assigned_at；保留时刻及微秒，无损统一UTC格式。 |
## users

| 原attribute | 去向与条件 |
|---|---|
| `id` | users.id |
| `firebase_uid` | users.firebase_uid |
| `email_cache` | users.email_cache |
| `display_name` | users.display_name |
| `status` | users.status |
| `created_at` | users.created_at；保留时刻及微秒，无损统一UTC格式。 |
| `updated_at` | users.updated_at；保留时刻及微秒，无损统一UTC格式。 |
## violence_configs

| 原attribute | 去向与条件 |
|---|---|
| `config_version_id` | config_versions.id：五个同版本行合并；缺行/重复阻断。 |
| `segment_seconds` | config_versions.violence_segment_seconds |
| `stride_seconds` | config_versions.violence_stride_seconds |
| `detector_input_frames` | config_versions.violence_detector_input_frames |
| `sensitive_threshold` | config_versions.violence_sensitive_threshold |
| `moderate_threshold` | config_versions.violence_moderate_threshold |
| `certain_threshold` | config_versions.violence_certain_threshold |
| `cooldown_seconds` | config_versions.violence_cooldown_seconds |
| `incident_merge_gap_seconds` | config_versions.violence_merge_gap_seconds |
## vlm_configs

| 原attribute | 去向与条件 |
|---|---|
| `config_version_id` | config_versions.id：五个同版本行合并；缺行/重复阻断。 |
| `model_id` | config_versions.vlm_model_id |
| `quantization` | config_versions.vlm_quantization |
| `input_fps` | config_versions.vlm_input_fps |
| `video_token_budget` | config_versions.vlm_video_token_budget |
| `max_output_tokens` | config_versions.vlm_max_output_tokens |
| `borderline_review_enabled` | config_versions.vlm_borderline_review_enabled |
| `scene_description_enabled` | config_versions.vlm_scene_description_enabled |
| `binary_prompt_version` | config_versions.binary_prompt_version_id → prompt_versions.version原标签唯一映射。 |
| `scene_prompt_version` | config_versions.scene_prompt_version_id → prompt_versions.version原标签唯一映射。 |
| `scene_description_frames` | config_versions.vlm_scene_description_frames |
## vlm_descriptions

| 原attribute | 去向与条件 |
|---|---|
| `id` | vlm_descriptions.id |
| `alert_id` | vlm_descriptions.alert_id |
| `model_version_id` | vlm_descriptions.model_version_id |
| `prompt_version_id` | vlm_descriptions.prompt_version_id |
| `status` | vlm_descriptions.status |
| `structured_json` | vlm_descriptions.structured_json |
| `display_text` | vlm_descriptions.display_text |
| `error_code` | vlm_descriptions.error_code |
| `created_at` | vlm_descriptions.created_at；保留时刻及微秒，无损统一UTC格式。 |
| `completed_at` | vlm_descriptions.completed_at；保留时刻及微秒，无损统一UTC格式。 |
## weapon_configs

| 原attribute | 去向与条件 |
|---|---|
| `config_version_id` | config_versions.id：五个同版本行合并；缺行/重复阻断。 |
| `sample_fps` | config_versions.weapon_sample_fps |
| `confidence_threshold` | config_versions.weapon_confidence_threshold |
| `n_required` | config_versions.weapon_n_required |
| `m_window` | config_versions.weapon_m_window |
| `cooldown_seconds` | config_versions.weapon_cooldown_seconds |
| `nms_candidate_floor` | config_versions.weapon_nms_candidate_floor |
| `nms_iou_threshold` | config_versions.weapon_nms_iou_threshold |
| `class_thresholds_json` | config_versions.weapon_class_thresholds_json |
| `incident_merge_gap_seconds` | config_versions.weapon_merge_gap_seconds |

共44张源表、339个源属性，均有迁移/派生/审计去向。

新增值的来源也必须明确：record_origin/validation_profile/is_legacy依据原表和已验证的导入事实；message_kind根据原event_key与phase/动作关联，无法证明用legacy；visibility_policy依原recipient集合为空或非空；历史send-time用户和description配置未知保持NULL；缺失prompt内容用metadata_only；独立旧delivery.updated_at保持NULL，不能捏造。
