"""Regress the live/evaluation input mismatch using real source decoding."""
import base64
import importlib.util
import json
from pathlib import Path

import pytest

from app.domain import SystemSettings
from app.inference import borderline_protocol as protocol
from app.inference.minicpm import MiniCpmOllamaRunner
from app.inference.orchestrator import InferenceOrchestrator
from app.sqlite_repository import SQLiteRepository
from test_minicpm_incidents import video, runtime


def archived():
    path = Path(__file__).resolve().parent / 'reference_borderline_input.py'
    spec = importlib.util.spec_from_file_location('archived_borderline_protocol', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('fps', [2, 4])
def test_original_source_images_and_prompt_are_byte_identical_to_archived_evaluator(tmp_path, fps):
    source = tmp_path / 'original.mp4'
    video(source, 6, fps=12, width=640, height=360)
    old = archived()
    raw = old.extract_video_frames(old.find_ffmpeg(), source, 1.0, tmp_path / 'reference', fps, 4 * fps)
    expected, _, _ = old.resize_frames_to_short_side(raw, 360)
    actual, metadata = protocol.prepare_images(source, start=1.0, fps=fps)
    assert [base64.b64decode(value) for value in actual] == expected
    assert protocol.build_prompt(fps, 4 * fps) == old.build_prompt(fps, 4 * fps)
    assert metadata['sample_times'] == [i / fps for i in range(fps * 4)]


def test_production_uses_original_source_offset_not_reencoded_context(tmp_path, monkeypatch):
    source, context = tmp_path / 'source.mp4', tmp_path / 'context.mp4'
    video(source, 6, fps=12)
    video(context, 4, fps=4)
    runner = MiniCpmOllamaRunner()
    payloads = []
    monkeypatch.setattr(runner, '_api', lambda endpoint, payload: (
        payloads.append(payload) or {'message': {'content': '0'}, 'done_reason': 'stop'}))
    settings = SystemSettings().vlm.model_copy(update={'input_fps': 4})
    result = runner.review_borderline(context, settings, source_path=source, start_seconds=1)
    expected, _ = protocol.prepare_images(source, start=1, fps=4)
    wrong, _ = protocol.prepare_images(context, fps=4)
    assert expected != wrong
    assert payloads[0]['messages'][0]['images'] == expected
    assert len(payloads[0]['messages']) == 1 and 'format' not in payloads[0]
    assert payloads[0]['options'] == {'temperature': 0, 'seed': 20261017,
                                      'num_predict': 12, 'num_ctx': 24576}
    assert not result['raise_alert']


@pytest.mark.parametrize('text,reason', [('true', 'stop'), ('{"decision":0}', 'stop'),
                                      ('0 because no violence', 'stop'), ('1', 'length'), ('', 'stop')])
def test_nonbinary_or_truncated_answers_are_not_silently_confirmed(text, reason):
    with pytest.raises(ValueError):
        protocol.parse_decision({'message': {'content': text}, 'done_reason': reason})


def test_missing_or_short_source_does_not_fall_back_to_reencoded_clip(tmp_path):
    with pytest.raises(FileNotFoundError):
        protocol.prepare_images(tmp_path / 'missing.mp4')
    short = tmp_path / 'short.mp4'
    video(short, 1)
    with pytest.raises(ValueError, match='requires 16 frames'):
        protocol.prepare_images(short)


def test_binary_version_upgrade_is_idempotent_and_preserves_scene_and_detectors(tmp_path):
    config = runtime(tmp_path)
    repo = SQLiteRepository(config)
    before = repo.get_settings()
    legacy = before.model_copy(update={'vlm': before.vlm.model_copy(
        update={'binary_prompt_version': 'minicpm_binary_360p_v1', 'input_fps': 4}),
        'config_version': before.config_version + 1})
    repo.save_settings(legacy)
    after = SQLiteRepository(config).get_settings()
    assert after.vlm.binary_prompt_version == protocol.PROTOCOL_VERSION
    assert after.vlm.model_dump(exclude={'binary_prompt_version'}) == legacy.vlm.model_dump(exclude={'binary_prompt_version'})
    assert after.violence == legacy.violence and after.weapon == legacy.weapon
    assert SQLiteRepository(config).get_settings() == after


def test_persisted_task_calls_original_camera_window_and_persists_negative(tmp_path, monkeypatch):
    from test_minicpm_incidents import pipeline, process
    orchestrator, repo, camera, job = pipeline(tmp_path)
    try:
        runner = MiniCpmOllamaRunner()
        calls = []
        def review(context, settings, **kwargs):
            calls.append(kwargs)
            return {'raise_alert': False, 'uncertainty': 'high', 'brief_reason': ''}
        monkeypatch.setattr(runner, 'review_borderline', review)
        orchestrator.vlm = runner
        settings = repo.get_settings()
        process(orchestrator, camera, job, settings, 1)
        alert = repo.list_alerts()[0]
        orchestrator._vlm_queue.drain()
        assert len(calls) == 1
        assert calls[0]['source_path'] == orchestrator.media_store.materialize(camera.storage_path)
        assert calls[0]['start_seconds'] == 1
        tasks = repo.list_queued_borderline_review_tasks()
        assert tasks == []
        assert repo.get_alert(alert.id).borderline_review_status == 'negative'
    finally:
        orchestrator.shutdown()


def test_queued_review_rejects_replaced_original_source(tmp_path, monkeypatch):
    from test_minicpm_incidents import pipeline, process
    orchestrator, repo, camera, job = pipeline(tmp_path)
    try:
        runner = MiniCpmOllamaRunner()
        monkeypatch.setattr(runner, 'review_borderline', lambda *a, **k: pytest.fail('Changed source must not be reviewed'))
        orchestrator.vlm = runner
        process(orchestrator, camera, job, repo.get_settings(), 1)
        alert = repo.list_alerts()[0]
        source = orchestrator.media_store.materialize(camera.storage_path)
        source.write_bytes(b'replaced after queueing')
        orchestrator._vlm_queue.drain()
        task = repo.list_borderline_review_tasks(alert.id)[0]
        assert task['status'] == 'failed' and task['error_code'] == 'ValueError'
    finally:
        orchestrator.shutdown()
