"""Fresh MC3-18 Kinetics-400 training with DanceTrack and ±2-frame MIL views.

This profile preserves the audited Harvard/RWF-2000/AIRTLab split policy,
adds DanceTrack's official train/val/test shards as candidate negatives, and
uses a distinct frame cache for the five deterministic training offsets.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Sequence

import cv2 as _cv2
import mc3_18_four_second_pipeline as four
import mc3_18_harvard_letterbox_pipeline as letterbox
import mc3_18_mil_pipeline as core
import mc3_18_native_finetune_pipeline as native


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = letterbox.DATA_ROOT
BASE_MANIFEST = letterbox.BASE_MANIFEST
OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_temporal_shift_letterbox_4dataset_4s16f2_k400"
# Keep this cache directory component short: with the full SHA-256 cache
# filename, the previous descriptive component pushed Windows destinations
# past MAX_PATH and os.replace raised WinError 3.
CACHE_PROTOCOL = "ts160-rgbmean-five-offsets-v1"
EXPERIMENT_PROTOCOL = "mc3-harvard-rwf-airtlab-dancetrack-letterbox160-4s16f2-temporal-jitter-k400-v1"
TEMPORAL_CACHE_BASE_TARGET_GIB = 16.97
TEMPORAL_CACHE_ADDITIONAL_TARGET_GIB = 14.82
TEMPORAL_CACHE_TARGET_BYTES = int(
    (TEMPORAL_CACHE_BASE_TARGET_GIB + TEMPORAL_CACHE_ADDITIONAL_TARGET_GIB) * 1024**3
)
TEMPORAL_CACHE_MIN_FREE_BYTES = 3 * 1024**3
INPUT_REPRESENTATION = letterbox.INPUT_REPRESENTATION
INPUT_SIZE = letterbox.INPUT_SIZE
DATASET_NAMES = (*letterbox.DATASET_NAMES, "DanceTrack")
SPLITS = ("train", "val", "test")
TEMPORAL_OFFSETS = (-2, -1, 0, 1, 2)
OFFSET_POLICY = "Apply one fixed signed source-frame-index offset to all 16 samples in each training window; evaluation uses offset 0 only. Clamp indices at source-video edges."
DANCETRACK_SHARDS = {"train1": "train", "train2": "train", "val": "val",
                     "test1": "test", "test2": "test"}
EXPECTED_DANCETRACK_SHARDS = {"train1": 20, "train2": 20, "val": 25, "test1": 20, "test2": 15}
MEAN_RGB = tuple(float(value) for value in core.MEAN)


def experiment_protocol() -> dict[str, Any]:
    protocol = dict(letterbox.experiment_protocol())
    protocol.update({
        "protocol": EXPERIMENT_PROTOCOL,
        "experiment_protocol": EXPERIMENT_PROTOCOL,
        "input_representation": INPUT_REPRESENTATION,
        "input_size": [INPUT_SIZE, INPUT_SIZE],
        "padding": "resize the complete frame to fit within 160x160 without changing aspect ratio; center-pad with the RGB normalization mean",
        "padding_rgb_0_1": list(MEAN_RGB),
        "selected_datasets": list(DATASET_NAMES),
        "dancetrack_split": "official DanceTrack shards: train1+train2=train, val=val, test1+test2=test",
        "dancetrack_label_policy": "DanceTrack sequences are assigned candidate NonFight labels for this experiment; this is not a claim about official violence annotations.",
        "temporal_augmentation": {
            "unit": "original source-video frame index",
            "training_offsets": list(TEMPORAL_OFFSETS),
            "variants_per_window": len(TEMPORAL_OFFSETS),
            "application": "same offset applied to every one of the 16 frame indices in the 4-second window",
            "evaluation_offsets": [0],
            "edge_policy": "clamp shifted indices to the valid source-frame range; repeated edge frames are allowed only when a shift crosses a video boundary",
            "dance_track_frame_rate": "use the DanceTrack seqinfo.ini frameRate; the offset remains an integer source-frame step",
        },
        "cache_policy": {
            "protocol": CACHE_PROTOCOL,
            "write_root": "a new profile-specific cache folder; never modify or delete prior experiment caches",
            "training_frames": "unique sampled source indices required by offsets -2,-1,0,+1,+2",
            "evaluation_frames": "unshifted source indices only",
            "cache_miss": "decode from source on demand; report this in the live log",
        },
        "tail_policy": "full 4-second windows at 2-second stride, with one end-aligned tail when needed; clips shorter than 4 seconds use 16 samples uniformly spaced over their natural duration",
        "split_policy": "Harvard Dataverse class-stratified fixed-seed 80:10:10; RWF-2000 and AIRTLab exact eligible assignments from the audited 4S16F2 manifest; AIRT Lab paired camera views remain together; DanceTrack official shard mapping",
    })
    return protocol


def shifted_indices(frame_indices: Sequence[int], offset: int, frame_count: int) -> tuple[int, ...]:
    """Shift an entire sampled window by one source-frame offset consistently."""
    if frame_count <= 0:
        raise ValueError("frame_count must be positive")
    if offset not in TEMPORAL_OFFSETS:
        raise ValueError(f"Unsupported source-frame offset {offset}; expected one of {TEMPORAL_OFFSETS}.")
    return tuple(max(0, min(frame_count - 1, int(index) + offset)) for index in frame_indices)


def temporal_cache_budget_bytes(free_bytes: int, existing_cache_bytes: int) -> int:
    """Apply this profile's cache cap while retaining the 3 GiB free-space reserve."""
    total_cap = TEMPORAL_CACHE_TARGET_BYTES
    existing = max(0, int(existing_cache_bytes))
    available_for_new_cache = max(0, int(free_bytes) - TEMPORAL_CACHE_MIN_FREE_BYTES)
    return min(total_cap, existing + available_for_new_cache)


def window_variants(window: core.Window, *, frame_count: int, training: bool) -> list[tuple[int, tuple[int, ...]]]:
    offsets = TEMPORAL_OFFSETS if training else (0,)
    return [(offset, shifted_indices(window.frame_indices, offset, frame_count)) for offset in offsets]


def required_frame_indices(record: core.VideoRecord, *, training: bool | None = None) -> tuple[int, ...]:
    is_training = record.split == "train" if training is None else bool(training)
    indices = {index for window in four._windows_for_record(record, training=is_training)
               for _, variant in window_variants(window, frame_count=record.frame_count,
                                                 training=is_training)
               for index in variant}
    if not indices:
        raise RuntimeError(f"No source frames are required for {record.path}.")
    return tuple(sorted(indices))


def _parse_seqinfo(sequence_path: Path) -> tuple[float, int]:
    seqinfo = sequence_path / "seqinfo.ini"
    if not seqinfo.is_file():
        raise FileNotFoundError(f"DanceTrack seqinfo.ini is missing: {seqinfo}")
    values: dict[str, str] = {}
    for raw_line in seqinfo.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().casefold()] = value.strip()
    try:
        fps = float(values["framerate"])
        frame_count = int(values["seqlength"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"DanceTrack seqinfo.ini must provide numeric frameRate and seqLength: {seqinfo}") from exc
    if not math.isfinite(fps) or fps <= 0 or frame_count <= 0:
        raise ValueError(f"DanceTrack seqinfo.ini has invalid frameRate/seqLength: {seqinfo}")
    return fps, frame_count


def _dance_frame_path(record: core.VideoRecord, frame_index: int) -> Path:
    return record.path / "img1" / f"{frame_index + 1:08d}.jpg"


def _attach_temporal_metadata(record: core.VideoRecord) -> None:
    indices = required_frame_indices(record)
    record.temporal_frame_indices = indices
    record.required_frame_indices = indices


def _summarize_records(records: Sequence[core.VideoRecord]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "datasets": {},
        "overall": {split: {"Fight": 0, "NonFight": 0, "bags": 0, "windows": 0}
                    for split in SPLITS},
    }
    for dataset in DATASET_NAMES:
        per_split = {split: {"Fight": 0, "NonFight": 0, "bags": 0, "windows": 0}
                     for split in SPLITS}
        for record in records:
            if record.dataset != dataset or record.split not in SPLITS:
                continue
            per_split[record.split][record.class_name] += 1
            per_split[record.split]["bags"] += 1
            per_split[record.split]["windows"] += len(four._windows_for_record(record, training=False))
        per_split["total_bags"] = sum(per_split[split]["bags"] for split in SPLITS)
        summary["datasets"][dataset] = per_split
        for split in SPLITS:
            for key in summary["overall"][split]:
                summary["overall"][split][key] += per_split[split][key]
    summary["total_bags"] = sum(summary["overall"][split]["bags"] for split in SPLITS)
    return summary


def load_records(*, dataset_root: Path = DATA_ROOT, base_manifest: Path = BASE_MANIFEST,
                 seed: int = 2026, cv2: Any | None = None, logger: logging.Logger | None = None,
                 validate_dance_frames: bool = False, progress_callback=None):
    """Reuse the three audited datasets, then add DanceTrack's official shards."""
    cv2 = cv2 or _cv2
    dataset_root = Path(dataset_root).expanduser().resolve()
    records, manifest_rows, metadata = letterbox.load_records(
        dataset_root=dataset_root, base_manifest=Path(base_manifest), seed=seed, cv2=cv2,
        logger=logger, progress_callback=progress_callback)
    dance_root = dataset_root / "DanceTrack"
    missing: list[str] = []
    sequences_seen: dict[str, int] = {name: 0 for name in DANCETRACK_SHARDS}
    for shard, split in DANCETRACK_SHARDS.items():
        shard_root = dance_root / shard
        if not shard_root.is_dir():
            raise FileNotFoundError(f"DanceTrack official split folder is missing: {shard_root}")
        sequences = sorted((path for path in shard_root.iterdir() if path.is_dir()),
                           key=lambda path: path.name.casefold())
        if len(sequences) != EXPECTED_DANCETRACK_SHARDS[shard]:
            raise RuntimeError(f"DanceTrack {shard} sequence count changed: {len(sequences)}; expected {EXPECTED_DANCETRACK_SHARDS[shard]}.")
        sequences_seen[shard] = len(sequences)
        for sequence_path in sequences:
            fps, frame_count = _parse_seqinfo(sequence_path)
            duration = frame_count / fps
            record = native._new_record("DanceTrack", split, "NonFight", sequence_path,
                                        fps, frame_count, duration,
                                        source_group=f"DanceTrack|{sequence_path.name.casefold()}")
            record.dancetrack_shard = shard
            record.dancetrack_root = dance_root
            _attach_temporal_metadata(record)
            signature_parts: list[str] = []
            total_bytes = 0
            for frame_index in record.temporal_frame_indices:
                frame_path = _dance_frame_path(record, frame_index)
                if not frame_path.is_file():
                    missing.append(str(frame_path))
                    continue
                stat = frame_path.stat()
                total_bytes += stat.st_size
                signature_parts.append(f"{frame_index}:{stat.st_size}:{stat.st_mtime_ns}")
            record.size_bytes = total_bytes
            record.mtime_ns = int(hashlib.sha256("|".join(signature_parts).encode("utf-8")).hexdigest()[:15], 16)
            if validate_dance_frames and any(not _dance_frame_path(record, index).is_file()
                                             for index in record.temporal_frame_indices):
                absent = next(_dance_frame_path(record, index) for index in record.temporal_frame_indices
                              if not _dance_frame_path(record, index).is_file())
                raise FileNotFoundError(f"DanceTrack frame required by 4S16F2 and temporal offsets is missing: {absent}")
            records.append(record)
            manifest_rows.append({
                "dataset": "DanceTrack", "split": split, "class_name": "NonFight", "label": 0,
                "path": str(sequence_path.resolve()), "source_group": record.source_group,
                "fps": f"{fps:.8f}", "frame_count": frame_count,
                "duration_seconds": f"{duration:.8f}", "eligible": 1,
                "exclusion_reason": "", "size_bytes": total_bytes,
                "split_source": f"official_{shard}_mapping", "temporal_source_frame_offsets": ";".join(map(str, TEMPORAL_OFFSETS if split == "train" else (0,))),
            })
    if missing:
        raise FileNotFoundError(f"DanceTrack is missing {len(missing)} required sampled/shifted frames; first missing: {missing[0]}")
    for record in records:
        if record.dataset != "DanceTrack":
            _attach_temporal_metadata(record)
    metadata = {
        **metadata,
        "summary": _summarize_records(records),
        "dancetrack_root": str(dance_root.resolve()),
        "dancetrack_shards": sequences_seen,
        "dancetrack_split_policy": "official train1+train2/train, val/val, test1+test2/test",
        "temporal_offsets_source_frames": list(TEMPORAL_OFFSETS),
        "temporal_offsets_apply_to_training_split_only": True,
        "split_policy": metadata.get("split_policy", "") + "; DanceTrack official shards train1+train2/train, val/val, test1+test2/test",
    }
    return records, manifest_rows, metadata


def quick_inventory(*, dataset_root: Path = DATA_ROOT, base_manifest: Path = BASE_MANIFEST,
                    seed: int = 2026) -> dict[str, Any]:
    base_inventory = letterbox.quick_inventory(dataset_root=dataset_root, base_manifest=base_manifest, seed=seed)
    dance_root = Path(dataset_root).expanduser().resolve() / "DanceTrack"
    missing_dance_frames: list[str] = []
    shard_counts = {name: 0 for name in DANCETRACK_SHARDS}
    dance_split = {split: {"Fight": 0, "NonFight": 0, "bags": 0} for split in SPLITS}
    for shard, split in DANCETRACK_SHARDS.items():
        shard_root = dance_root / shard
        if not shard_root.is_dir():
            missing_dance_frames.append(str(shard_root))
            continue
        sequences = sorted((path for path in shard_root.iterdir() if path.is_dir()), key=lambda path: path.name.casefold())
        shard_counts[shard] = len(sequences)
        if len(sequences) != EXPECTED_DANCETRACK_SHARDS[shard]:
            missing_dance_frames.append(f"{shard}: expected {EXPECTED_DANCETRACK_SHARDS[shard]} sequences, found {len(sequences)}")
        for sequence_path in sequences:
            try:
                fps, frame_count = _parse_seqinfo(sequence_path)
                record = native._new_record("DanceTrack", split, "NonFight", sequence_path,
                                            fps, frame_count, frame_count / fps,
                                            source_group=f"DanceTrack|{sequence_path.name.casefold()}")
                _attach_temporal_metadata(record)
                dance_split[split]["NonFight"] += 1
                dance_split[split]["bags"] += 1
                for frame_index in record.temporal_frame_indices:
                    frame_path = _dance_frame_path(record, frame_index)
                    if not frame_path.is_file():
                        missing_dance_frames.append(str(frame_path))
                        if len(missing_dance_frames) >= 5:
                            break
            except (OSError, ValueError, RuntimeError) as exc:
                missing_dance_frames.append(f"{sequence_path}: {exc}")
            if len(missing_dance_frames) >= 5:
                break
        if len(missing_dance_frames) >= 5:
            break
    source_summary = base_inventory.get("summary", {})
    summary = {"datasets": dict(source_summary.get("datasets", {})),
               "overall": dict(source_summary.get("overall", {}))}
    for split in SPLITS:
        summary["overall"].setdefault(split, {"Fight": 0, "NonFight": 0, "bags": 0})
        summary["overall"][split].pop("windows", None)
        for key, value in dance_split[split].items():
            summary["overall"][split][key] = int(summary["overall"][split].get(key, 0)) + value
        for dataset_values in summary["datasets"].values():
            if split in dataset_values:
                dataset_values[split].pop("windows", None)
    dance_split["total_bags"] = sum(dance_split[split]["bags"] for split in SPLITS)
    summary["datasets"]["DanceTrack"] = dance_split
    summary["total_bags"] = sum(int(summary["overall"][split].get("bags", 0)) for split in SPLITS)
    source_missing = int(base_inventory.get("missing_source_count", base_inventory.get("missing_source_count_sampled", 0)) or 0)
    bad_shards = any(shard_counts.get(shard) != count for shard, count in EXPECTED_DANCETRACK_SHARDS.items())
    missing_total = source_missing + len(missing_dance_frames)
    return {
        "ready": bool(base_inventory.get("ready")) and not missing_dance_frames and not bad_shards,
        "protocol": EXPERIMENT_PROTOCOL,
        "selected_datasets": list(DATASET_NAMES),
        "summary": summary,
        "output_root": str(OUTPUT_ROOT.resolve()),
        "cache_dir": str((OUTPUT_ROOT / "_frame_cache" / CACHE_PROTOCOL).resolve()),
        "base_manifest": str(Path(base_manifest).resolve()),
        "base_manifest_sha256": base_inventory.get("base_manifest_sha256"),
        "split_policy": (str(base_inventory.get("split_policy", ""))
                         + "; DanceTrack official train1+train2/train, val/val, test1+test2/test"),
        "seed": seed,
        "initialization": letterbox.KINETICS_INITIALIZATION,
        "missing_source_count": missing_total,
        "missing_dancetrack_frame_count": len(missing_dance_frames),
        "first_missing_sources": (base_inventory.get("first_missing_sources", []) + missing_dance_frames[:5])[:5],
        "dancetrack_shards": shard_counts,
        "temporal_offsets": list(TEMPORAL_OFFSETS),
        "temporal_offset_unit": "原始视频帧索引；每窗16帧整体偏移，val/test只用0",
        "cache_note": "独立缓存；训练使用五个偏移视图，验证/测试使用未偏移帧；不修改旧缓存",
    }


def _decode_video_frames(record: core.VideoRecord, indices: Sequence[int], cv2: Any) -> Any:
    import numpy as np

    wanted = tuple(sorted(set(int(index) for index in indices)))
    if not wanted:
        raise ValueError(f"No source indices requested for {record.path}")
    capture = cv2.VideoCapture(str(record.path))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"Cannot decode source video for temporal cache: {record.path}")
    selected: dict[int, Any] = {}
    wanted_set = set(wanted)
    current = 0
    try:
        while current <= wanted[-1]:
            ok, bgr = capture.read()
            if not ok:
                break
            if current in wanted_set:
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                selected[current] = core._resize_short_side(rgb, cv2)
            current += 1
    finally:
        capture.release()
    absent = [index for index in wanted if index not in selected]
    if absent:
        raise RuntimeError(f"Source video ended before required shifted frame {absent[0]}: {record.path}")
    return np.stack([selected[index] for index in wanted]).astype(np.uint8, copy=False)


def _decode_dancetrack_frames(record: core.VideoRecord, indices: Sequence[int], cv2: Any) -> Any:
    import numpy as np

    images = []
    for index in indices:
        path = _dance_frame_path(record, int(index))
        bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if bgr is None:
            raise RuntimeError(f"OpenCV cannot read required DanceTrack offset frame: {path}")
        images.append(core._resize_short_side(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), cv2))
    return np.stack(images).astype(np.uint8, copy=False)


def prepare_temporal_cache(records: Sequence[core.VideoRecord], cache_dir: Path, cv2: Any, np: Any,
                           logger: logging.Logger, update_status, *, cache_budget_bytes: int) -> None:
    """Build/reuse an isolated, hash-addressed cache without touching old caches."""
    root = Path(cache_dir) / "temporal_shift_frames"
    root.mkdir(parents=True, exist_ok=True)
    used_bytes = sum(path.stat().st_size for path in root.glob("*.npz") if path.is_file())
    started = time.monotonic()
    reused = created = on_demand = 0
    frame_total = 0
    for index, record in enumerate(records, start=1):
        indices = required_frame_indices(record)
        record.temporal_frame_indices = indices
        record.required_frame_indices = indices
        if record.dataset == "DanceTrack":
            signatures = []
            for frame_index in indices:
                path = _dance_frame_path(record, frame_index)
                if not path.is_file():
                    raise FileNotFoundError(f"DanceTrack offset frame is not present: {path}")
                stat = path.stat()
                signatures.append(f"{frame_index}:{stat.st_size}:{stat.st_mtime_ns}")
        else:
            signatures = [f"{record.path.stat().st_size}:{record.path.stat().st_mtime_ns}"]
        key_payload = f"{CACHE_PROTOCOL}|{record.dataset}|{record.split}|{record.path}|{record.fps:.8f}|{record.frame_count}|{','.join(map(str, indices))}|{'|'.join(signatures)}"
        cache_path = root / f"{hashlib.sha256(key_payload.encode('utf-8')).hexdigest()}.npz"
        record.temporal_cache_path = cache_path
        valid = False
        if cache_path.is_file():
            try:
                with np.load(cache_path, allow_pickle=False) as saved:
                    saved_indices = saved["frame_indices"].astype(np.int64)
                    frames = saved["frames"]
                    valid = (saved_indices.tolist() == list(indices) and frames.ndim == 4
                             and len(frames) == len(indices) and frames.shape[-1] == 3
                             and frames.dtype == np.uint8)
            except (OSError, ValueError, KeyError):
                valid = False
        if valid:
            reused += 1
            frame_total += len(indices)
        elif used_bytes >= cache_budget_bytes:
            record.temporal_cache_path = None
            on_demand += 1
        else:
            frames = (_decode_dancetrack_frames(record, indices, cv2) if record.dataset == "DanceTrack"
                      else _decode_video_frames(record, indices, cv2))
            temporary = four._cache_write_temp_path(cache_path)
            try:
                with temporary.open("wb") as handle:
                    np.savez_compressed(handle, frame_indices=np.asarray(indices, dtype=np.int32), frames=frames)
                written_size = temporary.stat().st_size
                if used_bytes + written_size <= cache_budget_bytes:
                    os.replace(temporary, cache_path)
                    used_bytes += written_size
                    created += 1
                    frame_total += len(indices)
                else:
                    record.temporal_cache_path = None
                    on_demand += 1
            finally:
                temporary.unlink(missing_ok=True)
        if index == 1 or index % 10 == 0 or index == len(records):
            elapsed = time.monotonic() - started
            eta = elapsed / index * (len(records) - index)
            logger.info("Temporal-shift cache %d/%d | reused=%d created=%d on-demand=%d | frames=%d | disk=%.2f/%.2f GiB | elapsed=%.1fs | ETA=%.1fs",
                        index, len(records), reused, created, on_demand, frame_total,
                        used_bytes / (1024**3), cache_budget_bytes / (1024**3), elapsed, eta)
            update_status(stage="准备±2源帧偏移缓存", step=index, steps_total=len(records),
                          stage_progress=index / max(1, len(records)), eta_seconds=eta,
                          cache_reused=reused, cache_created=created, cache_on_demand=on_demand,
                          cached_source_frames=frame_total)
    logger.info("Temporal-shift cache ready | records=%d reused=%d created=%d on-demand=%d frames=%d bytes=%d/%d root=%s",
                len(records), reused, created, on_demand, frame_total, used_bytes, cache_budget_bytes, root)


def _load_temporal_cache(record: core.VideoRecord, np: Any, cv2: Any) -> tuple[Any, dict[int, int]]:
    indices = tuple(getattr(record, "temporal_frame_indices", required_frame_indices(record)))
    path = getattr(record, "temporal_cache_path", None)
    if path is not None and Path(path).is_file():
        try:
            with np.load(path, allow_pickle=False) as saved:
                frame_indices = saved["frame_indices"].astype(np.int64)
                frames = saved["frames"].copy()
            if frame_indices.tolist() == list(indices) and frames.ndim == 4 and len(frames) == len(indices):
                return frames, {int(value): i for i, value in enumerate(frame_indices.tolist())}
        except (OSError, ValueError, KeyError):
            pass
    frames = (_decode_dancetrack_frames(record, indices, cv2) if record.dataset == "DanceTrack"
              else _decode_video_frames(record, indices, cv2))
    return frames, {int(value): i for i, value in enumerate(indices)}


def record_bag_tensor(record: core.VideoRecord, *, training: bool, epoch: int, seed: int,
                      np: Any, cv2: Any, torch: Any):
    frames, index_map = _load_temporal_cache(record, np, cv2)
    windows = four._windows_for_record(record, training=training)
    if not windows:
        raise RuntimeError(f"No 4S16F2 windows were generated for {record.path}.")
    training_offsets = training and record.split == "train"
    variants_per_window = len(TEMPORAL_OFFSETS) if training_offsets else 1
    rng = np.random.default_rng(core.stable_seed(seed, epoch, record.identity))
    clips = []
    for window in windows:
        for offset, frame_indices in window_variants(window, frame_count=record.frame_count,
                                                     training=training_offsets):
            missing = [value for value in frame_indices if value not in index_map]
            if missing:
                raise RuntimeError(f"Temporal cache lacks source frame {missing[0]} for offset {offset:+d}: {record.path}")
            sampled = [frames[index_map[int(value)]] for value in frame_indices]
            clips.append(letterbox.frames_to_clip(
                sampled, np=np, torch=torch, cv2=cv2, training=training,
                seed=int(rng.integers(0, 2**32 - 1))))
    if training_offsets and len(clips) != len(windows) * variants_per_window:
        raise RuntimeError("Five-view temporal training expansion did not produce exactly five views per window.")
    return torch.stack(clips, dim=0), windows


def configure_runtime() -> None:
    native.PROTOCOL_ID = EXPERIMENT_PROTOCOL
    native.INPUT_REPRESENTATION = INPUT_REPRESENTATION
    native.INPUT_PROTOCOL_ID = EXPERIMENT_PROTOCOL
    native.DATASET_NAMES = DATASET_NAMES
    native.load_records = load_records
    native._experiment_protocol = experiment_protocol
    native._prepare_video_cache = prepare_temporal_cache
    native.CACHE_REUSE_METADATA = {
        "strategy": "isolated hash-addressed source-frame cache for temporal variants",
        "path_suffix": "temporal_shift_frames",
        "old_profile_caches_modified": False,
        "train_offsets": list(TEMPORAL_OFFSETS),
        "validation_test_offsets": [0],
    }
    original_install = native._install_runtime_protocol
    original_parser = native.build_parser

    def build_parser():
        return _build_temporal_profile_parser(original_parser)

    native.build_parser = build_parser

    def install(cache_root: Path) -> None:
        original_install(cache_root)
        core.SHORT_SIDE = 128
        core.CROP_SIZE = INPUT_SIZE
        core._record_bag_tensor = record_bag_tensor

        temporal_cache_root = Path(cache_root) / "temporal_shift_frames"

        def cache_budget_from_free_space(free_bytes: int) -> int:
            existing_bytes = sum(path.stat().st_size for path in temporal_cache_root.glob("*.npz")
                                 if path.is_file())
            return temporal_cache_budget_bytes(free_bytes, existing_bytes)

        native._video_cache_budget_from_free_bytes = cache_budget_from_free_space

    native._install_runtime_protocol = install


def _build_temporal_profile_parser(original_parser):
    """Apply this profile's defaults, including its directory-based DanceTrack split."""
    parser = original_parser()
    parser.set_defaults(epochs=20, patience=4, lr_reduce_after=2, dancetrack_manifest="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_runtime()
    return native.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
