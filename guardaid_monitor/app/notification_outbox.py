from __future__ import annotations

import json
import logging
from datetime import datetime
from threading import Event, Thread


LOGGER = logging.getLogger(__name__)


class MobileNotificationOutboxWorker:
    """Deliver persisted mobile notifications and resume interrupted attempts."""

    def __init__(self, repository, sender, *, poll_seconds: float = 1.0) -> None:
        self._repository = repository
        self._sender = sender
        self._poll_seconds = poll_seconds
        self._stop_event = Event()
        self._wake_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if not hasattr(self._repository, "claim_mobile_notification_outbox"):
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(
            target=self._run,
            name="guardaid-mobile-notification-outbox",
            daemon=True,
        )
        self._thread.start()
        self.notify()

    def notify(self) -> None:
        self._wake_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._wake_event.wait(self._poll_seconds)
            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            try:
                self.process_pending()
            except Exception as exc:
                LOGGER.error(
                    "Mobile notification outbox scan failed (%s).",
                    type(exc).__name__,
                )

    def process_pending(self, now: datetime | None = None) -> int:
        sent = 0
        while True:
            dispatch = self._repository.claim_mobile_notification_outbox(now)
            if dispatch is None:
                return sent
            try:
                payload = json.loads(dispatch["data_payload_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            data = {str(key): str(value) for key, value in payload.items()}
            devices = self._repository.list_mobile_outbox_devices(
                dispatch["id"], dispatch["recipient_user_ids"]
            )
            if not devices:
                self._repository.complete_mobile_notification_outbox(
                    dispatch["id"], no_recipients=True, now=now
                )
                continue
            try:
                payload = json.loads(dispatch["data_payload_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            data = {str(key): str(value) for key, value in payload.items()}
            notification = self._repository.notification_content(
                dispatch["notification_id"]
            )
            if notification is None:
                self._repository.complete_mobile_notification_outbox(
                    dispatch["id"],
                    no_recipients=True,
                    error_code="NotificationRecordMissing",
                    now=now,
                )
                continue
            if payload.get("source") == "alert_created" and hasattr(
                self._repository, "prepare_mobile_alert_initial_delivery"
            ):
                eligible_users = self._repository.prepare_mobile_alert_initial_delivery(
                    dispatch["id"], now=now
                )
                devices = [item for item in devices if item["user_id"] in eligible_users]
                if not devices:
                    self._repository.complete_mobile_notification_outbox(
                        dispatch["id"], no_recipients=True, now=now
                    )
                    continue
            if payload.get("source") == "alert_reminder":
                phase_id = payload.get("reminderPhaseId")
                try:
                    stage = int(payload.get("reminderStage"))
                except (TypeError, ValueError):
                    stage = 0
                prepare = getattr(
                    self._repository,
                    "prepare_mobile_alert_reminder_delivery",
                    None,
                )
                if not phase_id or stage not in {1, 2} or prepare is None:
                    self._repository.complete_mobile_notification_outbox(
                        dispatch["id"],
                        no_recipients=True,
                        error_code="ReminderDispatchMetadataMissing",
                        now=now,
                    )
                    continue
                allowed_users = prepare(dispatch["id"], str(phase_id), stage, now=now)
                if allowed_users is None:
                    continue
                if not allowed_users:
                    self._repository.complete_mobile_notification_outbox(
                        dispatch["id"], no_recipients=True, now=now
                    )
                    continue
                devices = [item for item in devices if item["user_id"] in allowed_users]
                if not devices:
                    self._repository.complete_mobile_notification_outbox(
                        dispatch["id"], no_recipients=True, now=now
                    )
                    continue
            for device in devices:
                if device.get("prior_status") == "sent":
                    continue
                try:
                    provider_id = self._sender.send(
                        device["fcm_registration_value"],
                        notification["title"],
                        notification["body"],
                        data,
                        dispatch["channel_id"],
                    )
                    self._repository.record_mobile_outbox_delivery(
                        dispatch["id"],
                        dispatch["notification_id"],
                        device["id"],
                        "sent",
                        provider_id,
                        None,
                        recipient_user_id=device["user_id"],
                    )
                    sent += 1
                except Exception as exc:
                    try:
                        self._repository.record_mobile_outbox_delivery(
                            dispatch["id"],
                            dispatch["notification_id"],
                            device["id"],
                            "failed",
                            None,
                            type(exc).__name__,
                            recipient_user_id=device["user_id"],
                        )
                    except Exception as persist_exc:
                        LOGGER.error(
                            "Could not persist an outbox delivery failure (%s).",
                            type(persist_exc).__name__,
                        )
            self._repository.complete_mobile_notification_outbox(
                dispatch["id"], now=now
            )
