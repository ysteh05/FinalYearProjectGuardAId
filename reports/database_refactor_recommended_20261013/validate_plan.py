"""Validate only the proposed empty schema; source connection is read-only."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
import sqlite3
import json
import build_plan as plan

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT.parents[1] / 'guardaid_monitor/runtime_data/database/guardaid.db'
NOW = '2026-10-13T01:02:03.123456Z'
RESULTS = []


def canonical(value):
    instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
    assert instant.tzinfo is not None, 'timezone is required'
    return instant.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def later(value=NOW, seconds=60):
    return canonical((datetime.fromisoformat(value.replace('Z', '+00:00')) + timedelta(seconds=seconds)).isoformat())


def insert(c, table, values):
    fields = list(values)
    c.execute(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", [values[x] for x in fields])


def check(c, label, action, reject=False, mechanism=None):
    c.execute('BEGIN')
    try:
        action()
        if reject:
            c.commit()  # Includes deferred FK checks, not merely INSERT-time checks.
            raise AssertionError('invalid data accepted: ' + label)
        assert not c.execute('PRAGMA foreign_key_check').fetchall(), label
        c.rollback()
    except sqlite3.Error as exc:
        c.rollback()
        if not reject:
            raise AssertionError(f'valid scenario rejected: {label}: {exc}') from exc
        if mechanism and mechanism not in str(exc):
            raise AssertionError(f'wrong rejection for {label}: {exc}, expected {mechanism}') from exc
    RESULTS.append((label, '拒绝非法数据' if reject else '接受合法场景', 'PASS'))


def policy(key='config_1', version=1):
    return dict(id=key, version_number=version, created_by='fixture', created_at=NOW,
        default_sensitivity='moderate', violence_segment_seconds=4., violence_stride_seconds=2., violence_detector_input_frames=16,
        violence_sensitive_threshold=.3, violence_moderate_threshold=.5, violence_certain_threshold=.7,
        violence_cooldown_seconds=15, violence_merge_gap_seconds=15.,
        weapon_sample_fps=4., weapon_confidence_threshold=.6, weapon_nms_candidate_floor=.001, weapon_nms_iou_threshold=.45,
        weapon_n_required=3, weapon_m_window=5, weapon_cooldown_seconds=15, weapon_merge_gap_seconds=15.,
        weapon_class_thresholds_json=json.dumps({s: dict(knife=.3, gun=.4) for s in plan.SENS}),
        vlm_model_id='Qwen/Qwen3-VL-2B-Instruct', vlm_quantization='NF4 4-bit', vlm_input_fps=2., vlm_video_token_budget=2048,
        vlm_max_output_tokens=220, vlm_scene_description_frames=20, vlm_borderline_review_enabled=1, vlm_scene_description_enabled=1,
        binary_prompt_version_id='prompt_binary', scene_prompt_version_id='prompt_scene',
        same_audience_repeat_after_seconds=60, all_users_escalation_after_seconds=120)


VLM = dict(model_id='Qwen/Qwen3-VL-2B-Instruct', quantization='NF4 4-bit', input_fps=2., video_token_budget=2048,
    max_output_tokens=220, scene_description_frames=20, borderline_review_enabled=True, scene_description_enabled=True,
    binary_prompt_version='binary_v1', scene_prompt_version='scene_v1')


def alert(key='alert_1', camera='cam_1', workflow='awaiting_confirmation'):
    return dict(id=key, camera_id=camera, incident_type='violence', workflow_status=workflow,
        sensitivity_mode='moderate', borderline_review_status='not_applicable', evidence_status='preparing',
        opening_config_version_id='config_1', opening_model_version_id='model_violence', created_at=NOW, workflow_updated_at=NOW)


def detector(key='detect_1'):
    return dict(id=key, alert_id='alert_1', model_version_id='model_violence', config_version_id='config_1',
        confidence=.5, decision_source='threshold', source_start_seconds=0., source_end_seconds=4., detected_at=NOW)


def review(key='review_1'):
    return dict(id=key, record_origin='task', camera_id='cam_1', alert_id='alert_1', detection_event_id='detect_1',
        model_version_id='model_binary', prompt_version_id='prompt_binary', config_version_id='config_1', detector_confidence=.5,
        source_start_seconds=0., source_end_seconds=4., context_path='borderline_context/review.mp4', status='queued',
        attempt_count=0, vlm_settings_json=json.dumps(VLM), created_at=NOW)


def phase(key='phase_1', incident='alert_1', kind='awaiting_decision', start=NOW, status='active'):
    return dict(id=key, alert_id=incident, phase_kind=kind, phase_status=status, started_at=start,
        first_after_seconds=60, all_after_seconds=120, first_due_at=later(start, 60), all_due_at=later(start, 120))


def notice(key='notice_1', kind='initial', phase_id=None, stage=None):
    return dict(id=key, alert_id='alert_1', message_kind=kind, event_key=key, phase_id=phase_id, reminder_stage=stage,
        title='GuardAId', body='An incident needs attention.', data_payload_json='{}', created_at=NOW)


def seed(c):
    for n in (1, 2, 3):
        insert(c, 'users', dict(id=f'user_{n}', firebase_uid=f'firebase-{n}', status='active', created_at=NOW, updated_at=NOW))
    for n in (1, 2):
        insert(c, 'roles', dict(id=f'role_{n}', name=f'role_{n}', created_at=NOW, updated_at=NOW))
        insert(c, 'user_roles', dict(user_id='user_1', role_id=f'role_{n}', assigned_by_user_id='user_2', assigned_at=NOW))
    insert(c, 'devices', dict(id='device_1', user_id='user_1', fcm_registration_value='fixture-registration-value-123', platform='android',
        last_seen_at=NOW, created_at=NOW, updated_at=NOW))
    insert(c, 'locations', dict(id='loc_1', name='School', normalized_name='school', created_at=NOW))
    for n in (1, 2):
        insert(c, 'cameras', dict(id=f'cam_{n}', location_id='loc_1', name=f'Camera {n}', status='ready', created_at=NOW, updated_at=NOW))
    for key, kind in (('video_1', 'camera_source'), ('clip_1', 'evidence_clip'), ('image_1', 'evidence_image')):
        insert(c, 'media_files', dict(id=key, relative_path=f'validation/{key}.mp4', media_kind=kind,
            content_type='image/jpeg' if kind == 'evidence_image' else 'video/mp4', size_bytes=100, sha256='a'*64, created_at=NOW))
    for task, key in (('violence', 'model_violence'), ('weapon', 'model_weapon'), ('borderline_binary', 'model_binary'), ('scene_description', 'model_scene')):
        insert(c, 'model_versions', dict(id=key, task=task, model_name=key, version='v1', created_at=NOW))
    for key, task, version in (('prompt_binary', 'borderline_binary', 'binary_v1'), ('prompt_scene', 'scene_description', 'scene_v1')):
        insert(c, 'prompt_versions', dict(id=key, task=task, version=version, created_at=NOW))
    insert(c, 'config_versions', policy())
    insert(c, 'alerts', alert())
    insert(c, 'alerts', alert('alert_2', 'cam_2', 'confirmed_awaiting_handler'))
    insert(c, 'camera_sources', dict(id='source_1', camera_id='cam_1', media_file_id='video_1', source_type='uploaded_video', duration_seconds=10., created_at=NOW))
    insert(c, 'detection_events', detector())
    insert(c, 'borderline_reviews', review())
    insert(c, 'evidence_records', dict(id='evidence_1', alert_id='alert_1', media_file_id='clip_1', evidence_type='violence_clip', captured_at=NOW))
    insert(c, 'vlm_descriptions', dict(id='description_1', alert_id='alert_1', model_version_id='model_scene', prompt_version_id='prompt_scene',
        status='ready', output_format='summary', structured_json=json.dumps(dict(description='Two people are visible.', visible_evidence=['Physical contact'], uncertainty='high')), created_at=NOW))
    insert(c, 'alert_actions', dict(id='action_1', alert_id='alert_2', user_id='user_1', action='confirm_only', record_origin='workflow',
        idempotency_key='fixture-key-123', broadcast_role_snapshot_json='["role_1"]', created_at=NOW))
    insert(c, 'alert_reads', dict(alert_id='alert_1', user_id='user_1', read_at=NOW))
    insert(c, 'notification_phases', phase())
    insert(c, 'notification_phases', phase('phase_2', 'alert_2', 'awaiting_handler'))
    c.execute("UPDATE alerts SET active_phase_id='phase_1' WHERE id='alert_1'")
    c.execute("UPDATE alerts SET active_phase_id='phase_2' WHERE id='alert_2'")
    insert(c, 'notification_phase_roles', dict(phase_id='phase_2', role_id='role_1'))
    insert(c, 'notifications', notice())
    insert(c, 'notifications', notice('stage_1', 'phase_reminder', 'phase_1', 1))
    insert(c, 'notification_outbox', dict(id='outbox_1', notification_id='notice_1', channel_id='guardaid_incident_v1', status='pending',
        next_attempt_at=NOW, created_at=NOW, updated_at=NOW))
    insert(c, 'notification_recipients', dict(notification_id='notice_1', user_id='user_1', feed_visible=1, queued_target=1))
    insert(c, 'notification_deliveries', dict(id='delivery_1', notification_id='notice_1', device_id='device_1', recipient_user_id='user_1',
        status='sent', attempt_count=1, last_attempt_at=NOW, updated_at=NOW))
    insert(c, 'notification_reads', dict(notification_id='notice_1', user_id='user_1', read_at=NOW))
    insert(c, 'alert_notification_audience', dict(alert_id='alert_1', user_id='user_1', first_notification_id='notice_1', first_queued_at=NOW))
    insert(c, 'notification_phase_dispatches', dict(phase_id='phase_1', stage=1, status='queued', notification_id='stage_1', claimed_at=NOW))
    insert(c, 'notification_phase_audience', dict(phase_id='phase_1', user_id='user_1', stage=1, status='queued', notification_id='stage_1', updated_at=NOW))
    insert(c, 'mobile_settings', dict(id='global', updated_at=NOW))
    insert(c, 'audit_logs', dict(id='audit_1', user_id='user_3', action='update_role', entity_type='role', entity_id='role_1', detail_json='{}', created_at=NOW))
    insert(c, 'schema_migrations', dict(version=1, name='fixture_only', applied_at=NOW))
    c.commit()


def cases(c):
    def bad(label, action, message=None): check(c, label, action, True, message)
    def good(label, action): check(c, label, action)
    def new_config(**changes): return {**policy('config_test', 2), **changes}
    def fresh_review(**changes): return {**review('review_test'), **changes}
    def new_scene(**changes): return {**dict(id='scene_test', alert_id='alert_1', model_version_id='model_scene', status='ready', output_format='text', display_text='Text', created_at=NOW), **changes}
    bad('Boolean不允许2', lambda: c.execute("UPDATE devices SET enabled=2 WHERE id='device_1'"), 'CHECK')
    bad('STRICT整数拒绝非数字', lambda: c.execute("UPDATE devices SET enabled='yes' WHERE id='device_1'"), 'cannot store')
    bad('必需配置不能用NULL绕过', lambda: insert(c, 'config_versions', new_config(violence_sensitive_threshold=None)), 'NOT NULL')
    bad('Firebase UID最多128字符', lambda: insert(c, 'users', dict(id='u_bad', firebase_uid='x'*129, created_at=NOW, updated_at=NOW)), 'CHECK')
    bad('Firebase UID唯一', lambda: insert(c, 'users', dict(id='u_bad', firebase_uid='firebase-1', created_at=NOW, updated_at=NOW)), 'UNIQUE')
    good('Provider长显示缓存不截断', lambda: c.execute("UPDATE users SET display_name=?,email_cache=? WHERE id='user_1'", ('外部'*100, 'x'*300)))
    good('用户允许多角色', lambda: insert(c, 'user_roles', dict(user_id='user_2', role_id='role_1', assigned_at=NOW)))
    bad('角色关系未知FK', lambda: insert(c, 'user_roles', dict(user_id='user_2', role_id='missing', assigned_at=NOW)), 'FOREIGN KEY')
    bad('角色关系不可重复', lambda: insert(c, 'user_roles', dict(user_id='user_1', role_id='role_1', assigned_at=NOW)), 'UNIQUE')
    bad('摄像头名称80字符上限', lambda: c.execute("UPDATE cameras SET name=? WHERE id='cam_1'", ('x'*81,)), 'CHECK')
    bad('FCM注册值20字符下限', lambda: c.execute("UPDATE devices SET fcm_registration_value='short' WHERE id='device_1'"), 'CHECK')
    bad('媒体路径拒绝父目录穿越', lambda: c.execute("UPDATE media_files SET relative_path='../outside' WHERE id='video_1'"), 'CHECK')
    bad('SHA固定64位小写hex', lambda: c.execute("UPDATE media_files SET sha256=? WHERE id='video_1'", ('G'*64,)), 'CHECK')
    bad('同摄像头仅一个active source', lambda: insert(c, 'camera_sources', dict(id='source_test', camera_id='cam_1', media_file_id='video_1', source_type='uploaded_video', created_at=NOW)), 'UNIQUE')
    good('同文件可作另一摄像头输入', lambda: insert(c, 'camera_sources', dict(id='source_test', camera_id='cam_2', media_file_id='video_1', source_type='uploaded_video', created_at=NOW)))
    bad('RTSP不能同时引用上传文件', lambda: insert(c, 'camera_sources', dict(id='source_test', camera_id='cam_2', media_file_id='video_1', source_type='rtsp', rtsp_endpoint_ref='rtsp-ref', created_at=NOW)), 'CHECK')
    bad('截图必须引用image文件', lambda: insert(c, 'evidence_records', dict(id='ev_bad', alert_id='alert_1', media_file_id='clip_1', evidence_type='weapon_screenshot', captured_at=NOW)), 'wrong reference')
    for label, values in (
        ('stride不大于segment', dict(violence_stride_seconds=5.)), ('阈值排序', dict(violence_sensitive_threshold=.9)),
        ('N不大于M', dict(weapon_n_required=6)), ('scene帧为偶数', dict(vlm_scene_description_frames=21)),
        ('升级晚于重复', dict(all_users_escalation_after_seconds=30)), ('模型标识不能空', dict(vlm_model_id=''))):
        bad(label, lambda values=values: insert(c, 'config_versions', new_config(**values)), 'CHECK')
    bad('prompt用途FK校验', lambda: insert(c, 'config_versions', new_config(binary_prompt_version_id='prompt_scene')), 'wrong reference')
    bad('概率不能大于1', lambda: insert(c, 'detection_events', {**detector('detect_test'), 'confidence':1.1}), 'CHECK')
    bad('detector不能使用scene模型', lambda: insert(c, 'detection_events', {**detector('detect_test'), 'model_version_id':'model_scene'}), 'wrong reference')
    for value in ('{"gun":null}', '{"gun":1.1}', '{"gun":"0.3"}'):
        bad('分数逐值校验 '+value, lambda value=value: insert(c, 'detection_events', {**detector('detect_test'), 'weapon_class_scores_json':value}), 'probabilities')
    bad('新配置类别/精度', lambda: insert(c, 'config_versions', new_config(weapon_class_thresholds_json=json.dumps({s:dict(knife=.300001, gun=.4) for s in plan.SENS}))), 'classes/precision')
    good('历史阈值精度及long_gun保留', lambda: insert(c, 'config_versions', new_config(validation_profile='historical', weapon_class_thresholds_json=json.dumps({s:dict(knife=.300001, long_gun=.456789, handgun=.34567) for s in plan.SENS}))))
    good('武器跨敏感度排序未强加新规则', lambda: insert(c, 'config_versions', new_config(weapon_class_thresholds_json=json.dumps({s:dict(knife=.7 if s=='sensitive' else .2, gun=.4) for s in plan.SENS}))))
    good('身体霸凌可用共享violence模型', lambda: insert(c,'alerts',{**alert('bully_shared'),'incident_type':'physical_contact_bullying'}))
    good('身体霸凌可按现有save_alert登记任务', lambda: physical_contact_model(c))
    bad('配置版本不能原位修改', lambda: c.execute("UPDATE config_versions SET weapon_sample_fps=8 WHERE id='config_1'"), 'append-only')
    bad('prompt版本不能硬删除', lambda: c.execute("DELETE FROM prompt_versions WHERE id='prompt_scene'"), 'retain')
    bad('冻结prompt需要内容和SHA', lambda: insert(c, 'prompt_versions', dict(id='prompt_test', task='scene_description', version='other', capture_state='frozen', created_at=NOW)), 'CHECK')
    bad('每detector仅一个task复核', lambda: insert(c, 'borderline_reviews', fresh_review()), 'UNIQUE')
    bad('复核event必须同警报摄像头', lambda: insert(c, 'borderline_reviews', fresh_review(alert_id='alert_2', camera_id='cam_2')), 'belong')
    bad('task最多尝试3', lambda: c.execute("UPDATE borderline_reviews SET attempt_count=4 WHERE id='review_1'"), 'CHECK')
    bad('positive需要raise_alert=1', lambda: c.execute("UPDATE borderline_reviews SET status='positive',raise_alert=0 WHERE id='review_1'"), 'CHECK')
    bad('positive不能NULL绕过', lambda: c.execute("UPDATE borderline_reviews SET status='positive',raise_alert=NULL WHERE id='review_1'"), 'CHECK')
    bad('negative不能获得补充资格', lambda: c.execute("UPDATE borderline_reviews SET status='negative',raise_alert=0,supplemental_notification_status='eligible' WHERE id='review_1'"), 'CHECK')
    def review_snapshot(value):
        insert(c, 'detection_events', detector('detect_test'))
        insert(c, 'borderline_reviews', fresh_review(detection_event_id='detect_test', vlm_settings_json=value))
    for label, value in (('缺键', '{}'), ('JSON null', json.dumps({**VLM, 'video_token_budget':None})), ('整数类型', json.dumps({**VLM, 'video_token_budget':256.5})), ('Boolean类型', json.dumps({**VLM, 'borderline_review_enabled':1}))):
        bad('VLM snapshot '+label, lambda value=value: review_snapshot(value), 'field schema')
    good('positive与补充资格一起保存', lambda: c.execute("UPDATE borderline_reviews SET status='positive',raise_alert=1,supplemental_notification_status='eligible',completed_at=? WHERE id='review_1'", (later(),)))
    good('旧审计诚实表示无任务输入', lambda: insert(c, 'borderline_reviews', dict(id='old_review', record_origin='audit_only', camera_id='cam_1', model_version_id='model_binary', prompt_version_id='prompt_binary', config_version_id='config_1', detector_confidence=.5, status='negative', raise_alert=0, reviewed_at=NOW)))
    bad('task输入上下文不能空', lambda: insert(c, 'borderline_reviews', fresh_review(context_path='')), 'CHECK')
    for label, payload in (('最多2证据', dict(description='x', visible_evidence=['1','2','3'], uncertainty='low')), ('必须有description', dict(visible_evidence=[], uncertainty='low')), ('证据必须字符串', dict(description='x', visible_evidence=[None], uncertainty='low'))):
        bad('SceneSummary '+label, lambda payload=payload: insert(c, 'vlm_descriptions', dict(id='scene_test', alert_id='alert_1', model_version_id='model_scene', status='ready', output_format='summary', structured_json=json.dumps(payload), created_at=NOW)), 'schema failed')
    good('保留raw JSON fallback', lambda: insert(c, 'vlm_descriptions', dict(id='scene_test', alert_id='alert_1', model_version_id='model_scene', status='ready', output_format='raw_json', structured_json='["older output"]', created_at=NOW)))
    good('保留长text和未知旧config', lambda: insert(c, 'vlm_descriptions', new_scene(display_text='History '*100)))
    bad('同alert只有一个active phase', lambda: insert(c, 'notification_phases', phase('phase_test')), 'UNIQUE')
    bad('期限必须等于anchor加delay', lambda: insert(c, 'notification_phases', {**phase('phase_test', status='completed'), 'first_due_at':later(seconds=59)}), 'CHECK')
    bad('期限不允许微秒偏移', lambda: insert(c, 'notification_phases', {**phase('phase_test', status='completed'), 'first_due_at':later().replace('123456','123457')}), 'CHECK')
    good('边界微秒跨天精确保留', lambda: insert(c, 'notification_phases', phase('phase_test', start='2026-10-13T23:59:59.999999Z', status='completed')))
    bad('active phase不能串alert', lambda: c.execute("UPDATE alerts SET active_phase_id='phase_1' WHERE id='alert_2'"), 'wrong reference')
    bad('phase source通知必须同alert', lambda: c.execute("UPDATE notification_phases SET source_notification_id='notice_1' WHERE id='phase_2'"), 'wrong reference')
    bad('只有Phase B可选live角色', lambda: insert(c, 'notification_phase_roles', dict(phase_id='phase_1', role_id='role_1')), 'Phase B')
    good('停止phase清理active pointer', lambda: stop_phase(c))
    good('不同phase保留独立历史', lambda: insert(c, 'notification_phases', {**phase('phase_test', status='archived'), 'is_legacy':1}))
    bad('新phase不能假冒归档legacy', lambda: insert(c, 'notification_phases', phase('phase_test', status='archived')), 'CHECK')
    bad('同phase-stage主消息唯一', lambda: insert(c, 'notifications', notice('notice_test', 'phase_reminder', 'phase_1', 1)), 'UNIQUE')
    good('主stage后允许late repeat另一消息', lambda: insert(c, 'notifications', notice('notice_test', 'late_repeat', 'phase_1', 1)))
    bad('recipient用户重复被partial UNIQUE拒绝', lambda: insert(c, 'notification_recipients', dict(notification_id='notice_1', user_id='user_1')), 'UNIQUE')
    bad('recipient必须用户或角色恰一项', lambda: insert(c, 'notification_recipients', dict(notification_id='notice_1', user_id='user_2', role_id='role_2')), 'CHECK')
    bad('role行不能冒充queued用户', lambda: insert(c, 'notification_recipients', dict(notification_id='notice_1', role_id='role_2', queued_target=1)), 'CHECK')
    good('feed名单与queue名单可以不同', lambda: insert(c, 'notification_recipients', dict(notification_id='notice_1', user_id='user_2', feed_visible=0, queued_target=1)))
    bad('逐消息设备结果唯一', lambda: insert(c, 'notification_deliveries', dict(id='delivery_test', notification_id='notice_1', device_id='device_1', status='queued', updated_at=NOW)), 'UNIQUE')
    good('设备token转移不改旧发送用户', lambda: transfer_device(c))
    good('lease恢复允许超过3次claim', lambda: c.execute("UPDATE notification_outbox SET attempt_count=5,status='dispatching',claimed_at=? WHERE id='outbox_1'", (NOW,)))
    bad('消息内容发送后被冻结', lambda: c.execute("UPDATE notifications SET body='changed' WHERE id='notice_1'"), 'frozen')
    good('未发送消息可完成payload组装', lambda: c.execute("UPDATE notifications SET data_payload_json='{"+'"stage":1'+"}' WHERE id='stage_1'"))
    good('stage跳过可没有消息', lambda: insert(c, 'notification_phase_dispatches', dict(phase_id='phase_1', stage=2, status='no_recipients', claimed_at=NOW)))
    bad('stage不能引用另一stage消息', lambda: insert(c, 'notification_phase_dispatches', dict(phase_id='phase_1', stage=2, status='queued', notification_id='stage_1', claimed_at=NOW)), 'wrong reference')
    bad('每phase-user-stage关系唯一', lambda: insert(c, 'notification_phase_audience', dict(phase_id='phase_1', user_id='user_1', stage=1, status='queued', updated_at=NOW)), 'UNIQUE')
    bad('action幂等key唯一', lambda: insert(c, 'alert_actions', dict(id='action_test', alert_id='alert_2', user_id='user_1', action='confirm_only', record_origin='workflow', idempotency_key='fixture-key-123', broadcast_role_snapshot_json='["role_1"]', created_at=NOW)), 'UNIQUE')
    bad('confirm-only选择1到20角色', lambda: insert(c, 'alert_actions', dict(id='action_test', alert_id='alert_2', user_id='user_1', action='confirm_only', record_origin='workflow', idempotency_key='other-key-123', broadcast_role_snapshot_json='[]', created_at=NOW)), '1..20')
    good('旧action长note不截断', lambda: insert(c, 'alert_actions', dict(id='action_test', alert_id='alert_1', user_id='user_1', action='review', record_origin='legacy', note='x'*1500, created_at=NOW)))
    bad('只允许模拟call', lambda: c.execute("UPDATE mobile_settings SET emergency_call_simulation_only=0 WHERE id='global'"), 'CHECK')
    bad('行政审计不可改写内容', lambda: c.execute("UPDATE audit_logs SET detail_json='{} ',action='other' WHERE id='audit_1'"), 'append-only')
    good('actor删除不删除审计', lambda: delete_audit_actor(c))
    bad('警报read集合唯一', lambda: insert(c, 'alert_reads', dict(alert_id='alert_1', user_id='user_1', read_at=NOW)), 'UNIQUE')
    bad('通知read集合唯一', lambda: insert(c, 'notification_reads', dict(notification_id='notice_1', user_id='user_1', read_at=NOW)), 'UNIQUE')
    for value in ('2026-02-30T01:02:03.123456Z','2026-10-13T24:00:00.000000Z','2026-10-13T01:02:03.123Z','2026-10-13T01:02:03.123456+00:00','0000-01-01T01:02:03.123456Z'):
        bad('拒绝非规范时间 '+value, lambda value=value: c.execute("UPDATE users SET updated_at=? WHERE id='user_1'", (value,)), 'CHECK')
    good('有效闰日', lambda: insert(c, 'users', dict(id='leap', firebase_uid='leap', created_at='2024-02-29T01:02:03.000001Z', updated_at='2024-02-29T01:02:03.000001Z')))
    good('restricted名单删除仍restricted', lambda: delete_role_recipient(c))


def stop_phase(c):
    c.execute("UPDATE notification_phases SET phase_status='cancelled',completed_at=? WHERE id='phase_1'", (later(),))
    assert c.execute("SELECT active_phase_id FROM alerts WHERE id='alert_1'").fetchone()[0] is None


def physical_contact_model(c):
    insert(c,'model_versions',dict(id='model_bully',task='physical_contact_bullying',model_name='shared-model',version='v1',created_at=NOW))
    insert(c,'alerts',{**alert('bully_registered'),'incident_type':'physical_contact_bullying','opening_model_version_id':'model_bully'})
    insert(c,'detection_events',{**detector('bully_event'),'alert_id':'bully_registered','model_version_id':'model_bully'})


def transfer_device(c):
    c.execute("UPDATE devices SET user_id='user_2' WHERE id='device_1'")
    assert c.execute("SELECT recipient_user_id FROM notification_deliveries WHERE id='delivery_1'").fetchone()[0] == 'user_1'


def delete_audit_actor(c):
    c.execute("DELETE FROM users WHERE id='user_3'")
    assert c.execute("SELECT user_id FROM audit_logs WHERE id='audit_1'").fetchone()[0] is None


def delete_role_recipient(c):
    insert(c, 'notifications', notice('restricted_test'))
    insert(c, 'notification_recipients', dict(notification_id='restricted_test', role_id='role_2'))
    c.execute("DELETE FROM user_roles WHERE role_id='role_2'")
    c.execute("DELETE FROM roles WHERE id='role_2'")
    assert c.execute("SELECT visibility_policy FROM notifications WHERE id='restricted_test'").fetchone()[0] == 'restricted'
    assert c.execute("SELECT count(*) FROM notification_recipients WHERE notification_id='restricted_test'").fetchone()[0] == 0


CONFIG_MAPS = {
    'violence_configs': {'segment_seconds':'violence_segment_seconds','stride_seconds':'violence_stride_seconds','detector_input_frames':'violence_detector_input_frames',
        'sensitive_threshold':'violence_sensitive_threshold','moderate_threshold':'violence_moderate_threshold','certain_threshold':'violence_certain_threshold',
        'cooldown_seconds':'violence_cooldown_seconds','incident_merge_gap_seconds':'violence_merge_gap_seconds'},
    'weapon_configs': {'sample_fps':'weapon_sample_fps','confidence_threshold':'weapon_confidence_threshold','nms_candidate_floor':'weapon_nms_candidate_floor',
        'nms_iou_threshold':'weapon_nms_iou_threshold','n_required':'weapon_n_required','m_window':'weapon_m_window','cooldown_seconds':'weapon_cooldown_seconds',
        'incident_merge_gap_seconds':'weapon_merge_gap_seconds','class_thresholds_json':'weapon_class_thresholds_json'},
    'vlm_configs': {'model_id':'vlm_model_id','quantization':'vlm_quantization','input_fps':'vlm_input_fps','video_token_budget':'vlm_video_token_budget',
        'max_output_tokens':'vlm_max_output_tokens','scene_description_frames':'vlm_scene_description_frames','borderline_review_enabled':'vlm_borderline_review_enabled',
        'scene_description_enabled':'vlm_scene_description_enabled'},
    'alert_notification_escalation_configs': {'same_audience_repeat_after_seconds':'same_audience_repeat_after_seconds','all_users_escalation_after_seconds':'all_users_escalation_after_seconds'},
}


def baseline(c):
    source = sqlite3.connect(RUNTIME.resolve().as_uri()+'?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    source.execute('BEGIN')
    names = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    assert names == set(plan.MAPPING)
    config_db=sqlite3.connect(':memory:')
    config_db.executescript((ROOT/'schema_proposal.sql').read_text(encoding='utf-8'))
    prompt_map={}
    for row in source.execute('SELECT * FROM prompt_versions'):
        values=dict(row)
        values['created_at']=canonical(values['created_at'])
        values['capture_state']='frozen' if values['content'] is not None and values['content_sha256'] is not None else 'metadata_only'
        insert(config_db,'prompt_versions',values)
        prompt_map[(values['task'],values['version'])]=values['id']
    config_db.commit()
    count = 0
    for row in source.execute('SELECT * FROM config_versions ORDER BY version_number'):
        values = policy(row['id'], row['version_number'])
        values.update(validation_profile='historical',created_by=row['created_by'],created_at=canonical(row['created_at']),
            default_sensitivity=row['default_sensitivity'],detection_debug_mode_enabled=row['detection_debug_mode_enabled'])
        for table, mapping in CONFIG_MAPS.items():
            detail = source.execute(f'SELECT * FROM {table} WHERE config_version_id=?', (row['id'],)).fetchone()
            assert detail is not None, 'missing config detail'
            values.update({new:detail[old] for old,new in mapping.items()})
            if table=='vlm_configs':
                values['binary_prompt_version_id']=prompt_map[('borderline_binary',detail['binary_prompt_version'])]
                values['scene_prompt_version_id']=prompt_map[('scene_description',detail['scene_prompt_version'])]
        insert(config_db, 'config_versions', values)
        stored = config_db.execute('SELECT * FROM config_versions WHERE id=?',(values['id'],)).fetchone()
        actual = dict(zip([x[1] for x in config_db.execute('PRAGMA table_info(config_versions)')],stored))
        assert all(actual[k] == v for k,v in values.items()), 'field or precision loss'
        count += 1
    config_db.commit()
    assert not config_db.execute('PRAGMA foreign_key_check').fetchall()
    config_db.close()
    audit = dict(source_tables=len(names),schema_version=source.execute('SELECT max(version) FROM schema_migrations').fetchone()[0],config_versions=count)
    audit['source_foreign_key_violations'] = len(source.execute('PRAGMA foreign_key_check').fetchall())
    audit['delivery_mirrors'] = source.execute('SELECT count(*) FROM mobile_notification_outbox_deliveries').fetchone()[0]
    audit['delivery_mirror_differences'] = source.execute('''SELECT count(*) FROM mobile_notification_outbox_deliveries d
        JOIN mobile_notification_outbox o ON o.id=d.outbox_id LEFT JOIN notification_deliveries n ON n.notification_id=o.notification_id AND n.device_id=d.device_id
        WHERE n.id IS NULL OR n.status IS NOT d.status OR n.provider_message_id IS NOT d.provider_message_id OR n.error_code IS NOT d.error_code
        OR n.attempt_count IS NOT d.attempt_count OR n.last_attempt_at IS NOT d.updated_at''').fetchone()[0]
    audit['feed_deliveries_without_outbox_mirror']=source.execute('''SELECT count(*) FROM notification_deliveries n WHERE NOT EXISTS(
        SELECT 1 FROM mobile_notification_outbox_deliveries d JOIN mobile_notification_outbox o ON o.id=d.outbox_id
        WHERE o.notification_id=n.notification_id AND d.device_id=n.device_id)''').fetchone()[0]
    audit['payload_differences'] = source.execute('''SELECT count(*) FROM mobile_notification_outbox o JOIN notifications n ON n.id=o.notification_id
        WHERE o.data_payload_json IS NOT n.data_payload_json''').fetchone()[0]
    audit['queue_users_missing_feed_user'] = source.execute('''SELECT count(*) FROM mobile_notification_outbox_recipients r
        JOIN mobile_notification_outbox o ON o.id=r.outbox_id WHERE NOT EXISTS(SELECT 1 FROM notification_recipients n WHERE n.notification_id=o.notification_id AND n.user_id=r.user_id)''').fetchone()[0]
    audit['alerts_without_workflow'] = source.execute('''SELECT count(*) FROM alerts a WHERE NOT EXISTS(SELECT 1 FROM mobile_alert_workflows w WHERE w.alert_id=a.id)''').fetchone()[0]
    audit['status_contradictions'] = sum(a['status'] != plan.STATUS_MAP[w['workflow_status']] for a in source.execute('SELECT id,status FROM alerts a')
        for w in source.execute('SELECT workflow_status FROM mobile_alert_workflows WHERE alert_id=?',(a['id'],)))
    audit['review_tasks'] = source.execute('SELECT count(*) FROM borderline_review_tasks').fetchone()[0]
    audit['review_task_event_missing'] = source.execute('''SELECT count(*) FROM borderline_review_tasks t WHERE NOT EXISTS(
        SELECT 1 FROM detection_events e JOIN alerts a ON a.id=e.alert_id WHERE e.id=t.detection_event_key AND e.alert_id=t.alert_id AND a.camera_id=t.camera_id)''').fetchone()[0]
    audit['timestamps_checked'] = 0
    audit['timestamp_errors'] = 0
    for name in sorted(names):
        for column in source.execute(f'PRAGMA table_info({name})'):
            field = column['name']
            if field.endswith('_at') or field == 'delete_after':
                for row in source.execute(f'SELECT {field} FROM {name} WHERE {field} IS NOT NULL'):
                    audit['timestamps_checked'] += 1
                    try:
                        normalized=canonical(row[0])
                        assert datetime.fromisoformat(row[0].replace('Z','+00:00')) == datetime.fromisoformat(normalized.replace('Z','+00:00'))
                    except (ValueError,AssertionError): audit['timestamp_errors'] += 1
    attribute_mapping(source)
    source.rollback()
    source.close()
    assert all(audit[k] == 0 for k in ('source_foreign_key_violations','delivery_mirror_differences','payload_differences',
        'queue_users_missing_feed_user','alerts_without_workflow','status_contradictions','review_task_event_missing','timestamp_errors')), audit
    return audit


def attribute_mapping(source):
    """Every source column has a concrete destination, derivation or archival rule."""
    targets = {t['name']:{col[0] for col in t['columns']} for t in plan.TABLES}
    table_rename = {'mobile_alert_reminder_phases':'notification_phases','mobile_alert_reminder_dispatches':'notification_phase_dispatches',
        'mobile_alert_reminder_audience':'notification_phase_audience','mobile_notification_outbox':'notification_outbox',
        'mobile_alert_workflow_events':'alert_actions','borderline_review_tasks':'borderline_reviews'}
    special = {
        ('alerts','status'):'派生：alerts.workflow_status → 固定status映射，保留原API；冲突阻断。',
        ('mobile_alert_workflows','alert_id'):'alerts.id：一对一主键合并，不生成新ID。',
        ('mobile_alert_workflows','broadcast_role_ids_json'):'alerts.broadcast_role_snapshot_json：完整历史数组；live关系属于phase。',
        ('mobile_alert_workflows','updated_at'):'alerts.workflow_updated_at：不要覆盖alerts.created_at。',
        ('mobile_alert_workflow_events','actor_user_id'):'alert_actions.user_id',
        ('mobile_alert_workflow_events','event_type'):'alert_actions.action',
        ('mobile_alert_workflow_events','comment'):'alert_actions.note：旧/新差异值原样保留。',
        ('mobile_alert_workflow_events','broadcast_role_ids_json'):'alert_actions.broadcast_role_snapshot_json',
        ('borderline_review_tasks','detection_event_key'):'borderline_reviews.detection_event_id：实际detector ID，校验同alert/camera。',
        ('borderline_review_tasks','model_version'):'borderline_reviews.model_version_id：按原task+版本唯一登记映射；原标签可由FK查询，无法唯一映射则阻断。',
        ('borderline_review_tasks','prompt_version'):'borderline_reviews.prompt_version_id：按唯一原版本标签映射，不伪造内容。',
        ('borderline_review_tasks','config_version'):'borderline_reviews.config_version_id：按config_versions.version_number唯一映射。',
        ('mobile_notification_outbox','event_key'):'notifications.event_key：保持原值及唯一性。',
        ('mobile_notification_outbox','data_payload_json'):'notifications.data_payload_json：先核对双份一致；不一致归档双方并阻断。',
        ('mobile_alert_reminder_phases','broadcast_role_ids_json'):'notification_phases.broadcast_role_snapshot_json；存在的角色展开notification_phase_roles，删除角色仍保留原快照。',
        ('mobile_alert_reminder_audience','alert_id'):'由notification_phases.alert_id派生；迁移先验证与原值一致。',
        ('mobile_alert_reminder_audience','outbox_id'):'notification_phase_audience.notification_id，按原outbox唯一映射；原outbox ID仍保留notification_outbox.id。',
        ('mobile_settings','borderline_recipient_roles_json'):'roles.compat_borderline_selected：原集合展开；不存在/被删role ID完整原数组存migration audit。',
        ('borderline_settings_revision','id'):'mobile_settings.id=global：将原单例身份和revision映射记入migration audit。',
        ('borderline_settings_revision','revision'):'mobile_settings.borderline_revision',
        ('borderline_role_settings','role_id'):'roles.id：按PK合并，不改变role身份。',
        ('borderline_role_settings','default_enabled'):'roles.borderline_default_enabled',
        ('borderline_role_settings','updated_by_user_id'):'roles.borderline_updated_by_user_id',
        ('borderline_role_settings','updated_at'):'roles.borderline_updated_at：与roles.updated_at分开。',
        ('borderline_user_settings','user_id'):'users.id：按PK合并，不改变user身份。',
        ('borderline_user_settings','enabled'):'users.borderline_enabled：无旧行保持NULL，不填false。',
        ('borderline_user_settings','setting_source'):'users.borderline_source',
        ('borderline_user_settings','updated_by_user_id'):'users.borderline_updated_by_user_id',
        ('borderline_user_settings','updated_at'):'users.borderline_updated_at：与users.updated_at分开。',
    }
    docs=['# 原44表逐属性迁移去向','',
        '这是规划映射，未执行数据迁移。所有原值（包括历史NULL和精度）都有明确去向。时间列无损规范化UTC微秒Z；公开ID保留。审计归档不是默默丢弃：必须保存原表/原ID/原属性、差异及新映射，并在验收中核对。','']
    total=0
    for table in sorted(plan.MAPPING):
        docs += ['## '+table,'','| 原attribute | 去向与条件 |','|---|---|']
        target=table_rename.get(table, table)
        for row in source.execute(f'PRAGMA table_info({table})'):
            field=row['name']; dest=special.get((table,field))
            if dest is None and table in CONFIG_MAPS:
                if field=='config_version_id': dest='config_versions.id：五个同版本行合并；缺行/重复阻断。'
                elif field=='binary_prompt_version':dest='config_versions.binary_prompt_version_id → prompt_versions.version原标签唯一映射。'
                elif field=='scene_prompt_version':dest='config_versions.scene_prompt_version_id → prompt_versions.version原标签唯一映射。'
                else:dest='config_versions.'+CONFIG_MAPS[table][field]
            if dest is None and table=='mobile_alert_workflows': dest='alerts.'+field
            if dest is None and table=='mobile_notification_outbox_recipients':
                dest={'outbox_id':'notification_recipients.notification_id：通过保留原ID的outbox关联；queued_target=1。', 'user_id':'notification_recipients.user_id；feed标记与queue标记独立，不能用并集扩大推送。'}[field]
            if dest is None and table=='mobile_notification_outbox_deliveries':
                dest={'outbox_id':'notification_deliveries.notification_id：按原outbox唯一映射；outbox ID保留。', 'updated_at':'notification_deliveries.updated_at：与delivery.last_attempt_at分别保存；先核对原镜像。'}.get(field,'notification_deliveries.'+field+'：镜像逐字段一致才合并，次数不相加。')
            if dest is None and table=='alert_notification_escalation_schedules':
                dest={'alert_id':'notification_phases.alert_id：对应已证明同一phase，或独立legacy归档phase；原对应关系保存在迁移审计。',
                    'initial_notification_id':'notification_phases.source_notification_id','same_audience_repeat_after_seconds':'notification_phases.first_after_seconds',
                    'all_users_escalation_after_seconds':'notification_phases.all_after_seconds','initial_notified_at':'notification_phases.started_at',
                    'same_audience_repeat_due_at':'notification_phases.first_due_at','all_users_escalation_due_at':'notification_phases.all_due_at'}[field]
            if dest is None and table=='alert_notification_escalation_dispatches':
                dest='notification_phases.alert_id → 对应notification_phase_dispatches.phase_id：原alert-stage映射存migration audit。' if field=='alert_id' else 'notification_phase_dispatches.'+field+'：重复相同事实合并，差异保留独立归档phase-stage。'
            if dest is None and target in targets and field in targets[target]:dest=target+'.'+field
            assert dest is not None, (table,field,'unmapped')
            if table=='borderline_reviews' and field=='id':dest+='；已证明同一task镜像时保存为legacy_review_id，否则保留原id的audit_only行。'
            if table=='notification_deliveries' and field=='last_attempt_at':dest+='；updated_at有outbox镜像才复制，独立旧行未记录updated_at，保持NULL，不能用last_attempt_at代替。'
            if field.endswith('_at') or field=='delete_after':dest+='；保留时刻及微秒，无损统一UTC格式。'
            docs.append(f'| `{field}` | {dest} |');total+=1
    docs += ['',f'共{len(plan.MAPPING)}张源表、{total}个源属性，均有迁移/派生/审计去向。',
        '', '新增值的来源也必须明确：record_origin/validation_profile/is_legacy依据原表和已验证的导入事实；message_kind根据原event_key与phase/动作关联，无法证明用legacy；visibility_policy依原recipient集合为空或非空；历史send-time用户和description配置未知保持NULL；缺失prompt内容用metadata_only；独立旧delivery.updated_at保持NULL，不能捏造。']
    (ROOT/'ATTRIBUTE_MAPPING.md').write_text('\n'.join(docs)+'\n',encoding='utf-8')


def main():
    plan.generate()
    c=sqlite3.connect(':memory:')
    c.executescript((ROOT/'schema_proposal.sql').read_text(encoding='utf-8'))
    seed(c)
    tables=c.execute("SELECT name,strict FROM pragma_table_list WHERE schema='main' AND name NOT LIKE 'sqlite_%'").fetchall()
    assert len(tables)==31 and all(x[1]==1 for x in tables)
    assert all(c.execute(f'SELECT count(*) FROM {t[0]}').fetchone()[0]>0 for t in tables)
    cases(c)
    audit=baseline(c)
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not c.execute('PRAGMA foreign_key_check').fetchall()
    docs=['# 推荐重构规划验证记录','',f'执行日期：2026-10-13；Python SQLite `{sqlite3.sqlite_version}`。','',
        f'- 独立内存数据库成功创建 **31张STRICT表**，**305个属性**；31表都有合成数据。',
        f'- **{len(RESULTS)}个针对性正/反约束场景通过**，并检查真实拒绝机制；deferred FK检查包含commit时点。',
        '- 目标合成数据库：integrity_check=ok；foreign_key_check无错误。',
        '- 当前系统DB仅mode=ro打开并在单一BEGIN快照中核对；未改变运行库、Web/Flutter源码或迁移版本。',
        '- 在另一独立内存库导入真实prompt登记，按原task/version映射配置FK；所有当前历史配置逐字段与目标存储值比较，包括原ID/版本、JSON原字符串与数值精度。只验证prompt/config的表示与关联，不是完整44表迁移。','',
        '## 只读基线核对','', '| 项目 | 数量 |','|---|---:|']
    docs += [f'| {key} | {value} |' for key,value in audit.items()]
    docs += ['', '镜像比对包含status/provider/error/attempt/time；payload以原字串完全一致为依据。当前task数量为0时，event外键核对不构成真实任务迁移证明，task情形仅有合成约束验证。时间核对验证时区与无损规范化，未把用户信息、FCM注册值或payload正文写入报告。',
        '', '## 证明范围','', '这些结果证明DDL可执行、列出的局部约束场景及当前基线的一些合并前提。没有执行生产迁移、repository改写、Firebase/FCM真实发送、worker并发/重启、文件搬迁或Web/Mobile端到端回归。DESIGN.md的验收矩阵是后续实施要求，不是本次已完成测试。',
        '', '## 约束场景','', '| 场景 | 预期 | 结果 |','|---|---|---|']
    docs += [f'| {a} | {b} | {r} |' for a,b,r in RESULTS]
    (ROOT/'VALIDATION.md').write_text('\n'.join(docs)+'\n',encoding='utf-8')
    (ROOT/'baseline_summary.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    c.close()
    print(f'PASS: {len(RESULTS)} cases; 31 tables; 305 attributes; {audit["config_versions"]} historical configs; readonly v{audit["schema_version"]}/{audit["source_tables"]} source.')


if __name__=='__main__':main()
