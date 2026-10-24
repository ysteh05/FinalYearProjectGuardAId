"""Query-local projections for API contracts, never persisted compatibility tables.

Writes use physical v15 entities. These named CTEs retain existing DTO names and
derive coarse alert status from the single workflow authority.
"""
import re

PROJECTIONS = {
 'incident_state': "SELECT a.*, CASE workflow_status WHEN 'awaiting_confirmation' THEN 'open' WHEN 'resolved' THEN 'resolved' WHEN 'dismissed' THEN 'dismissed' WHEN 'legacy_escalated' THEN 'escalated' ELSE 'in_review' END AS status FROM alerts a",
 'workflow_state': 'SELECT id AS alert_id,workflow_status,handler_user_id,active_phase_id,broadcast_role_snapshot_json AS broadcast_role_ids_json,revision,workflow_updated_at AS updated_at FROM alerts',
 'workflow_history': "SELECT rowid AS rowid,id,alert_id,user_id AS actor_user_id,action AS event_type,note AS comment,broadcast_role_snapshot_json AS broadcast_role_ids_json,idempotency_key,created_at FROM alert_actions WHERE record_origin='workflow'",
 'queue_messages': 'SELECT o.*,n.event_key,n.data_payload_json FROM notification_outbox o JOIN notifications n ON n.id=o.notification_id',
 'queued_users': 'SELECT o.id AS outbox_id,r.user_id FROM notification_recipients r JOIN notification_outbox o ON o.notification_id=r.notification_id WHERE r.queued_target=1',
 'device_results': 'SELECT o.id AS outbox_id,d.* FROM notification_deliveries d JOIN notification_outbox o ON o.notification_id=d.notification_id',
 'review_tasks': "SELECT r.rowid AS rowid,r.*,r.detection_event_id AS detection_event_key,m.version AS model_version,p.version AS prompt_version,c.version_number AS config_version FROM borderline_reviews r LEFT JOIN model_versions m ON m.id=r.model_version_id LEFT JOIN prompt_versions p ON p.id=r.prompt_version_id LEFT JOIN config_versions c ON c.id=r.config_version_id WHERE r.record_origin='task'",
 'role_preferences': 'SELECT id AS role_id,borderline_default_enabled AS default_enabled,borderline_updated_by_user_id AS updated_by_user_id,borderline_updated_at AS updated_at FROM roles',
 'personal_preferences': 'SELECT id AS user_id,borderline_enabled AS enabled,borderline_source AS setting_source,borderline_updated_by_user_id AS updated_by_user_id,borderline_updated_at AS updated_at FROM users WHERE borderline_enabled IS NOT NULL',
 'preference_version': "SELECT id,borderline_revision AS revision FROM mobile_settings WHERE id='global'",
 'mobile_preferences': 'SELECT s.*,(SELECT json_group_array(id) FROM (SELECT id FROM roles WHERE compat_borderline_selected=1 ORDER BY id)) AS borderline_recipient_roles_json FROM mobile_settings s',
 'reminder_timers': 'SELECT p.*,(SELECT json_group_array(role_id) FROM (SELECT role_id FROM notification_phase_roles WHERE phase_id=p.id ORDER BY role_id)) AS broadcast_role_ids_json FROM notification_phases p',
 'stage_claims': 'SELECT * FROM notification_phase_dispatches',
 'user_reminders': 'SELECT a.*,p.alert_id,o.id AS outbox_id FROM notification_phase_audience a JOIN notification_phases p ON p.id=a.phase_id LEFT JOIN notification_outbox o ON o.notification_id=a.notification_id',
 'legacy_timers': "SELECT alert_id,source_notification_id AS initial_notification_id,first_after_seconds AS same_audience_repeat_after_seconds,all_after_seconds AS all_users_escalation_after_seconds,started_at AS initial_notified_at,first_due_at AS same_audience_repeat_due_at,all_due_at AS all_users_escalation_due_at,id AS phase_id FROM notification_phases p WHERE phase_kind='awaiting_decision' AND id=(SELECT q.id FROM notification_phases q WHERE q.alert_id=p.alert_id AND q.phase_kind='awaiting_decision' ORDER BY (q.phase_status='active') DESC,q.started_at DESC,q.id LIMIT 1)",
 'legacy_stages': 'SELECT p.alert_id,d.* FROM notification_phase_dispatches d JOIN notification_phases p ON p.id=d.phase_id',
 'reminder_policy': 'SELECT id AS config_version_id,same_audience_repeat_after_seconds,all_users_escalation_after_seconds FROM config_versions',
 'vlm_policy': 'SELECT c.id AS config_version_id,c.vlm_model_id AS model_id,c.vlm_quantization AS quantization,c.vlm_input_fps AS input_fps,c.vlm_video_token_budget AS video_token_budget,c.vlm_max_output_tokens AS max_output_tokens,c.vlm_scene_description_frames AS scene_description_frames,c.vlm_borderline_review_enabled AS borderline_review_enabled,c.vlm_scene_description_enabled AS scene_description_enabled,b.version AS binary_prompt_version,s.version AS scene_prompt_version FROM config_versions c LEFT JOIN prompt_versions b ON b.id=c.binary_prompt_version_id LEFT JOIN prompt_versions s ON s.id=c.scene_prompt_version_id',
}
for name,prefix,fields in [
 ('violence_policy','violence_',['segment_seconds','stride_seconds','detector_input_frames','sensitive_threshold','moderate_threshold','certain_threshold','cooldown_seconds','incident_merge_gap_seconds']),
 ('weapon_policy','weapon_',['sample_fps','confidence_threshold','nms_candidate_floor','nms_iou_threshold','class_thresholds_json','n_required','m_window','cooldown_seconds','incident_merge_gap_seconds'])]:
    PROJECTIONS[name]='SELECT id AS config_version_id,'+','.join(prefix+('merge_gap_seconds' if field=='incident_merge_gap_seconds' else field)+' AS '+field for field in fields)+' FROM config_versions'

_NAMES=re.compile(r'\b('+'|'.join(PROJECTIONS)+r')\b')

def read_sql(sql, *, projections=None):
    """Attach only the named read projections used by a repository statement."""
    projections = PROJECTIONS if projections is None else projections
    names=set(_NAMES.findall(sql))
    if not names:return sql
    return 'WITH '+','.join(name+' AS ('+projections[name]+')' for name in sorted(names))+' '+sql
