from datetime import datetime, timedelta, timezone

import pytest

from app.domain import (
    SensitivityMode,
    SystemSettings,
    ViolenceSettings,
    WeaponClassThresholds,
    WeaponThresholdProfile,
)
from app.domain import Camera
from app.policy import (
    active_violence_threshold,
    cooldown_allows,
    first_n_of_m_trigger_index,
    generate_segment_windows,
    is_borderline_candidate,
    n_of_m_trigger,
)
from app.repository import DemoRepository
from app.services import AlertService, WeaponFrameGate


def test_sliding_windows_cover_tail_without_duplicate() -> None:
    assert generate_segment_windows(12, 5, 2.5) == [
        (0.0, 5.0),
        (2.5, 7.5),
        (5.0, 10.0),
        (7.0, 12.0),
    ]


def test_short_video_is_one_partial_window() -> None:
    assert generate_segment_windows(3.2, 5, 2.5) == [(0.0, 3.2)]


def test_invalid_stride_is_rejected() -> None:
    with pytest.raises(ValueError):
        generate_segment_windows(10, 4, 5)


def test_mc3_4s2s_windows_add_end_aligned_tail_and_keep_short_clip() -> None:
    assert generate_segment_windows(11, 4, 2) == [
        (0.0, 4.0),
        (2.0, 6.0),
        (4.0, 8.0),
        (6.0, 10.0),
        (7.0, 11.0),
    ]
    assert generate_segment_windows(3.2, 4, 2) == [(0.0, 3.2)]


def test_threshold_roles_match_selected_mc3_policy_and_borderline_bounds() -> None:
    settings = ViolenceSettings()
    assert settings.segment_seconds == 4.0
    assert settings.stride_seconds == 2.0
    assert settings.detector_input_frames == 16
    assert active_violence_threshold(SensitivityMode.SENSITIVE, settings) == pytest.approx(0.9208608865737915)
    assert active_violence_threshold(SensitivityMode.MODERATE, settings) == pytest.approx(0.9954879879951477)
    assert active_violence_threshold(SensitivityMode.CERTAIN, settings) == pytest.approx(0.9997246861457825)
    assert is_borderline_candidate(0.95, settings.certain_threshold, settings.sensitive_threshold)
    assert is_borderline_candidate(0.997, settings.certain_threshold, settings.sensitive_threshold)
    assert not is_borderline_candidate(
        settings.certain_threshold, settings.certain_threshold, settings.sensitive_threshold
    )
    assert not is_borderline_candidate(
        settings.sensitive_threshold - 0.001,
        settings.certain_threshold,
        settings.sensitive_threshold,
    )


def test_weapon_n_of_m_uses_exact_recent_window() -> None:
    values = [0.2, 0.8, 0.75, 0.1, 0.9]
    assert n_of_m_trigger(values, threshold=0.6, n_required=3, m_window=5)
    assert first_n_of_m_trigger_index(values, 0.6, 3, 5) == 4
    assert not n_of_m_trigger(values[-4:], 0.6, 3, 5)


def test_cooldown_boundary() -> None:
    now = datetime.now(timezone.utc)
    assert cooldown_allows(None, now, 15)
    assert not cooldown_allows(now, now + timedelta(seconds=14.9), 15)
    assert cooldown_allows(now, now + timedelta(seconds=15), 15)


def test_borderline_promotion_and_source_timeline_incident_grouping() -> None:
    repository = DemoRepository()
    service = AlertService(repository)
    camera = Camera(
        name="Hall",
        storagePath="demo/cameras/test/source.mp4",
        originalFilename="source.mp4",
    )
    now = datetime.now(timezone.utc)

    assert service.record_violence_candidate(camera, 0.02, detected_at=now) is None
    first = service.record_violence_candidate(
        camera, 0.95, vlm_promoted=True, detected_at=now,
        source_start_seconds=10.0, source_end_seconds=15.0,
    )
    assert first is not None
    assert first.is_borderline_promoted
    second = service.record_violence_candidate(
        camera, 0.999, detected_at=now + timedelta(seconds=12.5),
        source_start_seconds=27.5, source_end_seconds=32.5,
    )
    assert second is not None
    assert second.id == first.id
    assert second.detection_count == 2
    assert second.max_confidence == pytest.approx(0.999)
    assert second.source_start_seconds == 10.0
    assert second.source_end_seconds == 32.5

    third = service.record_violence_candidate(
        camera, 0.9999, detected_at=now + timedelta(seconds=40),
        source_start_seconds=48.0, source_end_seconds=53.0,
    )
    assert third is not None
    assert third.id != first.id


def test_weapon_gate_waits_for_n_of_m() -> None:
    repository = DemoRepository()
    service = AlertService(repository)
    gate = WeaponFrameGate(service, repository)
    camera = Camera(
        name="Gate",
        storagePath="demo/cameras/gate/source.mp4",
        originalFilename="source.mp4",
    )
    outputs = [
        gate.observe(camera, value, model_version="validated-weapon-v1")
        for value in [0.2, 0.8, 0.75, 0.1, 0.9]
    ]
    assert outputs[:4] == [None, None, None, None]
    assert outputs[4] is not None
    assert outputs[4].max_confidence == pytest.approx(0.9)


def test_absent_weapon_class_does_not_hit_zero_threshold() -> None:
    repository = DemoRepository()
    service = AlertService(repository)
    gate = WeaponFrameGate(service, repository)
    camera = Camera(
        name="Zero threshold",
        storagePath="demo/cameras/zero/source.mp4",
        originalFilename="source.mp4",
    )
    zero_profile = WeaponThresholdProfile(knife=0.0, gun=0.0)
    settings = SystemSettings().model_copy(
        update={
            "weapon": SystemSettings().weapon.model_copy(
                update={
                    "n_required": 2,
                    "m_window": 3,
                    "class_thresholds": WeaponClassThresholds(
                        sensitive=zero_profile,
                        moderate=zero_profile,
                        certain=zero_profile,
                    ),
                }
            )
        }
    )
    results = [
        gate.observe(
            camera,
            0.0,
            class_scores={},
            model_version="test-weapon",
            settings=settings,
        )
        for _ in range(3)
    ]
    assert results == [None, None, None]


def test_weapon_thresholds_round_half_up_to_four_decimal_places() -> None:
    profile = WeaponThresholdProfile(knife=0.12345, gun=0.87654)
    assert profile.knife == 0.1235
    assert profile.gun == 0.8765
    assert WeaponThresholdProfile(knife=0.99995, gun=0.00005).model_dump() == {
        "knife": 1.0,
        "gun": 0.0001,
    }
    with pytest.raises(ValueError):
        WeaponThresholdProfile(knife=1.00001, gun=0.5)


def test_weapon_profiles_keep_independent_n_of_m_qualifications() -> None:
    repository = DemoRepository()
    service = AlertService(repository)
    gate = WeaponFrameGate(service, repository)
    camera = Camera(
        name="Profile N-of-M",
        storagePath="demo/cameras/profile/source.mp4",
        originalFilename="source.mp4",
    )
    settings = SystemSettings().model_copy(
        update={
            "weapon": SystemSettings().weapon.model_copy(
                update={
                    "n_required": 2,
                    "m_window": 3,
                    "class_thresholds": WeaponClassThresholds(
                        sensitive=WeaponThresholdProfile(knife=0.2, gun=0.2),
                        moderate=WeaponThresholdProfile(knife=0.4, gun=0.4),
                        certain=WeaponThresholdProfile(knife=0.9, gun=0.9),
                    ),
                }
            )
        }
    )
    alert = None
    for score in (0.95, 0.5, 0.5):
        alert = gate.observe(
            camera,
            score,
            class_scores={"knife": score},
            model_version="test-weapon",
            settings=settings,
        )
    assert alert is not None
    assert alert.detection_qualifications == {
        "weapon": {"sensitive": ["knife"], "moderate": ["knife"]}
    }
    assert alert.weapon_class_scores == {"knife": 0.95}


def test_borderline_candidate_is_recorded_without_provisional_notification_state() -> None:
    repository = DemoRepository()
    service = AlertService(repository)
    camera = Camera(
        name="Stairwell",
        storagePath="demo/cameras/stairwell/source.mp4",
        originalFilename="source.mp4",
    )
    alert = service.record_violence_candidate(
        camera, 0.95, source_start_seconds=10.0, source_end_seconds=15.0
    )
    assert alert is not None
    assert alert.confidence == 0.95
    assert not alert.is_provisional
    assert not alert.is_borderline_promoted
    assert alert.detection_qualifications == {"violence": ["sensitive"]}
    assert alert.detection_count == 1
