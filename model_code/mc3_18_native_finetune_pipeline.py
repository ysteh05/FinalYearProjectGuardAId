"""Four-source native-video MC3-18 fine-tuning using the audited 4S16F2 best model.

AIRTLab split assignments are copied from the selected base run's manifest.
DanceTrack keeps its prepared official split mapping. Dance Genre Estimation is
stratified by genre for a deterministic 80:10:10 split; ShakeFive2 gets a
deterministic 80:10:10 split. The latter three sources are used as NonFight
candidate bags for this experiment, not as authoritative violence labels.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import numbers
import os
import random
import shutil
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import mc3_18_four_second_pipeline as four
import mc3_18_mil_pipeline as core


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "GuardAId Video Classification Model Dataset"
DEFAULT_BASE_RUN = PROJECT_ROOT / "model_runs" / "mc3_4s16f2" / "run_four_ui_20261007_234526_491833"
DEFAULT_BASE_CHECKPOINT = DEFAULT_BASE_RUN / "best_model.pt"
DEFAULT_AIR_MANIFEST = DEFAULT_BASE_RUN / "dataset_manifest.csv"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_native_four_dataset_finetune_4s16f2"
DEFAULT_DANCETRACK_MANIFEST = DATA_ROOT / "DanceTrack" / "dancetrack_4s16f2_frame_manifest.json"
DEFAULT_TWO_SECOND_CACHE = (
    PROJECT_ROOT / "model_runs" / "mc3_2s8f_reverse_concat" / "_frame_cache"
    / "mc3-2s-1s-stride-8f-128short-112crop-reverse-concat-v2"
)
MIN_FREE_DISK_RESERVE_BYTES = 2 * 1024**3
VIDEO_CACHE_CAP_BYTES = 20 * 1024**3
DANCETRACK_CACHE_CAP_BYTES = 2 * 1024**3
PROTOCOL_ID = "mc3-native-four-dataset-finetune-4s16f2-v1"
INPUT_REPRESENTATION = "native_video"
INPUT_PROTOCOL_ID = four.PROTOCOL
_DEFAULT_CACHE_REUSE_METADATA: dict[str, Any] = {
    "base_four_second_read_only": "audited 4S16F2 raw-frame cache",
    "legacy_two_second_read_only": "legacy read-only cache",
    "new_supplemental_cache": "run-local cache for missing sampled frames",
}
CACHE_REUSE_METADATA: dict[str, Any] = _DEFAULT_CACHE_REUSE_METADATA
SPLITS = ("train", "val", "test")
DATASET_NAMES = ("AIRTLab", "DanceTrack", "Dance Genre Estimation", "ShakeFive2")
VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv"}
SOURCE_LABELS = {"Fight": 1, "NonFight": 0}
FOUR_SECOND_PROTOCOL = four.PROTOCOL


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: Sequence[dict[str, Any]], fieldnames: Sequence[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows and fieldnames is None:
        path.write_text("", encoding="utf-8")
        return
    names = list(fieldnames or rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in names})


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _base_four_second_supplement_cache_root() -> Path:
    """Resolve the exact 4S cache recorded by the selected Best run."""
    saved = _read_json(DEFAULT_BASE_RUN / "config.json") or {}
    preprocessing = saved.get("preprocessing", {})
    cache_reuse = preprocessing.get("cache_reuse", {}) if isinstance(preprocessing, dict) else {}
    configured = cache_reuse.get("supplemental_cache") if isinstance(cache_reuse, dict) else None
    configured = configured or saved.get("cache_dir")
    if configured:
        return Path(configured).expanduser().resolve()
    return four.DEFAULT_SUPPLEMENT_CACHE_ROOT.expanduser().resolve()


def _stable_hash(value: str, seed: int) -> str:
    return hashlib.sha256(f"{seed}|{value}".encode("utf-8")).hexdigest()


def _stratified_dge_splits(paths: Sequence[Path], seed: int) -> dict[Path, str]:
    """Use genre prefixes as strata while keeping the overall split exactly 80/10/10."""
    by_genre: dict[str, list[Path]] = defaultdict(list)
    for path in paths:
        by_genre[path.stem.split("_")[0]].append(path)
    result: dict[Path, str] = {}
    leftovers: list[Path] = []
    for genre, members in sorted(by_genre.items()):
        ordered = sorted(members, key=lambda item: (_stable_hash(item.name.casefold(), seed), item.name.casefold()))
        if len(ordered) < 5:
            raise ValueError(f"Dance Genre Estimation stratum {genre!r} has too few videos to split safely.")
        # Reserve 2 validation and 2 test items per genre, and use 16 for train
        # when possible. Remaining videos are globally assigned to hit exact totals.
        base_train = min(16, len(ordered) - 4)
        for path in ordered[:base_train]:
            result[path] = "train"
        cursor = base_train
        val_count = min(2, len(ordered) - cursor)
        for path in ordered[cursor:cursor + val_count]:
            result[path] = "val"
        cursor += val_count
        test_count = min(2, len(ordered) - cursor)
        for path in ordered[cursor:cursor + test_count]:
            result[path] = "test"
        cursor += test_count
        leftovers.extend(ordered[cursor:])
    total = len(paths)
    targets = {"train": round(total * 0.8), "val": round(total * 0.1)}
    targets["test"] = total - targets["train"] - targets["val"]
    counts = {split: sum(value == split for value in result.values()) for split in SPLITS}
    for path in sorted(leftovers, key=lambda item: (_stable_hash(item.name.casefold(), seed + 1), item.name.casefold())):
        deficit = {split: targets[split] - counts[split] for split in SPLITS}
        chosen = max(SPLITS, key=lambda split: (deficit[split], -SPLITS.index(split)))
        result[path] = chosen
        counts[chosen] += 1
    if len(result) != total or counts != targets:
        raise RuntimeError(f"Could not produce exact stratified Dance Genre Estimation split: {counts} vs {targets}")
    return result


def _random_splits(paths: Sequence[Path], seed: int) -> dict[Path, str]:
    ordered = sorted(paths, key=lambda item: item.name.casefold())
    random.Random(seed).shuffle(ordered)
    raw = {"train": len(ordered) * 0.8, "val": len(ordered) * 0.1, "test": len(ordered) * 0.1}
    targets = {split: math.floor(raw[split]) for split in SPLITS}
    for split in sorted(SPLITS, key=lambda item: (-(raw[item] - targets[item]), SPLITS.index(item)))[:len(ordered) - sum(targets.values())]:
        targets[split] += 1
    train_count, val_count = targets["train"], targets["val"]
    return {path: "train" if index < train_count else "val" if index < train_count + val_count else "test"
            for index, path in enumerate(ordered)}


def _new_record(dataset: str, split: str, class_name: str, path: Path, fps: float, frame_count: int,
                duration: float, *, source_group: str = "") -> core.VideoRecord:
    stat = path.stat()
    record = core.VideoRecord(dataset=dataset, split=split, class_name=class_name,
                              label=SOURCE_LABELS[class_name], path=path.resolve(), fps=fps,
                              frame_count=frame_count, duration=duration, size_bytes=stat.st_size,
                              mtime_ns=stat.st_mtime_ns)
    record.source_group = source_group or f"{dataset}|{path.name.casefold()}"
    return record


def _probe_video(path: Path, dataset: str, split: str, class_name: str, cv2: Any,
                 *, source_group: str = "") -> tuple[core.VideoRecord | None, dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Dataset video is missing: {path}")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        cap.release()
        raise RuntimeError(f"OpenCV cannot open dataset video: {path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    if not math.isfinite(fps) or fps <= 0 or frame_count <= 0:
        raise RuntimeError(f"Invalid FPS/frame count in dataset video: {path}")
    duration = frame_count / fps
    eligible = duration >= four.MIN_SCAN_SECONDS
    row = {"dataset": dataset, "split": split, "class_name": class_name,
           "label": SOURCE_LABELS[class_name], "path": str(path.resolve()),
           "source_group": source_group or f"{dataset}|{path.name.casefold()}",
           "fps": f"{fps:.8f}", "frame_count": frame_count,
           "duration_seconds": f"{duration:.8f}", "eligible": int(eligible),
           "exclusion_reason": "shorter_than_1_second" if not eligible else "",
           "size_bytes": path.stat().st_size}
    return (_new_record(dataset, split, class_name, path, fps, frame_count, duration,
                        source_group=source_group) if eligible else None), row


def load_records(*, dataset_root: Path = DATA_ROOT, base_manifest: Path = DEFAULT_AIR_MANIFEST,
                 dancetrack_manifest: Path = DEFAULT_DANCETRACK_MANIFEST, seed: int = 2026,
                 cv2: Any | None = None, logger: logging.Logger | None = None,
                 validate_dance_frames: bool = False, progress_callback=None) -> tuple[list[core.VideoRecord], list[dict[str, Any]], dict[str, Any]]:
    """Read the locked AIRT split and construct reproducible non-fight datasets."""
    dataset_root = dataset_root.resolve()
    if cv2 is None:
        import cv2 as cv2_module
        cv2 = cv2_module
    if not base_manifest.is_file():
        raise FileNotFoundError(f"The audited base-run AIRT split manifest is missing: {base_manifest}")
    base_rows = [row for row in _read_csv(base_manifest) if row.get("dataset") == "AIRTLab" and row.get("eligible") == "1"]
    if len(base_rows) != 350:
        raise RuntimeError(f"Expected all 350 eligible AIRT Lab rows from the base checkpoint manifest, found {len(base_rows)}")
    records: list[core.VideoRecord] = []
    manifest_rows: list[dict[str, Any]] = []
    groups: dict[str, set[str]] = defaultdict(set)
    group_labels: dict[str, set[int]] = defaultdict(set)
    for row_index, row in enumerate(base_rows, start=1):
        split, class_name = row.get("split", ""), row.get("class_name", "")
        label = int(row.get("label", "-1"))
        if split not in SPLITS or class_name not in SOURCE_LABELS or SOURCE_LABELS[class_name] != label:
            raise ValueError(f"Unexpected AIRT Lab base-run split/label row: {row}")
        path = Path(row.get("path", "")).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"AIRT Lab base-run sample is missing: {path}")
        fps, frame_count = float(row["fps"]), int(row["frame_count"])
        duration = float(row["duration_seconds"])
        if fps <= 0 or frame_count <= 0 or duration < four.MIN_SCAN_SECONDS:
            raise ValueError(f"Invalid or ineligible AIRT Lab metadata in base manifest: {path}")
        record = _new_record("AIRTLab", split, class_name, path, fps, frame_count, duration,
                             source_group=row.get("source_group", ""))
        record.base_manifest_row = dict(row)
        records.append(record)
        manifest_rows.append({**row, "dataset": "AIRTLab", "path": str(path), "source_group": record.source_group,
                              "split_source": "exact_base_best_checkpoint_manifest"})
        groups[record.source_group].add(split)
        group_labels[record.source_group].add(label)
        if row_index == 1 or row_index % 50 == 0 or row_index == len(base_rows):
            if logger:
                logger.info("Preflight AIRTLab manifest %d/%d rows | preserving prior split", row_index, len(base_rows))
            if progress_callback:
                progress_callback(stage="核验 AIRT Lab 原始 split 清单", step=row_index,
                                  steps_total=len(base_rows), stage_progress=row_index / len(base_rows))
    if any(len(splits) != 1 for splits in groups.values()) or any(len(labels) != 1 for labels in group_labels.values()):
        raise RuntimeError("AIRT Lab base split leakage check failed: a camera-paired group crosses splits or labels.")
    source_group_sizes: dict[str, int] = defaultdict(int)
    for record in records:
        source_group_sizes[record.source_group] += 1
    if any(size != 2 for size in source_group_sizes.values()):
        raise RuntimeError("AIRT Lab base split no longer contains exactly paired camera views for every source group.")

    dance_manifest = json.loads(dancetrack_manifest.read_text(encoding="utf-8"))
    if dance_manifest.get("protocol") != "4S16F2":
        raise RuntimeError("DanceTrack retained-frame manifest is not the prepared 4S16F2 dataset.")
    dance_root = dataset_root / "DanceTrack"
    if Path(str(dance_manifest.get("dataset_root", ""))).resolve() != dance_root.resolve():
        raise RuntimeError("DanceTrack manifest belongs to a different data directory.")
    split_mapping = dance_manifest.get("split_mapping", {})
    sequence_rows = dance_manifest.get("sequences", [])
    if len(sequence_rows) != 100:
        raise RuntimeError(f"Expected 100 DanceTrack sequences, found {len(sequence_rows)}")
    for sequence_index, sequence in enumerate(sequence_rows, start=1):
        source_split = str(sequence.get("source_shard", ""))
        split = split_mapping.get(source_split)
        if split not in SPLITS:
            raise ValueError(f"Unrecognized DanceTrack official split shard {source_split!r}")
        rel = Path(str(sequence["sequence_path"]))
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"Unsafe DanceTrack sequence path: {rel}")
        sequence_path = dance_root / rel
        fps = float(sequence["fps"])
        frame_count = int(sequence["original_frame_count"])
        record = _new_record("DanceTrack", split, "NonFight", sequence_path, fps, frame_count,
                             frame_count / fps, source_group=f"DanceTrack|{sequence_path.name.casefold()}")
        record.dancetrack_sequence = sequence
        record.dancetrack_root = dance_root
        record.retained_frame_numbers = set(int(value) for value in sequence["retained_frame_numbers_1based"])
        windows = four.make_four_second_windows(record.duration, record.fps, record.frame_count)
        manifest_paths: set[int] = set()
        for window in sequence.get("windows", []):
            manifest_paths.update(int(value) - 1 for value in window.get("source_frame_numbers_1based", []))
        expected_indices = {frame_index for window in windows for frame_index in window.frame_indices}
        if expected_indices != manifest_paths:
            raise RuntimeError(f"DanceTrack 4S16F2 sampling differs from its retained-frame manifest: {sequence_path}")
        required_numbers = {index + 1 for index in expected_indices}
        if not required_numbers.issubset(record.retained_frame_numbers):
            raise RuntimeError(f"DanceTrack manifest did not retain every required 4S16F2 frame: {sequence_path}")
        if validate_dance_frames:
            missing = [sequence_path / "img1" / f"{number:08d}.jpg" for number in sorted(required_numbers)
                       if not (sequence_path / "img1" / f"{number:08d}.jpg").is_file()]
            if missing:
                raise FileNotFoundError(f"DanceTrack sampled frame is missing: {missing[0]}")
        record.required_frame_indices = tuple(sorted(expected_indices))
        records.append(record)
        manifest_rows.append({"dataset": "DanceTrack", "split": split, "class_name": "NonFight", "label": 0,
                              "path": str(sequence_path.resolve()), "source_group": record.source_group,
                              "fps": fps, "frame_count": frame_count, "duration_seconds": record.duration,
                              "eligible": 1, "exclusion_reason": "", "size_bytes": 0,
                              "split_source": f"official_{source_split}_mapping", "window_count": len(windows)})
        if sequence_index == 1 or sequence_index % 10 == 0 or sequence_index == len(sequence_rows):
            if logger:
                logger.info("Preflight DanceTrack official split/frame map %d/%d sequences", sequence_index, len(sequence_rows))
            if progress_callback:
                progress_callback(stage="核验 DanceTrack 官方 split 与帧映射", step=sequence_index,
                                  steps_total=len(sequence_rows), stage_progress=sequence_index / len(sequence_rows))
    if logger:
        logger.info("AIRT Lab: exact base split locked | rows=%d | paired source groups=%d", len(base_rows), len(groups))

    dge_root = dataset_root / "dance_genre_estimation" / "dance_genre_estimation"
    dge_paths = sorted((path for path in dge_root.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES),
                       key=lambda path: path.name.casefold())
    if len(dge_paths) != 210:
        raise RuntimeError(f"Expected 210 Dance Genre Estimation videos, found {len(dge_paths)} under {dge_root}")
    dge_splits = _stratified_dge_splits(dge_paths, seed)
    for path_index, path in enumerate(dge_paths, start=1):
        group = f"DGE|{path.stem.casefold()}"
        record, row = _probe_video(path, "Dance Genre Estimation", dge_splits[path], "NonFight", cv2,
                                   source_group=group)
        row["split_source"] = f"genre_stratified_seed_{seed}"
        if record is not None:
            records.append(record)
        manifest_rows.append(row)
        if path_index == 1 or path_index % 25 == 0 or path_index == len(dge_paths):
            if logger:
                logger.info("Preflight Dance Genre Estimation %d/%d videos", path_index, len(dge_paths))
            if progress_callback:
                progress_callback(stage="读取 Dance Genre Estimation 视频元数据", step=path_index,
                                  steps_total=len(dge_paths), stage_progress=path_index / len(dge_paths))

    shake_root = dataset_root / "ShakeFive2.videos"
    shake_paths = sorted((path for path in shake_root.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES),
                         key=lambda path: path.name.casefold())
    if len(shake_paths) != 153:
        raise RuntimeError(f"Expected 153 ShakeFive2 videos, found {len(shake_paths)} under {shake_root}")
    shake_splits = _random_splits(shake_paths, seed)
    for path_index, path in enumerate(shake_paths, start=1):
        group = f"ShakeFive2|{path.stem.casefold()}"
        record, row = _probe_video(path, "ShakeFive2", shake_splits[path], "NonFight", cv2, source_group=group)
        row["split_source"] = f"random_seed_{seed}_80_10_10"
        if record is not None:
            records.append(record)
        manifest_rows.append(row)
        if path_index == 1 or path_index % 25 == 0 or path_index == len(shake_paths):
            if logger:
                logger.info("Preflight ShakeFive2 %d/%d videos", path_index, len(shake_paths))
            if progress_callback:
                progress_callback(stage="读取 ShakeFive2 视频元数据", step=path_index,
                                  steps_total=len(shake_paths), stage_progress=path_index / len(shake_paths))

    for dataset in DATASET_NAMES:
        dataset_records = [record for record in records if record.dataset == dataset]
        if not dataset_records:
            raise RuntimeError(f"No eligible records found for {dataset}")
        if {record.split for record in dataset_records} != set(SPLITS):
            raise RuntimeError(f"Dataset {dataset} does not have non-empty train/val/test splits.")
    if {record.label for record in records if record.split == "train"} != {0, 1}:
        raise RuntimeError("Training split must contain AIRT Lab Fight and NonFight bags.")
    if {record.label for record in records if record.split == "val"} != {0, 1}:
        raise RuntimeError("Validation split must contain AIRT Lab Fight and NonFight bags.")
    if {record.label for record in records if record.split == "test"} != {0, 1}:
        raise RuntimeError("Test split must contain AIRT Lab Fight and NonFight bags.")
    summary = summarize(records, manifest_rows)
    return records, manifest_rows, {"summary": summary, "base_manifest": str(base_manifest.resolve()),
                                    "base_manifest_sha256": hashlib.sha256(base_manifest.read_bytes()).hexdigest(),
                                    "dancetrack_manifest": str(dancetrack_manifest.resolve()),
                                    "dancetrack_manifest_sha256": hashlib.sha256(dancetrack_manifest.read_bytes()).hexdigest(),
                                    "split_policy": "AIRTLab exact base-run manifest; DanceTrack official shard mapping; Dance Genre Estimation genre-stratified 80:10:10; ShakeFive2 deterministic random 80:10:10",
                                    "negative_source_label_note": "DanceTrack, Dance Genre Estimation, and ShakeFive2 are treated as NonFight candidate samples for this experiment; this is an experimental label assignment, not an assertion about official violence annotations."}


def summarize(records: Sequence[core.VideoRecord], manifest_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    datasets: dict[str, Any] = {}
    for dataset in DATASET_NAMES:
        row: dict[str, Any] = {split: {"Fight": 0, "NonFight": 0, "bags": 0, "windows": 0} for split in SPLITS}
        for record in records:
            if record.dataset != dataset:
                continue
            entry = row[record.split]
            entry[record.class_name] += 1
            entry["bags"] += 1
            entry["windows"] += len(four.make_four_second_windows(record.duration, record.fps, record.frame_count))
        excluded = sum(1 for item in manifest_rows if item.get("dataset") == dataset and str(item.get("eligible")) in {"0", "False"})
        row["excluded_shorter_than_1s"] = excluded
        row["total_bags"] = sum(row[split]["bags"] for split in SPLITS)
        datasets[dataset] = row
    overall = {split: {"Fight": 0, "NonFight": 0, "bags": 0, "windows": 0} for split in SPLITS}
    for dataset in datasets.values():
        for split in SPLITS:
            for key in overall[split]:
                overall[split][key] += dataset[split][key]
    return {"datasets": datasets, "overall": overall,
            "total_bags": sum(overall[split]["bags"] for split in SPLITS),
            "eligible_manifest_bags": sum(1 for row in manifest_rows if str(row.get("eligible", 1)) in {"1", "True"})}


def quick_inventory(dataset_root: Path = DATA_ROOT, base_manifest: Path = DEFAULT_AIR_MANIFEST,
                    dancetrack_manifest: Path = DEFAULT_DANCETRACK_MANIFEST, seed: int = 2026) -> dict[str, Any]:
    """Low-latency dashboard summary; full media decoding happens in child preflight."""
    if not base_manifest.is_file() or not dancetrack_manifest.is_file():
        raise FileNotFoundError("Required AIRT Lab or DanceTrack split manifest is missing.")
    base_rows = [row for row in _read_csv(base_manifest) if row.get("dataset") == "AIRTLab" and row.get("eligible") == "1"]
    if len(base_rows) != 350:
        raise RuntimeError(f"Expected 350 eligible AIRT Lab rows in the base manifest; found {len(base_rows)}")
    records: list[dict[str, Any]] = []
    for row in base_rows:
        records.append({"dataset": "AIRTLab", "split": row["split"], "class_name": row["class_name"], "label": int(row["label"])})
    dancetrack = json.loads(dancetrack_manifest.read_text(encoding="utf-8"))
    if dancetrack.get("protocol") != "4S16F2" or len(dancetrack.get("sequences", [])) != 100:
        raise RuntimeError("The prepared DanceTrack 4S16F2 manifest is missing or inconsistent.")
    for item in dancetrack["sequences"]:
        split = dancetrack.get("split_mapping", {}).get(str(item.get("source_shard", "")))
        if split not in SPLITS:
            raise ValueError(f"Invalid DanceTrack split shard: {item.get('source_shard')}")
        records.append({"dataset": "DanceTrack", "split": split, "class_name": "NonFight", "label": 0})
    dge_root = dataset_root / "dance_genre_estimation" / "dance_genre_estimation"
    dge_paths = sorted((path for path in dge_root.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES),
                       key=lambda path: path.name.casefold()) if dge_root.is_dir() else []
    if len(dge_paths) != 210:
        raise RuntimeError(f"Expected 210 Dance Genre Estimation videos, found {len(dge_paths)}")
    for path, split in _stratified_dge_splits(dge_paths, seed).items():
        records.append({"dataset": "Dance Genre Estimation", "split": split, "class_name": "NonFight", "label": 0})
    shake_root = dataset_root / "ShakeFive2.videos"
    shake_paths = sorted((path for path in shake_root.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES),
                         key=lambda path: path.name.casefold()) if shake_root.is_dir() else []
    if len(shake_paths) != 153:
        raise RuntimeError(f"Expected 153 ShakeFive2 videos, found {len(shake_paths)}")
    for path, split in _random_splits(shake_paths, seed).items():
        records.append({"dataset": "ShakeFive2", "split": split, "class_name": "NonFight", "label": 0})
    summary: dict[str, Any] = {"datasets": {}, "overall": {split: {"Fight": 0, "NonFight": 0, "bags": 0} for split in SPLITS}}
    for dataset in DATASET_NAMES:
        by_split = {split: {"Fight": 0, "NonFight": 0, "bags": 0} for split in SPLITS}
        for row in records:
            if row["dataset"] == dataset:
                entry = by_split[row["split"]]
                entry[row["class_name"]] += 1
                entry["bags"] += 1
        by_split["total_bags"] = sum(by_split[split]["bags"] for split in SPLITS)
        summary["datasets"][dataset] = by_split
        for split in SPLITS:
            for key in summary["overall"][split]:
                summary["overall"][split][key] += by_split[split][key]
    summary["total_bags"] = sum(summary["overall"][split]["bags"] for split in SPLITS)
    summary["base_manifest_sha256"] = hashlib.sha256(base_manifest.read_bytes()).hexdigest()
    summary["dancetrack_manifest_sha256"] = hashlib.sha256(dancetrack_manifest.read_bytes()).hexdigest()
    summary["split_policy"] = "AIRTLab exact base-run manifest; DanceTrack official shard mapping; Dance Genre Estimation genre-stratified 80:10:10; ShakeFive2 deterministic random 80:10:10"
    return summary


def _prepare_dancetrack_cache(records: Sequence[core.VideoRecord], cache_dir: Path, cv2: Any, np: Any,
                              logger: logging.Logger, update_status, *, cache_budget_bytes: int) -> None:
    samples = [record for record in records if record.dataset == "DanceTrack"]
    root = cache_dir / "dancetrack"
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    reused = created = uncached = 0
    used_bytes = sum(path.stat().st_size for path in root.glob("*.npz") if path.is_file())
    for index, record in enumerate(samples, start=1):
        required = tuple(sorted(record.required_frame_indices))
        frame_paths = {frame_index: record.path / "img1" / f"{frame_index + 1:08d}.jpg" for frame_index in required}
        record.legacy_cache_path = None
        record.required_frame_indices = required
        missing = [path for path in frame_paths.values() if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"DanceTrack retained input frame is missing: {missing[0]}")
        source_signature = [f"{frame_index}:{frame_paths[frame_index].stat().st_size}:{frame_paths[frame_index].stat().st_mtime_ns}"
                            for frame_index in required]
        cache_key = hashlib.sha256((PROTOCOL_ID + "|" + record.identity + "|" + "|".join(source_signature)).encode("utf-8")).hexdigest()
        cache_path = root / f"{cache_key}.npz"
        record.supplement_cache_path = cache_path
        record.cache_path = cache_path
        valid = False
        if cache_path.is_file():
            try:
                with np.load(cache_path, allow_pickle=False) as saved:
                    ids, frames = saved["frame_indices"].astype(np.int64), saved["frames"]
                    valid = (ids.tolist() == list(required) and frames.ndim == 4 and len(ids) == len(frames)
                             and frames.shape[-1] == 3 and frames.dtype == np.uint8
                             and frames.shape[1] >= core.CROP_SIZE and frames.shape[2] >= core.CROP_SIZE)
            except (OSError, ValueError, KeyError):
                valid = False
        if valid:
            reused += 1
        elif used_bytes >= cache_budget_bytes:
            # Do not even build a temporary archive if the reserved disk budget is exhausted.
            record.supplement_cache_path = None
            record.cache_path = None
            uncached += 1
        else:
            images = []
            for frame_index in required:
                bgr = cv2.imread(str(frame_paths[frame_index]), cv2.IMREAD_COLOR)
                if bgr is None:
                    raise RuntimeError(f"OpenCV could not read DanceTrack input frame: {frame_paths[frame_index]}")
                images.append(core._resize_short_side(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), cv2))
            # Use the shared short sibling temp name: DanceTrack cache keys are
            # long too, and appending them to the final name can exceed the
            # Windows path limit under the full-frame cache profile.
            temporary = four._cache_write_temp_path(cache_path)
            prior_size = cache_path.stat().st_size if cache_path.is_file() else 0
            try:
                with temporary.open("wb") as handle:
                    np.savez_compressed(handle, frame_indices=np.asarray(required, dtype=np.int32),
                                        frames=np.stack(images).astype(np.uint8, copy=False))
                written_size = temporary.stat().st_size
                if used_bytes - prior_size + written_size <= cache_budget_bytes:
                    os.replace(temporary, cache_path)
                    used_bytes = used_bytes - prior_size + written_size
                else:
                    # Keep the original retained JPGs untouched and fall back to on-demand reads.
                    record.supplement_cache_path = None
                    record.cache_path = None
                    uncached += 1
            finally:
                temporary.unlink(missing_ok=True)
            if record.supplement_cache_path is not None:
                created += 1
        if index == 1 or index % 10 == 0 or index == len(samples):
            elapsed = time.monotonic() - started
            eta = elapsed / index * (len(samples) - index) if index else 0.0
            logger.info("DanceTrack frame cache %d/%d | reused=%d new=%d on-demand=%d | used=%.2f/%.2f GiB | elapsed=%.1fs | ETA=%.1fs",
                        index, len(samples), reused, created, uncached, used_bytes / (1024**3),
                        cache_budget_bytes / (1024**3), elapsed, eta)
            update_status(stage="准备 DanceTrack 帧缓存", step=index, steps_total=len(samples),
                          stage_progress=index / max(1, len(samples)), eta_seconds=eta)
    logger.info("DanceTrack frame cache ready | records=%d | reused=%d | created=%d | on-demand=%d | bytes=%d/%d | path=%s",
                len(samples), reused, created, uncached, used_bytes, cache_budget_bytes, root)


def _prepare_video_cache(records: Sequence[core.VideoRecord], cache_dir: Path, cv2: Any, np: Any,
                         logger: logging.Logger, update_status, *, cache_budget_bytes: int) -> None:
    videos = [record for record in records if record.dataset != "DanceTrack"]
    if not videos:
        raise RuntimeError("No video-file records were available for frame caching.")
    four.LEGACY_CACHE_ROOT = DEFAULT_TWO_SECOND_CACHE
    four.SUPPLEMENT_CACHE_ROOT = cache_dir / "videos"
    prior_roots = four.READ_ONLY_SUPPLEMENT_CACHE_ROOTS
    base_four_cache = _base_four_second_supplement_cache_root()
    four.READ_ONLY_SUPPLEMENT_CACHE_ROOTS = (base_four_cache,) if base_four_cache.is_dir() else ()
    existing_bytes = four._cache_size_bytes(four.SUPPLEMENT_CACHE_ROOT)
    original_cap = four.MAX_SUPPLEMENT_CACHE_BYTES
    four.MAX_SUPPLEMENT_CACHE_BYTES = max(existing_bytes, cache_budget_bytes)
    class CacheProgressHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            message = record.getMessage()
            if not message.startswith("Hybrid frame cache "):
                return
            fields = message.split()
            if len(fields) < 4 or "/" not in fields[3]:
                return
            try:
                step, total = (int(value) for value in fields[3].split("/", 1))
            except ValueError:
                return
            update_status(stage="准备视频帧缓存", step=step, steps_total=total,
                          stage_progress=step / max(1, total))

    progress_handler = CacheProgressHandler()
    logger.addHandler(progress_handler)
    try:
        # Reuse the exact 4S16F2 cache recorded by the Best run, along with the
        # older 2S cache; newly missing frames go only to this run's cache.
        logger.info("Read-only base 4S16F2 cache=%s | available=%s | files=%d",
                    base_four_cache, base_four_cache.is_dir(), four._cache_size_bytes(base_four_cache))
        logger.info("Video-cache write budget=%.2f GiB (existing cache is retained; over-budget frames decode on demand).",
                    four.MAX_SUPPLEMENT_CACHE_BYTES / (1024**3))
        four._prepare_hybrid_cache(videos, four.SUPPLEMENT_CACHE_ROOT, cv2, np, logger, rebuild=False)
    finally:
        logger.removeHandler(progress_handler)
        four.MAX_SUPPLEMENT_CACHE_BYTES = original_cap
        four.READ_ONLY_SUPPLEMENT_CACHE_ROOTS = prior_roots
    update_status(stage="视频帧缓存完成", step=len(videos), steps_total=len(videos), stage_progress=1.0)


def _load_native_cache(record: core.VideoRecord, np: Any) -> tuple[Any, dict[int, int]]:
    if record.dataset != "DanceTrack":
        return four._load_hybrid_cache(record, np)
    required = tuple(getattr(record, "required_frame_indices", ()))
    path = getattr(record, "supplement_cache_path", None)
    if path is not None and Path(path).is_file():
        try:
            with np.load(path, allow_pickle=False) as saved:
                indices = saved["frame_indices"].astype(np.int64)
                frames = saved["frames"].copy()
            if indices.tolist() == list(required) and frames.ndim == 4 and len(indices) == len(frames) and frames.shape[-1] == 3:
                return frames, {int(frame_index): index for index, frame_index in enumerate(indices.tolist())}
        except (OSError, ValueError, KeyError):
            pass
    import cv2
    images = []
    for frame_index in required:
        frame_path = record.path / "img1" / f"{frame_index + 1:08d}.jpg"
        bgr = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
        if bgr is None:
            raise RuntimeError(f"OpenCV could not read DanceTrack frame on demand: {frame_path}")
        images.append(core._resize_short_side(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), cv2))
    frames = np.stack(images).astype(np.uint8, copy=False)
    return frames, {int(frame_index): index for index, frame_index in enumerate(required)}


def _install_runtime_protocol(cache_root: Path) -> None:
    core.WINDOW_SECONDS = four.WINDOW_SECONDS
    core.STRIDE_SECONDS = four.STRIDE_SECONDS
    core.NUM_FRAMES = four.NUM_FRAMES
    core.CACHE_PROTOCOL = four.PROTOCOL
    core._preprocessing_protocol = four._protocol
    core._windows_for_record = four._windows_for_record
    core._load_cache = four._load_hybrid_cache
    four.SUPPLEMENT_CACHE_ROOT = cache_root / "videos"


def _experiment_protocol() -> dict[str, Any]:
    protocol = dict(four._protocol())
    protocol.update({
        "selected_datasets": list(DATASET_NAMES),
        "legacy_datasets": [],
        "source_roots": {
            "dataset_root": str(DATA_ROOT.resolve()),
            "airtlab": str((DATA_ROOT / "AIRTLab" / "A-Dataset-for-Automatic-Violence-Detection-in-Videos-master"
                             / "violence-detection-dataset").resolve()),
            "dancetrack": str((DATA_ROOT / "DanceTrack").resolve()),
            "dance_genre_estimation": str((DATA_ROOT / "dance_genre_estimation" / "dance_genre_estimation").resolve()),
            "shakefive2": str((DATA_ROOT / "ShakeFive2.videos").resolve()),
        },
        "airtlab_split": {
            "rule": "copy the exact train/val/test assignments from the audited native 4S16F2 Best run manifest",
            "source_manifest": str(DEFAULT_AIR_MANIFEST.resolve()),
            "source_manifest_sha256": hashlib.sha256(DEFAULT_AIR_MANIFEST.read_bytes()).hexdigest(),
            "camera_pairing": "preserved source_group; both camera views remain in the prior split",
        },
        "dancetrack_split": "official train1+train2=train, val=val, test1+test2=test mapping from retained-frame manifest",
        "dance_genre_estimation_split": "genre-prefix stratified 80/10/10; deterministic seed 2026",
        "shakefive2_split": "deterministic random 80/10/10; seed 2026",
        "short_clip_policy": {
            "duration_below_seconds": four.WINDOW_SECONDS,
            "operation": "sample 16 frame centres uniformly across natural clip duration; no reverse or padding",
            "minimum_duration_seconds": four.MIN_SCAN_SECONDS,
        },
        "tail_policy": "full 4-second windows at 2-second stride, with one end-aligned tail when needed",
        "negative_label_policy": "DanceTrack, Dance Genre Estimation, and ShakeFive2 are assigned NonFight candidate labels for this experiment; not claims about official violence annotations",
    })
    return protocol


def _dataset_fingerprint(records: Sequence[core.VideoRecord], manifest_path: Path) -> str:
    parts = [hashlib.sha256(manifest_path.read_bytes()).hexdigest()]
    for record in records:
        parts.append(f"{record.dataset}|{record.split}|{record.label}|{record.path}|{record.size_bytes}|{record.mtime_ns}|{record.fps:.8f}|{record.frame_count}")
        if record.dataset == "DanceTrack":
            for frame_index in record.required_frame_indices:
                frame = record.path / "img1" / f"{frame_index + 1:08d}.jpg"
                stat = frame.stat()
                parts.append(f"frame|{record.path.name}|{frame_index}|{stat.st_size}|{stat.st_mtime_ns}")
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()


def _threshold_metrics(predictions: Sequence[dict[str, Any]], thresholds: dict[str, dict[str, float]]) -> dict[str, Any]:
    return core._mode_metrics(predictions, thresholds)


def _write_live(path: Path, values: dict[str, Any]) -> None:
    current = _read_json(path) or {}
    payload = {**current, **values, "updated_at": datetime.now().isoformat(timespec="seconds")}

    def json_safe(value: Any) -> Any:
        # Before the first validation pass, best AUC is intentionally -inf.
        # Keep strict JSON output and represent any unavailable metric as null.
        if isinstance(value, bool):
            return value
        if isinstance(value, numbers.Integral):
            return int(value)
        if isinstance(value, numbers.Real):
            numeric = float(value)
            return numeric if math.isfinite(numeric) else None
        if isinstance(value, dict):
            return {key: json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_safe(item) for item in value]
        return value

    _atomic_json(path, json_safe(payload))


def _video_cache_budget_from_free_bytes(free_bytes: int) -> int:
    """Apply the configured cap while retaining disk reserve and headroom."""
    safe_budget = max(0, int(free_bytes) - MIN_FREE_DISK_RESERVE_BYTES)
    return min(VIDEO_CACHE_CAP_BYTES, int(safe_budget * 0.65))


def _metrics_with_rates(metrics: dict[str, Any]) -> dict[str, Any]:
    value = dict(metrics)
    value["false_positive_rate"] = metrics["fp"] / (metrics["fp"] + metrics["tn"]) if metrics["fp"] + metrics["tn"] else 0.0
    value["false_negative_rate"] = metrics["fn"] / (metrics["fn"] + metrics["tp"]) if metrics["fn"] + metrics["tp"] else 0.0
    return value


def _per_dataset_at_half(predictions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    output = {}
    for dataset in DATASET_NAMES:
        subset = [row for row in predictions if row["dataset"] == dataset]
        if not subset:
            continue
        metrics = core.binary_metrics([int(row["label"]) for row in subset],
                                      [float(row["probability_fight"]) for row in subset], 0.5)
        output[dataset] = _metrics_with_rates(metrics)
    return output


def _evaluate(records: Sequence[core.VideoRecord], split: str, *, model: Any, epoch: int, seed: int,
              np: Any, cv2: Any, torch: Any, device: Any, amp: bool, batch_size: int,
              logger: logging.Logger, live_path: Path, epoch_started: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected = [record for record in records if record.split == split]
    predictions: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []
    stage_label = "验证" if split == "val" else "测试"
    started = time.monotonic()
    for index, record in enumerate(selected, start=1):
        clips, windows = core._record_bag_tensor(record, training=False, epoch=epoch, seed=seed,
                                                 np=np, cv2=cv2, torch=torch)
        bag_probability, window_probabilities = core._predict_bag(model, clips, torch, device,
                                                                  amp=amp, window_batch_size=batch_size)
        predictions.append({"dataset": record.dataset, "split": split, "class_name": record.class_name,
                            "label": record.label, "path": str(record.path), "source_group": record.source_group,
                            "duration_seconds": record.duration, "window_count": len(windows),
                            "probability_fight": bag_probability})
        for window, probability in zip(windows, window_probabilities):
            window_rows.append({"dataset": record.dataset, "split": split, "class_name": record.class_name,
                                "label": record.label, "path": str(record.path), "start_seconds": window.start,
                                "end_seconds": window.end, "probability_fight": probability})
        if index == 1 or index % 10 == 0 or index == len(selected):
            elapsed = time.monotonic() - started
            eta = elapsed / index * (len(selected) - index) if index else 0.0
            logger.info("%s epoch=%d %d/%d bags | %.1f%% | elapsed=%.1fs | ETA=%.1fs",
                        stage_label, epoch, index, len(selected), 100 * index / max(1, len(selected)), elapsed, eta)
            _write_live(live_path, {"status": "running", "stage": f"{stage_label}中", "epoch": epoch,
                                    "total_epochs": int((_read_json(live_path) or {}).get("total_epochs", 15)),
                                    "step": index, "steps_total": len(selected),
                                    "stage_progress": index / max(1, len(selected)), "eta_seconds": eta,
                                    "elapsed_seconds": time.monotonic() - epoch_started,
                                    "latest_split_metrics": _metrics_with_rates(core.binary_metrics(
                                        [int(item["label"]) for item in predictions],
                                        [float(item["probability_fight"]) for item in predictions], 0.5))
                                    if len({item["label"] for item in predictions}) == 2 else {"bags_processed": index}})
    return predictions, window_rows


def _save_deployment(model: Any, path: Path, torch: Any, epoch: int, auc: float,
                     thresholds: dict[str, dict[str, float]]) -> None:
    payload = {"deployment_checkpoint_version": 1, "model_name": "mc3_18",
               "state_dict": {key: tensor.detach().cpu() for key, tensor in model.state_dict().items()},
               "class_to_idx": core.CLASS_TO_LABEL,
               "protocol": {**_experiment_protocol(), "experiment_protocol": PROTOCOL_ID,
                            "input_modality": "native", "input_representation": INPUT_REPRESENTATION,
                            "best_epoch": epoch, "best_validation_roc_auc": auc,
                            "calibrated_thresholds": thresholds,
                            "datasets": list(DATASET_NAMES)}}
    core._atomic_torch_save(payload, path, torch)


def _save_last(path: Path, *, model: Any, optimizer: Any, scheduler: Any, scaler: Any,
               torch: Any, np: Any, epoch: int, best_epoch: int, best_auc: float,
               patience_best_auc: float, bad_epochs: int, history: list[dict[str, Any]],
               fingerprint: str, config: dict[str, Any]) -> None:
    core._atomic_torch_save({"native_finetune_checkpoint_version": 1, "experiment_protocol": PROTOCOL_ID,
                             "model_state": model.state_dict(), "optimizer_state": optimizer.state_dict(),
                             "scheduler_state": scheduler.state_dict(), "scaler_state": scaler.state_dict(),
                             "completed_epoch": epoch, "best_epoch": best_epoch, "best_auc": best_auc,
                             "patience_best_auc": patience_best_auc, "bad_epochs": bad_epochs,
                             "history": history, "dataset_fingerprint": fingerprint, "config": config,
                             "rng_state": core._state_rng(torch, np),
                             "saved_at": datetime.now().isoformat(timespec="seconds")}, path, torch)


def _load_checkpoint(path: Path, torch: Any) -> dict[str, Any]:
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def _run_training(args: argparse.Namespace) -> Path:
    run_dir = Path(args.run_dir).resolve()
    resume_payload = None
    if args.resume:
        resume_path = Path(args.resume).resolve()
        if resume_path.name != "last.pt" or resume_path.parent != run_dir:
            raise ValueError("Resume checkpoint must be this run's last.pt.")
        if (run_dir / "final_report.json").is_file():
            raise RuntimeError("This run already completed final testing; start a new run instead.")
        import torch
        resume_payload = _load_checkpoint(resume_path, torch)
        if resume_payload.get("native_finetune_checkpoint_version") != 1:
            raise RuntimeError("Unsupported native-video MC3-18 training resume checkpoint.")
    elif run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(f"Run directory already contains files: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    logger = core._log_setup(run_dir, resume=resume_payload is not None)
    logger.propagate = False
    initialization_mode = getattr(args, "initialization_mode", "base-checkpoint")
    logger.info("Native 4S16F2 training | %s | initialization=%s",
                PROTOCOL_ID, initialization_mode)
    cv2, np, torch, nn, model_symbols = core._import_runtime_dependencies()
    weights_enum, model_builder = model_symbols
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but no CUDA device is available.")
    if args.device == "cpu" and not args.allow_cpu:
        raise RuntimeError("CPU training requires explicit --allow-cpu confirmation.")
    device = torch.device("cuda:0" if args.device == "cuda" else "cpu")
    amp = device.type == "cuda" and not args.no_amp
    if initialization_mode == "base-checkpoint" and not Path(args.base_checkpoint).is_file():
        raise FileNotFoundError(f"Best Checkpoint is missing: {args.base_checkpoint}")
    logger.info("Runtime | device=%s | AMP=%s", device, amp)
    _install_runtime_protocol(Path(args.cache_dir).resolve())
    _write_live(run_dir / "live_status.json", {"status": "running", "stage": "核验数据划分与采样帧",
                                                "epoch": 0, "total_epochs": args.epochs,
                                                "stage_progress": 0.0, "started_at": time.time()})
    dancetrack_manifest_path = str(args.dancetrack_manifest or "").strip()
    record_load_args = {
        "dataset_root": Path(args.dataset_root),
        "base_manifest": Path(args.base_manifest),
        "seed": args.seed,
        "cv2": cv2,
        "logger": logger,
        "validate_dance_frames": True,
        "progress_callback": lambda **values: _write_live(run_dir / "live_status.json", values),
    }
    if dancetrack_manifest_path:
        record_load_args["dancetrack_manifest"] = Path(dancetrack_manifest_path)
    records, manifest_rows, data_meta = load_records(**record_load_args)
    _write_csv(run_dir / "dataset_manifest.csv", manifest_rows)
    fingerprint = _dataset_fingerprint(records, run_dir / "dataset_manifest.csv")
    cache_root = Path(args.cache_dir).resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(cache_root).free
    safe_budget = max(0, free_bytes - MIN_FREE_DISK_RESERVE_BYTES)
    video_cache_budget = _video_cache_budget_from_free_bytes(free_bytes)
    dance_cache_budget = (min(DANCETRACK_CACHE_CAP_BYTES, max(0, safe_budget - video_cache_budget))
                          if dancetrack_manifest_path else 0)
    video_cache_budget = max(video_cache_budget, four._cache_size_bytes(cache_root / "videos"))
    dance_cache_budget = max(dance_cache_budget, four._cache_size_bytes(cache_root / "dancetrack"))
    protocol = _experiment_protocol()
    summary = summarize(records, manifest_rows)
    _atomic_json(run_dir / "dataset_summary.json", {**summary, **data_meta})
    if initialization_mode == "kinetics400":
        kinetics_weights = weights_enum.KINETICS400_V1
        initialization = {
            "kind": "torchvision_mc3_18_kinetics400",
            "weights": "MC3_18_Weights.KINETICS400_V1",
            "url": kinetics_weights.url,
            "classifier": "replace Kinetics-400 classifier with a randomly initialized Fight/NonFight head",
        }
    else:
        initialization = {
            "kind": "deployment_checkpoint",
            "path": str(Path(args.base_checkpoint).resolve()),
        }
    config = {"experiment_protocol": PROTOCOL_ID, "input_representation": INPUT_REPRESENTATION,
              "initialization_mode": initialization_mode, "initialization": initialization,
              "seed": args.seed, "epochs": args.epochs, "patience": args.patience,
              "min_delta": args.min_delta, "backbone_lr": args.backbone_lr, "head_lr": args.head_lr,
              "lr_scheduler": "ReduceLROnPlateau", "lr_scheduler_factor": args.lr_factor,
              "lr_scheduler_no_improvement_epochs": args.lr_reduce_after, "min_lr": args.min_lr,
              "weight_decay": args.weight_decay, "grad_accum": args.grad_accum,
              "window_batch_size": args.window_batch_size, "amp": amp, "device": str(device),
              "protocol": INPUT_PROTOCOL_ID, "preprocessing": protocol, "dataset_root": str(Path(args.dataset_root).resolve()),
              "base_manifest": str(Path(args.base_manifest).resolve()),
              "base_manifest_sha256": data_meta["base_manifest_sha256"],
              "cache_dir": str(cache_root), "split_policy": data_meta["split_policy"],
              "cache_reuse": ({**CACHE_REUSE_METADATA,
                               "new_supplemental_cache": str((cache_root / "videos").resolve())}
                              if CACHE_REUSE_METADATA is _DEFAULT_CACHE_REUSE_METADATA else
                              {**CACHE_REUSE_METADATA, "cache_root": str(cache_root.resolve())}),
              "split_counts": summary, "dataset_fingerprint": fingerprint,
              "cache_space_policy": {"free_bytes_at_preflight": free_bytes,
                                     "reserve_bytes": MIN_FREE_DISK_RESERVE_BYTES,
                                     "video_cache_budget_bytes": video_cache_budget,
                                     "dancetrack_cache_budget_bytes": dance_cache_budget,
                                     "over_budget_policy": "reuse existing; decode directly/on demand rather than exceeding budget"}}
    if initialization_mode == "base-checkpoint":
        config["initial_checkpoint_sha256"] = hashlib.sha256(
            Path(args.base_checkpoint).read_bytes()).hexdigest()
    if resume_payload:
        if resume_payload.get("dataset_fingerprint") != fingerprint:
            raise RuntimeError("Dataset files or split manifest changed since the last checkpoint; refusing unsafe resume.")
        previous = resume_payload.get("config", {})
        compare_keys = ("seed", "epochs", "patience", "min_delta", "backbone_lr", "head_lr", "lr_scheduler_factor",
                        "lr_scheduler_no_improvement_epochs", "min_lr", "weight_decay", "grad_accum",
                        "window_batch_size", "amp", "device", "protocol", "input_representation")
        if initialization_mode == "base-checkpoint":
            compare_keys += ("initial_checkpoint_sha256",)
        else:
            compare_keys += ("initialization_mode", "initialization")
        mismatch = []
        for key in compare_keys:
            previous_value = previous.get(key)
            # Older native fine-tuning runs predate this explicit config key;
            # their saved protocol was always the ordinary RGB-video path.
            if key == "input_representation" and previous_value is None:
                previous_value = "native_video"
            if previous_value != config.get(key):
                mismatch.append(key)
        if mismatch:
            raise RuntimeError("Resume settings differ from the saved run: " + ", ".join(mismatch))
    _atomic_json(run_dir / "config.json", config)
    logger.info("Split lock | manifest SHA-256=%s | source groups paired=%s", hashlib.sha256((run_dir / "dataset_manifest.csv").read_bytes()).hexdigest(), True)
    _write_live(run_dir / "live_status.json", {"status": "running", "stage": "准备视频帧缓存", "epoch": 0,
                                                "total_epochs": args.epochs, "stage_progress": 0.0,
                                                "sample_total": len(records)})
    _prepare_video_cache(records, cache_root, cv2, np, logger,
                         lambda **values: _write_live(run_dir / "live_status.json", values),
                         cache_budget_bytes=video_cache_budget)
    if dancetrack_manifest_path:
        config["dancetrack_manifest"] = str(Path(dancetrack_manifest_path).resolve())
        config["dancetrack_manifest_sha256"] = data_meta["dancetrack_manifest_sha256"]
        config["cache_reuse"]["dancetrack_cache"] = str((cache_root / "dancetrack").resolve())
        _prepare_dancetrack_cache(records, cache_root, cv2, np, logger,
                                  lambda **values: _write_live(run_dir / "live_status.json", values),
                                  cache_budget_bytes=dance_cache_budget)
    core._load_cache = _load_native_cache

    core._set_seed(args.seed, torch, np)
    model = core._build_model(torch, nn, weights_enum, model_builder, device,
                              pretrained=(initialization_mode == "kinetics400" and resume_payload is None))
    if resume_payload:
        model.load_state_dict(resume_payload["model_state"])
    elif initialization_mode == "base-checkpoint":
        base = _load_checkpoint(Path(args.base_checkpoint), torch)
        if (base.get("deployment_checkpoint_version") != 1 or base.get("model_name") != "mc3_18"
                or base.get("class_to_idx") != core.CLASS_TO_LABEL):
            raise RuntimeError("The selected Best Checkpoint is not a supported MC3-18 deployment model.")
        if base.get("protocol", {}).get("protocol") != four.PROTOCOL:
            raise RuntimeError("The Best Checkpoint is not the native 4S16F2 protocol.")
        model.load_state_dict(base["state_dict"], strict=True)
        logger.info("Initialized from the audited base Best Checkpoint.")
    else:
        logger.info("Initialized from TorchVision MC3-18 Kinetics-400 pretrained weights: %s",
                    weights_enum.KINETICS400_V1.url)
    optimizer = torch.optim.AdamW([
        {"params": [parameter for name, parameter in model.named_parameters() if not name.startswith("fc.")],
         "lr": args.backbone_lr},
        {"params": list(model.fc.parameters()), "lr": args.head_lr},
    ], weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=args.lr_factor, patience=args.lr_reduce_after - 1, min_lr=args.min_lr)
    scaler = core._make_scaler(torch, amp)
    start_epoch, best_epoch, best_auc, patience_best_auc, bad_epochs = 1, 0, -float("inf"), -float("inf"), 0
    history: list[dict[str, Any]] = []
    if resume_payload:
        optimizer.load_state_dict(resume_payload["optimizer_state"])
        scheduler.load_state_dict(resume_payload["scheduler_state"])
        scaler.load_state_dict(resume_payload.get("scaler_state", {}))
        start_epoch = int(resume_payload["completed_epoch"]) + 1
        best_epoch, best_auc = int(resume_payload["best_epoch"]), float(resume_payload["best_auc"])
        patience_best_auc = float(resume_payload["patience_best_auc"])
        bad_epochs = int(resume_payload["bad_epochs"])
        history = list(resume_payload["history"])
        core._restore_rng(resume_payload["rng_state"], torch, np)
        logger.info("Resume after epoch %d; next epoch %d", start_epoch - 1, start_epoch)
    elif (run_dir / "last.pt").exists():
        raise FileExistsError("Run directory has a last.pt but no resume was requested.")

    train_records = [record for record in records if record.split == "train"]
    criterion = nn.CrossEntropyLoss()
    live_path = run_dir / "live_status.json"
    for epoch in range(start_epoch, args.epochs + 1):
        epoch_started = time.monotonic()
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
        train_probabilities: list[float] = []
        logger.info("EPOCH %d/%d START | train bags=%d", epoch, args.epochs, len(ordered))
        _write_live(live_path, {"status": "running", "stage": "训练中", "epoch": epoch,
                                "total_epochs": args.epochs, "step": 0, "steps_total": len(ordered),
                                "stage_progress": 0.0, "completed_epochs": epoch - 1,
                                "best_epoch": best_epoch, "best_val_roc_auc": best_auc,
                                "patience_used": bad_epochs, "patience": args.patience})
        for index, record in enumerate(ordered, start=1):
            clips, _ = core._record_bag_tensor(record, training=True, epoch=epoch, seed=args.seed,
                                                np=np, cv2=cv2, torch=torch)
            hard_index, _ = core._choose_hard_instance(model, clips, torch, device,
                                                       amp=amp, window_batch_size=args.window_batch_size)
            selected = clips[hard_index:hard_index + 1].to(device, non_blocking=True)
            target = torch.tensor([record.label], dtype=torch.long, device=device)
            with core._amp_context(torch, device, amp):
                logits = model(selected)
                loss = criterion(logits, target)
            scaler.scale(loss / args.grad_accum).backward()
            accumulated += 1
            total_loss += float(loss.detach().float().item())
            train_probabilities.append(float(torch.softmax(logits.detach().float(), dim=1)[0, 1].item()))
            train_labels.append(record.label)
            if accumulated == args.grad_accum:
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad(set_to_none=True)
                accumulated = 0
            if index == 1 or index % args.log_every == 0 or index == len(ordered):
                elapsed = time.monotonic() - epoch_started
                eta = elapsed / index * (len(ordered) - index) if index else 0.0
                train_metric = None
                if len(set(train_labels)) == 2:
                    train_metric = _metrics_with_rates(core.binary_metrics(train_labels, train_probabilities, 0.5))
                logger.info("epoch %d/%d train %d/%d bags | loss=%.5f | train_AUC=%s | LR=%.3g/%.3g | ETA=%.1fs",
                            epoch, args.epochs, index, len(ordered), total_loss / index,
                            f"{train_metric['roc_auc']:.5f}" if train_metric else "pending",
                            optimizer.param_groups[0]["lr"], optimizer.param_groups[1]["lr"], eta)
                _write_live(live_path, {"status": "running", "stage": "训练中", "epoch": epoch,
                                        "total_epochs": args.epochs, "step": index, "steps_total": len(ordered),
                                        "stage_progress": index / max(1, len(ordered)), "train_loss": total_loss / index,
                                        "train_metrics_at_0_5": train_metric, "eta_seconds": eta,
                                        "elapsed_seconds": elapsed, "learning_rate_backbone": optimizer.param_groups[0]["lr"],
                                        "learning_rate_head": optimizer.param_groups[1]["lr"],
                                        "best_epoch": best_epoch, "best_val_roc_auc": best_auc,
                                        "patience_used": bad_epochs, "patience": args.patience})
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
        train_auc = core.roc_auc(train_labels, train_probabilities)
        validation, validation_windows = _evaluate(records, "val", model=model, epoch=epoch, seed=args.seed,
                                                   np=np, cv2=cv2, torch=torch, device=device, amp=amp,
                                                   batch_size=args.window_batch_size, logger=logger,
                                                   live_path=live_path, epoch_started=epoch_started)
        labels = [int(row["label"]) for row in validation]
        probabilities = [float(row["probability_fight"]) for row in validation]
        val_auc = core.roc_auc(labels, probabilities)
        if val_auc is None:
            raise RuntimeError("Validation ROC AUC is undefined: both classes are required.")
        scheduler.step(val_auc)
        val_half = _metrics_with_rates(core.binary_metrics(labels, probabilities, 0.5))
        epoch_thresholds = core.choose_thresholds(labels, probabilities)
        improved = val_auc > best_auc
        significant = val_auc > patience_best_auc + args.min_delta
        if improved:
            best_auc, best_epoch = val_auc, epoch
        if significant:
            patience_best_auc, bad_epochs = val_auc, 0
        else:
            bad_epochs += 1
        epoch_seconds = time.monotonic() - epoch_started
        row = {"epoch": epoch, "train_loss": train_loss, "train_roc_auc": train_auc,
               "val_roc_auc": val_auc, "val_average_precision": core.pr_auc_average_precision(labels, probabilities),
               "val_accuracy_at_0_5": val_half["accuracy"], "val_precision_at_0_5": val_half["precision"],
               "val_recall_at_0_5": val_half["recall"], "val_f1_at_0_5": val_half["f1"],
               "val_f0_5_at_0_5": val_half["f0_5"], "val_f2_at_0_5": val_half["f2"],
               "val_specificity_at_0_5": val_half["specificity"],
               "val_false_positive_rate_at_0_5": val_half["false_positive_rate"],
               "val_false_negative_rate_at_0_5": val_half["false_negative_rate"],
               "val_balanced_accuracy_at_0_5": val_half["balanced_accuracy"],
               "learning_rate_backbone": optimizer.param_groups[0]["lr"],
               "learning_rate_head": optimizer.param_groups[1]["lr"], "seconds": epoch_seconds,
               "improved": int(improved), "patience_counter": bad_epochs,
               "best_epoch": best_epoch, "best_val_roc_auc": best_auc}
        if device.type == "cuda":
            row["peak_gpu_memory_mb"] = torch.cuda.max_memory_allocated(device) / (1024**2)
        history.append(row)
        _write_csv(run_dir / "training_history.csv", history)
        _write_csv(run_dir / f"validation_epoch_{epoch:02d}_predictions.csv", validation)
        _write_csv(run_dir / f"validation_epoch_{epoch:02d}_window_predictions.csv", validation_windows)
        by_dataset = _per_dataset_at_half(validation)
        _atomic_json(run_dir / f"validation_epoch_{epoch:02d}_by_dataset.json", by_dataset)
        epoch_dir = run_dir / f"epoch_{epoch:02d}"
        epoch_dir.mkdir(parents=True, exist_ok=True)
        _save_deployment(model, epoch_dir / "best_model.pt", torch, epoch, val_auc, epoch_thresholds)
        _atomic_json(epoch_dir / "thresholds.json", {"protocol": INPUT_PROTOCOL_ID, "selection": epoch_thresholds,
                                                       "validation_metrics": _threshold_metrics(validation, epoch_thresholds)})
        _atomic_json(epoch_dir / "final_report.json", {"experiment_protocol": PROTOCOL_ID,
                                                         "input_representation": INPUT_REPRESENTATION, "status": "epoch_checkpoint",
                                                         "best_epoch": epoch, "best_validation_roc_auc": val_auc,
                                                         "validation_metrics_at_0_5": val_half,
                                                         "validation_metrics_by_dataset_at_0_5": by_dataset,
                                                         "thresholds": epoch_thresholds})
        if improved:
            _save_deployment(model, run_dir / "best_model.pt", torch, epoch, val_auc, epoch_thresholds)
        _save_last(run_dir / "last.pt", model=model, optimizer=optimizer, scheduler=scheduler, scaler=scaler,
                   torch=torch, np=np, epoch=epoch, best_epoch=best_epoch, best_auc=best_auc,
                   patience_best_auc=patience_best_auc, bad_epochs=bad_epochs, history=history,
                   fingerprint=fingerprint, config=config)
        logger.info("EPOCH %d/%d COMPLETE | train_loss=%.5f | train_AUC=%s | val_AUC=%.6f | val_P/R/F1@0.5=%.4f/%.4f/%.4f | val_FPR=%.4f | LR=%.3g/%.3g | best=%0.6f@%d | early_stop=%d/%d | checkpoint=epoch_%02d/best_model.pt",
                    epoch, args.epochs, train_loss, "n/a" if train_auc is None else f"{train_auc:.6f}", val_auc,
                    val_half["precision"], val_half["recall"], val_half["f1"], val_half["false_positive_rate"],
                    optimizer.param_groups[0]["lr"], optimizer.param_groups[1]["lr"], best_auc, best_epoch,
                    bad_epochs, args.patience, epoch)
        _write_live(live_path, {"status": "running", "stage": "Epoch 已完成", "epoch": epoch,
                                "total_epochs": args.epochs, "completed_epochs": epoch, "stage_progress": 1.0,
                                "train_loss": train_loss, "train_roc_auc": train_auc,
                                "train_metrics_at_0_5": _metrics_with_rates(core.binary_metrics(train_labels, train_probabilities, 0.5)),
                                "val_roc_auc": val_auc, "val_metrics_at_0_5": val_half,
                                "validation_by_dataset": by_dataset, "validation_thresholds": epoch_thresholds,
                                "best_epoch": best_epoch, "best_val_roc_auc": best_auc,
                                "patience_used": bad_epochs, "patience": args.patience,
                                "epoch_seconds": epoch_seconds,
                                "epoch_checkpoint": str(epoch_dir / "best_model.pt")})
        if bad_epochs >= args.patience:
            logger.info("EARLY STOP after epoch %d: validation ROC-AUC did not improve by %.4f for %d epochs.",
                        epoch, args.min_delta, args.patience)
            break
        if core.consume_pause_request(args.pause_file):
            logger.info("SAFE PAUSE after epoch %d. Resume from %s", epoch, run_dir / "last.pt")
            _write_live(live_path, {"status": "paused", "stage": "已在 epoch 边界安全暂停",
                                    "completed_epochs": epoch, "last_checkpoint": str(run_dir / "last.pt")})
            return run_dir

    best_path = run_dir / "best_model.pt"
    if not best_path.is_file():
        raise RuntimeError("No validation-selected Best Checkpoint was produced.")
    best_payload = _load_checkpoint(best_path, torch)
    model.load_state_dict(best_payload["state_dict"])
    best_epoch = int(best_payload["protocol"]["best_epoch"])
    best_auc = float(best_payload["protocol"]["best_validation_roc_auc"])
    logger.info("Locked best model at epoch %d (validation AUC %.6f); now calibrating validation thresholds.", best_epoch, best_auc)
    validation, validation_windows = _evaluate(records, "val", model=model, epoch=best_epoch, seed=args.seed,
                                                np=np, cv2=cv2, torch=torch, device=device, amp=amp,
                                                batch_size=args.window_batch_size, logger=logger,
                                                live_path=live_path, epoch_started=time.monotonic())
    val_labels = [int(row["label"]) for row in validation]
    val_probabilities = [float(row["probability_fight"]) for row in validation]
    thresholds = core.choose_thresholds(val_labels, val_probabilities)
    val_metrics = _threshold_metrics(validation, thresholds)
    _write_csv(run_dir / "validation_predictions.csv", validation)
    _write_csv(run_dir / "validation_window_predictions.csv", validation_windows)
    _atomic_json(run_dir / "thresholds.json", {"protocol": INPUT_PROTOCOL_ID, "selection": thresholds,
                                                  "validation_metrics": val_metrics})
    # Re-save the global Best with final locked validation thresholds.
    _save_deployment(model, best_path, torch, best_epoch, best_auc, thresholds)
    logger.info("Thresholds selected using validation only: %s", json.dumps(thresholds, ensure_ascii=False))
    _write_live(live_path, {"status": "running", "stage": "最终测试集评估", "epoch": best_epoch,
                            "total_epochs": args.epochs, "step": 0,
                            "steps_total": sum(record.split == "test" for record in records),
                            "stage_progress": 0.0, "validation_thresholds": thresholds,
                            "validation_metrics": val_metrics})
    test_started = time.monotonic()
    test, test_windows = _evaluate(records, "test", model=model, epoch=best_epoch, seed=args.seed,
                                   np=np, cv2=cv2, torch=torch, device=device, amp=amp,
                                   batch_size=args.window_batch_size, logger=logger,
                                   live_path=live_path, epoch_started=test_started)
    test_metrics = _threshold_metrics(test, thresholds)
    val_by_dataset_modes = {}
    for dataset in DATASET_NAMES:
        subset = [row for row in validation if row["dataset"] == dataset]
        if subset:
            val_by_dataset_modes[dataset] = {mode: _metrics_with_rates(metrics) for mode, metrics in
                                             core._per_dataset_metrics(subset, thresholds)[dataset].items()}
    test_by_dataset = {}
    for dataset in DATASET_NAMES:
        subset = [row for row in test if row["dataset"] == dataset]
        if subset:
            test_by_dataset[dataset] = {mode: _metrics_with_rates(metrics) for mode, metrics in
                                        core._per_dataset_metrics(subset, thresholds)[dataset].items()}
    _write_csv(run_dir / "test_predictions.csv", test)
    _write_csv(run_dir / "test_window_predictions.csv", test_windows)
    report = {"experiment_protocol": PROTOCOL_ID, "status": "completed",
              "input_representation": INPUT_REPRESENTATION, "best_epoch": best_epoch,
              "best_validation_roc_auc": best_auc, "epochs_completed": len(history),
              "early_stopped_after_epoch": int(history[-1]["epoch"]), "thresholds": thresholds,
              "validation_metrics": val_metrics, "test_metrics": test_metrics,
              "validation_metrics_at_0_5": _metrics_with_rates(core.binary_metrics(val_labels, val_probabilities, 0.5)),
              "test_metrics_at_0_5": _metrics_with_rates(core.binary_metrics(
                  [int(row["label"]) for row in test], [float(row["probability_fight"]) for row in test], 0.5)),
              "validation_metrics_by_dataset_at_0_5": _per_dataset_at_half(validation),
              "validation_metrics_by_dataset": val_by_dataset_modes,
              "test_metrics_by_dataset": test_by_dataset,
              "test_evaluation_seconds": time.monotonic() - test_started,
              "dataset_summary": summary, "dataset_fingerprint": fingerprint,
              "initialization_mode": initialization_mode, "initialization": initialization,
              "base_manifest_sha256": data_meta["base_manifest_sha256"],
              "protocol": protocol}
    if initialization_mode == "base-checkpoint":
        report["base_checkpoint"] = str(Path(args.base_checkpoint).resolve())
    _atomic_json(run_dir / "final_report.json", report)
    logger.info("FINAL TEST | best_epoch=%d | validation AUC=%.6f | test AUC=%.6f | val@0.5=%s | test@0.5=%s",
                best_epoch, best_auc, test_metrics["moderate"]["roc_auc"],
                json.dumps(report["validation_metrics_at_0_5"], ensure_ascii=False),
                json.dumps(report["test_metrics_at_0_5"], ensure_ascii=False))
    _write_live(live_path, {"status": "completed", "stage": "训练、验证和测试完成",
                            "epoch": best_epoch, "total_epochs": args.epochs,
                            "completed_epochs": len(history), "stage_progress": 1.0,
                            "best_epoch": best_epoch, "best_val_roc_auc": best_auc,
                            "validation_metrics": val_metrics, "test_metrics": test_metrics,
                            "validation_by_dataset": report["validation_metrics_by_dataset_at_0_5"],
                            "test_by_dataset": test_by_dataset, "eta_seconds": 0})
    return run_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", default=str(DATA_ROOT))
    parser.add_argument("--base-manifest", default=str(DEFAULT_AIR_MANIFEST))
    parser.add_argument("--dancetrack-manifest", default=str(DEFAULT_DANCETRACK_MANIFEST))
    parser.add_argument("--base-checkpoint", default=str(DEFAULT_BASE_CHECKPOINT))
    parser.add_argument("--initialization-mode", choices=("base-checkpoint", "kinetics400"),
                        default="base-checkpoint")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--resume")
    parser.add_argument("--pause-file")
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--allow-cpu", action="store_true")
    parser.add_argument("--no-amp", action="store_true")
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--patience", type=int, default=4)
    parser.add_argument("--lr-reduce-after", type=int, default=2)
    parser.add_argument("--lr-factor", type=float, default=0.5)
    parser.add_argument("--min-lr", type=float, default=1e-6)
    parser.add_argument("--min-delta", type=float, default=0.001)
    parser.add_argument("--backbone-lr", type=float, default=2e-5)
    parser.add_argument("--head-lr", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--window-batch-size", type=int, default=2)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--log-every", type=int, default=10)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.epochs <= 300 or not 1 <= args.patience <= 300:
        raise SystemExit("epochs and patience must be between 1 and 300")
    if not 1 <= args.lr_reduce_after <= 300 or not 1 <= args.grad_accum <= 128:
        raise SystemExit("lr-reduce-after and grad-accum must be positive within supported limits")
    if not 1 <= args.window_batch_size <= 8:
        raise SystemExit("window-batch-size must be between 1 and 8")
    if not 0 < args.lr_factor < 1 or not 0 <= args.min_delta <= 1:
        raise SystemExit("invalid scheduler factor or min-delta")
    try:
        run_dir = _run_training(args)
    except Exception as exc:
        run_dir = Path(args.run_dir).resolve()
        try:
            _write_live(run_dir / "live_status.json", {"status": "failed", "stage": "训练失败", "error": str(exc),
                                                        "traceback": traceback.format_exc()[-8000:]})
        except OSError:
            pass
        logging.getLogger("mc3_native_training").exception("Native-video MC3-18 training failed")
        raise
    return 0 if (run_dir / "final_report.json").is_file() or (run_dir / "last.pt").is_file() else 1


if __name__ == "__main__":
    raise SystemExit(main())
