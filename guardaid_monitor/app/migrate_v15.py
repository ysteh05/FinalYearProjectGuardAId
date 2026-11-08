"""Offline v14 -> v15 converter. Source is always opened read-only.

Usage: python -m app.migrate_v15 SOURCE TARGET [--report REPORT]
Never overwrites SOURCE or an existing TARGET. No models or senders are imported.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from .schema_v15 import STATUS, VERSION, create_schema, insert, utc

NS=uuid.uuid5(uuid.NAMESPACE_URL,'https://github.com/ysteh05/FinalYearProjectGuardAId/database/v15')


def stable(kind,key):return kind+'_'+uuid.uuid5(NS,str(key)).hex


def normalize(values):
    return {k:utc(v) if v is not None and (k.endswith('_at') or k=='delete_after') else v for k,v in values.items()}


def rows(c,table):return [dict(r) for r in c.execute('SELECT * FROM '+table)]


def migrate(source_path,target_path,report_path=None):
    source_path=Path(source_path).resolve();target_path=Path(target_path).resolve()
    if source_path==target_path or target_path.exists():raise ValueError('Target must be a new independent file')
    if not source_path.is_file():raise ValueError('Source database does not exist')
    source=sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True);source.row_factory=sqlite3.Row;source.execute('BEGIN')
    version=source.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]
    if version!=14:source.close();raise ValueError(f'Expected v14, found v{version}')
    if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok':source.close();raise ValueError('Source integrity check failed; use the project SQLite runtime')
    if source.execute('PRAGMA foreign_key_check').fetchall():source.close();raise ValueError('Source FK violations')
    data={r[0]:rows(source,r[0]) for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    source.rollback();source.close()
    if len(data)!=44:raise ValueError('Source v14 table fingerprint mismatch')
    if any(r['is_provisional'] for r in data['alerts']):raise ValueError('Unexpected provisional history: explicit archival decision required')
    target_path.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(target_path);c.row_factory=sqlite3.Row
    audits=[]
    def audit(kind,key,detail):audits.append(dict(id=stable('migration',kind+':'+str(key)),user_id=None,action='migration_v15_'+kind,entity_type='migration',entity_id=str(key),detail_json=json.dumps(detail,separators=(',',':')),created_at=utc()))
    def put(table,values):insert(c,table,normalize(values))
    try:
        create_schema(c);c.execute('BEGIN IMMEDIATE');c.execute('PRAGMA defer_foreign_keys=ON')
        user_prefs={r['user_id']:r for r in data['borderline_user_settings']}
        role_prefs={r['role_id']:r for r in data['borderline_role_settings']}
        settings=data['mobile_settings'][0];selected=set(json.loads(settings['borderline_recipient_roles_json']))
        for r in data['users']:
            r=r.copy();p=user_prefs.get(r['id'])
            if p:r.update(borderline_enabled=p['enabled'],borderline_source=p['setting_source'],borderline_updated_by_user_id=p['updated_by_user_id'],borderline_updated_at=p['updated_at'])
            put('users',r)
        for r in data['roles']:
            r=r.copy();p=role_prefs.get(r['id']);r['compat_borderline_selected']=int(r['id'] in selected)
            if p:r.update(borderline_default_enabled=p['default_enabled'],borderline_updated_by_user_id=p['updated_by_user_id'],borderline_updated_at=p['updated_at'])
            put('roles',r)
        for name in ('user_roles','devices','locations','cameras','media_files','model_versions'):
            for r in data[name]:put(name,r)
        prompts={r['version']:r['id'] for r in data['prompt_versions']}
        prompt_tasks={r['version']:r['task'] for r in data['prompt_versions']}
        for r in data['prompt_versions']:
            r=r.copy();r['capture_state']='frozen' if r['content'] is not None and r['content_sha256'] is not None else 'metadata_only';put('prompt_versions',r)
        def prompt_ref(version,task):
            if not version or not version.strip():raise ValueError('Empty historical prompt label')
            if version in prompts:
                if prompt_tasks[version]!=task:raise ValueError('Prompt task conflict')
                return prompts[version]
            key=stable('prompt',task+':'+version)
            put('prompt_versions',dict(id=key,task=task,version=version,capture_state='metadata_only',created_at=utc()))
            prompts[version]=key;prompt_tasks[version]=task
            audit('prompt_metadata',key,{'task':task,'version':version,'reason':'historical label has no registered content'})
            return key
        details={name:{r['config_version_id']:r for r in data[name]} for name in ('violence_configs','weapon_configs','vlm_configs','alert_notification_escalation_configs')}
        for r in data['config_versions']:
            r=r.copy();r['validation_profile']='historical'
            for name,prefix in [('violence_configs','violence_'),('weapon_configs','weapon_')]:
                detail=details[name][r['id']]
                for key,value in detail.items():
                    if key!='config_version_id':r[prefix+('merge_gap_seconds' if key=='incident_merge_gap_seconds' else key)]=value
            q=details['vlm_configs'][r['id']]
            for key,value in q.items():
                if key in ('binary_prompt_version','scene_prompt_version'):r[key+'_id']=prompt_ref(value,'borderline_binary' if key.startswith('binary') else 'scene_description')
                elif key!='config_version_id':r['vlm_'+key]=value
            r.update({k:v for k,v in details['alert_notification_escalation_configs'][r['id']].items() if k!='config_version_id'})
            put('config_versions',r)
        workflows={r['alert_id']:r for r in data['mobile_alert_workflows']}
        for r in data['alerts']:
            r=r.copy();w=workflows.get(r['id'])
            if w is None or STATUS[w['workflow_status']]!=r['status']:raise ValueError('Alert/workflow state mismatch')
            del r['status'];del r['is_provisional']
            r.update(workflow_status=w['workflow_status'],handler_user_id=w['handler_user_id'],active_phase_id=None,
                broadcast_role_snapshot_json=w['broadcast_role_ids_json'],revision=w['revision'],workflow_updated_at=w['updated_at'])
            put('alerts',r)
        for name in ('camera_sources','detection_events','evidence_records','alert_reads','notification_reads'):
            # Reads referencing notifications are loaded after messages.
            if name=='notification_reads':continue
            for r in data[name]:put(name,r)
        model_refs={}
        for r in data['model_versions']:
            key=(r['task'],r['version'])
            if key in model_refs:raise ValueError('Ambiguous model task/version identity')
            model_refs[key]=r['id']
        config_refs={r['version_number']:r['id'] for r in data['config_versions']}
        for r in data['borderline_review_tasks']:
            r=r.copy();r['record_origin']='task';r['detection_event_id']=r.pop('detection_event_key')
            version=r.pop('model_version');key=('borderline_binary',version)
            if key not in model_refs:
                if not version or not version.strip():raise ValueError('Empty historical model label')
                model_refs[key]=stable('model',':'.join(key))
                put('model_versions',dict(id=model_refs[key],task=key[0],model_name=version,version=version,created_at=utc()))
                audit('model_metadata',model_refs[key],{'task':key[0],'version':version,'reason':'historical task label only'})
            r['model_version_id']=model_refs[key]
            r['prompt_version_id']=prompt_ref(r.pop('prompt_version'),'borderline_binary');r['config_version_id']=config_refs[r.pop('config_version')]
            put('borderline_reviews',r)
        for r in data['borderline_reviews']:
            r=r.copy();r.update(record_origin='audit_only',status='positive' if r['raise_alert'] else 'negative');put('borderline_reviews',r)
        for r in data['vlm_descriptions']:
            r=r.copy();raw=r['structured_json'];text=r['display_text']
            if raw is not None:
                value=json.loads(raw)
                summary=isinstance(value,dict) and isinstance(value.get('description'),str) and isinstance(value.get('visible_evidence'),list) and value.get('uncertainty') in ('low','medium','high')
                r['output_format']='summary' if summary else 'raw_json'
            elif text is not None:r['output_format']='text'
            else:r['output_format']='none'
            put('vlm_descriptions',r)
        for r in data['alert_actions']:put('alert_actions',{**r,'record_origin':'legacy'})
        for r in data['mobile_alert_workflow_events']:
            r=r.copy();r.update(user_id=r.pop('actor_user_id'),action=r.pop('event_type'),note=r.pop('comment'),broadcast_role_snapshot_json=r.pop('broadcast_role_ids_json'),record_origin='workflow');put('alert_actions',r)
        # Determine a stable correspondence for old v10 plans before loading messages.
        phase_map={};phases=[]
        for r in data['mobile_alert_reminder_phases']:
            r=r.copy();r['broadcast_role_snapshot_json']=r.pop('broadcast_role_ids_json');phases.append(r)
        for old in data['alert_notification_escalation_schedules']:
            matches=[r for r in phases if r['alert_id']==old['alert_id'] and r['phase_kind']=='awaiting_decision'
                and r['source_notification_id']==old['initial_notification_id'] and utc(r['started_at'])==utc(old['initial_notified_at'])
                and r['first_after_seconds']==old['same_audience_repeat_after_seconds'] and r['all_after_seconds']==old['all_users_escalation_after_seconds']
                and utc(r['first_due_at'])==utc(old['same_audience_repeat_due_at']) and utc(r['all_due_at'])==utc(old['all_users_escalation_due_at'])]
            # A plan with v10 stage facts is retained independently unless full stage equivalence is proven.
            has_old_stages=any(d['alert_id']==old['alert_id'] for d in data['alert_notification_escalation_dispatches'])
            old_stages=[d for d in data['alert_notification_escalation_dispatches'] if d['alert_id']==old['alert_id']]
            mirrored = len(matches)==1 and all(any(
                d['phase_id']==matches[0]['id'] and normalize({k:v for k,v in d.items() if k!='phase_id'})==normalize({k:v for k,v in old_d.items() if k!='alert_id'})
                for d in data['mobile_alert_reminder_dispatches']) for old_d in old_stages)
            if len(matches)==1 and (not has_old_stages or mirrored):phase_map[old['alert_id']]=matches[0]['id']
            else:
                key=stable('legacyphase',old['alert_id']);phase_map[old['alert_id']]=key
                phases.append(dict(id=key,alert_id=old['alert_id'],phase_kind='awaiting_decision',phase_status='archived',is_legacy=1,
                    started_at=old['initial_notified_at'],first_after_seconds=old['same_audience_repeat_after_seconds'],all_after_seconds=old['all_users_escalation_after_seconds'],
                    first_due_at=old['same_audience_repeat_due_at'],all_due_at=old['all_users_escalation_due_at'],source_notification_id=old['initial_notification_id']))
            audit('legacy_plan',old['alert_id'],{'source':old,'phase_id':phase_map[old['alert_id']]})
        old_notice_stage={r['notification_id']:(phase_map[r['alert_id']],r['stage']) for r in data['alert_notification_escalation_dispatches'] if r['notification_id']}
        outboxes={r['notification_id']:r for r in data['mobile_notification_outbox']}
        notice_by_id={r['id']:r for r in data['notifications']}
        recipients=data['notification_recipients']
        for r in data['notifications']:
            r=r.copy();payload=json.loads(r['data_payload_json']);o=outboxes.get(r['id']);r['message_kind']='legacy'
            if o:
                if o['data_payload_json']!=r['data_payload_json']:raise ValueError('Notification/outbox payload conflict')
                r['event_key']=o['event_key']
            phase_id=payload.get('reminderPhaseId');stage=payload.get('reminderStage')
            if phase_id and stage:
                r.update(phase_id=phase_id,reminder_stage=int(stage),message_kind='late_repeat' if payload.get('lateAudience')=='true' else 'phase_reminder')
            elif r['id'] in old_notice_stage:
                phase_id,stage=old_notice_stage[r['id']];r.update(phase_id=phase_id,reminder_stage=stage,message_kind='phase_reminder')
            elif payload.get('source')=='alert_broadcast':r['message_kind']='broadcast'
            elif payload.get('source')=='alert_decision':r['message_kind']='decision_update'
            elif payload.get('source')=='alert_created':r['message_kind']='initial'
            r['visibility_policy']='restricted' if any(x['notification_id']==r['id'] for x in recipients) else 'broadcast'
            put('notifications',r)
        # All notification IDs now exist; deferred phase/message cycles can be completed.
        for r in phases:put('notification_phases',r)
        role_ids={r['id'] for r in data['roles']}
        for r in phases:
            if r['phase_kind']=='awaiting_handler':
                for role_id in set(json.loads(r.get('broadcast_role_snapshot_json','[]')))&role_ids:put('notification_phase_roles',dict(phase_id=r['id'],role_id=role_id))
        for key,w in workflows.items():
            if w['active_phase_id']:c.execute('UPDATE alerts SET active_phase_id=? WHERE id=?',(w['active_phase_id'],key))
        for r in data['notification_reads']:put('notification_reads',r)
        for r in data['mobile_notification_outbox']:
            r={k:v for k,v in r.items() if k not in ('event_key','data_payload_json')};put('notification_outbox',r)
        outbox_to_notice={r['id']:r['notification_id'] for r in data['mobile_notification_outbox']}
        targets={}
        for r in recipients:targets[(r['notification_id'],r['user_id'],r['role_id'])]=dict(**r,feed_visible=1,queued_target=0)
        for r in data['mobile_notification_outbox_recipients']:
            key=(outbox_to_notice[r['outbox_id']],r['user_id'],None)
            targets.setdefault(key,dict(notification_id=key[0],user_id=key[1],role_id=None,feed_visible=0,queued_target=0))['queued_target']=1
        for r in targets.values():put('notification_recipients',r)
        if len(recipients)>sum(r['feed_visible'] for r in targets.values()):audit('recipient_duplicates','global',{'source_rows':len(recipients),'distinct_rows':sum(r['feed_visible'] for r in targets.values())})
        deliveries={(r['notification_id'],r['device_id']):r.copy() for r in data['notification_deliveries']}
        for old in data['mobile_notification_outbox_deliveries']:
            key=(outbox_to_notice[old['outbox_id']],old['device_id']);existing=deliveries.get(key)
            if existing:
                for field in ('status','provider_message_id','error_code','attempt_count'):
                    if existing[field]!=old[field]:raise ValueError('Device delivery mirror conflict')
                if utc(existing['last_attempt_at'])!=utc(old['updated_at']):raise ValueError('Device delivery timestamp conflict')
                existing['updated_at']=old['updated_at']
            else:
                deliveries[key]=dict(id=stable('delivery',':'.join(key)),notification_id=key[0],device_id=key[1],status=old['status'],provider_message_id=old['provider_message_id'],
                    error_code=old['error_code'],attempt_count=old['attempt_count'],last_attempt_at=old['updated_at'],updated_at=old['updated_at'])
        for r in deliveries.values():put('notification_deliveries',r)
        for r in data['alert_notification_audience']:put('alert_notification_audience',r)
        dispatches={ (r['phase_id'],r['stage']):r.copy() for r in data['mobile_alert_reminder_dispatches'] }
        for r in data['alert_notification_escalation_dispatches']:
            r=r.copy();r['phase_id']=phase_map[r.pop('alert_id')]
            key=(r['phase_id'],r['stage'])
            if key in dispatches and normalize(dispatches[key])!=normalize(r):raise ValueError('Dispatch mirror conflict')
            dispatches[key]=r
        for r in dispatches.values():
            r['recipient_count_unit']='user' if r['status']=='queued' else 'device'
            put('notification_phase_dispatches',r)
        for r in data['mobile_alert_reminder_audience']:
            r=r.copy();alert_id=r.pop('alert_id');phase=next(p for p in phases if p['id']==r['phase_id'])
            if phase['alert_id']!=alert_id:raise ValueError('Phase audience alert mismatch')
            outbox_id=r.pop('outbox_id');r['notification_id']=outbox_to_notice[outbox_id] if outbox_id else None;put('notification_phase_audience',r)
        settings=settings.copy();selected_raw=settings.pop('borderline_recipient_roles_json');settings['borderline_revision']=data['borderline_settings_revision'][0]['revision'];put('mobile_settings',settings)
        audit('legacy_role_selection','global',{'role_ids':json.loads(selected_raw)})
        for r in data['audit_logs']:put('audit_logs',r)
        for r in audits:put('audit_logs',r)
        for r in data['schema_migrations']:put('schema_migrations',r)
        fk=c.execute('PRAGMA foreign_key_check').fetchall()
        if fk:raise ValueError('Target FK verification failed: '+str([tuple(r) for r in fk[:3]]))
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Target integrity check failed')
        put('schema_migrations',dict(version=VERSION,name='targeted_refactor_v15',applied_at=utc()))
        c.commit()
        target_counts={r[0]:c.execute('SELECT count(*) FROM '+r[0]).fetchone()[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        attribute_count=sum(len(c.execute('PRAGMA table_info('+name+')').fetchall()) for name in target_counts)
        if len(target_counts)!=31 or attribute_count!=304:raise ValueError('Target schema count mismatch')
        report={'source_version':14,'target_version':15,'source_counts':{k:len(v) for k,v in data.items()},'target_counts':target_counts,'target_tables':31,'target_attributes':attribute_count,
            'integrity':'ok','foreign_key_violations':0,'original_audit_rows':len(data['audit_logs']),'migration_audit_rows':len(audits),'delivery_mirrors_collapsed':len(data['notification_deliveries'])+len(data['mobile_notification_outbox_deliveries'])-len(deliveries),'success':True}
        if report_path:Path(report_path).write_text(json.dumps(report,indent=2),encoding='utf-8')
        return report
    except Exception:
        c.rollback();raise
    finally:c.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('target');p.add_argument('--report');a=p.parse_args()
    report=migrate(a.source,a.target,a.report);print(json.dumps(report,indent=2))


if __name__=='__main__':main()
