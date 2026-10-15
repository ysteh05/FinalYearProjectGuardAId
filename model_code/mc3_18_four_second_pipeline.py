"""Train the independent MC3-18 four-second / sixteen-frame MIL protocol.

The runner preserves the original splits for RWF-2000, Real Life Violence
Dataset and ShanghaiTech, then adds UCF and AIRTLab with grouped 80/10/10
splits. Matching AIRTLab filenames across cam1/cam2 are assigned together.
GuardAId remains a separate extra test set and is excluded from train/val/test.
The old two-second frame cache is read-only and reused when its sampled frames
match; only missing frames are stored under a separately capped 5 GiB cache.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

import mc3_18_mil_pipeline as core


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_ROOT = PROJECT_ROOT / "GuardAId Video Classification Model Dataset"
DEFAULT_UCF_ROOT = DEFAULT_DATASET_ROOT / "UCF"
DEFAULT_AIRTLAB_ROOT = (
    DEFAULT_DATASET_ROOT / "AIRTLab" / "A-Dataset-for-Automatic-Violence-Detection-in-Videos-master"
    / "violence-detection-dataset"
)
DEFAULT_GUARDAID_ROOT = DEFAULT_DATASET_ROOT / "GuardAId"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_4s16f"
DEFAULT_LEGACY_CACHE_ROOT = (
    PROJECT_ROOT / "model_runs" / "mc3_2s8f_reverse_concat" / "_frame_cache"
    / "mc3-2s-1s-stride-8f-128short-112crop-reverse-concat-v2"
)
DEFAULT_SUPPLEMENT_CACHE_ROOT = DEFAULT_OUTPUT_ROOT / "_supplemental_cache"
LEGACY_PROTOCOL = "mc3-2s-1s-stride-8f-128short-112crop-reverse-concat-v2"
PROTOCOL = "mc3-4s-2s-stride-16f-128short-112crop-short-tail-v1"
WINDOW_SECONDS = 4.0
STRIDE_SECONDS = 2.0
NUM_FRAMES = 16
MIN_SCAN_SECONDS = 1.0
MAX_SUPPLEMENT_CACHE_BYTES = 5 * 1024**3
VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv"}

LEGACY_DATASET_ROOT = DEFAULT_DATASET_ROOT
UCF_ROOT = DEFAULT_UCF_ROOT
AIRTLAB_ROOT = DEFAULT_AIRTLAB_ROOT
GUARDAID_ROOT = DEFAULT_GUARDAID_ROOT
LEGACY_CACHE_ROOT = DEFAULT_LEGACY_CACHE_ROOT
SUPPLEMENT_CACHE_ROOT = DEFAULT_SUPPLEMENT_CACHE_ROOT
# Optional, immutable 4S caches from an audited prior run. The native
# fine-tuning page uses this to avoid decoding the same AIRT Lab frames again.
READ_ONLY_SUPPLEMENT_CACHE_ROOTS: tuple[Path, ...] = ()
_reverse: Any = None
np: Any = None
_ACTIVE_SEED = 2026
_EXTERNAL_GUARDAID_RECORDS: list[core.VideoRecord] = []


def _numpy() -> Any:
    global np
    if np is None:
        import numpy as numpy_module

        np = numpy_module
    return np


def duration_from_frame_count(fps: float, frame_count: int, recorded_duration: Any = None) -> float:
    """Return the duration used by the 4s16f2 extractor from its frame metadata.

    Skeleton manifests store the duration rounded to nine decimals, whereas
    window indices were generated from the unrounded ``frame_count / fps``
    value. Reconstructing from the rounded value can shift tail-window sample
    indices by one frame, so training and auditing must use this same source.
    """
    if not math.isfinite(fps) or fps <= 0 or frame_count <= 0:
        raise ValueError(f"Invalid frame-derived duration metadata: fps={fps}, frame_count={frame_count}")
    duration = frame_count / fps
    if recorded_duration not in (None, ""):
        try:
            recorded = float(recorded_duration)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid recorded video duration: {recorded_duration!r}") from exc
        if not math.isfinite(recorded) or abs(recorded - duration) > (1.0 / fps + 1e-6):
            raise ValueError(
                "Recorded duration is inconsistent with frame_count/fps: "
                f"recorded={recorded}, reconstructed={duration:.12f}"
            )
    return duration


def make_four_second_windows(duration: float, fps: float, frame_count: int) -> list[core.Window]:
    """Return full 4 s windows, one end-aligned tail, or one short natural clip.

    A sub-four-second clip gets 16 samples uniformly distributed over its real
    duration. Longer videos use 16 frame-centre samples at 4 frames/second.
    """
    if (not math.isfinite(duration) or not math.isfinite(fps) or duration < MIN_SCAN_SECONDS
            or fps <= 0 or frame_count <= 0):
        return []
    intervals: list[tuple[float, float]] = []
    if duration < WINDOW_SECONDS:
        interval = (0.0, duration)
        indices = tuple(
            max(0, min(frame_count - 1, int(math.floor(((index + 0.5) * duration / NUM_FRAMES) * fps))))
            for index in range(NUM_FRAMES)
        )
        return [core.Window(interval[0], interval[1], indices)]

    start = 0.0
    while start + WINDOW_SECONDS <= duration + 1e-9:
        intervals.append((round(start, 9), round(start + WINDOW_SECONDS, 9)))
        start += STRIDE_SECONDS
    tail_start = max(0.0, duration - WINDOW_SECONDS)
    if not intervals or tail_start > intervals[-1][0] + 1e-6:
        intervals.append((tail_start, duration))

    windows: list[core.Window] = []
    for start, end in intervals:
        indices = tuple(
            max(0, min(frame_count - 1, int(math.floor((start + (index + 0.5) * WINDOW_SECONDS / NUM_FRAMES) * fps))))
            for index in range(NUM_FRAMES)
        )
        windows.append(core.Window(start, end, indices))
    return windows


def _windows_for_record(record: core.VideoRecord, *, training: bool) -> list[core.Window]:
    del training  # The selected policy uses natural clips; no reverse concatenation.
    return make_four_second_windows(record.duration, record.fps, record.frame_count)


def _selected_legacy_roots(root: Path) -> list[tuple[str, Path]]:
    root = Path(root).expanduser().resolve()
    candidates = [
        ("RWF-2000", root / "RWF-2000"),
        ("Real Life Violence Dataset", root / "archive" / "Real Life Violence Dataset"),
        ("ShanghaiTech", root / "ShanghaiTech"),
    ]
    missing = [str(path) for _, path in candidates if not path.is_dir()]
    if missing:
        raise FileNotFoundError("Missing selected legacy datasets:\n  " + "\n  ".join(missing))
    return candidates


def _protocol() -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "window_seconds": WINDOW_SECONDS,
        "stride_seconds": STRIDE_SECONDS,
        "frames_per_window": NUM_FRAMES,
        "sampling_rate_fps": NUM_FRAMES / WINDOW_SECONDS,
        "frame_sample_positions": [round((index + 0.5) / NUM_FRAMES, 6) for index in range(NUM_FRAMES)],
        "short_side": core.SHORT_SIDE,
        "crop_size": core.CROP_SIZE,
        "color_space": "RGB",
        "normalization_mean": list(core.MEAN),
        "normalization_std": list(core.STD),
        "short_clip_policy": {
            "duration_below_seconds": WINDOW_SECONDS,
            "operation": "sample 16 frame centres uniformly over the real video duration; do not reverse or pad",
            "minimum_duration_seconds": MIN_SCAN_SECONDS,
            "real_life_violence_dataset": "exclude samples shorter than 4 seconds in every split",
        },
        "tail_policy": "add one end-aligned 4-second window when the regular 2-second stride leaves an uncovered tail",
        "video_aggregation": "maximum_window_probability",
        # Protocol metadata describes the fixed experiment, not whichever
        # source directories happen to be mounted at checkpoint-save time.
        # Dataset discovery remains validated by load_records/preflight.
        "legacy_datasets": ["RWF-2000", "Real Life Violence Dataset", "ShanghaiTech"],
        "ucf_split": "preserve the existing video-source-group 80/10/10 split",
        "airtlab_split": {
            "ratios": {"train": 0.8, "val": 0.1, "test": 0.1},
            "unit": "normalized filename stem within label, shared across camera folders",
            "camera_pairing": "matching cam1/cam2 names are one source group and cannot cross splits",
            "seed": _ACTIVE_SEED,
        },
        "source_roots": {
            "legacy": str(LEGACY_DATASET_ROOT.resolve()),
            "ucf": str(UCF_ROOT.resolve()),
            "airtlab": str(AIRTLAB_ROOT.resolve()),
            "guardaid": str(GUARDAID_ROOT.resolve()),
        },
        "cache_reuse": {
            "legacy_read_only_cache": str(LEGACY_CACHE_ROOT.resolve()),
            "supplemental_cache": str(SUPPLEMENT_CACHE_ROOT.resolve()),
            "supplemental_max_bytes": MAX_SUPPLEMENT_CACHE_BYTES,
            "missing_frame_policy": "decode on demand when a new frame cannot fit under the supplement cap",
        },
        "excluded_datasets": ["bus-violence", "seymanurakti-fight-detection-surv-dataset-ff83b7c"],
        "external_test_only": ["GuardAId"],
        "extra_test_only": ["GuardAId"],
        "guardaid_label_policy": "unlabeled; conditional NonFight false-positive summaries only",
        "leakage_controls": [
            "preserve existing train/val/test assignments for the selected legacy datasets",
            "split UCF by normalized source group and exact-file duplicate group",
            "split AIRTLab 80/10/10 by normalized filename stem; matching cam1/cam2 views stay together",
            "exclude exact duplicate/conflicting samples and cross-split duplicate copies",
            "keep GuardAId outside train/validation/test and evaluate it as a separate extra test set",
        ],
        "airtlab_note": "AIRTLab camera views of the same filename share a split; per-clip metrics are not independent-event metrics",
    }


def _legacy_cache_path(record: core.VideoRecord) -> Path:
    key = (
        f"{LEGACY_PROTOCOL}|{record.path}|{record.size_bytes}|{record.mtime_ns}|"
        f"{record.fps:.6f}|{record.frame_count}"
    )
    return LEGACY_CACHE_ROOT / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.npz"


def _supplement_cache_path(record: core.VideoRecord) -> Path:
    return SUPPLEMENT_CACHE_ROOT / _supplement_cache_filename(record)


def _supplement_cache_filename(record: core.VideoRecord) -> str:
    key = (
        f"{PROTOCOL}|{record.path}|{record.size_bytes}|{record.mtime_ns}|"
        f"{record.fps:.6f}|{record.frame_count}"
    )
    return f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.npz"


def _cache_indices(path: Path) -> set[int]:
    numpy_module = _numpy()
    if not path.is_file():
        return set()
    try:
        with numpy_module.load(path, allow_pickle=False) as data:
            indices = data["frame_indices"]
            frames = data["frames"]
            if indices.ndim != 1:
                raise ValueError("frame_indices must be one-dimensional")
            if frames.ndim != 4 or len(indices) != len(frames):
                raise ValueError("frames must be NHWC and match frame_indices")
            return {int(value) for value in indices.tolist()}
    except (OSError, KeyError, ValueError) as exc:
        raise RuntimeError(f"Invalid frame-cache entry {path}: {exc}") from exc


def _decode_record_frames(record: core.VideoRecord, indices: Sequence[int], cv2: Any) -> Any:
    numpy_module = _numpy()
    from mc3_18_reverse_concat_pipeline import _extract_zip_member

    archive_path = getattr(record, "archive_path", None)
    archive_member = getattr(record, "archive_member", None)
    if archive_path and archive_member:
        with tempfile.TemporaryDirectory(prefix="mc3_4s_cache_") as directory:
            video_path = Path(directory) / Path(str(archive_member)).name
            _extract_zip_member(Path(archive_path), str(archive_member), video_path)
            decoded_record = replace(record, path=video_path)
            return core._decode_selected_frames(decoded_record, indices, cv2, numpy_module)
    return core._decode_selected_frames(record, indices, cv2, numpy_module)


def _cache_size_bytes(cache_dir: Path) -> int:
    return sum(path.stat().st_size for path in cache_dir.glob("*.npz") if path.is_file()) if cache_dir.is_dir() else 0


def _validate_cache_roots(legacy_root: Path, supplement_root: Path) -> tuple[Path, Path]:
    legacy = Path(legacy_root).expanduser().resolve()
    supplement = Path(supplement_root).expanduser().resolve()
    if legacy == supplement or legacy in supplement.parents or supplement in legacy.parents:
        raise ValueError("The supplemental cache must be separate from the shared read-only two-second cache.")
    return legacy, supplement


def _atomic_write_cache(
    path: Path,
    indices: Sequence[int],
    frames: Any,
    *,
    cap_bytes: int | None = None,
    current_bytes: int = 0,
    prior_size: int = 0,
) -> tuple[bool, int]:
    numpy_module = _numpy()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Cache filenames already contain a 64-character digest. Keep the atomic
    # sibling temp name short so Windows does not exceed MAX_PATH when adding
    # the PID and timestamp to a long profile directory.
    temporary = _cache_write_temp_path(path)
    try:
        with temporary.open("wb") as handle:
            numpy_module.savez_compressed(handle, frame_indices=numpy_module.asarray(indices, dtype=numpy_module.int32), frames=frames)
        new_size = temporary.stat().st_size
        if cap_bytes is not None:
            used_without_old = current_bytes - prior_size
            if used_without_old + new_size > cap_bytes:
                temporary.unlink(missing_ok=True)
                return False, current_bytes
        os.replace(temporary, path)
        return True, current_bytes - prior_size + new_size
    finally:
        temporary.unlink(missing_ok=True)


def _cache_write_temp_path(path: Path) -> Path:
    """Return a unique, short sibling path for an atomic cache write."""
    return Path(path).with_name(f".cache-write-{os.getpid()}-{time.time_ns()}.tmp")


def _prepare_hybrid_cache(
    records: Sequence[core.VideoRecord],
    cache_dir: Path,
    cv2: Any,
    _np: Any,
    logger: logging.Logger,
    *,
    rebuild: bool,
) -> None:
    global SUPPLEMENT_CACHE_ROOT
    numpy_module = _numpy()
    if rebuild:
        raise RuntimeError("The shared two-second cache is read-only; rebuilding is disabled for the four-second protocol.")
    _, SUPPLEMENT_CACHE_ROOT = _validate_cache_roots(LEGACY_CACHE_ROOT, Path(cache_dir))
    SUPPLEMENT_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    used = _cache_size_bytes(SUPPLEMENT_CACHE_ROOT)
    if used > MAX_SUPPLEMENT_CACHE_BYTES:
        raise RuntimeError(
            f"Supplement cache already uses {used} bytes, above its {MAX_SUPPLEMENT_CACHE_BYTES}-byte cap. "
            "Choose an empty supplement-cache folder; the existing files were not changed."
        )

    legacy_entries = 0
    supplement_reused = 0
    supplement_created = 0
    prior_supplement_entries = 0
    decoded_missing = 0
    uncached_missing = 0
    cache_errors: list[str] = []
    started = time.monotonic()
    for count, record in enumerate(records, start=1):
        requested = sorted({index for window in _windows_for_record(record, training=record.split == "train") for index in window.frame_indices})
        if not requested:
            raise RuntimeError(f"No frame samples were generated for {record.path}")
        legacy_path = _legacy_cache_path(record)
        supplement_path = _supplement_cache_path(record)
        prior_supplement_paths = tuple(
            Path(root).expanduser().resolve() / _supplement_cache_filename(record)
            for root in READ_ONLY_SUPPLEMENT_CACHE_ROOTS
            if Path(root).expanduser().resolve() != SUPPLEMENT_CACHE_ROOT.resolve()
        )
        record.legacy_cache_path = legacy_path
        record.supplement_cache_path = supplement_path
        record.prior_supplement_cache_paths = prior_supplement_paths
        record.required_frame_indices = tuple(requested)
        record.cache_path = supplement_path
        try:
            legacy_indices = _cache_indices(legacy_path)
            supplement_indices = _cache_indices(supplement_path)
            prior_supplement_indices = set()
            for prior_path in prior_supplement_paths:
                prior_supplement_indices.update(_cache_indices(prior_path))
        except RuntimeError as exc:
            cache_errors.append(str(exc))
            continue
        if prior_supplement_indices:
            prior_supplement_entries += 1
        if legacy_indices:
            legacy_entries += 1
        if supplement_indices:
            supplement_reused += 1
        missing = sorted(set(requested) - legacy_indices - prior_supplement_indices - supplement_indices)
        if missing and used < MAX_SUPPLEMENT_CACHE_BYTES:
            frames = _decode_record_frames(record, missing, cv2)
            decoded_missing += len(missing)
            prior_size = supplement_path.stat().st_size if supplement_path.is_file() else 0
            if supplement_indices:
                with numpy_module.load(supplement_path, allow_pickle=False) as saved:
                    old_indices = saved["frame_indices"].astype(numpy_module.int32).tolist()
                    old_frames = saved["frames"].copy()
                by_index = {int(index): old_frames[i] for i, index in enumerate(old_indices)}
                by_index.update({int(index): frames[i] for i, index in enumerate(missing)})
                final_indices = sorted(by_index)
                final_frames = numpy_module.stack([by_index[index] for index in final_indices]).astype(numpy_module.uint8, copy=False)
            else:
                final_indices, final_frames = missing, frames
            written, used = _atomic_write_cache(
                supplement_path,
                final_indices,
                final_frames,
                cap_bytes=MAX_SUPPLEMENT_CACHE_BYTES,
                current_bytes=used,
                prior_size=prior_size,
            )
            if written:
                if not supplement_indices:
                    supplement_created += 1
                supplement_reused += 1 if supplement_indices else 0
            else:
                uncached_missing += len(missing)
        elif missing:
            uncached_missing += len(missing)
        if count == 1 or count % 100 == 0 or count == len(records):
            logger.info(
                "Hybrid frame cache %d/%d | legacy entries=%d | prior 4s entries=%d | supplemental entries=%d | supplement=%.2f/%.2f GiB | missing stored=%d | on-demand frames=%d",
                count, len(records), legacy_entries, prior_supplement_entries, supplement_reused,
                used / (1024**3), MAX_SUPPLEMENT_CACHE_BYTES / (1024**3), decoded_missing - uncached_missing,
                uncached_missing,
            )
    if cache_errors:
        raise RuntimeError("One or more cache entries could not be read safely:\n" + "\n".join(cache_errors[:20]))
    logger.info(
        "Hybrid frame cache ready | records=%d | legacy hits=%d | prior 4s cache hits=%d | supplemental entries reused=%d/new=%d | supplemental bytes=%d/%d (%.2f GiB cap) | missing frames cached=%d | on-demand frames=%d | elapsed=%.1fs",
        len(records), legacy_entries, prior_supplement_entries, supplement_reused, supplement_created,
        used, MAX_SUPPLEMENT_CACHE_BYTES, MAX_SUPPLEMENT_CACHE_BYTES / (1024**3),
        decoded_missing - uncached_missing, uncached_missing, time.monotonic() - started,
    )


def _load_hybrid_cache(record: core.VideoRecord, _np: Any) -> tuple[Any, dict[int, int]]:
    numpy_module = _np if _np is not None else _numpy()
    frames_by_index: dict[int, Any] = {}
    cache_paths = [getattr(record, "legacy_cache_path", None)]
    cache_paths.extend(getattr(record, "prior_supplement_cache_paths", ()))
    cache_paths.append(getattr(record, "supplement_cache_path", None))
    seen_paths: set[Path] = set()
    for path in cache_paths:
        if path is None or not Path(path).is_file():
            continue
        resolved_path = Path(path).resolve()
        if resolved_path in seen_paths:
            continue
        seen_paths.add(resolved_path)
        with numpy_module.load(path, allow_pickle=False) as saved:
            indices = saved["frame_indices"].astype(numpy_module.int64)
            cached_frames = saved["frames"].copy()
        if cached_frames.ndim != 4 or len(indices) != len(cached_frames):
            raise RuntimeError(f"Corrupt frame-cache dimensions in {path}")
        for position, frame_index in enumerate(indices.tolist()):
            frames_by_index.setdefault(int(frame_index), cached_frames[position])
    required = list(getattr(record, "required_frame_indices", ()))
    missing = [int(index) for index in required if int(index) not in frames_by_index]
    if missing:
        import cv2

        new_frames = _decode_record_frames(record, missing, cv2)
        for position, frame_index in enumerate(missing):
            frames_by_index[frame_index] = new_frames[position]
    if not required:
        raise RuntimeError(f"The four-second cache has no frame index manifest for {record.path}")
    ordered = sorted(set(required))
    frames = numpy_module.stack([frames_by_index[index] for index in ordered]).astype(numpy_module.uint8, copy=False)
    return frames, {frame_index: position for position, frame_index in enumerate(ordered)}


def _scan_four_second_datasets(dataset_root: Path, cv2: Any, logger: logging.Logger):
    """Reuse the established scanner and grouping rules, then apply the 4 s RLVD rule."""
    global _EXTERNAL_GUARDAID_RECORDS
    assert _reverse is not None
    original_window, original_minimum = _reverse.WINDOW_SECONDS, _reverse.MIN_TRAIN_SECONDS
    try:
        # Scan natural clips from 1 s onward so the new short-tail policy can
        # keep short examples from the selected sources. RLVD is filtered below.
        _reverse.WINDOW_SECONDS = MIN_SCAN_SECONDS
        _reverse.MIN_TRAIN_SECONDS = MIN_SCAN_SECONDS
        records, manifest = _reverse._scan_datasets(dataset_root, cv2, logger)
    finally:
        _reverse.WINDOW_SECONDS, _reverse.MIN_TRAIN_SECONDS = original_window, original_minimum

    # GuardAId is an independent, unlabeled external test. Keep it out of the
    # model's train/validation/held-out test records and evaluate it separately
    # only after the checkpoint and thresholds have been locked.
    _EXTERNAL_GUARDAID_RECORDS = [record for record in records if record.dataset == "GuardAId"]
    for record in _EXTERNAL_GUARDAID_RECORDS:
        record.split = "external_test"
    records = [record for record in records if record.dataset != "GuardAId"]

    kept, short_tail_count = _apply_rlvd_duration_rule(records, manifest)
    for row in manifest:
        if row.get("dataset") == "Real Life Violence Dataset":
            try:
                duration = float(row.get("duration_seconds", 0) or 0)
                reason = str(row.get("exclusion_reason", ""))
                scanned_short_reason = reason in {
                    "", "shorter_than_1_second", "shorter_than_2_seconds_for_natural_sampling",
                    "real_life_violence_shorter_than_4_seconds",
                }
                if duration > 0 and scanned_short_reason and duration < WINDOW_SECONDS:
                    row["eligible"] = 0
                    row["exclusion_reason"] = "real_life_violence_shorter_than_4_seconds"
                    row["short_tail_window_used"] = 0
            except (TypeError, ValueError):
                pass
    if not kept:
        raise RuntimeError("No samples remain after applying the selected four-second data rules.")
    logger.info("Four-second short-tail policy: %d selected non-RLVD samples below 4 s retained with natural sampling.", short_tail_count)
    return kept, manifest


def _apply_rlvd_duration_rule(records: Sequence[core.VideoRecord], manifest: list[dict[str, Any]]) -> tuple[list[core.VideoRecord], int]:
    """Drop short RLVD clips while retaining other short clips for natural sampling."""
    manifest_by_uri = {item.get("source_uri", item.get("path")): item for item in manifest}
    kept: list[core.VideoRecord] = []
    short_tail_count = 0
    for record in records:
        row = manifest_by_uri.get(getattr(record, "source_uri", str(record.path)))
        if record.dataset == "Real Life Violence Dataset" and record.duration < WINDOW_SECONDS:
            if row is not None:
                row["eligible"] = 0
                row["exclusion_reason"] = "real_life_violence_shorter_than_4_seconds"
                row["short_tail_window_used"] = 0
            continue
        if row is not None:
            row["short_tail_window_used"] = int(record.duration < WINDOW_SECONDS)
        if record.duration < WINDOW_SECONDS:
            short_tail_count += 1
        kept.append(record)
    return kept, short_tail_count


def _install_protocol() -> None:
    global _reverse
    import mc3_18_reverse_concat_pipeline as reverse

    _reverse = reverse
    reverse.UCF_ROOT = UCF_ROOT
    reverse.LEGACY_DATASET_ROOT = LEGACY_DATASET_ROOT
    reverse.AIRTLAB_ROOT = AIRTLAB_ROOT
    reverse.GUARDAID_ROOT = GUARDAID_ROOT
    reverse.AIRTLAB_SPLIT_FOR_MODEL = True
    reverse._ACTIVE_SEED = _ACTIVE_SEED
    reverse._ORIGINAL_DISCOVER_DATASET_ROOTS = _selected_legacy_roots
    reverse.WINDOW_SECONDS = WINDOW_SECONDS
    reverse.STRIDE_SECONDS = STRIDE_SECONDS
    reverse.NUM_FRAMES = NUM_FRAMES
    reverse.MIN_TRAIN_SECONDS = WINDOW_SECONDS
    reverse.PROTOCOL = PROTOCOL
    reverse._install_core_protocol()

    # Restore the four-second protocol after the reverse runner installs its
    # own two-second globals; the training code then uses core's shared model.
    core.WINDOW_SECONDS = WINDOW_SECONDS
    core.STRIDE_SECONDS = STRIDE_SECONDS
    core.NUM_FRAMES = NUM_FRAMES
    core.CACHE_PROTOCOL = PROTOCOL
    core.CACHE_ENGINE_VERSION = "read-through-legacy-2s-cache-with-capped-supplement-v1"
    core.default_dataset_root = lambda: LEGACY_DATASET_ROOT
    core.default_cache_root = lambda: SUPPLEMENT_CACHE_ROOT
    core._preprocessing_protocol = _protocol
    core.discover_dataset_roots = reverse._discover_dataset_roots
    core._scan_datasets = _scan_four_second_datasets
    core._dataset_fingerprint = reverse._dataset_fingerprint
    core._windows_for_record = _windows_for_record
    core._prepare_cache = _prepare_hybrid_cache
    core._load_cache = _load_hybrid_cache
    core._count_split_samples = reverse._count_split_samples
    core._aggregate_metrics = reverse._aggregate_metrics
    core._per_dataset_metrics = reverse._per_dataset_metrics
    core._evaluate_split = reverse._evaluate_split


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ucf-root", default=str(DEFAULT_UCF_ROOT))
    parser.add_argument("--legacy-dataset-root", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--airtlab-root", default=str(DEFAULT_AIRTLAB_ROOT))
    parser.add_argument("--guardaid-root", default=str(DEFAULT_GUARDAID_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-dir")
    parser.add_argument("--cache-dir", default=str(DEFAULT_SUPPLEMENT_CACHE_ROOT), help="Four-second missing-frame supplement cache (hard-capped at 5 GiB).")
    parser.add_argument("--legacy-cache-dir", default=str(DEFAULT_LEGACY_CACHE_ROOT), help="Read-only existing two-second cache.")
    parser.add_argument("--resume")
    parser.add_argument("--pause-file")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--allow-cpu", action="store_true")
    parser.add_argument("--no-amp", action="store_true")
    parser.add_argument("--rebuild-cache", action="store_true")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--min-delta", type=float, default=0.001)
    parser.add_argument("--backbone-lr", type=float, default=2e-5)
    parser.add_argument("--head-lr", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--window-batch-size", type=int, default=2, help="Number of 16-frame windows per GPU batch; 2 is conservative for 8 GiB GPUs.")
    parser.add_argument("--log-every", type=int, default=25)
    parser.add_argument("--seed", type=int, default=2026)
    return parser


def _evaluate_guardaid_extra_test(run_dir: Path, args: argparse.Namespace) -> None:
    """Evaluate unlabeled GuardAId only after the regular test report is locked."""
    records = _EXTERNAL_GUARDAID_RECORDS
    report_path = run_dir / "final_report.json"
    if not report_path.is_file():
        return  # A pause/stop before finalization must not be marked complete.

    cv2, numpy_module, torch, nn, model_symbols = core._import_runtime_dependencies()
    weights_enum, model_builder = model_symbols
    del weights_enum
    device = torch.device("cuda:0" if args.device == "cuda" else "cpu")
    amp = device.type == "cuda" and not args.no_amp
    checkpoint_path = run_dir / "best_model.pt"
    if not checkpoint_path.is_file():
        raise FileNotFoundError(f"Best model checkpoint is missing: {checkpoint_path}")
    try:
        payload = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    except TypeError:
        payload = torch.load(checkpoint_path, map_location="cpu")
    expected_protocol = _protocol()
    saved_protocol = payload.get("protocol", {})
    mismatches = [key for key, value in expected_protocol.items() if saved_protocol.get(key) != value]
    if (
        payload.get("deployment_checkpoint_version") != 1
        or payload.get("model_name") != "mc3_18"
        or payload.get("class_to_idx") != core.CLASS_TO_LABEL
        or not isinstance(payload.get("state_dict"), dict)
        or mismatches
    ):
        raise ValueError("The selected best checkpoint does not match the current four-second protocol.")

    logger = core._log_setup(run_dir, resume=True)
    logger.info("Starting separate GuardAId extra test; this set is outside train/validation/test.")
    if not records:
        logger.info("No eligible GuardAId videos were found for external inference.")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["extra_test_metrics_by_dataset"] = {
            "GuardAId": {
                "ground_truth_available": False,
                "unlabeled_video_count": 0,
                "metrics_not_computed": "No GuardAId videos met the minimum duration for a four-second window.",
                "conditional_if_all_nonfight": {},
            }
        }
        _write_final_report(report_path, report)
        return

    model = core._build_model(torch, nn, None, model_builder, device, pretrained=False)
    model.load_state_dict(payload["state_dict"])
    model.eval()
    _prepare_hybrid_cache(records, SUPPLEMENT_CACHE_ROOT, cv2, numpy_module, logger, rebuild=False)
    started = time.perf_counter()
    predictions, windows = core._evaluate_split(
        records,
        model,
        split="external_test",
        epoch=int(saved_protocol.get("best_epoch", 0)),
        seed=int(args.seed),
        np=numpy_module,
        cv2=cv2,
        torch=torch,
        device=device,
        amp=amp,
        window_batch_size=int(args.window_batch_size),
        logger=logger,
    )
    elapsed = time.perf_counter() - started
    core._write_predictions(run_dir, "guardaid_external", predictions, windows)
    thresholds_payload = json.loads((run_dir / "thresholds.json").read_text(encoding="utf-8"))
    per_dataset = _reverse._per_dataset_metrics(predictions, thresholds_payload["selection"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["extra_test_metrics_by_dataset"] = per_dataset
    report["extra_test_summary"] = {
        "dataset": "GuardAId",
        "ground_truth_available": False,
        "video_count": len(predictions),
        "window_count": len(windows),
        "evaluation_seconds": elapsed,
        "predictions_file": "guardaid_external_predictions.csv",
        "window_predictions_file": "guardaid_external_window_predictions.csv",
        "note": "Separate unlabeled external test, excluded from train/validation/test and from threshold selection.",
    }
    _write_final_report(report_path, report)
    logger.info("GuardAId extra test complete | videos=%d | windows=%d | elapsed=%.1fs", len(predictions), len(windows), elapsed)


def _write_final_report(path: Path, report: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    global LEGACY_DATASET_ROOT, UCF_ROOT, AIRTLAB_ROOT, GUARDAID_ROOT
    global LEGACY_CACHE_ROOT, SUPPLEMENT_CACHE_ROOT, _ACTIVE_SEED
    parser = build_parser()
    options = parser.parse_args(argv)
    if options.epochs < 1 or options.patience < 1 or options.grad_accum < 1 or options.window_batch_size < 1 or options.log_every < 1:
        parser.error("epochs, patience, grad-accum, window-batch-size and log-every must be positive")
    if options.backbone_lr <= 0 or options.head_lr <= 0 or options.weight_decay < 0 or options.min_delta < 0:
        parser.error("learning rates must be positive; weight-decay and min-delta must be non-negative")
    LEGACY_DATASET_ROOT = Path(options.legacy_dataset_root).expanduser().resolve()
    UCF_ROOT = Path(options.ucf_root).expanduser().resolve()
    AIRTLAB_ROOT = Path(options.airtlab_root).expanduser().resolve()
    GUARDAID_ROOT = Path(options.guardaid_root).expanduser().resolve()
    LEGACY_CACHE_ROOT = Path(options.legacy_cache_dir).expanduser().resolve()
    SUPPLEMENT_CACHE_ROOT = Path(options.cache_dir).expanduser().resolve()
    _ACTIVE_SEED = int(options.seed)
    if not LEGACY_CACHE_ROOT.is_dir():
        raise FileNotFoundError(f"Read-only two-second cache not found: {LEGACY_CACHE_ROOT}")
    try:
        _validate_cache_roots(LEGACY_CACHE_ROOT, SUPPLEMENT_CACHE_ROOT)
    except ValueError as exc:
        parser.error(str(exc))
    _install_protocol()

    if options.device == "auto":
        try:
            import torch
        except Exception:
            options.device = "cuda"
        else:
            options.device = "cuda" if torch.cuda.is_available() else "cpu"
            if options.device == "cpu" and not options.allow_cpu:
                raise RuntimeError("No CUDA device was found. Use --device cpu --allow-cpu to confirm a slow CPU run.")
    if options.device == "cuda":
        try:
            import torch
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA was requested but PyTorch cannot see a CUDA device.")
        except ImportError as exc:
            raise RuntimeError("PyTorch is not installed in this Python environment.") from exc
    if options.device == "cpu" and not options.allow_cpu:
        raise RuntimeError("CPU training is disabled by default. Add --allow-cpu to confirm.")
    namespace = argparse.Namespace(
        dataset_root=str(LEGACY_DATASET_ROOT), output_root=options.output_root, run_dir=options.run_dir,
        cache_dir=str(SUPPLEMENT_CACHE_ROOT), resume=options.resume, pause_file=options.pause_file,
        device=options.device, allow_cpu=options.allow_cpu, no_amp=options.no_amp,
        rebuild_cache=options.rebuild_cache, epochs=options.epochs, patience=options.patience,
        min_delta=options.min_delta, backbone_lr=options.backbone_lr, head_lr=options.head_lr,
        weight_decay=options.weight_decay, grad_accum=options.grad_accum,
        window_batch_size=options.window_batch_size, log_every=options.log_every, seed=options.seed,
    )
    core._run_training(namespace)
    completed_run_dir = Path(namespace.run_dir).expanduser().resolve() if namespace.run_dir else Path(core._ACTIVE_RUN_DIR)
    _evaluate_guardaid_extra_test(completed_run_dir, namespace)
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
