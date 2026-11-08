"""Positive-path regressions: detectors must not wait for media or FCM."""
from dataclasses import replace
import io
import json
from threading import Event
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.domain import Camera
from app.inference.orchestrator import InferenceOrchestrator
from app.inference.weapon import WeaponDetection
from app.main import create_app
from app.media import LocalMediaStore
from app.media_tasks import MediaTaskStore
from app.services import AlertService, NotificationService
from app.sqlite_repository import SQLiteRepository
from test_minicpm_incidents import runtime, video, pipeline, process


def wait_for(predicate, seconds=8):
    deadline = time.monotonic()+seconds
    while time.monotonic()<deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Timed out waiting for pipeline transition')


def test_queued_review_is_skipped_after_later_certain_even_if_latest_score_falls(tmp_path):
    worker, repo, camera, job = pipeline(tmp_path)
    settings = repo.get_settings()
    try:
        process(worker, camera, job, settings, 0)
        alert = repo.list_alerts()[0]
        assert len(repo.list_borderline_review_tasks(alert.id)) == 1
        worker.mc3.score = 1.0
        ring = [(2+i*.5, np.zeros((48,64,3),np.uint8)) for i in range(9)]
        worker._process_violence_window(job.id,camera,ring,(2,6),2,settings)
        process(worker,camera,job,settings,4)
        worker._vlm_queue.drain()
        tasks = repo.list_borderline_review_tasks(alert.id)
        assert len(tasks)==1 and tasks[0]['status']=='cancelled'
        assert tasks[0]['error_code']=='certain_threshold_reached'
        assert not worker.vlm.reviews
        assert not repo.list_alerts()[0].is_borderline_promoted
    finally:
        worker.shutdown()


def test_running_review_finishes_as_audit_without_supplement_after_certain(tmp_path):
    worker, repo, camera, job = pipeline(tmp_path)
    settings = repo.get_settings()
    try:
        process(worker,camera,job,settings,0)
        alert = repo.list_alerts()[0]
        task = repo.list_borderline_review_tasks(alert.id)[0]
        assert repo.claim_borderline_review_task(task['id'])
        worker.mc3.score = 1.0
        ring = [(2+i*.5,np.zeros((48,64,3),np.uint8)) for i in range(9)]
        worker._process_violence_window(job.id,camera,ring,(2,6),2,settings)
        result = repo.finish_borderline_review_task(task['id'],status='positive',raise_alert=True)
        assert result['review_redundant']
        assert not repo.list_pending_borderline_notification_tasks()
    finally:
        worker.shutdown()


def test_notification_enqueue_returns_while_sender_is_blocked(tmp_path):
    config = runtime(tmp_path)
    repo = SQLiteRepository(config)
    admin = repo.bootstrap_first_administrator('sender-test','sender@example.test','Admin')
    repo.register_device(admin['id'],'sender-test-device-token-0123456789','android','Fixture','fixture')
    camera=repo.save_camera(Camera(name='Fixture',storage_path='fixture.mp4',original_filename='fixture.mp4'))
    alert=AlertService(repo).record_violence_candidate(camera,1.0)
    entered, release = Event(), Event()
    class Sender:
        def send(self,*a,**k):
            entered.set()
            assert release.wait(5)
            return 'isolated-provider'
    service=NotificationService(repo,Sender(),defer_delivery=True)
    service._outbox.start()
    try:
        before=time.monotonic()
        service.dispatch(alert)
        assert time.monotonic()-before<.5
        assert entered.wait(2)
        before=time.monotonic()
        service.dispatch(alert)
        assert time.monotonic()-before<.5
    finally:
        release.set()
        service._outbox.stop()


def test_durable_descriptor_recovery_and_invalid_metric_rejection(tmp_path):
    repo=SQLiteRepository(runtime(tmp_path))
    camera=repo.save_camera(Camera(name='Fixture',storage_path='fixture.mp4',original_filename='fixture.mp4'))
    alert=AlertService(repo).record_violence_candidate(camera,1.0)
    payload={'alert_id':alert.id,'camera':camera.model_dump(mode='json')}
    store=MediaTaskStore(repo)
    task_id,created=store.enqueue('stable-key','weapon_evidence',payload,1)
    assert created
    assert store.claim()['id']==task_id
    reopened=MediaTaskStore(SQLiteRepository(runtime(tmp_path)))
    reopened.recover()
    task=reopened.claim()
    assert task['id']==task_id and task['attempts']==2
    reopened.finish(task)
    assert reopened.enqueue('stable-key','weapon_evidence',payload,1)==(task_id,False)
    with pytest.raises(ValueError):
        reopened.enqueue('invalid','weapon_evidence',{**payload,'score':float('nan')},1)
    assert not reopened.pending(camera.id)


def test_restart_recovers_context_without_the_old_in_memory_job(tmp_path):
    old,repo,camera,job=pipeline(tmp_path)
    settings=repo.get_settings()
    old._media_worker.stop()
    job.status='running'
    old._source_fps[job.id]=4
    process(old,camera,job,settings,0)
    assert old._media_store.pending()==1
    old.shutdown()
    recovered=InferenceOrchestrator(runtime(tmp_path),repo,old.media_store,None)
    from test_minicpm_incidents import Vlm
    recovered.vlm=Vlm()
    try:
        recovered.recover_pending_scene_descriptions()
        wait_for(lambda:recovered._media_store.pending()==0,10)
        wait_for(lambda:repo.list_alerts()[0].description_status=='ready',8)
        tasks=repo.list_borderline_review_tasks(repo.list_alerts()[0].id)
        assert len(tasks)==1 and tasks[0]['status']=='negative'
        assert recovered.get_job(job.id) is None
        assert len(repo.list_alerts()[0].evidence)==1
    finally:
        recovered.shutdown()


def test_media_commit_retry_does_not_repeat_codec_work(tmp_path,monkeypatch):
    from app.media_tasks import MediaTaskWorker
    repo=SQLiteRepository(runtime(tmp_path))
    camera=repo.save_camera(Camera(name='Fixture',storage_path='fixture.mp4',original_filename='fixture.mp4'))
    alert=AlertService(repo).record_violence_candidate(camera,1)
    store=MediaTaskStore(repo)
    store.enqueue('codec-once','weapon_evidence',{'alert_id':alert.id,'camera':camera.model_dump(mode='json')},1)
    finish=store.finish
    failures=[True]
    def flaky(*a,**k):
        if failures:
            failures.pop()
            raise OSError('simulated temporary database write failure')
        return finish(*a,**k)
    monkeypatch.setattr(store,'finish',flaky)
    calls=[]
    worker=MediaTaskWorker(store,lambda *a:calls.append(a[0]['id']))
    worker.start()
    try:
        wait_for(lambda:not store.pending(),4)
        assert len(calls)==1
        assert worker.thread.is_alive()
    finally:
        worker.stop()


def test_interrupted_final_media_attempt_marks_evidence_failed_without_reencoding(tmp_path):
    worker,repo,camera,job=pipeline(tmp_path)
    payload={'alert_id':AlertService(repo).record_violence_candidate(camera,1).id,
             'camera':camera.model_dump(mode='json'),'job_id':None}
    store=MediaTaskStore(repo)
    task_id,_=store.enqueue('exhausted','weapon_evidence',payload,1)
    with repo._connect() as connection:
        connection.execute("UPDATE media_tasks SET status='running',attempts=3 WHERE id=?",(task_id,))
        connection.commit()
    calls=[]
    from app.media_tasks import MediaTaskWorker
    recovered=MediaTaskWorker(store,lambda *a:calls.append(True),worker._media_terminal)
    recovered.start()
    try:
        wait_for(lambda:repo.get_alert(payload['alert_id']).evidence_status=='failed')
        assert not calls
        assert not store.pending()
    finally:
        recovered.stop()
        worker.shutdown()


def test_filtered_alert_reads_preserve_decisions_and_do_not_erase_evidence(tmp_path):
    from app.domain import EvidenceRecord
    repo=SQLiteRepository(runtime(tmp_path))
    a=repo.save_camera(Camera(name='A',storage_path='a.mp4',original_filename='a.mp4'))
    b=repo.save_camera(Camera(name='B',storage_path='b.mp4',original_filename='b.mp4'))
    service=AlertService(repo)
    settings=repo.get_settings()
    settings.violence.sensitive_threshold=.2
    settings.violence.moderate_threshold=.6
    settings.violence.certain_threshold=.9
    settings.config_version+=1
    repo.save_settings(settings)
    alert=service.record_violence_candidate(a,.95,source_start_seconds=0,source_end_seconds=4)
    other=service.record_violence_candidate(b,.8,source_start_seconds=0,source_end_seconds=4)
    repo.save_evidence(EvidenceRecord(alert_id=alert.id,media_type='violence_clip',storage_path='evidence/a.mp4',
        content_type='video/mp4',sha256='0'*64,start_seconds=0,end_seconds=4),1)
    full=next(a for a in repo.list_alerts() if a.id==alert.id)
    # A read DTO receives a fresh, transient next-detection key.
    assert repo.get_alert(alert.id).model_dump(exclude={'detection_event_key'})==full.model_dump(exclude={'detection_event_key'})
    active=repo.list_active_alerts(a.id)
    assert len(active)==1 and active[0].id==alert.id and not active[0].evidence
    assert active[0].detection_qualifications==full.detection_qualifications
    merged=service.record_violence_candidate(a,.4,source_start_seconds=2,source_end_seconds=6)
    assert merged.id==alert.id
    updated=repo.get_alert(alert.id)
    assert len(updated.evidence)==1 and updated.detection_count==2
    assert 'certain' in updated.detection_qualifications['violence']
    assert repo.get_alert(other.id).detection_count==1
    with repo._connect() as connection:
        connection.execute("UPDATE alerts SET workflow_status='dismissed' WHERE id=?",(alert.id,))
        connection.commit()
    assert not repo.list_active_alerts(a.id)
    assert repo.get_alert(alert.id).status=='dismissed'
    assert repo.get_alert('missing') is None


def test_live_positive_decode_continues_while_media_worker_is_blocked(tmp_path):
    config=runtime(tmp_path)
    repo=SQLiteRepository(config)
    source=tmp_path/'source.mp4'
    video(source,3,width=64,height=48,fps=10)
    media=LocalMediaStore(config)
    stored=media.save_camera_video('test',source.name,'video/mp4',io.BytesIO(source.read_bytes()))
    camera=repo.save_camera(Camera(name='Positive fixture',storage_path=stored.storage_path,
        original_filename=source.name,duration_seconds=3))
    settings=repo.get_settings()
    settings.violence.segment_seconds=1
    settings.violence.stride_seconds=.5
    settings.violence.sensitive_threshold=.2
    settings.violence.moderate_threshold=.5
    settings.violence.certain_threshold=.9
    settings.weapon.sample_fps=2
    settings.weapon.n_required=2
    settings.weapon.m_window=3
    settings.config_version += 1
    repo.save_settings(settings)
    app=create_app(config,repo,media)
    worker=app.state.orchestrator
    worker.mc3.load=worker.weapon.load=lambda:None
    worker.mc3.infer=lambda frames:.95
    worker.weapon.infer=lambda *a,**k:[WeaponDetection(0,'knife',.95,(2,2,30,35),.1)]
    class Vlm:
        is_loaded=True
        def describe_scene(self,*a,**k):
            return {'description':'People interact.','visible_evidence':['A person moves.'],'uncertainty':'high'}
        describe_weapon_scene=describe_scene
        def review_borderline(self,*a,**k):
            return {'raise_alert':True,'uncertainty':'high','brief_reason':''}
    worker.vlm=Vlm()
    entered,release=Event(),Event()
    original=worker._media_worker.handler
    def blocked(task,payload):
        entered.set()
        assert release.wait(8)
        return original(task,payload)
    worker._media_worker.handler=blocked
    try:
        with TestClient(app) as client:
            response=client.post('/api/analysis/'+camera.id)
            assert response.status_code==202,response.text
            job_id=response.json()['id']
            assert entered.wait(3)
            wait_for(lambda:str(worker.get_job(job_id).status)=='completed',5)
            job=worker.get_job(job_id)
            assert job.weapon_frames_processed==6 and job.violence_windows_processed==5
            assert job.pending_media_jobs>0
            snapshot=client.get('/api/monitor').json()['jobs'][0]
            assert snapshot['detectorReadySeconds']==3
            assert snapshot['timings']['detectorCycle']['samples']>0
            assert client.delete('/api/cameras/'+camera.id).status_code==409
            json.dumps(snapshot,allow_nan=False)
            release.set()
            wait_for(lambda:worker.get_job(job_id).pending_media_jobs==0,10)
            wait_for(lambda:worker.get_job(job_id).pending_vlm_jobs==0,5)
            alerts=repo.list_alerts()
            assert len(alerts)==2
            weapon=next(a for a in alerts if str(a.type)=='weapon')
            assert len(weapon.evidence)==6
            assert all(media.materialize(e.storage_path).is_file() for a in alerts for e in a.evidence)
    finally:
        release.set()
        worker.shutdown()
