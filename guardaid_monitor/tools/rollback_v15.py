"""Restore a quiesced cutover only before any normal service work resumes.

After resume, freeze and snapshot v15, then reconcile or fix forward. Never
silently erase new actions or delivery facts, and never replay real pushes.
"""
import argparse,json,os,shutil,socket,sqlite3
from pathlib import Path
from app.verify_v15 import verify

def rollback(state_path,workspace):
    workspace=Path(workspace).resolve();state_path=Path(state_path).resolve()
    def safe(path):
        path=Path(path).resolve()
        if not path.is_relative_to(workspace):raise ValueError('Recovery path escaped workspace')
        return path
    state=json.loads(safe(state_path).read_text(encoding='utf-8'))
    if state['service_resumed']:raise RuntimeError('Normal service resumed: preserve v15 and reconcile forward')
    assert state['stage']=='installed_maintenance_pending'
    with socket.socket() as s:
        if s.connect_ex(('127.0.0.1',8020))==0:raise RuntimeError('Stop maintenance backend before rollback')
    db=safe(workspace/'guardaid_monitor/runtime_data/database/guardaid.db')
    # A manual backend launch or direct database write also blocks restoration.
    verify(safe(state['source']),db)
    folder=safe(state['folder']);original=safe(state['original_database'])
    for item in state['changes']:
        target=safe(workspace/item['path'])
        import hashlib
        assert hashlib.sha256(target.read_bytes()).hexdigest()==item['new_sha256'],'Code changed after installation'
    with sqlite3.connect(db) as c:assert c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]==0
    c.close()
    for suffix in ('-wal','-shm'):
        path=safe(str(db)+suffix)
        if path.exists():
            if suffix=='-wal' and path.stat().st_size:raise RuntimeError('WAL is active')
            path.unlink()
    os.replace(db,safe(folder/'v15_before_rollback.db'));shutil.copy2(original,db)
    for item in state['changes']:
        target=safe(workspace/item['path']);backup=safe(folder/'code'/item['path'])
        if backup.exists():shutil.copy2(backup,target)
        else:target.unlink()
    state['stage']='restored_before_service_resume'
    state_path.write_text(json.dumps(state,indent=2),encoding='utf-8')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('state');p.add_argument('workspace');a=p.parse_args()
    rollback(a.state,a.workspace)

if __name__=='__main__':main()
