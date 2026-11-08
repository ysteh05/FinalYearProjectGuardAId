"""Regressions for the confirmed MiniCPM / single-incident processing contract."""
import base64
from dataclasses import replace
import json
import io
import sqlite3
from pathlib import Path

import av
import cv2
import numpy as np
import pytest

from app.config import RuntimeConfig
from app.domain import AnalysisJob, Camera, SceneSummary, SystemSettings, utc_now
from app.inference.minicpm import MiniCpmOllamaRunner, _strict_object
from app.inference.orchestrator import InferenceOrchestrator
from app.media import LocalMediaStore
from app.services import AlertService
from app.sqlite_repository import SQLiteRepository
from app.vlm_contract import MODEL_ID, SCENE_SCHEMA, scene_sampling


def runtime(tmp_path):
    return replace(RuntimeConfig.from_env(), data_dir=tmp_path, database_path=tmp_path / "guardaid.db",
                   persistence_mode="sqlite", auth_mode="disabled", notification_mode="console", maintenance_mode=False)


def video(path, duration, *, width=64, height=48, fps=4):
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.options = {"preset": "ultrafast", "crf": "18"}
        for index in range(round(duration * fps)):
            frame = av.VideoFrame.from_ndarray(np.full((height, width, 3), index % 250, np.uint8), format="bgr24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


@pytest.mark.parametrize("duration,fps,included", [(4,2,4),(8,2,8),(8.25,1,8.25),(60,1,60),(90,1,60)])
def test_adaptive_scene_policy(duration, fps, included):
    assert scene_sampling(duration) == (fps, included)


@pytest.mark.parametrize("duration", [0,-1,float("nan"),float("inf")])
def test_scene_policy_rejects_invalid_durations(duration):
    with pytest.raises(ValueError):
        scene_sampling(duration)


@pytest.mark.parametrize("duration,count,fps", [(4,8,2),(8,16,2),(8.25,9,1),(61,60,1)])
def test_actual_decode_sampling_and_sixty_second_prefix(tmp_path, duration, count, fps):
    path = tmp_path / "source.mp4"
    video(path, duration)
    images, metadata = MiniCpmOllamaRunner()._sample(path, scene=True)
    assert len(images) == count
    assert metadata["fps"] == fps
    assert all(t < min(duration,60) for t in metadata["sample_times"])
    assert metadata["sample_times"][0] == .5 / fps
    decoded = cv2.imdecode(np.frombuffer(base64.b64decode(images[0]), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (360,480)
    assert all(source <= target + 1e-6 for source,target in zip(metadata["source_times"],metadata["sample_times"]))


def test_structured_scene_preserves_all_short_details():
    output = {"summary":"People interact in a room.", "details":["One person raises both hands.",
              "Another person steps closer.","A third person watches.","A person checks a phone."], "uncertainty":"low"}
    parsed = _strict_object(json.dumps(output))
    assert len(SceneSummary.model_validate(parsed).visible_evidence) == 4
    for invalid in [
        '{"summary":"x","details":[]}',
        '{"summary":"x","summary":"y","details":[],"uncertainty":"low"}',
        json.dumps({**output,"details":["A woman in a brown top waves and claps while another c"]}),
        json.dumps({**output,"uncertainty":"certain"}),
    ]:
        with pytest.raises(ValueError):
            _strict_object(invalid)


def test_ollama_payload_is_non_thinking_and_binary_only(monkeypatch):
    runner = MiniCpmOllamaRunner()
    runner._loaded = True
    monkeypatch.setattr('app.inference.minicpm.prepare_images', lambda *a, **k:
                        (["frame"]*16, {"fps":4, "sample_times":[.25*i for i in range(16)]}))
    calls=[]
    def api(path,payload,**kwargs):
        calls.append(payload)
        return {"message":{"content":"1"},"done_reason":"stop"}
    runner._api=api
    assert runner.review_borderline(Path("unused.mp4"),SystemSettings().vlm)["raise_alert"]
    assert calls[0]["think"] is False
    assert calls[0]["model"] == MODEL_ID
    assert 'format' not in calls[0]
    assert calls[0]["options"]["num_ctx"] == 24576
    assert "0.25" in calls[0]["messages"][0]["content"]


def test_scene_thinking_payload_records_presence_without_persisting_reasoning():
    runner = MiniCpmOllamaRunner()
    runner._loaded = True
    payloads = []

    def api(path, payload, **kwargs):
        payloads.append(payload)
        return {"message": {"content": '{"summary":"A person walks.","details":[],"uncertainty":"low"}',
                            "thinking": "private model reasoning"}, "done_reason": "stop"}

    runner._api = api
    runner.last_call_metadata = {}
    runner.scene_trace = {"think": True}
    runner._chat(["frame"], SystemSettings().vlm, system="scene", prompt="describe", schema={},
                 output_tokens=1024, think=True)

    assert payloads[0]["think"] is True
    assert runner.last_call_metadata["think_requested"] is True
    assert runner.last_call_metadata["thinking_nonempty"] is True
    assert runner.scene_trace["response"]["message"]["content"].startswith('{"summary"')
    assert "thinking" not in runner.scene_trace["response"]["message"]


def test_scene_invalid_details_are_retried_without_truncation():
    runner=MiniCpmOllamaRunner()
    runner._sample=lambda *a,**k: (["AA=="],{})
    replies=iter([
        '{"summary":"People interact.","details":["This ends mid word c"],"uncertainty":"low"}',
        '{"summary":"People interact.","details":["One person raises a hand."],"uncertainty":"low"}',
    ])
    calls=[]
    def chat(*args,**kwargs):
        calls.append(kwargs)
        return next(replies)
    runner._chat=chat
    assert runner.describe_scene(Path("unused"),SystemSettings().vlm)["visible_evidence"] == ["One person raises a hand."]
    assert len(calls)==2 and calls[0]["schema"] == SCENE_SCHEMA
    assert "failed validation" in calls[1]["prompt"]




def test_pending_final_export_keeps_camera_source_available_and_rejects_stale_model_config(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.repository import DemoRepository
    from app.media import DemoMediaStore
    config=replace(runtime(tmp_path),persistence_mode="demo")
    repository=DemoRepository()
    camera=repository.save_camera(Camera(name="Source protection",storage_path="fixture.mp4",original_filename="fixture.mp4"))
    app=create_app(config,repository,DemoMediaStore(config))
    app.state.orchestrator._jobs["pending"]=AnalysisJob(id="pending",camera_id=camera.id,camera_name=camera.name,
        status="completed",pending_vlm_jobs=1)
    try:
        client=TestClient(app)
        assert client.put(f"/api/cameras/{camera.id}/video",files={"video":("replacement.mp4",b"data","video/mp4")}).status_code==409
        assert client.delete(f"/api/cameras/{camera.id}").status_code==409
        settings=client.get("/api/settings").json()
        settings["vlm"]["modelId"]="Qwen/Qwen3-VL-2B-Instruct"
        assert client.put("/api/settings",json=settings).status_code==422
    finally:
        app.state.orchestrator.shutdown()


class Queue:
    def __init__(self): self.tasks=[]
    def submit(self,priority,function,*args,**kwargs): self.tasks.append((priority,function,args,kwargs))
    def run_one(self):
        index=min(range(len(self.tasks)),key=lambda i:self.tasks[i][0])
        _,function,args,kwargs=self.tasks.pop(index)
        function(*args,**kwargs)
    def drain(self):
        while self.tasks: self.run_one()
    def shutdown(self): pass


class Notifications:
    def __init__(self): self.calls=[]
    def dispatch(self,alert,*args,**kwargs):
        self.calls.append((alert.id,kwargs.get("borderline_review_task_id")))
        return 0


class Vlm:
    MODEL_VERSION=MODEL_ID
    def __init__(self): self.reviews=[]; self.scenes=[]; self.decisions=iter([False,True])
    def review_borderline(self,path,settings):
        self.reviews.append(path)
        return {"raise_alert":next(self.decisions),"uncertainty":"high","brief_reason":""}
    def describe_scene(self,path,settings,**kwargs):
        _,metadata=MiniCpmOllamaRunner()._sample(path,scene=True)
        self.scenes.append(metadata)
        return {"description":"People interact in a room.","visible_evidence":[
            "One person raises both hands.","Another person steps closer.","A third person watches."],"uncertainty":"low"}


def pipeline(tmp_path):
    config=runtime(tmp_path)
    repository=SQLiteRepository(config)
    media=LocalMediaStore(config)
    source=tmp_path / "source.mp4"
    video(source,90)
    stored=media.save_camera_video("camera",source.name,"video/mp4",io.BytesIO(source.read_bytes()))
    camera=repository.save_camera(Camera(name="Fixture",storage_path=stored.storage_path,original_filename=source.name,duration_seconds=90))
    orchestrator=InferenceOrchestrator(config,repository,media,None)
    orchestrator._vlm_queue.shutdown()
    orchestrator._vlm_queue=Queue()
    orchestrator.vlm=Vlm()
    orchestrator.notifications=Notifications()
    orchestrator.mc3=type("Mc3",(),{"score":.5,"infer":lambda self,frames:self.score})()
    job=AnalysisJob(camera_id=camera.id,camera_name=camera.name,started_at=utc_now())
    orchestrator._jobs[job.id]=job
    return orchestrator,repository,camera,job


def process(orchestrator,camera,job,settings,start,positive=True):
    orchestrator.mc3.score=.5 if positive else .01
    ring=[(start+i*.5,np.zeros((48,64,3),np.uint8)) for i in range(9)]
    orchestrator._process_violence_window(job.id,camera,ring,(start,start+4),2,settings)


def test_negative_then_positive_stops_queued_reviews_and_finalises_exact_incident(tmp_path):
    orchestrator,repository,camera,job=pipeline(tmp_path)
    settings=repository.get_settings()
    try:
        process(orchestrator,camera,job,settings,20)
        assert not orchestrator.vlm.scenes
        orchestrator._vlm_queue.run_one()
        assert repository.list_alerts()[0].borderline_review_status=="negative"
        process(orchestrator,camera,job,settings,22)
        process(orchestrator,camera,job,settings,24) # Already queued when 22-26 turns positive.
        orchestrator._vlm_queue.run_one()
        orchestrator._vlm_queue.drain()
        assert len(orchestrator.vlm.reviews)==2
        alert=repository.list_alerts()[0]
        assert alert.is_borderline_promoted
        assert len([entry for entry in orchestrator.notifications.calls if entry[1]])==1
        assert repository.list_borderline_review_tasks(alert.id)[2]["status"]=="cancelled"
        for start in range(26,76,2):
            process(orchestrator,camera,job,settings,start,positive=start in {26,30,34,38,44,50,56})
            orchestrator._finalise_violence_incidents(job.id,next_window_start=start+2)
            if start<74:
                assert not orchestrator._vlm_queue.tasks
        assert not orchestrator.vlm.scenes
        orchestrator._vlm_queue.drain()
        saved=repository.list_alerts()[0]
        assert (saved.source_start_seconds,saved.source_end_seconds)==(20,60)
        assert len(saved.evidence)==1
        assert (saved.evidence[0].start_seconds,saved.evidence[0].end_seconds)==(20,60)
        assert len(orchestrator.vlm.reviews)==2 and len(orchestrator.vlm.scenes)==1
        assert orchestrator.vlm.scenes[0]["duration_seconds"]==pytest.approx(40)
        assert orchestrator.vlm.scenes[0]["fps"]==1
        assert saved.description_status=="ready" and len(saved.scene_summary.visible_evidence)==3
        assert job.pending_vlm_jobs==0
        restarted=SQLiteRepository(runtime(tmp_path))
        assert restarted.list_alerts()[0].is_borderline_promoted
        stale=saved.model_copy(update={"is_borderline_promoted":False,"borderline_review_status":"queued"})
        restarted.save_alert(stale)
        assert restarted.list_alerts()[0].borderline_review_status=="positive"
    finally:
        orchestrator.shutdown()


def test_last_eligible_late_positive_still_merges_and_closed_alert_does_not(tmp_path):
    orchestrator,repository,camera,job=pipeline(tmp_path)
    settings=repository.get_settings()
    try:
        process(orchestrator,camera,job,settings,56)
        orchestrator._finalise_violence_incidents(job.id,next_window_start=74)
        assert len(orchestrator._pending_violence_scenes)==1
        process(orchestrator,camera,job,settings,74)
        alert=repository.list_alerts()[0]
        assert alert.detection_count==2 and alert.source_end_seconds==78
        orchestrator._finalise_violence_incidents(job.id,next_window_start=76)
        assert len(orchestrator._pending_violence_scenes)==1
        admin=repository.bootstrap_first_administrator("fixture-admin","admin@example.test","Admin")
        repository.record_mobile_workflow_action(
            alert.id,admin["id"],"dismiss_false_alarm",comment=None,recipient_role_ids=[],
            allow_empty_audience=False,idempotency_key="dismiss-fixture",first_after_seconds=60,all_after_seconds=120,
        )
        process(orchestrator,camera,job,settings,76)
        assert len(repository.list_alerts())==2
    finally:
        orchestrator.shutdown()


def test_evidence_write_failure_keeps_the_previous_clip(tmp_path,monkeypatch):
    orchestrator,repository,camera,job=pipeline(tmp_path)
    try:
        process(orchestrator,camera,job,repository.get_settings(),20)
        alert=repository.list_alerts()[0]
        original=alert.evidence[0]
        path=orchestrator.media_store.materialize(original.storage_path)
        previous_bytes=path.read_bytes()
        def fail(*args,**kwargs): raise OSError("simulated full disk")
        monkeypatch.setattr(orchestrator,"_attach_evidence",fail)
        with pytest.raises(OSError,match="full disk"):
            orchestrator._replace_evidence(alert,media_type="violence_clip")
        assert path.read_bytes()==previous_bytes
        assert repository.list_alerts()[0].evidence[0].id==original.id
    finally:
        orchestrator.shutdown()
