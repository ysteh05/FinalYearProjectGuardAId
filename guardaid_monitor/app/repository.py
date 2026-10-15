from __future__ import annotations

from threading import RLock
from typing import Protocol

from .domain import (
    Alert,
    Camera,
    DetectionEventSnapshot,
    EvidenceRecord,
    SceneSummary,
    SystemSettings,
)


class Repository(Protocol):
    def list_cameras(self) -> list[Camera]: ...
    def get_camera(self, camera_id: str) -> Camera | None: ...
    def save_camera(self, camera: Camera) -> Camera: ...
    def delete_camera(self, camera_id: str) -> Camera | None: ...
    def list_alerts(self) -> list[Alert]: ...
    def save_alert(
        self, alert: Alert, detection_event: DetectionEventSnapshot | None = None
    ) -> Alert: ...
    def save_evidence(self, evidence: EvidenceRecord, size_bytes: int) -> EvidenceRecord: ...
    def delete_evidence(self, alert_id: str, media_type: str) -> list[str]: ...
    def set_alert_evidence_status(self, alert_id: str, status: str) -> None: ...
    def update_description(
        self, alert_id: str, status: str, text: str | dict | SceneSummary | None,
        error_code: str | None = None
    ) -> None: ...
    def get_settings(self) -> SystemSettings: ...
    def save_settings(self, settings: SystemSettings) -> SystemSettings: ...


class DemoRepository:
    """Non-persistent repository retained for isolated unit/API tests."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._cameras: dict[str, Camera] = {}
        self._alerts: dict[str, Alert] = {}
        self._settings = SystemSettings()

    def list_cameras(self) -> list[Camera]:
        with self._lock:
            return sorted(self._cameras.values(), key=lambda item: item.created_at)

    def get_camera(self, camera_id: str) -> Camera | None:
        with self._lock:
            return self._cameras.get(camera_id)

    def save_camera(self, camera: Camera) -> Camera:
        with self._lock:
            self._cameras[camera.id] = camera
            return camera

    def delete_camera(self, camera_id: str) -> Camera | None:
        with self._lock:
            return self._cameras.pop(camera_id, None)

    def list_alerts(self) -> list[Alert]:
        with self._lock:
            return sorted(
                self._alerts.values(), key=lambda item: item.last_detected_at, reverse=True
            )

    def save_alert(
        self, alert: Alert, detection_event: DetectionEventSnapshot | None = None
    ) -> Alert:
        del detection_event
        with self._lock:
            self._alerts[alert.id] = alert
            return alert

    def save_evidence(self, evidence: EvidenceRecord, size_bytes: int) -> EvidenceRecord:
        del size_bytes
        with self._lock:
            alert = self._alerts[evidence.alert_id]
            self._alerts[alert.id] = alert.model_copy(
                update={"evidence": [*alert.evidence, evidence], "evidence_status": "available"}
            )
        return evidence

    def set_alert_evidence_status(self, alert_id: str, status: str) -> None:
        with self._lock:
            alert = self._alerts.get(alert_id)
            if alert:
                self._alerts[alert_id] = alert.model_copy(update={"evidence_status": status})

    def delete_evidence(self, alert_id: str, media_type: str) -> list[str]:
        with self._lock:
            alert = self._alerts.get(alert_id)
            if not alert:
                return []
            removed = [item.storage_path for item in alert.evidence if item.media_type == media_type]
            self._alerts[alert_id] = alert.model_copy(
                update={"evidence": [item for item in alert.evidence if item.media_type != media_type]}
            )
            return removed

    def update_description(
        self, alert_id: str, status: str, text: str | dict | SceneSummary | None,
        error_code: str | None = None
    ) -> None:
        del error_code
        with self._lock:
            alert = self._alerts.get(alert_id)
            if alert:
                summary = None
                if isinstance(text, SceneSummary):
                    summary = text
                    display = text.description
                elif isinstance(text, dict):
                    summary = SceneSummary.model_validate(text)
                    display = summary.description
                else:
                    display = text
                self._alerts[alert_id] = alert.model_copy(
                    update={
                        "description_status": status,
                        "scene_description": display,
                        "scene_summary": summary,
                    }
                )

    def get_settings(self) -> SystemSettings:
        with self._lock:
            return self._settings.model_copy(deep=True)

    def save_settings(self, settings: SystemSettings) -> SystemSettings:
        with self._lock:
            self._settings = settings
            return settings
