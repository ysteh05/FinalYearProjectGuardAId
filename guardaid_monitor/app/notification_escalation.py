from __future__ import annotations

import json
import logging
from datetime import datetime
from threading import Event, Thread

from .notification_outbox import MobileNotificationOutboxWorker


LOGGER = logging.getLogger(__name__)

_STAGE_DETAILS = {
    1: {
        "name": "first_repeat",
        "channel": "guardaid_urgent_repeat_v1",
        "title": "GuardAId alert reminder",
    },
    2: {
        "name": "all_users_escalation",
        "channel": "guardaid_urgent_escalated_v1",
        "title": "GuardAId alert requires attention",
    },
}


class AlertNotificationEscalationWorker:
    """Dispatch one first-repeat and one all-user reminder per active phase."""

    def __init__(self, repository, sender, *, poll_seconds: float = 1.0) -> None:
        self._repository = repository
        self._sender = sender
        self._poll_seconds = poll_seconds
        self._outbox = MobileNotificationOutboxWorker(repository, sender)
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if not hasattr(self._repository, "list_due_mobile_alert_reminder_phases"):
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(
            target=self._run,
            name="guardaid-alert-notification-escalation",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_seconds):
            try:
                self.process_due()
                self._outbox.process_pending()
            except Exception as exc:
                LOGGER.error(
                    "Alert reminder scan failed (%s).", type(exc).__name__
                )

    def process_due(self, now: datetime | None = None) -> int:
        repository = self._repository
        if not hasattr(repository, "list_due_mobile_alert_reminder_phases"):
            return 0
        sent = 0
        for candidate in repository.list_due_mobile_alert_reminder_phases(now):
            phase = repository.claim_mobile_alert_reminder_dispatch(
                candidate["phase_id"], int(candidate["stage"]), now
            )
            if phase is None:
                continue
            sent += self._dispatch_phase_stage(
                phase, int(candidate["stage"]), now
            )
        if hasattr(repository, "list_late_mobile_alert_repeat_candidates"):
            for candidate in repository.list_late_mobile_alert_repeat_candidates(now):
                alert = next(
                    (item for item in repository.list_alerts() if item.id == candidate["alert_id"]),
                    None,
                )
                if alert is None:
                    continue
                user_id = candidate["user_id"]
                devices = repository.list_enabled_devices_for_users([user_id])
                detail = _STAGE_DETAILS[1]
                label = str(alert.type).replace("_", " ").title()
                queued = repository.queue_late_mobile_alert_repeat_notification(
                    candidate["phase_id"],
                    user_id,
                    recipients=devices,
                    title=detail["title"],
                    body=(
                        f"The {label} alert at {alert.camera_name} is still awaiting a response. "
                        "Please review it."
                    ),
                    channel_id=detail["channel"],
                    data={
                        "type": str(alert.type),
                        "notificationStage": detail["name"],
                        "phaseKind": candidate["phase_kind"],
                    },
                    event_key=f"reminder:{candidate['phase_id']}:1:late:{user_id}",
                    now=now,
                )
                if queued.get("status") == "queued":
                    sent += self._outbox.process_pending(now)
        return sent

    def _dispatch_phase_stage(
        self, phase: dict, stage: int, now: datetime | None
    ) -> int:
        repository = self._repository
        alert = next(
            (item for item in repository.list_alerts() if item.id == phase["alert_id"]),
            None,
        )
        if alert is None:
            repository.queue_mobile_alert_reminder_notification(
                phase["id"], stage, recipients=[], title="", body="",
                channel_id=_STAGE_DETAILS[stage]["channel"], data={},
                event_key=f"reminder:{phase['id']}:{stage}", now=now,
            )
            return 0

        if stage == 2:
            devices = repository.list_enabled_devices_for_all_active_users()
        elif phase["phase_kind"] == "awaiting_decision":
            devices = (
                repository.list_alert_notification_audience_devices(alert.id)
                if hasattr(repository, "list_alert_notification_audience_devices")
                else repository.list_enabled_devices_for_alert(alert)
            )
        else:
            role_ids = json.loads(phase["broadcast_role_ids_json"] or "[]")
            devices = repository.list_enabled_devices_for_roles(role_ids)
            if hasattr(repository, "list_alert_notification_audience_devices"):
                devices.extend(repository.list_alert_notification_audience_devices(alert.id))
            devices = list({item["id"]: item for item in devices}.values())
            initiator = phase.get("initiated_by_user_id")
            if initiator:
                devices = [item for item in devices if item.get("user_id") != initiator]

        detail = _STAGE_DETAILS[stage]
        alert_label = str(alert.type).replace("_", " ").title()
        if phase["phase_kind"] == "awaiting_decision":
            body = (
                f"No response was recorded for the {alert_label} alert at "
                f"{alert.camera_name}. Please review it."
            )
        else:
            body = (
                f"No handler has taken ownership of the {alert_label} incident at "
                f"{alert.camera_name}. Please review it."
            )
        if stage == 2:
            if phase["phase_kind"] == "awaiting_handler":
                body = (
                    f"No handler has taken ownership of the {alert_label} incident at "
                    f"{alert.camera_name}. All active users have been notified."
                )
            else:
                body = (
                    f"No response was recorded for the {alert_label} alert at "
                    f"{alert.camera_name}. All active users have been notified."
                )
        data = {
            "alertId": alert.id,
            "type": str(alert.type),
            "source": "alert_reminder",
            "notificationStage": detail["name"],
            "phaseKind": phase["phase_kind"],
            "reminderPhaseId": phase["id"],
            "reminderStage": str(stage),
        }
        queued = repository.queue_mobile_alert_reminder_notification(
            phase["id"],
            stage,
            recipients=devices,
            title=detail["title"],
            body=body,
            channel_id=detail["channel"],
            data=data,
            event_key=f"reminder:{phase['id']}:{stage}",
            now=now,
        )
        if queued.get("status") != "queued":
            return 0
        return self._outbox.process_pending(now)
