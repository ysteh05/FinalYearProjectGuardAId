"""Persisted 2-to-4 FPS transition, real decoding, and independent scene policy."""
import base64
import json
import sqlite3

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.inference.minicpm import MiniCpmOllamaRunner
from app.main import create_app
from app.sqlite_repository import SQLiteRepository
from scripts.set_borderline_sampling import update_borderline_sampling
from test_minicpm_incidents import runtime, video


def test_existing_config_changes_only_borderline_fps_and_is_idempotent(tmp_path):
    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    before = repository.get_settings()
    assert before.vlm.input_fps == 2
    result = update_borderline_sampling(config.database_path, 4)
    assert result['config_version'] == before.config_version + 1
    assert result['unrelated_settings_preserved']
    assert result['other_database_tables_unchanged']
    restored = SQLiteRepository(config).get_settings()
    assert restored.vlm.input_fps == 4
    assert restored.weapon == before.weapon and restored.violence == before.violence
    with sqlite3.connect(config.database_path) as c:
        assert c.execute('SELECT vlm_input_fps FROM config_versions ORDER BY version_number DESC LIMIT 2').fetchall() == [(4.0,), (2.0,)]
    assert update_borderline_sampling(config.database_path, 4)['changed'] is False
    assert SQLiteRepository(config).get_settings().config_version == restored.config_version


@pytest.mark.parametrize('fps', [0, -1, 13, float('nan'), float('inf')])
def test_invalid_fps_cannot_create_a_configuration_version(tmp_path, fps):
    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    before = repository.get_settings()
    with pytest.raises(ValueError):
        update_borderline_sampling(config.database_path, fps)
    assert repository.get_settings() == before
    assert not list(tmp_path.glob('*.before-borderline-*'))


def test_saved_fps_is_exposed_by_the_dashboard_settings_api(tmp_path):
    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    update_borderline_sampling(config.database_path, 4)
    app = create_app(config, repository)
    try:
        response = TestClient(app).get('/api/settings')
        assert response.status_code == 200
        assert response.json()['vlm']['inputFps'] == 4
    finally:
        app.state.orchestrator.shutdown()


@pytest.mark.parametrize('width,height', [(640, 360), (360, 640), (360, 360)])
def test_real_decode_sends_sixteen_360p_frames_without_changing_scenes(tmp_path, width, height):
    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    update_borderline_sampling(config.database_path, 4)
    settings = SQLiteRepository(config).get_settings().vlm
    path = tmp_path / 'four-seconds.mp4'
    video(path, 4, width=width, height=height, fps=8)
    runner = MiniCpmOllamaRunner()
    runner._loaded = True
    calls = []

    def api(endpoint, payload, **kwargs):
        calls.append(payload)
        return {'message': {'content': '1'}, 'done_reason': 'stop'}

    runner._api = api
    assert runner.review_borderline(path, settings)['raise_alert'] is True
    payload = calls[0]
    images = payload['messages'][0]['images']
    assert len(images) == 16
    assert runner.last_call_metadata['fps'] == 4
    assert runner.last_call_metadata['sample_times'] == [i * .25 for i in range(16)]
    assert payload['think'] is False
    assert runner.last_call_metadata['think_requested'] is False
    assert 'format' not in payload
    for image in images:
        decoded = cv2.imdecode(np.frombuffer(base64.b64decode(image), np.uint8), cv2.IMREAD_COLOR)
        assert min(decoded.shape[:2]) == 360
        assert decoded.shape[1] / decoded.shape[0] == pytest.approx(width / height)
    scene_calls = []
    runner._chat = lambda *args, **kwargs: (scene_calls.append(kwargs) or json.dumps({
        'summary': 'People stand in a room.', 'details': ['A person stands near a wall.'],
        'uncertainty': 'high',
    }))
    runner.describe_scene(path, settings)
    assert runner.last_call_metadata['fps'] == 2
    assert runner.last_call_metadata['frame_count'] == 8
    assert runner.last_call_metadata['think'] is True
    assert scene_calls[0]['think'] is True
