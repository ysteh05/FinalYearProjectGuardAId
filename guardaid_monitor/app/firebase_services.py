from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from .config import RuntimeConfig
from .domain import INITIAL_ALERT_DELIVERY_MAX_AGE_SECONDS


LOGGER = logging.getLogger(__name__)

_ANDROID_NOTIFICATION_CHANNEL_ALIASES = {
    "guardaid_urgent_alerts_v7": "guardaid_urgent_alerts_v8",
    "guardaid_urgent_repeat_v1": "guardaid_urgent_repeat_v2",
    "guardaid_urgent_escalated_v1": "guardaid_urgent_escalated_v2",
}


def _current_android_notification_channel(channel_id: str) -> str:
    """Route queued legacy alerts through channels that respect ringer mode."""

    return _ANDROID_NOTIFICATION_CHANNEL_ALIASES.get(channel_id, channel_id)


def _unverified_token_timing(id_token: str) -> str:
    """Return non-identifying JWT timing metadata for diagnostics only."""

    try:
        payload_part = id_token.split(".")[1]
        padding = "=" * (-len(payload_part) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(payload_part + padding).decode("utf-8")
        )
        now = int(time.time())

        def delta(name: str) -> str:
            value = payload.get(name)
            return f"{name}=missing" if not isinstance(value, int) else f"{name}-now={value - now}s"

        return ", ".join((delta("iat"), delta("auth_time"), delta("exp")))
    except (IndexError, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        return "timing metadata unavailable"


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    firebase_uid: str
    email: str | None
    display_name: str | None


class TokenVerificationError(RuntimeError):
    """A safe, user-facing Firebase verification failure classification."""

    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


class TokenVerifier(Protocol):
    def verify(self, id_token: str) -> VerifiedIdentity: ...


@dataclass(frozen=True, slots=True)
class FirebaseAuthAccount:
    uid: str
    email: str
    display_name: str | None
    disabled: bool


class FirebaseAccountAlreadyExists(RuntimeError):
    pass


class UserAccountAdmin(Protocol):
    def find_by_email(self, email: str) -> FirebaseAuthAccount | None: ...
    def create_user(self, email: str, display_name: str, password: str) -> FirebaseAuthAccount: ...
    def delete_user(self, uid: str) -> None: ...


class FirebaseUserAccountAdmin:
    """Server-only Firebase Auth account operations backed by the Admin SDK."""

    def __init__(self, config: RuntimeConfig) -> None:
        self._app = _firebase_app(config)

    @staticmethod
    def _record(user) -> FirebaseAuthAccount:
        return FirebaseAuthAccount(
            uid=user.uid,
            email=user.email or "",
            display_name=user.display_name,
            disabled=bool(user.disabled),
        )

    def find_by_email(self, email: str) -> FirebaseAuthAccount | None:
        from firebase_admin import auth

        try:
            return self._record(auth.get_user_by_email(email, app=self._app))
        except auth.UserNotFoundError:
            return None

    def create_user(self, email: str, display_name: str, password: str) -> FirebaseAuthAccount:
        from firebase_admin import auth

        try:
            user = auth.create_user(
                email=email,
                display_name=display_name,
                password=password,
                email_verified=False,
                disabled=False,
                app=self._app,
            )
        except auth.EmailAlreadyExistsError as exc:
            raise FirebaseAccountAlreadyExists from exc
        return self._record(user)

    def delete_user(self, uid: str) -> None:
        from firebase_admin import auth

        auth.delete_user(uid, app=self._app)


def _firebase_app(config: RuntimeConfig):
    config.validate_firebase_admin()
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError as exc:
        raise RuntimeError("firebase-admin is required for Firebase Auth/FCM") from exc
    try:
        return firebase_admin.get_app("guardaid-identity-messaging")
    except ValueError:
        credential = credentials.Certificate(str(config.firebase_credentials))
        return firebase_admin.initialize_app(
            credential,
            {"projectId": config.firebase_project_id},
            name="guardaid-identity-messaging",
        )


class FirebaseTokenVerifier:
    """Verifies client ID tokens; it never receives or stores user passwords."""

    def __init__(self, config: RuntimeConfig) -> None:
        self._app = _firebase_app(config)

    def verify(self, id_token: str) -> VerifiedIdentity:
        from firebase_admin import auth

        try:
            decoded = auth.verify_id_token(
                id_token,
                app=self._app,
                clock_skew_seconds=60,
            )
        except auth.ExpiredIdTokenError as exc:
            LOGGER.warning("Firebase token verification failed [expired]: %s", exc)
            raise TokenVerificationError(
                "expired",
                "Your Firebase session expired. Sign in again to obtain a new token.",
            ) from exc
        except auth.CertificateFetchError as exc:
            LOGGER.warning(
                "Firebase token verification failed [certificate_fetch_failed]: %s",
                exc,
            )
            raise TokenVerificationError(
                "certificate_fetch_failed",
                "The GuardAId server could not obtain Firebase verification certificates. "
                "Check the computer's internet connection and retry.",
            ) from exc
        except auth.InvalidIdTokenError as exc:
            detail = str(exc).lower()
            if 'incorrect "aud"' in detail or 'incorrect "iss"' in detail:
                LOGGER.warning(
                    "Firebase token verification failed [project_mismatch]: %s",
                    exc,
                )
                raise TokenVerificationError(
                    "project_mismatch",
                    "The mobile app and GuardAId server are configured for different "
                    "Firebase projects.",
                ) from exc
            if any(
                marker in detail
                for marker in ("future", "clock", "too early", "issued at")
            ):
                timing = _unverified_token_timing(id_token)
                LOGGER.warning(
                    "Firebase token verification failed [clock_not_synchronised] (%s): %s",
                    timing,
                    exc,
                )
                raise TokenVerificationError(
                    "clock_not_synchronised",
                    f"Firebase rejected the token time ({timing}). Restart the GuardAId "
                    "backend and sign in again.",
                ) from exc
            LOGGER.warning("Firebase token verification failed [invalid]: %s", exc)
            raise TokenVerificationError(
                "invalid",
                "Firebase rejected this sign-in token. Clear the GuardAId app data and "
                "sign in again.",
            ) from exc
        return VerifiedIdentity(
            firebase_uid=decoded["uid"],
            email=decoded.get("email"),
            display_name=decoded.get("name"),
        )


class NotificationSender(Protocol):
    def send(
        self,
        registration_value: str,
        title: str,
        body: str,
        data: dict[str, str],
        channel_id: str = "guardaid_updates_v1",
    ) -> str: ...


class ConsoleNotificationSender:
    """Safe local adapter: returns a simulated provider ID without logging a device token."""

    def send(self, registration_value: str, title: str, body: str, data: dict[str, str], channel_id: str = "guardaid_updates_v1") -> str:
        return "console-notification-not-sent"


class FcmNotificationSender:
    """FCM-only adapter. It does not initialize Firestore or Cloud Storage."""

    def __init__(self, config: RuntimeConfig) -> None:
        self._app = _firebase_app(config)

    def send(self, registration_value: str, title: str, body: str, data: dict[str, str], channel_id: str = "guardaid_updates_v1") -> str:
        from firebase_admin import messaging

        channel_id = _current_android_notification_channel(channel_id)

        ttl = None
        if data.get("source") == "alert_created":
            ttl_seconds = INITIAL_ALERT_DELIVERY_MAX_AGE_SECONDS
            detected_at = data.get("alertDetectedAt")
            if detected_at:
                try:
                    event_time = datetime.fromisoformat(
                        detected_at.replace("Z", "+00:00")
                    )
                    if event_time.tzinfo is None:
                        event_time = event_time.replace(tzinfo=timezone.utc)
                    age = max(
                        0.0,
                        (datetime.now(timezone.utc) - event_time).total_seconds(),
                    )
                    ttl_seconds = max(
                        0, int(INITIAL_ALERT_DELIVERY_MAX_AGE_SECONDS - age)
                    )
                except (TypeError, ValueError):
                    pass
            ttl = timedelta(seconds=ttl_seconds)

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=data,
            android=messaging.AndroidConfig(
                priority="high",
                ttl=ttl,
                notification=messaging.AndroidNotification(
                    channel_id=channel_id,
                ),
            ),
            token=registration_value,
        )
        return messaging.send(message, app=self._app)


def build_token_verifier(config: RuntimeConfig) -> TokenVerifier | None:
    return FirebaseTokenVerifier(config) if config.auth_mode == "firebase" else None


def build_user_account_admin(config: RuntimeConfig) -> UserAccountAdmin | None:
    return FirebaseUserAccountAdmin(config) if config.auth_mode == "firebase" else None


def build_notification_sender(config: RuntimeConfig) -> NotificationSender:
    return (
        FcmNotificationSender(config)
        if config.notification_mode == "firebase"
        else ConsoleNotificationSender()
    )
