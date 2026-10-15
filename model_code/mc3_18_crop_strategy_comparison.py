"""Compare center, FiveCrop and ThreePosition inference for the saved 4S16F2 MC3-18.

This is evaluation-only. It reads the frozen 4S16F2 dataset manifest and source
videos directly; it does not train, modify, or create frame caches.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_CODE = Path(__file__).resolve().parent
if str(MODEL_CODE) not in sys.path:
    sys.path.insert(0, str(MODEL_CODE))

import mc3_18_four_second_pipeline as four  # noqa: E402
import mc3_18_mil_pipeline as core  # noqa: E402


BASE_RUN_DIR = PROJECT_ROOT / "model_runs" / "mc3_4s16f2" / "run_four_ui_20261007_234526_491833"
FINETUNE_RUN_DIR = (
    PROJECT_ROOT / "model_runs" / "mc3_native_four_dataset_finetune_4s16f2"
    / "run_native_finetune_ui_20261014_093949_230580"
)
FINETUNE_CONFIG = FINETUNE_RUN_DIR / "config.json"
CHECKPOINT = FINETUNE_RUN_DIR / "epoch_01" / "best_model.pt"
MANIFEST = BASE_RUN_DIR / "dataset_manifest.csv"
BASELINE_VALIDATION = FINETUNE_RUN_DIR / "validation_epoch_01_predictions.csv"
BASELINE_TEST = FINETUNE_RUN_DIR / "test_predictions.csv"
OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_crop_strategy_comparison_4s16f2"
DATASET_NAMES = ("Real Life Violence Dataset", "RWF-2000", "AIRTLab")
STRATEGIES = ("center", "three_position", "five_crop")
MODE_OBJECTIVES = {"sensitive": 2.0, "moderate": 1.0, "certain": 0.5}
EXPECTED_COUNTS = {
    ("val", "Real Life Violence Dataset", 0): 93,
    ("val", "Real Life Violence Dataset", 1): 94,
    ("val", "RWF-2000", 0): 97,
    ("val", "RWF-2000", 1): 99,
    ("val", "AIRTLab", 0): 12,
    ("val", "AIRTLab", 1): 24,
    ("test", "Real Life Violence Dataset", 0): 96,
    ("test", "Real Life Violence Dataset", 1): 91,
    ("test", "RWF-2000", 0): 200,
    ("test", "RWF-2000", 1): 200,
    ("test", "AIRTLab", 0): 12,
    ("test", "AIRTLab", 1): 22,
}


def crop_positions(height: int, width: int, strategy: str, crop_size: int = 112) -> list[tuple[int, int]]:
    """Return deterministic (top, left) crop anchors for resized RGB frames."""
    if height < crop_size or width < crop_size:
        raise ValueError(f"Frame {height}x{width} is smaller than crop size {crop_size}.")
    max_top, max_left = height - crop_size, width - crop_size
    center = (max_top // 2, max_left // 2)
    if strategy == "center":
        return [center]
    if strategy == "five_crop":
        # torchvision FiveCrop ordering: top-left, top-right, bottom-left,
        # bottom-right, center. Keep duplicate anchors for exact standard
        # FiveCrop weighting on aspect ratios with a zero margin on one axis.
        return [(0, 0), (0, max_left), (max_top, 0), (max_top, max_left), center]
    if strategy == "three_position":
        # Three evenly spaced anchors along the longer dimension. A square
        # frame follows the vertical convention (top, center, bottom).
        if width > height:
            return [(center[0], 0), center, (center[0], max_left)]
        return [(0, center[1]), center, (max_top, center[1])]
    raise ValueError(f"Unknown crop strategy: {strategy}")


def _normalize_frame_crop(crop: Any, np: Any) -> Any:
    mean = np.asarray(core.MEAN, dtype=np.float32).reshape(1, 1, 3)
    std = np.asarray(core.STD, dtype=np.float32).reshape(1, 1, 3)
    image = crop.astype(np.float32) / 255.0
    return np.ascontiguousarray((image - mean) / std, dtype=np.float32)


def _setup_four_second_protocol(dataset_root: Path, cache_root: Path) -> None:
    """Install the exact 4S16F2 window/sampling protocol without preparing cache."""
    four.LEGACY_DATASET_ROOT = dataset_root
    four.UCF_ROOT = dataset_root / "UCF"
    four.AIRTLAB_ROOT = dataset_root / "AIRTLab" / "A-Dataset-for-Automatic-Violence-Detection-in-Videos-master"
    four.GUARDAID_ROOT = dataset_root / "GuardAId"
    four.LEGACY_CACHE_ROOT = four.DEFAULT_LEGACY_CACHE_ROOT
    four.SUPPLEMENT_CACHE_ROOT = cache_root
    four._ACTIVE_SEED = 2026
    four._install_protocol()


def _airtlab_event_group(path: Path, airtlab_root: Path) -> str:
    relative = path.resolve().relative_to(airtlab_root.resolve())
    parts = list(relative.parts)
    for index, part in enumerate(parts):
        if part.casefold() in {"cam1", "cam2"}:
            del parts[index]
            break
    return "/".join(parts).casefold()


def load_records(manifest_path: Path, dataset_root: Path) -> dict[str, list[core.VideoRecord]]:
    """Load only frozen, eligible validation/test rows for the requested sources."""
    records: dict[str, list[core.VideoRecord]] = {"val": [], "test": []}
    with manifest_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        dataset, split = row.get("dataset", ""), row.get("split", "")
        if dataset not in DATASET_NAMES or split not in records or row.get("eligible") != "1":
            continue
        path = Path(row["path"]).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Manifest source video is missing: {path}")
        stat = path.stat()
        manifest_size = int(row.get("size_bytes") or stat.st_size)
        if stat.st_size != manifest_size:
            raise RuntimeError(f"Source file size changed since the frozen manifest: {path}")
        fps, frame_count = float(row["fps"]), int(row["frame_count"])
        duration = four.duration_from_frame_count(fps, frame_count, row.get("duration_seconds"))
        record = core.VideoRecord(
            dataset=dataset,
            split=split,
            class_name=row["class_name"],
            label=int(row["label"]),
            path=path,
            fps=fps,
            frame_count=frame_count,
            duration=duration,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
        # Retain the event group for paired AIRT Lab camera views in exports.
        record.crop_event_group = (
            _airtlab_event_group(path, four.AIRTLAB_ROOT) if dataset == "AIRTLab" else ""
        )
        records[split].append(record)

    for split, selected in records.items():
        selected.sort(key=lambda record: (DATASET_NAMES.index(record.dataset), str(record.path).casefold()))
        observed: dict[tuple[str, str, int], int] = defaultdict(int)
        for record in selected:
            observed[(split, record.dataset, record.label)] += 1
        for key, expected in EXPECTED_COUNTS.items():
            if key[0] == split and observed.get(key, 0) != expected:
                raise RuntimeError(
                    f"Frozen {split} split count mismatch for {key[1]} label={key[2]}: "
                    f"expected {expected}, found {observed.get(key, 0)}."
                )
        if not selected or {record.label for record in selected} != {0, 1}:
            raise RuntimeError(f"{split} data must contain eligible examples from both classes.")
    _validate_airtlab_pair_splits(records)
    return records


def _validate_airtlab_pair_splits(records: dict[str, list[core.VideoRecord]]) -> None:
    group_split: dict[str, tuple[str, int]] = {}
    for split, selected in records.items():
        for record in selected:
            if record.dataset != "AIRTLab":
                continue
            group = record.crop_event_group
            prior = group_split.get(group)
            current = (split, record.label)
            if prior is not None and prior != current:
                raise RuntimeError(f"AIRT Lab paired camera/source group crosses splits or labels: {group}")
            group_split[group] = current


def _decode_record(record: core.VideoRecord, cv2: Any, np: Any) -> tuple[Any, dict[int, int], list[Any]]:
    windows = four._windows_for_record(record, training=False)
    if not windows:
        raise RuntimeError(f"No 4S16F2 windows could be formed for {record.path}")
    requested = sorted({frame for window in windows for frame in window.frame_indices})
    frames = core._decode_selected_frames(record, requested, cv2, np)
    return frames, {frame_index: index for index, frame_index in enumerate(requested)}, windows


def _window_crop_batch(
    frames: Any,
    index_map: dict[int, int],
    window: Any,
    strategy: str,
    torch: Any,
    np: Any,
) -> Any:
    positions = crop_positions(frames.shape[1], frames.shape[2], strategy, core.CROP_SIZE)
    index_list = [index_map[index] for index in window.frame_indices]
    result = []
    for top, left in positions:
        crop = frames[index_list, top : top + core.CROP_SIZE, left : left + core.CROP_SIZE, :]
        normalized = _normalize_frame_crop(crop, np)
        # T,H,W,C -> C,T,H,W, matching the MC3-18 training tensor layout.
        result.append(torch.from_numpy(np.ascontiguousarray(normalized.transpose(3, 0, 1, 2))))
    return torch.stack(result, dim=0)


def predict_record(
    record: core.VideoRecord,
    frames: Any,
    index_map: dict[int, int],
    windows: Sequence[Any],
    model: Any,
    strategy: str,
    torch: Any,
    np: Any,
    device: Any,
    *,
    amp: bool,
    window_batch_size: int,
) -> dict[str, Any]:
    if window_batch_size < 1:
        raise ValueError("window_batch_size must be positive")
    window_scores: list[float] = []
    with torch.inference_mode():
        # Reproduce the post-finetuning center-crop evaluator's window batch
        # size. Different CUDA convolution batch shapes can shift probabilities
        # slightly, which otherwise makes a valid checkpoint-vs-saved-score
        # regression check spuriously fail.
        batch_windows = window_batch_size if strategy == "center" else 1
        for start in range(0, len(windows), batch_windows):
            window_chunk = windows[start : start + batch_windows]
            crop_batches = [
                _window_crop_batch(frames, index_map, window, strategy, torch, np)
                for window in window_chunk
            ]
            batch = torch.cat(crop_batches, dim=0).to(device, non_blocking=True)
            with core._amp_context(torch, device, amp):
                logits = model(batch)
            crop_probabilities = torch.softmax(logits.float(), dim=1)[:, 1]
            probabilities_by_window = crop_probabilities.reshape(len(window_chunk), -1)
            scores = probabilities_by_window.mean(dim=1).cpu().tolist()
            for score in scores:
                score = float(score)
                if not math.isfinite(score):
                    raise RuntimeError(f"Non-finite probability for {strategy}: {record.path}")
                window_scores.append(score)
    return {
        "strategy": strategy,
        "dataset": record.dataset,
        "split": record.split,
        "class_name": record.class_name,
        "label": int(record.label),
        "path": str(record.path),
        "duration_seconds": float(record.duration),
        "window_count": len(windows),
        "probability_fight": max(window_scores),
        "airtlab_event_group": getattr(record, "crop_event_group", ""),
    }


def _count_rows(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(records),
        "negative": sum(int(row["label"]) == 0 for row in records),
        "positive": sum(int(row["label"]) == 1 for row in records),
        "by_dataset": {
            dataset: {
                "total": sum(row["dataset"] == dataset for row in records),
                "negative": sum(row["dataset"] == dataset and int(row["label"]) == 0 for row in records),
                "positive": sum(row["dataset"] == dataset and int(row["label"]) == 1 for row in records),
            }
            for dataset in DATASET_NAMES
        },
    }


def _metrics(rows: Sequence[dict[str, Any]], threshold: float) -> dict[str, Any]:
    result = core.binary_metrics(
        [int(row["label"]) for row in rows],
        [float(row["probability_fight"]) for row in rows],
        threshold,
    )
    denominator = result["fp"] + result["tn"]
    result["false_positive_rate"] = result["fp"] / denominator if denominator else None
    return result


def _event_level_airtlab_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["dataset"] == "AIRTLab":
            groups[(str(row["airtlab_event_group"]), int(row["label"]))].append(row)
    return [
        {
            "dataset": "AIRTLab event-group average",
            "label": label,
            "probability_fight": sum(float(row["probability_fight"]) for row in members) / len(members),
            "camera_views": len(members),
            "event_group": group,
        }
        for (group, label), members in sorted(groups.items())
    ]


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"Refusing to write an empty results file: {path}")
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_logger(output_dir: Path) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=False)
    logger = logging.getLogger("mc3_crop_strategy_comparison")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(output_dir / "run.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    return logger


def _load_model(torch: Any, nn: Any, model_builder: Any, device: Any) -> tuple[Any, dict[str, Any]]:
    if not CHECKPOINT.is_file():
        raise FileNotFoundError(f"Selected best checkpoint does not exist: {CHECKPOINT}")
    try:
        payload = torch.load(CHECKPOINT, map_location="cpu", weights_only=True)
    except TypeError:
        payload = torch.load(CHECKPOINT, map_location="cpu")
    if payload.get("deployment_checkpoint_version") != 1 or payload.get("model_name") != "mc3_18":
        raise ValueError("The selected file is not an exported MC3-18 deployment checkpoint.")
    if payload.get("class_to_idx") != core.CLASS_TO_LABEL or not isinstance(payload.get("state_dict"), dict):
        raise ValueError("Checkpoint class mapping/state_dict is incompatible with this evaluator.")
    protocol = payload.get("protocol", {})
    if protocol.get("protocol") != four.PROTOCOL:
        raise ValueError(f"Checkpoint protocol mismatch: {protocol.get('protocol')!r}")
    for key, expected in (("window_seconds", 4.0), ("stride_seconds", 2.0), ("frames_per_window", 16),
                          ("short_side", 128), ("crop_size", 112)):
        if protocol.get(key) != expected:
            raise ValueError(f"Checkpoint preprocessing mismatch for {key}: {protocol.get(key)!r}")
    model = core._build_model(torch, nn, None, model_builder, device, pretrained=False)
    model.load_state_dict(payload["state_dict"], strict=True)
    model.eval()
    return model, protocol


def _load_baseline(split: str) -> dict[str, float]:
    path = BASELINE_VALIDATION if split == "val" else BASELINE_TEST
    if not path.is_file():
        raise FileNotFoundError(f"Saved center-crop baseline predictions are missing: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        result = {}
        for row in rows:
            if row.get("dataset") in DATASET_NAMES:
                result[str(Path(row["path"]).resolve())] = float(row["probability_fight"])
    return {path: score for path, score in result.items()
            if Path(path).as_posix().casefold().find("/airtlab/") >= 0
            or "\\airtlab\\" in path.casefold()}


def _compare_center_with_saved(
    rows: Sequence[dict[str, Any]], split: str, tolerance: float = 2e-4,
    *, require_complete: bool = True,
) -> dict[str, float]:
    baseline = _load_baseline(split)
    center = {
        str(Path(row["path"]).resolve()): float(row["probability_fight"])
        for row in rows if row["strategy"] == "center" and row["dataset"] == "AIRTLab"
    }
    if not center.keys() <= baseline.keys():
        extra = len(center.keys() - baseline.keys())
        raise RuntimeError(f"Center baseline contains {extra} unexpected {split} rows.")
    if require_complete and center.keys() != baseline.keys():
        missing = len(baseline.keys() - center.keys())
        raise RuntimeError(f"Center baseline row mismatch for {split}: missing={missing}")
    differences = [abs(center[path] - baseline[path]) for path in center]
    maximum = max(differences, default=0.0)
    mean = sum(differences) / len(differences) if differences else 0.0
    if maximum > tolerance:
        raise RuntimeError(
            f"Center-crop regression against saved {split} predictions failed: "
            f"max_abs_diff={maximum:.8g} > {tolerance}. No final comparison should be trusted."
        )
    return {"n": len(differences), "max_abs_difference": maximum, "mean_abs_difference": mean,
            "tolerance": tolerance}


def _run_split(
    split: str,
    records: Sequence[core.VideoRecord],
    cv2: Any,
    np: Any,
    torch: Any,
    model: Any,
    device: Any,
    logger: logging.Logger,
    *,
    amp: bool,
    window_batch_size: int,
) -> list[dict[str, Any]]:
    started = time.perf_counter()
    output: list[dict[str, Any]] = []
    total_windows = 0
    for index, record in enumerate(records, start=1):
        frames, index_map, windows = _decode_record(record, cv2, np)
        total_windows += len(windows)
        for strategy in STRATEGIES:
            output.append(predict_record(record, frames, index_map, windows, model, strategy,
                                         torch, np, device, amp=amp,
                                         window_batch_size=window_batch_size))
        if index == 1 or index % 10 == 0 or index == len(records):
            elapsed = time.perf_counter() - started
            eta = elapsed / index * (len(records) - index)
            logger.info(
                "%s %d/%d videos | %.1f%% | elapsed %.1f min | ETA %.1f min | windows=%d",
                split, index, len(records), 100 * index / max(len(records), 1), elapsed / 60, eta / 60,
                total_windows,
            )
    return output


def _summarize(
    validation_rows: Sequence[dict[str, Any]], test_rows: Sequence[dict[str, Any]],
    thresholds: dict[str, dict[str, dict[str, float]]],
) -> dict[str, Any]:
    report: dict[str, Any] = {"thresholds_selected_on_validation": thresholds, "validation": {}, "test": {}}
    for split, rows in (("validation", validation_rows), ("test", test_rows)):
        for strategy in STRATEGIES:
            strategy_rows = [row for row in rows if row["strategy"] == strategy]
            report[split][strategy] = {
                "overall": {
                    mode: _metrics(strategy_rows, float(thresholds[strategy][mode]["threshold"]))
                    for mode in MODE_OBJECTIVES
                },
                "by_dataset": {},
            }
            for dataset in DATASET_NAMES:
                subset = [row for row in strategy_rows if row["dataset"] == dataset]
                report[split][strategy]["by_dataset"][dataset] = {
                    "thresholds": {mode: thresholds[strategy][mode]["threshold"] for mode in MODE_OBJECTIVES},
                    "metrics": {
                        mode: _metrics(subset, float(thresholds[strategy][mode]["threshold"]))
                        for mode in MODE_OBJECTIVES
                    },
                }
            airt_rows = _event_level_airtlab_rows(strategy_rows)
            if airt_rows:
                report[split][strategy]["airtlab_event_group_average"] = {
                    "n_groups": len(airt_rows),
                    "metrics": {
                        mode: _metrics(airt_rows, float(thresholds[strategy][mode]["threshold"]))
                        for mode in MODE_OBJECTIVES
                    },
                }
    return report


def _read_prediction_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"Prediction file is empty: {path}")
    for row in rows:
        row["label"] = int(row["label"])
        row["probability_fight"] = float(row["probability_fight"])
        if not math.isfinite(row["probability_fight"]):
            raise ValueError(f"Non-finite score found in {path}: {row.get('path')}")
        if row["label"] not in (0, 1):
            raise ValueError(f"Invalid binary label found in {path}: {row['label']}")
    return rows


def finalize_saved_run(output_dir: Path) -> Path:
    """Build the final report from completed, already-saved prediction CSVs."""
    output_dir = output_dir.resolve()
    validation_rows = _read_prediction_csv(output_dir / "val_predictions.csv")
    test_rows = _read_prediction_csv(output_dir / "test_predictions.csv")
    with (output_dir / "thresholds.json").open("r", encoding="utf-8") as handle:
        threshold_payload = json.load(handle)
    if threshold_payload.get("selection_split") != "val" or threshold_payload.get("test_not_used_for_threshold_selection") is not True:
        raise ValueError("Saved thresholds do not prove validation-only selection.")
    thresholds = threshold_payload["thresholds_by_strategy"]
    for split, rows, expected_total in (
        ("val", validation_rows, 419),
        ("test", test_rows, 621),
    ):
        for strategy in STRATEGIES:
            selected = [row for row in rows if row.get("strategy") == strategy]
            if len(selected) != expected_total:
                raise ValueError(
                    f"Saved {split}/{strategy} predictions are incomplete: "
                    f"expected {expected_total}, found {len(selected)}."
                )
            observed = _count_rows(selected)
            for (expected_split, dataset, label), expected_count in EXPECTED_COUNTS.items():
                if expected_split != split:
                    continue
                actual = observed["by_dataset"].get(dataset, {}).get(
                    "positive" if label == 1 else "negative", 0
                )
                if actual != expected_count:
                    raise ValueError(
                        f"Saved {split}/{strategy} row count mismatch for {dataset} "
                        f"label={label}: expected {expected_count}, found {actual}."
                    )

    validation_baseline = _compare_center_with_saved(validation_rows, "val")
    test_baseline = _compare_center_with_saved(test_rows, "test")
    report = _summarize(validation_rows, test_rows, thresholds)
    checkpoint_hash = _hash_file(CHECKPOINT)
    try:
        _, _, torch, _, _ = core._import_runtime_dependencies()
        try:
            checkpoint_payload = torch.load(CHECKPOINT, map_location="cpu", weights_only=True)
        except TypeError:
            checkpoint_payload = torch.load(CHECKPOINT, map_location="cpu")
    except Exception as exc:
        raise RuntimeError(f"Could not verify the Epoch 1 checkpoint while finalizing: {exc}") from exc
    checkpoint_protocol = checkpoint_payload.get("protocol", {})
    if checkpoint_protocol.get("best_epoch") != 1:
        raise ValueError("Saved predictions are not tied to the requested Epoch 1 checkpoint.")
    manifest_hash = _hash_file(MANIFEST)
    if checkpoint_protocol.get("airtlab_split", {}).get("source_manifest_sha256") != manifest_hash:
        raise ValueError("Epoch 1 checkpoint provenance does not match the frozen split manifest.")
    with FINETUNE_CONFIG.open("r", encoding="utf-8") as handle:
        window_batch_size = int(json.load(handle)["window_batch_size"])
    payload = {
        "experiment": "MC3-18 center vs three-position vs FiveCrop inference comparison",
        "run_mode": "evaluation_only",
        "checkpoint": str(CHECKPOINT),
        "checkpoint_sha256": checkpoint_hash,
        "best_epoch": 1,
        "model_protocol": four.PROTOCOL,
        "dataset_manifest": str(MANIFEST),
        "dataset_manifest_sha256": manifest_hash,
        "split_counts": {
            "val": _count_rows([row for row in validation_rows if row["strategy"] == "center"]),
            "test": _count_rows([row for row in test_rows if row["strategy"] == "center"]),
        },
        "crop_definitions": {
            "center": "single centered 112x112 crop after short-side resize to 128",
            "three_position": "three evenly spaced positions along the long axis; short-side axis centered",
            "five_crop": "top-left, top-right, bottom-left, bottom-right and center 112x112 crops",
            "spatial_aggregation": "arithmetic mean of per-crop Fight probabilities within each temporal window",
            "temporal_aggregation": "maximum window probability, unchanged from the trained 4S16F2 protocol",
            "resize_sampling": "same 4-second windows, 2-second stride, 16 frames/window for all methods",
        },
        "metrics_note": "AIRT Lab also includes event-group metrics averaging its paired camera-view probabilities; clip/view metrics are not independent-event counts.",
        "runtime": {"device": "cuda:0", "amp": True,
                    "center_crop_window_batch_size": window_batch_size,
                    "frame_cache_created_or_modified": False},
        "center_baseline_regression": {"validation": validation_baseline, "test": test_baseline},
        **report,
    }
    report_path = output_dir / "comparison_report.json"
    report_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    return report_path


def run(*, smoke_only: bool = False, output_dir: Path | None = None, max_smoke_per_dataset: int = 1) -> Path | None:
    dataset_root = PROJECT_ROOT / "GuardAId Video Classification Model Dataset"
    # The comparison reads the frozen raw-video manifest directly and never
    # creates or mutates training caches. Keep the protocol's cache path rooted
    # beside the original 4S16F2 run for compatibility with its config object.
    cache_root = BASE_RUN_DIR.parent / "_supplemental_cache"
    _setup_four_second_protocol(dataset_root, cache_root)

    cv2, np, torch, nn, (_weights_enum, model_builder) = core._import_runtime_dependencies()
    with FINETUNE_CONFIG.open("r", encoding="utf-8") as handle:
        finetune_config = json.load(handle)
    window_batch_size = int(finetune_config.get("window_batch_size", 0))
    if window_batch_size < 1:
        raise ValueError(f"Invalid window_batch_size in the Epoch 1 fine-tuning run config: {window_batch_size}")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; stopping instead of silently running this 1,040-video comparison on CPU.")
    device = torch.device("cuda:0")
    records_by_split = load_records(MANIFEST, dataset_root)
    if smoke_only:
        records_by_split = {
            split: [
                record
                for dataset in DATASET_NAMES
                for record in [item for item in selected if item.dataset == dataset][:max_smoke_per_dataset]
            ]
            for split, selected in records_by_split.items()
        }
        # The quick pass is for inference-path verification, not thresholding.
    else:
        expected_totals = {"val": 419, "test": 621}
        for split, expected in expected_totals.items():
            if len(records_by_split[split]) != expected:
                raise RuntimeError(f"Unexpected {split} total: expected {expected}, found {len(records_by_split[split])}")

    if smoke_only:
        logger = logging.getLogger("mc3_crop_comparison_smoke")
        logger.handlers.clear()
        logger.setLevel(logging.INFO)
        logger.addHandler(logging.StreamHandler(sys.stdout))
    else:
        output_dir = output_dir or OUTPUT_ROOT / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        logger = _make_logger(output_dir)

    model, checkpoint_protocol = _load_model(torch, nn, model_builder, device)
    if checkpoint_protocol.get("best_epoch") != 1:
        raise RuntimeError(
            f"Requested post-finetune Epoch 1 checkpoint, but embedded best_epoch is "
            f"{checkpoint_protocol.get('best_epoch')!r}."
        )
    expected_base_manifest_hash = checkpoint_protocol.get("airtlab_split", {}).get("source_manifest_sha256")
    actual_base_manifest_hash = _hash_file(MANIFEST)
    if expected_base_manifest_hash != actual_base_manifest_hash:
        raise RuntimeError(
            "The checkpoint's recorded AIRT Lab source split manifest does not match the "
            "evaluation manifest; refusing to evaluate a potentially different split."
        )
    checkpoint_hash = _hash_file(CHECKPOINT)
    logger.info("Checkpoint: %s | SHA-256=%s | best_epoch=%s", CHECKPOINT, checkpoint_hash,
                checkpoint_protocol.get("best_epoch"))
    logger.info(
        "Device=%s | AMP=on | center-crop window_batch_size=%d | cache writes=disabled | evaluation split manifest=%s | "
        "AIRT Lab split SHA-256 verified against checkpoint provenance",
        device, window_batch_size, MANIFEST,
    )
    for split, selected in records_by_split.items():
        logger.info("Preflight %s: %s", split, json.dumps(_count_rows([{
            "dataset": r.dataset, "label": r.label
        } for r in selected]), ensure_ascii=False))

    validation_rows = _run_split("val", records_by_split["val"], cv2, np, torch, model, device, logger,
                                  amp=True, window_batch_size=window_batch_size)
    baseline_check = _compare_center_with_saved(
        validation_rows, "val", require_complete=not smoke_only
    )
    logger.info("Center baseline regression passed for validation: %s",
                json.dumps(baseline_check, ensure_ascii=False))

    if smoke_only:
        test_smoke_rows = _run_split("test", records_by_split["test"], cv2, np, torch, model, device, logger,
                                     amp=True, window_batch_size=window_batch_size)
        test_baseline_check = _compare_center_with_saved(test_smoke_rows, "test", require_complete=False)
        logger.info("Center baseline regression passed for test smoke sample: %s",
                    json.dumps(test_baseline_check, ensure_ascii=False))
        logger.info("Smoke inference passed: all three crop methods produced finite scores for %d videos per split.",
                    len(records_by_split["val"]))
        return None

    _write_csv(output_dir / "val_predictions.csv", validation_rows)

    # Thresholds are selected and persisted from validation only, before any
    # test metrics are calculated or written.
    thresholds: dict[str, dict[str, dict[str, float]]] = {}
    for strategy in STRATEGIES:
        val_rows = [row for row in validation_rows if row["strategy"] == strategy]
        thresholds[strategy] = core.choose_thresholds(
            [int(row["label"]) for row in val_rows],
            [float(row["probability_fight"]) for row in val_rows],
        )
    threshold_payload = {
        "selection_split": "val",
        "selection_population": "combined RLVD + RWF-2000 + AIRT Lab eligible video views",
        "selection_objectives": MODE_OBJECTIVES,
        "thresholds_by_strategy": thresholds,
        "test_not_used_for_threshold_selection": True,
    }
    (output_dir / "thresholds.json").write_text(
        json.dumps(threshold_payload, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    logger.info("Validation thresholds locked; test now evaluated without retuning: %s",
                json.dumps(thresholds, ensure_ascii=False, allow_nan=False))

    test_rows = _run_split("test", records_by_split["test"], cv2, np, torch, model, device, logger,
                           amp=True, window_batch_size=window_batch_size)
    test_baseline_check = _compare_center_with_saved(test_rows, "test")
    logger.info("Center baseline regression passed for test: %s",
                json.dumps(test_baseline_check, ensure_ascii=False))
    _write_csv(output_dir / "test_predictions.csv", test_rows)

    report = _summarize(validation_rows, test_rows, thresholds)
    payload = {
        "experiment": "MC3-18 center vs three-position vs FiveCrop inference comparison",
        "run_mode": "evaluation_only",
        "checkpoint": str(CHECKPOINT),
        "checkpoint_sha256": checkpoint_hash,
        "best_epoch": checkpoint_protocol.get("best_epoch"),
        "model_protocol": four.PROTOCOL,
        "dataset_manifest": str(MANIFEST),
        "split_counts": {
            split: _count_rows([{"dataset": r.dataset, "label": r.label} for r in selected])
            for split, selected in records_by_split.items()
        },
        "crop_definitions": {
            "center": "single centered 112x112 crop after short-side resize to 128",
            "three_position": "three evenly spaced positions along the long axis; short-side axis centered",
            "five_crop": "top-left, top-right, bottom-left, bottom-right and center 112x112 crops",
            "spatial_aggregation": "arithmetic mean of per-crop Fight probabilities within each temporal window",
            "temporal_aggregation": "maximum window probability, unchanged from the trained 4S16F2 protocol",
            "resize_sampling": "same 4-second windows, 2-second stride, 16 frames/window for all methods",
        },
        "metrics_note": "AIRT Lab also includes event-group metrics averaging its paired camera-view probabilities; clip/view metrics are not independent-event counts.",
        "runtime": {"device": str(device), "amp": True,
                    "center_crop_window_batch_size": window_batch_size,
                    "frame_cache_created_or_modified": False},
        "center_baseline_regression": {"validation": baseline_check, "test": test_baseline_check},
        **report,
    }
    (output_dir / "comparison_report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    logger.info("Comparison complete. Results saved to %s", output_dir)
    for strategy in STRATEGIES:
        for mode in MODE_OBJECTIVES:
            val = report["validation"][strategy]["overall"][mode]
            test = report["test"][strategy]["overall"][mode]
            logger.info(
                "%s/%s | threshold=%.6f | val AUC=%.4f F1=%.4f FPR=%.4f | "
                "test AUC=%.4f precision=%.4f recall=%.4f F1=%.4f FPR=%.4f",
                strategy, mode, thresholds[strategy][mode]["threshold"], val["roc_auc"], val["f1"],
                val["false_positive_rate"], test["roc_auc"], test["precision"], test["recall"],
                test["f1"], test["false_positive_rate"],
            )
    return output_dir


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-only", action="store_true", help="Run a tiny real-video inference pass; do not evaluate full splits.")
    parser.add_argument("--output-dir", type=Path, help="Optional new output folder; must not already exist for full runs.")
    parser.add_argument("--finalize-existing", type=Path,
                        help="Build a report from a completed run's saved predictions without rerunning inference.")
    parser.add_argument("--max-smoke-per-dataset", type=int, default=1)
    args = parser.parse_args(argv)
    if args.max_smoke_per_dataset < 1:
        parser.error("--max-smoke-per-dataset must be positive")
    if args.finalize_existing:
        report_path = finalize_saved_run(args.finalize_existing)
        print(f"Comparison report finalized: {report_path}")
        return 0
    run(smoke_only=args.smoke_only, output_dir=args.output_dir,
        max_smoke_per_dataset=args.max_smoke_per_dataset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
