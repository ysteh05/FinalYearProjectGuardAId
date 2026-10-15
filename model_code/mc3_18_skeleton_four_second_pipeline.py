"""Train the four-source black-background skeleton MC3-18 model.

The runner consumes the already extracted RTMPose skeleton PNGs and their
4s16f2 manifests. RWF-2000, RLVD and ShanghaiTech keep their audited split;
AIRTLab is split 80/10/10 by paired event group (seed 2026).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import os
import random
import shutil
import sys
import time
import traceback
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import mc3_18_airtlab_modalities_experiment as air
import mc3_18_four_second_pipeline as four
import mc3_18_mil_pipeline as core


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "GuardAId Video Classification Model Dataset"
DEFAULT_SKELETON_ROOT = DATA_ROOT / "RTMPose_M_Skeleton_MC3_4s2s_16frames"
DEFAULT_AIRTLAB_ROOT = DATA_ROOT / "AIRTLab" / "RTMPose_M_Skeleton_MC3_4s2s_16frames"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_multidataset_skeleton_4s16f2"
PROTOCOL_ID = "mc3-multidataset-black-skeleton-4s2s16f-v1"
EPOCHS = 15
PATIENCE = 4
LR_SCHEDULER_FACTOR = 0.5
LR_SCHEDULER_NO_IMPROVEMENT_EPOCHS = 2
# PyTorch reduces after num_bad_epochs > patience, so 1 means exactly
# two consecutive validation epochs without AUC improvement before halving.
LR_SCHEDULER_PATIENCE = LR_SCHEDULER_NO_IMPROVEMENT_EPOCHS - 1
MIN_DELTA = 0.001
SEED = 2026
SPLIT_RATIOS = {"train": 0.80, "val": 0.10, "test": 0.10}
SOURCE_LAYOUT = {
    "RWF-2000": (Path("RWF-2000"), DATA_ROOT / "RWF-2000"),
    "Real Life Violence Dataset": (Path("Real Life Violence Dataset"), DATA_ROOT / "archive" / "Real Life Violence Dataset"),
    "ShanghaiTech": (Path("ShanghaiTech"), DATA_ROOT / "ShanghaiTech"),
}


@dataclass
class SkeletonSample:
    record: core.VideoRecord
    source_relative: str
    group_id: str
    camera: str
    source_sha256: str
    window_rows: list[dict[str, Any]]
    frame_paths_by_index: dict[int, Path]
    frame_stat_signature: str = ""


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _temporary_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


def _replace_with_retry(source: Path, destination: Path, *, attempts: int = 8) -> None:
    """Retry transient Windows sharing/AV locks without weakening atomic replacement."""
    delay = 0.05
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt + 1 == attempts:
                raise
            time.sleep(min(delay, 0.5))
            delay *= 2


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        _replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _normal_relative(value: str) -> str:
    return Path(value.replace("\\", "/")).as_posix()


def _load_manifest_dataset(
    name: str, pose_root: Path, source_root: Path, *, verify_frame_files: bool = False,
) -> list[SkeletonSample]:
    dataset_root = pose_root / name
    video_path = dataset_root / "video_manifest.csv"
    window_path = dataset_root / "window_manifest.csv"
    protocol_path = pose_root / "extraction_summary.json"
    if not video_path.is_file() or not window_path.is_file() or not protocol_path.is_file():
        raise FileNotFoundError(f"{name}: missing completed skeleton manifests under {dataset_root}")
    videos = _read_csv(video_path)
    windows = _read_csv(window_path)
    windows_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in windows:
        source_relative = _normal_relative(raw["source_relative"])
        try:
            indices = [int(value) for value in json.loads(raw["source_frame_indices"])]
            frame_paths = [str(value) for value in json.loads(raw["frame_paths"])]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"{name}: invalid window frame mapping for {raw.get('sample_id')}") from exc
        if len(indices) != 16 or len(frame_paths) != 16:
            raise ValueError(f"{name}: window {raw.get('sample_id')} does not contain exactly 16 frames")
        windows_by_source[source_relative].append({**raw, "source_relative": source_relative,
                                                   "indices": indices, "frame_paths_list": frame_paths})

    samples: list[SkeletonSample] = []
    for video_index, row in enumerate(videos, start=1):
        source_relative = _normal_relative(row["source_relative"])
        if row.get("status") != "completed":
            raise RuntimeError(f"{name}: extraction is not complete for {source_relative}")
        label_name = row.get("class_name", "")
        label = int(row.get("label", "-1"))
        if label_name not in core.CLASS_TO_LABEL or core.CLASS_TO_LABEL[label_name] != label:
            raise ValueError(f"{name}: unexpected label for {source_relative}")
        try:
            fps = float(row["fps"])
            frame_count = int(row["source_frame_count"])
            duration = four.duration_from_frame_count(fps, frame_count, row.get("duration_seconds"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{name}: invalid video metadata for {source_relative}") from exc
        source_relative_path = Path(source_relative)
        if source_relative_path.is_absolute() or ".." in source_relative_path.parts:
            raise ValueError(f"{name}: source path escapes its dataset root: {source_relative}")
        source_path = source_root / source_relative_path
        source_bytes = int(row.get("source_bytes", 0))
        expected = four.make_four_second_windows(duration, fps, frame_count)
        actual = sorted(windows_by_source.get(source_relative, []), key=lambda item: int(item["window_index"]))
        if len(expected) != len(actual) or not actual:
            raise ValueError(f"{name}: 4s16f2 window count mismatch for {source_relative}")
        frame_paths_by_index: dict[int, Path] = {}
        for index, (expected_window, actual_window) in enumerate(zip(expected, actual)):
            if tuple(actual_window["indices"]) != expected_window.frame_indices:
                raise ValueError(f"{name}: sampled frame indices differ for {source_relative}, window {index}")
            if actual_window["split"] != row["split"] or actual_window["class_name"] != label_name:
                raise ValueError(f"{name}: window labels/split disagree with the video manifest: {source_relative}")
            for source_index, relative_frame in zip(actual_window["indices"], actual_window["frame_paths_list"]):
                relative_frame_path = Path(relative_frame)
                if relative_frame_path.is_absolute() or ".." in relative_frame_path.parts:
                    raise ValueError(f"{name}: frame path escapes its skeleton dataset folder")
                frame_path = dataset_root / relative_frame_path
                previous = frame_paths_by_index.get(source_index)
                if previous is None:
                    if verify_frame_files and not frame_path.is_file():
                        raise FileNotFoundError(f"{name}: skeleton image is missing: {frame_path}")
                    frame_paths_by_index[source_index] = frame_path
                elif previous != frame_path:
                    raise ValueError(f"{name}: conflicting skeleton frame mapping for {source_relative}")
        record = core.VideoRecord(
            dataset=name, split=row["split"], class_name=label_name, label=label,
            path=source_path, fps=fps, frame_count=frame_count, duration=duration,
            size_bytes=source_bytes, mtime_ns=0,
        )
        samples.append(SkeletonSample(record, source_relative, source_relative, "", row.get("source_sha256", ""), actual, frame_paths_by_index))
        if video_index == 1 or video_index % 100 == 0 or video_index == len(videos):
            print(f"[preflight] {name}: validated {video_index}/{len(videos)} videos", flush=True)
    return samples


def load_samples(
    pose_root: Path = DEFAULT_SKELETON_ROOT,
    airtlab_root: Path = DEFAULT_AIRTLAB_ROOT,
    *, verify_frame_files: bool = False,
) -> tuple[list[SkeletonSample], dict[str, Any]]:
    pose_root, airtlab_root = pose_root.resolve(), airtlab_root.resolve()
    summary_path = pose_root / "extraction_summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"Three-source skeleton extraction summary is missing: {summary_path}")
    extraction_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not extraction_summary.get("complete"):
        raise RuntimeError("The RWF-2000/RLVD/ShanghaiTech skeleton extraction is not marked complete.")

    samples: list[SkeletonSample] = []
    for name, (pose_name, source_root) in SOURCE_LAYOUT.items():
        print(f"[preflight] Starting {name} manifest/window/split checks", flush=True)
        samples.extend(_load_manifest_dataset(name, pose_root, source_root, verify_frame_files=verify_frame_files))

    # The paired camera views are assigned by the shared source/event group.
    print("[preflight] Starting AIRTLab paired-camera manifest and split checks", flush=True)
    air.SPLIT_RATIOS = dict(SPLIT_RATIOS)
    air_samples, air_protocol = air.load_dataset_manifest(verify_source_hashes=False,
                                                          verify_skeleton_files=verify_frame_files,
                                                          verify_source_files=False)
    samples.extend(SkeletonSample(item.record, item.source_relative, item.group_id, item.camera,
                                  item.source_sha256, _normalise_air_windows(item.window_rows), item.frame_paths_by_index)
                   for item in air_samples)
    print(f"[preflight] AIRTLab: validated {len(air_samples)}/350 videos; paired cameras remain grouped", flush=True)
    if len(air_samples) != 350:
        raise RuntimeError(f"AIRTLab skeleton manifest should contain 350 videos, found {len(air_samples)}")

    for sample in samples:
        if sample.record.split not in {"train", "val", "test"}:
            raise ValueError(f"Unexpected split {sample.record.split!r} for {sample.record.path}")
        if not sample.frame_paths_by_index or not sample.window_rows:
            raise ValueError(f"No 4s16f2 skeleton windows for {sample.record.path}")
    if len({sample.record.label for sample in samples if sample.record.split == "train"}) != 2:
        raise RuntimeError("Combined training split must contain Fight and NonFight videos.")
    if len({sample.record.label for sample in samples if sample.record.split == "val"}) != 2:
        raise RuntimeError("Combined validation split must contain Fight and NonFight videos.")
    if len({sample.record.label for sample in samples if sample.record.split == "test"}) != 2:
        raise RuntimeError("Combined test split must contain Fight and NonFight videos.")
    metadata = {
        "protocol": PROTOCOL_ID,
        "skeleton_root": str(pose_root),
        "airtlab_skeleton_root": str(air.SKELETON_ROOT.resolve()),
        "source_extraction_summary": extraction_summary,
        "airtlab_protocol": air_protocol,
        "airtlab_split_ratios": SPLIT_RATIOS,
        "airtlab_split_seed": SEED,
        "split_policy": "preserve audited splits for RWF-2000/RLVD/ShanghaiTech; split AIRTLab event groups 80/10/10 with seed 2026; paired camera views stay together",
    }
    return samples, metadata


def split_summary(samples: Sequence[SkeletonSample]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for dataset in ("AIRTLab", "RWF-2000", "Real Life Violence Dataset", "ShanghaiTech"):
        entry: dict[str, Any] = {split: {"Fight": 0, "NonFight": 0, "videos": 0, "windows": 0} for split in ("train", "val", "test")}
        for sample in samples:
            record = sample.record
            if record.dataset != dataset:
                continue
            row = entry[record.split]
            row[record.class_name] += 1
            row["videos"] += 1
            row["windows"] += len(sample.window_rows)
        entry["total_videos"] = sum(entry[split]["videos"] for split in ("train", "val", "test"))
        entry["total_windows"] = sum(entry[split]["windows"] for split in ("train", "val", "test"))
        result[dataset] = entry
    result["overall"] = {
        split: {
            "Fight": sum(value[split]["Fight"] for name, value in result.items() if name != "overall"),
            "NonFight": sum(value[split]["NonFight"] for name, value in result.items() if name != "overall"),
            "videos": sum(value[split]["videos"] for name, value in result.items() if name != "overall"),
            "windows": sum(value[split]["windows"] for name, value in result.items() if name != "overall"),
        }
        for split in ("train", "val", "test")
    }
    result["overall"]["total_videos"] = sum(result["overall"][split]["videos"] for split in ("train", "val", "test"))
    result["overall"]["total_windows"] = sum(result["overall"][split]["windows"] for split in ("train", "val", "test"))
    return result


def _split_manifest(samples: Sequence[SkeletonSample]) -> list[dict[str, Any]]:
    return [{"dataset": item.record.dataset, "source_relative": item.source_relative,
             "group_id": item.group_id, "camera": item.camera, "split": item.record.split,
             "class_name": item.record.class_name, "label": item.record.label,
             "duration_seconds": item.record.duration, "window_count": len(item.window_rows),
             "unique_skeleton_frames": len(item.frame_paths_by_index), "source_sha256": item.source_sha256}
            for item in sorted(samples, key=lambda value: (value.record.dataset, value.source_relative.casefold()))]


def _window_manifest(samples: Sequence[SkeletonSample]) -> list[dict[str, Any]]:
    rows = []
    for item in samples:
        for window in item.window_rows:
            rows.append({"dataset": item.record.dataset, "source_relative": item.source_relative,
                         "group_id": item.group_id, "camera": item.camera, "split": item.record.split,
                         "class_name": item.record.class_name, "label": item.record.label,
                         "window_index": int(window["window_index"]),
                         "window_start_seconds": window["window_start_seconds"],
                         "window_end_seconds": window["window_end_seconds"],
                         "source_frame_indices": json.dumps(window["indices"]),
                         "skeleton_frame_paths": json.dumps(window["frame_paths_list"], ensure_ascii=False)})
    return rows


def _normalise_air_windows(window_rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize AIRT Lab manifest field names to the shared runner schema."""
    normalized = []
    for index, row in enumerate(window_rows):
        raw_indices = row.get("indices", row.get("source_frame_indices_list"))
        if raw_indices is None and row.get("source_frame_indices"):
            raw_indices = json.loads(row["source_frame_indices"])
        indices = [int(value) for value in (raw_indices or [])]
        frame_paths = [str(value) for value in row.get("frame_paths_list", [])]
        if len(indices) != four.NUM_FRAMES or len(frame_paths) != four.NUM_FRAMES:
            raise ValueError(
                f"AIRTLab window {row.get('window_index', index)} must contain exactly "
                f"{four.NUM_FRAMES} frame indices and paths."
            )
        normalized.append({**row, "window_index": int(row.get("window_index", index)),
                           "indices": indices, "frame_paths_list": frame_paths})
    return normalized


def _install_skeleton_runtime_protocol(cache_root: Path) -> dict[str, Any]:
    """Install four-second model helpers while retaining raw source roots.

    The runner reads pose images from skeleton roots, but deployment metadata
    must describe the original datasets. Passing a pose root to the shared
    four-second protocol makes it look for raw-data subfolders such as
    ``archive/Real Life Violence Dataset`` under the pose directory, which
    only fails later when the first epoch checkpoint is written.
    """
    four.LEGACY_DATASET_ROOT = DATA_ROOT
    four.AIRTLAB_ROOT = air.RAW_VIDEO_ROOT
    four.SUPPLEMENT_CACHE_ROOT = Path(cache_root).expanduser().resolve()
    four._install_protocol()
    protocol = four._protocol()
    # Keep metadata serialization failures in preflight, before expensive work.
    json.dumps(protocol, ensure_ascii=False, allow_nan=False)
    return protocol


def _dataset_fingerprint(samples: Sequence[SkeletonSample]) -> str:
    rows = []
    started = time.monotonic()
    for index, item in enumerate(samples, start=1):
        frame_signature = []
        for frame_index, path in sorted(item.frame_paths_by_index.items()):
            stat = path.stat()
            frame_signature.append(f"{frame_index}:{path.name}:{stat.st_size}:{stat.st_mtime_ns}")
        item.frame_stat_signature = ";".join(frame_signature)
        rows.append("|".join((item.record.dataset, item.record.split, str(item.record.label),
                              item.source_relative, item.source_sha256, item.frame_stat_signature)))
        if index == 1 or index % 250 == 0 or index == len(samples):
            elapsed = time.monotonic() - started
            eta = elapsed / index * (len(samples) - index) if index else 0.0
            print(f"[preflight] Skeleton file signature {index}/{len(samples)} samples | elapsed={elapsed:.1f}s | ETA={eta:.1f}s", flush=True)
    return hashlib.sha256("\n".join(sorted(rows)).encode("utf-8")).hexdigest()


def _cache_path(sample: SkeletonSample, cache_root: Path) -> Path:
    frame_stats = sample.frame_stat_signature or ";".join(
        f"{index}:{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}"
        for index, path in sorted(sample.frame_paths_by_index.items())
    )
    signature = "|".join((PROTOCOL_ID, sample.record.dataset, sample.source_relative,
                          sample.source_sha256, frame_stats))
    return cache_root / f"{hashlib.sha256(signature.encode('utf-8')).hexdigest()[:40]}.npz"


def _prepare_skeleton_cache(samples: Sequence[SkeletonSample], cache_root: Path, cv2: Any, np: Any,
                            logger: logging.Logger, status_callback) -> None:
    cache_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    reused = created = 0
    for index, sample in enumerate(samples, start=1):
        path = _cache_path(sample, cache_root)
        record = sample.record
        record.legacy_cache_path = None
        record.supplement_cache_path = path
        record.cache_path = path
        record.required_frame_indices = tuple(sorted(sample.frame_paths_by_index))
        valid = False
        if path.is_file():
            try:
                with np.load(path, allow_pickle=False) as payload:
                    saved_indices = payload["frame_indices"].astype(np.int64)
                    frames = payload["frames"]
                    valid = (saved_indices.tolist() == list(record.required_frame_indices)
                             and frames.ndim == 4 and len(frames) == len(saved_indices)
                             and frames.shape[1] >= core.CROP_SIZE and frames.shape[2] >= core.CROP_SIZE
                             and frames.shape[3] == 3 and frames.dtype == np.uint8)
            except (OSError, ValueError, KeyError):
                valid = False
        if valid:
            reused += 1
        else:
            frames = []
            for frame_index in record.required_frame_indices:
                frame_path = sample.frame_paths_by_index[frame_index]
                bgr = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
                if bgr is None:
                    raise RuntimeError(f"OpenCV could not read skeleton image: {frame_path}")
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                frames.append(core._resize_short_side(rgb, cv2))
            array = np.stack(frames).astype(np.uint8, copy=False)
            temporary = _temporary_path(path)
            try:
                with temporary.open("wb") as handle:
                    np.savez_compressed(handle, frame_indices=np.asarray(record.required_frame_indices, dtype=np.int32), frames=array)
                _replace_with_retry(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            created += 1
        if index == 1 or index % 25 == 0 or index == len(samples):
            elapsed = time.monotonic() - started
            eta = elapsed / index * (len(samples) - index) if index else None
            logger.info("Skeleton input cache %d/%d | reused=%d new=%d | elapsed=%.1fs | ETA=%.1fs",
                        index, len(samples), reused, created, elapsed, eta or 0.0)
            status_callback(stage="准备骨架帧缓存", progress=index / max(1, len(samples)),
                            processed=index, total=len(samples), eta_seconds=eta,
                            detail=f"复用 {reused} · 新建 {created}")
    core._load_cache = four._load_hybrid_cache
    logger.info("Skeleton input cache ready | videos=%d | reused=%d | created=%d | path=%s",
                len(samples), reused, created, cache_root)


def _log_setup(run_dir: Path, resume: bool) -> logging.Logger:
    logger = logging.getLogger("mc3_skeleton_four_source")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(run_dir / "run.log", mode="a" if resume else "w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    return logger


def _deployment_payload(model: Any, torch: Any, epoch: int, auc: float) -> dict[str, Any]:
    return {"deployment_checkpoint_version": 1, "model_name": "mc3_18",
            "state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
            "class_to_idx": core.CLASS_TO_LABEL,
            "protocol": {**four._protocol(), "experiment_protocol": PROTOCOL_ID,
                         "input_modality": "skeleton", "input_representation": "black_background_skeleton",
                         "best_epoch": epoch, "best_validation_roc_auc": auc}}


def _save_recovery(path: Path, *, model: Any, optimizer: Any, scheduler: Any, scaler: Any,
                   torch: Any, np: Any, epoch: int, best_epoch: int, best_auc: float,
                   patience_best_auc: float, bad_epochs: int, history: list[dict[str, Any]],
                   fingerprint: str, config: dict[str, Any]) -> None:
    payload = {"skeleton_checkpoint_version": 1, "experiment_protocol": PROTOCOL_ID,
               "model_state": model.state_dict(), "optimizer_state": optimizer.state_dict(),
               "scheduler_state": scheduler.state_dict(), "scaler_state": scaler.state_dict(),
               "completed_epoch": epoch, "best_epoch": best_epoch, "best_auc": best_auc,
               "patience_best_auc": patience_best_auc, "bad_epochs": bad_epochs,
               "history": history, "dataset_fingerprint": fingerprint, "config": config,
               "rng_state": core._state_rng(torch, np), "saved_at": datetime.now().isoformat(timespec="seconds")}
    temporary = _temporary_path(path)
    try:
        torch.save(payload, temporary)
        _replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_live(path: Path, state: dict[str, Any]) -> None:
    def json_safe(value: Any) -> Any:
        # Training uses -inf as the initial "no best AUC yet" sentinel. JSON
        # has no representation for NaN/Infinity, and allow_nan=False is
        # intentional so invalid metrics never silently leak into status.
        # Convert only for the monitoring artifact; keep the in-memory value
        # unchanged for comparisons and early-stopping logic.
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key: json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_safe(item) for item in value]
        return value

    try:
        _atomic_json(path, json_safe({"updated_at": datetime.now().isoformat(timespec="seconds"), **state}))
    except PermissionError as exc:
        # Telemetry is not part of the model update or recovery checkpoint. A
        # persistently locked status file must not abort an otherwise healthy
        # epoch; the next status write will retry the same atomic update.
        logging.getLogger("mc3_skeleton_four_source").warning(
            "Could not update live status file after retries; training continues: %s", exc
        )


def _evaluate(records: Sequence[core.VideoRecord], split: str, *, model: Any, args: argparse.Namespace,
              np: Any, cv2: Any, torch: Any, device: Any, logger: logging.Logger,
              live_path: Path, epoch: int, status_callback) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected = [record for record in records if record.split == split]
    predictions: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []
    started = time.monotonic()
    amp = device.type == "cuda" and not args.no_amp
    for index, record in enumerate(selected, start=1):
        clips, windows = core._record_bag_tensor(record, training=False, epoch=epoch, seed=args.seed,
                                                 np=np, cv2=cv2, torch=torch)
        probability, scores = core._predict_bag(model, clips, torch, device, amp=amp,
                                                window_batch_size=args.window_batch_size)
        predictions.append({"dataset": record.dataset, "split": record.split, "class_name": record.class_name,
                            "label": record.label, "path": str(record.path), "duration_seconds": record.duration,
                            "window_count": len(windows), "probability_fight": probability})
        for window, score in zip(windows, scores):
            window_rows.append({"dataset": record.dataset, "split": record.split, "class_name": record.class_name,
                                "label": record.label, "path": str(record.path), "start_seconds": window.start,
                                "end_seconds": window.end, "probability_fight": score})
        if index == 1 or index % 20 == 0 or index == len(selected):
            elapsed = time.monotonic() - started
            eta = elapsed / index * (len(selected) - index) if index else None
            logger.info("%s progress %d/%d videos | elapsed=%.1fs | ETA=%.1fs", split, index, len(selected), elapsed, eta or 0.0)
            status_callback(stage="验证中" if split == "val" else "测试中", progress=index / max(1, len(selected)),
                            processed=index, total=len(selected), eta_seconds=eta,
                            detail=f"{split} · {record.dataset} · {index}/{len(selected)}")
            _write_live(live_path, {"stage": "验证中" if split == "val" else "测试中", "epoch": epoch,
                                    "total_epochs": args.epochs, "step": index, "steps_total": len(selected),
                                    "stage_progress": index / max(1, len(selected)), "eta_seconds": eta})
    return predictions, window_rows


def _epoch_artifact(run_dir: Path, epoch: int, model: Any, torch: Any,
                    thresholds: dict[str, Any], val_predictions: Sequence[dict[str, Any]],
                    val_auc: float) -> Path:
    epoch_dir = run_dir / f"epoch_{epoch:02d}"
    epoch_dir.mkdir(parents=True, exist_ok=True)
    payload = _deployment_payload(model, torch, epoch, val_auc)
    temporary = _temporary_path(epoch_dir / "best_model.pt")
    try:
        torch.save(payload, temporary)
        _replace_with_retry(temporary, epoch_dir / "best_model.pt")
    finally:
        temporary.unlink(missing_ok=True)
    val_metrics = core._mode_metrics(val_predictions, thresholds)
    _atomic_json(epoch_dir / "thresholds.json", {"protocol": PROTOCOL_ID, "selection": thresholds,
                                                   "validation_metrics": val_metrics})
    _atomic_json(epoch_dir / "final_report.json", {"experiment_protocol": PROTOCOL_ID,
                                                    "input_representation": "black_background_skeleton",
                                                    "best_epoch": epoch, "best_validation_roc_auc": val_auc,
                                                    "epoch_checkpoint": True,
                                                    "note": "Validation-calibrated thresholds belong to this epoch checkpoint; no test data was used."})
    return epoch_dir / "best_model.pt"


def _run(args: argparse.Namespace) -> Path:
    core._set_seed(args.seed, *_import_seed_deps())
    samples, source_meta = load_samples(Path(args.skeleton_root), Path(args.airtlab_skeleton_root))
    summary = split_summary(samples)
    fingerprint = _dataset_fingerprint(samples)
    run_dir: Path
    resume_payload = None
    if args.resume:
        resume_path = Path(args.resume).expanduser().resolve()
        if not resume_path.is_file() or resume_path.name != "last.pt":
            raise FileNotFoundError("Resume must point to this run's last.pt recovery checkpoint.")
        run_dir = resume_path.parent
        if (run_dir / "final_report.json").is_file():
            raise RuntimeError("This run already finished final evaluation and cannot be resumed.")
        import torch
        try:
            resume_payload = torch.load(resume_path, map_location="cpu", weights_only=False)
        except TypeError:
            resume_payload = torch.load(resume_path, map_location="cpu")
    else:
        run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else Path(args.output_root).resolve() / f"run_skeleton_ui_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        if run_dir.exists() and any(run_dir.iterdir()):
            raise FileExistsError(f"Run directory is not empty; refusing to overwrite: {run_dir}")
        run_dir.mkdir(parents=True, exist_ok=True)

    logger = _log_setup(run_dir, resume_payload is not None)
    logger.info("Black-background skeleton MC3-18 | protocol=%s | seed=%d | epochs=%d | patience=%d", PROTOCOL_ID, args.seed, args.epochs, args.patience)
    logger.info("Four datasets | %s", json.dumps(summary, ensure_ascii=False))
    split_rows = _split_manifest(samples)
    window_rows = _window_manifest(samples)
    _write_csv(run_dir / "dataset_manifest.csv", split_rows)
    _write_csv(run_dir / "window_manifest.csv", window_rows)
    _atomic_json(run_dir / "dataset_summary.json", summary)
    cache_root = Path(args.cache_dir).expanduser().resolve()
    runtime_protocol = _install_skeleton_runtime_protocol(cache_root)
    config = {"experiment_protocol": PROTOCOL_ID, "input_representation": "black_background_skeleton",
              "initialization": "TorchVision MC3-18 Kinetics-400 pretrained weights",
              "seed": args.seed, "epochs": args.epochs, "patience": args.patience,
              "min_delta": args.min_delta, "backbone_lr": args.backbone_lr, "head_lr": args.head_lr,
              "lr_scheduler": "ReduceLROnPlateau", "lr_scheduler_factor": args.lr_factor,
              "lr_scheduler_no_improvement_epochs": args.lr_reduce_after, "min_lr": args.min_lr,
              "weight_decay": args.weight_decay, "grad_accum": args.grad_accum,
              "window_batch_size": args.window_batch_size, "amp": not args.no_amp,
              "device": args.device, "protocol": four.PROTOCOL, "preprocessing": runtime_protocol,
              "skeleton_root": str(Path(args.skeleton_root).resolve()),
              "airtlab_skeleton_root": str(Path(args.airtlab_skeleton_root).resolve()),
              "cache_dir": str(cache_root), "split_policy": source_meta["split_policy"],
              "split_counts": summary, "dataset_fingerprint": fingerprint}
    if resume_payload:
        if resume_payload.get("skeleton_checkpoint_version") != 1 or resume_payload.get("experiment_protocol") != PROTOCOL_ID:
            raise RuntimeError("Unsupported or mismatched skeleton training checkpoint.")
        if resume_payload.get("dataset_fingerprint") != fingerprint:
            raise RuntimeError("Skeleton input files or split assignments changed since the checkpoint; refusing unsafe resume.")
        previous = resume_payload.get("config", {})
        compare_keys = ("seed", "epochs", "patience", "min_delta", "backbone_lr", "head_lr",
                        "lr_scheduler", "lr_scheduler_factor", "lr_scheduler_no_improvement_epochs", "min_lr",
                        "weight_decay", "grad_accum", "window_batch_size", "amp", "protocol")
        mismatch = [key for key in compare_keys if previous.get(key) != config.get(key)]
        if mismatch:
            raise RuntimeError("Resume options differ from checkpoint: " + ", ".join(mismatch))
    _atomic_json(run_dir / "config.json", config)
    split_digest = hashlib.sha256((run_dir / "dataset_manifest.csv").read_bytes()).hexdigest()
    logger.info("Split lock: dataset manifest SHA-256=%s", split_digest)

    cv2, np, torch, nn, model_symbols = core._import_runtime_dependencies()
    weights_enum, model_builder = model_symbols
    if args.device == "auto":
        if not torch.cuda.is_available() and not args.allow_cpu:
            raise RuntimeError("CUDA is unavailable; confirm CPU training explicitly with --device cpu --allow-cpu.")
        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    elif args.device == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but PyTorch cannot see a CUDA device.")
        device = torch.device("cuda:0")
    else:
        if not args.allow_cpu:
            raise RuntimeError("CPU mode needs explicit --allow-cpu confirmation.")
        device = torch.device("cpu")
    amp = device.type == "cuda" and not args.no_amp
    logger.info("Runtime | device=%s | AMP=%s | GPU=%s", device, amp, torch.cuda.get_device_name(0) if device.type == "cuda" else "n/a")
    live_path = run_dir / "live_status.json"

    def status_callback(**values: Any) -> None:
        current = _read_json(live_path) or {}
        _write_live(live_path, {**current, **values})

    logger.info("Preparing/reusing resized skeleton frame cache. Original PNGs and extraction manifests remain unchanged.")
    _write_live(live_path, {"status": "running", "stage": "准备骨架帧缓存", "epoch": 0,
                            "total_epochs": args.epochs, "stage_progress": 0.0,
                            "sample_total": len(samples), "started_at": time.time()})
    _prepare_skeleton_cache(samples, cache_root, cv2, np, logger, status_callback)
    records = [sample.record for sample in samples]
    if resume_payload:
        model = core._build_model(torch, nn, None, model_builder, device, pretrained=False)
    else:
        model = core._build_model(torch, nn, weights_enum, model_builder, device, pretrained=True)
    optimizer = torch.optim.AdamW([
        {"params": [p for name, p in model.named_parameters() if not name.startswith("fc.")], "lr": args.backbone_lr},
        {"params": list(model.fc.parameters()), "lr": args.head_lr},
    ], weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=args.lr_factor,
        patience=args.lr_reduce_after - 1, min_lr=args.min_lr,
    )
    scaler = core._make_scaler(torch, amp)
    start_epoch, best_epoch, best_auc, patience_best_auc, bad_epochs = 1, 0, -float("inf"), -float("inf"), 0
    history: list[dict[str, Any]] = []
    if resume_payload:
        model.load_state_dict(resume_payload["model_state"])
        optimizer.load_state_dict(resume_payload["optimizer_state"])
        scheduler.load_state_dict(resume_payload["scheduler_state"])
        scaler.load_state_dict(resume_payload.get("scaler_state", {}))
        start_epoch = int(resume_payload["completed_epoch"]) + 1
        best_epoch, best_auc = int(resume_payload["best_epoch"]), float(resume_payload["best_auc"])
        patience_best_auc = float(resume_payload["patience_best_auc"])
        bad_epochs = int(resume_payload["bad_epochs"])
        history = list(resume_payload["history"])
        core._restore_rng(resume_payload["rng_state"], torch, np)
        logger.info("Resume from epoch %d; next epoch=%d | best val AUC=%.6f@%d", start_epoch - 1, start_epoch, best_auc, best_epoch)

    train_records = [record for record in records if record.split == "train"]
    criterion = nn.CrossEntropyLoss()
    for epoch in range(start_epoch, args.epochs + 1):
        epoch_start = time.monotonic()
        if device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(device)
        model.train()
        core._freeze_batch_norm_running_stats(model, nn)
        ordered = list(train_records)
        random.Random(args.seed + epoch).shuffle(ordered)
        optimizer.zero_grad(set_to_none=True)
        accumulated = 0
        total_loss = 0.0
        train_labels: list[int] = []
        train_probs: list[float] = []
        logger.info("EPOCH %d/%d START | train videos=%d", epoch, args.epochs, len(ordered))
        for index, record in enumerate(ordered, start=1):
            clips, _ = core._record_bag_tensor(record, training=True, epoch=epoch, seed=args.seed,
                                               np=np, cv2=cv2, torch=torch)
            hard_window, _ = core._choose_hard_instance(model, clips, torch, device,
                                                         amp=amp, window_batch_size=args.window_batch_size)
            selected = clips[hard_window:hard_window + 1].to(device, non_blocking=True)
            target = torch.tensor([record.label], dtype=torch.long, device=device)
            with core._amp_context(torch, device, amp):
                logits = model(selected)
                loss = criterion(logits, target)
            scaler.scale(loss / args.grad_accum).backward()
            accumulated += 1
            total_loss += float(loss.detach().float().item())
            train_probs.append(float(torch.softmax(logits.detach().float(), dim=1)[0, 1].item()))
            train_labels.append(record.label)
            if accumulated == args.grad_accum:
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad(set_to_none=True)
                accumulated = 0
            if index == 1 or index % args.log_every == 0 or index == len(ordered):
                elapsed = time.monotonic() - epoch_start
                eta = elapsed / index * (len(ordered) - index) if index else 0.0
                logger.info("epoch %d/%d train %d/%d videos | loss=%.5f | val-early-stop=AUC | elapsed=%.1fs | ETA=%.1fs",
                            epoch, args.epochs, index, len(ordered), total_loss / index, elapsed, eta)
                _write_live(live_path, {"status": "running", "stage": "训练中", "epoch": epoch,
                                        "total_epochs": args.epochs, "step": index, "steps_total": len(ordered),
                                        "stage_progress": index / len(ordered), "train_loss": total_loss / index,
                                        "eta_seconds": eta, "best_epoch": best_epoch, "best_val_roc_auc": best_auc,
                                        "patience": args.patience, "patience_used": bad_epochs,
                                        "elapsed_seconds": time.monotonic() - epoch_start})
        if accumulated:
            scaler.unscale_(optimizer)
            correction = args.grad_accum / accumulated
            for parameter in model.parameters():
                if parameter.grad is not None:
                    parameter.grad.mul_(correction)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad(set_to_none=True)
        train_loss = total_loss / max(1, len(ordered))
        train_auc = core.roc_auc(train_labels, train_probs)
        val_predictions, val_windows = _evaluate(records, "val", model=model, args=args, np=np, cv2=cv2,
                                                  torch=torch, device=device, logger=logger,
                                                  live_path=live_path, epoch=epoch, status_callback=status_callback)
        val_labels = [int(row["label"]) for row in val_predictions]
        val_probabilities = [float(row["probability_fight"]) for row in val_predictions]
        val_auc = core.roc_auc(val_labels, val_probabilities)
        if val_auc is None:
            raise RuntimeError("Combined validation ROC-AUC is undefined; Fight and NonFight are both required.")
        scheduler.step(val_auc)
        val_half = core.binary_metrics(val_labels, val_probabilities, 0.5)
        epoch_thresholds = core.choose_thresholds(val_labels, val_probabilities)
        improved = val_auc > best_auc
        significant = val_auc > patience_best_auc + args.min_delta
        if improved:
            best_auc, best_epoch = val_auc, epoch
            payload = _deployment_payload(model, torch, epoch, val_auc)
            temporary = _temporary_path(run_dir / "best_model.pt")
            torch.save(payload, temporary)
            _replace_with_retry(temporary, run_dir / "best_model.pt")
        if significant:
            patience_best_auc, bad_epochs = val_auc, 0
        else:
            bad_epochs += 1
        epoch_seconds = time.monotonic() - epoch_start
        history_row = {"epoch": epoch, "train_loss": train_loss, "train_roc_auc": train_auc,
                       "val_roc_auc": val_auc, "val_accuracy_at_0_5": val_half["accuracy"],
                       "val_precision_at_0_5": val_half["precision"], "val_recall_at_0_5": val_half["recall"],
                       "val_f1_at_0_5": val_half["f1"], "val_pr_auc": val_half.get("average_precision"),
                       "learning_rate_backbone": optimizer.param_groups[0]["lr"],
                       "learning_rate_head": optimizer.param_groups[1]["lr"], "seconds": epoch_seconds,
                       "improved": int(improved), "patience_counter": bad_epochs,
                       "best_epoch": best_epoch, "best_val_roc_auc": best_auc}
        if device.type == "cuda":
            history_row["peak_gpu_memory_mb"] = torch.cuda.max_memory_allocated(device) / (1024**2)
        history.append(history_row)
        _write_csv(run_dir / "training_history.csv", history)
        _write_csv(run_dir / f"validation_epoch_{epoch:02d}_predictions.csv", val_predictions)
        _write_csv(run_dir / f"validation_epoch_{epoch:02d}_window_predictions.csv", val_windows)
        _atomic_json(run_dir / f"validation_epoch_{epoch:02d}_by_dataset.json",
                     core._per_dataset_metrics(val_predictions, {"moderate": {"threshold": 0.5}}))
        _epoch_artifact(run_dir, epoch, model, torch, epoch_thresholds, val_predictions, val_auc)
        _save_recovery(run_dir / "last.pt", model=model, optimizer=optimizer, scheduler=scheduler,
                       scaler=scaler, torch=torch, np=np, epoch=epoch, best_epoch=best_epoch,
                       best_auc=best_auc, patience_best_auc=patience_best_auc, bad_epochs=bad_epochs,
                       history=history, fingerprint=fingerprint, config=config)
        logger.info("EPOCH %d/%d COMPLETE | train_loss=%.5f | train_AUC=%s | val_AUC=%.6f | val_P/R/F1@0.5=%.4f/%.4f/%.4f | LR=%.8g/%.8g | best=%.6f@%d | early_stop_patience=%d/%d | checkpoint=epoch_%02d/best_model.pt",
                    epoch, args.epochs, train_loss, "n/a" if train_auc is None else f"{train_auc:.6f}",
                    val_auc, val_half["precision"], val_half["recall"], val_half["f1"],
                    optimizer.param_groups[0]["lr"], optimizer.param_groups[1]["lr"], best_auc,
                    best_epoch, bad_epochs, args.patience, epoch)
        _write_live(live_path, {"status": "running", "stage": "Epoch 完成", "epoch": epoch,
                                "total_epochs": args.epochs, "completed_epochs": epoch,
                                "stage_progress": 1.0, "train_loss": train_loss, "train_roc_auc": train_auc,
                                "val_roc_auc": val_auc, "val_metrics_at_0_5": val_half,
                                "best_epoch": best_epoch, "best_val_roc_auc": best_auc,
                                "patience_used": bad_epochs, "patience": args.patience,
                                "epoch_seconds": epoch_seconds, "epoch_checkpoint": str(run_dir / f"epoch_{epoch:02d}" / "best_model.pt"),
                                "validation_by_dataset": core._per_dataset_metrics(val_predictions, epoch_thresholds)})
        if core.consume_pause_request(args.pause_file):
            logger.info("SAFE PAUSE after completed epoch %d; resume from %s", epoch, run_dir / "last.pt")
            _write_live(live_path, {"status": "paused", "stage": "已在 epoch 边界安全暂停", "epoch": epoch,
                                    "total_epochs": args.epochs, "completed_epochs": epoch,
                                    "last_checkpoint": str(run_dir / "last.pt"), "stage_progress": 0.0})
            return run_dir
        if bad_epochs >= args.patience:
            logger.info("EARLY STOP after epoch %d | validation ROC-AUC did not improve by %.4f for %d epochs.", epoch, args.min_delta, args.patience)
            break

    best_path = run_dir / "best_model.pt"
    if not best_path.is_file():
        raise RuntimeError("No validation-selected best model checkpoint exists.")
    best_payload = torch.load(best_path, map_location="cpu", weights_only=True)
    model.load_state_dict(best_payload["state_dict"])
    model.eval()
    best_epoch = int(best_payload["protocol"]["best_epoch"])
    logger.info("Locked best model at epoch %d, validation ROC-AUC=%.6f. Calibrate thresholds on validation before test.", best_epoch, best_auc)
    val_predictions, val_windows = _evaluate(records, "val", model=model, args=args, np=np, cv2=cv2,
                                              torch=torch, device=device, logger=logger, live_path=live_path,
                                              epoch=best_epoch, status_callback=status_callback)
    val_labels = [int(row["label"]) for row in val_predictions]
    val_probabilities = [float(row["probability_fight"]) for row in val_predictions]
    thresholds = core.choose_thresholds(val_labels, val_probabilities)
    validation_metrics = core._mode_metrics(val_predictions, thresholds)
    _write_csv(run_dir / "validation_predictions.csv", val_predictions)
    _write_csv(run_dir / "validation_window_predictions.csv", val_windows)
    _atomic_json(run_dir / "thresholds.json", {"protocol": PROTOCOL_ID, "selection": thresholds,
                                                "validation_metrics": validation_metrics})
    logger.info("Thresholds locked from validation only: %s", json.dumps(thresholds, ensure_ascii=False))
    test_predictions, test_windows = _evaluate(records, "test", model=model, args=args, np=np, cv2=cv2,
                                                torch=torch, device=device, logger=logger, live_path=live_path,
                                                epoch=best_epoch, status_callback=status_callback)
    test_metrics = core._mode_metrics(test_predictions, thresholds)
    _write_csv(run_dir / "test_predictions.csv", test_predictions)
    _write_csv(run_dir / "test_window_predictions.csv", test_windows)
    report = {"experiment_protocol": PROTOCOL_ID, "status": "completed",
              "input_representation": "black_background_skeleton", "best_epoch": best_epoch,
              "best_validation_roc_auc": best_auc, "epochs_completed": len(history),
              "early_stopped_after_epoch": int(history[-1]["epoch"]), "thresholds": thresholds,
              "metric_groups": {mode: {"threshold": thresholds[mode]["threshold"],
                                        "validation": validation_metrics[mode], "test": test_metrics[mode]}
                                for mode in ("sensitive", "moderate", "certain")},
              "validation_metrics_by_dataset": core._per_dataset_metrics(val_predictions, thresholds),
              "test_metrics_by_dataset": core._per_dataset_metrics(test_predictions, thresholds),
              "counts": {"videos": summary["overall"]["total_videos"], "windows": summary["overall"]["total_windows"],
                         "validation_videos": len(val_predictions), "test_videos": len(test_predictions),
                         "validation_windows": len(val_windows), "test_windows": len(test_windows)},
              "split_summary": summary, "training_config": config,
              "artifacts": {"best_checkpoint": str(best_path.resolve()), "last_checkpoint": str((run_dir / "last.pt").resolve()),
                            "epoch_checkpoints": str(run_dir.resolve()), "training_history": str((run_dir / "training_history.csv").resolve())},
              "test_policy": "test predictions were generated only after locking the validation-AUC best checkpoint and all validation-calibrated thresholds"}
    _atomic_json(run_dir / "final_report.json", report)
    logger.info("TRAINING COMPLETE | best epoch=%d | val AUC=%.6f | test AUC@0.5=%.6f | 15-epoch cap / 4-epoch early-stop patience",
                best_epoch, best_auc, test_metrics["moderate"]["roc_auc"])
    _write_live(live_path, {"status": "completed", "stage": "训练、阈值校准与测试已完成",
                            "epoch": int(history[-1]["epoch"]), "completed_epochs": len(history),
                            "total_epochs": args.epochs, "stage_progress": 1.0, "best_epoch": best_epoch,
                            "best_val_roc_auc": best_auc, "last_checkpoint": str(run_dir / "last.pt"),
                            "final_report": str(run_dir / "final_report.json")})
    return run_dir


def _import_seed_deps():
    import numpy as np
    import torch
    return torch, np


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skeleton-root", default=str(DEFAULT_SKELETON_ROOT))
    parser.add_argument("--airtlab-skeleton-root", default=str(DEFAULT_AIRTLAB_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--cache-dir", default=str(DEFAULT_OUTPUT_ROOT / "_frame_cache" / PROTOCOL_ID))
    parser.add_argument("--run-dir")
    parser.add_argument("--resume")
    parser.add_argument("--pause-file")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--allow-cpu", action="store_true")
    parser.add_argument("--no-amp", action="store_true")
    parser.add_argument("--epochs", type=int, default=EPOCHS)
    parser.add_argument("--patience", type=int, default=PATIENCE)
    parser.add_argument("--min-delta", type=float, default=MIN_DELTA)
    parser.add_argument("--backbone-lr", type=float, default=2e-5)
    parser.add_argument("--head-lr", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--lr-reduce-after", type=int, default=LR_SCHEDULER_NO_IMPROVEMENT_EPOCHS,
                        help="Halve LR after this many consecutive validation epochs without improvement.")
    parser.add_argument("--lr-factor", type=float, default=LR_SCHEDULER_FACTOR)
    parser.add_argument("--min-lr", type=float, default=1e-6)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--window-batch-size", type=int, default=2)
    parser.add_argument("--log-every", type=int, default=25)
    parser.add_argument("--seed", type=int, default=SEED)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.epochs <= 300 or not 1 <= args.patience <= 300:
        raise SystemExit("epochs and early-stop patience must each be between 1 and 300.")
    if not 1 <= args.lr_reduce_after <= 300 or args.log_every < 1:
        raise SystemExit("LR reduction patience must be 1..300 and log-every must be positive.")
    if not 1 <= args.grad_accum <= 128 or not 1 <= args.window_batch_size <= 8:
        raise SystemExit("grad-accum must be 1..128 and window-batch-size must be 1..8.")
    if not 0 <= args.seed <= 2**32 - 1:
        raise SystemExit("Seed must be between 0 and 2^32-1.")
    numeric = (args.min_delta, args.backbone_lr, args.head_lr, args.weight_decay,
               args.lr_factor, args.min_lr)
    if not all(math.isfinite(value) for value in numeric):
        raise SystemExit("Hyperparameters must be finite numbers.")
    if args.backbone_lr <= 0 or args.head_lr <= 0 or args.weight_decay < 0 or args.min_delta < 0:
        raise SystemExit("Learning rates must be positive; weight decay and min-delta must be non-negative.")
    if not 0 < args.lr_factor < 1 or args.min_lr < 0 or args.min_lr > min(args.backbone_lr, args.head_lr):
        raise SystemExit("LR factor must be between 0 and 1, and minimum LR cannot exceed either initial LR.")
    try:
        if args.plan_only:
            samples, _metadata = load_samples(Path(args.skeleton_root).expanduser().resolve(), Path(args.airtlab_skeleton_root).expanduser().resolve())
            summary = split_summary(samples)
            split_rows, window_rows = _split_manifest(samples), _window_manifest(samples)
            runtime_protocol = _install_skeleton_runtime_protocol(Path(args.cache_dir))
            print("Skeleton four-source split (paired AIRTLab event groups, 80/10/10):", flush=True)
            print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
            print(f"Manifest serialization preflight: {len(split_rows)} video rows / {len(window_rows)} window rows", flush=True)
            print(f"Checkpoint/deployment protocol serialization preflight: ok | source_root={runtime_protocol['source_roots']['legacy']}", flush=True)
            print(f"Total videos={summary['overall']['total_videos']} | windows={summary['overall']['total_windows']} | protocol={PROTOCOL_ID}", flush=True)
            return 0
        if args.pause_file:
            Path(args.pause_file).parent.mkdir(parents=True, exist_ok=True)
        _run(args)
        return 0
    except Exception as exc:
        run_value = args.run_dir or (str(Path(args.resume).expanduser().resolve().parent) if args.resume else "")
        if run_value:
            run_dir = Path(run_value).expanduser().resolve()
            try:
                run_dir.mkdir(parents=True, exist_ok=True)
                logger = logging.getLogger("mc3_skeleton_four_source")
                run_log = (run_dir / "run.log").resolve()
                has_run_handler = any(
                    isinstance(handler, logging.FileHandler)
                    and Path(handler.baseFilename).resolve() == run_log
                    for handler in logger.handlers
                )
                if has_run_handler:
                    logger.exception("RUN FAILED: %s", exc)
                    for handler in logger.handlers:
                        handler.flush()
                else:
                    with run_log.open("a", encoding="utf-8") as handle:
                        handle.write(f"\nRUN FAILED: {exc}\n{traceback.format_exc()}\n")
                current = _read_json(run_dir / "live_status.json") or {}
                _write_live(run_dir / "live_status.json", {
                    **current, "status": "failed", "stage": "训练失败",
                    "error": str(exc), "error_traceback": traceback.format_exc(),
                })
            except Exception as log_error:
                print(f"Could not persist failure details: {log_error}", flush=True)
        print(f"ERROR: {exc}", flush=True)
        raise


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
