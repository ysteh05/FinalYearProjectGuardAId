from __future__ import annotations

import sqlite3
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import app.main as main_module
from app.config import RuntimeConfig
from app.firebase_services import (
    FirebaseAccountAlreadyExists,
    FirebaseAuthAccount,
    FirebaseTokenVerifier,
    TokenVerificationError,
    VerifiedIdentity,
)
from app.main import create_app
from app.media import LocalMediaStore
from app.services import AlertService, NotificationService
from app.sqlite_repository import (
    _LEGACY_FORMAT_ERROR,
    SQLiteRepository,
    _UNAVAILABLE_SCENE_SUMMARY,
    _readable_scene_record,
    _readable_scene_text,
    _scene_storage_values,
)


def sqlite_runtime(tmp_path: Path) -> RuntimeConfig:
    return RuntimeConfig(
        persistence_mode="sqlite",
        data_dir=tmp_path,
        database_path=tmp_path / "database" / "guardaid.db",
        max_upload_mb=5,
        auth_mode="disabled",
        notification_mode="console",
        firebase_project_id=None,
        firebase_credentials=None,
        violence_checkpoint=tmp_path / "mc3.pt",
        weapon_checkpoint=None,
        hf_cache=tmp_path / "hf",
    )


def test_workflow_migration_recovers_after_partial_table_creation(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    migrations = Path(__file__).parents[1] / "migrations"
    with sqlite3.connect(config.database_path) as connection:
        for migration in sorted(migrations.glob("0[01][0-9]_*.sql")):
            if int(migration.name[:3]) > 10:
                continue
            connection.executescript(migration.read_text(encoding="utf-8"))
        # Model the interrupted first attempt: v10 is intact, but the new
        # workflow table exists before its referenced phase table is created.
        connection.execute(
            """
            CREATE TABLE mobile_alert_workflows (
                alert_id TEXT PRIMARY KEY REFERENCES alerts(id) ON DELETE CASCADE,
                workflow_status TEXT NOT NULL,
                handler_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                active_phase_id TEXT REFERENCES mobile_alert_reminder_phases(id),
                broadcast_role_ids_json TEXT NOT NULL DEFAULT '[]',
                revision INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.commit()

    repository = SQLiteRepository(config)
    assert repository.schema_version() == 14
    with repository._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM mobile_alert_reminder_phases"
        ).fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def upload_camera(client: TestClient, name: str = "Corridor A") -> dict:
    response = client.post(
        "/api/cameras",
        data={"name": name, "location": "Block A Level 2", "durationSeconds": "12"},
        files={"video": ("sample.mp4", b"local-test-video", "video/mp4")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_legacy_scene_json_is_extracted_as_readable_text() -> None:
    assert _UNAVAILABLE_SCENE_SUMMARY == (
        "AI scene summary is unavailable. Review the video evidence."
    )
    nested = json.dumps(
        {
            "result": {
                "scene_summary": {
                    "description": "A person pushes another person near a corridor wall.",
                    "visible_evidence": ["One person steps away."],
                    "uncertainty": "low",
                }
            }
        }
    )
    assert _readable_scene_text(nested) == (
        "A person pushes another person near a corridor wall."
    )
    assert _readable_scene_text('{"unknown": {"text": "A door opens."}}') == (
        "A door opens."
    )
    assert _readable_scene_text('{"truncated": [') == (
        _UNAVAILABLE_SCENE_SUMMARY
    )
    assert _readable_scene_text(
        '{"description":"A person moves away from the doorway.","visible_evidence":[ '
    ) == "A person moves away from the doorway."
    assert _readable_scene_text(
        r'{"description":"A student\'s bag falls near the doorway.","visible_evidence":['
    ) == "A student's bag falls near the doorway."
    assert _readable_scene_text(
        '{"description":"A student falls near the doorway. A staff member approaches the student'
    ) == "A student falls near the doorway. A staff member approaches the…"


def test_scene_record_recovers_structured_summary_and_json_with_prefix() -> None:
    raw = 'Model response:\n```json\n{"result":{"description":"A person drops a bag."}}\n```'
    display, structured = _scene_storage_values(raw)
    assert display == "A person drops a bag."
    assert structured is not None
    assert _readable_scene_record(
        "Scene description could not be formatted.",
        structured,
    ) == "A person drops a bag."
    from app.sqlite_repository import _scene_summary_from_storage

    assert _scene_summary_from_storage(_LEGACY_FORMAT_ERROR, None) is None


def test_alert_api_exposes_typed_scene_summary_for_legacy_and_new_values(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    client = TestClient(create_app(config, repository, LocalMediaStore(config)))
    camera_json = upload_camera(client, "Summary corridor")
    camera = repository.get_camera(camera_json["id"])
    assert camera is not None
    alert = AlertService(repository).record_violence_candidate(camera, 0.9999)
    assert alert is not None

    repository.update_description(
        alert.id,
        "ready",
        json.dumps(
            {
                "result": {
                    "scene_summary": {
                        "description": "One person moves toward another in the corridor.",
                        "visible_evidence": ["Two people are visible."],
                        "uncertainty": "medium",
                    }
                }
            }
        ),
    )
    response = client.get("/api/alerts")
    assert response.status_code == 200
    value = next(item for item in response.json() if item["id"] == alert.id)
    assert value["sceneDescription"] == "One person moves toward another in the corridor."
    assert value["sceneSummary"] == {
        "description": "One person moves toward another in the corridor.",
        "visibleEvidence": ["Two people are visible."],
        "uncertainty": "medium",
    }
    repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    mobile_client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=FakeVerifier(),
        )
    )
    mobile_response = mobile_client.get(
        "/api/mobile/alerts", headers={"Authorization": "Bearer valid-test-token"}
    )
    assert mobile_response.status_code == 200
    mobile_value = next(item for item in mobile_response.json() if item["id"] == alert.id)
    assert mobile_value["sceneSummary"] == value["sceneSummary"]


def test_sqlite_persists_camera_and_normalises_location_media(tmp_path: Path) -> None:
    config = sqlite_runtime(tmp_path)
    first_repository = SQLiteRepository(config)
    first_client = TestClient(create_app(config, first_repository, LocalMediaStore(config)))
    camera = upload_camera(first_client)

    second_repository = SQLiteRepository(config)
    second_client = TestClient(create_app(config, second_repository, LocalMediaStore(config)))
    persisted = second_client.get("/api/cameras").json()
    assert len(persisted) == 1
    assert persisted[0]["id"] == camera["id"]
    assert persisted[0]["storagePath"].startswith("camera_sources/")
    assert not Path(persisted[0]["storagePath"]).is_absolute()

    with sqlite3.connect(config.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM cameras").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM media_files").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM camera_sources").fetchone()[0] == 1
        alert_columns = {row[1] for row in connection.execute("PRAGMA table_info(alerts)")}
        assert "camera_name" not in alert_columns
        assert "max_confidence" not in alert_columns
        assert "detection_count" not in alert_columns

    assert second_client.delete(f"/api/cameras/{camera['id']}").status_code == 204
    assert second_client.get("/api/cameras").json() == []
    with sqlite3.connect(config.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM media_files").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 0


def test_detection_events_are_aggregated_into_one_alert(tmp_path: Path) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    client = TestClient(create_app(config, repository, LocalMediaStore(config)))
    camera_json = upload_camera(client)
    camera = repository.get_camera(camera_json["id"])
    assert camera is not None

    service = AlertService(repository)
    now = datetime.now(timezone.utc)
    first = service.record_violence_candidate(
        camera, 0.9998, detected_at=now, source_start_seconds=10, source_end_seconds=15
    )
    second = service.record_violence_candidate(
        camera, 0.9999, detected_at=now + timedelta(seconds=15),
        source_start_seconds=30, source_end_seconds=35,
    )
    third = service.record_violence_candidate(
        camera, 0.99995, detected_at=now + timedelta(seconds=31),
        source_start_seconds=51, source_end_seconds=56,
    )
    assert first is not None and second is not None and third is not None
    assert first.id == second.id
    assert third.id != first.id

    alerts = repository.list_alerts()
    assert len(alerts) == 2
    merged_alert = next(alert for alert in alerts if alert.id == first.id)
    assert merged_alert.camera_name == "Corridor A"
    assert merged_alert.detection_count == 2
    assert merged_alert.max_confidence == 0.9999
    with sqlite3.connect(config.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM detection_events").fetchone()[0] == 3
    events = repository.list_alert_detection_events(first.id)
    assert [item["confidence"] for item in events] == [0.9998, 0.9999]
    assert [
        (item["source_start_seconds"], item["source_end_seconds"])
        for item in events
    ] == [(10, 15), (30, 35)]
    assert [item["config_version"] for item in events] == [1, 1]
    deletion = client.delete(f"/api/cameras/{camera.id}")
    assert deletion.status_code == 409
    assert "alert history" in deletion.json()["detail"]


def test_review_task_anchors_to_the_latest_detection_merged_into_an_alert(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    client = TestClient(create_app(config, repository, LocalMediaStore(config)))
    camera_json = upload_camera(client, "Review event anchor")
    camera = repository.get_camera(camera_json["id"])
    assert camera is not None
    service = AlertService(repository)
    now = datetime.now(timezone.utc)
    first = service.record_violence_candidate(
        camera, 0.95, detected_at=now, source_start_seconds=10, source_end_seconds=14
    )
    merged = service.record_violence_candidate(
        camera, 0.96, detected_at=now + timedelta(seconds=1),
        source_start_seconds=12, source_end_seconds=16,
    )
    assert first is not None and merged is not None and first.id == merged.id
    assert merged.detection_event_key != first.detection_event_key

    task_id = repository.create_borderline_review_task(
        alert_id=merged.id,
        camera_id=camera.id,
        detection_event_key=merged.detection_event_key,
        detector_confidence=0.96,
        source_start_seconds=12,
        source_end_seconds=16,
        context_path=str(tmp_path / "tmp" / "review.mp4"),
        model_version="test-vlm",
        prompt_version="test-prompt-v1",
        config_version=merged.config_version,
        vlm_settings=repository.get_settings().vlm.model_dump(mode="json"),
    )
    assert repository.list_borderline_review_tasks(merged.id)[0]["id"] == task_id
    assert any(
        event["id"] == merged.detection_event_key
        for event in repository.list_alert_detection_events(merged.id)
    )


@pytest.mark.parametrize(
    "terminal_action,expected_status",
    [
        ("dismiss_false_alarm", "dismissed"),
        ("confirm_resolved", "resolved"),
        ("legacy_escalate", "escalated"),
    ],
)
def test_new_detection_does_not_merge_into_closed_or_legacy_escalated_alert(
    tmp_path: Path, terminal_action: str, expected_status: str
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    client = TestClient(create_app(config, repository, LocalMediaStore(config)))
    camera_json = upload_camera(client)
    camera = repository.get_camera(camera_json["id"])
    assert camera is not None
    admin = repository.bootstrap_first_administrator(
        "merge-terminal-admin", "merge-terminal@example.test", "Admin"
    )
    service = AlertService(repository)
    first = service.record_violence_candidate(
        camera,
        0.9999,
        detected_at=datetime.now(timezone.utc),
        source_start_seconds=10,
        source_end_seconds=15,
    )
    assert first is not None

    if terminal_action == "legacy_escalate":
        assert repository.record_mobile_alert_action(
            first.id, admin["id"], "escalate", "Legacy historical action"
        )
    else:
        result = repository.record_mobile_workflow_action(
            first.id,
            admin["id"],
            terminal_action,
            comment=None,
            recipient_role_ids=[],
            allow_empty_audience=False,
            idempotency_key=f"close-before-merge-{terminal_action}",
            first_after_seconds=60,
            all_after_seconds=120,
        )
        assert result is not None

    second = service.record_violence_candidate(
        camera,
        0.9999,
        detected_at=datetime.now(timezone.utc) + timedelta(seconds=1),
        source_start_seconds=16,
        source_end_seconds=20,
    )
    assert second is not None
    assert second.id != first.id
    saved = {item.id: item for item in repository.list_alerts()}
    assert saved[first.id].status == expected_status
    assert saved[first.id].detection_count == 1
    assert saved[second.id].detection_count == 1


class FakeVerifier:
    def verify(self, id_token: str) -> VerifiedIdentity:
        assert id_token == "valid-test-token"
        return VerifiedIdentity("firebase-uid-1", "principal@example.test", "Principal")


class MultiTokenVerifier:
    def __init__(self, identities: dict[str, VerifiedIdentity]) -> None:
        self.identities = identities

    def verify(self, id_token: str) -> VerifiedIdentity:
        if id_token not in self.identities:
            raise TokenVerificationError("invalid_token", "Invalid test token")
        return self.identities[id_token]


def test_alert_reads_are_authorized_per_user_persistent_and_idempotent(tmp_path: Path) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    admin = repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    other = repository.upsert_firebase_user(
        "security-uid", "security@example.test", "Security"
    )
    assert repository.assign_mobile_user(
        other["id"], "role_security_personnel", "active", admin["id"]
    )
    verifier = MultiTokenVerifier({
        "admin-token": VerifiedIdentity("firebase-uid-1", "principal@example.test", "Principal"),
        "other-token": VerifiedIdentity("security-uid", "security@example.test", "Security"),
        "pending-token": VerifiedIdentity("pending-uid", "pending@example.test", "Pending"),
    })
    client = TestClient(create_app(
        config, repository, LocalMediaStore(config), token_verifier_override=verifier
    ))
    camera = repository.get_camera(upload_camera(client)["id"])
    assert camera is not None
    alert = AlertService(repository).record_violence_candidate(
        camera, 0.9999, detected_at=datetime.now(timezone.utc),
        source_start_seconds=0, source_end_seconds=5,
    )
    assert alert is not None
    admin_headers = {"Authorization": "Bearer admin-token"}
    other_headers = {"Authorization": "Bearer other-token"}
    url = f"/api/mobile/alerts/{alert.id}"
    assert client.get(url, headers=admin_headers).json()["isRead"] is False
    assert client.post(f"{url}/read").status_code == 401
    assert client.post(f"{url}/read", headers={"Authorization": "Bearer pending-token"}).status_code == 403
    assert client.post("/api/mobile/alerts/missing/read", headers=admin_headers).status_code == 404
    for _ in range(2):
        assert client.post(f"{url}/read", headers=admin_headers).status_code == 200
    assert client.get(url, headers=admin_headers).json()["isRead"] is True
    assert client.get(url, headers=other_headers).json()["isRead"] is False
    listed = client.get("/api/mobile/alerts", headers=admin_headers).json()
    assert next(item for item in listed if item["id"] == alert.id)["isRead"] is True
    reopened = SQLiteRepository(config)
    assert reopened.schema_version() == 14
    assert reopened.list_read_alert_ids(admin["id"]) == {alert.id}
    assert reopened.list_read_alert_ids(other["id"]) == set()
    with sqlite3.connect(config.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM alert_reads").fetchone()[0] == 1
        assert connection.execute("SELECT status FROM alerts WHERE id=?", (alert.id,)).fetchone()[0] == "open"


def test_borderline_settings_support_bulk_overrides_and_revision_conflicts(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(sqlite_runtime(tmp_path))
    admin = repository.bootstrap_first_administrator(
        "borderline-admin", "admin@example.test", "Admin"
    )
    teacher = repository.upsert_firebase_user(
        "borderline-teacher", "teacher@example.test", "Teacher"
    )
    assert repository.assign_mobile_user(
        teacher["id"], "role_teacher", "active", admin["id"]
    )

    revision = repository.get_borderline_settings_revision()
    role = repository.set_borderline_role_enabled(
        "role_teacher", True, admin["id"], revision
    )
    assert role is not None
    teacher_setting = next(user for user in role["users"] if user["id"] == teacher["id"])
    assert teacher_setting["enabled"] is True
    assert repository.get_borderline_settings_revision() == revision + 1

    revision += 1
    repository.set_borderline_user_enabled(teacher["id"], False, admin["id"], revision)
    revision += 1
    with pytest.raises(ValueError, match="revision_conflict"):
        repository.set_borderline_role_enabled(
            "role_teacher", True, admin["id"], revision - 1
        )

    overridden = repository.set_borderline_role_enabled(
        "role_teacher", True, admin["id"], revision
    )
    assert overridden is not None
    teacher_setting = next(
        user for user in overridden["users"] if user["id"] == teacher["id"]
    )
    assert teacher_setting["enabled"] is True
    assert teacher_setting["settingSource"] == "role_bulk"
    assert repository.list_enabled_devices_for_roles([]) == []

    revision_before_invalid = repository.get_borderline_settings_revision()
    with pytest.raises(ValueError, match="invalid_role"):
        repository.set_borderline_roles_enabled(
            ["missing-role"], admin["id"], revision_before_invalid
        )
    assert repository.get_borderline_settings_revision() == revision_before_invalid
    assert next(
        item for item in repository.list_borderline_role_settings()
        if item["id"] == "role_teacher"
    )["defaultEnabled"] is True


def test_mobile_borderline_api_reports_availability_and_supports_role_and_user_updates(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    admin = repository.bootstrap_first_administrator(
        "borderline-api-admin", "admin@example.test", "Admin"
    )
    teacher = repository.upsert_firebase_user(
        "borderline-api-teacher", "teacher@example.test", "Teacher"
    )
    assert repository.assign_mobile_user(
        teacher["id"], "role_teacher", "active", admin["id"]
    )
    settings = repository.get_settings()
    repository.save_settings(
        settings.model_copy(
            update={
                "vlm": settings.vlm.model_copy(update={"borderline_review_enabled": True}),
                "config_version": settings.config_version + 1,
                "updated_by": "test",
            }
        )
    )
    verifier = MultiTokenVerifier(
        {
            "borderline-admin-token": VerifiedIdentity(
                "borderline-api-admin", "admin@example.test", "Admin"
            ),
            "borderline-teacher-token": VerifiedIdentity(
                "borderline-api-teacher", "teacher@example.test", "Teacher"
            ),
        }
    )
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=verifier,
        )
    )
    admin_headers = {"Authorization": "Bearer borderline-admin-token"}
    teacher_headers = {"Authorization": "Bearer borderline-teacher-token"}
    initial = client.get(
        "/api/mobile/admin/borderline-settings", headers=admin_headers
    )
    assert initial.status_code == 200
    assert initial.json()["reviewEnabled"] is True
    assert initial.json()["available"] is True
    revision = initial.json()["revision"]

    enabled = client.put(
        "/api/mobile/admin/borderline-settings/roles/role_teacher",
        headers=admin_headers,
        json={"enabled": True, "revision": revision},
    )
    assert enabled.status_code == 200
    group = next(item for item in enabled.json()["roles"] if item["id"] == "role_teacher")
    assert group["selectionState"] == "on"
    teacher_setting = next(item for item in group["users"] if item["id"] == teacher["id"])
    assert teacher_setting["enabled"] is True

    session = client.get("/api/mobile/session", headers=teacher_headers).json()
    assert session["borderlineReviewAvailable"] is True
    assert session["borderlineReviewEnabledForAccount"] is True
    assert session["borderlineReviewEffective"] is True

    next_revision = enabled.json()["revision"]
    individual = client.put(
        f"/api/mobile/admin/borderline-settings/users/{teacher['id']}",
        headers=admin_headers,
        json={"enabled": False, "revision": next_revision},
    )
    assert individual.status_code == 200
    stale = client.put(
        "/api/mobile/admin/borderline-settings/roles/role_teacher",
        headers=admin_headers,
        json={"enabled": False, "revision": next_revision},
    )
    assert stale.status_code == 409
    legacy = client.put(
        "/api/mobile/admin/borderline-settings",
        headers=admin_headers,
        json={"enabled": True, "recipientRoleIds": ["role_teacher"]},
    )
    assert legacy.status_code == 409
    session = client.get("/api/mobile/session", headers=teacher_headers).json()
    assert session["borderlineReviewEffective"] is False
    stale_legacy = client.put(
        "/api/mobile/admin/borderline-settings",
        headers=admin_headers,
        json={
            "enabled": True,
            "recipientRoleIds": ["role_teacher"],
            "revision": next_revision,
        },
    )
    assert stale_legacy.status_code == 409


def test_mobile_alert_detail_exposes_detection_review_and_account_audience_evidence(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=FakeVerifier(),
        )
    )
    camera_json = upload_camera(client, "Borderline detail")
    camera = repository.get_camera(camera_json["id"])
    assert camera is not None
    alert = AlertService(repository).record_violence_candidate(
        camera, 0.95, source_start_seconds=0, source_end_seconds=4
    )
    assert alert is not None
    repository.create_borderline_review_task(
        alert_id=alert.id,
        camera_id=camera.id,
        detection_event_key=alert.detection_event_key,
        detector_confidence=0.95,
        source_start_seconds=0,
        source_end_seconds=4,
        context_path=str(tmp_path / "tmp" / "borderline_reviews" / "review.mp4"),
        model_version="test-vlm",
        prompt_version="test-prompt-v1",
        config_version=alert.config_version,
        vlm_settings=repository.get_settings().vlm.model_dump(mode="json"),
    )

    response = client.get(
        f"/api/mobile/alerts/{alert.id}",
        headers={"Authorization": "Bearer valid-test-token"},
    )
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["borderlineReviewStatus"] == "queued"
    assert value["borderlineReviewSummary"]["pending"] == 1
    assert value["borderlineReviewTasks"][0]["promptVersion"] == "test-prompt-v1"
    assert value["detectionEvents"][0]["decisionSource"] == "threshold"
    assert value["detectionEvents"][0]["qualifications"]["violence"] == ["sensitive"]
    assert value["notificationEligibility"]["status"] == "not_recorded"


def test_web_alert_list_includes_review_summary_and_decision_basis(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    client = TestClient(create_app(config, repository, LocalMediaStore(config)))
    camera_json = upload_camera(client, "Web audit summary")
    camera = repository.get_camera(camera_json["id"])
    assert camera is not None
    alert = AlertService(repository).record_violence_candidate(
        camera, 0.95, source_start_seconds=0, source_end_seconds=4
    )
    assert alert is not None
    task_id = repository.create_borderline_review_task(
        alert_id=alert.id,
        camera_id=camera.id,
        detection_event_key=alert.detection_event_key,
        detector_confidence=0.95,
        source_start_seconds=0,
        source_end_seconds=4,
        context_path=str(tmp_path / "tmp" / "review.mp4"),
        model_version="test-vlm",
        prompt_version="test-prompt-v1",
        config_version=alert.config_version,
        vlm_settings=repository.get_settings().vlm.model_dump(mode="json"),
    )
    repository.finish_borderline_review_task(
        task_id, status="failed", error_code="ContextEncodingFailed"
    )

    response = client.get("/api/alerts")
    assert response.status_code == 200
    value = next(item for item in response.json() if item["id"] == alert.id)
    assert value["detectionQualifications"]["violence"] == ["sensitive"]
    assert value["borderlineReviewSummary"] == {
        "total": 1,
        "positive": 0,
        "negative": 0,
        "failed": 1,
        "cancelled": 0,
        "pending": 0,
        "latestStatus": "failed",
        "latestReason": None,
        "latestUncertainty": None,
        "latestErrorCode": "ContextEncodingFailed",
    }
    assert value["evidenceStatus"] == "preparing"


def test_positive_borderline_review_survives_a_later_negative_window(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    client = TestClient(create_app(config, repository, LocalMediaStore(config)))
    camera_json = upload_camera(client, "Multiple review windows")
    camera = repository.get_camera(camera_json["id"])
    assert camera is not None
    service = AlertService(repository)
    now = datetime.now(timezone.utc)
    first = service.record_violence_candidate(
        camera, 0.95, detected_at=now, source_start_seconds=0, source_end_seconds=4
    )
    second = service.record_violence_candidate(
        camera, 0.96, detected_at=now + timedelta(seconds=1),
        source_start_seconds=2, source_end_seconds=6,
    )
    assert first is not None and second is not None and first.id == second.id
    task_ids = []
    for detection, start in ((first, 0.0), (second, 2.0)):
        task_ids.append(
            repository.create_borderline_review_task(
                alert_id=second.id,
                camera_id=camera.id,
                detection_event_key=detection.detection_event_key,
                detector_confidence=detection.max_confidence,
                source_start_seconds=start,
                source_end_seconds=start + 4,
                context_path=str(tmp_path / "tmp" / f"review-{start}.mp4"),
                model_version="test-vlm",
                prompt_version="test-prompt-v1",
                config_version=second.config_version,
                vlm_settings=repository.get_settings().vlm.model_dump(mode="json"),
            )
        )
    repository.finish_borderline_review_task(
        task_ids[0], status="positive", raise_alert=True, uncertainty="medium",
        brief_reason="Visible forceful contact.",
    )
    repository.finish_borderline_review_task(
        task_ids[1], status="negative", raise_alert=False, uncertainty="low",
        brief_reason="The later window is not concerning.",
    )
    saved = next(item for item in repository.list_alerts() if item.id == second.id)
    assert saved.borderline_review_status == "positive"
    assert saved.is_borderline_promoted is True


def test_disabling_web_borderline_review_cancels_queued_tasks_and_contexts(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    client = TestClient(create_app(config, repository, LocalMediaStore(config)))
    camera_json = upload_camera(client, "Borderline cancellation")
    camera = repository.get_camera(camera_json["id"])
    assert camera is not None
    alert = AlertService(repository).record_violence_candidate(
        camera, 0.95, source_start_seconds=0, source_end_seconds=4
    )
    assert alert is not None
    context_path = tmp_path / "tmp" / "borderline_reviews" / "queued-review.mp4"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_bytes(b"private temporary context")
    task_id = repository.create_borderline_review_task(
        alert_id=alert.id,
        camera_id=camera.id,
        detection_event_key=alert.detection_event_key,
        detector_confidence=0.95,
        source_start_seconds=0,
        source_end_seconds=4,
        context_path=str(context_path),
        model_version="test-vlm",
        prompt_version="test-prompt-v1",
        config_version=alert.config_version,
        vlm_settings=repository.get_settings().vlm.model_dump(mode="json"),
    )

    settings = repository.get_settings()
    updated = settings.model_copy(
        update={
            "vlm": settings.vlm.model_copy(update={"borderline_review_enabled": False})
        }
    )
    response = client.put(
        "/api/settings",
        json=updated.model_dump(mode="json", by_alias=True),
    )
    assert response.status_code == 200, response.text
    assert repository.list_borderline_review_tasks(alert.id)[0]["status"] == "cancelled"
    assert repository.list_borderline_review_tasks(alert.id)[0]["error_code"] == "global_disabled"
    assert repository.list_queued_borderline_review_tasks() == []
    assert not context_path.exists()
    saved_alert = next(item for item in repository.list_alerts() if item.id == alert.id)
    assert saved_alert.borderline_review_status == "cancelled"
    assert task_id


class FakeUserAccountAdmin:
    def __init__(
        self,
        accounts: list[FirebaseAuthAccount] | None = None,
        *,
        fail_delete: bool = False,
    ) -> None:
        self.accounts = {item.email.casefold(): item for item in accounts or []}
        self.created_passwords: list[str] = []
        self.deleted_uids: list[str] = []
        self.find_calls = 0
        self.fail_delete = fail_delete

    def find_by_email(self, email: str) -> FirebaseAuthAccount | None:
        self.find_calls += 1
        return self.accounts.get(email.casefold())

    def create_user(
        self, email: str, display_name: str, password: str
    ) -> FirebaseAuthAccount:
        if email.casefold() in self.accounts:
            raise FirebaseAccountAlreadyExists
        account = FirebaseAuthAccount(
            uid=f"created-{len(self.created_passwords) + 1}",
            email=email,
            display_name=display_name,
            disabled=False,
        )
        self.accounts[email.casefold()] = account
        self.created_passwords.append(password)
        return account

    def delete_user(self, uid: str) -> None:
        self.deleted_uids.append(uid)
        if self.fail_delete:
            raise RuntimeError("simulated Firebase cleanup failure")
        self.accounts = {
            email: account
            for email, account in self.accounts.items()
            if account.uid != uid
        }


class RejectedVerifier:
    def verify(self, id_token: str) -> VerifiedIdentity:
        raise TokenVerificationError(
            "project_mismatch",
            "The mobile app and GuardAId server are configured for different Firebase projects.",
        )


def test_mobile_returns_specific_safe_token_verification_error(tmp_path: Path) -> None:
    config = sqlite_runtime(tmp_path)
    client = TestClient(
        create_app(
            config,
            SQLiteRepository(config),
            LocalMediaStore(config),
            token_verifier_override=RejectedVerifier(),
        )
    )

    response = client.get(
        "/api/mobile/session",
        headers={"Authorization": "Bearer rejected-test-token"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == (
        "The mobile app and GuardAId server are configured for different Firebase projects."
    )


def test_firebase_configuration_rejects_service_account_project_mismatch(
    tmp_path: Path,
) -> None:
    credential_path = tmp_path / "firebase-service-account.json"
    credential_path.write_text(
        json.dumps({"project_id": "wrong-project"}), encoding="utf-8"
    )
    config = replace(
        sqlite_runtime(tmp_path),
        auth_mode="firebase",
        firebase_project_id="guardaid-prototype",
        firebase_credentials=credential_path,
    )

    with pytest.raises(RuntimeError, match="Firebase project mismatch"):
        config.validate_firebase_admin()


def test_firebase_verifier_allows_safe_clock_skew(monkeypatch) -> None:
    from firebase_admin import auth

    captured: dict[str, object] = {}

    def fake_verify(id_token: str, *, app, clock_skew_seconds: int) -> dict:
        captured.update(
            id_token=id_token,
            app=app,
            clock_skew_seconds=clock_skew_seconds,
        )
        return {
            "uid": "firebase-uid-clock-test",
            "email": "clock@example.test",
        }

    monkeypatch.setattr(auth, "verify_id_token", fake_verify)
    verifier = object.__new__(FirebaseTokenVerifier)
    verifier._app = object()

    identity = verifier.verify("clock-test-token")

    assert identity.firebase_uid == "firebase-uid-clock-test"
    assert captured["clock_skew_seconds"] == 60


def test_firebase_identity_maps_to_sqlite_user_and_device(tmp_path: Path) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=FakeVerifier(),
        )
    )
    headers = {"Authorization": "Bearer valid-test-token"}
    session = client.get("/api/mobile/session", headers=headers)
    assert session.status_code == 200
    assert session.json()["firebaseUid"] == "firebase-uid-1"
    assert session.json()["status"] == "pending"

    registration = client.put(
        "/api/mobile/device",
        headers=headers,
        json={
            "registrationValue": "fcm-registration-value-for-unit-test-1234567890",
            "installationId": "installation-1",
            "platform": "android",
            "deviceLabel": "Test phone",
        },
    )
    assert registration.status_code == 403
    assert "activate" in registration.json()["detail"]

    repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    active_session = client.get("/api/mobile/session", headers=headers)
    assert active_session.status_code == 200
    assert active_session.json()["status"] == "active"
    assert active_session.json()["isAdmin"] is True

    registration = client.put(
        "/api/mobile/device",
        headers=headers,
        json={
            "registrationValue": "fcm-registration-value-for-unit-test-1234567890",
            "installationId": "installation-1",
            "platform": "android",
            "deviceLabel": "Test phone",
        },
    )
    assert registration.status_code == 200
    with sqlite3.connect(config.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM user_roles").fetchone()[0] == 1


def test_mobile_admin_response_workflow_is_role_controlled_and_call_is_simulated(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=FakeVerifier(),
        )
    )
    headers = {"Authorization": "Bearer valid-test-token"}
    camera_json = upload_camera(client, "North Gate")
    camera = repository.get_camera(camera_json["id"])
    assert camera is not None
    alert = AlertService(repository).record_violence_candidate(
        camera,
        0.9999,
        detected_at=datetime.now(timezone.utc),
        source_start_seconds=5,
        source_end_seconds=10,
    )
    assert alert is not None

    mobile_alerts = client.get("/api/mobile/alerts", headers=headers)
    assert mobile_alerts.status_code == 200
    assert mobile_alerts.json()[0]["location"] == "Block A Level 2"

    simulated = client.post(
        f"/api/mobile/alerts/{alert.id}/simulate-emergency-call", headers=headers
    )
    assert simulated.status_code == 200
    assert simulated.json()["realCallMade"] is False
    assert simulated.json()["diallerOpened"] is False

    resolved = client.post(
        f"/api/mobile/alerts/{alert.id}/actions",
        headers=headers,
        json={"action": "confirm_resolved", "reason": "On-site staff confirmed the area is safe."},
    )
    assert resolved.status_code == 200
    assert resolved.json()["alert"]["status"] == "resolved"

    repository.upsert_firebase_user(
        "pending-teacher-uid", "teacher@example.test", "Teacher Example"
    )
    users = client.get("/api/mobile/admin/users", headers=headers)
    pending = next(item for item in users.json() if item["firebaseUid"] == "pending-teacher-uid")
    assigned = client.patch(
        f"/api/mobile/admin/users/{pending['id']}",
        headers=headers,
        json={"roleId": "role_teacher", "status": "active"},
    )
    assert assigned.status_code == 200
    assert assigned.json()["roleName"] == "teacher"
    assert assigned.json()["sensitivityMode"] == "certain"

    with sqlite3.connect(config.database_path) as connection:
        actions = {
            row[0] for row in connection.execute("SELECT action FROM alert_actions")
        }
    assert actions == {"simulated_emergency_call"}
    workflow_events = repository.get_mobile_alert_workflow(alert.id)["events"]
    assert {item["action"] for item in workflow_events} == {
        "confirm_resolved",
        "simulated_emergency_call",
    }


@pytest.mark.parametrize(
    ("action", "expected_status", "reason"),
    [
        ("dismiss_false_alarm", "dismissed", None),
        ("confirm_resolved", "resolved", ""),
        ("confirm_only", "in_review", "   "),
    ],
)
def test_mobile_alert_actions_accept_missing_or_blank_optional_comments(
    tmp_path: Path,
    action: str,
    expected_status: str,
    reason: str | None,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=FakeVerifier(),
        )
    )
    headers = {"Authorization": "Bearer valid-test-token"}
    camera_json = upload_camera(client, "Optional comment camera")
    camera = repository.get_camera(camera_json["id"])
    assert camera is not None
    alert = AlertService(repository).record_violence_candidate(
        camera,
        0.9999,
        detected_at=datetime.now(timezone.utc),
        source_start_seconds=5,
        source_end_seconds=10,
    )
    assert alert is not None

    payload: dict[str, object] = {"action": action}
    if reason is not None:
        payload["reason"] = reason
    if action == "confirm_only":
        missing_roles_payload = {**payload, "recipient_role_ids": []}
        response = client.post(
            f"/api/mobile/alerts/{alert.id}/actions",
            headers=headers,
            json=missing_roles_payload,
        )
        assert response.status_code == 409
        assert "at least one Broadcast Role" in response.json()["detail"]
        payload["recipient_role_ids"] = ["role_security_personnel"]
    response = client.post(
        f"/api/mobile/alerts/{alert.id}/actions",
        headers=headers,
        json=payload,
    )

    assert response.status_code == 200, response.text
    result = response.json()["alert"]
    assert result["status"] == expected_status
    assert result["actions"][0]["action"] == action
    assert result["actions"][0]["note"] is None


def test_mobile_admin_can_create_firebase_identity_and_active_sqlite_user(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    account_admin = FakeUserAccountAdmin()
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=FakeVerifier(),
            user_account_admin_override=account_admin,
        )
    )

    assert client.post(
        "/api/mobile/admin/users",
        json={
            "email": "  new.staff@example.test ",
            "displayName": "New Staff",
            "roleId": "role_teacher",
        },
    ).status_code == 401

    created = client.post(
        "/api/mobile/admin/users",
        headers={"Authorization": "Bearer valid-test-token"},
        json={
            "email": "  New.Staff@example.test ",
            "displayName": "  New   Staff ",
            "roleId": "role_teacher",
        },
    )
    assert created.status_code == 201, created.text
    value = created.json()
    assert value["email"] == "new.staff@example.test"
    assert value["displayName"] == "New Staff"
    assert value["status"] == "active"
    assert value["roleId"] == "role_teacher"
    assert value["identityAction"] == "created"
    assert "password" not in value
    assert len(account_admin.created_passwords) == 1
    assert len(account_admin.created_passwords[0]) >= 32

    with sqlite3.connect(config.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM user_roles WHERE user_id=?", (value["id"],)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT action FROM audit_logs WHERE entity_id=?", (value["id"],)
        ).fetchone()[0] == "create_mobile_user"

    duplicate = client.post(
        "/api/mobile/admin/users",
        headers={"Authorization": "Bearer valid-test-token"},
        json={
            "email": "NEW.STAFF@example.test",
            "displayName": "Duplicate",
            "roleId": "role_teacher",
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["identityAction"] == "already_registered"
    assert duplicate.json()["id"] == value["id"]
    assert account_admin.find_calls == 1


def test_mobile_admin_requires_explicit_confirmation_to_link_existing_firebase_user(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    account_admin = FakeUserAccountAdmin(
        [
            FirebaseAuthAccount(
                "existing-firebase-uid",
                "already.exists@example.test",
                "Existing Staff",
                False,
            )
        ]
    )
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=FakeVerifier(),
            user_account_admin_override=account_admin,
        )
    )
    payload = {
        "email": "already.exists@example.test",
        "displayName": "Existing Staff",
        "roleId": "role_teacher",
    }
    headers = {"Authorization": "Bearer valid-test-token"}

    conflict = client.post("/api/mobile/admin/users", headers=headers, json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "firebase_identity_exists"
    assert repository.mobile_user_by_email(payload["email"]) is None

    linked = client.post(
        "/api/mobile/admin/users",
        headers=headers,
        json={**payload, "linkExisting": True},
    )
    assert linked.status_code == 201, linked.text
    assert linked.json()["identityAction"] == "linked"
    assert linked.json()["firebaseUid"] == "existing-firebase-uid"
    assert account_admin.created_passwords == []
    with sqlite3.connect(config.database_path) as connection:
        action = connection.execute(
            "SELECT action FROM audit_logs WHERE entity_id=?",
            (linked.json()["id"],),
        ).fetchone()[0]
    assert action == "link_mobile_user"


def test_mobile_admin_rolls_back_new_firebase_identity_if_sqlite_creation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    account_admin = FakeUserAccountAdmin()

    def fail_sqlite_create(*_args, **_kwargs):
        raise RuntimeError("simulated SQLite failure")

    monkeypatch.setattr(repository, "create_mobile_user", fail_sqlite_create)
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=FakeVerifier(),
            user_account_admin_override=account_admin,
        )
    )
    response = client.post(
        "/api/mobile/admin/users",
        headers={"Authorization": "Bearer valid-test-token"},
        json={
            "email": "rollback@example.test",
            "displayName": "Rollback User",
            "roleId": "role_teacher",
        },
    )
    assert response.status_code == 502
    assert response.json()["detail"] == (
        "The GuardAId user record could not be created."
    )
    assert account_admin.deleted_uids == ["created-1"]
    assert account_admin.find_by_email("rollback@example.test") is None


def test_mobile_admin_reports_partial_failure_if_firebase_compensation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    account_admin = FakeUserAccountAdmin(fail_delete=True)

    def fail_sqlite_create(*_args, **_kwargs):
        raise RuntimeError("simulated SQLite failure")

    monkeypatch.setattr(repository, "create_mobile_user", fail_sqlite_create)
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=FakeVerifier(),
            user_account_admin_override=account_admin,
        )
    )

    response = client.post(
        "/api/mobile/admin/users",
        headers={"Authorization": "Bearer valid-test-token"},
        json={
            "email": "partial@example.test",
            "displayName": "Partial Failure",
            "roleId": "role_teacher",
        },
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "account_created_but_local_setup_failed"
    assert account_admin.deleted_uids == ["created-1"]
    assert account_admin.find_by_email("partial@example.test") is not None


def test_mobile_user_management_enforces_permission_ceiling_and_lifecycle(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    admin = repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    manager_role = repository.create_mobile_role(
        "Limited user manager",
        "Can manage users only.",
        "moderate",
        {
            "can_manage_roles": False,
            "can_manage_users": True,
            "can_manage_borderline_settings": False,
        },
        admin["id"],
    )
    privileged_role = repository.create_mobile_role(
        "Borderline administrator",
        "Can manage borderline settings.",
        "moderate",
        {
            "can_manage_roles": False,
            "can_manage_users": False,
            "can_manage_borderline_settings": True,
        },
        admin["id"],
    )
    assert manager_role is not None and privileged_role is not None
    manager = repository.upsert_firebase_user(
        "manager-firebase-uid", "manager@example.test", "User Manager"
    )
    teacher = repository.upsert_firebase_user(
        "teacher-firebase-uid", "teacher@example.test", "Teacher"
    )
    assert repository.assign_mobile_user(
        manager["id"], manager_role["id"], "active", admin["id"]
    )
    assert repository.assign_mobile_user(
        teacher["id"], "role_teacher", "active", admin["id"]
    )
    repository.register_device(
        teacher["id"],
        "teacher-fcm-token-0123456789abcdef",
        "android",
        "Teacher phone",
        "teacher-install",
    )

    verifier = MultiTokenVerifier(
        {
            "admin-token": VerifiedIdentity(
                "firebase-uid-1", "principal@example.test", "Principal"
            ),
            "manager-token": VerifiedIdentity(
                "manager-firebase-uid", "manager@example.test", "User Manager"
            ),
            "teacher-token": VerifiedIdentity(
                "teacher-firebase-uid", "teacher@example.test", "Teacher"
            ),
        }
    )
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=verifier,
            user_account_admin_override=FakeUserAccountAdmin(),
        )
    )
    manager_headers = {"Authorization": "Bearer manager-token"}
    prohibited_create = client.post(
        "/api/mobile/admin/users",
        headers=manager_headers,
        json={
            "email": "overprivileged@example.test",
            "displayName": "Overprivileged",
            "roleId": privileged_role["id"],
        },
    )
    assert prohibited_create.status_code == 403
    prohibited_update = client.patch(
        f"/api/mobile/admin/users/{teacher['id']}",
        headers=manager_headers,
        json={"roleId": privileged_role["id"], "status": "active"},
    )
    assert prohibited_update.status_code == 403

    admin_headers = {"Authorization": "Bearer admin-token"}
    self_deactivate = client.post(
        f"/api/mobile/admin/users/{admin['id']}/deactivate",
        headers=admin_headers,
    )
    assert self_deactivate.status_code == 409
    assert "own account" in self_deactivate.json()["detail"]
    last_admin_deactivate = client.post(
        f"/api/mobile/admin/users/{admin['id']}/deactivate",
        headers=manager_headers,
    )
    assert last_admin_deactivate.status_code == 409
    assert "last active School Administrator" in last_admin_deactivate.json()["detail"]

    deactivated = client.post(
        f"/api/mobile/admin/users/{teacher['id']}/deactivate",
        headers=admin_headers,
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["status"] == "disabled"
    assert deactivated.json()["roleId"] == "role_teacher"
    with sqlite3.connect(config.database_path) as connection:
        assert connection.execute(
            "SELECT enabled FROM devices WHERE user_id=?", (teacher["id"],)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE entity_id=? AND action='deactivate_user'",
            (teacher["id"],),
        ).fetchone()[0] == 1

    denied = client.get(
        "/api/mobile/alerts", headers={"Authorization": "Bearer teacher-token"}
    )
    assert denied.status_code == 403
    disabled_session = client.get(
        "/api/mobile/session", headers={"Authorization": "Bearer teacher-token"}
    )
    assert disabled_session.status_code == 200
    assert disabled_session.json()["status"] == "disabled"

    reactivated = client.patch(
        f"/api/mobile/admin/users/{teacher['id']}",
        headers=admin_headers,
        json={"roleId": "role_teacher", "status": "active"},
    )
    assert reactivated.status_code == 200, reactivated.text
    with sqlite3.connect(config.database_path) as connection:
        assert connection.execute(
            "SELECT enabled FROM devices WHERE user_id=?", (teacher["id"],)
        ).fetchone()[0] == 0
    refreshed_device = client.put(
        "/api/mobile/device",
        headers={"Authorization": "Bearer teacher-token"},
        json={
            "registrationValue": "teacher-fcm-token-0123456789abcdef",
            "platform": "android",
            "deviceLabel": "Teacher phone",
            "installationId": "teacher-install",
        },
    )
    assert refreshed_device.status_code == 200
    with sqlite3.connect(config.database_path) as connection:
        assert connection.execute(
            "SELECT enabled FROM devices WHERE user_id=?", (teacher["id"],)
        ).fetchone()[0] == 1


def test_decision_update_notifies_other_original_alert_recipients_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    admin = repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    teacher = repository.upsert_firebase_user(
        "firebase-uid-teacher", "teacher@example.test", "Teacher Example"
    )
    assert repository.assign_mobile_user(
        teacher["id"], "role_teacher", "active", admin["id"]
    )
    repository.register_device(
        admin["id"], "admin-decision-token-1234567890", "android", "Admin phone", "admin-install"
    )
    repository.register_device(
        teacher["id"], "teacher-decision-token-1234567890", "android", "Teacher phone", "teacher-install"
    )
    camera_json = upload_camera(
        TestClient(create_app(config, repository, LocalMediaStore(config))), "Decision corridor"
    )
    camera = repository.get_camera(camera_json["id"])
    assert camera is not None
    alert = AlertService(repository).record_violence_candidate(
        camera,
        0.9999,
        detected_at=datetime.now(timezone.utc),
        source_start_seconds=2,
        source_end_seconds=7,
    )
    assert alert is not None
    original_notification = repository.create_notification(
        alert.id,
        "GuardAId safety alert",
        "Violence detected",
        json.dumps({"alertId": alert.id, "source": "alert_created", "type": "violence"}),
    )
    repository.set_notification_user_recipients(
        original_notification, [admin["id"], teacher["id"]]
    )

    class RecordingSender:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def send(self, registration_value, title, body, data, channel_id="guardaid_updates_v1"):
            self.calls.append(
                {
                    "token": registration_value,
                    "title": title,
                    "body": body,
                    "data": data,
                    "channel_id": channel_id,
                }
            )
            return "recorded-provider-id"

    sender = RecordingSender()
    monkeypatch.setattr(main_module, "build_notification_sender", lambda _config: sender)
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=FakeVerifier(),
        )
    )
    response = client.post(
        f"/api/mobile/alerts/{alert.id}/actions",
        headers={"Authorization": "Bearer valid-test-token"},
        json={"action": "confirm_resolved", "reason": "Staff confirmed the area is safe."},
    )
    assert response.status_code == 200, response.text
    assert len(sender.calls) == 1
    assert sender.calls[0]["token"] == "teacher-decision-token-1234567890"
    assert sender.calls[0]["channel_id"] == "guardaid_updates_v1"
    assert {"alertId", "action", "source"}.issubset(sender.calls[0]["data"])
    assert "reason" not in sender.calls[0]["data"]
    assert sender.calls[0]["data"]["source"] == "alert_decision"
    assert "Principal" in sender.calls[0]["body"]
    assert "resolved" in sender.calls[0]["body"]
    assert response.json()["alert"]["actions"][0]["performedBy"] == "Principal"


def test_urgent_alert_dispatch_uses_v7_and_skips_empty_audience(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    admin = repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    repository.register_device(
        admin["id"], "admin-urgent-token-1234567890", "android", "Admin phone", "admin-urgent"
    )
    client = TestClient(create_app(config, repository, LocalMediaStore(config)))
    camera_json = upload_camera(client, "Urgent corridor")
    camera = repository.get_camera(camera_json["id"])
    assert camera is not None
    alert = AlertService(repository).record_weapon_confirmation(
        camera,
        0.91,
        detection_qualifications={"weapon": {"moderate": ["gun"]}},
        weapon_class_scores={"gun": 0.91},
    )

    class RecordingSender:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def send(self, registration_value, title, body, data, channel_id="guardaid_updates_v1"):
            self.calls.append(
                {"token": registration_value, "channel_id": channel_id, "data": data}
            )
            return "urgent-provider-id"

    sender = RecordingSender()
    service = NotificationService(repository, sender)
    assert service.dispatch(alert) == 1
    assert sender.calls[0]["channel_id"] == "guardaid_urgent_alerts_v7"
    assert {"alertId", "type", "source"}.issubset(sender.calls[0]["data"])
    assert "evidence" not in sender.calls[0]["data"]
    assert "reason" not in sender.calls[0]["data"]

    # A second alert with no active eligible device must not leave a global row.
    with sqlite3.connect(config.database_path) as connection:
        connection.execute("UPDATE devices SET enabled=0 WHERE user_id=?", (admin["id"],))
        connection.commit()
    quiet = AlertService(repository).record_weapon_confirmation(
        camera,
        0.92,
        detection_qualifications={"weapon": {"moderate": ["gun"]}},
        weapon_class_scores={"gun": 0.92},
    )
    assert service.dispatch(quiet) == 0
    with sqlite3.connect(config.database_path) as connection:
        rows = connection.execute(
            "SELECT alert_id FROM notifications WHERE alert_id=?", (quiet.id,)
        ).fetchall()
    assert rows == []


def test_escalation_does_not_duplicate_original_recipients_or_create_empty_role_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    admin = repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    teacher = repository.upsert_firebase_user(
        "firebase-uid-teacher", "teacher@example.test", "Teacher Example"
    )
    assert repository.assign_mobile_user(
        teacher["id"], "role_teacher", "active", admin["id"]
    )
    repository.register_device(
        admin["id"], "admin-escalation-token-1234567890", "android", "Admin phone", "admin-escalation"
    )
    repository.register_device(
        teacher["id"], "teacher-escalation-token-1234567890", "android", "Teacher phone", "teacher-escalation"
    )
    camera_json = upload_camera(
        TestClient(create_app(config, repository, LocalMediaStore(config))),
        "Escalation corridor",
    )
    camera = repository.get_camera(camera_json["id"])
    assert camera is not None
    alert = AlertService(repository).record_violence_candidate(camera, 0.9999)
    assert alert is not None
    original = repository.create_notification(
        alert.id,
        "GuardAId safety alert",
        "Violence detected",
        json.dumps({"alertId": alert.id, "source": "alert_created", "type": "violence"}),
    )
    repository.set_notification_user_recipients(original, [admin["id"], teacher["id"]])

    class RecordingSender:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def send(self, registration_value, title, body, data, channel_id="guardaid_updates_v1"):
            self.calls.append({"token": registration_value, "channel_id": channel_id, "data": data})
            return "recorded-provider-id"

    sender = RecordingSender()
    monkeypatch.setattr(main_module, "build_notification_sender", lambda _config: sender)
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=FakeVerifier(),
        )
    )
    response = client.post(
        f"/api/mobile/alerts/{alert.id}/actions",
        headers={"Authorization": "Bearer valid-test-token"},
        json={
            "action": "confirm_only",
            "reason": "Please notify the school response team.",
            "recipientRoleIds": ["role_teacher"],
        },
    )
    assert response.status_code == 200, response.text
    # Confirm-only sends one urgent broadcast to the selected role. The actor
    # is not sent their own routine decision update.
    assert [call["token"] for call in sender.calls] == [
        "teacher-escalation-token-1234567890"
    ]
    assert sender.calls[0]["channel_id"] == "guardaid_urgent_alerts_v7"
    with sqlite3.connect(config.database_path) as connection:
        rows = connection.execute(
            "SELECT data_payload_json FROM notifications WHERE alert_id=? ORDER BY created_at",
            (alert.id,),
        ).fetchall()
    assert len(rows) == 2
    assert json.loads(rows[1][0]).get("source") == "alert_broadcast"


def test_original_alert_recipient_snapshot_does_not_follow_later_role_changes(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    camera_json = upload_camera(
        TestClient(create_app(config, repository, LocalMediaStore(config))),
        "Snapshot corridor",
    )
    camera = repository.get_camera(camera_json["id"])
    assert camera is not None
    alert = AlertService(repository).record_violence_candidate(camera, 0.9999)
    assert alert is not None
    notification_id = repository.create_notification(
        alert.id,
        "Alert",
        "Alert",
        json.dumps({"source": "alert_created"}),
    )
    first = repository.upsert_firebase_user("snapshot-1", "one@example.test", "One")
    second = repository.upsert_firebase_user("snapshot-2", "two@example.test", "Two")
    repository.set_notification_user_recipients(
        notification_id, [first["id"], second["id"]]
    )
    assert set(repository.list_original_alert_recipient_user_ids(alert.id)) == {
        first["id"],
        second["id"],
    }


def test_deleting_custom_role_moves_its_only_assigned_users_to_pending_and_disables_devices(
    tmp_path: Path,
) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    admin = repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=FakeVerifier(),
        )
    )
    headers = {"Authorization": "Bearer valid-test-token"}
    created = client.post(
        "/api/mobile/admin/roles",
        headers=headers,
        json={
            "name": "Temporary responders",
            "description": "Short-term alert reviewers.",
            "sensitivityMode": "moderate",
        },
    )
    assert created.status_code == 201, created.text
    role = created.json()
    assert role["isSystem"] is False

    pending = repository.upsert_firebase_user(
        "firebase-uid-responder", "responder@example.test", "Responder"
    )
    assigned = repository.assign_mobile_user(
        pending["id"], role["id"], "active", admin["id"]
    )
    assert assigned
    repository.register_device(
        pending["id"],
        "fcm-device-token-for-role-delete-test-12345",
        "android",
        "Test phone",
        "installation-role-delete-test",
    )
    repository.update_mobile_settings(
        True,
        ["role_school_administrator", role["id"]],
        admin["id"],
    )

    deleted = client.delete(
        f"/api/mobile/admin/roles/{role['id']}", headers=headers
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["assignedUsers"] == 1
    assert deleted.json()["pendingUsers"] == 1
    assert deleted.json()["disabledDevices"] == 1

    saved_user = next(
        item for item in repository.list_mobile_users() if item["id"] == pending["id"]
    )
    assert saved_user["status"] == "pending"
    assert saved_user["role_id"] is None
    assert role["id"] not in {
        item["id"] for item in repository.list_mobile_roles()
    }
    assert role["id"] not in repository.get_mobile_settings()["borderline_recipient_role_ids"]
    with sqlite3.connect(config.database_path) as connection:
        assert connection.execute(
            "SELECT enabled FROM devices WHERE user_id=?", (pending["id"],)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action='delete_role' AND entity_id=?",
            (role["id"],),
        ).fetchone()[0] == 1

    protected = client.delete(
        "/api/mobile/admin/roles/role_school_administrator", headers=headers
    )
    assert protected.status_code == 409
    assert protected.json()["detail"] == "System roles cannot be deleted."


def test_cannot_delete_the_last_active_role_manager(tmp_path: Path) -> None:
    config = sqlite_runtime(tmp_path)
    repository = SQLiteRepository(config)
    admin = repository.bootstrap_first_administrator(
        "firebase-uid-1", "principal@example.test", "Principal"
    )
    role = repository.create_mobile_role(
        "Sole role manager",
        "Temporary administration role.",
        "moderate",
        {"can_manage_roles": True},
        admin["id"],
    )
    assert role is not None
    # Seed the role assignment as setup data. The user-management API now
    # correctly forbids self-role changes, so this test must not use the
    # production assignment method to create its intentionally unsafe state.
    with sqlite3.connect(config.database_path) as connection:
        connection.execute("DELETE FROM user_roles WHERE user_id=?", (admin["id"],))
        connection.execute(
            "INSERT INTO user_roles(user_id,role_id,assigned_by_user_id,assigned_at) "
            "VALUES (?,?,?,?)",
            (admin["id"], role["id"], admin["id"], datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=FakeVerifier(),
        )
    )

    response = client.delete(
        f"/api/mobile/admin/roles/{role['id']}",
        headers={"Authorization": "Bearer valid-test-token"},
    )

    assert response.status_code == 409
    assert "last active administrator" in response.json()["detail"]
    assert any(item["id"] == role["id"] for item in repository.list_mobile_roles())


def test_local_media_rejects_path_traversal(tmp_path: Path) -> None:
    store = LocalMediaStore(sqlite_runtime(tmp_path))
    with pytest.raises(ValueError, match="Invalid local media path"):
        store.materialize("../private-file.mp4")
