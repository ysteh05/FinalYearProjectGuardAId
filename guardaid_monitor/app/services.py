from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
import json
from typing import Any

from .domain import (
    Alert,
    AlertStatus,
    AlertType,
    Camera,
    DetectionEventSnapshot,
    DescriptionStatus,
    SystemSettings,
    new_id,
    utc_now,
)
from .model_metadata import VIOLENCE_MODEL_VERSION
from .notification_outbox import MobileNotificationOutboxWorker
from .policy import (
    cooldown_allows,
    violence_borderline_range,
    violence_qualified_modes,
    violence_recording_threshold,
)
from .repository import Repository


class AlertService:
    """Creates alerts and groups detections by their source-video timeline."""

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def record_violence_candidate(
        self,
        camera: Camera,
        confidence: float,
        *,
        vlm_promoted: bool = False,
        detected_at: datetime | None = None,
        source_start_seconds: float | None = None,
        source_end_seconds: float | None = None,
        incident_not_before: datetime | None = None,
        settings: SystemSettings | None = None,
    ) -> Alert | None:
        settings = settings or self._repository.get_settings()
        recording_floor = violence_recording_threshold(settings.violence)
        if confidence < recording_floor:
            return None
        qualified = violence_qualified_modes(confidence, settings.violence)
        floor, ceiling = violence_borderline_range(settings.violence)
        review_status = (
            "queued"
            if settings.vlm.borderline_review_enabled and floor <= confidence < ceiling
            else "disabled"
            if floor <= confidence < ceiling
            else "not_applicable"
        )
        return self._record_confirmed(
            camera,
            AlertType.VIOLENCE,
            confidence,
            settings,
            detected_at=detected_at,
            source_start_seconds=source_start_seconds,
            source_end_seconds=source_end_seconds,
            is_borderline_promoted=vlm_promoted,
            is_provisional=False,
            borderline_review_status=review_status,
            detection_qualifications={"violence": [str(mode) for mode in qualified]},
            weapon_class_scores={},
            model_version=VIOLENCE_MODEL_VERSION,
            prompt_version=(
                settings.vlm.binary_prompt_version if vlm_promoted else None
            ),
            incident_not_before=incident_not_before,
        )

    def record_weapon_confirmation(
        self,
        camera: Camera,
        confidence: float,
        *,
        detected_at: datetime | None = None,
        source_seconds: float | None = None,
        source_start_seconds: float | None = None,
        model_version: str = "weapon-checkpoint-pending",
        incident_not_before: datetime | None = None,
        settings: SystemSettings | None = None,
        detection_qualifications: dict[str, Any] | None = None,
        weapon_class_scores: dict[str, float] | None = None,
    ) -> Alert:
        settings = settings or self._repository.get_settings()
        return self._record_confirmed(
            camera,
            AlertType.WEAPON,
            confidence,
            settings,
            detected_at=detected_at,
            source_start_seconds=(
                source_start_seconds
                if source_start_seconds is not None
                else source_seconds
            ),
            source_end_seconds=source_seconds,
            is_borderline_promoted=False,
            is_provisional=False,
            borderline_review_status="not_applicable",
            detection_qualifications=detection_qualifications or {},
            weapon_class_scores=weapon_class_scores or {},
            model_version=model_version,
            prompt_version=None,
            incident_not_before=incident_not_before,
        )

    def _record_confirmed(
        self,
        camera: Camera,
        alert_type: AlertType,
        confidence: float,
        settings: SystemSettings,
        *,
        detected_at: datetime | None,
        source_start_seconds: float | None,
        source_end_seconds: float | None,
        is_borderline_promoted: bool,
        is_provisional: bool,
        borderline_review_status: str,
        detection_qualifications: dict[str, Any],
        weapon_class_scores: dict[str, float],
        model_version: str,
        prompt_version: str | None,
        incident_not_before: datetime | None,
    ) -> Alert:
        event_time = detected_at or utc_now()
        event = DetectionEventSnapshot(
            confidence=confidence,
            detected_at=event_time,
            source_start_seconds=source_start_seconds,
            source_end_seconds=source_end_seconds,
            model_version=model_version,
            config_version=settings.config_version,
            qualifications=detection_qualifications,
            weapon_class_scores=weapon_class_scores,
        )
        merge_gap = (
            settings.weapon.incident_merge_gap_seconds
            if alert_type == AlertType.WEAPON
            else settings.violence.incident_merge_gap_seconds
        )
        candidates: list[tuple[float, Alert]] = []
        if source_start_seconds is not None and source_end_seconds is not None:
            for existing in self._repository.list_alerts():
                if (
                    existing.camera_id != camera.id
                    or existing.type != alert_type
                    # A later detection must not be absorbed into a closed
                    # record. Legacy escalated records are also shown in the
                    # historical Logs view, so only operationally active
                    # alerts may be candidates for source-time merging.
                    or existing.status not in {AlertStatus.OPEN, AlertStatus.IN_REVIEW}
                    or (
                        incident_not_before is not None
                        and existing.first_detected_at < incident_not_before
                    )
                    or existing.source_start_seconds is None
                    or existing.source_end_seconds is None
                ):
                    continue
                if source_start_seconds > existing.source_end_seconds:
                    gap = source_start_seconds - existing.source_end_seconds
                elif existing.source_start_seconds > source_end_seconds:
                    gap = existing.source_start_seconds - source_end_seconds
                else:
                    gap = 0.0
                if gap <= merge_gap:
                    candidates.append((gap, existing))
        recent = min(candidates, key=lambda item: item[0])[1] if candidates else None
        if recent:
            consolidated = recent.model_copy(
                update={
                    "last_detected_at": event_time,
                    "confidence": confidence,
                    "max_confidence": max(recent.max_confidence, confidence),
                    "detection_count": recent.detection_count + 1,
                    "source_start_seconds": min(
                        recent.source_start_seconds, source_start_seconds
                    ),
                    "source_end_seconds": max(recent.source_end_seconds, source_end_seconds),
                    "is_borderline_promoted": (
                        recent.is_borderline_promoted or is_borderline_promoted
                    ),
                    "is_provisional": recent.is_provisional and is_provisional,
                    "detection_event_key": new_id("detect"),
                    "detection_qualifications": detection_qualifications,
                    "weapon_class_scores": weapon_class_scores,
                    "model_version": model_version,
                    "config_version": settings.config_version,
                    "prompt_version": prompt_version,
                    "borderline_review_status": (
                        borderline_review_status
                        if borderline_review_status != "not_applicable"
                        else recent.borderline_review_status
                    ),
                }
            )
            return self._repository.save_alert(consolidated, detection_event=event)

        description_status = (
            DescriptionStatus.PENDING
            if settings.vlm.scene_description_enabled
            else DescriptionStatus.DISABLED
        )
        alert = Alert(
            camera_id=camera.id,
            camera_name=camera.name,
            type=alert_type,
            confidence=confidence,
            max_confidence=confidence,
            # The database column remains for older clients. It is not used by
            # detection or notification policy.
            sensitivity_mode="sensitive",
            is_borderline_promoted=is_borderline_promoted,
            is_provisional=is_provisional,
            borderline_review_status=borderline_review_status,
            detection_qualifications=detection_qualifications,
            weapon_class_scores=weapon_class_scores,
            source_start_seconds=source_start_seconds,
            source_end_seconds=source_end_seconds,
            first_detected_at=event_time,
            last_detected_at=event_time,
            description_status=description_status,
            model_version=model_version,
            prompt_version=prompt_version,
            config_version=settings.config_version,
        )
        return self._repository.save_alert(alert, detection_event=event)


class WeaponFrameGate:
    """Maintains an independent N-of-M confidence window per camera."""

    def __init__(self, alert_service: AlertService, repository: Repository) -> None:
        self._alert_service = alert_service
        self._repository = repository
        self._buffers: dict[str, deque] = defaultdict(deque)

    def reset(self, camera_id: str) -> None:
        self._buffers.pop(camera_id, None)

    def observe(
        self,
        camera: Camera,
        confidence: float,
        hit: bool | None = None,
        *,
        detected_at: datetime | None = None,
        source_seconds: float | None = None,
        source_start_seconds: float | None = None,
        model_version: str,
        incident_not_before: datetime | None = None,
        class_scores: dict[str, float] | None = None,
        settings: SystemSettings | None = None,
    ) -> Alert | None:
        settings = settings or self._repository.get_settings()
        weapon = settings.weapon
        buffer = self._buffers[camera.id]
        if buffer.maxlen != weapon.m_window:
            buffer = deque(buffer, maxlen=weapon.m_window)
            self._buffers[camera.id] = buffer
        if class_scores is not None:
            buffer.append({
                name: float(class_scores[name]) if name in class_scores else None
                for name in ("knife", "gun")
            })
            values = list(buffer)
            if len(values) < weapon.m_window:
                return None
            qualification: dict[str, list[str]] = {}
            for mode in ("sensitive", "moderate", "certain"):
                profile = getattr(weapon.class_thresholds, mode)
                classes = [
                    name for name in ("knife", "gun")
                    if sum(
                        frame[name] is not None and frame[name] >= getattr(profile, name)
                        for frame in values
                    )
                    >= weapon.n_required
                ]
                if classes:
                    qualification[mode] = classes
            floor_profile = {
                name: min(
                    getattr(getattr(weapon.class_thresholds, mode), name)
                    for mode in ("sensitive", "moderate", "certain")
                )
                for name in ("knife", "gun")
            }
            floor_classes = [
                name for name in ("knife", "gun")
                if sum(
                    frame[name] is not None and frame[name] >= floor_profile[name]
                    for frame in values
                )
                >= weapon.n_required
            ]
            if not floor_classes:
                return None
            confidence = max(
                (
                    frame[name]
                    for frame in values
                    for name in floor_classes
                    if frame[name] is not None
                ),
                default=0.0,
            )
            return self._alert_service.record_weapon_confirmation(
                camera,
                confidence,
                detected_at=detected_at,
                source_seconds=source_seconds,
                source_start_seconds=source_start_seconds,
                model_version=model_version,
                incident_not_before=incident_not_before,
                settings=settings,
                detection_qualifications={"weapon": qualification},
                weapon_class_scores={
                    name: max(
                        frame[name] for frame in values if frame[name] is not None
                    )
                    for name in floor_classes
                },
            )
        # Compatibility for isolated legacy callers that provide a single hit.
        effective_hit = confidence >= weapon.confidence_threshold if hit is None else hit
        buffer.append((effective_hit, confidence))
        values = list(buffer)
        if len(values) < weapon.m_window or sum(value[0] for value in values) < weapon.n_required:
            return None
        return self._alert_service.record_weapon_confirmation(
            camera,
            max((value[1] for value in values if value[0]), default=0.0),
            detected_at=detected_at,
            source_seconds=source_seconds,
            source_start_seconds=source_start_seconds,
            model_version=model_version,
            incident_not_before=incident_not_before,
        )


class NotificationService:
    """Persists a minimal alert notification and dispatches it to enabled devices."""

    def __init__(self, repository: Repository, sender) -> None:
        self._repository = repository
        self._sender = sender
        self._last_dispatch: dict[tuple[str, str], datetime] = {}
        self._outbox = MobileNotificationOutboxWorker(repository, sender)

    def dispatch(
        self,
        alert: Alert,
        cooldown_seconds: int = 0,
        escalation_settings=None,
        *,
        borderline_review_task_id: str | None = None,
        process_pending: bool = True,
    ) -> int:
        key = (alert.camera_id, str(alert.type))
        now = utc_now()
        if not all(
            hasattr(self._repository, name)
            for name in ("list_enabled_devices", "create_notification", "record_notification_delivery")
        ):
            return 0
        data = {
            "alertId": alert.id,
            "type": str(alert.type),
            "source": "alert_created",
            "alertDetectedAt": alert.first_detected_at.isoformat(),
        }
        title = "GuardAId safety alert"
        body = f"{str(alert.type).replace('_', ' ').title()} detected at {alert.camera_name}"
        channel_id = (
            "guardaid_urgent_alerts_v7"
            if str(alert.type) in {"weapon", "violence", "physical_contact_bullying"}
            else "guardaid_updates_v1"
        )
        devices = (
            self._repository.list_enabled_devices_for_alert(alert)
            if hasattr(self._repository, "list_enabled_devices_for_alert")
            else self._repository.list_enabled_devices()
        )
        if hasattr(self._repository, "queue_initial_mobile_alert_notification"):
            if escalation_settings is None and hasattr(self._repository, "get_settings"):
                escalation_settings = self._repository.get_settings().alert_notification_escalation
            if escalation_settings is None:
                return 0
            queued = self._repository.queue_initial_mobile_alert_notification(
                alert,
                recipients=devices,
                title=title,
                body=body,
                channel_id=channel_id,
                first_after_seconds=escalation_settings.same_audience_repeat_after_seconds,
                all_after_seconds=escalation_settings.all_users_escalation_after_seconds,
                started_at=now,
                borderline_review_task_id=borderline_review_task_id,
            )
            self._last_dispatch[key] = now
            return (
                self._outbox.process_pending()
                if process_pending
                else int(queued.get("recipientCount", 0))
            )
        if not cooldown_allows(self._last_dispatch.get(key), now, cooldown_seconds):
            return 0
        # Do not create an untargeted notification record when no active,
        # enabled device was eligible for the original alert. This keeps the
        # recipient snapshot meaningful and avoids a misleading global row.
        if not devices:
            return 0
        self._last_dispatch[key] = now
        notification_id = self._repository.create_notification(
            alert.id, title, body, json.dumps(data, separators=(",", ":"))
        )
        sent = 0
        if hasattr(self._repository, "set_notification_user_recipients"):
            self._repository.set_notification_user_recipients(
                notification_id,
                [device.get("user_id") for device in devices if device.get("user_id")],
            )
        if escalation_settings is not None and hasattr(
            self._repository, "schedule_alert_notification_escalation"
        ):
            self._repository.schedule_alert_notification_escalation(
                alert.id,
                notification_id,
                escalation_settings.same_audience_repeat_after_seconds,
                escalation_settings.all_users_escalation_after_seconds,
            )
        for device in devices:
            try:
                provider_id = self._sender.send(
                    device["fcm_registration_value"], title, body, data, channel_id
                )
                self._repository.record_notification_delivery(
                    notification_id, device["id"], "sent", provider_id, None
                )
                sent += 1
            except Exception as exc:
                self._repository.record_notification_delivery(
                    notification_id, device["id"], "failed", None, type(exc).__name__
                )
        return sent
