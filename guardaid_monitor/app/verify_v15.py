"""Read-only, independent field reconciliation of every v14 source table."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
from .schema_v15 import utc, fingerprint, expected_fingerprint
from .read_models_v15 import read_sql

PROJECTIONS={
 'alerts':'incident_state','mobile_alert_workflows':'workflow_state','mobile_alert_workflow_events':'workflow_history',
 'violence_configs':'violence_policy','weapon_configs':'weapon_policy','vlm_configs':'vlm_policy','alert_notification_escalation_configs':'reminder_policy',
 'mobile_notification_outbox':'queue_messages','mobile_notification_outbox_recipients':'queued_users',
 'mobile_notification_outbox_deliveries':'device_results','borderline_role_settings':'role_preferences',
 'borderline_user_settings':'personal_preferences','borderline_settings_revision':'preference_version',
 'borderline_review_tasks':'review_tasks','mobile_alert_reminder_audience':'user_reminders','mobile_settings':'mobile_preferences',
 'mobile_alert_reminder_phases':'notification_phases','mobile_alert_reminder_dispatches':'notification_phase_dispatches',
}

def canonical(value,field):
    if value is None:return value
    if field.endswith('_at') or field=='delete_after':return utc(value)
    if field=='borderline_recipient_roles_json':return json.dumps(sorted(set(json.loads(value))))
    return value


def verify(source_path,target_path,report_path=None):
    connections=[]
    try:
        for path in (source_path,target_path):
            c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
            c.row_factory=sqlite3.Row;c.execute('BEGIN');connections.append(c)
        old,new=connections
        assert fingerprint(new)==expected_fingerprint(),'Schema fingerprint mismatch'
        for c in connections:
            assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert not c.execute('PRAGMA foreign_key_check').fetchall()
        assert old.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]==14
        assert new.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]==15
        tables=[r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        assert len(tables)==44
        counts={}
        # Legacy plans have full raw source facts and a stable phase correspondence in audit.
        plans={r['entity_id']:json.loads(r['detail_json']) for r in new.execute("SELECT entity_id,detail_json FROM audit_logs WHERE action='migration_v15_legacy_plan'")}
        for table in tables:
            source=[dict(r) for r in old.execute('SELECT * FROM '+table)]
            if table=='alerts':assert all(r['is_provisional']==0 for r in source)
            fields=[r[1] for r in old.execute('PRAGMA table_info('+table+')')]
            projection=PROJECTIONS.get(table,table)
            if table=='alert_notification_escalation_schedules':
                assert len(plans)==len(source)
                for row in source:
                    archived=plans[row['alert_id']]
                    assert archived['source']==row,'Legacy plan audit differs'
                    p=dict(new.execute('SELECT * FROM notification_phases WHERE id=?',(archived['phase_id'],)).fetchone())
                    mapping={'alert_id':'alert_id','initial_notification_id':'source_notification_id','initial_notified_at':'started_at',
                        'same_audience_repeat_after_seconds':'first_after_seconds','all_users_escalation_after_seconds':'all_after_seconds',
                        'same_audience_repeat_due_at':'first_due_at','all_users_escalation_due_at':'all_due_at'}
                    for key,target in mapping.items():assert canonical(row[key],key)==canonical(p[target],key),(table,key)
                counts[table]=len(source);continue
            if table=='alert_notification_escalation_dispatches':
                for row in source:
                    phase_id=plans[row['alert_id']]['phase_id']
                    target=dict(new.execute('SELECT * FROM notification_phase_dispatches WHERE phase_id=? AND stage=?',(phase_id,row['stage'])).fetchone())
                    for key,value in row.items():
                        if key!='alert_id':assert canonical(value,key)==canonical(target[key],key),(table,key)
                counts[table]=len(source);continue
            if table=='mobile_alert_reminder_phases':
                select=','.join('broadcast_role_snapshot_json AS broadcast_role_ids_json' if f=='broadcast_role_ids_json' else f for f in fields)
            else:select=','.join(f for f in fields if not(table=='alerts' and f=='is_provisional'))
            where={
                'borderline_reviews':" WHERE record_origin='audit_only'",
                'alert_actions':" WHERE record_origin='legacy'",
                'notification_recipients':" WHERE feed_visible=1",
                'audit_logs':" WHERE action NOT LIKE 'migration_v15_%'",
                'schema_migrations':' WHERE version<=14',
            }.get(table,'')
            targets=[dict(r) for r in new.execute(read_sql('SELECT '+select+' FROM '+projection+where))]
            compare_fields=[f for f in fields if not(table=='alerts' and f=='is_provisional')]
            def key(row):return tuple(canonical(row[f],f) for f in compare_fields)
            left=Counter(key(r) for r in source);right=Counter(key(r) for r in targets)
            if table in ('notification_recipients','mobile_notification_outbox_recipients'):left=Counter(set(left))
            # New archived legacy phases and outbox-only device rows are valid additional representations.
            if table in ('mobile_alert_reminder_phases','notification_deliveries','prompt_versions','model_versions'):
                assert not(left-right),(table,'source fields missing or changed')
            else:assert left==right,(table,'source/target field mismatch',sum(left.values()),sum(right.values()))
            counts[table]=len(source)
        assert all(new.execute('SELECT count(*) FROM '+t).fetchone()[0]==old.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('alerts','notifications','config_versions','evidence_records','media_files','detection_events'))
        report=dict(success=True,checked_source_tables=44,checked_source_rows=counts,
            removed_attribute='alerts.is_provisional (source values verified zero)',
            preserved_compatibility_attributes=['prompt_versions.capture_state','borderline_reviews.legacy_review_id','notification_phases.is_legacy'],
            integrity='ok',foreign_key_violations=0,target_tables=31,target_attributes=304)
        if report_path:Path(report_path).write_text(json.dumps(report,indent=2),encoding='utf-8')
        return report
    finally:
        for c in connections:c.rollback();c.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source');parser.add_argument('target');parser.add_argument('--report');a=parser.parse_args()
    print(json.dumps(verify(a.source,a.target,a.report),indent=2))

if __name__=='__main__':main()
