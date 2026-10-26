from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum
import re
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .vlm_contract import MODEL_ID, BINARY_PROMPT_VERSION, SCENE_PROMPT_VERSION

from .model_metadata import (
    WEAPON_CLASS_THRESHOLDS,
    VIOLENCE_CERTAIN_THRESHOLD,
    VIOLENCE_INPUT_FRAMES,
    VIOLENCE_MODERATE_THRESHOLD,
    VIOLENCE_SENSITIVE_THRESHOLD,
    VIOLENCE_STRIDE_SECONDS,
    VIOLENCE_WINDOW_SECONDS,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


INITIAL_ALERT_DELIVERY_MAX_AGE_SECONDS = 60


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


class DomainModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        use_enum_values=True,
        extra="forbid",
    )


class SensitivityMode(StrEnum):
    SENSITIVE = "sensitive"
    MODERATE = "moderate"
    CERTAIN = "certain"


class CameraStatus(StrEnum):
    READY = "ready"
    ANALYSING = "analysing"
    PAUSED = "paused"
    ERROR = "error"


class AlertType(StrEnum):
    VIOLENCE = "violence"
    PHYSICAL_CONTACT_BULLYING = "physical_contact_bullying"
    WEAPON = "weapon"


class AlertStatus(StrEnum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    ESCALATED = "escalated"


class DescriptionStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    DISABLED = "disabled"


class ViolenceSettings(DomainModel):
    segment_seconds: float = Field(default=VIOLENCE_WINDOW_SECONDS, ge=1, le=60)
    stride_seconds: float = Field(default=VIOLENCE_STRIDE_SECONDS, gt=0, le=60)
    detector_input_frames: int = Field(default=VIOLENCE_INPUT_FRAMES, ge=4, le=128)
    sensitive_threshold: float = Field(default=VIOLENCE_SENSITIVE_THRESHOLD, ge=0, le=1)
    moderate_threshold: float = Field(default=VIOLENCE_MODERATE_THRESHOLD, ge=0, le=1)
    certain_threshold: float = Field(default=VIOLENCE_CERTAIN_THRESHOLD, ge=0, le=1)
    incident_merge_gap_seconds: float = Field(default=15.0, ge=0, le=120)
    cooldown_seconds: int = Field(default=15, ge=0, le=3600)

    @model_validator(mode="after")
    def check_threshold_order(self) -> "ViolenceSettings":
        if not (
            self.sensitive_threshold <= self.moderate_threshold <= self.certain_threshold
        ):
            raise ValueError("Violence thresholds must be sensitive <= moderate <= certain.")
        if self.stride_seconds > self.segment_seconds:
            raise ValueError("Violence stride cannot exceed segment duration.")
        return self


class WeaponThresholdProfile(DomainModel):
    # Keep reading historical settings after the deployed class changed from
    # long_gun to the broader gun category.
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        use_enum_values=True,
        extra="ignore",
    )

    knife: float = Field(ge=0, le=1)
    gun: float = Field(ge=0, le=1)

    @field_validator("knife", "gun", mode="before")
    @classmethod
    def round_threshold_to_four_decimals(cls, value: Any) -> float:
        try:
            decimal_value = Decimal(str(value))
            if not decimal_value.is_finite() or not Decimal("0") <= decimal_value <= Decimal("1"):
                raise ValueError("Weapon thresholds must be between 0 and 1.")
            return float(decimal_value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("Weapon thresholds must be finite numbers.") from exc

    @model_validator(mode="before")
    @classmethod
    def rename_legacy_long_gun(cls, value: Any) -> Any:
        if isinstance(value, dict) and "gun" not in value and "long_gun" in value:
            value = {**value, "gun": value["long_gun"]}
        return value


class WeaponClassThresholds(DomainModel):
    sensitive: WeaponThresholdProfile = Field(
        default_factory=lambda: WeaponThresholdProfile(**WEAPON_CLASS_THRESHOLDS["sensitive"])
    )
    moderate: WeaponThresholdProfile = Field(
        default_factory=lambda: WeaponThresholdProfile(**WEAPON_CLASS_THRESHOLDS["moderate"])
    )
    certain: WeaponThresholdProfile = Field(
        default_factory=lambda: WeaponThresholdProfile(**WEAPON_CLASS_THRESHOLDS["certain"])
    )


class WeaponSettings(DomainModel):
    sample_fps: float = Field(default=4.0, ge=0.25, le=30)
    # Retained for compatibility with config v1. Runtime decisions use the
    # calibrated per-class profiles below, not this legacy global threshold.
    confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    nms_candidate_floor: float = Field(default=0.001, ge=0, le=0.1)
    nms_iou_threshold: float = Field(default=0.45, ge=0.1, le=0.95)
    class_thresholds: WeaponClassThresholds = Field(default_factory=WeaponClassThresholds)
    n_required: int = Field(default=3, ge=1, le=120)
    m_window: int = Field(default=5, ge=1, le=120)
    incident_merge_gap_seconds: float = Field(default=15.0, ge=0, le=120)
    cooldown_seconds: int = Field(default=15, ge=0, le=3600)

    @model_validator(mode="after")
    def check_n_of_m(self) -> "WeaponSettings":
        if self.n_required > self.m_window:
            raise ValueError("Weapon N must be less than or equal to M.")
        return self


class VlmSettings(DomainModel):
    model_id: str = MODEL_ID
    quantization: str = "Q4_K_M (Ollama)"
    input_fps: float = Field(default=2.0, ge=0.25, le=12)
    video_token_budget: int = Field(default=2048, ge=256, le=16384)
    max_output_tokens: int = Field(default=1024, ge=32, le=2048)
    # Kept only to read historical config versions. Scene sampling is adaptive.
    scene_description_frames: int = Field(default=20, ge=4, le=128, multiple_of=2)
    borderline_review_enabled: bool = True
    scene_description_enabled: bool = True
    binary_prompt_version: str = BINARY_PROMPT_VERSION
    scene_prompt_version: str = SCENE_PROMPT_VERSION


class AlertNotificationEscalationSettings(DomainModel):
    """Timing for unanswered urgent alerts, measured from the initial push."""

    same_audience_repeat_after_seconds: int = Field(default=60, ge=15, le=3600)
    all_users_escalation_after_seconds: int = Field(default=120, ge=30, le=7200)

    @model_validator(mode="after")
    def check_escalation_order(self) -> "AlertNotificationEscalationSettings":
        if (
            self.all_users_escalation_after_seconds
            <= self.same_audience_repeat_after_seconds
        ):
            raise ValueError(
                "The all-user escalation time must be later than the first repeat time."
            )
        return self


class SceneSummary(DomainModel):
    """Validated, concise VLM scene information exposed to reviewers."""

    # Length is a generation preference, not a storage truncation boundary.
    description: str = Field(min_length=1)
    visible_evidence: list[str] = Field(default_factory=list)
    uncertainty: Literal["low", "medium", "high"] = "high"

    @model_validator(mode="before")
    @classmethod
    def read_minicpm_keys(cls, value: Any) -> Any:
        if isinstance(value, dict) and "summary" in value:
            value = {**value}
            value["description"] = value.pop("summary")
            value["visible_evidence"] = value.pop("details", [])
        return value


class SystemSettings(DomainModel):
    id: str = "global"
    default_sensitivity: SensitivityMode = SensitivityMode.MODERATE
    detection_debug_mode_enabled: bool = False
    violence: ViolenceSettings = Field(default_factory=ViolenceSettings)
    weapon: WeaponSettings = Field(default_factory=WeaponSettings)
    vlm: VlmSettings = Field(default_factory=VlmSettings)
    alert_notification_escalation: AlertNotificationEscalationSettings = Field(
        default_factory=AlertNotificationEscalationSettings
    )
    config_version: int = Field(default=1, ge=1)
    updated_by: str = "local-demo"
    updated_at: datetime = Field(default_factory=utc_now)


class Camera(DomainModel):
    id: str = Field(default_factory=lambda: new_id("cam"))
    name: str = Field(min_length=1, max_length=80)
    location: str = Field(default="Unspecified", max_length=120)
    enabled: bool = True
    source_type: str = "uploaded_video"
    storage_path: str
    original_filename: str
    content_type: str = "video/mp4"
    size_bytes: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    status: CameraStatus = CameraStatus.READY
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CameraUpdate(DomainModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    location: str | None = Field(default=None, max_length=120)
    enabled: bool | None = None


class EvidenceRecord(DomainModel):
    id: str = Field(default_factory=lambda: new_id("evidence"))
    alert_id: str
    media_type: str
    storage_path: str
    content_type: str
    sha256: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    created_at: datetime = Field(default_factory=utc_now)


class DetectionEventSnapshot(DomainModel):
    """Immutable detector evidence for one decision window or sampled frame."""

    confidence: float = Field(ge=0, le=1)
    detected_at: datetime
    source_start_seconds: float | None = Field(default=None, ge=0)
    source_end_seconds: float | None = Field(default=None, ge=0)
    model_version: str
    config_version: int = Field(ge=1)
    qualifications: dict[str, Any] = Field(default_factory=dict)
    weapon_class_scores: dict[str, float] = Field(default_factory=dict)


class Alert(DomainModel):
    id: str = Field(default_factory=lambda: new_id("alert"))
    camera_id: str
    camera_name: str
    type: AlertType
    status: AlertStatus = AlertStatus.OPEN
    confidence: float = Field(ge=0, le=1)
    max_confidence: float = Field(ge=0, le=1)
    detection_count: int = Field(default=1, ge=1)
    sensitivity_mode: SensitivityMode
    is_borderline_promoted: bool = False
    borderline_review_status: Literal[
        "not_applicable", "disabled", "queued", "running", "positive",
        "negative", "failed", "cancelled", "legacy_unknown"
    ] = "not_applicable"
    evidence_status: Literal["preparing", "available", "failed", "legacy_unknown"] = "preparing"
    detection_event_key: str = Field(default_factory=lambda: new_id("detect"))
    detection_qualifications: dict[str, Any] = Field(default_factory=dict)
    weapon_class_scores: dict[str, float] = Field(default_factory=dict)
    source_start_seconds: float | None = Field(default=None, ge=0)
    source_end_seconds: float | None = Field(default=None, ge=0)
    first_detected_at: datetime = Field(default_factory=utc_now)
    last_detected_at: datetime = Field(default_factory=utc_now)
    description_status: DescriptionStatus = DescriptionStatus.PENDING
    scene_description: str | None = None
    scene_summary: SceneSummary | None = None
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    model_version: str
    prompt_version: str | None = None
    config_version: int = 1


class SimulationPlan(DomainModel):
    camera_id: str
    camera_name: str
    duration_seconds: float
    violence_windows: list[tuple[float, float]]
    weapon_sample_count: int
    violence_recording_threshold: float
    violence_notification_thresholds: dict[str, float]
    borderline_review_interval: tuple[float, float] | None
    detector_readiness: dict[str, Any]
    note: str


class AnalysisStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisDebugBox(DomainModel):
    class_name: str
    confidence: float = Field(ge=0, le=1)
    threshold: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)
    x2: float = Field(ge=0, le=1)
    y2: float = Field(ge=0, le=1)


class AnalysisDebugEvent(DomainModel):
    sequence: int = Field(ge=1)
    detector: Literal["violence", "weapon"]
    sensitivity_mode: SensitivityMode
    source_seconds: float = Field(ge=0)
    window_end_seconds: float | None = Field(default=None, ge=0)
    scores: dict[str, float] = Field(default_factory=dict)
    thresholds: dict[str, float] = Field(default_factory=dict)
    positive_labels: list[str] = Field(default_factory=list)
    boxes: list[AnalysisDebugBox] = Field(default_factory=list)
    status: Literal["negative", "positive", "alert", "review_queued"]


class AnalysisJob(DomainModel):
    id: str = Field(default_factory=lambda: new_id("job"))
    camera_id: str
    camera_name: str
    status: AnalysisStatus = AnalysisStatus.QUEUED
    progress_percent: float = Field(default=0, ge=0, le=100)
    source_seconds: float = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    eta_seconds: float | None = Field(default=None, ge=0)
    weapon_frames_processed: int = Field(default=0, ge=0)
    violence_windows_processed: int = Field(default=0, ge=0)
    borderline_reviews_processed: int = Field(default=0, ge=0)
    pending_vlm_jobs: int = Field(default=0, ge=0)
    pending_media_jobs: int = Field(default=0, ge=0)
    alerts_created: int = Field(default=0, ge=0)
    debug_mode_enabled: bool = False
    debug_event_count: int = Field(default=0, ge=0)
    debug_events: list[AnalysisDebugEvent] = Field(default_factory=list)
    latest_result: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class DeviceRegistration(DomainModel):
    registration_value: str = Field(min_length=20, max_length=4096)
    platform: str = Field(pattern="^(android|ios|web)$")
    device_label: str | None = Field(default=None, max_length=100)
    installation_id: str | None = Field(default=None, max_length=512)


class MobileAlertAction(DomainModel):
    action: Literal[
        "dismiss",
        "resolve",
        "escalate",
        "dismiss_false_alarm",
        "confirm_only",
        "confirm_and_handle",
        "confirm_resolved",
        "take_ownership",
        "simulate_emergency_call",
    ]
    reason: str | None = Field(default=None, max_length=1000)
    recipient_role_ids: list[str] = Field(default_factory=list, max_length=20)
    # Legacy compatibility only. A selected Broadcast Role is required, but
    # the current number of eligible devices does not block confirmation.
    allow_empty_audience: bool = False
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)
    expected_workflow_revision: int | None = Field(default=None, ge=0)


class MobileRoleUpdate(DomainModel):
    sensitivity_mode: SensitivityMode | None = None
    display_name: str | None = Field(default=None, min_length=2, max_length=60)
    description: str | None = Field(default=None, max_length=200)
    can_manage_roles: bool | None = None
    can_manage_users: bool | None = None
    can_manage_borderline_settings: bool | None = None


class MobileRoleCreate(DomainModel):
    name: str = Field(min_length=2, max_length=60)
    description: str | None = Field(default=None, max_length=200)
    sensitivity_mode: SensitivityMode
    can_manage_roles: bool = False
    can_manage_users: bool = False
    can_manage_borderline_settings: bool = False


class MobileUserAssignment(DomainModel):
    role_id: str | None = Field(default=None, min_length=1, max_length=100)
    status: Literal["pending", "active", "disabled"]

    @model_validator(mode="after")
    def validate_role_status(self) -> "MobileUserAssignment":
        if self.role_id is None and self.status != "pending":
            raise ValueError("A user without a role must remain pending.")
        return self


class MobileUserCreate(DomainModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=80)
    role_id: str = Field(min_length=1, max_length=100)
    link_existing: bool = False

    @model_validator(mode="after")
    def normalize_and_validate_identity(self) -> "MobileUserCreate":
        self.email = self.email.strip().casefold()
        self.display_name = " ".join(self.display_name.split())
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", self.email):
            raise ValueError("Enter a valid email address.")
        if not self.display_name:
            raise ValueError("Display name is required.")
        return self


class MobileProfileUpdate(DomainModel):
    display_name: str = Field(min_length=1, max_length=80)


class MobileBorderlineSettings(DomainModel):
    enabled: bool
    recipient_role_ids: list[str] = Field(default_factory=list, max_length=20)
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_recipient_when_enabled(self) -> "MobileBorderlineSettings":
        if self.enabled and not self.recipient_role_ids:
            raise ValueError("At least one recipient role is required when borderline alerts are enabled.")
        return self


class BorderlineRecipientUpdate(DomainModel):
    enabled: bool
    revision: int = Field(ge=1)
