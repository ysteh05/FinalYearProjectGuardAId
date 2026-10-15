from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app.config import RuntimeConfig
from app.domain import (
    INITIAL_ALERT_DELIVERY_MAX_AGE_SECONDS,
    AlertNotificationEscalationSettings,
    Camera,
    EvidenceRecord,
    SensitivityMode,
)
from app.firebase_services import VerifiedIdentity
from app.main import create_app
from app.media import LocalMediaStore
from app.notification_escalation import AlertNotificationEscalationWorker
from app.notification_outbox import MobileNotificationOutboxWorker
from app.services import AlertService, NotificationService
from app.sqlite_repository import SQLiteRepository


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
        weapon_checkpoint=tmp_path / "weapon.pt",
        hf_cache=tmp_path / "hf-cache",
    )


class RecordingSender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, token, title, body, data, channel_id="guardaid_updates_v1"):
        self.calls.append(
            {"token": token, "title": title, "body": body, "data": data, "channel": channel_id}
        )
        return f"provider-{len(self.calls)}"


class EscalationTokenVerifier:
    def verify(self, id_token: str) -> VerifiedIdentity:
        identities = {
            "admin-token": VerifiedIdentity(
                "firebase-escalation-admin", "admin@example.test", "Admin"
            ),
            "teacher-token": VerifiedIdentity(
                "firebase-escalation-teacher", "teacher@example.test", "Teacher"
            ),
            "pending-token": VerifiedIdentity(
                "firebase-escalation-pending", "pending@example.test", "Pending"
            ),
            "roleless-token": VerifiedIdentity(
                "firebase-escalation-roleless", "roleless@example.test", "Roleless"
            ),
        }
        if id_token not in identities:
            raise ValueError("Invalid test token")
        return identities[id_token]


def make_unanswered_alert(
    tmp_path: Path, *, first_after_seconds: int = 60, all_after_seconds: int = 120
):
    repository = SQLiteRepository(runtime(tmp_path))
    admin = repository.bootstrap_first_administrator(
        "firebase-escalation-admin", "admin@example.test", "Admin"
    )
    teacher = repository.upsert_firebase_user(
        "firebase-escalation-teacher", "teacher@example.test", "Teacher"
    )
    assert repository.assign_mobile_user(
        teacher["id"], "role_teacher", "active", admin["id"]
    )
    admin_token = "admin-escalation-device-token-123456789"
    teacher_token = "teacher-escalation-device-token-123456789"
    repository.register_device(
        admin["id"], admin_token, "android", "Admin phone", "admin-install"
    )
    repository.register_device(
        teacher["id"], teacher_token, "android", "Teacher phone", "teacher-install"
    )
    camera = Camera(
        name="Hallway",
        location="Block A",
        storage_path="cameras/hallway/source.mp4",
        original_filename="source.mp4",
    )
    repository.save_camera(camera)
    saved_camera = repository.get_camera(camera.id)
    assert saved_camera is not None
    alert = AlertService(repository).record_violence_candidate(saved_camera, 0.9999)
    assert alert is not None

    initial_notification_id = repository.create_notification(
        alert.id,
        "GuardAId safety alert",
        "Violence detected at Hallway",
        json.dumps({"alertId": alert.id, "type": "violence", "source": "alert_created"}),
    )
    repository.set_notification_user_recipients(initial_notification_id, [admin["id"]])
    admin_device_id = next(
        item["id"] for item in repository.list_enabled_devices()
        if item["user_id"] == admin["id"]
    )
    repository.record_notification_delivery(
        initial_notification_id, admin_device_id, "sent", "legacy-provider-id", None
    )
    repository.schedule_alert_notification_escalation(
        alert.id, initial_notification_id, first_after_seconds, all_after_seconds
    )
    with repository._connect() as connection:
        notified_at = datetime.fromisoformat(
            connection.execute(
                "SELECT created_at FROM notifications WHERE id=?",
                (initial_notification_id,),
            ).fetchone()[0]
        )
    return repository, alert, admin, teacher, admin_token, teacher_token, notified_at


def test_broadcast_preview_defaults_roles_from_detection_qualification_not_legacy_label(
    tmp_path: Path,
) -> None:
    repository, alert, _, _, _, _, _ = make_unanswered_alert(tmp_path)
    config = runtime(tmp_path)
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=EscalationTokenVerifier(),
        )
    )
    role_sensitivities: dict[str, str] | None = None

    for sensitivity in ("sensitive", "moderate", "certain"):
        with repository._connect() as connection:
            connection.execute(
                "UPDATE alerts SET sensitivity_mode=? WHERE id=?",
                (sensitivity, alert.id),
            )
            connection.commit()

        response = client.get(
            f"/api/mobile/alerts/{alert.id}/broadcast-preview",
            headers={"Authorization": "Bearer admin-token"},
        )
        assert response.status_code == 200
        roles = response.json()["roles"]
        if role_sensitivities is None:
            role_sensitivities = {
                role["id"]: role["sensitivityMode"] for role in roles
            }
            assert role_sensitivities["role_security_personnel"] == "sensitive"
            assert role_sensitivities["role_school_administrator"] == "moderate"
            assert role_sensitivities["role_teacher"] == "certain"
        expected = set(role_sensitivities)
        assert {role["id"] for role in roles if role["defaultSelected"]} == expected


def test_initial_notification_sensitivity_matrix_targets_expected_roles(
    tmp_path: Path,
) -> None:
    score_by_qualification = {
        "sensitive": 0.93,
        "moderate": 0.997,
        "certain": 0.9999,
    }
    expected_recipients = {
        "sensitive": {"security-test-token-123456789012"},
        "moderate": {
            "security-test-token-123456789012",
            "admin-test-token-123456789012",
        },
        "certain": {
            "security-test-token-123456789012",
            "admin-test-token-123456789012",
            "teacher-test-token-123456789012",
        },
    }

    for sensitivity, expected_tokens in expected_recipients.items():
        case_dir = tmp_path / sensitivity
        case_dir.mkdir()
        repository = SQLiteRepository(runtime(case_dir))
        admin = repository.bootstrap_first_administrator(
            f"firebase-matrix-admin-{sensitivity}",
            "matrix-admin@example.test",
            "Admin",
        )
        security = repository.upsert_firebase_user(
            f"firebase-matrix-security-{sensitivity}",
            "matrix-security@example.test",
            "Security",
        )
        teacher = repository.upsert_firebase_user(
            f"firebase-matrix-teacher-{sensitivity}",
            "matrix-teacher@example.test",
            "Teacher",
        )
        assert repository.assign_mobile_user(
            security["id"], "role_security_personnel", "active", admin["id"]
        )
        assert repository.assign_mobile_user(
            teacher["id"], "role_teacher", "active", admin["id"]
        )
        for user, token, installation in (
            (admin, "admin-test-token-123456789012", "admin"),
            (security, "security-test-token-123456789012", "security"),
            (teacher, "teacher-test-token-123456789012", "teacher"),
        ):
            repository.register_device(
                user["id"], token, "android", f"{installation} test device", installation
            )

        camera = Camera(
            name="Sensitivity matrix camera",
            location="Test campus",
            storage_path="cameras/sensitivity/source.mp4",
            original_filename="source.mp4",
        )
        repository.save_camera(camera)
        saved_camera = repository.get_camera(camera.id)
        assert saved_camera is not None
        alert = AlertService(repository).record_violence_candidate(
            saved_camera, score_by_qualification[sensitivity]
        )
        assert alert is not None

        sender = RecordingSender()
        sent_count = NotificationService(repository, sender).dispatch(alert)
        assert sent_count == len(expected_tokens)
        assert {call["token"] for call in sender.calls} == expected_tokens


def test_escalation_repeats_original_audience_then_notifies_all_active_users(
    tmp_path: Path,
) -> None:
    repository, alert, admin, teacher, admin_token, teacher_token, notified_at = (
        make_unanswered_alert(tmp_path)
    )
    sender = RecordingSender()
    worker = AlertNotificationEscalationWorker(repository, sender)

    assert worker.process_due(notified_at + timedelta(seconds=61)) == 1
    assert len(sender.calls) == 1
    assert sender.calls[0]["token"] == admin_token
    assert sender.calls[0]["channel"] == "guardaid_urgent_repeat_v1"
    assert sender.calls[0]["data"]["notificationStage"] == "first_repeat"

    assert worker.process_due(notified_at + timedelta(seconds=121)) == 2
    assert len(sender.calls) == 3
    assert {call["token"] for call in sender.calls[1:]} == {admin_token, teacher_token}
    assert all(
        call["channel"] == "guardaid_urgent_escalated_v1"
        and call["data"]["notificationStage"] == "all_users_escalation"
        for call in sender.calls[1:]
    )
    assert alert.id in repository.list_all_user_escalated_alert_ids(teacher["id"])
    assert alert.id in repository.list_all_user_escalated_alert_ids(admin["id"])
    assert worker.process_due(notified_at + timedelta(minutes=10)) == 0
    assert len(sender.calls) == 3


def test_late_eligible_account_joins_original_first_repeat_deadline(
    tmp_path: Path,
) -> None:
    repository, alert, _, teacher, _, teacher_token, _ = make_unanswered_alert(tmp_path)
    now = datetime.now(timezone.utc)
    first_due = now - timedelta(seconds=5)
    all_due = now + timedelta(seconds=55)
    with repository._connect() as connection:
        phase_id = connection.execute(
            "SELECT id FROM mobile_alert_reminder_phases WHERE alert_id=? AND phase_status='active'",
            (alert.id,),
        ).fetchone()["id"]
        connection.execute(
            "UPDATE mobile_alert_reminder_phases SET first_due_at=?,all_due_at=? WHERE id=?",
            (first_due.isoformat(), all_due.isoformat(), phase_id),
        )
        connection.commit()

    sender = RecordingSender()
    worker = AlertNotificationEscalationWorker(repository, sender)
    assert worker.process_due(now) == 1
    assert sender.calls[0]["data"]["notificationStage"] == "first_repeat"

    # The higher-threshold account becomes eligible after the stage-1 push.
    assert NotificationService(repository, sender).dispatch(alert) == 1
    initial_call = sender.calls[-1]
    assert initial_call["token"] == teacher_token
    assert initial_call["data"]["notificationStage"] == "initial_alert"

    assert worker.process_due(now + timedelta(seconds=1)) == 1
    late_repeat = sender.calls[-1]
    assert late_repeat["token"] == teacher_token
    assert late_repeat["data"]["notificationStage"] == "first_repeat"
    assert late_repeat["data"]["lateAudience"] == "true"
    assert len(sender.calls) == 3


def test_ordinary_qualification_can_requeue_after_borderline_only_push_is_cancelled(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(runtime(tmp_path))
    admin = repository.bootstrap_first_administrator(
        "firebase-borderline-requeue-admin", "borderline-requeue@example.test", "Admin"
    )
    repository.set_borderline_role_enabled(
        "role_school_administrator", True, admin["id"]
    )
    token = "borderline-requeue-device-token-123456789"
    repository.register_device(admin["id"], token, "android", "Admin device", "admin-requeue")
    settings = repository.get_settings()
    repository.save_settings(
        settings.model_copy(
            update={
                "vlm": settings.vlm.model_copy(
                    update={"borderline_review_enabled": True}
                ),
                "config_version": settings.config_version + 1,
                "updated_by": "test",
            }
        )
    )
    camera = Camera(
        name="Review transition camera",
        location="Test campus",
        storage_path="cameras/review-transition/source.mp4",
        original_filename="source.mp4",
    )
    repository.save_camera(camera)
    saved_camera = repository.get_camera(camera.id)
    assert saved_camera is not None
    service = AlertService(repository)
    now = datetime.now(timezone.utc)
    first = service.record_violence_candidate(
        saved_camera, 0.95, detected_at=now, source_start_seconds=0, source_end_seconds=4
    )
    assert first is not None
    review_task_id = repository.create_borderline_review_task(
        alert_id=first.id,
        camera_id=saved_camera.id,
        detection_event_key=first.detection_event_key,
        detector_confidence=first.confidence,
        source_start_seconds=0,
        source_end_seconds=4,
        context_path="runtime_data/tmp/borderline_reviews/test-context.mp4",
        model_version="test-vlm",
        prompt_version="test-prompt-v1",
        config_version=first.config_version,
        vlm_settings=repository.get_settings().vlm.model_dump(mode="json"),
    )
    repository.finish_borderline_review_task(
        review_task_id,
        status="positive",
        raise_alert=True,
        uncertainty="low",
        brief_reason="Positive review for recipient test.",
    )
    first = next(item for item in repository.list_alerts() if item.id == first.id)
    recipients = repository.list_enabled_devices_for_alert(first)
    assert recipients[0]["recipient_reasons"] == ["borderline_review"]
    initial = repository.queue_initial_mobile_alert_notification(
        first,
        recipients=recipients,
        title="Safety alert",
        body="Review transition camera",
        channel_id="guardaid_urgent_alerts_v7",
        first_after_seconds=60,
        all_after_seconds=120,
        started_at=now,
        borderline_review_task_id=review_task_id,
    )
    assert initial["outboxId"]

    # Turning off the optional feature before FCM delivery cancels only this
    # Borderline-only recipient. The later Moderate qualification is ordinary.
    settings = repository.get_settings()
    repository.save_settings(
        settings.model_copy(
            update={
                "vlm": settings.vlm.model_copy(
                    update={"borderline_review_enabled": False}
                ),
                "config_version": settings.config_version + 1,
                "updated_by": "test",
            }
        )
    )
    sender = RecordingSender()
    MobileNotificationOutboxWorker(repository, sender).process_pending()
    assert sender.calls == []

    upgraded = service.record_violence_candidate(
        saved_camera,
        0.997,
        detected_at=now + timedelta(seconds=1),
        source_start_seconds=2,
        source_end_seconds=6,
    )
    assert upgraded is not None and upgraded.id == first.id
    assert NotificationService(repository, sender).dispatch(upgraded) == 1
    assert [call["token"] for call in sender.calls] == [token]
    assert sender.calls[0]["data"]["notificationStage"] == "initial_alert"
    audience = repository.get_alert_notification_audience(first.id, admin["id"])
    assert audience is not None
    assert audience["qualification_reasons"] == [
        "borderline_review", "ordinary_threshold"
    ]


@pytest.mark.parametrize("sensitivity", list(SensitivityMode))
def test_all_active_users_can_review_any_sensitivity_without_notification_device(
    tmp_path: Path, sensitivity: SensitivityMode,
) -> None:
    repository, alert, admin, teacher, admin_token, teacher_token, _ = make_unanswered_alert(tmp_path)
    assert repository.update_mobile_role(
        "role_school_administrator", admin["id"], sensitivity_mode="sensitive"
    )
    alert = repository.save_alert(alert.model_copy(update={"sensitivity_mode": sensitivity}))
    sender = RecordingSender()
    NotificationService(repository, sender).dispatch(alert)
    # The admin already received the initial alert in the fixture. Changing
    # the compatibility label cannot trigger another beep; the newly eligible
    # teacher receives one notification based on the saved score qualifications.
    assert {call["token"] for call in sender.calls} == {teacher_token}

    # Visibility must not depend on an FCM device, a sent push, or stage two.
    with repository._connect() as connection:
        connection.execute("UPDATE devices SET enabled=0 WHERE user_id=?", (teacher["id"],))
        connection.commit()
    config = runtime(tmp_path)
    media_store = LocalMediaStore(config)
    stored = media_store.save_evidence_bytes(
        alert.id, "weapon_screenshot", ".png", "image/png", b"access-control-test-evidence"
    )
    evidence = EvidenceRecord(
        alert_id=alert.id, media_type="weapon_screenshot",
        storage_path=stored.storage_path, content_type=stored.content_type,
    )
    repository.save_evidence(evidence, stored.size_bytes)
    client = TestClient(
        create_app(
            config,
            repository,
            media_store,
            token_verifier_override=EscalationTokenVerifier(),
        )
    )
    headers = {"Authorization": "Bearer teacher-token"}
    url = f"/api/mobile/alerts/{alert.id}"
    assert client.get(url, headers=headers).status_code == 200
    listed = client.get("/api/mobile/alerts", headers=headers).json()
    assert any(item["id"] == alert.id for item in listed)
    assert repository.list_all_user_escalated_alert_ids(teacher["id"]) == set()
    media_url = f"/api/mobile/evidence/{evidence.id}/media"
    assert client.get(media_url, headers=headers).content == b"access-control-test-evidence"
    assert client.post(f"{url}/read", headers=headers).status_code == 200

    protected_urls = ("/api/mobile/alerts", url, media_url)
    for protected_url in protected_urls:
        assert client.get(protected_url).status_code == 401
        assert client.get(protected_url, headers={"Authorization": "Bearer pending-token"}).status_code == 403
    assert repository.assign_mobile_user(teacher["id"], "role_teacher", "disabled", admin["id"])
    for protected_url in protected_urls:
        assert client.get(protected_url, headers=headers).status_code == 403
    assert client.post(f"{url}/read", headers=headers).status_code == 403


def test_active_roleless_user_cannot_access_alerts_or_evidence(
    tmp_path: Path,
) -> None:
    repository, alert, _, _, _, _, _ = make_unanswered_alert(tmp_path)
    roleless = repository.upsert_firebase_user(
        "firebase-escalation-roleless", "roleless@example.test", "Roleless"
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE users SET status='active' WHERE id=?", (roleless["id"],)
        )
        connection.commit()

    config = runtime(tmp_path)
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=EscalationTokenVerifier(),
        )
    )
    headers = {"Authorization": "Bearer roleless-token"}
    assert client.get("/api/mobile/alerts", headers=headers).status_code == 403
    assert client.get(
        f"/api/mobile/alerts/{alert.id}", headers=headers
    ).status_code == 403
    assert client.get(
        "/api/mobile/evidence/nonexistent/media", headers=headers
    ).status_code == 403


def test_overdue_worker_sends_only_the_highest_due_stage(tmp_path: Path) -> None:
    repository, alert, _, _, _, _, notified_at = make_unanswered_alert(tmp_path)
    sender = RecordingSender()
    worker = AlertNotificationEscalationWorker(repository, sender)

    assert worker.process_due(notified_at + timedelta(seconds=121)) == 2
    assert len(sender.calls) == 2
    assert all(
        call["data"]["notificationStage"] == "all_users_escalation"
        for call in sender.calls
    )
    with repository._connect() as connection:
        phase_id = connection.execute(
            "SELECT id FROM mobile_alert_reminder_phases WHERE alert_id=?",
            (alert.id,),
        ).fetchone()[0]
        statuses = {
            row["stage"]: row["status"]
            for row in connection.execute(
                "SELECT stage,status FROM mobile_alert_reminder_dispatches WHERE phase_id=?",
                (phase_id,),
            )
        }
    assert statuses == {1: "skipped_superseded", 2: "sent"}


def test_persisted_reminder_phase_resumes_after_repository_restart(
    tmp_path: Path,
) -> None:
    _, alert, _, _, admin_token, teacher_token, notified_at = make_unanswered_alert(
        tmp_path
    )
    restarted_repository = SQLiteRepository(runtime(tmp_path))
    sender = RecordingSender()
    worker = AlertNotificationEscalationWorker(restarted_repository, sender)

    assert worker.process_due(notified_at + timedelta(seconds=61)) == 1
    assert [call["token"] for call in sender.calls] == [admin_token]
    assert sender.calls[0]["data"]["notificationStage"] == "first_repeat"

    assert worker.process_due(notified_at + timedelta(seconds=121)) == 2
    assert {call["token"] for call in sender.calls[1:]} == {
        admin_token,
        teacher_token,
    }
    assert all(
        call["data"]["notificationStage"] == "all_users_escalation"
        for call in sender.calls[1:]
    )
    assert worker.process_due(notified_at + timedelta(minutes=10)) == 0
    assert len(sender.calls) == 3


def test_initial_outbox_delivery_checks_current_workflow_revision(tmp_path: Path) -> None:
    repository, alert, _, teacher, _, _, _ = make_unanswered_alert(tmp_path)
    service = NotificationService(repository, RecordingSender())

    assert service.dispatch(alert, process_pending=False) == 1
    with repository._connect() as connection:
        outbox = connection.execute(
            "SELECT id,data_payload_json FROM mobile_notification_outbox "
            "WHERE json_extract(data_payload_json,'$.source')='alert_created' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert outbox is not None
    payload = json.loads(outbox["data_payload_json"])
    assert payload["workflowStatus"] == "awaiting_confirmation"
    assert repository.prepare_mobile_alert_initial_delivery(outbox["id"]) == {teacher["id"]}

    with repository._connect() as connection:
        connection.execute(
            "UPDATE mobile_alert_workflows SET workflow_status='resolved',revision=revision+1 "
            "WHERE alert_id=?",
            (alert.id,),
        )
        connection.commit()
    assert repository.prepare_mobile_alert_initial_delivery(outbox["id"]) == set()


def test_initial_alert_push_expires_instead_of_replaying_after_login(
    tmp_path: Path,
) -> None:
    repository, alert, _, _, _, _, _ = make_unanswered_alert(tmp_path)
    service = NotificationService(repository, RecordingSender())
    assert service.dispatch(alert, process_pending=False) == 1
    with repository._connect() as connection:
        outbox = connection.execute(
            "SELECT id FROM mobile_notification_outbox "
            "WHERE json_extract(data_payload_json,'$.source')='alert_created' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert outbox is not None
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=INITIAL_ALERT_DELIVERY_MAX_AGE_SECONDS + 1
    )
    assert repository.prepare_mobile_alert_initial_delivery(
        outbox["id"], now=expires_at
    ) == set()


def test_startup_reconciles_positive_borderline_result_into_durable_outbox(
    tmp_path: Path,
) -> None:
    from app.inference.orchestrator import InferenceOrchestrator

    config = runtime(tmp_path)
    repository = SQLiteRepository(config)
    admin = repository.bootstrap_first_administrator(
        "firebase-borderline-recovery-admin", "recovery@example.test", "Admin"
    )
    repository.set_borderline_role_enabled(
        "role_school_administrator", True, admin["id"]
    )
    token = "borderline-recovery-device-token-123456789"
    repository.register_device(admin["id"], token, "android", "Admin", "recovery-install")
    current = repository.get_settings()
    repository.save_settings(
        current.model_copy(
            update={
                "vlm": current.vlm.model_copy(
                    update={"borderline_review_enabled": True}
                ),
                "config_version": current.config_version + 1,
                "updated_by": "test",
            }
        )
    )
    camera = Camera(
        name="Recovery camera",
        location="Test campus",
        storage_path="cameras/recovery/source.mp4",
        original_filename="source.mp4",
    )
    repository.save_camera(camera)
    saved_camera = repository.get_camera(camera.id)
    assert saved_camera is not None
    alert = AlertService(repository).record_violence_candidate(
        saved_camera, 0.95, source_start_seconds=0, source_end_seconds=4
    )
    assert alert is not None
    task_id = repository.create_borderline_review_task(
        alert_id=alert.id,
        camera_id=camera.id,
        detection_event_key=alert.detection_event_key,
        detector_confidence=0.95,
        source_start_seconds=0,
        source_end_seconds=4,
        context_path=str(tmp_path / "tmp" / "borderline_reviews" / "complete.mp4"),
        model_version="test-vlm",
        prompt_version="test-prompt-v1",
        config_version=alert.config_version,
        vlm_settings=repository.get_settings().vlm.model_dump(mode="json"),
    )
    repository.finish_borderline_review_task(
        task_id,
        status="positive",
        raise_alert=True,
        uncertainty="low",
        brief_reason="Positive test result.",
    )
    assert repository.list_pending_borderline_notification_tasks() == [
        {"id": task_id, "alert_id": alert.id}
    ]

    orchestrator = InferenceOrchestrator(
        config, repository, LocalMediaStore(config), notification_sender=RecordingSender()
    )
    try:
        assert orchestrator.reconcile_pending_borderline_notifications() == 1
        assert repository.list_pending_borderline_notification_tasks() == []
        task = repository.list_borderline_review_tasks(alert.id)[0]
        assert task["status"] == "positive"
        with repository._connect() as connection:
            outbox = connection.execute(
                "SELECT status,event_key,data_payload_json FROM mobile_notification_outbox "
                "WHERE json_extract(data_payload_json,'$.source')='alert_created'"
            ).fetchone()
        assert outbox is not None and outbox["status"] == "pending"
        assert json.loads(outbox["data_payload_json"])["notificationStage"] == "initial_alert"
    finally:
        orchestrator.shutdown()


def test_two_reminder_workers_cannot_send_the_same_stage_twice(
    tmp_path: Path,
) -> None:
    repository, alert, _, _, admin_token, _, notified_at = make_unanswered_alert(
        tmp_path
    )
    second_repository = SQLiteRepository(runtime(tmp_path))
    sender = RecordingSender()
    workers = [
        AlertNotificationEscalationWorker(repository, sender),
        AlertNotificationEscalationWorker(second_repository, sender),
    ]
    start_together = Barrier(2)
    due_at = notified_at + timedelta(seconds=61)

    def process(worker: AlertNotificationEscalationWorker) -> int:
        start_together.wait(timeout=5)
        return worker.process_due(due_at)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(process, workers))

    assert sorted(results) == [0, 1]
    assert len(sender.calls) == 1
    assert sender.calls[0]["token"] == admin_token
    assert sender.calls[0]["data"]["notificationStage"] == "first_repeat"
    assert sum(worker.process_due(due_at) for worker in workers) == 0
    assert len(sender.calls) == 1
    with repository._connect() as connection:
        phase_id = connection.execute(
            "SELECT id FROM mobile_alert_reminder_phases WHERE alert_id=?",
            (alert.id,),
        ).fetchone()["id"]
        dispatches = connection.execute(
            "SELECT COUNT(*) FROM mobile_alert_reminder_dispatches "
            "WHERE phase_id=? AND stage=1 AND status='sent'",
            (phase_id,),
        ).fetchone()[0]
    assert dispatches == 1


def test_stale_reminder_claim_is_retried_after_worker_interruption(tmp_path: Path) -> None:
    repository, alert, _, _, admin_token, _, notified_at = make_unanswered_alert(
        tmp_path, first_after_seconds=15, all_after_seconds=120
    )
    phase_id = repository.start_mobile_alert_reminder_phase(
        alert.id,
        phase_kind="awaiting_decision",
        started_at=notified_at,
        first_after_seconds=15,
        all_after_seconds=120,
    )
    assert phase_id is not None
    claimed = repository.claim_mobile_alert_reminder_dispatch(
        phase_id, 1, notified_at + timedelta(seconds=16)
    )
    assert claimed is not None

    sender = RecordingSender()
    worker = AlertNotificationEscalationWorker(repository, sender)
    assert worker.process_due(notified_at + timedelta(seconds=77)) == 1
    assert len(sender.calls) == 1
    assert sender.calls[0]["token"] == admin_token
    assert sender.calls[0]["data"]["notificationStage"] == "first_repeat"


def test_queued_reminder_is_suppressed_if_handler_takes_ownership_before_send(
    tmp_path: Path,
) -> None:
    repository, alert, admin, teacher, _, teacher_token, notified_at = make_unanswered_alert(tmp_path)
    sender = RecordingSender()
    worker = AlertNotificationEscalationWorker(repository, sender)
    confirmed = repository.record_mobile_workflow_action(
        alert.id,
        admin["id"],
        "confirm_only",
        comment=None,
        recipient_role_ids=["role_teacher"],
        allow_empty_audience=False,
        idempotency_key="confirm-for-stale-reminder-check",
        first_after_seconds=60,
        all_after_seconds=120,
    )
    assert confirmed is not None
    assert worker._outbox.process_pending() == 1
    assert sender.calls[0]["token"] == teacher_token
    assert sender.calls[0]["data"]["notificationStage"] == "initial_broadcast"

    phase_id = confirmed["workflow"]["active_phase_id"]
    assert phase_id
    with repository._connect() as connection:
        phase_started_at = datetime.fromisoformat(
            connection.execute(
                "SELECT started_at FROM mobile_alert_reminder_phases WHERE id=?",
                (phase_id,),
            ).fetchone()["started_at"]
        )
    first_repeat_at = phase_started_at + timedelta(seconds=61)
    claimed = repository.claim_mobile_alert_reminder_dispatch(
        phase_id, 1, first_repeat_at
    )
    assert claimed is not None
    queued = repository.queue_mobile_alert_reminder_notification(
        phase_id,
        1,
        recipients=repository.list_enabled_devices_for_roles(["role_teacher"]),
        title="GuardAId alert reminder",
        body="No handler has taken ownership.",
        channel_id="guardaid_urgent_repeat_v1",
        data={
            "alertId": alert.id,
            "source": "alert_reminder",
            "reminderPhaseId": phase_id,
            "reminderStage": "1",
        },
        event_key=f"reminder:{phase_id}:1",
        now=first_repeat_at,
    )
    assert queued["status"] == "queued"

    taken = repository.record_mobile_workflow_action(
        alert.id,
        admin["id"],
        "take_ownership",
        comment=None,
        recipient_role_ids=[],
        allow_empty_audience=False,
        idempotency_key="take-before-queued-reminder-send",
        first_after_seconds=60,
        all_after_seconds=120,
    )
    assert taken is not None

    # The action's ordinary update may be sent, but the canceled urgent
    # reminder must be discarded by the outbox pre-send revalidation.
    worker._outbox.process_pending(phase_started_at + timedelta(seconds=62))
    assert all(call["data"].get("source") != "alert_reminder" for call in sender.calls)
    assert any(call["data"].get("source") == "alert_decision" for call in sender.calls)
    with repository._connect() as connection:
        dispatch = connection.execute(
            "SELECT status,error_code FROM mobile_alert_reminder_dispatches "
            "WHERE phase_id=? AND stage=1",
            (phase_id,),
        ).fetchone()
        outbox = connection.execute(
            "SELECT status,last_error_code FROM mobile_notification_outbox WHERE id=?",
            (queued["outboxId"],),
        ).fetchone()
    assert dispatch["status"] == "skipped_stopped"
    assert dispatch["error_code"] == "WorkflowChanged"
    assert outbox["status"] == "no_recipients"
    assert outbox["last_error_code"] == "WorkflowChanged"


def test_handler_action_between_reminder_claim_and_queue_stops_the_stage(
    tmp_path: Path,
) -> None:
    repository, alert, admin, _, _, _, notified_at = make_unanswered_alert(tmp_path)
    phase_id = repository.start_mobile_alert_reminder_phase(
        alert.id,
        phase_kind="awaiting_decision",
        started_at=notified_at,
        first_after_seconds=60,
        all_after_seconds=120,
    )
    assert phase_id is not None
    assert repository.claim_mobile_alert_reminder_dispatch(
        phase_id, 1, notified_at + timedelta(seconds=61)
    ) is not None
    taken = repository.record_mobile_workflow_action(
        alert.id,
        admin["id"],
        "confirm_and_handle",
        comment=None,
        recipient_role_ids=[],
        allow_empty_audience=False,
        idempotency_key="handle-between-claim-and-queue",
        first_after_seconds=60,
        all_after_seconds=120,
    )
    assert taken is not None
    result = repository.queue_mobile_alert_reminder_notification(
        phase_id,
        1,
        recipients=repository.list_enabled_devices_for_all_active_users(),
        title="Stale reminder",
        body="This must not be queued.",
        channel_id="guardaid_urgent_repeat_v1",
        data={"source": "alert_reminder", "reminderPhaseId": phase_id, "reminderStage": "1"},
        event_key=f"reminder:{phase_id}:1",
        now=notified_at + timedelta(seconds=62),
    )
    assert result["status"] == "skipped_stopped"
    with repository._connect() as connection:
        dispatch = connection.execute(
            "SELECT status FROM mobile_alert_reminder_dispatches WHERE phase_id=? AND stage=1",
            (phase_id,),
        ).fetchone()
        count = connection.execute(
            "SELECT COUNT(*) FROM mobile_notification_outbox"
        ).fetchone()[0]
    assert dispatch["status"] == "skipped_stopped"
    assert count == 0


def test_confirm_broadcast_uses_selected_roles_for_initial_and_first_repeat_then_all_users(
    tmp_path: Path,
) -> None:
    repository, alert, admin, teacher, admin_token, teacher_token, notified_at = (
        make_unanswered_alert(tmp_path)
    )
    sender = RecordingSender()
    worker = AlertNotificationEscalationWorker(repository, sender)

    result = repository.record_mobile_workflow_action(
        alert.id,
        admin["id"],
        "confirm_only",
        comment=None,
        recipient_role_ids=["role_teacher"],
        allow_empty_audience=False,
        idempotency_key="confirm-broadcast-001",
        first_after_seconds=60,
        all_after_seconds=120,
    )
    assert result is not None
    assert result["broadcastEligibleUsers"] == 1
    assert len(result["broadcastOutboxIds"]) == 1
    assert result["decisionUpdateOutboxIds"] == []

    # The initial confirmed-incident broadcast is directed to the selected role.
    assert worker._outbox.process_pending() == 1
    assert [call["token"] for call in sender.calls] == [teacher_token]
    assert sender.calls[0]["data"]["notificationStage"] == "initial_broadcast"

    phase = result["workflow"]["active_phase_id"]
    with repository._connect() as connection:
        phase_started_at = datetime.fromisoformat(
            connection.execute(
                "SELECT started_at FROM mobile_alert_reminder_phases WHERE id=?",
                (phase,),
            ).fetchone()[0]
        )

    assert worker.process_due(phase_started_at + timedelta(seconds=61)) == 1
    assert sender.calls[-1]["token"] == teacher_token
    assert sender.calls[-1]["data"]["notificationStage"] == "first_repeat"

    assert worker.process_due(phase_started_at + timedelta(seconds=121)) == 2
    assert {call["token"] for call in sender.calls[-2:]} == {
        admin_token,
        teacher_token,
    }
    assert all(
        call["data"]["notificationStage"] == "all_users_escalation"
        for call in sender.calls[-2:]
    )
    assert all("No handler has taken ownership" in call["body"] for call in sender.calls[-2:])


def test_confirm_only_api_separates_broadcast_from_routine_update_queue(
    tmp_path: Path,
) -> None:
    repository, alert, admin, teacher, _, _, _ = make_unanswered_alert(tmp_path)
    security = repository.upsert_firebase_user(
        "firebase-escalation-security", "security@example.test", "Security"
    )
    assert repository.assign_mobile_user(
        security["id"], "role_security_personnel", "active", admin["id"]
    )
    with repository._connect() as connection:
        connection.execute("UPDATE devices SET enabled=0 WHERE user_id=?", (teacher["id"],))
        initial_notification_id = connection.execute(
            "SELECT id FROM notifications WHERE alert_id=? ORDER BY created_at LIMIT 1",
            (alert.id,),
        ).fetchone()["id"]
        connection.commit()
    repository.set_notification_user_recipients(
        initial_notification_id, [admin["id"], security["id"]]
    )

    config = runtime(tmp_path)
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=EscalationTokenVerifier(),
        )
    )
    response = client.post(
        f"/api/mobile/alerts/{alert.id}/actions",
        headers={"Authorization": "Bearer admin-token"},
        json={
            "action": "confirm_only",
            "recipient_role_ids": ["role_teacher"],
            "idempotency_key": "confirm-queue-separation",
            "expected_workflow_revision": repository.get_mobile_alert_workflow(
                alert.id
            )["revision"],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["broadcastEligibleUsers"] == 0
    assert payload["notificationsQueued"] == 1
    assert payload["broadcastDispatchesQueued"] == 0
    assert payload["decisionUpdateDispatchesQueued"] == 1


def test_confirm_only_requires_broadcast_role_and_is_idempotent(tmp_path: Path) -> None:
    repository, alert, admin, teacher, _, _, _ = make_unanswered_alert(tmp_path)
    with repository._connect() as connection:
        connection.execute("UPDATE devices SET enabled=0 WHERE user_id=?", (teacher["id"],))
        connection.commit()
    with pytest.raises(ValueError, match="at least one Broadcast Role"):
        repository.record_mobile_workflow_action(
            alert.id,
            admin["id"],
            "confirm_only",
            comment="",
            recipient_role_ids=[],
            allow_empty_audience=False,
            idempotency_key="confirm-no-role",
            first_after_seconds=60,
            all_after_seconds=120,
        )
    first = repository.record_mobile_workflow_action(
        alert.id,
        admin["id"],
        "confirm_only",
        comment="",
        recipient_role_ids=["role_teacher"],
        allow_empty_audience=False,
        idempotency_key="confirm-idempotent",
        first_after_seconds=60,
        all_after_seconds=120,
    )
    second = repository.record_mobile_workflow_action(
        alert.id,
        admin["id"],
        "confirm_only",
        comment="different retry payload",
        recipient_role_ids=["role_teacher"],
        allow_empty_audience=False,
        idempotency_key="confirm-idempotent",
        first_after_seconds=60,
        all_after_seconds=120,
    )
    assert first["idempotent"] is False
    assert first["broadcastEligibleUsers"] == 0
    assert first["queuedOutboxIds"] == []
    with repository._connect() as connection:
        phase = connection.execute(
            """
            SELECT phase_status FROM mobile_alert_reminder_phases
            WHERE alert_id=? AND phase_kind='awaiting_handler'
            """,
            (alert.id,),
        ).fetchone()
    assert phase["phase_status"] == "active"
    assert second["idempotent"] is True


def test_mobile_api_rejects_stale_workflow_revision_and_keeps_retries_idempotent(
    tmp_path: Path,
) -> None:
    repository, alert, admin, _, _, _, _ = make_unanswered_alert(tmp_path)
    config = runtime(tmp_path)
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=EscalationTokenVerifier(),
        )
    )
    headers = {"Authorization": "Bearer admin-token"}
    revision = repository.get_mobile_alert_workflow(alert.id)["revision"]
    confirm_payload = {
        "action": "confirm_and_handle",
        "reason": "",
        "recipientRoleIds": [],
        "idempotencyKey": "workflow-revision-confirm-001",
        "expectedWorkflowRevision": revision,
    }

    confirmed = client.post(
        f"/api/mobile/alerts/{alert.id}/actions",
        headers=headers,
        json=confirm_payload,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["workflow"]["workflow_status"] == "in_progress"

    # A network retry with the original idempotency key succeeds as a no-op,
    # even though the expected revision is now stale.
    retried = client.post(
        f"/api/mobile/alerts/{alert.id}/actions",
        headers=headers,
        json=confirm_payload,
    )
    assert retried.status_code == 200
    assert retried.json()["idempotent"] is True

    stale = client.post(
        f"/api/mobile/alerts/{alert.id}/actions",
        headers=headers,
        json={
            "action": "take_ownership",
            "idempotencyKey": "workflow-revision-stale-002",
            "expectedWorkflowRevision": revision,
        },
    )
    assert stale.status_code == 409
    assert "changed while you were viewing it" in stale.json()["detail"]
    workflow = repository.get_mobile_alert_workflow(alert.id)
    assert workflow["workflow_status"] == "in_progress"
    assert workflow["handler_user_id"] == admin["id"]
    assert [event["action"] for event in workflow["events"]].count(
        "confirm_and_handle"
    ) == 1


def test_concurrent_handler_acquisition_allows_exactly_one_owner(
    tmp_path: Path,
) -> None:
    repository, alert, admin, teacher, _, _, _ = make_unanswered_alert(tmp_path)
    config = runtime(tmp_path)
    client = TestClient(
        create_app(
            config,
            repository,
            LocalMediaStore(config),
            token_verifier_override=EscalationTokenVerifier(),
        )
    )
    confirmed = repository.record_mobile_workflow_action(
        alert.id,
        admin["id"],
        "confirm_only",
        comment=None,
        recipient_role_ids=["role_teacher"],
        allow_empty_audience=False,
        idempotency_key="concurrent-owner-confirm-001",
        first_after_seconds=60,
        all_after_seconds=120,
    )
    assert confirmed is not None
    assert confirmed["workflow"]["workflow_status"] == "confirmed_awaiting_handler"
    observed_revision = confirmed["workflow"]["revision"]

    start_together = Barrier(2)

    def try_take_ownership(token: str, idempotency_key: str):
        start_together.wait(timeout=5)
        return client.post(
            f"/api/mobile/alerts/{alert.id}/actions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "action": "take_ownership",
                "idempotencyKey": idempotency_key,
                "expectedWorkflowRevision": observed_revision,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda attempt: try_take_ownership(*attempt),
                [
                    ("admin-token", "concurrent-owner-admin-002"),
                    ("teacher-token", "concurrent-owner-teacher-003"),
                ],
            )
        )

    assert sorted(response.status_code for response in outcomes) == [200, 409]
    conflict = next(response for response in outcomes if response.status_code == 409)
    assert "changed while you were viewing it" in conflict.json()["detail"]

    final_workflow = repository.get_mobile_alert_workflow(alert.id)
    assert final_workflow["workflow_status"] == "in_progress"
    assert final_workflow["handler_user_id"] in {admin["id"], teacher["id"]}
    assert [event["action"] for event in final_workflow["events"]].count(
        "take_ownership"
    ) == 1


def test_formal_response_stops_both_unanswered_alert_reminders(tmp_path: Path) -> None:
    repository, alert, admin, _, _, _, notified_at = make_unanswered_alert(tmp_path)
    assert repository.record_mobile_alert_action(alert.id, admin["id"], "resolve", "Handled")
    sender = RecordingSender()
    worker = AlertNotificationEscalationWorker(repository, sender)

    assert worker.process_due(notified_at + timedelta(minutes=10)) == 0
    assert sender.calls == []


def test_read_and_simulated_call_do_not_stop_phase_but_handling_does(
    tmp_path: Path,
) -> None:
    repository, alert, admin, _, admin_token, _, notified_at = make_unanswered_alert(tmp_path)
    repository.mark_mobile_alert_read(admin["id"], alert.id)
    simulated = repository.record_mobile_workflow_action(
        alert.id,
        admin["id"],
        "simulate_emergency_call",
        comment=None,
        recipient_role_ids=[],
        allow_empty_audience=True,
        idempotency_key="simulated-call-does-not-stop",
        first_after_seconds=60,
        all_after_seconds=120,
    )
    assert simulated is not None
    sender = RecordingSender()
    worker = AlertNotificationEscalationWorker(repository, sender)

    assert worker.process_due(notified_at + timedelta(seconds=61)) == 1
    assert sender.calls[0]["token"] == admin_token

    handled = repository.record_mobile_workflow_action(
        alert.id,
        admin["id"],
        "confirm_and_handle",
        comment=None,
        recipient_role_ids=[],
        allow_empty_audience=False,
        idempotency_key="confirm-and-handle-stops-phase",
        first_after_seconds=60,
        all_after_seconds=120,
    )
    assert handled is not None
    assert handled["workflow"]["workflow_status"] == "in_progress"
    assert worker.process_due(notified_at + timedelta(minutes=10)) == 0


def test_simulated_call_after_confirmed_resolved_is_a_single_history_event(
    tmp_path: Path,
) -> None:
    repository, alert, admin, _, _, _, _ = make_unanswered_alert(tmp_path)
    resolved = repository.record_mobile_workflow_action(
        alert.id,
        admin["id"],
        "confirm_resolved",
        comment=None,
        recipient_role_ids=[],
        allow_empty_audience=False,
        idempotency_key="confirm-already-resolved",
        first_after_seconds=60,
        all_after_seconds=120,
    )
    assert resolved is not None
    simulation = repository.record_mobile_workflow_action(
        alert.id,
        admin["id"],
        "simulate_emergency_call",
        comment=None,
        recipient_role_ids=[],
        allow_empty_audience=True,
        idempotency_key="simulate-after-resolved",
        first_after_seconds=60,
        all_after_seconds=120,
    )
    assert simulation is not None
    assert simulation["workflow"]["workflow_status"] == "resolved"
    actions = repository.list_alert_actions(alert.id)
    assert [item["action"] for item in actions].count("simulated_emergency_call") == 1
    assert repository.get_mobile_alert_workflow(alert.id)["active_phase_id"] is None


def test_only_assigned_handler_can_resolve_an_in_progress_incident(
    tmp_path: Path,
) -> None:
    repository, alert, admin, teacher, _, _, _ = make_unanswered_alert(tmp_path)
    confirmed = repository.record_mobile_workflow_action(
        alert.id,
        admin["id"],
        "confirm_and_handle",
        comment=None,
        recipient_role_ids=[],
        allow_empty_audience=False,
        idempotency_key="single-handler-confirm",
        first_after_seconds=60,
        all_after_seconds=120,
    )
    assert confirmed is not None
    with pytest.raises(PermissionError, match="assigned handler"):
        repository.record_mobile_workflow_action(
            alert.id,
            teacher["id"],
            "resolve",
            comment=None,
            recipient_role_ids=[],
            allow_empty_audience=False,
            idempotency_key="wrong-handler-resolve",
            first_after_seconds=60,
            all_after_seconds=120,
        )
    resolved = repository.record_mobile_workflow_action(
        alert.id,
        admin["id"],
        "resolve",
        comment=None,
        recipient_role_ids=[],
        allow_empty_audience=False,
        idempotency_key="right-handler-resolve",
        first_after_seconds=60,
        all_after_seconds=120,
    )
    assert resolved is not None
    assert resolved["workflow"]["workflow_status"] == "resolved"


def test_notification_outbox_retries_a_failed_device_delivery(tmp_path: Path) -> None:
    repository, alert, _, _, _, _, _ = make_unanswered_alert(tmp_path)

    class OneFailureSender:
        def __init__(self) -> None:
            self.calls = 0

        def send(self, token, title, body, data, channel_id="guardaid_updates_v1"):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("Temporary provider timeout")
            return "provider-retry-success"

    sender = OneFailureSender()
    service = NotificationService(repository, sender)
    assert service.dispatch(alert) == 0
    with repository._connect() as connection:
        outbox = connection.execute(
            "SELECT id,status,attempt_count,next_attempt_at FROM mobile_notification_outbox WHERE event_key LIKE ? ORDER BY created_at DESC LIMIT 1",
            (f"alert-initial:{alert.id}:%",),
        ).fetchone()
    assert outbox["status"] == "pending"
    assert outbox["attempt_count"] == 1

    retry_at = datetime.fromisoformat(outbox["next_attempt_at"]) + timedelta(seconds=1)
    from app.notification_outbox import MobileNotificationOutboxWorker

    worker = MobileNotificationOutboxWorker(repository, sender)
    assert worker.process_pending(retry_at) == 1
    with repository._connect() as connection:
        retried = connection.execute(
            "SELECT status,attempt_count FROM mobile_notification_outbox WHERE id=?",
            (outbox["id"],),
        ).fetchone()
    assert retried["status"] == "sent"
    assert retried["attempt_count"] == 2
    assert sender.calls == 2


def test_escalation_configuration_requires_increasing_times() -> None:
    assert AlertNotificationEscalationSettings().same_audience_repeat_after_seconds == 60
    assert AlertNotificationEscalationSettings().all_users_escalation_after_seconds == 120
    try:
        AlertNotificationEscalationSettings(
            same_audience_repeat_after_seconds=180,
            all_users_escalation_after_seconds=180,
        )
    except ValueError:
        return
    raise AssertionError("The all-user stage must be later than the first repeat.")
