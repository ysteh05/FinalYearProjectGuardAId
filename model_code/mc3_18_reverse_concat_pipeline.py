"""Train the separate MC3-18 two-second reverse-concatenation MIL protocol.

The existing 1-second pipeline remains unchanged. This runner reuses its model,
optimizer, checkpoint, threshold calibration and reporting implementation while
installing a two-second data protocol over the existing five datasets plus UCF.
The existing datasets keep their checked-in train/val/test assignments; UCF is
split by source group. AIRTLab and GuardAId remain external test-only sources.
UCF's normal-video ZIP files are read one video at a time; the archive is never
fully extracted into the dataset directory. AIRTLab and GuardAId remain
external test-only sources unless a runner explicitly enables AIRTLab splitting.
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
import tempfile
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

import mc3_18_mil_pipeline as core


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UCF_ROOT = PROJECT_ROOT / "GuardAId Video Classification Model Dataset" / "UCF"
DEFAULT_DATASET_ROOT = PROJECT_ROOT / "GuardAId Video Classification Model Dataset"
DEFAULT_LEGACY_DATASET_ROOT = DEFAULT_DATASET_ROOT
DEFAULT_AIRTLAB_ROOT = (
    DEFAULT_DATASET_ROOT / "AIRTLab" / "A-Dataset-for-Automatic-Violence-Detection-in-Videos-master"
    / "violence-detection-dataset"
)
DEFAULT_GUARDAID_ROOT = DEFAULT_DATASET_ROOT / "GuardAId"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_2s8f_reverse_concat"
PROTOCOL = "mc3-2s-1s-stride-8f-128short-112crop-reverse-concat-v2"
WINDOW_SECONDS = 2.0
STRIDE_SECONDS = 1.0
NUM_FRAMES = 8
MIN_TRAIN_SECONDS = 1.0
UNKNOWN_LABEL = -1
VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv"}
SPLIT_RATIOS = (("train", 0.80), ("val", 0.10), ("test", 0.10))

UCF_ROOT = DEFAULT_UCF_ROOT
LEGACY_DATASET_ROOT = DEFAULT_LEGACY_DATASET_ROOT
AIRTLAB_ROOT = DEFAULT_AIRTLAB_ROOT
GUARDAID_ROOT = DEFAULT_GUARDAID_ROOT
AIRTLAB_SPLIT_FOR_MODEL = False
_ORIGINAL_DISCOVER_DATASET_ROOTS = core.discover_dataset_roots
_ORIGINAL_CACHE_WORKER = core._cache_video_worker


@dataclass
class _Source:
    dataset: str
    class_name: str
    label: int
    path: Path
    source_uri: str
    source_group: str
    fingerprint: str
    size_bytes: int
    mtime_ns: int
    archive_path: Path | None = None
    archive_member: str | None = None
    fps: float = 0.0
    frame_count: int = 0
    duration: float = 0.0
    split: str = "test"
    exclusion_reason: str = ""
    stratum: str = ""


def _protocol() -> dict[str, Any]:
    return {
        "window_seconds": WINDOW_SECONDS,
        "stride_seconds": STRIDE_SECONDS,
        "frames_per_window": NUM_FRAMES,
        "sampling_rate_fps": NUM_FRAMES / WINDOW_SECONDS,
        "frame_sample_positions": [round((i + 0.5) / NUM_FRAMES, 6) for i in range(NUM_FRAMES)],
        "short_side": core.SHORT_SIDE,
        "crop_size": core.CROP_SIZE,
        "color_space": "RGB",
        "normalization_mean": list(core.MEAN),
        "normalization_std": list(core.STD),
        "short_clip_training_augmentation": {
            "enabled": True,
            "applies_to": "all model train splits; AIRTLab/GuardAId remain natural external tests",
            "duration_seconds": "1.0 <= duration < 2.0",
            "operation": "append temporal reverse of the source clip; sample one 2-second window",
            "validation_and_test": "use natural video only; exclude source clips shorter than 2 seconds",
        },
        "subsecond_policy": "exclude from all splits when duration < 1 second",
        "legacy_split_policy": "preserve the original train/val/test folders for all five existing MC3-18 datasets",
        "legacy_datasets": [name for name, _ in _ORIGINAL_DISCOVER_DATASET_ROOTS(LEGACY_DATASET_ROOT)],
        "ucf_split": "stratified 80/10/10 by source-group within video class/subcategory",
        "ucf_split_seed": _ACTIVE_SEED,
        "leakage_controls": [
            "video-level grouping by normalized source identity",
            "exact-byte duplicate grouping/deduplication by CRC32 and size",
            "exact UCF duplicates of legacy or external holdout videos excluded from UCF splits",
            "exact legacy duplicates of AIRTLab/GuardAId holdouts excluded from model splits",
            "AIRTLab and GuardAId remain outside training and validation",
        ],
        "source_roots": {
            "legacy": str(LEGACY_DATASET_ROOT.resolve()),
            "ucf": str(UCF_ROOT.resolve()),
            "airtlab": str(AIRTLAB_ROOT.resolve()),
            "guardaid": str(GUARDAID_ROOT.resolve()),
        },
        "guardaid_label_policy": "unlabeled predictions only; conditional false-positive view assumes NonFight and is not ground-truth evaluation",
        "airtlab_note": "cam1/cam2 may show the same staged actions from two views; metrics are clip-level, not independent-event metrics",
    }


_ACTIVE_SEED = 2026


def _install_core_protocol() -> None:
    core.WINDOW_SECONDS = WINDOW_SECONDS
    core.STRIDE_SECONDS = STRIDE_SECONDS
    core.NUM_FRAMES = NUM_FRAMES
    core.CACHE_PROTOCOL = PROTOCOL
    core.CACHE_ENGINE_VERSION = "zip-aware-sequential-video-cache-v1"
    core.default_dataset_root = lambda: LEGACY_DATASET_ROOT
    core.default_cache_root = lambda: DEFAULT_OUTPUT_ROOT / "_frame_cache" / PROTOCOL
    core._preprocessing_protocol = _protocol
    core.discover_dataset_roots = _discover_dataset_roots
    core._scan_datasets = _scan_datasets
    core._dataset_fingerprint = _dataset_fingerprint
    core._windows_for_record = _windows_for_record
    core._cache_video_worker = _cache_video_worker
    core._count_split_samples = _count_split_samples
    core._aggregate_metrics = _aggregate_metrics
    core._per_dataset_metrics = _per_dataset_metrics
    core._evaluate_split = _evaluate_split


def _discover_dataset_roots(dataset_root: Path) -> list[tuple[str, Path]]:
    legacy_root = Path(dataset_root).expanduser().resolve()
    legacy_roots = _ORIGINAL_DISCOVER_DATASET_ROOTS(legacy_root)
    for label, path in (("UCF", UCF_ROOT), ("AIRTLab", AIRTLAB_ROOT), ("GuardAId", GUARDAID_ROOT)):
        if not path.is_dir():
            raise FileNotFoundError(f"{label} source directory is missing: {path}")
    return [*legacy_roots, ("UCF", UCF_ROOT), ("AIRTLab", AIRTLAB_ROOT), ("GuardAId", GUARDAID_ROOT)]


def _crc32_file(path: Path) -> str:
    import zlib

    checksum = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            checksum = zlib.crc32(chunk, checksum)
    return f"{checksum & 0xffffffff:08x}:{path.stat().st_size}"


def _canonical_stem(value: str) -> str:
    stem = Path(value).stem.casefold()
    for suffix in ("_x264", "_264", "_copy", "_trimmed"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return "".join(ch for ch in stem if ch.isalnum())


def _group_key(dataset: str, class_name: str, category: str, filename: str) -> str:
    return f"{dataset.casefold()}|{class_name.casefold()}|{category.casefold()}|{_canonical_stem(filename)}"


def _split_group_ids(groups: Sequence[str], seed: int) -> dict[str, str]:
    ordered = sorted(groups, key=lambda value: hashlib.sha256(f"{seed}|{value}".encode()).digest())
    count = len(ordered)
    train_count = int(math.floor(count * 0.80 + 0.5))
    val_count = int(math.floor(count * 0.10 + 0.5))
    while train_count + val_count > count:
        val_count -= 1
    train_count = max(0, min(count, train_count))
    val_count = max(0, min(count - train_count, val_count))
    return {
        group: "train" if index < train_count else "val" if index < train_count + val_count else "test"
        for index, group in enumerate(ordered)
    }


def _assign_airtlab_splits(airtlab_sources: Sequence[_Source], seed: int) -> dict[str, str]:
    """Assign AIRTLab source groups while keeping same-name camera views together."""
    group_labels: dict[str, set[str]] = defaultdict(set)
    for source in airtlab_sources:
        group_labels[source.source_group].add(source.class_name)
    groups_by_stratum: dict[str, set[str]] = defaultdict(set)
    for source in airtlab_sources:
        signature = "|".join(sorted(group_labels[source.source_group]))
        groups_by_stratum[signature].add(source.source_group)
    assignments: dict[str, str] = {}
    for stratum, groups in groups_by_stratum.items():
        assignments.update(_split_group_ids(sorted(groups), seed + sum(stratum.encode("utf-8"))))
    return assignments


def _extract_zip_member(archive: Path, member: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf, zf.open(member) as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)


def _metadata_for_source(source: _Source, cv2: Any, *, temp_dir: Path | None = None) -> tuple[float, int, float]:
    temporary: Path | None = None
    video_path = source.path
    try:
        if source.archive_path is not None and source.archive_member:
            root = temp_dir or Path(tempfile.gettempdir())
            with tempfile.TemporaryDirectory(prefix="guardaid_ucf_meta_", dir=root) as directory:
                temporary = Path(directory) / Path(source.archive_member).name
                _extract_zip_member(source.archive_path, source.archive_member, temporary)
                cap = cv2.VideoCapture(str(temporary))
                if not cap.isOpened():
                    raise RuntimeError(f"OpenCV could not open ZIP video {source.source_uri}")
                fps = float(cap.get(cv2.CAP_PROP_FPS))
                count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                cap.release()
        else:
            cap = cv2.VideoCapture(str(video_path))
            if not cap.isOpened():
                raise RuntimeError(f"OpenCV could not open video {source.source_uri}")
            fps = float(cap.get(cv2.CAP_PROP_FPS))
            count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()
        if not math.isfinite(fps) or fps <= 0 or count <= 0:
            raise RuntimeError(f"Invalid video metadata {source.source_uri}: fps={fps}, frames={count}")
        return fps, count, count / fps
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _find_airtlab_class_root(root: Path) -> Path:
    candidates = (root, root / "violence-detection-dataset")
    for candidate in candidates:
        if (candidate / "violent").is_dir() and (candidate / "non-violent").is_dir():
            return candidate
    raise FileNotFoundError(f"AIRTLab must contain violent/ and non-violent/ directories: {root}")


def _collect_sources(logger: logging.Logger) -> tuple[list[_Source], list[dict[str, Any]]]:
    sources: list[_Source] = []
    failures: list[dict[str, Any]] = []

    # The five established project datasets retain the split encoded by their
    # existing directory layout. This mirrors the original MC3-18 scanner.
    for dataset_name, root in _ORIGINAL_DISCOVER_DATASET_ROOTS(LEGACY_DATASET_ROOT.resolve()):
        for split in ("train", "val", "test"):
            for class_name, label in core.CLASS_TO_LABEL.items():
                class_dir = root / split / class_name
                if not class_dir.is_dir():
                    raise FileNotFoundError(f"Expected legacy dataset directory is missing: {class_dir}")
                paths = sorted(
                    (path for path in class_dir.iterdir() if path.is_file() and path.suffix.lower() in {".avi", ".mp4"}),
                    key=lambda path: path.name.casefold(),
                )
                for path in paths:
                    try:
                        stat = path.stat()
                        crc = _crc32_file(path)
                    except OSError as exc:
                        failures.append({"path": str(path), "error": str(exc)})
                        continue
                    group = _group_key(dataset_name, class_name, split, path.name)
                    sources.append(_Source(
                        dataset_name, class_name, label, path.resolve(), str(path.resolve()), group,
                        crc, stat.st_size, stat.st_mtime_ns, split=split, stratum=class_name,
                    ))

    ucf_root = UCF_ROOT.resolve()
    if not ucf_root.is_dir():
        raise FileNotFoundError(f"UCF directory not found: {ucf_root}")
    for label, class_name, category_root, required in (
        (1, "Fight", ucf_root / "Violence", True),
        (0, "NonFight", ucf_root / "Non-Violence", False),
    ):
        if not category_root.is_dir():
            if required:
                raise FileNotFoundError(f"UCF class directory is missing: {category_root}")
            # Normal videos may still be supplied by the ZIP archive below.
            continue
        for path in sorted((p for p in category_root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES), key=lambda p: str(p).casefold()):
            try:
                stat = path.stat()
                crc = _crc32_file(path)
            except OSError as exc:
                failures.append({"path": str(path), "error": str(exc)})
                continue
            category = path.parent.name if label else "NonFight"
            group = _group_key("UCF", class_name, category, path.name)
            sources.append(_Source("UCF", class_name, label, path.resolve(), str(path.resolve()), group,
                                   crc, stat.st_size, stat.st_mtime_ns, stratum=category))

    zip_entries: list[tuple[Path, zipfile.ZipInfo]] = []
    for archive in sorted(ucf_root.glob("Training-Normal-Videos-Part-*.zip")):
        try:
            with zipfile.ZipFile(archive) as zf:
                zip_entries.extend((archive.resolve(), info) for info in zf.infolist()
                                   if not info.is_dir() and Path(info.filename).suffix.lower() in VIDEO_SUFFIXES)
        except (OSError, zipfile.BadZipFile) as exc:
            failures.append({"path": str(archive), "error": str(exc)})
    if zip_entries:
        largest = max(info.file_size for _, info in zip_entries)
        scratch_root = Path(tempfile.gettempdir())
        free = shutil.disk_usage(scratch_root).free
        if free < largest + 64 * 1024 * 1024:
            raise RuntimeError(
                f"Temporary extraction needs at least {largest / (1024**3):.2f} GiB free for one normal video; "
                f"{scratch_root.drive or scratch_root} has {free / (1024**3):.2f} GiB. "
                "Choose a temp directory with more space or extract the normal videos to a different drive."
            )
        for index, (archive, info) in enumerate(zip_entries, start=1):
            pseudo_key = hashlib.sha256(f"{archive}::{info.filename}".encode("utf-8")).hexdigest()
            pseudo = ucf_root / "_archive_sources" / f"{pseudo_key}{Path(info.filename).suffix.lower()}"
            member_crc = f"{info.CRC:08x}:{info.file_size}"
            group = _group_key("UCF", "NonFight", "NonFight", Path(info.filename).name)
            try:
                source = _Source("UCF", "NonFight", 0, pseudo, f"{archive}::{info.filename}", group,
                                 member_crc, info.file_size, archive.stat().st_mtime_ns,
                                 archive_path=archive, archive_member=info.filename, stratum="NonFight")
                sources.append(source)
            except OSError as exc:
                failures.append({"path": f"{archive}::{info.filename}", "error": str(exc)})
            if index == 1 or index % 25 == 0 or index == len(zip_entries):
                logger.info("UCF normal ZIP inventory %d/%d videos.", index, len(zip_entries))

    airtlab_root = _find_airtlab_class_root(AIRTLAB_ROOT.resolve())
    for class_dir, label, class_name in (
        (airtlab_root / "violent", 1, "Fight"),
        (airtlab_root / "non-violent", 0, "NonFight"),
    ):
        for path in sorted((p for p in class_dir.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES), key=lambda p: str(p).casefold()):
            try:
                stat = path.stat()
                crc = _crc32_file(path)
            except OSError as exc:
                failures.append({"path": str(path), "error": str(exc)})
                continue
            rel = path.relative_to(class_dir)
            # The four-second protocol treats matching names across cam1/cam2
            # as views of one source event, independent of camera folder.
            group = (
                f"airtlab|{class_name.casefold()}|sourcevideo|{_canonical_stem(path.name)}"
                if AIRTLAB_SPLIT_FOR_MODEL
                else _group_key("AIRTLab", class_name, rel.parent.as_posix(), path.name)
            )
            sources.append(_Source("AIRTLab", class_name, label, path.resolve(), str(path.resolve()), group,
                                   crc, stat.st_size, stat.st_mtime_ns, stratum=class_name))

    guard_root = GUARDAID_ROOT.resolve()
    if not guard_root.is_dir():
        raise FileNotFoundError(f"GuardAId holdout directory not found: {guard_root}")
    for path in sorted((p for p in guard_root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES), key=lambda p: str(p).casefold()):
        try:
            stat = path.stat()
            crc = _crc32_file(path)
        except OSError as exc:
            failures.append({"path": str(path), "error": str(exc)})
            continue
        group = _group_key("GuardAId", "Unlabeled", path.parent.as_posix(), path.name)
        sources.append(_Source("GuardAId", "Unlabeled", UNKNOWN_LABEL, path.resolve(), str(path.resolve()), group,
                               crc, stat.st_size, stat.st_mtime_ns, stratum="Unlabeled"))

    if failures:
        raise RuntimeError("Some dataset files could not be indexed:\n" + "\n".join(f"- {row['path']}: {row['error']}" for row in failures[:30]))
    ucf_fight = sum(source.dataset == "UCF" and source.label == 1 for source in sources)
    ucf_nonfight = sum(source.dataset == "UCF" and source.label == 0 for source in sources)
    if not ucf_fight or not ucf_nonfight:
        raise RuntimeError(
            f"UCF needs both positive and negative videos. Found Fight={ucf_fight}, NonFight={ucf_nonfight}. "
            "The negative ZIP can supply NonFight videos; the folder must contain videos or Training-Normal-Videos-Part-*.zip must be present."
        )
    return sources, failures


def _resolve_group_ids(ucf_sources: Sequence[_Source]) -> dict[str, str]:
    parents: dict[str, str] = {}

    def find(key: str) -> str:
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            low, high = sorted((a, b))
            parents[high] = low

    fingerprints: dict[str, str] = {}
    for source in ucf_sources:
        group = source.source_group
        find(group)
        previous = fingerprints.setdefault(source.fingerprint, group)
        union(group, previous)
    return {group: find(group) for group in list(parents)}


def _scan_datasets(dataset_root: Path, cv2: Any, logger: logging.Logger) -> tuple[list[core.VideoRecord], list[dict[str, Any]]]:
    all_sources, _ = _collect_sources(logger)
    ucf_sources = [source for source in all_sources if source.dataset == "UCF"]
    airtlab_sources = [source for source in all_sources if source.dataset == "AIRTLab"]
    holdout_sources = [
        source for source in all_sources
        if source.dataset == "GuardAId" or (source.dataset == "AIRTLab" and not AIRTLAB_SPLIT_FOR_MODEL)
    ]
    legacy_sources = [source for source in all_sources if source.dataset not in {"UCF", "AIRTLab", "GuardAId"}]
    legacy_names = {source.dataset for source in legacy_sources}

    if AIRTLAB_SPLIT_FOR_MODEL:
        air_splits = _assign_airtlab_splits(airtlab_sources, _ACTIVE_SEED)
        for source in airtlab_sources:
            source.split = air_splits[source.source_group]

    holdout_fingerprints = {source.fingerprint for source in holdout_sources}
    legacy_fingerprints = {source.fingerprint for source in legacy_sources}
    for source in ucf_sources:
        if source.fingerprint in holdout_fingerprints:
            source.exclusion_reason = "exact_duplicate_of_external_holdout"
        elif source.fingerprint in legacy_fingerprints:
            source.exclusion_reason = "exact_duplicate_of_legacy_dataset"

    # Keep the external holdout copy when an old split contains the same exact
    # video, so the external evaluation cannot include a training duplicate.
    for source in (*legacy_sources, *(airtlab_sources if AIRTLAB_SPLIT_FOR_MODEL else [])):
        if source.fingerprint in holdout_fingerprints:
            source.exclusion_reason = "exact_duplicate_of_external_holdout"

    ucf_conflicts: set[str] = set()
    by_fingerprint: dict[str, list[_Source]] = defaultdict(list)
    for source in ucf_sources:
        by_fingerprint[source.fingerprint].append(source)
    for fingerprint, group in by_fingerprint.items():
        if len({source.label for source in group}) > 1:
            ucf_conflicts.add(fingerprint)
            for source in group:
                source.exclusion_reason = "exact_duplicate_conflicting_labels"
        elif len(group) > 1:
            # Keep one physical copy; the group assignment still keeps its copies together.
            for duplicate in sorted(group, key=lambda item: item.source_uri.casefold())[1:]:
                if not duplicate.exclusion_reason:
                    duplicate.exclusion_reason = "exact_duplicate_deduplicated"

    group_roots = _resolve_group_ids(ucf_sources)
    groups_by_stratum: dict[str, set[str]] = defaultdict(set)
    source_to_group: dict[str, str] = {}
    for source in ucf_sources:
        root = group_roots.get(source.source_group, source.source_group)
        source_to_group[source.source_uri] = root
        if not source.exclusion_reason and source.fingerprint not in ucf_conflicts:
            groups_by_stratum[source.stratum].add(root)
    group_splits: dict[str, str] = {}
    for stratum, groups in groups_by_stratum.items():
        group_splits.update(_split_group_ids(sorted(groups), _ACTIVE_SEED + sum(stratum.encode("utf-8"))))
    for source in ucf_sources:
        source.split = group_splits.get(source_to_group.get(source.source_uri, ""), "train")
    for source in holdout_sources:
        source.split = "external_test" if AIRTLAB_SPLIT_FOR_MODEL and source.dataset == "GuardAId" else "test"

    # Preserve the legacy split assignment, while removing exact duplicate
    # model samples that would otherwise cross split boundaries.
    included_airtlab = airtlab_sources if AIRTLAB_SPLIT_FOR_MODEL else []
    model_sources = [source for source in (*legacy_sources, *ucf_sources, *included_airtlab) if not source.exclusion_reason]
    model_by_fingerprint: dict[str, list[_Source]] = defaultdict(list)
    for source in model_sources:
        model_by_fingerprint[source.fingerprint].append(source)
    split_priority = {"test": 0, "val": 1, "train": 2}
    for group in model_by_fingerprint.values():
        if len(group) < 2:
            continue
        if len({source.label for source in group}) > 1:
            for source in group:
                source.exclusion_reason = "exact_duplicate_conflicting_labels"
            continue
        keeper = min(
            group,
            key=lambda source: (
                split_priority.get(source.split, 3),
                0 if source.dataset in legacy_names else 1,
                source.dataset.casefold(),
                source.source_uri.casefold(),
            ),
        )
        for source in group:
            if source is keeper:
                continue
            source.exclusion_reason = (
                "exact_duplicate_in_other_split" if source.split != keeper.split else "exact_duplicate_deduplicated"
            )

    manifest: list[dict[str, Any]] = []
    records: list[core.VideoRecord] = []
    total_metadata = len([source for source in all_sources if not source.exclusion_reason])
    metadata_done = 0
    scratch_root = Path(tempfile.gettempdir())
    started = time.monotonic()
    errors: list[str] = []
    for source in all_sources:
        if source.exclusion_reason:
            manifest.append(_manifest_row(source, False, source.exclusion_reason, group_roots.get(source.source_group, source.source_group)))
            continue
        try:
            source.fps, source.frame_count, source.duration = _metadata_for_source(source, cv2, temp_dir=scratch_root)
        except Exception as exc:
            errors.append(f"{source.source_uri}: {exc}")
            continue
        metadata_done += 1
        if metadata_done == 1 or metadata_done % 25 == 0 or metadata_done == total_metadata:
            elapsed = time.monotonic() - started
            eta = elapsed / metadata_done * (total_metadata - metadata_done) if metadata_done else 0.0
            logger.info("Video metadata preflight %d/%d | elapsed=%.1fs | ETA=%.1fs", metadata_done, total_metadata, elapsed, eta)

        is_airtlab_model_sample = AIRTLAB_SPLIT_FOR_MODEL and source.dataset == "AIRTLab"
        is_model_training_sample = (
            source.split == "train" and source.dataset != "GuardAId"
            and (source.dataset != "AIRTLab" or AIRTLAB_SPLIT_FOR_MODEL)
        )
        # The four-second natural short-tail policy accepts eligible AIRTLab
        # clips from 1–4 seconds in every model split. GuardAId remains an
        # external inference-only set and requires a full natural window.
        minimum = MIN_TRAIN_SECONDS if is_model_training_sample or is_airtlab_model_sample else WINDOW_SECONDS
        eligible = source.duration >= minimum
        reason = "" if eligible else (
            "shorter_than_1_second" if source.duration < MIN_TRAIN_SECONDS
            else "shorter_than_2_seconds_for_natural_sampling"
        )
        row = _manifest_row(source, eligible, reason, group_roots.get(source.source_group, source.source_group))
        row["fps"] = f"{source.fps:.6f}"
        row["frame_count"] = source.frame_count
        row["duration_seconds"] = f"{source.duration:.6f}"
        row["reverse_concat_applied"] = int(is_model_training_sample and MIN_TRAIN_SECONDS <= source.duration < WINDOW_SECONDS)
        manifest.append(row)
        if not eligible:
            continue
        record = core.VideoRecord(
            dataset=source.dataset,
            split=source.split,
            class_name=source.class_name,
            label=source.label,
            path=source.path,
            fps=source.fps,
            frame_count=source.frame_count,
            duration=source.duration,
            size_bytes=source.size_bytes,
            mtime_ns=source.mtime_ns,
        )
        record.source_uri = source.source_uri
        record.fingerprint = source.fingerprint
        record.source_group = group_roots.get(source.source_group, source.source_group)
        record.archive_path = source.archive_path
        record.archive_member = source.archive_member
        record.reverse_concat = bool(is_model_training_sample and MIN_TRAIN_SECONDS <= source.duration < WINDOW_SECONDS)
        records.append(record)

    if errors:
        raise RuntimeError("Video metadata preflight failed:\n" + "\n".join(f"- {error}" for error in errors[:40]))
    if not records:
        raise RuntimeError("No eligible legacy/UCF/AIRTLab/GuardAId videos were found.")

    # Verify split isolation across every model source; external test datasets
    # are intentionally all in the test split and are reported separately.
    split_groups: dict[str, set[str]] = defaultdict(set)
    split_fingerprints: dict[str, set[str]] = defaultdict(set)
    for record in records:
        if record.dataset == "GuardAId" or (record.dataset == "AIRTLab" and not AIRTLAB_SPLIT_FOR_MODEL):
            continue
        split_fingerprints[record.split].add(record.fingerprint)
        if record.dataset in {"UCF", "AIRTLab"}:
            split_groups[record.split].add(record.source_group)
    for left, right in (("train", "val"), ("train", "test"), ("val", "test")):
        group_overlap = split_groups[left] & split_groups[right]
        fingerprint_overlap = split_fingerprints[left] & split_fingerprints[right]
        if group_overlap or fingerprint_overlap:
            raise RuntimeError(f"Leakage guard failed for {left}/{right}: {len(group_overlap)} UCF/AIRTLab source-group and {len(fingerprint_overlap)} exact-file overlap(s).")

    for dataset in sorted({source.dataset for source in all_sources}):
        subset = [row for row in manifest if row["dataset"] == dataset]
        eligible = sum(int(row["eligible"]) for row in subset)
        excluded = len(subset) - eligible
        logger.info("Dataset %s preflight | files=%d | eligible=%d | excluded=%d", dataset, len(subset), eligible, excluded)
    split_counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for record in records:
        split_counts[(record.dataset, record.split, record.class_name)] += 1
    for dataset in sorted({record.dataset for record in records}):
        for split in ("train", "val", "test"):
            logger.info("Dataset split %s/%s | Fight=%d | NonFight=%d | total=%d", dataset, split,
                        split_counts[(dataset, split, "Fight")], split_counts[(dataset, split, "NonFight")],
                        split_counts[(dataset, split, "Fight")] + split_counts[(dataset, split, "NonFight")])
    if len({record.label for record in records if record.split == "train"}) < 2 or len({record.label for record in records if record.split == "val"}) < 2:
        raise RuntimeError("Combined train and validation splits must both retain Fight and NonFight after duration and duplicate filtering.")
    return records, manifest


def _manifest_row(source: _Source, eligible: bool, reason: str, group_id: str) -> dict[str, Any]:
    return {
        "dataset": source.dataset,
        "split": source.split,
        "class_name": source.class_name,
        "label": source.label,
        "path": source.source_uri,
        "source_uri": source.source_uri,
        "source_group": group_id,
        "file_fingerprint_crc32_size": source.fingerprint,
        "extension": Path(source.source_uri.split("::")[-1]).suffix.lower(),
        "fps": "",
        "frame_count": "",
        "duration_seconds": "",
        "eligible": int(eligible),
        "exclusion_reason": reason,
        "size_bytes": source.size_bytes,
        "reverse_concat_applied": 0,
    }


def _dataset_fingerprint(records: Sequence[core.VideoRecord]) -> str:
    material = []
    for record in records:
        material.append("|".join((
            str(record.dataset), str(record.split), str(record.label),
            str(getattr(record, "source_uri", record.path)), str(getattr(record, "fingerprint", "")),
            str(getattr(record, "source_group", "")), f"{record.duration:.8f}",
            str(record.frame_count), f"{record.fps:.8f}",
        )))
    return hashlib.sha256("\n".join(sorted(material)).encode("utf-8")).hexdigest()


def make_reverse_windows(duration: float, fps: float, frame_count: int, *, include_training_tail: bool = False) -> list[core.Window]:
    if fps <= 0 or frame_count <= 0 or duration < MIN_TRAIN_SECONDS:
        return []
    if duration < WINDOW_SECONDS:
        if not include_training_tail:
            return []
        indices = []
        for sample_index in range(NUM_FRAMES):
            virtual_time = (sample_index + 0.5) * WINDOW_SECONDS / NUM_FRAMES
            source_time = virtual_time if virtual_time < duration else max(0.0, 2.0 * duration - virtual_time)
            indices.append(max(0, min(frame_count - 1, int(math.floor(source_time * fps)))))
        return [core.Window(0.0, WINDOW_SECONDS, tuple(indices))]

    intervals: list[tuple[float, float]] = []
    start = 0.0
    while start + WINDOW_SECONDS <= duration + 1e-9:
        intervals.append((round(start, 9), round(start + WINDOW_SECONDS, 9)))
        start += STRIDE_SECONDS
    if include_training_tail:
        tail_start = max(0.0, duration - WINDOW_SECONDS)
        if not intervals or tail_start > intervals[-1][0] + 1e-6:
            intervals.append((tail_start, duration))
    windows = []
    for start, end in intervals:
        del end
        indices = tuple(
            max(0, min(frame_count - 1, int(math.floor((start + (i + 0.5) * WINDOW_SECONDS / NUM_FRAMES) * fps))))
            for i in range(NUM_FRAMES)
        )
        windows.append(core.Window(start, start + WINDOW_SECONDS, indices))
    return windows


def _windows_for_record(record: core.VideoRecord, *, training: bool) -> list[core.Window]:
    return make_reverse_windows(
        record.duration,
        record.fps,
        record.frame_count,
        include_training_tail=bool(training and record.split == "train"),
    )


def _cache_video_worker(record: core.VideoRecord, frame_indices: tuple[int, ...], cache_path_text: str) -> int:
    archive_path = getattr(record, "archive_path", None)
    archive_member = getattr(record, "archive_member", None)
    if not archive_path or not archive_member:
        return _ORIGINAL_CACHE_WORKER(record, frame_indices, cache_path_text)
    import cv2
    import numpy as np

    cache_path = Path(cache_path_text)
    with tempfile.TemporaryDirectory(prefix="guardaid_ucf_cache_") as directory:
        materialized = Path(directory) / Path(archive_member).name
        _extract_zip_member(Path(archive_path), str(archive_member), materialized)
        decoded = replace(record, path=materialized)
        frames = core._decode_selected_frames(decoded, frame_indices, cv2, np)
    temporary = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, frame_indices=np.asarray(frame_indices, dtype=np.int32), frames=frames)
        os.replace(temporary, cache_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return os.getpid()


def _count_split_samples(dataset_roots: Sequence[tuple[str, Path]], records: Sequence[core.VideoRecord], manifest_rows: Sequence[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    eligible: dict[str, int] = defaultdict(int)
    excluded: dict[str, int] = defaultdict(int)
    for row in manifest_rows:
        key = f"{row.get('dataset', 'unknown')}/{row.get('split', 'unknown')}/{row.get('class_name', 'unknown')}"
        if str(row.get("eligible", "0")) == "1":
            eligible[key] += 1
        else:
            excluded[key] += 1
    return dict(eligible), dict(excluded)


def _aggregate_metrics(predictions: Sequence[dict[str, Any]], threshold: float) -> dict[str, Any]:
    known = [row for row in predictions if int(row.get("label", UNKNOWN_LABEL)) in (0, 1)]
    if not known:
        return {"n": 0, "tp": 0, "tn": 0, "fp": 0, "fn": 0, "accuracy": None, "precision": None, "recall": None,
                "specificity": None, "f0_5": None, "f1": None, "f2": None, "balanced_accuracy": None, "roc_auc": None,
                "average_precision": None, "threshold": float(threshold)}
    return core.binary_metrics([int(row["label"]) for row in known], [float(row["probability_fight"]) for row in known], threshold)


def _per_dataset_metrics(predictions: Sequence[dict[str, Any]], thresholds: dict[str, dict[str, float]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for dataset in sorted({str(row["dataset"]) for row in predictions}):
        subset = [row for row in predictions if row["dataset"] == dataset]
        known = [row for row in subset if int(row.get("label", UNKNOWN_LABEL)) in (0, 1)]
        unknown = [row for row in subset if int(row.get("label", UNKNOWN_LABEL)) not in (0, 1)]
        if known:
            result[dataset] = core._mode_metrics(known, thresholds)
        if unknown:
            conditional = {}
            for mode, values in thresholds.items():
                cutoff = float(values["threshold"])
                predicted_positive = sum(float(row["probability_fight"]) >= cutoff for row in unknown)
                conditional[mode] = {
                    "threshold": cutoff,
                    "n_unlabeled": len(unknown),
                    "predicted_positive_count": predicted_positive,
                    "conditional_false_positive_rate_if_all_nonfight": predicted_positive / len(unknown),
                }
            result[dataset] = {
                "ground_truth_available": False,
                "unlabeled_video_count": len(unknown),
                "metrics_not_computed": "Ground truth labels are absent; conditional figures assume every video is NonFight.",
                "conditional_if_all_nonfight": conditional,
            }
    return result


_ORIGINAL_EVALUATE_SPLIT = core._evaluate_split


def _evaluate_split(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    predictions, windows = _ORIGINAL_EVALUATE_SPLIT(*args, **kwargs)
    for row in predictions:
        row["ground_truth_status"] = "labeled" if int(row.get("label", UNKNOWN_LABEL)) in (0, 1) else "unlabeled"
    for row in windows:
        row["ground_truth_status"] = "labeled" if int(row.get("label", UNKNOWN_LABEL)) in (0, 1) else "unlabeled"
    return predictions, windows


def quick_dataset_summary(ucf_root: Path, airtlab_root: Path, guardaid_root: Path, output_root: Path) -> dict[str, Any]:
    ucf_root, airtlab_root, guardaid_root, output_root = (Path(p).expanduser().resolve() for p in (ucf_root, airtlab_root, guardaid_root, output_root))
    fight_dir, nonfight_dir = ucf_root / "Violence", ucf_root / "Non-Violence"
    fight = [p for p in fight_dir.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES] if fight_dir.is_dir() else []
    nonfight_files = [p for p in nonfight_dir.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES] if nonfight_dir.is_dir() else []
    archive_rows = []
    for archive in sorted(ucf_root.glob("Training-Normal-Videos-Part-*.zip")) if ucf_root.is_dir() else []:
        try:
            with zipfile.ZipFile(archive) as zf:
                infos = [item for item in zf.infolist() if not item.is_dir() and Path(item.filename).suffix.lower() in VIDEO_SUFFIXES]
            archive_rows.append({"path": str(archive), "video_count": len(infos), "uncompressed_bytes": sum(item.file_size for item in infos),
                                 "largest_video_bytes": max((item.file_size for item in infos), default=0)})
        except (OSError, zipfile.BadZipFile) as exc:
            archive_rows.append({"path": str(archive), "video_count": 0, "error": str(exc)})
    ucf_negative = len(nonfight_files) + sum(int(row.get("video_count", 0)) for row in archive_rows)
    try:
        air_root = _find_airtlab_class_root(airtlab_root)
        air_fight = sum(1 for p in (air_root / "violent").rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES)
        air_nonfight = sum(1 for p in (air_root / "non-violent").rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES)
        air_error = None
    except (FileNotFoundError, OSError) as exc:
        air_fight = air_nonfight = 0
        air_error = str(exc)
    guard_videos = [p for p in guardaid_root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES] if guardaid_root.is_dir() else []
    free = shutil.disk_usage(output_root if output_root.exists() else output_root.parent).free if output_root.parent.exists() or output_root.exists() else None
    return {
        "protocol": PROTOCOL,
        "roots": {"ucf": str(ucf_root), "airtlab": str(airtlab_root), "guardaid": str(guardaid_root), "output": str(output_root)},
        "ucf": {"fight_folder_videos": len(fight), "nonfight_folder_videos": len(nonfight_files), "normal_archives": archive_rows,
                "normal_archive_videos": sum(int(row.get("video_count", 0)) for row in archive_rows), "raw_fight": len(fight),
                "raw_nonfight": ucf_negative, "raw_total": len(fight) + ucf_negative,
                "split_preview": {"train": {"Fight": round(len(fight) * .8), "NonFight": round(ucf_negative * .8)},
                                  "val": {"Fight": round(len(fight) * .1), "NonFight": round(ucf_negative * .1)},
                                  "test": {"Fight": len(fight) - round(len(fight) * .8) - round(len(fight) * .1),
                                           "NonFight": ucf_negative - round(ucf_negative * .8) - round(ucf_negative * .1)}},
                "split_note": "按视频来源组、类别/子类别分层；精确数以运行时数据清单为准。"},
        "airtlab": {"fight": air_fight, "nonfight": air_nonfight, "total": air_fight + air_nonfight, "error": air_error,
                    "role": "final labeled holdout only"},
        "guardaid": {"videos": len(guard_videos), "has_ground_truth_labels": False,
                     "role": "final unlabeled holdout; conditional NonFight false-positive view only"},
        "storage": {"free_bytes": free, "free_gib": round(free / (1024**3), 2) if free is not None else None,
                    "normal_archive_uncompressed_gib": round(sum(int(row.get("uncompressed_bytes", 0)) for row in archive_rows) / (1024**3), 2),
                    "archive_handling": "逐条临时解压到临时目录并删除，不会永久解压 38.6 GiB 压缩包"},
        "ready": bool(fight and ucf_negative and air_fight and air_nonfight and guard_videos),
        "notes": [
            "五个原有数据集沿用各自 split 文件夹；UCF 按视频来源组 80/10/10 划分。",
            "所有模型训练 split 中 1–2 秒视频使用原片接倒放增强；验证和测试只用自然视频，不足 2 秒的样本列入排除清单。",
            "AIRTLab 的 cam1/cam2 可能是相同行为的双视角，所有视角固定留在最终测试。",
            "GuardAId 当前没有 ground-truth 标签，故不纳入有标签总体成绩。",
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ucf-root", default=str(DEFAULT_UCF_ROOT))
    parser.add_argument("--legacy-dataset-root", default=str(DEFAULT_LEGACY_DATASET_ROOT))
    parser.add_argument("--airtlab-root", default=str(DEFAULT_AIRTLAB_ROOT))
    parser.add_argument("--guardaid-root", default=str(DEFAULT_GUARDAID_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-dir")
    parser.add_argument("--cache-dir")
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
    parser.add_argument("--window-batch-size", type=int, default=4)
    parser.add_argument("--log-every", type=int, default=25)
    parser.add_argument("--seed", type=int, default=2026)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    global UCF_ROOT, LEGACY_DATASET_ROOT, AIRTLAB_ROOT, GUARDAID_ROOT, DEFAULT_OUTPUT_ROOT, _ACTIVE_SEED
    parser = _build_parser()
    options = parser.parse_args(argv)
    if options.epochs < 1 or options.patience < 1 or options.grad_accum < 1 or options.window_batch_size < 1 or options.log_every < 1:
        parser.error("epochs, patience, grad-accum, window-batch-size and log-every must be positive")
    if options.backbone_lr <= 0 or options.head_lr <= 0 or options.weight_decay < 0 or options.min_delta < 0:
        parser.error("learning rates must be positive; weight-decay and min-delta must be non-negative")
    UCF_ROOT = Path(options.ucf_root).expanduser().resolve()
    LEGACY_DATASET_ROOT = Path(options.legacy_dataset_root).expanduser().resolve()
    AIRTLAB_ROOT = Path(options.airtlab_root).expanduser().resolve()
    GUARDAID_ROOT = Path(options.guardaid_root).expanduser().resolve()
    DEFAULT_OUTPUT_ROOT = Path(options.output_root).expanduser().resolve()
    _ACTIVE_SEED = int(options.seed)
    _install_core_protocol()
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
        dataset_root=str(LEGACY_DATASET_ROOT), output_root=str(DEFAULT_OUTPUT_ROOT), run_dir=options.run_dir,
        cache_dir=options.cache_dir, resume=options.resume, pause_file=options.pause_file,
        device=options.device, allow_cpu=options.allow_cpu, no_amp=options.no_amp,
        rebuild_cache=options.rebuild_cache, epochs=options.epochs, patience=options.patience,
        min_delta=options.min_delta, backbone_lr=options.backbone_lr, head_lr=options.head_lr,
        weight_decay=options.weight_decay, grad_accum=options.grad_accum,
        window_batch_size=options.window_batch_size, log_every=options.log_every, seed=options.seed,
    )
    core._run_training(namespace)
    return 0


_install_core_protocol()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
