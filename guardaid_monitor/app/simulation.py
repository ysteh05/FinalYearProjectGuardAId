from __future__ import annotations

from collections.abc import Callable

from .config import RuntimeConfig
from .domain import Camera, SensitivityMode, SimulationPlan, SystemSettings
from .model_metadata import VIOLENCE_MODEL_PROTOCOL, VIOLENCE_MODEL_VERSION
from .vlm_contract import MODEL_ID
from .policy import (
    active_violence_threshold,
    estimate_weapon_samples,
    generate_segment_windows,
    violence_borderline_range,
    violence_recording_threshold,
)


class SimulationPlanner:
    """Plans deterministic work without fabricating inference outcomes."""

    def __init__(
        self,
        runtime: RuntimeConfig,
        readiness_provider: Callable[[], dict[str, dict[str, object]]] | None = None,
    ) -> None:
        self._runtime = runtime
        self._readiness_provider = readiness_provider

    def readiness(self) -> dict[str, dict[str, object]]:
        if self._readiness_provider is not None:
            return self._readiness_provider()
        violence_available = self._runtime.violence_checkpoint.is_file()
        weapon_available = bool(
            self._runtime.weapon_checkpoint and self._runtime.weapon_checkpoint.is_file()
        )
        return {
            "violence": {
                "checkpointFound": violence_available,
                "workerConnected": False,
                "checkpoint": self._runtime.violence_checkpoint.name,
                "modelVersion": VIOLENCE_MODEL_VERSION,
                "protocol": "4 s window · 2 s stride · 16 centre-sampled frames · 160x160 RGB-mean letterbox",
                "trainingProtocol": VIOLENCE_MODEL_PROTOCOL,
                "message": (
                    "MC3-18 Kinetics-400 Epoch 8 checkpoint found; inference uses 4 s windows, "
                    "2 s stride, 16 centre-sampled frames and full-frame 160x160 RGB-mean letterboxing."
                    if violence_available
                    else "Fine-tuned MC3-18 checkpoint not found."
                ),
            },
            "weapon": {
                "checkpointFound": weapon_available,
                "workerConnected": False,
                "checkpoint": self._runtime.weapon_checkpoint.name if self._runtime.weapon_checkpoint else None,
                "message": (
                    "Weapon checkpoint found; class mapping still requires validation."
                    if weapon_available
                    else "No validated weapon checkpoint is configured."
                ),
            },
            "vlm": {
                "modelCacheFound": self._runtime.hf_cache.exists(),
                "workerConnected": False,
                "modelId": MODEL_ID,
                "message": "MiniCPM V4.7 1B via local Ollama; reviews stop after first confirmation; descriptions run after the incident ends.",
            },
        }

    def create_plan(self, camera: Camera, settings: SystemSettings) -> SimulationPlan:
        windows = generate_segment_windows(
            camera.duration_seconds,
            settings.violence.segment_seconds,
            settings.violence.stride_seconds,
            include_partial_tail=True,
        )
        borderline_range = violence_borderline_range(settings.violence)
        borderline_interval = (
            borderline_range
            if settings.vlm.borderline_review_enabled
            and borderline_range[0] < borderline_range[1]
            else None
        )
        return SimulationPlan(
            camera_id=camera.id,
            camera_name=camera.name,
            duration_seconds=camera.duration_seconds,
            violence_windows=windows,
            weapon_sample_count=estimate_weapon_samples(
                camera.duration_seconds, settings.weapon.sample_fps
            ),
            violence_recording_threshold=violence_recording_threshold(settings.violence),
            violence_notification_thresholds={
                mode.value: active_violence_threshold(mode, settings.violence)
                for mode in SensitivityMode
            },
            borderline_review_interval=borderline_interval,
            detector_readiness=self.readiness(),
            note=(
                "This endpoint creates the exact processing plan only. It does not invent alerts; "
                "the lowest violence threshold controls alert recording, role thresholds control "
                "ordinary notifications, and only scores in the reported Borderline interval "
                "are queued for review. Detector workers must be connected before results are emitted."
            ),
        )
