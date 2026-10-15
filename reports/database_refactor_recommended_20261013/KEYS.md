# 31表PK/FK速查

完整值域、NULL/默认值、CHECK、唯一索引和跨表trigger请配合DESIGN.md及SQL阅读。复合FK中某个成员为NULL时，SQLite不检查该组外键；任务/阶段等条件必填规则另有CHECK。

| Entity | Primary key | Foreign key / ON DELETE |
|---|---|---|
| `users` | `id` | `(borderline_updated_by_user_id) → users(id) / RESTRICT` |
| `roles` | `id` | `(borderline_updated_by_user_id) → users(id) / RESTRICT` |
| `user_roles` | `user_id, role_id` | `(user_id) → users(id) / CASCADE`<br>`(role_id) → roles(id) / RESTRICT`<br>`(assigned_by_user_id) → users(id) / RESTRICT` |
| `devices` | `id` | `(user_id) → users(id) / CASCADE` |
| `locations` | `id` | `无FK` |
| `cameras` | `id` | `(location_id) → locations(id) / RESTRICT` |
| `media_files` | `id` | `无FK` |
| `camera_sources` | `id` | `(camera_id) → cameras(id) / CASCADE`<br>`(media_file_id) → media_files(id) / RESTRICT` |
| `model_versions` | `id` | `无FK` |
| `prompt_versions` | `id` | `无FK` |
| `config_versions` | `id` | `(created_by_user_id) → users(id) / RESTRICT`<br>`(binary_prompt_version_id) → prompt_versions(id) / RESTRICT`<br>`(scene_prompt_version_id) → prompt_versions(id) / RESTRICT` |
| `alerts` | `id` | `(camera_id) → cameras(id) / RESTRICT`<br>`(opening_config_version_id) → config_versions(id) / RESTRICT`<br>`(opening_model_version_id) → model_versions(id) / RESTRICT`<br>`(binary_prompt_version_id) → prompt_versions(id) / RESTRICT`<br>`(handler_user_id) → users(id) / RESTRICT`<br>`(active_phase_id,id) → notification_phases(id,alert_id) / RESTRICT DEFERRABLE INITIALLY DEFERRED` |
| `detection_events` | `id` | `(alert_id) → alerts(id) / CASCADE`<br>`(model_version_id) → model_versions(id) / RESTRICT`<br>`(config_version_id) → config_versions(id) / RESTRICT` |
| `borderline_reviews` | `id` | `(camera_id) → cameras(id) / RESTRICT`<br>`(alert_id,camera_id) → alerts(id,camera_id) / RESTRICT`<br>`(detection_event_id) → detection_events(id) / RESTRICT`<br>`(model_version_id) → model_versions(id) / RESTRICT`<br>`(prompt_version_id) → prompt_versions(id) / RESTRICT`<br>`(config_version_id) → config_versions(id) / RESTRICT` |
| `evidence_records` | `id` | `(alert_id) → alerts(id) / CASCADE`<br>`(media_file_id) → media_files(id) / RESTRICT` |
| `vlm_descriptions` | `id` | `(alert_id) → alerts(id) / CASCADE`<br>`(model_version_id) → model_versions(id) / RESTRICT`<br>`(prompt_version_id) → prompt_versions(id) / RESTRICT`<br>`(config_version_id) → config_versions(id) / RESTRICT` |
| `alert_actions` | `id` | `(alert_id) → alerts(id) / RESTRICT`<br>`(user_id) → users(id) / RESTRICT` |
| `alert_reads` | `user_id, alert_id` | `(user_id) → users(id) / CASCADE`<br>`(alert_id) → alerts(id) / CASCADE` |
| `notifications` | `id` | `(alert_id) → alerts(id) / CASCADE`<br>`(phase_id,alert_id) → notification_phases(id,alert_id) / RESTRICT DEFERRABLE INITIALLY DEFERRED` |
| `notification_outbox` | `id` | `(notification_id) → notifications(id) / CASCADE` |
| `notification_recipients` | `id` | `(notification_id) → notifications(id) / CASCADE`<br>`(user_id) → users(id) / CASCADE`<br>`(role_id) → roles(id) / CASCADE` |
| `notification_deliveries` | `id` | `(notification_id) → notifications(id) / CASCADE`<br>`(device_id) → devices(id) / RESTRICT`<br>`(recipient_user_id) → users(id) / RESTRICT` |
| `notification_reads` | `notification_id, user_id` | `(notification_id) → notifications(id) / CASCADE`<br>`(user_id) → users(id) / CASCADE` |
| `alert_notification_audience` | `alert_id, user_id` | `(alert_id) → alerts(id) / CASCADE`<br>`(user_id) → users(id) / CASCADE`<br>`(first_notification_id) → notifications(id) / SET NULL` |
| `notification_phases` | `id` | `(alert_id) → alerts(id) / CASCADE`<br>`(initiated_by_user_id) → users(id) / RESTRICT`<br>`(source_notification_id) → notifications(id) / RESTRICT` |
| `notification_phase_roles` | `phase_id, role_id` | `(phase_id) → notification_phases(id) / CASCADE`<br>`(role_id) → roles(id) / CASCADE` |
| `notification_phase_dispatches` | `phase_id, stage` | `(phase_id) → notification_phases(id) / CASCADE`<br>`(notification_id) → notifications(id) / SET NULL` |
| `notification_phase_audience` | `phase_id, user_id, stage` | `(phase_id) → notification_phases(id) / CASCADE`<br>`(user_id) → users(id) / CASCADE`<br>`(notification_id) → notifications(id) / SET NULL` |
| `mobile_settings` | `id` | `(updated_by_user_id) → users(id) / RESTRICT` |
| `audit_logs` | `id` | `(user_id) → users(id) / SET NULL` |
| `schema_migrations` | `version` | `无FK` |

特别关系：一个alert最多一个active phase是partial UNIQUE；同phase-stage只有一个主提醒消息，但允许late-repeat；notification_recipients两个可选FK要求XOR且分别partial UNIQUE；send-time recipient FK不与可转移设备owner组成永久复合FK。schema_migrations独立基础设施账表；实体图只显示PK/FK列，全部其他属性见字典。
