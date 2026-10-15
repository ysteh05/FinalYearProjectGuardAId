import json
import itertools
import math
from pathlib import Path
import sqlite3
import struct
import wave
from threading import RLock

import pytest

from app.config import RuntimeConfig
from app.domain import (
    AnalysisJob,
    Camera,
    SystemSettings,
    WeaponClassThresholds,
    WeaponThresholdProfile,
)
from app.media import LocalMediaStore
from app.sqlite_repository import SQLiteRepository
from app.repository import DemoRepository
from app.services import AlertService, WeaponFrameGate
from app.inference.vlm import (
    QwenNativeVideoRunner,
    SCENE_USER_PROMPT,
    _borderline_object,
    _clean_model_text,
    _scene_summary_from_output,
)
from app.inference.orchestrator import InferenceOrchestrator
from app.inference.weapon import WeaponDetection, Yolov9WeaponRunner


def runtime(tmp_path: Path) -> RuntimeConfig:
    return RuntimeConfig(
        persistence_mode="sqlite",
        data_dir=tmp_path,
        database_path=tmp_path / "guardaid.db",
        max_upload_mb=5,
        auth_mode="disabled",
        notification_mode="console",
        firebase_project_id=None,
        firebase_credentials=None,
        violence_checkpoint=tmp_path / "mc3.pt",
        weapon_checkpoint=tmp_path / "best.zip",
        hf_cache=tmp_path / "hf",
    )


def test_default_weapon_profiles_match_the_active_two_class_model() -> None:
    thresholds = SystemSettings().weapon.class_thresholds.model_dump()
    assert sum(len(profile) for profile in thresholds.values()) == 6
    assert all(set(profile) == {"knife", "gun"} for profile in thresholds.values())
    assert thresholds["sensitive"]["knife"] == 0.2816
    assert thresholds["sensitive"]["gun"] == 0.1262
    assert thresholds["moderate"]["knife"] == 0.5161
    assert thresholds["moderate"]["gun"] == 0.3641
    assert thresholds["certain"]["knife"] == 0.6588
    assert thresholds["certain"]["gun"] == 0.6444


def test_weapon_incident_keeps_each_annotated_positive_frame(tmp_path: Path) -> None:
    import numpy as np

    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    camera = Camera(
        id="camera_weapon_gallery",
        name="Weapon gallery camera",
        storage_path="cameras/weapon-gallery/source.mp4",
        original_filename="source.mp4",
    )
    repository.save_camera(camera)
    settings = repository.get_settings()
    profile = WeaponThresholdProfile(knife=0.4, gun=0.4)
    settings = settings.model_copy(
        update={
            "weapon": settings.weapon.model_copy(
                update={
                    "n_required": 2,
                    "m_window": 3,
                    "class_thresholds": WeaponClassThresholds(
                        sensitive=profile, moderate=profile, certain=profile
                    ),
                }
            ),
            "vlm": settings.vlm.model_copy(
                update={"scene_description_enabled": False}
            ),
        }
    )

    class FakeWeapon:
        MODEL_VERSION = "test-weapon"
        EXPECTED_CLASSES = ("knife", "gun")

        def __init__(self) -> None:
            self.annotated_classes: list[set[str]] = []
            self.inference_calls = 0

        def infer(self, *_args, **_kwargs):
            self.inference_calls += 1
            if self.inference_calls == 3:
                return []
            return [
                WeaponDetection(0, "knife", 0.8, (2, 3, 24, 30), 0.4),
                WeaponDetection(1, "gun", 0.85, (28, 3, 50, 30), 0.4),
            ]

        def annotate(self, frame, detections):
            self.annotated_classes.append({item.class_name for item in detections})
            return frame

    class FakeNotifications:
        def dispatch(self, *_args, **_kwargs):
            return 0

    orchestrator = object.__new__(InferenceOrchestrator)
    orchestrator.runtime = config
    orchestrator.repository = repository
    orchestrator.media_store = LocalMediaStore(config)
    orchestrator.weapon = FakeWeapon()
    orchestrator.weapon_gate = WeaponFrameGate(AlertService(repository), repository)
    orchestrator.notifications = FakeNotifications()
    orchestrator._lock = RLock()
    orchestrator._weapon_evidence_candidates = {}
    orchestrator._jobs = {
        "job_weapon_gallery": AnalysisJob(
            id="job_weapon_gallery",
            camera_id=camera.id,
            camera_name=camera.name,
            duration_seconds=4,
        )
    }
    frame = np.zeros((48, 64, 3), dtype=np.uint8)

    orchestrator._process_weapon_frame(
        "job_weapon_gallery", camera, frame, 0.5, [], 4.0, settings
    )
    orchestrator._process_weapon_frame(
        "job_weapon_gallery", camera, frame, 0.75, [], 4.0, settings
    )
    orchestrator._process_weapon_frame(
        "job_weapon_gallery", camera, frame, 1.0, [], 4.0, settings
    )
    orchestrator._process_weapon_frame(
        "job_weapon_gallery", camera, frame, 1.25, [], 4.0, settings
    )

    alerts = repository.list_alerts()
    assert len(alerts) == 1
    assert alerts[0].detection_count == 2
    assert alerts[0].source_start_seconds == 0.5
    assert alerts[0].source_end_seconds == 1.25
    assert [item.start_seconds for item in alerts[0].evidence] == [0.5, 0.75, 1.25]
    assert orchestrator.weapon.annotated_classes == [
        {"knife", "gun"},
        {"knife", "gun"},
        {"knife", "gun"},
    ]


def test_weapon_candidate_prefilter_can_reach_a_zero_configured_class_threshold(
    tmp_path: Path,
) -> None:
    runner = Yolov9WeaponRunner(tmp_path / "best.pt", tmp_path / "yolov9")
    assert runner._candidate_confidence_floor(
        0.001, {"knife": 0.0, "gun": 0.4}
    ) == 0.0
    assert runner._candidate_confidence_floor(
        0.001, {"knife": 0.2, "gun": 0.4}
    ) == 0.001


def test_default_runtime_uses_selected_nine_source_epoch6_and_calibration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GUARDAID_WEAPON_CHECKPOINT", raising=False)
    monkeypatch.delenv("GUARDAID_WEAPON_THRESHOLDS", raising=False)
    config = RuntimeConfig.from_env()

    assert config.weapon_checkpoint is not None
    assert config.weapon_checkpoint.name == "best_inference.pt"
    assert "sel50_20261011_193754_83560d" in config.weapon_checkpoint.parts
    assert config.weapon_checkpoint.is_file()
    assert config.weapon_thresholds_path.name == "sensitivity_thresholds.json"
    assert "sel50_20261011_193754_83560d" in config.weapon_thresholds_path.parts
    assert config.weapon_thresholds_path.is_file()
    report = json.loads(config.weapon_thresholds_path.read_text(encoding="utf-8"))
    profiles = {item["profile"].casefold(): item["thresholds"] for item in report["profiles"]}
    assert profiles["sensitive"] == {
        "knife": 0.28156810998916626,
        "gun": 0.12619423866271973,
    }
    assert profiles["moderate"] == {
        "knife": 0.5161489248275757,
        "gun": 0.3640787899494171,
    }
    assert profiles["certain"] == {
        "knife": 0.6588038802146912,
        "gun": 0.6444171071052551,
    }


def test_mc3_inference_samples_sixteen_uniform_bin_centres() -> None:
    from app.inference.orchestrator import InferenceOrchestrator

    ring = [(index / 4, index) for index in range(17)]
    samples = InferenceOrchestrator._uniform_frames(ring, 0.0, 4.0, 16)
    assert samples == list(range(16))


def test_sqlite_round_trips_versioned_runtime_settings(tmp_path: Path) -> None:
    repository = SQLiteRepository(runtime(tmp_path))
    assert repository.schema_version() == 14
    settings = repository.get_settings()
    certain = settings.weapon.class_thresholds.certain.model_copy(update={"gun": 0.61})
    profiles = settings.weapon.class_thresholds.model_copy(update={"certain": certain})
    weapon = settings.weapon.model_copy(update={"class_thresholds": profiles})
    escalation = settings.alert_notification_escalation.model_copy(
        update={
            "same_audience_repeat_after_seconds": 180,
            "all_users_escalation_after_seconds": 360,
        }
    )
    repository.save_settings(
        settings.model_copy(
            update={
                "weapon": weapon,
                "alert_notification_escalation": escalation,
                "config_version": 2,
                "updated_by": "test",
            }
        )
    )
    saved = repository.get_settings()
    assert saved.config_version == 2
    assert saved.weapon.class_thresholds.certain.gun == 0.61
    assert saved.weapon.nms_candidate_floor == 0.001
    assert saved.violence.incident_merge_gap_seconds == 15.0
    assert saved.violence.cooldown_seconds == 15
    assert saved.weapon.incident_merge_gap_seconds == 15.0
    assert saved.weapon.cooldown_seconds == 15
    assert saved.alert_notification_escalation.same_audience_repeat_after_seconds == 180
    assert saved.alert_notification_escalation.all_users_escalation_after_seconds == 360
    assert saved.vlm.scene_description_frames == 20


def test_sqlite_migrates_existing_weapon_thresholds_to_four_decimals(
    tmp_path: Path,
) -> None:
    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    version = repository.get_settings().config_version
    stored = {
        "sensitive": {"knife": 0.12345, "gun": 0.87654},
        "moderate": {"knife": 0.33335, "gun": 0.77774},
        "certain": {"knife": 0.99995, "gun": 0.00005},
    }
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            """
            UPDATE weapon_configs SET class_thresholds_json=?
            WHERE config_version_id=(
                SELECT id FROM config_versions ORDER BY version_number DESC LIMIT 1
            )
            """,
            (json.dumps(stored),),
        )

    migrated = SQLiteRepository(config).get_settings()
    assert migrated.config_version == version + 1
    assert migrated.updated_by == "migration-weapon-threshold-4dp"
    assert migrated.weapon.class_thresholds.sensitive.knife == 0.1235
    assert migrated.weapon.class_thresholds.sensitive.gun == 0.8765
    assert migrated.weapon.class_thresholds.moderate.knife == 0.3334
    assert migrated.weapon.class_thresholds.certain.knife == 1.0
    assert migrated.weapon.class_thresholds.certain.gun == 0.0001
    with sqlite3.connect(config.database_path) as connection:
        normalised = json.loads(
            connection.execute(
                "SELECT class_thresholds_json FROM weapon_configs "
                "WHERE config_version_id=(SELECT id FROM config_versions "
                "ORDER BY version_number DESC LIMIT 1)"
            ).fetchone()[0]
        )
    assert normalised["sensitive"]["knife"] == 0.1235


def test_sqlite_migrates_legacy_weapon_defaults_and_preserves_custom_values(
    tmp_path: Path,
) -> None:
    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    initial = repository.get_settings()
    old_version = initial.config_version
    assert initial.weapon.class_thresholds.sensitive.knife == 0.2816
    assert initial.weapon.class_thresholds.sensitive.gun == 0.1262
    legacy = {
        "sensitive": {"knife": 0.25, "long_gun": 0.4412950575351715},
        "moderate": {"knife": 0.2014363706111908, "long_gun": 0.45},
        "certain": {"knife": 0.30376800894737244, "long_gun": 0.5570604205131531},
    }
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "UPDATE weapon_configs SET class_thresholds_json=?",
            (json.dumps(legacy),),
        )

    migrated = SQLiteRepository(config).get_settings()
    assert migrated.config_version == old_version + 1
    assert migrated.updated_by == "migration-yolov9-t-selected-9source-f05-ft-20261011-193754-83560d-e06"
    # Untouched legacy values are replaced by the new model's validation defaults.
    assert migrated.weapon.class_thresholds.sensitive.gun == 0.1262
    assert migrated.weapon.class_thresholds.moderate.knife == 0.5161
    assert migrated.weapon.class_thresholds.moderate.gun == pytest.approx(0.45)
    assert migrated.weapon.class_thresholds.certain.knife == 0.6588
    assert migrated.weapon.class_thresholds.certain.gun == 0.6444
    # Explicit operator edits survive the class-key/model migration.
    assert migrated.weapon.class_thresholds.sensitive.knife == pytest.approx(0.25)


def test_sqlite_migrates_brev_defaults_to_hard_negative_and_preserves_custom_values(
    tmp_path: Path,
) -> None:
    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    initial = repository.get_settings()
    old_version = initial.config_version
    brev_defaults = {
        "sensitive": {"knife": 0.15379296243190765, "gun": 0.22764818370342255},
        "moderate": {"knife": 0.5272320508956909, "gun": 0.41140204668045044},
        "certain": {"knife": 0.6442655324935913, "gun": 0.5589425563812256},
    }
    # This one value was customized in the live installation and must survive.
    brev_defaults["moderate"]["gun"] = 0.39
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "UPDATE weapon_configs SET class_thresholds_json=?",
            (json.dumps(brev_defaults),),
        )

    migrated = SQLiteRepository(config).get_settings()
    assert migrated.config_version == old_version + 1
    assert migrated.updated_by == "migration-yolov9-t-selected-9source-f05-ft-20261011-193754-83560d-e06"
    assert migrated.weapon.class_thresholds.sensitive.knife == 0.2816
    assert migrated.weapon.class_thresholds.sensitive.gun == 0.1262
    assert migrated.weapon.class_thresholds.moderate.knife == 0.5161
    assert migrated.weapon.class_thresholds.moderate.gun == pytest.approx(0.39)
    assert migrated.weapon.class_thresholds.certain.knife == 0.6588
    assert migrated.weapon.class_thresholds.certain.gun == 0.6444


def test_sqlite_migrates_mall_defaults_to_hard_negative_and_preserves_custom_values(
    tmp_path: Path,
) -> None:
    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    initial = repository.get_settings()
    old_version = initial.config_version
    mall_defaults = {
        "sensitive": {"knife": 0.3433084189891815, "gun": 0.19067250192165375},
        "moderate": {"knife": 0.4541761577129364, "gun": 0.41610413789749146},
        "certain": {"knife": 0.7339307069778442, "gun": 0.525465726852417},
    }
    mall_defaults["certain"]["gun"] = 0.53
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "UPDATE weapon_configs SET class_thresholds_json=?",
            (json.dumps(mall_defaults),),
        )

    migrated = SQLiteRepository(config).get_settings()
    assert migrated.config_version == old_version + 1
    assert migrated.updated_by == "migration-yolov9-t-selected-9source-f05-ft-20261011-193754-83560d-e06"
    assert migrated.weapon.class_thresholds.sensitive.knife == 0.2816
    assert migrated.weapon.class_thresholds.sensitive.gun == 0.1262
    assert migrated.weapon.class_thresholds.moderate.knife == 0.5161
    assert migrated.weapon.class_thresholds.moderate.gun == 0.3641
    assert migrated.weapon.class_thresholds.certain.knife == 0.6588
    assert migrated.weapon.class_thresholds.certain.gun == pytest.approx(0.53)


def test_sqlite_migrates_previous_hard_negative_defaults_to_epoch6_and_keeps_custom_values(
    tmp_path: Path,
) -> None:
    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    initial = repository.get_settings()
    old_version = initial.config_version
    previous_hard_negative = {
        "sensitive": {"knife": 0.2170601636171341, "gun": 0.18463186919689178},
        "moderate": {"knife": 0.44626009464263916, "gun": 0.4098173975944519},
        "certain": {"knife": 0.6011608242988586, "gun": 0.4950620234012604},
    }
    previous_hard_negative["certain"]["gun"] = 0.58
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "UPDATE weapon_configs SET class_thresholds_json=?",
            (json.dumps(previous_hard_negative),),
        )

    migrated = SQLiteRepository(config).get_settings()
    assert migrated.config_version == old_version + 1
    assert migrated.updated_by == "migration-yolov9-t-selected-9source-f05-ft-20261011-193754-83560d-e06"
    assert migrated.weapon.class_thresholds.sensitive.knife == 0.2816
    assert migrated.weapon.class_thresholds.sensitive.gun == 0.1262
    assert migrated.weapon.class_thresholds.moderate.knife == 0.5161
    assert migrated.weapon.class_thresholds.moderate.gun == 0.3641
    assert migrated.weapon.class_thresholds.certain.knife == 0.6588
    assert migrated.weapon.class_thresholds.certain.gun == pytest.approx(0.58)


def test_sqlite_restarts_keep_new_mc3_defaults_and_migrate_only_old_defaults(
    tmp_path: Path,
) -> None:
    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    initial = repository.get_settings()
    assert initial.violence.segment_seconds == 4.0
    assert initial.violence.stride_seconds == 2.0
    assert initial.violence.detector_input_frames == 16

    # A clean restart must not mistake the new 16-frame profile for the old
    # intermediate profile and rewrite it to 20 frames.
    restarted = SQLiteRepository(config)
    assert restarted.get_settings().violence.detector_input_frames == 16

    old_profile = restarted.get_settings().violence.model_copy(
        update={
            "segment_seconds": 1.0,
            "stride_seconds": 0.5,
            "detector_input_frames": 4,
            "sensitive_threshold": 0.016434572637081146,
            "moderate_threshold": 0.4920050799846649,
            "certain_threshold": 0.9982624650001526,
        }
    )
    operator_threshold = 0.4
    restarted.save_settings(
        restarted.get_settings().model_copy(
            update={
                "violence": old_profile.model_copy(
                    update={"sensitive_threshold": operator_threshold}
                ),
                "config_version": restarted.get_settings().config_version + 1,
                "updated_by": "test-legacy-profile",
            }
        )
    )

    upgraded = SQLiteRepository(config).get_settings()
    assert upgraded.violence.segment_seconds == 4.0
    assert upgraded.violence.stride_seconds == 2.0
    assert upgraded.violence.detector_input_frames == 16
    assert upgraded.violence.sensitive_threshold == operator_threshold
    assert upgraded.violence.moderate_threshold == pytest.approx(0.4920050799846649)
    assert upgraded.violence.certain_threshold == pytest.approx(0.9982624650001526)


def test_sqlite_migrates_untouched_old_mc3_threshold_triplet(tmp_path: Path) -> None:
    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    settings = repository.get_settings()
    old_profile = settings.violence.model_copy(
        update={
            "segment_seconds": 1.0,
            "stride_seconds": 0.5,
            "detector_input_frames": 4,
            "sensitive_threshold": 0.016434572637081146,
            "moderate_threshold": 0.4920050799846649,
            "certain_threshold": 0.9982624650001526,
        }
    )
    repository.save_settings(
        settings.model_copy(
            update={
                "violence": old_profile,
                "config_version": settings.config_version + 1,
                "updated_by": "test-old-mc3-profile",
            }
        )
    )

    migrated = SQLiteRepository(config).get_settings().violence
    assert migrated.segment_seconds == 4.0
    assert migrated.stride_seconds == 2.0
    assert migrated.detector_input_frames == 16
    assert migrated.sensitive_threshold == pytest.approx(0.9208608865737915)
    assert migrated.moderate_threshold == pytest.approx(0.9954879879951477)
    assert migrated.certain_threshold == pytest.approx(0.9997246861457825)


def test_scene_description_builds_exact_uniform_native_video_input(tmp_path: Path) -> None:
    import av
    import numpy as np

    source = tmp_path / "source.mp4"
    with av.open(str(source), "w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=2)
        stream.width = 64
        stream.height = 48
        stream.pix_fmt = "yuv420p"
        for index in range(120):
            frame = np.full((48, 64, 3), index, dtype=np.uint8)
            for packet in stream.encode(av.VideoFrame.from_ndarray(frame, format="bgr24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    runner = QwenNativeVideoRunner(tmp_path / "hf")
    sampled, metadata, sampled_fps = runner._uniform_scene_frames(source, 20)
    assert sampled.shape == (20, 48, 64, 3)
    assert sampled.dtype == np.uint8
    assert metadata.total_num_frames == 120
    assert metadata.frames_indices == list(range(3, 120, 6))
    assert abs(sampled_fps - (1 / 3)) < 0.01


def test_borderline_review_keeps_configured_native_video_fps(tmp_path: Path) -> None:
    runner = QwenNativeVideoRunner(tmp_path / "hf")
    captured: dict[str, object] = {}

    def fake_generate(video_path: Path, **kwargs) -> str:
        captured.update(kwargs)
        return '{"raise_alert":0,"decision_label":"Do Not Raise Alert","uncertainty":"low"}'

    runner._generate = fake_generate  # type: ignore[method-assign]
    settings = SystemSettings().vlm.model_copy(update={"input_fps": 3.5})
    result = runner.review_borderline(tmp_path / "borderline.mp4", settings)
    assert result["raise_alert"] is False
    assert captured["fps"] == 3.5


def test_codec_failure_keeps_alert_and_persists_borderline_failure(tmp_path: Path) -> None:
    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    camera = Camera(
        name="Codec failure camera",
        location="Test campus",
        storage_path="cameras/codec/source.mp4",
        original_filename="source.mp4",
    )
    repository.save_camera(camera)
    saved_camera = repository.get_camera(camera.id)
    assert saved_camera is not None
    orchestrator = InferenceOrchestrator(
        config, repository, LocalMediaStore(config), notification_sender=None
    )
    sent_alert_ids: list[str] = []

    class NotificationRecorder:
        def dispatch(self, alert, *args, **kwargs) -> int:
            del args, kwargs
            sent_alert_ids.append(alert.id)
            return 0

    orchestrator.notifications = NotificationRecorder()  # type: ignore[assignment]
    orchestrator.mc3.infer = lambda *_args: 0.95  # type: ignore[method-assign]
    orchestrator._encode_clip = lambda *_args: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("simulated codec failure")
    )
    job_id = "job_codec_failure"
    orchestrator._jobs[job_id] = AnalysisJob(
        id=job_id,
        camera_id=camera.id,
        camera_name=camera.name,
        duration_seconds=8,
        debug_mode_enabled=False,
    )
    try:
        orchestrator._process_violence_window(
            job_id,
            saved_camera,
            [(0.0, object())],
            (0.0, 4.0),
            4.0,
            repository.get_settings(),
        )
        alerts = repository.list_alerts()
        assert len(alerts) == 1
        assert sent_alert_ids == [alerts[0].id]
        assert alerts[0].evidence_status == "failed"
        task = repository.list_borderline_review_tasks(alerts[0].id)[0]
        assert task["status"] == "failed"
        assert task["error_code"] == "RuntimeError"
        assert alerts[0].borderline_review_status == "failed"
    finally:
        orchestrator.shutdown()


def test_interrupted_final_borderline_attempt_is_failed_and_context_cleaned(
    tmp_path: Path,
) -> None:
    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    camera = Camera(
        name="Interrupted review",
        location="Test campus",
        storage_path="cameras/interrupted/source.mp4",
        original_filename="source.mp4",
    )
    repository.save_camera(camera)
    saved_camera = repository.get_camera(camera.id)
    assert saved_camera is not None
    alert = AlertService(repository).record_violence_candidate(
        saved_camera, 0.95, source_start_seconds=0, source_end_seconds=4
    )
    assert alert is not None
    context = tmp_path / "tmp" / "borderline_reviews" / "interrupted.mp4"
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_bytes(b"temporary review context")
    task_id = repository.create_borderline_review_task(
        alert_id=alert.id,
        camera_id=camera.id,
        detection_event_key=alert.detection_event_key,
        detector_confidence=0.95,
        source_start_seconds=0,
        source_end_seconds=4,
        context_path=str(context),
        model_version="test-vlm",
        prompt_version="test-prompt-v1",
        config_version=alert.config_version,
        vlm_settings=repository.get_settings().vlm.model_dump(mode="json"),
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE borderline_review_tasks SET status='running',attempt_count=3 WHERE id=?",
            (task_id,),
        )
        connection.commit()

    orchestrator = InferenceOrchestrator(
        config, repository, LocalMediaStore(config), notification_sender=None
    )
    try:
        task = repository.list_borderline_review_tasks(alert.id)[0]
        assert task["status"] == "failed"
        assert task["error_code"] == "worker_interrupted_after_final_attempt"
        assert not context.exists()
        saved = next(item for item in repository.list_alerts() if item.id == alert.id)
        assert saved.borderline_review_status == "failed"
    finally:
        orchestrator.shutdown()


def test_duplicate_start_is_rejected_before_optional_models_are_loaded(
    tmp_path: Path,
) -> None:
    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    camera = Camera(
        name="Already running",
        location="Test campus",
        storage_path="cameras/already-running/source.mp4",
        original_filename="source.mp4",
    )
    repository.save_camera(camera)
    orchestrator = InferenceOrchestrator(
        config, repository, LocalMediaStore(config), notification_sender=None
    )
    orchestrator._jobs["active-job"] = AnalysisJob(
        id="active-job",
        camera_id=camera.id,
        camera_name=camera.name,
        duration_seconds=8,
    )
    load_calls: list[str] = []
    orchestrator.vlm.load = lambda *_args: load_calls.append("vlm")  # type: ignore[method-assign]
    orchestrator.mc3.load = lambda: load_calls.append("mc3")  # type: ignore[method-assign]
    orchestrator.weapon.load = lambda: load_calls.append("weapon")  # type: ignore[method-assign]
    try:
        with pytest.raises(ValueError, match="already has an active analysis job"):
            orchestrator.start(camera.id)
        assert load_calls == []
    finally:
        orchestrator.shutdown()


def test_vlm_text_fallbacks_are_narrow_and_remove_hidden_reasoning() -> None:
    decision = _borderline_object("raise_alert=1; uncertainty=medium; visible forceful strike")
    assert decision["raise_alert"] == 1
    assert decision["uncertainty"] == "medium"
    assert _clean_model_text("<think>private chain</think> Description: Two people push.") == "Two people push."


def test_scene_summary_contract_is_concise_and_bounded() -> None:
    result = _scene_summary_from_output(
        '{"result":{"scene_summary":{"description":"One person moves toward another in the corridor.",'
        '"visible_evidence":["Two people are visible in the corridor.","The raised arm moves toward the other person.","Ignore this third item."],'
        '"uncertainty":"invalid"}}}'
    )
    assert result == {
        "description": "One person moves toward another in the corridor.",
        "visible_evidence": [
            "Two people are visible in the corridor.",
            "The raised arm moves toward the other person.",
        ],
        "uncertainty": "high",
    }
    assert 'exactly one JSON object' in SCENE_USER_PROMPT
    assert _scene_summary_from_output("Scene description could not be formatted.") is None
    current = '{"description":"A person stands near the doorway.","visible_evidence":[],"uncertainty":"low"}'
    assert _scene_summary_from_output(f"```json\n{current}\n```") == {
        "description": "A person stands near the doorway.",
        "visible_evidence": [],
        "uncertainty": "low",
    }
    assert _scene_summary_from_output(json.dumps(current)) == {
        "description": "A person stands near the doorway.",
        "visible_evidence": [],
        "uncertainty": "low",
    }
    assert _scene_summary_from_output('{"visible_evidence":["Only evidence"]}') is None
    assert _scene_summary_from_output('{"description":') is None
    long = _scene_summary_from_output(
        json.dumps(
            {
                "description": " ".join(["visible"] * 30),
                "visible_evidence": [" ".join(["fact"] * 30)],
                "uncertainty": "high",
            }
        )
    )
    assert long is not None
    assert len(long["description"]) <= 120
    assert len(long["visible_evidence"]) == 1
    assert len(long["visible_evidence"][0].split()) <= 10


def test_vlm_failure_marks_description_failed_without_removing_alert(tmp_path: Path) -> None:
    repository = DemoRepository()
    camera = repository.save_camera(
        Camera(
            id="cam_vlm_failure",
            name="Failure test camera",
            storage_path="camera_sources/failure.mp4",
            original_filename="failure.mp4",
        )
    )
    alert = AlertService(repository).record_violence_candidate(camera, 0.9999)
    assert alert is not None
    context = tmp_path / "vlm_context" / "scene.mp4"
    context.parent.mkdir(parents=True)
    context.write_bytes(b"test")

    class FailingVlm:
        def describe_scene(self, _path, _settings):
            raise RuntimeError("simulated VLM outage")

    class ImmediateQueue:
        def submit(self, _priority, function, *args):
            function(*args)

    from app.inference.orchestrator import InferenceOrchestrator

    orchestrator = object.__new__(InferenceOrchestrator)
    orchestrator.vlm = FailingVlm()
    orchestrator.repository = repository
    orchestrator._vlm_queue = ImmediateQueue()
    orchestrator._submit_description(alert.id, context, SystemSettings())

    saved = repository.list_alerts()[0]
    assert saved.id == alert.id
    assert saved.description_status == "failed"
    assert context.exists() is False


def test_15_second_violence_merge_rebuilds_union_evidence_and_rechecks_audience_without_restarting_reminders(
    tmp_path: Path, monkeypatch
) -> None:
    import io
    from datetime import datetime, timedelta, timezone
    from threading import RLock
    from unittest.mock import Mock

    import av
    import cv2
    import numpy as np

    from app.inference import orchestrator as orchestrator_module
    from app.inference.orchestrator import InferenceOrchestrator

    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    media_store = LocalMediaStore(config)
    source_path = tmp_path / "source_with_two_incidents.mp4"
    with av.open(str(source_path), "w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=2)
        stream.width = 64
        stream.height = 48
        stream.pix_fmt = "yuv420p"
        stream.options = {"preset": "ultrafast", "crf": "18"}
        for index in range(52):
            timestamp = index / 2
            if timestamp <= 5:
                frame = np.full((48, 64, 3), (10, 20, 230), dtype=np.uint8)
            elif 20 <= timestamp <= 25:
                frame = np.full((48, 64, 3), (20, 220, 10), dtype=np.uint8)
            else:
                frame = np.full((48, 64, 3), (18, 18, 18), dtype=np.uint8)
            video_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
            for packet in stream.encode(video_frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    stored_source = media_store.save_camera_video(
        "camera_merge_evidence",
        "merge-evidence.mp4",
        "video/mp4",
        io.BytesIO(source_path.read_bytes()),
    )
    camera = repository.save_camera(
        Camera(
            id="camera_merge_evidence",
            name="Merge Evidence Camera",
            storage_path=stored_source.storage_path,
            original_filename="merge-evidence.mp4",
        )
    )
    settings = repository.get_settings()
    settings = settings.model_copy(
        update={
            "vlm": settings.vlm.model_copy(
                update={"scene_description_enabled": False}
            )
        }
    )
    event_start = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)

    first = AlertService(repository).record_violence_candidate(
        camera,
        0.9999,
        detected_at=event_start,
        source_start_seconds=0.0,
        source_end_seconds=5.0,
    )
    assert first is not None

    orchestrator = object.__new__(InferenceOrchestrator)
    orchestrator.runtime = config
    orchestrator.repository = repository
    orchestrator.media_store = media_store
    orchestrator.notifications = Mock()
    orchestrator._lock = RLock()
    orchestrator._evidence_timers = {}

    assert orchestrator._persist_violence_alert(
        first,
        camera,
        b"initial clip",
        2.0,
        settings,
        schedule_description=False,
    ) is True
    orchestrator.notifications.dispatch.assert_called_once()

    phase_id = repository.start_mobile_alert_reminder_phase(
        first.id,
        phase_kind="awaiting_decision",
        started_at=event_start + timedelta(seconds=1),
        first_after_seconds=60,
        all_after_seconds=120,
    )
    assert phase_id is not None

    second = AlertService(repository).record_violence_candidate(
        camera,
        0.998,
        detected_at=event_start + timedelta(seconds=20),
        source_start_seconds=20.0,
        source_end_seconds=25.0,
    )
    assert second is not None
    assert second.id == first.id
    assert second.detection_count == 2
    assert (second.source_start_seconds, second.source_end_seconds) == (0.0, 25.0)

    class ControlledTimer:
        instances = []

        def __init__(self, delay, function):
            self.delay = delay
            self.function = function
            self.cancelled = False
            self.instances.append(self)

        def start(self):
            return None

        def cancel(self):
            self.cancelled = True

        def fire(self):
            self.function()

    monkeypatch.setattr(orchestrator_module, "Timer", ControlledTimer)
    assert orchestrator._persist_violence_alert(
        second,
        camera,
        b"later clip",
        2.0,
        settings,
        schedule_description=False,
    ) is False
    # A merged detection can make new role-threshold recipients eligible.
    # Re-evaluate the audience; repository-level account/event idempotency
    # prevents repeating a first notification to users already notified.
    assert orchestrator.notifications.dispatch.call_count == 2
    assert ControlledTimer.instances[-1].delay == 15.0

    with repository._connect() as connection:
        before_merge_phase = dict(
            connection.execute(
                "SELECT id,phase_status,started_at,first_due_at,all_due_at "
                "FROM mobile_alert_reminder_phases WHERE id=?",
                (phase_id,),
            ).fetchone()
        )

    ControlledTimer.instances[-1].fire()

    saved = next(alert for alert in repository.list_alerts() if alert.id == first.id)
    assert len(saved.evidence) == 1
    evidence = saved.evidence[0]
    assert evidence.media_type == "violence_clip"
    assert (evidence.start_seconds, evidence.end_seconds) == (0.0, 25.0)
    combined_path = media_store.materialize(evidence.storage_path)
    capture = cv2.VideoCapture(str(combined_path))
    assert capture.isOpened()
    output_fps = capture.get(cv2.CAP_PROP_FPS)
    output_frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
    assert output_fps > 0
    assert 24.0 <= output_frames / output_fps <= 27.0

    def average_frame_at(seconds: float):
        capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, frame = capture.read()
        assert ok
        return frame.mean(axis=(0, 1))

    first_incident = average_frame_at(2.0)
    second_incident = average_frame_at(22.0)
    capture.release()
    assert first_incident[2] > first_incident[1] + 60
    assert second_incident[1] > second_incident[2] + 60
    assert orchestrator.notifications.dispatch.call_count == 2

    with repository._connect() as connection:
        after_merge_phase = dict(
            connection.execute(
                "SELECT id,phase_status,started_at,first_due_at,all_due_at "
                "FROM mobile_alert_reminder_phases WHERE id=?",
                (phase_id,),
            ).fetchone()
        )
    assert after_merge_phase == before_merge_phase


def test_urgent_v7_wav_is_loud_and_contains_exactly_two_beeps() -> None:
    sound_path = (
        Path(__file__).resolve().parents[2]
        / "mobileapp"
        / "android"
        / "app"
        / "src"
        / "main"
        / "res"
        / "raw"
        / "guardaid_urgent_beep_v7.wav"
    )
    with wave.open(str(sound_path), "rb") as audio:
        frame_count = audio.getnframes()
        sample_rate = audio.getframerate()
        samples = struct.unpack(
            "<" + "h" * frame_count, audio.readframes(frame_count)
        )
        assert audio.getnchannels() == 1
    assert 0.8 <= frame_count / sample_rate <= 1.5
    assert max(map(abs, samples)) >= 31000
    assert math.sqrt(sum(sample * sample for sample in samples) / frame_count) >= 16000
    window = max(1, int(sample_rate * 0.01))
    active = [
        math.sqrt(sum(sample * sample for sample in samples[start : start + window]) / max(1, len(samples[start : start + window])))
        > 800
        for start in range(0, frame_count, window)
    ]
    runs: list[tuple[bool, int, int]] = []
    position = 0
    for value, group in itertools.groupby(active):
        count = sum(1 for _ in group)
        runs.append((value, position, position + count))
        position += count
    audible_regions = [(start, end) for value, start, end in runs if value]
    assert len(audible_regions) == 2
    assert all(0.25 <= (end - start) * 0.01 <= 0.34 for start, end in audible_regions)
    silence = audible_regions[1][0] - audible_regions[0][1]
    assert 0.15 <= silence * 0.01 <= 0.22


def test_repeat_and_all_user_wavs_have_three_and_four_beeps() -> None:
    raw_directory = (
        Path(__file__).resolve().parents[2]
        / "mobileapp"
        / "android"
        / "app"
        / "src"
        / "main"
        / "res"
        / "raw"
    )
    for filename, expected_beeps, expected_duration in (
        ("guardaid_urgent_repeat_3beep_v1.wav", 3, 1.44),
        ("guardaid_urgent_escalated_4beep_v1.wav", 4, 1.92),
    ):
        with wave.open(str(raw_directory / filename), "rb") as audio:
            frame_count = audio.getnframes()
            sample_rate = audio.getframerate()
            samples = struct.unpack(
                "<" + "h" * frame_count, audio.readframes(frame_count)
            )
            assert audio.getnchannels() == 1
        assert abs(frame_count / sample_rate - expected_duration) < 0.02
        assert max(map(abs, samples)) >= 31000
        assert math.sqrt(sum(sample * sample for sample in samples) / frame_count) >= 16000
        window = max(1, int(sample_rate * 0.01))
        active = [
            math.sqrt(
                sum(sample * sample for sample in samples[start : start + window])
                / max(1, len(samples[start : start + window]))
            )
            > 800
            for start in range(0, frame_count, window)
        ]
        runs: list[tuple[bool, int, int]] = []
        position = 0
        for value, group in itertools.groupby(active):
            count = sum(1 for _ in group)
            runs.append((value, position, position + count))
            position += count
        audible_regions = [(start, end) for value, start, end in runs if value]
        assert len(audible_regions) == expected_beeps
        assert all(
            0.25 <= (end - start) * 0.01 <= 0.34
            for start, end in audible_regions
        )
        gaps = [
            audible_regions[index + 1][0] - audible_regions[index][1]
            for index in range(len(audible_regions) - 1)
        ]
        assert all(0.15 <= gap * 0.01 <= 0.22 for gap in gaps)


def test_local_evidence_has_sha256_and_safe_relative_path(tmp_path: Path) -> None:
    store = LocalMediaStore(runtime(tmp_path))
    stored = store.save_evidence_bytes(
        "alert_test", "weapon_screenshot", ".jpg", "image/jpeg", b"jpeg-test"
    )
    assert stored.storage_path.startswith("evidence/alert_test/")
    assert stored.sha256 == "6f9109e11e5edc0405b468a92162a77f85887d780a9519f5ae5ef852a1a954c3"
    assert store.materialize(stored.storage_path).read_bytes() == b"jpeg-test"
