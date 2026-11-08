"""The old `ValueError: MiniCPM output reached its token limit` must recover."""
import io
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
import pytest
from app.domain import Alert, Camera, AnalysisJob, SystemSettings
from app.inference.minicpm import MiniCpmOllamaRunner, _strict_object
from app.inference.orchestrator import InferenceOrchestrator
from app.inference.scene_reliability import classify, SceneFormatError, SceneOutputTruncated
from app.media import LocalMediaStore
from app.sqlite_repository import SQLiteRepository
from test_minicpm_incidents import runtime, Queue

GOOD = {'summary':'People stand nearby.', 'details':['One person raises a hand.'], 'uncertainty':'medium'}

@pytest.fixture
def worker(tmp_path, monkeypatch):
    config = runtime(tmp_path)
    repo = SQLiteRepository(config)
    camera = repo.save_camera(Camera(name='Isolated scene', storage_path='fixture.mp4', original_filename='fixture.mp4'))
    alert = repo.save_alert(Alert(camera_id=camera.id,camera_name=camera.name,type='violence',confidence=.9,
        max_confidence=.9,sensitivity_mode='sensitive',model_version='fixture',config_version=repo.get_settings().config_version))
    w = InferenceOrchestrator(config, repo, LocalMediaStore(config), None)
    w._vlm_queue.shutdown()
    w._vlm_queue = Queue()
    w.vlm._loaded = True
    import base64
    import cv2
    import numpy as np
    encoded = base64.b64encode(cv2.imencode('.jpg', np.zeros((48,64,3),np.uint8))[1]).decode()
    w.vlm._sample = lambda *a,**k: ([encoded], {'sample_times':[.25], 'frame_count':1})
    class Timer:
        def __init__(self, delay, function): self.delay,self.function=delay,function
        def start(self): self.function()
        def cancel(self): pass
    monkeypatch.setattr('app.inference.orchestrator.Timer', Timer)
    context = tmp_path/'tmp'/'vlm_context'/'fixture.mp4'
    context.write_bytes(b'fixture')
    job = AnalysisJob(camera_id=camera.id,camera_name=camera.name)
    w._jobs[job.id] = job
    yield w,repo,alert,context,job
    w.shutdown()

def run(worker, responses):
    w,repo,alert,path,job = worker
    calls=[]
    responses=iter(responses)
    def api(endpoint,payload,**kwargs):
        calls.append(payload)
        value=next(responses)
        if isinstance(value,Exception): raise value
        return value
    w.vlm._api=api
    w._submit_description(alert.id,path,repo.get_settings(),job.id)
    w._vlm_queue.drain()
    return calls,repo.get_scene_task(alert.id)

def response(value=GOOD,reason='stop',raw=None):
    return {'message':{'content':json.dumps(value) if raw is None else raw},'done_reason':reason,'eval_count':1024}

def test_truncation_then_format_then_success_shared_budget_and_raw_log(worker):
    calls,state=run(worker,[response(reason='length'),response({**GOOD,'details':['Incomplete c']}),response()])
    w,repo,alert,path,job=worker
    assert [p['options']['num_predict'] for p in calls]==[1024,1024,1024]
    assert 'details[0]' in calls[2]['messages'][1]['content']
    assert 'Incomplete c' in calls[2]['messages'][1]['content']
    assert all(p['think'] is True for p in calls)
    assert repo.list_alerts()[0].description_status=='ready' and state['attempts']==3
    assert job.pending_vlm_jobs==0 and not path.exists()
    logs=list((w.runtime.data_dir/'diagnostics').rglob('attempt-*.json'))
    assert len(logs)==3
    first=json.loads(sorted(logs)[0].read_text())
    assert first['request']['response']['done_reason']=='length'
    assert first['request']['think'] is True
    assert 'SceneOutputTruncated' in first['error']['traceback']
    assert 'images' not in first['request']

def test_three_transient_failures_exhaust_and_restart_does_not_reset(worker):
    calls,state=run(worker,[URLError('connection refused')]*3)
    w,repo,alert,path,job=worker
    assert len(calls)==3 and state['status']=='failed'
    assert repo.list_alerts()[0].description_status=='failed' and job.pending_vlm_jobs==0
    assert not path.exists()
    w._submit_description(alert.id,path,repo.get_settings(),job.id)
    assert not w._vlm_queue.tasks
    reopened=SQLiteRepository(runtime(w.runtime.data_dir))
    assert reopened.get_scene_task(alert.id)['attempts']==3

def test_retry_releases_worker_and_preserves_context(worker):
    w,repo,alert,path,job=worker
    w.vlm._api=lambda *a,**k: (_ for _ in ()).throw(URLError('temporary'))
    w._submit_description(alert.id,path,repo.get_settings(),job.id)
    w._vlm_queue.run_one()
    assert path.exists() and job.pending_vlm_jobs==1
    assert repo.list_alerts()[0].description_status=='pending'
    marker=[]
    w._vlm_queue.submit(1,lambda: marker.append('borderline'))
    w._vlm_queue.run_one()
    assert marker==['borderline']
    w.vlm._api=lambda *a,**k: response()
    w._vlm_queue.drain()
    assert job.pending_vlm_jobs==0

def test_restart_running_attempt_and_deadline_are_persisted(worker):
    w,repo,alert,path,job=worker
    w._submit_description(alert.id,path,repo.get_settings(),job.id)
    state=repo.get_scene_task(alert.id)
    state.update(attempts=3,status='running')
    repo.save_scene_task(alert.id,state,{})
    w._vlm_queue.tasks.clear()
    w._submit_description(alert.id,path,repo.get_settings(),None)
    w._vlm_queue.drain()
    assert repo.get_scene_task(alert.id)['error_code']=='retry_limit'

@pytest.mark.parametrize('status,retry',[(429,True),(502,True),(503,True),(504,True),(400,False),(404,False),(500,False)])
def test_http_error_classification(status,retry):
    error=HTTPError('http://localhost',status,'test',{'Retry-After':'7'},io.BytesIO(b'temporary'))
    code,actual,delay,message=classify(error,1)
    assert actual==retry and code==f'ollama_http_{status}'
    if status==429: assert delay==7

def test_oom_is_not_retried_and_precise_field_error():
    assert classify(RuntimeError('CUDA out of memory'),1)[:2]==('model_out_of_memory',False)
    with pytest.raises(SceneFormatError,match=r'details\[0\].*maximum 12 / 80'):
        _strict_object(json.dumps({**GOOD,'details':[' '.join(['word']*13)+'.']}))

def test_persistence_is_json_safe(worker):
    w,repo,alert,path,job=worker
    repo.save_scene_task(alert.id,{'duration':float('nan')},{'duration':float('inf')})
    assert repo.get_scene_task(alert.id)['duration'] is None
    assert repo.scene_diagnostics()[alert.id]['duration'] is None

def test_expired_queue_budget_does_not_call_model(worker):
    w,repo,alert,path,job=worker
    w._submit_description(alert.id,path,repo.get_settings(),job.id)
    state=repo.get_scene_task(alert.id)
    state['deadline']=time.time()-1
    repo.save_scene_task(alert.id,state,{})
    w._vlm_queue.tasks.clear()
    w._submit_description(alert.id,path,repo.get_settings())
    w.vlm._api=lambda *a,**k: pytest.fail('Expired queued task must not invoke Ollama')
    w._vlm_queue.drain()
    assert repo.get_scene_task(alert.id)['error_code']=='scene_deadline'

def test_incomplete_truncations_use_1024_every_attempt_and_stop(worker):
    calls,state=run(worker,[response(reason='length',raw='{"summary":"Unfinished')]*3)
    assert [p['options']['num_predict'] for p in calls]==[1024,1024,1024]
    assert state['status']=='failed' and state['error_code']=='output_truncated'


def test_complete_json_at_token_limit_is_retained_for_final_fallback(worker):
    calls,state=run(worker,[response(reason='length')]*3)
    assert [p['options']['num_predict'] for p in calls]==[1024,1024,1024]
    assert state['status']=='ready' and state['attempts']==3
    assert state['length_relaxed'] is True
    assert state['selected_candidate_attempt']==3
    w,repo,alert,path,job=worker
    summary=repo.list_alerts()[0].scene_summary
    assert summary.description==GOOD['summary']
    assert summary.visible_evidence==GOOD['details']
    assert summary.uncertainty==GOOD['uncertainty']


def test_external_retries_persist_latest_long_answer_when_third_is_malformed(worker):
    from test_scene_unannotated_length_fallback import LONG, as_result
    older={**LONG,'summary':LONG['summary'].replace('obscured','small')}
    calls,state=run(worker,[response(older),response(LONG),response(raw='{"summary":')])
    assert [p['options']['num_predict'] for p in calls]==[1024]*3
    assert state['status']=='ready' and state['attempts']==3
    assert state['length_candidate']==LONG
    assert state['selected_candidate_attempt']==2
    w,repo,alert,path,job=worker
    assert repo.list_alerts()[0].scene_summary.model_dump()==as_result(LONG)
    assert job.pending_vlm_jobs==0 and not path.exists()

def test_v18_upgrade_preserves_existing_rows_and_backs_up(tmp_path):
    import sqlite3
    from app.schema_v18 import initialize
    target=tmp_path/'guardaid.db'
    with sqlite3.connect(target) as connection:
        initialize(connection)
        before=connection.execute('SELECT * FROM schema_migrations').fetchall()
    repo=SQLiteRepository(runtime(tmp_path))
    assert repo.schema_version()==20
    with sqlite3.connect(tmp_path/'guardaid.before-scene-v19.db') as backup:
        assert backup.execute('SELECT * FROM schema_migrations').fetchall()==before
    with repo._connect() as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()
