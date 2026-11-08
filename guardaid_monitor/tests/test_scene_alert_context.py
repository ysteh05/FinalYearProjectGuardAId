"""Alert-aware scene prompts use raw sidecars of registered gallery photographs."""
import base64
import io
import json
import time
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from app.domain import Camera, EvidenceRecord, SystemSettings
from app.inference.minicpm import MiniCpmOllamaRunner
from app.inference.weapon import WeaponDetection
from app.main import create_app
from app.media import LocalMediaStore
from app.media_tasks import MediaTaskDeferred, MediaTaskStore
from app.services import AlertService
from app.sqlite_repository import SQLiteRepository
from app.vlm_contract import SCENE_PROMPT_VERSION, scene_prompt
from test_minicpm_incidents import runtime, video
from test_nonblocking_pipeline import wait_for


ANSWER={'summary':'People move near a dark object.','details':[
    'The visible object resembles a bag.','The gun alert may be a false positive.'], 'uncertainty':'high'}


@pytest.mark.parametrize('count',[1,8,12])
def test_weapon_runner_sends_exact_raw_bytes_capped_at_eight(tmp_path,count):
    paths=[]
    contents=[]
    for i in range(count):
        image=np.full((40,60,3),i*10,np.uint8)
        raw_content=cv2.imencode('.jpg',image)[1].tobytes()
        cv2.rectangle(image,(4,4),(30,30),(0,0,255),2)
        content=cv2.imencode('.jpg',image)[1].tobytes()
        path=tmp_path/f'{i}.jpg';path.write_bytes(content)
        path.with_name(path.stem+'.raw.jpg').write_bytes(raw_content)
        paths.append(path);contents.append(raw_content)
    runner=MiniCpmOllamaRunner();calls=[]
    def chat(images,settings,**kwargs):
        calls.append((images,kwargs));return json.dumps(ANSWER)
    runner._chat=chat
    result=runner.describe_weapon_scene(paths,SystemSettings().vlm,detected_classes=['gun'],sample_times=list(range(count)))
    assert [base64.b64decode(v) for v in calls[0][0]]==contents[:8]
    assert calls[0][1]['think'] is True
    assert runner.last_call_metadata['evidence_count']==min(8,count)
    assert 'Suspected classes: gun' in calls[0][1]['prompt']
    assert 'not proof' in calls[0][1]['prompt'] and 'false positive' in calls[0][1]['prompt']
    assert result['visible_evidence'][1]=='The gun alert may be a false positive.'


def test_video_prompt_has_alert_context_and_preserves_sampling(tmp_path):
    path=tmp_path/'8-seconds.mp4';video(path,8)
    runner=MiniCpmOllamaRunner();calls=[]
    runner._chat=lambda images,settings,**k:(calls.append(k) or json.dumps(ANSWER))
    runner.describe_scene(path,SystemSettings().vlm,alert_type='physical_contact_bullying')
    assert runner.last_call_metadata['fps']==2 and runner.last_call_metadata['frame_count']==16
    assert runner.last_call_metadata['think'] is True
    assert calls[0]['think'] is True
    assert 'upstream model hypothesis' in calls[0]['prompt']
    assert 'dancing' in calls[0]['prompt'] and 'false positive' in calls[0]['prompt']
    assert 'weapon detection' not in calls[0]['prompt']
    with pytest.raises(ValueError):scene_prompt('untrusted')


def test_waiting_for_photos_does_not_consume_retry_budget(tmp_path):
    repo=SQLiteRepository(runtime(tmp_path))
    camera=repo.save_camera(Camera(name='Fixture',storage_path='fixture.mp4',original_filename='fixture.mp4'))
    alert=AlertService(repo).record_weapon_confirmation(camera,.999)
    store=MediaTaskStore(repo)
    store.enqueue('wait','weapon_context',{'alert_id':alert.id,'camera':camera.model_dump(mode='json')},2)
    for _ in range(5):
        task=store.claim();assert task and task['attempts']==1
        assert store.finish(task,MediaTaskDeferred())=='queued'
        with repo._connect() as c:
            c.execute('update media_tasks set available_at=0');c.commit()
    assert store.pending()


def test_live_gallery_to_vlm_first_eight_and_restart_recovery(tmp_path):
    config=runtime(tmp_path);repo=SQLiteRepository(config);media=LocalMediaStore(config)
    source=tmp_path/'source.mp4';video(source,5,width=64,height=48,fps=10)
    stored=media.save_camera_video('test',source.name,'video/mp4',io.BytesIO(source.read_bytes()))
    camera=repo.save_camera(Camera(name='Gallery fixture',storage_path=stored.storage_path,original_filename=source.name,duration_seconds=5))
    s=repo.get_settings();s.weapon.sample_fps=2;s.weapon.n_required=s.weapon.m_window=1
    s.vlm.borderline_review_enabled=False;s.config_version+=1;repo.save_settings(s)
    app=create_app(config,repo,media);worker=app.state.orchestrator
    worker.mc3.load=worker.weapon.load=lambda:None;worker.mc3.infer=lambda frames:0
    worker.weapon.infer=lambda *a,**k:[WeaponDetection(1,'gun',.999,(3,3,30,35),.2)]
    # Leave persistent media work queued to test restart after evidence collection.
    worker._media_worker.stop()
    try:
        response=TestClient(app).post('/api/analysis/'+camera.id+'?monitorPlayback=true')
        assert response.status_code==202,response.text
        jid=response.json()['id'];wait_for(lambda:worker.get_job(jid).status=='completed',9)
        assert worker.get_job(jid).weapon_frames_processed==10
        assert not repo.list_alerts()[0].evidence
    finally:worker.shutdown()
    recovered=create_app(config,repo,media);runner=recovered.state.orchestrator.vlm
    calls=[]
    def chat(images,settings,**kwargs):
        calls.append((images,kwargs));return json.dumps(ANSWER)
    runner._chat=chat
    with TestClient(recovered):
        wait_for(lambda:repo.list_alerts()[0].description_status=='ready',12)
        alert=repo.list_alerts()[0]
        assert alert.status=='open' and not alert.is_borderline_promoted
        assert len(alert.evidence)==10 and len(calls)==1
        ordered=sorted(alert.evidence,key=lambda e:(e.start_seconds,e.created_at,e.id))
        annotated_paths=[media.materialize(e.storage_path) for e in ordered[:8]]
        raw_contents=[p.with_name(p.stem+'.raw.jpg').read_bytes() for p in annotated_paths]
        assert [base64.b64decode(v) for v in calls[0][0]]==raw_contents
        assert all(p.read_bytes()!=raw for p,raw in zip(annotated_paths,raw_contents))
        assert runner.last_call_metadata['sample_times']==[i*.5 for i in range(8)]
        assert runner.last_call_metadata['evidence_count']==8
        assert alert.scene_summary.visible_evidence[1].endswith('false positive.')
        assert not repo.list_borderline_review_tasks(alert.id)


def test_prompt_metadata_upgrade_only_changes_scene_version(tmp_path):
    config=runtime(tmp_path);repo=SQLiteRepository(config)
    s=repo.get_settings();s.vlm.scene_prompt_version='minicpm_scene_short_points_v8';s.config_version+=1;repo.save_settings(s)
    before=s.model_dump()
    after=SQLiteRepository(config).get_settings()
    assert after.vlm.scene_prompt_version==SCENE_PROMPT_VERSION
    assert after.weapon==s.weapon and after.violence==s.violence
    assert after.vlm.binary_prompt_version==s.vlm.binary_prompt_version
    assert after.config_version==s.config_version+1
