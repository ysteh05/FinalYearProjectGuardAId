from __future__ import annotations

import sys
import types
from datetime import timedelta

import pytest

from app import firebase_services
from app.domain import INITIAL_ALERT_DELIVERY_MAX_AGE_SECONDS


class _Payload:
    def __init__(self, **values):
        self.__dict__.update(values)


@pytest.mark.parametrize(
    ("channel_id", "data"),
    [
        (
            "guardaid_urgent_alerts_v7",
            {
                "alertId": "test-alert",
                "notificationStage": "initial_alert",
                "source": "alert_created",
            },
        ),
        (
            "guardaid_urgent_alerts_v7",
            {
                "alertId": "test-alert",
                "source": "alert_broadcast",
                "notificationStage": "initial_broadcast",
                "channelId": "guardaid_urgent_alerts_v7",
            },
        ),
        (
            "guardaid_urgent_repeat_v1",
            {"alertId": "test-alert", "notificationStage": "first_repeat"},
        ),
        (
            "guardaid_urgent_escalated_v1",
            {"alertId": "test-alert", "notificationStage": "all_users_escalation"},
        ),
        (
            "guardaid_updates_v1",
            {"alertId": "test-alert", "source": "alert_decision"},
        ),
    ],
)
def test_fcm_sender_builds_visible_notification_and_android_channel(
    monkeypatch: pytest.MonkeyPatch,
    channel_id: str,
    data: dict[str, str],
) -> None:
    sent: dict[str, object] = {}
    fake_app = object()

    def fake_send(message, *, app):
        sent["message"] = message
        sent["app"] = app
        return "test-provider-accepted-id"

    fake_messaging = types.ModuleType("firebase_admin.messaging")
    fake_messaging.Notification = lambda **values: _Payload(**values)
    fake_messaging.AndroidNotification = lambda **values: _Payload(**values)
    fake_messaging.AndroidConfig = lambda **values: _Payload(**values)
    fake_messaging.Message = lambda **values: _Payload(**values)
    fake_messaging.send = fake_send

    fake_firebase_admin = types.ModuleType("firebase_admin")
    fake_firebase_admin.messaging = fake_messaging
    monkeypatch.setitem(sys.modules, "firebase_admin", fake_firebase_admin)
    monkeypatch.setitem(sys.modules, "firebase_admin.messaging", fake_messaging)
    monkeypatch.setattr(firebase_services, "_firebase_app", lambda _config: fake_app)

    sender = firebase_services.FcmNotificationSender(config=object())
    result = sender.send(
        registration_value="not-a-real-device-token",
        title="Test notification title",
        body="Test notification body",
        data=data,
        channel_id=channel_id,
    )

    message = sent["message"]
    assert result == "test-provider-accepted-id"
    assert sent["app"] is fake_app
    assert message.notification.title == "Test notification title"
    assert message.notification.body == "Test notification body"
    assert message.data == data
    assert message.android.priority == "high"
    assert message.android.ttl == (
        timedelta(seconds=INITIAL_ALERT_DELIVERY_MAX_AGE_SECONDS)
        if data.get("source") == "alert_created"
        else None
    )
    assert message.android.notification.channel_id == channel_id
    assert message.token == "not-a-real-device-token"
