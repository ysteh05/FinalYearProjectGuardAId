"""Controlled native-video vs RTMPose-skeleton MC3-18 AIRT Lab experiment.

Run from the project root with the Python 3.10 MC3 environment:
    py -3.10 -u model_code/mc3_18_airtlab_modalities_experiment.py

Both arms use the same event-group split, Kinetics-400 initialization, 4s16f2
training recipe, and video-level max-window aggregation. Test predictions are
only produced after the best validation-AUC checkpoint and validation F1
threshold have been locked.
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
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import mc3_18_four_second_pipeline as four
import mc3_18_mil_pipeline as core


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AIRTLAB_DATA_ROOT = PROJECT_ROOT / "GuardAId Video Classification Model Dataset" / "AIRTLab"
RAW_VIDEO_ROOT = (
    AIRTLAB_DATA_ROOT / "A-Dataset-for-Automatic-Violence-Detection-in-Videos-master"
    / "violence-detection-dataset"
)
SKELETON_ROOT = AIRTLAB_DATA_ROOT / "RTMPose_M_Skeleton_MC3_4s2s_16frames"
OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_airtlab_modalities_4s16f2"
NATIVE_CACHE_ROOT = PROJECT_ROOT / "model_runs" / "mc3_4s16f" / "_supplemental_cache"
LEGACY_CACHE_ROOT = (
    PROJECT_ROOT / "model_runs" / "mc3_2s8f_reverse_concat" / "_frame_cache"
    / "mc3-2s-1s-stride-8f-128short-112crop-reverse-concat-v2"
)
SOURCE_RUN_CONFIG = (
    PROJECT_ROOT / "model_runs" / "mc3_4s16f2" / "run_four_ui_20261007_234526_491833"
    / "config.json"
)
EXPERIMENT_PROTOCOL = "airtlab-native-vs-rtmpose-skeleton-mc3-4s2s-16f-v1"
SPLIT_RATIOS = {"train": 0.60, "val": 0.20, "test": 0.20}
MODE_NAMES = {"native": "native_video", "skeleton": "black_background_skeleton"}


@dataclass
class SampleRecord:
    record: core.VideoRecord
    source_relative: str
    group_id: str
    camera: str
    label_name: str
    source_sha256: str
    window_rows: list[dict[str, Any]]
    frame_paths_by_index: dict[int, Path]


def _log_setup(run_dir: Path) -> logging.Logger:
    run_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("mc3_airtlab_modalities")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(run_dir / "experiment.log", mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    return logger


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _balanced_counts(count: int, ratios: dict[str, float]) -> dict[str, int]:
    exact = {name: count * ratio for name, ratio in ratios.items()}
    result = {name: math.floor(value) for name, value in exact.items()}
    remainder = count - sum(result.values())
    order = sorted(ratios, key=lambda name: (exact[name] - result[name], -list(ratios).index(name)), reverse=True)
    for name in order[:remainder]:
        result[name] += 1
    return result


def assign_group_splits(
    groups_by_label: dict[int, Sequence[str]], seed: int = 2026,
    ratios: dict[str, float] | None = None,
) -> dict[str, str]:
    """Assign whole event groups with deterministic per-class stratification."""
    ratios = dict(ratios or SPLIT_RATIOS)
    if set(ratios) != {"train", "val", "test"} or not math.isclose(sum(ratios.values()), 1.0):
        raise ValueError("Split ratios must define train/val/test and sum to 1.")
    assignments: dict[str, str] = {}
    for label in sorted(groups_by_label):
        groups = sorted(set(groups_by_label[label]))
        counts = _balanced_counts(len(groups), ratios)
        random.Random(seed + 100_003 * int(label)).shuffle(groups)
        offset = 0
        for split in ("train", "val", "test"):
            for group in groups[offset : offset + counts[split]]:
                assignments[group] = split
            offset += counts[split]
        if offset != len(groups):
            raise AssertionError("Stratified group split did not consume all event groups.")
    return assignments


def _normalise_relative(path: str) -> str:
    return Path(path.replace("\\", "/")).as_posix()


def load_dataset_manifest(*, verify_source_hashes: bool, logger: logging.Logger | None = None,
                          verify_skeleton_files: bool = True,
                          verify_source_files: bool = True) -> tuple[list[SampleRecord], dict[str, Any]]:
    protocol_path = SKELETON_ROOT / "dataset_protocol.json"
    videos_path = SKELETON_ROOT / "video_manifest.csv"
    windows_path = SKELETON_ROOT / "window_manifest.csv"
    for required in (protocol_path, videos_path, windows_path):
        if not required.is_file():
            raise FileNotFoundError(f"Required AIRT Lab experiment input is missing: {required}")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    video_rows = _read_csv(videos_path)
    window_rows = _read_csv(windows_path)
    if protocol.get("dataset_protocol") != "airtlab-mc3-4s-2s-16f-rtmpose-m-v1":
        raise ValueError("The skeleton dataset protocol does not match the 4s16f2 experiment.")
    if len(video_rows) != 350 or len(window_rows) != 798:
        raise ValueError(f"Unexpected manifest sizes: videos={len(video_rows)}, windows={len(window_rows)} (expected 350/798).")

    raw_root = Path(protocol.get("source_root", RAW_VIDEO_ROOT)).resolve()
    if raw_root != RAW_VIDEO_ROOT.resolve():
        raise ValueError(f"The AIRT Lab source path changed: {raw_root}")
    windows_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in window_rows:
        source_relative = _normalise_relative(raw["source_relative"])
        indices = json.loads(raw["source_frame_indices"])
        frame_paths = json.loads(raw["frame_paths"])
        if len(indices) != 16 or len(frame_paths) != 16:
            raise ValueError(f"Window {raw.get('sample_id')} does not contain exactly 16 aligned frames.")
        windows_by_source[source_relative].append({
            **raw,
            "source_relative": source_relative,
            "source_frame_indices_list": [int(value) for value in indices],
            "frame_paths_list": [str(value) for value in frame_paths],
        })

    provisional: list[SampleRecord] = []
    groups_by_label: dict[int, list[str]] = defaultdict(list)
    group_cameras: dict[str, set[str]] = defaultdict(set)
    group_labels: dict[str, set[int]] = defaultdict(set)
    for index, row in enumerate(video_rows, start=1):
        if row.get("status") != "completed":
            raise RuntimeError(f"AIRT Lab skeleton extraction is incomplete for {row.get('source_relative')}.")
        source_relative = _normalise_relative(row["source_relative"])
        source_relative_path = Path(source_relative)
        if source_relative_path.is_absolute() or ".." in source_relative_path.parts:
            raise ValueError(f"AIRT Lab source path escapes its root: {source_relative}")
        source = raw_root / source_relative_path
        source_bytes = int(row["source_bytes"])
        source_mtime_ns = 0
        if verify_source_files or verify_source_hashes:
            source = source.resolve()
            if not source.is_file():
                raise FileNotFoundError(f"AIRT Lab source video is missing: {source}")
            stat = source.stat()
            if stat.st_size != source_bytes:
                raise RuntimeError(f"Source video size changed after pose extraction: {source}")
            source_mtime_ns = stat.st_mtime_ns
        source_hash = str(row["source_sha256"]).lower()
        if verify_source_hashes:
            actual_hash = _sha256_file(source)
            if actual_hash != source_hash:
                raise RuntimeError(f"Source video hash changed after pose extraction: {source}")
            if logger and (index == 1 or index % 25 == 0 or index == len(video_rows)):
                logger.info("Source integrity %d/%d | SHA-256 matches skeleton extraction manifest", index, len(video_rows))

        label_name = row["label"]
        if label_name not in {"violent", "non-violent"}:
            raise ValueError(f"Unexpected AIRT Lab class: {label_name}")
        label = 1 if label_name == "violent" else 0
        group_id = str(row["group_id"])
        camera = str(row["camera"])
        groups_by_label[label].append(group_id)
        group_cameras[group_id].add(camera)
        group_labels[group_id].add(label)

        matching_windows = sorted(windows_by_source.get(source_relative, []), key=lambda item: int(item["window_index"]))
        if not matching_windows:
            raise ValueError(f"No skeleton windows found for source video {source_relative}")
        fps = float(row["fps"])
        frame_count = int(row["source_frame_count"])
        duration = four.duration_from_frame_count(fps, frame_count, row.get("duration_seconds"))
        expected_windows = four.make_four_second_windows(duration, fps, frame_count)
        if len(expected_windows) != len(matching_windows):
            raise ValueError(f"Native/skeleton window count mismatch for {source_relative}.")
        frame_paths_by_index: dict[int, Path] = {}
        for window_index, (expected, actual) in enumerate(zip(expected_windows, matching_windows)):
            actual_indices = tuple(actual["source_frame_indices_list"])
            if actual_indices != expected.frame_indices:
                raise ValueError(f"Native/skeleton frame-index mismatch for {source_relative}, window {window_index}.")
            if abs(float(actual["window_start_seconds"]) - expected.start) > 1e-5 or abs(float(actual["window_end_seconds"]) - expected.end) > 1e-5:
                raise ValueError(f"Native/skeleton window-time mismatch for {source_relative}, window {window_index}.")
            for source_index, relative_frame in zip(actual_indices, actual["frame_paths_list"]):
                relative_frame_path = Path(relative_frame)
                if relative_frame_path.is_absolute() or ".." in relative_frame_path.parts:
                    raise ValueError(f"Skeleton frame path escapes its dataset folder: {relative_frame}")
                frame_path = SKELETON_ROOT / relative_frame_path
                if verify_skeleton_files and not frame_path.is_file():
                    raise FileNotFoundError(f"Required skeleton frame is missing: {frame_path}")
                previous = frame_paths_by_index.setdefault(int(source_index), frame_path)
                if previous != frame_path:
                    raise ValueError(f"Conflicting skeleton frame mapping for {source_relative} at source frame {source_index}.")

        record = core.VideoRecord(
            dataset="AIRTLab",
            split="pending",
            class_name="Fight" if label else "NonFight",
            label=label,
            path=source,
            fps=fps,
            frame_count=frame_count,
            duration=duration,
            size_bytes=source_bytes,
            mtime_ns=source_mtime_ns,
        )
        provisional.append(SampleRecord(record, source_relative, group_id, camera, label_name, source_hash, matching_windows, frame_paths_by_index))

    for group_id, cameras in group_cameras.items():
        if cameras != {"cam1", "cam2"}:
            raise ValueError(f"Event group {group_id} must include exactly cam1 and cam2; found {sorted(cameras)}")
        if len(group_labels[group_id]) != 1:
            raise ValueError(f"Event group {group_id} contains conflicting class labels.")
    splits = assign_group_splits(groups_by_label, seed=2026)
    for item in provisional:
        item.record.split = splits[item.group_id]

    expected_class_groups = {0: 60, 1: 115}
    observed_class_groups = {label: len(set(groups)) for label, groups in groups_by_label.items()}
    if observed_class_groups != expected_class_groups:
        raise ValueError(f"Unexpected AIRT Lab event-group class counts: {observed_class_groups}")
    return provisional, protocol


def make_split_manifest(samples: Sequence[SampleRecord]) -> list[dict[str, Any]]:
    return [
        {
            "source_relative": item.source_relative,
            "group_id": item.group_id,
            "camera": item.camera,
            "label_name": item.label_name,
            "label": item.record.label,
            "split": item.record.split,
            "window_count": len(item.window_rows),
            "source_sha256": item.source_sha256,
        }
        for item in sorted(samples, key=lambda item: item.source_relative.casefold())
    ]


def split_summary(samples: Sequence[SampleRecord]) -> dict[str, Any]:
    report: dict[str, Any] = {split: {"event_groups": 0, "videos": 0, "windows": 0, "violent_videos": 0, "nonviolent_videos": 0} for split in ("train", "val", "test")}
    groups_per_split: dict[str, set[str]] = defaultdict(set)
    for item in samples:
        row = report[item.record.split]
        row["videos"] += 1
        row["windows"] += len(item.window_rows)
        row["violent_videos" if item.record.label else "nonviolent_videos"] += 1
        groups_per_split[item.record.split].add(item.group_id)
    for split, groups in groups_per_split.items():
        report[split]["event_groups"] = len(groups)
    return report


def _write_pose_frame_cache(
    samples: Sequence[SampleRecord], cache_dir: Path, cv2: Any, np: Any, logger: logging.Logger,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    for count, item in enumerate(samples, start=1):
        cache_path = cache_dir / f"{item.source_sha256[:32]}.npz"
        item.record.legacy_cache_path = None
        item.record.supplement_cache_path = cache_path
        item.record.cache_path = cache_path
        item.record.required_frame_indices = tuple(sorted(item.frame_paths_by_index))
        if not cache_path.is_file():
            indices = item.record.required_frame_indices
            frames: list[Any] = []
            for frame_index in indices:
                frame_path = item.frame_paths_by_index[frame_index]
                bgr = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
                if bgr is None:
                    raise RuntimeError(f"OpenCV could not read skeleton image: {frame_path}")
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                frames.append(core._resize_short_side(rgb, cv2))
            array = np.stack(frames).astype(np.uint8, copy=False)
            temporary = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.tmp")
            try:
                with temporary.open("wb") as handle:
                    np.savez_compressed(handle, frame_indices=np.asarray(indices, dtype=np.int32), frames=array)
                os.replace(temporary, cache_path)
            finally:
                temporary.unlink(missing_ok=True)
        if count == 1 or count % 25 == 0 or count == len(samples):
            total_bytes = sum(path.stat().st_size for path in cache_dir.glob("*.npz") if path.is_file())
            logger.info(
                "Skeleton frame cache %d/%d | cached=%d | size=%.2f GiB | elapsed=%.1fs",
                count, len(samples), len(list(cache_dir.glob("*.npz"))), total_bytes / (1024**3), time.monotonic() - started,
            )


def _prepare_native_cache(samples: Sequence[SampleRecord], cv2: Any, np: Any, logger: logging.Logger) -> None:
    records = [item.record for item in samples]
    four._prepare_hybrid_cache(records, NATIVE_CACHE_ROOT, cv2, np, logger, rebuild=False)


def _protocol_snapshot(modality: str, seed: int) -> dict[str, Any]:
    source = four._protocol()
    keys = (
        "protocol", "window_seconds", "stride_seconds", "frames_per_window", "sampling_rate_fps",
        "frame_sample_positions", "short_side", "crop_size", "color_space",
        "normalization_mean", "normalization_std", "short_clip_policy", "tail_policy", "video_aggregation",
    )
    return {
        "experiment_protocol": EXPERIMENT_PROTOCOL,
        "input_modality": modality,
        "seed": seed,
        **{key: source[key] for key in keys if key in source},
    }


def _deployment_payload(model: Any, torch: Any, *, modality: str, best_epoch: int, best_auc: float, seed: int) -> dict[str, Any]:
    return {
        "deployment_checkpoint_version": 1,
        "model_name": "mc3_18",
        "state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
        "class_to_idx": core.CLASS_TO_LABEL,
        "protocol": {
            **_protocol_snapshot(modality, seed),
            "best_epoch": best_epoch,
            "best_validation_roc_auc": best_auc,
        },
    }


def _atomic_torch_save(payload: dict[str, Any], path: Path, torch: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        torch.save(payload, temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def evaluate_videos(
    samples: Sequence[SampleRecord], model: Any, *, split: str, epoch: int, seed: int,
    np: Any, cv2: Any, torch: Any, device: Any, amp: bool, window_batch_size: int,
    logger: logging.Logger, modality: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected = [item for item in samples if item.record.split == split]
    predictions: list[dict[str, Any]] = []
    window_predictions: list[dict[str, Any]] = []
    started = time.monotonic()
    for index, item in enumerate(selected, start=1):
        clips, windows = core._record_bag_tensor(
            item.record, training=False, epoch=epoch, seed=seed, np=np, cv2=cv2, torch=torch,
        )
        video_probability, probabilities = core._predict_bag(
            model, clips, torch, device, amp=amp, window_batch_size=window_batch_size,
        )
        predictions.append({
            "modality": modality,
            "split": split,
            "source_relative": item.source_relative,
            "group_id": item.group_id,
            "camera": item.camera,
            "label_name": item.label_name,
            "label": item.record.label,
            "probability_violent": video_probability,
            "window_count": len(windows),
        })
        for window_index, (window, probability) in enumerate(zip(windows, probabilities)):
            window_predictions.append({
                "modality": modality,
                "split": split,
                "source_relative": item.source_relative,
                "group_id": item.group_id,
                "camera": item.camera,
                "label": item.record.label,
                "window_index": window_index,
                "window_start_seconds": window.start,
                "window_end_seconds": window.end,
                "probability_violent": probability,
            })
        if index == 1 or index % 10 == 0 or index == len(selected):
            elapsed = time.monotonic() - started
            eta = elapsed / index * (len(selected) - index) if index else 0.0
            logger.info("%s %s inference %d/%d videos | elapsed=%.1fs | ETA=%.1fs", modality, split, index, len(selected), elapsed, eta)
    return predictions, window_predictions


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _fit_one_modality(
    modality: str, samples: Sequence[SampleRecord], run_dir: Path, *, args: argparse.Namespace,
    cv2: Any, np: Any, torch: Any, nn: Any, model_symbols: tuple[Any, Any], device: Any,
    logger: logging.Logger,
) -> dict[str, Any]:
    weights_enum, model_builder = model_symbols
    mode_dir = run_dir / MODE_NAMES[modality]
    mode_dir.mkdir(parents=True, exist_ok=True)
    mode_logger = _log_setup(mode_dir)
    amp = device.type == "cuda" and not args.no_amp
    train_samples = [item for item in samples if item.record.split == "train"]
    val_samples = [item for item in samples if item.record.split == "val"]
    if not train_samples or not val_samples or {item.record.label for item in train_samples} != {0, 1} or {item.record.label for item in val_samples} != {0, 1}:
        raise RuntimeError(f"{modality}: train and validation must both contain both classes.")

    core._set_seed(args.seed, torch, np)
    model = core._build_model(torch, nn, weights_enum, model_builder, device, pretrained=True)
    optimizer = torch.optim.AdamW(
        [
            {"params": [parameter for name, parameter in model.named_parameters() if not name.startswith("fc.")], "lr": args.backbone_lr},
            {"params": list(model.fc.parameters()), "lr": args.head_lr},
        ],
        weight_decay=args.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=0.5, patience=2, min_lr=1e-6,
    )
    scaler = core._make_scaler(torch, amp)
    criterion = nn.BCEWithLogitsLoss()
    best_auc = -float("inf")
    best_epoch = 0
    patience_best_auc = -float("inf")
    bad_epochs = 0
    history: list[dict[str, Any]] = []

    mode_logger.info(
        "START modality=%s | device=%s | amp=%s | init=TorchVision MC3-18 Kinetics-400 | train=%d videos/%d windows | val=%d videos",
        modality, device, amp, len(train_samples), sum(len(item.window_rows) for item in train_samples), len(val_samples),
    )
    for epoch in range(1, args.epochs + 1):
        epoch_start = time.monotonic()
        if device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(device)
        model.train()
        core._freeze_batch_norm_running_stats(model, nn)
        ordered = list(train_samples)
        random.Random(args.seed + epoch).shuffle(ordered)
        optimizer.zero_grad(set_to_none=True)
        accumulation_count = 0
        total_loss = 0.0
        train_labels: list[int] = []
        train_probabilities: list[float] = []

        for index, item in enumerate(ordered, start=1):
            clips, _ = core._record_bag_tensor(
                item.record, training=True, epoch=epoch, seed=args.seed, np=np, cv2=cv2, torch=torch,
            )
            hard_window, _ = core._choose_hard_instance(
                model, clips, torch, device, amp=amp, window_batch_size=args.window_batch_size,
            )
            selected = clips[hard_window : hard_window + 1].to(device, non_blocking=True)
            target = torch.tensor([item.record.label], dtype=torch.float32, device=device)
            with core._amp_context(torch, device, amp):
                logits = model(selected)
                bag_logit = core._binary_scores(logits).reshape(1)
                loss = criterion(bag_logit, target)
            scaler.scale(loss / args.grad_accum).backward()
            accumulation_count += 1
            total_loss += float(loss.detach().float().item())
            train_probabilities.append(float(torch.sigmoid(bag_logit.detach().float()).item()))
            train_labels.append(item.record.label)
            if accumulation_count == args.grad_accum:
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad(set_to_none=True)
                accumulation_count = 0
            if index == 1 or index % args.log_every == 0 or index == len(ordered):
                elapsed = time.monotonic() - epoch_start
                eta = elapsed / index * (len(ordered) - index) if index else 0.0
                mode_logger.info(
                    "epoch %d/%d train %d/%d videos | loss=%.5f | lr(backbone)=%.2e | elapsed=%.1fs | ETA=%.1fs",
                    epoch, args.epochs, index, len(ordered), total_loss / index, optimizer.param_groups[0]["lr"], elapsed, eta,
                )
        if accumulation_count:
            scaler.unscale_(optimizer)
            correction = args.grad_accum / accumulation_count
            for parameter in model.parameters():
                if parameter.grad is not None:
                    parameter.grad.mul_(correction)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad(set_to_none=True)

        train_auc = core.roc_auc(train_labels, train_probabilities)
        train_loss = total_loss / max(len(ordered), 1)
        val_predictions, _ = evaluate_videos(
            samples, model, split="val", epoch=epoch, seed=args.seed, np=np, cv2=cv2, torch=torch,
            device=device, amp=amp, window_batch_size=args.window_batch_size,
            logger=mode_logger, modality=modality,
        )
        val_labels = [int(row["label"]) for row in val_predictions]
        val_probabilities = [float(row["probability_violent"]) for row in val_predictions]
        val_auc = core.roc_auc(val_labels, val_probabilities)
        if val_auc is None:
            raise RuntimeError(f"{modality}: validation ROC-AUC undefined; both classes are required.")
        scheduler.step(val_auc)
        val_at_half = core.binary_metrics(val_labels, val_probabilities, 0.5)
        improved = val_auc > best_auc
        significant_improvement = val_auc > patience_best_auc + args.min_delta
        if improved:
            best_auc, best_epoch = val_auc, epoch
            best_payload = _deployment_payload(model, torch, modality=modality, best_epoch=best_epoch, best_auc=best_auc, seed=args.seed)
            _atomic_torch_save(best_payload, mode_dir / "best_model.pt", torch)
        if significant_improvement:
            patience_best_auc = val_auc
            bad_epochs = 0
        else:
            bad_epochs += 1

        epoch_seconds = time.monotonic() - epoch_start
        history_row = {
            "epoch": epoch,
            "train_loss": train_loss,
            "train_roc_auc": train_auc,
            "validation_roc_auc": val_auc,
            "validation_f1_at_0_5": val_at_half["f1"],
            "learning_rate_backbone": optimizer.param_groups[0]["lr"],
            "learning_rate_head": optimizer.param_groups[1]["lr"],
            "seconds": epoch_seconds,
            "improved_best_auc": int(improved),
            "patience_counter": bad_epochs,
        }
        if device.type == "cuda":
            history_row["peak_gpu_memory_mb"] = torch.cuda.max_memory_allocated(device) / (1024 * 1024)
        history.append(history_row)
        _write_csv(mode_dir / "training_history.csv", history)
        _write_csv(mode_dir / "validation_epoch_predictions.csv", val_predictions)

        # Keep a durable, epoch-complete recovery checkpoint for each arm.
        recovery = {
            "experiment_protocol": EXPERIMENT_PROTOCOL,
            "modality": modality,
            "completed_epoch": epoch,
            "model_state": model.state_dict(),
            "optimizer_state": optimizer.state_dict(),
            "scheduler_state": scheduler.state_dict(),
            "scaler_state": scaler.state_dict(),
            "best_epoch": best_epoch,
            "best_auc": best_auc,
            "patience_best_auc": patience_best_auc,
            "bad_epochs": bad_epochs,
            "history": history,
            "split_manifest_sha256": hashlib.sha256((run_dir / "split_manifest.csv").read_bytes()).hexdigest(),
        }
        _atomic_torch_save(recovery, mode_dir / "last.pt", torch)
        peak = history_row.get("peak_gpu_memory_mb", 0.0)
        mode_logger.info(
            "EPOCH %d/%d COMPLETE | train_loss=%.5f | train_auc=%s | val_auc=%.6f | val_f1@0.5=%.4f | best_val_auc=%.6f@%d | early_stop=%d/%d | peak_gpu=%.0f MiB | epoch=%.1fs",
            epoch, args.epochs, train_loss, "n/a" if train_auc is None else f"{train_auc:.6f}", val_auc,
            val_at_half["f1"], best_auc, best_epoch, bad_epochs, args.patience, peak, epoch_seconds,
        )
        logger.info("%s progress: completed epoch %d/%d (best validation AUC %.6f at epoch %d)", modality, epoch, args.epochs, best_auc, best_epoch)
        if bad_epochs >= args.patience:
            mode_logger.info("EARLY STOP after epoch %d: validation AUC did not improve by min_delta=%.4f for %d epochs.", epoch, args.min_delta, args.patience)
            break

    best_path = mode_dir / "best_model.pt"
    if not best_path.is_file():
        raise RuntimeError(f"{modality}: no validation-selected best checkpoint was saved.")
    best_payload = torch.load(best_path, map_location="cpu", weights_only=True)
    model.load_state_dict(best_payload["state_dict"])
    model.eval()
    mode_logger.info("Locked checkpoint: epoch %d | validation ROC-AUC %.6f. Test evaluation starts now.", best_epoch, best_auc)

    val_predictions, val_window_predictions = evaluate_videos(
        samples, model, split="val", epoch=best_epoch, seed=args.seed, np=np, cv2=cv2, torch=torch,
        device=device, amp=amp, window_batch_size=args.window_batch_size,
        logger=mode_logger, modality=modality,
    )
    val_labels = [int(row["label"]) for row in val_predictions]
    val_probabilities = [float(row["probability_violent"]) for row in val_predictions]
    selected_thresholds = core.choose_thresholds(val_labels, val_probabilities)
    f1_threshold = float(selected_thresholds["moderate"]["threshold"])
    threshold_summary = {
        "selection_rule": "maximize validation video-level F1; ties use the highest threshold",
        "threshold": f1_threshold,
        "validation_f1": float(selected_thresholds["moderate"]["validation_score"]),
    }
    _write_csv(mode_dir / "validation_predictions.csv", val_predictions)
    _write_csv(mode_dir / "validation_window_predictions.csv", val_window_predictions)

    # No test-set prediction is made until best weights and the validation-only threshold are locked.
    test_predictions, test_window_predictions = evaluate_videos(
        samples, model, split="test", epoch=best_epoch, seed=args.seed, np=np, cv2=cv2, torch=torch,
        device=device, amp=amp, window_batch_size=args.window_batch_size,
        logger=mode_logger, modality=modality,
    )
    _write_csv(mode_dir / "test_predictions.csv", test_predictions)
    _write_csv(mode_dir / "test_window_predictions.csv", test_window_predictions)

    def metrics_for(rows: Sequence[dict[str, Any]], threshold: float) -> dict[str, Any]:
        labels = [int(row["label"]) for row in rows]
        probabilities = [float(row["probability_violent"]) for row in rows]
        return core.binary_metrics(labels, probabilities, threshold)

    result = {
        "modality": modality,
        "input_representation": MODE_NAMES[modality],
        "best_epoch": best_epoch,
        "best_validation_roc_auc": best_auc,
        "early_stopped_after_epoch": int(history[-1]["epoch"]),
        "validation_best_f1_threshold": threshold_summary,
        "metric_groups": {
            "threshold_0_5": {
                "threshold": 0.5,
                "validation": metrics_for(val_predictions, 0.5),
                "test": metrics_for(test_predictions, 0.5),
            },
            "validation_best_f1_threshold": {
                "threshold": f1_threshold,
                "validation": metrics_for(val_predictions, f1_threshold),
                "test": metrics_for(test_predictions, f1_threshold),
            },
        },
        "counts": {
            "validation_videos": len(val_predictions),
            "test_videos": len(test_predictions),
            "validation_windows": len(val_window_predictions),
            "test_windows": len(test_window_predictions),
        },
        "artifacts": {
            "best_checkpoint": str(best_path.resolve()),
            "last_checkpoint": str((mode_dir / "last.pt").resolve()),
            "history": str((mode_dir / "training_history.csv").resolve()),
        },
    }
    (mode_dir / "final_report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    mode_logger.info(
        "RESULT | best epoch=%d | val AUC=%.6f | F1-optimal validation threshold=%.6f (val F1=%.4f) | test AUC=%.6f",
        best_epoch, best_auc, f1_threshold,
        result["metric_groups"]["validation_best_f1_threshold"]["validation"]["f1"],
        result["metric_groups"]["threshold_0_5"]["test"]["roc_auc"],
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", help="Existing empty/new run directory. Defaults to a timestamped directory.")
    parser.add_argument("--output-root", default=str(OUTPUT_ROOT))
    parser.add_argument("--plan-only", action="store_true", help="Validate manifests and print split/window counts; do not cache or train.")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--allow-cpu", action="store_true")
    parser.add_argument("--no-amp", action="store_true")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--min-delta", type=float, default=0.001)
    parser.add_argument("--backbone-lr", type=float, default=2e-5)
    parser.add_argument("--head-lr", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--window-batch-size", type=int, default=2)
    parser.add_argument("--log-every", type=int, default=10)
    parser.add_argument("--seed", type=int, default=2026)
    return parser


def validate_reference_recipe(args: argparse.Namespace) -> dict[str, Any]:
    if not SOURCE_RUN_CONFIG.is_file():
        raise FileNotFoundError(f"The requested 4s16f2 reference training config is missing: {SOURCE_RUN_CONFIG}")
    reference = json.loads(SOURCE_RUN_CONFIG.read_text(encoding="utf-8"))
    keys = ("seed", "epochs", "patience", "min_delta", "backbone_lr", "head_lr", "weight_decay", "grad_accum", "window_batch_size")
    mismatches = [f"{key}: requested={getattr(args, key)!r}, 4s16f2={reference.get(key)!r}" for key in keys if getattr(args, key) != reference.get(key)]
    if mismatches:
        raise ValueError("Training parameters must match the recorded 4s16f2 run exactly: " + "; ".join(mismatches))
    if args.no_amp and bool(reference.get("amp", False)):
        raise ValueError("The 4s16f2 reference used AMP; --no-amp would make this comparison inconsistent.")
    preprocessing = reference.get("preprocessing", {})
    expected_preprocessing = {"window_seconds": 4.0, "stride_seconds": 2.0, "frames_per_window": 16, "short_side": 128, "crop_size": 112}
    differences = [key for key, value in expected_preprocessing.items() if preprocessing.get(key) != value]
    if differences:
        raise ValueError("The 4s16f2 reference preprocessing differs from this experiment: " + ", ".join(differences))
    return reference


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.epochs < 1 or args.patience < 1 or args.grad_accum < 1 or args.window_batch_size < 1 or args.log_every < 1:
        raise SystemExit("epochs, patience, grad-accum, window-batch-size and log-every must be positive")
    if args.backbone_lr <= 0 or args.head_lr <= 0 or args.weight_decay < 0 or args.min_delta < 0:
        raise SystemExit("learning rates must be positive; weight decay and min delta must be non-negative")
    reference_recipe = validate_reference_recipe(args)

    samples, source_protocol = load_dataset_manifest(verify_source_hashes=False)
    summary = split_summary(samples)
    split_manifest = make_split_manifest(samples)
    print("AIRT Lab paired-view split plan (event-group stratified):", flush=True)
    print(json.dumps(summary, indent=2), flush=True)
    if args.plan_only:
        print(f"Total: videos={len(samples)}, windows={sum(len(item.window_rows) for item in samples)}; cam1/cam2 event groups stay together.", flush=True)
        return 0

    output_root = Path(args.output_root).expanduser().resolve()
    run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else output_root / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(f"Run directory is not empty; refusing to overwrite: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    logger = _log_setup(run_dir)
    logger.info("AIRT Lab two-modality MC3-18 experiment | run_dir=%s", run_dir)
    logger.info("Input source: %s", RAW_VIDEO_ROOT)
    logger.info("Skeleton source: %s", SKELETON_ROOT)
    logger.info("Source 4s16f2 configuration: %s", SOURCE_RUN_CONFIG)
    logger.info("Verified 4s16f2 recipe | %s", json.dumps({key: reference_recipe[key] for key in ("seed", "epochs", "patience", "min_delta", "backbone_lr", "head_lr", "weight_decay", "grad_accum", "window_batch_size", "amp")}, sort_keys=True))
    logger.info("Split: 60/20/20 by event group, seed=%d; paired cameras never cross splits.", args.seed)

    if args.seed != 2026:
        # The current audited split is defined by seed 2026; do not allow accidental divergence.
        raise ValueError("This experiment's predeclared paired-camera split is fixed to seed 2026.")
    core._write_csv(run_dir / "split_manifest.csv", split_manifest)
    config = {
        "experiment_protocol": EXPERIMENT_PROTOCOL,
        "source_4s16f2_config": str(SOURCE_RUN_CONFIG.resolve()),
        "source_4s16f2_config_sha256": _sha256_file(SOURCE_RUN_CONFIG),
        "initialization": "TorchVision MC3-18 Kinetics-400 pretrained weights; identical initialization recipe and seed for both arms",
        "optimizer": "AdamW",
        "scheduler": "ReduceLROnPlateau(mode=max, factor=0.5, patience=2, min_lr=1e-6)",
        "seed": args.seed,
        "epochs": args.epochs,
        "patience": args.patience,
        "min_delta": args.min_delta,
        "backbone_lr": args.backbone_lr,
        "head_lr": args.head_lr,
        "weight_decay": args.weight_decay,
        "grad_accum": args.grad_accum,
        "window_batch_size": args.window_batch_size,
        "amp": not args.no_amp,
        "device_requested": args.device,
        "split_ratios": SPLIT_RATIOS,
        "split_counts": summary,
        "protocol": four.PROTOCOL,
        "window_seconds": 4.0,
        "stride_seconds": 2.0,
        "frames_per_window": 16,
        "video_aggregation": "maximum_window_probability",
        "training_unit": "one hard-max window per source video per epoch (video-level MIL), gradient accumulation from 4s16f2",
        "metric_unit": "camera video; paired camera views share an event group and are not independent events",
        "threshold_policy": "validation-only F1-optimal threshold; the locked threshold is applied unchanged to test; also report fixed 0.5",
        "test_policy": "test is evaluated once after validation-AUC best checkpoint and validation threshold are locked",
        "source_protocol": source_protocol,
        "input_cache": {
            "native_shared_cache": str(NATIVE_CACHE_ROOT.resolve()),
            "native_legacy_cache_read_only": str(LEGACY_CACHE_ROOT.resolve()),
            "skeleton_cache": str((run_dir / "skeleton_frame_cache").resolve()),
            "skeleton_cache_retained": True,
        },
    }
    (run_dir / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    split_digest = hashlib.sha256((run_dir / "split_manifest.csv").read_bytes()).hexdigest()
    (run_dir / "preflight.json").write_text(json.dumps({"status": "running", "split_manifest_sha256": split_digest}, indent=2), encoding="utf-8")

    cv2, np, torch, nn, model_symbols = core._import_runtime_dependencies()
    if args.device == "auto":
        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        if device.type == "cpu" and not args.allow_cpu:
            raise RuntimeError("CUDA is unavailable; CPU training is disabled unless --allow-cpu is explicitly supplied.")
    elif args.device == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested, but PyTorch cannot see a CUDA device.")
        device = torch.device("cuda:0")
    else:
        if not args.allow_cpu:
            raise RuntimeError("CPU training is disabled by default; add --allow-cpu only if you intend a slow CPU run.")
        device = torch.device("cpu")
    logger.info("Runtime | Python=%s | PyTorch=%s | TorchVision=%s | device=%s | GPU=%s", sys.version.split()[0], torch.__version__, __import__("torchvision").__version__, device, torch.cuda.get_device_name(0) if device.type == "cuda" else "n/a")

    # Install the exact 4s16f2 preprocessing/model helpers, while restricting data to AIRT Lab.
    four._ACTIVE_SEED = args.seed
    four.LEGACY_CACHE_ROOT = LEGACY_CACHE_ROOT
    four.SUPPLEMENT_CACHE_ROOT = NATIVE_CACHE_ROOT
    four.AIRTLAB_ROOT = RAW_VIDEO_ROOT
    four._install_protocol()
    logger.info("Checking all original-video hashes against the pose-extraction manifest...")
    samples, source_protocol = load_dataset_manifest(verify_source_hashes=True, logger=logger)
    summary = split_summary(samples)
    core._write_csv(run_dir / "split_manifest.csv", make_split_manifest(samples))
    logger.info("Split locked | %s", json.dumps(summary, ensure_ascii=False))

    logger.info("Preparing/reusing native 4s16f2 frame cache...")
    _prepare_native_cache(samples, cv2, np, logger)
    pose_cache_dir = run_dir / "skeleton_frame_cache"
    logger.info("Preparing persistent resized-skeleton cache...")
    _write_pose_frame_cache(samples, pose_cache_dir, cv2, np, logger)
    logger.info("Preflight: verifying one native and one skeleton bag tensor and MC3 forward pass before any training.")
    four._install_protocol()
    probe_model = core._build_model(torch, nn, model_symbols[0], model_symbols[1], device, pretrained=True)
    probe_model.eval()
    for item in (next(x for x in samples if x.record.split == "train"), next(x for x in samples if x.record.split == "val")):
        for modality in ("native", "skeleton"):
            if modality == "native":
                cache_path = four._supplement_cache_path(item.record)
                item.record.legacy_cache_path = four._legacy_cache_path(item.record)
                item.record.supplement_cache_path = cache_path
                item.record.required_frame_indices = tuple(sorted(item.frame_paths_by_index))
            else:
                item.record.legacy_cache_path = None
                item.record.supplement_cache_path = pose_cache_dir / f"{item.source_sha256[:32]}.npz"
                item.record.required_frame_indices = tuple(sorted(item.frame_paths_by_index))
            clips, _ = core._record_bag_tensor(item.record, training=False, epoch=0, seed=args.seed, np=np, cv2=cv2, torch=torch)
            if clips.ndim != 5 or clips.shape[1:] != (3, 16, 112, 112):
                raise RuntimeError(f"{modality} preflight produced unexpected MC3 input shape {tuple(clips.shape)}")
            with torch.inference_mode(), core._amp_context(torch, device, device.type == "cuda" and not args.no_amp):
                logits = probe_model(clips[:1].to(device))
            if tuple(logits.shape) != (1, 2) or not torch.isfinite(logits).all():
                raise RuntimeError(f"{modality} MC3 forward smoke test returned invalid logits.")
            logger.info("Preflight OK | split=%s | modality=%s | bag=%s | output=%s", item.record.split, modality, tuple(clips.shape), tuple(logits.shape))
    del probe_model
    if device.type == "cuda":
        torch.cuda.empty_cache()
    (run_dir / "preflight.json").write_text(json.dumps({"status": "passed", "split_manifest_sha256": split_digest, "videos": len(samples), "windows": sum(len(x.window_rows) for x in samples), "device": str(device)}, indent=2), encoding="utf-8")
    logger.info("PREFLIGHT PASSED. Training will now start; test predictions remain untouched until each arm completes.")

    results: dict[str, Any] = {}
    for modality in ("native", "skeleton"):
        # Set cache routing for the selected representation without changing frame indices.
        for item in samples:
            item.record.legacy_cache_path = four._legacy_cache_path(item.record) if modality == "native" else None
            item.record.supplement_cache_path = (
                four._supplement_cache_path(item.record) if modality == "native"
                else pose_cache_dir / f"{item.source_sha256[:32]}.npz"
            )
            item.record.cache_path = item.record.supplement_cache_path
            item.record.required_frame_indices = tuple(sorted(item.frame_paths_by_index))
        results[modality] = _fit_one_modality(
            modality, samples, run_dir, args=args, cv2=cv2, np=np, torch=torch, nn=nn,
            model_symbols=model_symbols, device=device, logger=logger,
        )

    summary_report = {
        "experiment_protocol": EXPERIMENT_PROTOCOL,
        "status": "completed",
        "split": summary,
        "video_count": len(samples),
        "window_count": sum(len(item.window_rows) for item in samples),
        "training_config": config,
        "results": results,
        "interpretation_note": "AIRT Lab contains paired camera views. Metrics count videos, while splits are grouped by shared event; do not treat paired views as independent events.",
    }
    (run_dir / "final_report.json").write_text(json.dumps(summary_report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    logger.info("EXPERIMENT COMPLETE | native best epoch=%d val_AUC=%.6f test_AUC=%.6f | skeleton best epoch=%d val_AUC=%.6f test_AUC=%.6f", results["native"]["best_epoch"], results["native"]["best_validation_roc_auc"], results["native"]["metric_groups"]["threshold_0_5"]["test"]["roc_auc"], results["skeleton"]["best_epoch"], results["skeleton"]["best_validation_roc_auc"], results["skeleton"]["metric_groups"]["threshold_0_5"]["test"]["roc_auc"])
    logger.info("Final report: %s", run_dir / "final_report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
