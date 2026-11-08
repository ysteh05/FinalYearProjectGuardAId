from __future__ import annotations

import math
from datetime import datetime
from typing import Iterable, Sequence

from .domain import SensitivityMode, ViolenceSettings


def generate_segment_windows(
    duration_seconds: float,
    segment_seconds: float,
    stride_seconds: float,
    *,
    include_partial_tail: bool = True,
) -> list[tuple[float, float]]:
    """Return deterministic sliding windows in source-video seconds."""
    if duration_seconds <= 0:
        return []
    if segment_seconds <= 0 or stride_seconds <= 0:
        raise ValueError("segment_seconds and stride_seconds must be positive")
    if stride_seconds > segment_seconds:
        raise ValueError("stride_seconds cannot exceed segment_seconds")

    windows: list[tuple[float, float]] = []
    start = 0.0
    epsilon = 1e-9
    while start + segment_seconds <= duration_seconds + epsilon:
        windows.append((round(start, 3), round(min(start + segment_seconds, duration_seconds), 3)))
        start += stride_seconds

    if include_partial_tail:
        final_start = windows[-1][0] if windows else -1.0
        tail_start = max(0.0, duration_seconds - segment_seconds)
        if tail_start > final_start + epsilon:
            windows.append((round(tail_start, 3), round(duration_seconds, 3)))
    return windows


def active_violence_threshold(
    mode: SensitivityMode | str, settings: ViolenceSettings
) -> float:
    mode_value = SensitivityMode(mode)
    return {
        SensitivityMode.SENSITIVE: settings.sensitive_threshold,
        SensitivityMode.MODERATE: settings.moderate_threshold,
        SensitivityMode.CERTAIN: settings.certain_threshold,
    }[mode_value]


def violence_recording_threshold(settings: ViolenceSettings) -> float:
    """Lowest calibrated violence threshold; independent of the UI profile."""
    return min(
        settings.sensitive_threshold,
        settings.moderate_threshold,
        settings.certain_threshold,
    )


def violence_qualified_modes(
    confidence: float, settings: ViolenceSettings
) -> list[SensitivityMode]:
    """Profiles whose direct-notification threshold this score has reached."""
    return [
        mode
        for mode in SensitivityMode
        if confidence >= active_violence_threshold(mode, settings)
    ]


def violence_borderline_range(settings: ViolenceSettings) -> tuple[float, float]:
    """Return [recording floor, highest profile threshold)."""
    thresholds = (
        settings.sensitive_threshold,
        settings.moderate_threshold,
        settings.certain_threshold,
    )
    return min(thresholds), max(thresholds)


def weapon_profile_qualified_classes(
    class_scores: dict[str, float], profile
) -> list[str]:
    """Return classes that clear this profile, ignoring absent classes."""
    return sorted(
        name
        for name in ("knife", "gun")
        if name in class_scores and class_scores[name] >= getattr(profile, name)
    )


def is_borderline_candidate(
    confidence: float,
    user_threshold: float,
    sensitive_threshold: float,
) -> bool:
    return sensitive_threshold <= confidence < user_threshold


def n_of_m_trigger(
    confidences: Sequence[float],
    threshold: float,
    n_required: int,
    m_window: int,
) -> bool:
    if n_required < 1 or m_window < 1 or n_required > m_window:
        raise ValueError("N-of-M requires 1 <= N <= M")
    recent = confidences[-m_window:]
    return len(recent) >= m_window and sum(value >= threshold for value in recent) >= n_required


def first_n_of_m_trigger_index(
    confidences: Sequence[float],
    threshold: float,
    n_required: int,
    m_window: int,
) -> int | None:
    for index in range(m_window - 1, len(confidences)):
        if n_of_m_trigger(confidences[: index + 1], threshold, n_required, m_window):
            return index
    return None


def cooldown_allows(
    last_emitted_at: datetime | None,
    candidate_at: datetime,
    cooldown_seconds: int,
) -> bool:
    if last_emitted_at is None or cooldown_seconds <= 0:
        return True
    return (candidate_at - last_emitted_at).total_seconds() >= cooldown_seconds


def estimate_weapon_samples(duration_seconds: float, fps: float) -> int:
    if duration_seconds <= 0 or fps <= 0:
        return 0
    return math.ceil(duration_seconds * fps)


def count_threshold_hits(values: Iterable[float], threshold: float) -> int:
    return sum(value >= threshold for value in values)
