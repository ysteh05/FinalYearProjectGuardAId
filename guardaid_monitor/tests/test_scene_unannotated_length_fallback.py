"""Scene contract regressions with real JPEG bytes and no database/model service.

Prompt assertions cover instructions only: they cannot prove that a live VLM
recognizes an object or avoids copying an example.
"""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from app.domain import Camera, SceneSummary, SystemSettings
from app.inference.media_pipeline import BackgroundMediaPipeline
from app.inference.minicpm import (
    MiniCpmOllamaRunner, _strict_object, scene_length_fallback,
)
from app.inference.scene_reliability import (
    SceneFormatError, SceneLengthError, SceneOutputTruncated,
)
from app.media import LocalMediaStore
from app.vlm_contract import (
    BINARY_PROMPT_VERSION, BINARY_SYSTEM_PROMPT, BINARY_USER_PROMPT,
    SCENE_SCHEMA, SCENE_SYSTEM_PROMPT, scene_prompt,
)


GOOD = {
    'summary': 'A person carries an indistinct object.',
    'details': ['The object is partly obscured.', 'Its identity cannot be confirmed.'],
    'uncertainty': 'high',
}
LONG = {
    'summary': ' '.join(['A person'] + ['carefully'] * 30 + ['carries an obscured object.']),
    'details': [
        ' '.join(['The object remains'] + ['partly'] * 14 + ['obscured.']),
        'Its identity cannot be confirmed.',
        'Another person stands near the doorway.',
    ],
    'uncertainty': 'medium',
}


def as_result(value):
    return dict(description=value['summary'], visible_evidence=value['details'],
                uncertainty=value['uncertainty'])


def jpeg(image):
    ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok
    return encoded.tobytes()


@pytest.fixture
def image():
    return base64.b64encode(jpeg(np.full((48, 64, 3), 83, np.uint8))).decode('ascii')


def scripted_runner(monkeypatch, replies):
    """Exercise the real chat payload, response parsing and retry code."""
    runner = MiniCpmOllamaRunner()
    runner._loaded = True
    replies = iter(replies)
    calls = []

    def api(endpoint, payload, **kwargs):
        assert endpoint == '/api/chat'
        calls.append(deepcopy(payload))
        content, reason = next(replies)
        return {'message': {'content': content}, 'done_reason': reason,
                'eval_count': 1024 if reason == 'length' else 100}

    monkeypatch.setattr(runner, '_api', api)
    return runner, calls


@pytest.mark.parametrize('field,value', [
    ('summary', ' '.join(['word'] * 26) + '.'),
    ('summary', 'x' * 241 + '.'),
    ('details', [' '.join(['word'] * 13) + '.']),
    ('details', ['x' * 81 + '.']),
])
def test_allow_long_relaxes_each_prose_limit_without_changing_contents(field, value):
    candidate = {**GOOD, field: value}
    raw = json.dumps(candidate)
    with pytest.raises(SceneLengthError):
        _strict_object(raw)
    assert _strict_object(raw, allow_long=True) == candidate


def test_resumed_attempt_cannot_restore_legacy_512_token_override(monkeypatch, image):
    runner, calls = scripted_runner(monkeypatch, [(json.dumps(GOOD), 'stop')])
    state = {'attempts': 2, 'output_tokens': 512}
    assert runner._describe_images([image], SystemSettings().vlm, scene_prompt(), state) == as_result(GOOD)
    assert calls[0]['options']['num_predict'] == 1024


INVALID = [
    ('invalid-json', '{'),
    ('array', '[]'),
    ('missing-key', json.dumps({'summary': 'A person stands.', 'details': []})),
    ('extra-key', json.dumps({**LONG, 'confidence': .99})),
    ('duplicate-key', '{"summary":"A.","summary":"B.","details":[],"uncertainty":"low"}'),
    ('empty-summary', json.dumps({**LONG, 'summary': '  '})),
    ('summary-type', json.dumps({**LONG, 'summary': 7})),
    ('details-type', json.dumps({**LONG, 'details': 'A person stands.'})),
    ('detail-type', json.dumps({**LONG, 'details': [None]})),
    ('unfinished-detail', json.dumps({**LONG, 'details': ['The person holds an obj']})),
    ('comma', json.dumps({**LONG, 'details': ['One person stands, another sits.']})),
    ('semicolon', json.dumps({**LONG, 'details': ['One person stands; another sits.']})),
    ('two-sentences', json.dumps({**LONG, 'details': ['One person stands. Another sits.']})),
    ('empty-detail', json.dumps({**LONG, 'details': ['']})),
    ('uncertainty-enum', json.dumps({**LONG, 'uncertainty': 'certain'})),
    ('uncertainty-type', json.dumps({**LONG, 'uncertainty': ['high']})),
]


@pytest.mark.parametrize('name,raw', INVALID, ids=[case[0] for case in INVALID])
@pytest.mark.parametrize('allow_long', [False, True])
def test_structure_and_nonlength_errors_never_become_length_candidates(name, raw, allow_long):
    with pytest.raises(SceneFormatError) as failure:
        _strict_object(raw, allow_long=allow_long)
    assert not isinstance(failure.value, SceneLengthError), name


def test_schema_and_summary_model_preserve_complete_long_fallback():
    properties = SCENE_SCHEMA['properties']
    assert set(SCENE_SCHEMA['required']) == {'summary', 'details', 'uncertainty'}
    assert SCENE_SCHEMA['additionalProperties'] is False
    assert properties['summary']['type'] == 'string'
    assert properties['summary']['minLength'] == 1
    assert 'maxLength' not in properties['summary']
    assert properties['details']['type'] == 'array'
    assert 'maxItems' not in properties['details']
    assert properties['details']['items']['type'] == 'string'
    assert 'maxLength' not in properties['details']['items']
    assert re.fullmatch(properties['details']['items']['pattern'], LONG['details'][0])
    assert set(properties['uncertainty']['enum']) == {'low', 'medium', 'high'}
    result = as_result(LONG)
    assert SceneSummary.model_validate(LONG).model_dump() == result
    assert SceneSummary.model_validate(result).model_dump() == result
    for invalid in ({**result, 'description': ''}, {**result, 'uncertainty': 'certain'},
                    {**result, 'visible_evidence': [42]}):
        with pytest.raises(ValidationError):
            SceneSummary.model_validate(invalid)


@pytest.mark.parametrize('last', [json.dumps(LONG), '{"summary":', json.dumps({**GOOD, 'details': ['No ending']})])
def test_standalone_third_failure_uses_latest_complete_candidate(monkeypatch, image, last):
    older = {**LONG, 'summary': LONG['summary'].replace('obscured', 'small')}
    runner, calls = scripted_runner(monkeypatch, [
        (json.dumps(older), 'stop'), (json.dumps(LONG), 'stop'), (last, 'stop'),
    ])
    settings = SystemSettings().vlm
    assert settings.max_output_tokens == 1024
    assert runner._describe_images([image], settings, scene_prompt()) == as_result(LONG)
    assert [call['options']['num_predict'] for call in calls] == [1024] * 3
    assert all(call['messages'][1]['images'] == [image] for call in calls)


def test_valid_retry_wins_over_older_length_candidate(monkeypatch, image):
    runner, calls = scripted_runner(monkeypatch, [
        (json.dumps(LONG), 'stop'), (json.dumps(GOOD), 'stop'),
    ])
    assert runner._describe_images([image], SystemSettings().vlm, scene_prompt()) == as_result(GOOD)
    assert len(calls) == 2


def test_external_attempt_state_preserves_candidate_through_json_roundtrip(monkeypatch, image):
    runner, calls = scripted_runner(monkeypatch, [
        (json.dumps(LONG), 'stop'), ('{"summary":', 'stop'),
        (json.dumps({**GOOD, 'uncertainty': 'certain'}), 'stop'),
    ])
    state = {'attempts': 1, 'output_tokens': 1024}
    for attempt in range(1, 4):
        state['attempts'] = attempt
        with pytest.raises(SceneFormatError):
            runner._describe_images([image], SystemSettings().vlm, scene_prompt(), state)
        assert len(calls) == attempt  # External caller owns the three-attempt budget.
        assert state['length_candidate'] == LONG
        assert state['length_candidate_attempt'] == 1
        state = json.loads(json.dumps(state, allow_nan=False))
    assert scene_length_fallback(state) == as_result(LONG)
    assert state['length_relaxed'] is True
    assert state['selected_candidate_attempt'] == 1
    assert [call['options']['num_predict'] for call in calls] == [1024] * 3


def test_external_attempt_state_replaces_candidate_with_latest(monkeypatch, image):
    newer = {**LONG, 'uncertainty': 'low', 'details': LONG['details'] + ['A person steps back.']}
    runner, calls = scripted_runner(monkeypatch, [
        (json.dumps(LONG), 'stop'), (json.dumps(newer), 'stop'), ('{', 'stop'),
    ])
    state = {'output_tokens': 1024}
    for attempt in range(1, 4):
        state['attempts'] = attempt
        with pytest.raises(SceneFormatError):
            runner._describe_images([image], SystemSettings().vlm, scene_prompt(), state)
    assert len(calls) == 3
    assert state['length_candidate'] == newer
    assert scene_length_fallback(state) == as_result(newer)
    assert state['selected_candidate_attempt'] == 2


def test_complete_token_boundary_candidate_survives_later_malformed_json(monkeypatch, image):
    runner, calls = scripted_runner(monkeypatch, [
        (json.dumps(LONG), 'length'), ('{', 'stop'), ('{', 'stop'),
    ])
    assert runner._describe_images([image], SystemSettings().vlm, scene_prompt()) == as_result(LONG)
    assert [call['options']['num_predict'] for call in calls] == [1024] * 3


@pytest.mark.parametrize('reason,error', [('stop', SceneFormatError), ('length', SceneOutputTruncated)])
def test_three_unusable_replies_fail_without_synthesized_fallback(monkeypatch, image, reason, error):
    runner, calls = scripted_runner(monkeypatch, [('{', reason)] * 3)
    with pytest.raises(error):
        runner._describe_images([image], SystemSettings().vlm, scene_prompt())
    assert len(calls) == 3
    assert [call['options']['num_predict'] for call in calls] == [1024] * 3
    assert scene_length_fallback({}) is None


def test_saved_candidate_is_revalidated_before_selection():
    with pytest.raises(SceneFormatError):
        scene_length_fallback({'length_candidate': {**LONG, 'details': ['Unfinished']}})


@pytest.mark.parametrize('count', [1, 8, 10])
def test_weapon_chat_receives_exact_raw_sidecar_bytes(tmp_path, monkeypatch, count):
    annotated_paths, raw_contents, annotated_contents = [], [], []
    for index in range(count):
        frame = np.full((48, 64, 3), 40 + index * 8, np.uint8)
        raw_contents.append(jpeg(frame))
        cv2.rectangle(frame, (3, 3), (40, 40), (0, 0, 255), 3)
        annotated_contents.append(jpeg(frame))
        path = tmp_path / f'photo.{index}.jpg'
        path.write_bytes(annotated_contents[-1])
        path.with_name(path.stem + '.raw.jpg').write_bytes(raw_contents[-1])
        annotated_paths.append(path)
    runner, calls = scripted_runner(monkeypatch, [(json.dumps(GOOD), 'stop')])
    result = runner.describe_weapon_scene(annotated_paths, SystemSettings().vlm,
                                         detected_classes=['gun'], sample_times=list(range(count)))
    sent = calls[0]['messages'][1]['images']
    assert calls[0]['think'] is True
    assert [base64.b64decode(value) for value in sent] == raw_contents[:8]
    assert all(raw != annotated for raw, annotated in zip(raw_contents, annotated_contents))
    assert [path.read_bytes() for path in annotated_paths] == annotated_contents
    assert runner.last_call_metadata['input_type'] == 'unannotated_weapon_images'
    assert runner.last_call_metadata['sample_times'] == list(range(min(count, 8)))
    assert runner.last_call_metadata['evidence_count'] == min(count, 8)
    assert runner.scene_trace['evidence'] == [
        {'sha256': hashlib.sha256(content).hexdigest(), 'dimensions': [64, 48]}
        for content in raw_contents[:8]
    ]
    assert runner.scene_trace['think'] is True
    assert runner.last_call_metadata['think_requested'] is True
    assert result == as_result(GOOD)


@pytest.mark.parametrize('raw_content', [None, b'not a JPEG'])
def test_missing_or_unreadable_raw_sidecar_never_uses_annotation(tmp_path, monkeypatch, raw_content):
    path = tmp_path / 'annotated.jpg'
    path.write_bytes(jpeg(np.zeros((48, 64, 3), np.uint8)))
    if raw_content is not None:
        path.with_name(path.stem + '.raw.jpg').write_bytes(raw_content)
    runner, calls = scripted_runner(monkeypatch, [])
    with pytest.raises(ValueError):
        runner.describe_weapon_scene([path], SystemSettings().vlm)
    assert not calls


def test_background_evidence_preserves_annotation_and_same_frame_raw_bytes(tmp_path):
    """Real evidence writer -> raw sidecar -> chat; repository stays in memory."""
    frame = np.full((48, 64, 3), 87, np.uint8)
    expected_raw = jpeg(frame)
    alert = SimpleNamespace(id='alert_fixture', evidence=[])
    pipeline = BackgroundMediaPipeline()
    pipeline.media_store = LocalMediaStore(SimpleNamespace(data_dir=tmp_path, max_upload_mb=1))

    def save_evidence(evidence, size):
        alert.evidence.append(evidence)

    pipeline.repository = SimpleNamespace(get_alert=lambda alert_id: alert, save_evidence=save_evidence)
    pipeline._source_frames = lambda *args, **kwargs: iter([frame])
    pipeline._timing = SimpleNamespace(record=lambda *args: None)

    def annotate(image, detections):
        # Deliberately mutate the source to catch raw encoding after annotation.
        cv2.rectangle(image, (3, 3), (40, 40), (0, 0, 255), 3)
        return image

    pipeline.weapon = SimpleNamespace(annotate=annotate)
    camera = Camera(name='Fixture', storage_path='unused.mp4', original_filename='unused.mp4')
    pipeline._handle_media({'id': 'media_fixture', 'kind': 'weapon_evidence'}, {
        'alert_id': alert.id, 'camera': camera.model_dump(mode='json'),
        'settings': SystemSettings().model_dump(mode='json'), 'seconds': 1.5,
        'frame_index': 3, 'detections': [],
    })
    assert len(alert.evidence) == 1
    evidence = alert.evidence[0]
    assert evidence.media_type == 'weapon_screenshot'
    path = pipeline.media_store.materialize(evidence.storage_path)
    assert path.read_bytes() == jpeg(frame)
    assert path.read_bytes() != expected_raw
    assert path.with_name(path.stem + '.raw.jpg').read_bytes() == expected_raw
    calls = []
    runner = MiniCpmOllamaRunner()
    runner._chat = lambda images, settings, **kwargs: (calls.append(images) or json.dumps(GOOD))
    runner.describe_weapon_scene([path], SystemSettings().vlm)
    assert [base64.b64decode(value) for value in calls[0]] == [expected_raw]


def test_weapon_prompt_has_independent_semantic_instructions_without_numeric_confidence():
    prompt = scene_prompt('weapon', ['knife', 'gun', 'gun 0.99', 'confidence 99%'])
    combined = (SCENE_SYSTEM_PROMPT + '\n' + prompt).lower()
    assert 'suspected classes: gun, knife' in prompt.lower()
    assert 'unannotated' in combined and 'chronological' in combined
    assert 'not proof' in combined and 'independently inspect' in combined
    assert 'false positive' in combined and 'cannot be confirmed' in combined
    assert 'held' in combined and 'position' in combined
    assert re.search(r'ignore[^.]*confidence', combined)
    assert '0.99' not in combined and '99%' not in combined
    assert 'inside the boxes' not in combined


@pytest.mark.parametrize('alert_type', ['weapon', 'violence', 'physical_contact_bullying'])
def test_example_copying_is_only_a_prompt_contract(alert_type):
    prompt = scene_prompt(alert_type).lower()
    assert re.search(r'(?:do not|never) copy[^.]*example', prompt)
    assert 'never invent details to meet a count' in ' '.join(prompt.split())
    # No mocked model response here can demonstrate actual visual grounding.


@pytest.mark.parametrize('answer', ['0', '1'])
def test_binary_review_uses_selected_source_window_request_contract(monkeypatch, answer):
    runner, calls = scripted_runner(monkeypatch, [(answer, 'stop')])
    monkeypatch.setattr('app.inference.minicpm.prepare_images', lambda path, **kwargs: (
        ['unchanged-frame'] * 16, {'fps': 4, 'sample_times': [i / 4 for i in range(16)]},
    ))
    result = runner.review_borderline(Path('unused.mp4'), SystemSettings().vlm)
    payload = calls[0]
    assert BINARY_PROMPT_VERSION == 'minicpm_binary_source_window_v2'
    assert BINARY_SYSTEM_PROMPT == 'Classify visual actions. Ignore audio. Reply only 0 or 1. Do not think.'
    assert BINARY_USER_PROMPT == (
        'Return 1 if the sequence shows credible aggressive physical contact or an apparent '
        'physical attack, such as punching, kicking, striking, forceful pushing, choking, '
        'aggressive grappling, dragging or repeated assaultive contact. Return 0 for ordinary '
        'movement, sports, non-aggressive play, dancing, crowding, accidental contact, a fall '
        'without attack, or frames that do not visibly support violence. Use only the images, '
        'not audio. Reply with exactly one character: 0 or 1.'
    )
    from app.inference.borderline_protocol import build_prompt
    assert len(payload['messages']) == 1
    assert payload['messages'][0]['content'] == build_prompt(4, 16)
    assert payload['messages'][0]['images'] == ['unchanged-frame'] * 16
    assert 'format' not in payload
    assert payload['options']['num_predict'] == 12
    assert payload['think'] is False
    assert runner.last_call_metadata['think_requested'] is False
    assert result['raise_alert'] is (answer == '1')
