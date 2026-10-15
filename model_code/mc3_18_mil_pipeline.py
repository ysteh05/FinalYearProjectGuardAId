"""Train, calibrate, and test the GuardAId MC3-18 violence classifier.

The default run uses 1-second, 4-frame instances at a 0.5-second stride.
Original videos are MIL bags, so every video contributes one bag-level loss.

Run from PyCharm with no arguments, or from PowerShell:
    py -3.10 model_code/mc3_18_mil_pipeline.py

Resume the latest complete epoch:
    py -3.10 model_code/mc3_18_mil_pipeline.py --resume model_runs/mc3_1s4f/<run>/last.pt
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
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


WINDOW_SECONDS = 1.0
STRIDE_SECONDS = 0.5
NUM_FRAMES = 4
SHORT_SIDE = 128
CROP_SIZE = 112
CLASS_TO_LABEL = {"NonFight": 0, "Fight": 1}
MEAN = (0.43216, 0.394666, 0.37645)
STD = (0.22803, 0.22145, 0.216989)
CACHE_PROTOCOL = "mc3-1s-4f-128short-112crop-v1"
CACHE_WORKERS = 4
CACHE_ENGINE_VERSION = "sequential-decode-process-pool-v2"
CHECKPOINT_VERSION = 2
_ACTIVE_RUN_DIR: Path | None = None


@dataclass(frozen=True)
class Window:
    start: float
    end: float
    frame_indices: tuple[int, ...]


@dataclass
class VideoRecord:
    dataset: str
    split: str
    class_name: str
    label: int
    path: Path
    fps: float
    frame_count: int
    duration: float
    size_bytes: int
    mtime_ns: int
    cache_path: Path | None = None

    @property
    def identity(self) -> str:
        return f"{self.dataset}|{self.split}|{self.class_name}|{self.path.as_posix()}"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_dataset_root() -> Path:
    return project_root() / "GuardAId Video Classification Model Dataset"


def default_cache_root() -> Path:
    return project_root() / "model_runs" / "_frame_cache" / CACHE_PROTOCOL


def _preprocessing_protocol() -> dict[str, Any]:
    return {
        "window_seconds": WINDOW_SECONDS,
        "stride_seconds": STRIDE_SECONDS,
        "frames_per_window": NUM_FRAMES,
        "frame_sample_positions": [0.125, 0.375, 0.625, 0.875],
        "short_side": SHORT_SIDE,
        "crop_size": CROP_SIZE,
        "color_space": "RGB",
        "normalization_mean": list(MEAN),
        "normalization_std": list(STD),
    }


def discover_dataset_roots(dataset_root: Path) -> list[tuple[str, Path]]:
    """Return the canonical datasets; the archived duplicate is ignored."""
    candidates = [
        ("RWF-2000", dataset_root / "RWF-2000"),
        ("Real Life Violence Dataset", dataset_root / "archive" / "Real Life Violence Dataset"),
        ("bus-violence", dataset_root / "bus-violence"),
        (
            "seymanurakti-fight-detection-surv-dataset-ff83b7c",
            dataset_root / "seymanurakti-fight-detection-surv-dataset-ff83b7c",
        ),
        ("ShanghaiTech", dataset_root / "ShanghaiTech"),
    ]
    missing = [f"{name}: {path}" for name, path in candidates if not path.is_dir()]
    if missing:
        raise FileNotFoundError("Missing canonical dataset directories:\n  " + "\n  ".join(missing))
    return candidates


def make_windows(
    duration: float,
    fps: float,
    frame_count: int,
    *,
    include_training_tail: bool = False,
) -> list[Window]:
    """Build fixed-duration windows; training may append one end-aligned tail."""
    if duration < WINDOW_SECONDS or fps <= 0 or frame_count <= 0:
        return []
    intervals: list[tuple[float, float]] = []
    start = 0.0
    while start + WINDOW_SECONDS <= duration:
        intervals.append((round(start, 9), round(start + WINDOW_SECONDS, 9)))
        start += STRIDE_SECONDS
    if include_training_tail:
        tail_start = max(0.0, duration - WINDOW_SECONDS)
        if not intervals or tail_start > intervals[-1][0] + 1e-6:
            intervals.append((tail_start, duration))
    windows: list[Window] = []
    for start, end in intervals:
        indices = tuple(
            max(0, min(frame_count - 1, int(math.floor((start + (i + 0.5) * WINDOW_SECONDS / NUM_FRAMES) * fps))))
            for i in range(NUM_FRAMES)
        )
        windows.append(Window(start, end, indices))
    return windows


def stable_seed(seed: int, epoch: int, identity: str, view: int = 0) -> int:
    digest = hashlib.sha256(f"{seed}|{epoch}|{identity}|{view}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (2**32)


def consume_pause_request(pause_file: str | None) -> bool:
    """Consume a UI pause marker only at a safe completed-epoch boundary."""
    if not pause_file:
        return False
    marker = Path(pause_file)
    if not marker.is_file():
        return False
    marker.unlink()
    return True


def _metric_counts(labels: Sequence[int], predictions: Sequence[int]) -> dict[str, int]:
    tp = sum(y == 1 and p == 1 for y, p in zip(labels, predictions))
    tn = sum(y == 0 and p == 0 for y, p in zip(labels, predictions))
    fp = sum(y == 0 and p == 1 for y, p in zip(labels, predictions))
    fn = sum(y == 1 and p == 0 for y, p in zip(labels, predictions))
    return {"tp": tp, "tn": tn, "fp": fp, "fn": fn}


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def f_beta(precision: float, recall: float, beta: float) -> float:
    beta2 = beta * beta
    denominator = beta2 * precision + recall
    return (1.0 + beta2) * precision * recall / denominator if denominator else 0.0


def binary_metrics(labels: Sequence[int], probabilities: Sequence[float], threshold: float) -> dict[str, Any]:
    if len(labels) != len(probabilities) or not labels:
        raise ValueError("Metrics require equally sized, non-empty label and probability arrays.")
    predictions = [int(float(p) >= threshold) for p in probabilities]
    counts = _metric_counts(labels, predictions)
    tp, tn, fp, fn = counts["tp"], counts["tn"], counts["fp"], counts["fn"]
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    specificity = _safe_div(tn, tn + fp)
    auc = roc_auc(labels, probabilities)
    average_precision = pr_auc_average_precision(labels, probabilities)
    return {
        **counts,
        "n": len(labels),
        "accuracy": _safe_div(tp + tn, len(labels)),
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "f0_5": f_beta(precision, recall, 0.5),
        "f1": f_beta(precision, recall, 1.0),
        "f2": f_beta(precision, recall, 2.0),
        "balanced_accuracy": (recall + specificity) / 2.0,
        "roc_auc": auc,
        "average_precision": average_precision,
        "threshold": float(threshold),
    }


def roc_auc(labels: Sequence[int], probabilities: Sequence[float]) -> float | None:
    positives = [float(p) for y, p in zip(labels, probabilities) if y == 1]
    negatives = [float(p) for y, p in zip(labels, probabilities) if y == 0]
    if not positives or not negatives:
        return None
    wins = 0.0
    for pos in positives:
        for neg in negatives:
            wins += 1.0 if pos > neg else 0.5 if pos == neg else 0.0
    return wins / (len(positives) * len(negatives))


def pr_auc_average_precision(labels: Sequence[int], probabilities: Sequence[float]) -> float | None:
    positive_count = sum(int(y == 1) for y in labels)
    if not positive_count:
        return None
    ordered = sorted(zip(probabilities, labels), key=lambda item: item[0], reverse=True)
    tp = fp = 0
    previous_recall = 0.0
    area = 0.0
    index = 0
    while index < len(ordered):
        score = ordered[index][0]
        while index < len(ordered) and ordered[index][0] == score:
            if ordered[index][1] == 1:
                tp += 1
            else:
                fp += 1
            index += 1
        recall = tp / positive_count
        precision = tp / (tp + fp) if tp + fp else 0.0
        area += (recall - previous_recall) * precision
        previous_recall = recall
    return area


def threshold_candidates(probabilities: Sequence[float]) -> list[float]:
    unique = sorted({0.0, 1.0, *(min(1.0, max(0.0, float(p))) for p in probabilities)})
    return unique


def choose_thresholds(labels: Sequence[int], probabilities: Sequence[float]) -> dict[str, dict[str, float]]:
    """Select deterministic, ordered validation thresholds with the planned F-beta objectives."""
    if len(labels) != len(probabilities) or not labels:
        raise ValueError("Threshold calibration requires non-empty validation predictions.")
    if len(set(labels)) < 2:
        raise ValueError("Threshold calibration requires both Fight and NonFight validation videos.")
    candidates = threshold_candidates(probabilities)

    def score_at(threshold: float, beta: float) -> float:
        predicted = [int(float(p) >= threshold) for p in probabilities]
        counts = _metric_counts(labels, predicted)
        precision = _safe_div(counts["tp"], counts["tp"] + counts["fp"])
        recall = _safe_div(counts["tp"], counts["tp"] + counts["fn"])
        return f_beta(precision, recall, beta)

    moderate_scores = [(score_at(t, 1.0), t) for t in candidates]
    moderate_best = max(score for score, _ in moderate_scores)
    moderate = max(t for score, t in moderate_scores if math.isclose(score, moderate_best, rel_tol=0, abs_tol=1e-12))

    sensitive_candidates = [t for t in candidates if t <= moderate + 1e-12]
    sensitive_scores = [(score_at(t, 2.0), t) for t in sensitive_candidates]
    sensitive_best = max(score for score, _ in sensitive_scores)
    sensitive = min(t for score, t in sensitive_scores if math.isclose(score, sensitive_best, rel_tol=0, abs_tol=1e-12))

    certain_candidates = [t for t in candidates if t >= moderate - 1e-12]
    certain_scores = [(score_at(t, 0.5), t) for t in certain_candidates]
    certain_best = max(score for score, _ in certain_scores)
    certain = max(t for score, t in certain_scores if math.isclose(score, certain_best, rel_tol=0, abs_tol=1e-12))

    return {
        "sensitive": {"threshold": sensitive, "objective": "F2", "validation_score": score_at(sensitive, 2.0)},
        "moderate": {"threshold": moderate, "objective": "F1", "validation_score": score_at(moderate, 1.0)},
        "certain": {"threshold": certain, "objective": "F0.5", "validation_score": score_at(certain, 0.5)},
    }


def _import_runtime_dependencies() -> tuple[Any, Any, Any, Any, Any]:
    missing: list[str] = []
    import_error: Exception | None = None
    try:
        import cv2 as cv2_module
    except ImportError:
        missing.append("opencv-python")
        cv2_module = None
    try:
        import numpy as np_module
    except ImportError:
        missing.append("numpy")
        np_module = None
    try:
        import torch as torch_module
        import torch.nn as nn_module
        from torchvision.models.video import MC3_18_Weights, mc3_18
    except Exception as exc:
        import_error = exc
        missing.extend(["PyTorch", "TorchVision"])
        torch_module = nn_module = MC3_18_Weights = mc3_18 = None
    if missing:
        unique = ", ".join(dict.fromkeys(missing))
        detail = f" Import detail: {import_error}" if import_error is not None else ""
        raise RuntimeError(
            f"Training dependencies are missing or incompatible ({unique}). Install a matching "
            "PyTorch/TorchVision pair for this Python and CUDA setup, then run "
            f"`py -3.10 -m pip install -r model_code/requirements-mc3.txt`.{detail}"
        ) from import_error
    return cv2_module, np_module, torch_module, nn_module, (MC3_18_Weights, mc3_18)


def _log_setup(run_dir: Path, resume: bool = False) -> logging.Logger:
    run_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("mc3_18_mil")
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.flush()
        finally:
            handler.close()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(run_dir / "run.log", mode="a" if resume else "w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    return logger


def _dataset_fingerprint(records: Sequence[VideoRecord]) -> str:
    rows = [
        f"{r.dataset}|{r.split}|{r.label}|{r.path.resolve()}|{r.size_bytes}|{r.mtime_ns}|{r.duration:.6f}"
        for r in records
    ]
    return hashlib.sha256("\n".join(sorted(rows)).encode("utf-8")).hexdigest()


def _scan_datasets(dataset_root: Path, cv2: Any, logger: logging.Logger) -> tuple[list[VideoRecord], list[dict[str, Any]]]:
    roots = discover_dataset_roots(dataset_root)
    records: list[VideoRecord] = []
    manifest_rows: list[dict[str, Any]] = []
    failures: list[str] = []
    total_files = 0
    for dataset_name, root in roots:
        for split in ("train", "val", "test"):
            for class_name, label in CLASS_TO_LABEL.items():
                class_dir = root / split / class_name
                if not class_dir.is_dir():
                    raise FileNotFoundError(f"Expected directory is missing: {class_dir}")
                files = sorted(
                    (p for p in class_dir.iterdir() if p.is_file() and p.suffix.lower() in {".avi", ".mp4"}),
                    key=lambda p: p.name.casefold(),
                )
                for path in files:
                    total_files += 1
                    cap = cv2.VideoCapture(str(path))
                    if not cap.isOpened():
                        failures.append(f"{path}: OpenCV could not open the video")
                        cap.release()
                        continue
                    fps = float(cap.get(cv2.CAP_PROP_FPS))
                    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    cap.release()
                    if not math.isfinite(fps) or fps <= 0 or frame_count <= 0:
                        failures.append(f"{path}: invalid metadata (fps={fps}, frames={frame_count})")
                        continue
                    stat = path.stat()
                    duration = frame_count / fps
                    record = VideoRecord(
                        dataset=dataset_name,
                        split=split,
                        class_name=class_name,
                        label=label,
                        path=path.resolve(),
                        fps=fps,
                        frame_count=frame_count,
                        duration=duration,
                        size_bytes=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                    )
                    eligible = duration >= WINDOW_SECONDS
                    manifest_rows.append(
                        {
                            "dataset": dataset_name,
                            "split": split,
                            "class_name": class_name,
                            "label": label,
                            "path": str(path.resolve()),
                            "extension": path.suffix.lower(),
                            "fps": f"{fps:.6f}",
                            "frame_count": frame_count,
                            "duration_seconds": f"{duration:.6f}",
                            "eligible": int(eligible),
                            "exclusion_reason": "shorter_than_1_second" if not eligible else "",
                            "size_bytes": stat.st_size,
                        }
                    )
                    if eligible:
                        records.append(record)
        logger.info("Scanned dataset %s", dataset_name)
    if failures:
        details = "\n".join(f"  - {item}" for item in failures)
        raise RuntimeError(f"Video preflight found {len(failures)} unreadable/invalid files; fix them before training:\n{details}")
    if not records:
        raise RuntimeError("No videos of at least 1.0 second were found.")
    logger.info("Scanned %d AVI/MP4 files; %d are eligible (>=1 second), %d are excluded as short.", total_files, len(records), total_files - len(records))
    return records, manifest_rows


def _write_csv(path: Path, rows: Sequence[dict[str, Any]], fieldnames: Sequence[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows and fieldnames is None:
        path.write_text("", encoding="utf-8")
        return
    names = list(fieldnames or rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in names})


def _print_data_summary(
    records: Sequence[VideoRecord],
    manifest_rows: Sequence[dict[str, Any]],
    logger: logging.Logger,
    dataset_names: Sequence[str] | None = None,
) -> None:
    excluded = [row for row in manifest_rows if not int(row["eligible"])]
    logger.info("Eligible video bags by dataset / split / class:")
    counts: dict[tuple[str, str, str], int] = {}
    for record in records:
        key = (record.dataset, record.split, record.class_name)
        counts[key] = counts.get(key, 0) + 1
    all_datasets = sorted(set(dataset_names or ()) | {record.dataset for record in records})
    for dataset in all_datasets:
        logger.info("  %s", dataset)
        for split in ("train", "val", "test"):
            fight = counts.get((dataset, split, "Fight"), 0)
            nonfight = counts.get((dataset, split, "NonFight"), 0)
            logger.info("    %-5s Fight=%4d NonFight=%4d Total=%4d", split, fight, nonfight, fight + nonfight)
    logger.info("Excluded (<1s), by dataset / split / class:")
    short_counts: dict[tuple[str, str, str], int] = {}
    for row in excluded:
        key = (str(row["dataset"]), str(row["split"]), str(row["class_name"]))
        short_counts[key] = short_counts.get(key, 0) + 1
    for dataset in all_datasets:
        values = [
            f"{split}: Fight={short_counts.get((dataset, split, 'Fight'), 0)}, NonFight={short_counts.get((dataset, split, 'NonFight'), 0)}"
            for split in ("train", "val", "test")
        ]
        logger.info("  %s | %s", dataset, " | ".join(values))


def _windows_for_record(record: VideoRecord, *, training: bool) -> list[Window]:
    return make_windows(
        record.duration,
        record.fps,
        record.frame_count,
        include_training_tail=training,
    )


def _cache_key(record: VideoRecord) -> str:
    key = f"{CACHE_PROTOCOL}|{record.path}|{record.size_bytes}|{record.mtime_ns}|{record.fps:.6f}|{record.frame_count}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _resize_short_side(frame: Any, cv2: Any) -> Any:
    height, width = frame.shape[:2]
    if height <= width:
        out_height = SHORT_SIDE
        out_width = int(round(width * SHORT_SIDE / height))
    else:
        out_width = SHORT_SIDE
        out_height = int(round(height * SHORT_SIDE / width))
    return cv2.resize(frame, (out_width, out_height), interpolation=cv2.INTER_LINEAR)


def _decode_selected_frames(record: VideoRecord, frame_indices: Sequence[int], cv2: Any, np: Any) -> Any:
    capture = cv2.VideoCapture(str(record.path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not reopen video for frame cache: {record.path}")
    requested = sorted({int(frame_index) for frame_index in frame_indices})
    if not requested:
        capture.release()
        raise RuntimeError(f"No frames requested for frame cache: {record.path}")
    frames: list[Any] = []
    next_requested = 0
    try:
        # OpenCV seeking can fail near the end of an otherwise readable MP4.
        # Decode forward once and retain only the requested indexed frames.
        for frame_index in range(requested[-1] + 1):
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(
                    f"Could not sequentially decode frame {frame_index} from {record.path}; "
                    f"next requested frame is {requested[next_requested]}"
                )
            if frame_index == requested[next_requested]:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frames.append(_resize_short_side(rgb, cv2))
                next_requested += 1
                if next_requested == len(requested):
                    break
    finally:
        capture.release()
    return np.stack(frames).astype(np.uint8)


def _init_cache_worker() -> None:
    import cv2

    cv2.setNumThreads(1)


def _cache_video_worker(record: VideoRecord, frame_indices: tuple[int, ...], cache_path_text: str) -> int:
    import cv2
    import numpy as np

    cache_path = Path(cache_path_text)
    frames = _decode_selected_frames(record, frame_indices, cv2, np)
    temporary = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, frame_indices=np.asarray(frame_indices, dtype=np.int32), frames=frames)
        os.replace(temporary, cache_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return os.getpid()


def _prepare_cache(
    records: Sequence[VideoRecord],
    cache_dir: Path,
    cv2: Any,
    np: Any,
    logger: logging.Logger,
    *,
    rebuild: bool,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    total = len(records)
    tasks: list[tuple[VideoRecord, tuple[int, ...], str]] = []
    reused = 0
    for record in records:
        frame_indices = sorted(
            {
                frame_idx
                for window in _windows_for_record(record, training=record.split == "train")
                for frame_idx in window.frame_indices
            }
        )
        cache_path = cache_dir / f"{_cache_key(record)}.npz"
        record.cache_path = cache_path
        if rebuild or not cache_path.is_file():
            tasks.append((record, tuple(frame_indices), str(cache_path)))
        else:
            reused += 1

    worker_count = min(CACHE_WORKERS, len(tasks))
    logger.info("Frame cache engine: %s | script=%s", CACHE_ENGINE_VERSION, Path(__file__).resolve())
    logger.info(
        "Frame cache: total=%d, reused=%d, to_build=%d, worker_processes=%d",
        total, reused, len(tasks), worker_count,
    )
    if not tasks:
        logger.info("Frame cache ready: all %d video entries reused.", reused)
        return

    started = time.monotonic()
    last_report = started
    created = 0
    completed = 0
    failures: list[tuple[Path, str]] = []
    worker_pids: set[int] = set()
    with ProcessPoolExecutor(max_workers=worker_count, initializer=_init_cache_worker) as executor:
        task_iter = iter(tasks)
        pending: dict[Any, VideoRecord] = {}

        def submit_next() -> bool:
            try:
                record, frame_indices, cache_path = next(task_iter)
            except StopIteration:
                return False
            pending[executor.submit(_cache_video_worker, record, frame_indices, cache_path)] = record
            return True

        for _ in range(min(len(tasks), worker_count * 2)):
            submit_next()
        while pending:
            done, _ = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            for future in done:
                record = pending.pop(future)
                try:
                    worker_pids.add(future.result())
                    created += 1
                except Exception as exc:
                    failures.append((record.path, str(exc)))
                    logger.error("Frame cache failed for %s: %s", record.path, exc)
                completed += 1
                submit_next()

            now = time.monotonic()
            if completed == len(tasks) or now - last_report >= 1.0:
                done_total = reused + completed
                logger.info(
                    "Frame cache progress: %d/%d (%.1f%%), reused=%d, created=%d, failed=%d, "
                    "worker_processes=%d, worker_pids_seen=%d, elapsed=%.1fs",
                    done_total, total, (100.0 * done_total / total) if total else 100.0,
                    reused, created, len(failures), worker_count, len(worker_pids), now - started,
                )
                last_report = now

    cache_bytes = sum(path.stat().st_size for path in cache_dir.glob("*.npz") if path.is_file())
    logger.info(
        "Frame cache ready: reused=%d, created=%d, failed=%d, worker_processes=%d, "
        "worker_pids_seen=%d, cache_size=%.2f GiB, elapsed=%.1fs",
        reused, created, len(failures), worker_count, len(worker_pids),
        cache_bytes / (1024**3), time.monotonic() - started,
    )
    if failures:
        details = "\n".join(f"  {path}: {message}" for path, message in failures)
        raise RuntimeError(f"Frame cache failed for {len(failures)} video(s):\n{details}")


def _load_cache(record: VideoRecord, np: Any) -> tuple[Any, dict[int, int]]:
    if record.cache_path is None or not record.cache_path.is_file():
        raise FileNotFoundError(f"Frame cache is missing for {record.path}")
    with np.load(record.cache_path, allow_pickle=False) as payload:
        indices = payload["frame_indices"].astype(np.int64)
        frames = payload["frames"].copy()
    return frames, {int(frame_index): index for index, frame_index in enumerate(indices.tolist())}


def _augment_params(rng: Any) -> dict[str, Any]:
    return {
        "flip": bool(rng.random() < 0.5),
        "brightness": float(rng.uniform(-0.035, 0.035)),
        "contrast": float(rng.uniform(0.88, 1.12)),
        "saturation": float(rng.uniform(0.90, 1.10)) if rng.random() < 0.5 else 1.0,
        "blur": bool(rng.random() < 0.08),
    }


def _record_bag_tensor(
    record: VideoRecord,
    *,
    training: bool,
    epoch: int,
    seed: int,
    np: Any,
    cv2: Any,
    torch: Any,
) -> Any:
    frames, index_map = _load_cache(record, np)
    windows = _windows_for_record(record, training=training)
    if not windows:
        raise RuntimeError(f"No complete windows for eligible video {record.path}")
    rng = np.random.default_rng(stable_seed(seed, epoch, record.identity))
    height, width = frames.shape[1:3]
    if training:
        top = int(rng.integers(0, height - CROP_SIZE + 1))
        left = int(rng.integers(0, width - CROP_SIZE + 1))
        params = _augment_params(rng)
    else:
        top = (height - CROP_SIZE) // 2
        left = (width - CROP_SIZE) // 2
        params = {"flip": False, "brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "blur": False}
    clips: list[Any] = []
    mean = np.asarray(MEAN, dtype=np.float32).reshape(1, 1, 3)
    std = np.asarray(STD, dtype=np.float32).reshape(1, 1, 3)
    for window in windows:
        processed: list[Any] = []
        for frame_index in window.frame_indices:
            frame = frames[index_map[frame_index], top : top + CROP_SIZE, left : left + CROP_SIZE]
            if params["flip"]:
                frame = frame[:, ::-1]
            image = frame.astype(np.float32) / 255.0
            saturation = params["saturation"]
            if saturation != 1.0:
                gray = image[..., 0:1] * 0.299 + image[..., 1:2] * 0.587 + image[..., 2:3] * 0.114
                image = gray + saturation * (image - gray)
            image = (image - 0.5) * params["contrast"] + 0.5 + params["brightness"]
            image = np.clip(image, 0.0, 1.0)
            if params["blur"]:
                image = cv2.GaussianBlur(image, (3, 3), sigmaX=0.45)
            image = (image - mean) / std
            processed.append(np.ascontiguousarray(image, dtype=np.float32))
        # T,H,W,C -> C,T,H,W
        clip = np.stack(processed, axis=0).transpose(3, 0, 1, 2)
        clips.append(torch.from_numpy(np.ascontiguousarray(clip)))
    return torch.stack(clips, dim=0), windows


def _build_model(torch: Any, nn: Any, weights_enum: Any, model_builder: Any, device: Any, *, pretrained: bool) -> Any:
    if pretrained:
        try:
            model = model_builder(weights=weights_enum.KINETICS400_V1)
        except Exception as exc:
            raise RuntimeError(
                "Could not load the official Kinetics-400 MC3-18 weights. Check the network or "
                "TorchVision weight cache; this run must not silently fall back to random initialization."
            ) from exc
    else:
        model = model_builder(weights=None)
    model.fc = nn.Linear(model.fc.in_features, 2)
    model = model.to(device)
    return model


def _freeze_batch_norm_running_stats(model: Any, nn: Any) -> None:
    """Keep pretrained BN running statistics stable for one-bag microbatches."""
    batch_norm_type = nn.modules.batchnorm._BatchNorm
    for module in model.modules():
        if isinstance(module, batch_norm_type):
            module.eval()


def _set_seed(seed: int, torch: Any, np: Any) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def _binary_scores(logits: Any) -> Any:
    return logits[:, 1] - logits[:, 0]


def _amp_context(torch: Any, device: Any, enabled: bool):
    return torch.autocast(device_type=device.type, dtype=torch.float16, enabled=enabled)


def _make_scaler(torch: Any, enabled: bool) -> Any:
    try:
        return torch.amp.GradScaler("cuda", enabled=enabled)
    except (AttributeError, TypeError):
        return torch.cuda.amp.GradScaler(enabled=enabled)


def _predict_bag(
    model: Any,
    clips: Any,
    torch: Any,
    device: Any,
    *,
    amp: bool,
    window_batch_size: int,
) -> tuple[float, list[float]]:
    model.eval()
    scores: list[float] = []
    with torch.inference_mode():
        for start in range(0, clips.shape[0], window_batch_size):
            batch = clips[start : start + window_batch_size].to(device, non_blocking=True)
            with _amp_context(torch, device, amp):
                logits = model(batch)
            probs = torch.softmax(logits.float(), dim=1)[:, 1]
            scores.extend(float(value) for value in probs.cpu().tolist())
    return max(scores), scores


def _choose_hard_instance(
    model: Any,
    clips: Any,
    torch: Any,
    device: Any,
    *,
    amp: bool,
    window_batch_size: int,
) -> tuple[int, float]:
    """Find the max-MIL instance without retaining all window activations."""
    best_index = 0
    best_score = -float("inf")
    with torch.no_grad():
        for start in range(0, clips.shape[0], window_batch_size):
            batch = clips[start : start + window_batch_size].to(device, non_blocking=True)
            with _amp_context(torch, device, amp):
                logits = model(batch)
            scores = _binary_scores(logits.float())
            score, offset = scores.max(dim=0)
            if float(score.item()) > best_score:
                best_score = float(score.item())
                best_index = start + int(offset.item())
    return best_index, best_score


def _state_rng(torch: Any, np: Any) -> dict[str, Any]:
    return {
        "python": random.getstate(),
        "numpy": np.random.get_state(),
        "torch": torch.get_rng_state(),
        "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
    }


def _restore_rng(state: dict[str, Any], torch: Any, np: Any) -> None:
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch"])
    if torch.cuda.is_available() and state.get("cuda"):
        torch.cuda.set_rng_state_all(state["cuda"])


def _atomic_torch_save(payload: dict[str, Any], path: Path, torch: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(payload, temporary)
    os.replace(temporary, path)


def _save_deployment_checkpoint(model: Any, path: Path, torch: Any, *, best_epoch: int, best_auc: float) -> None:
    payload = {
        "deployment_checkpoint_version": 1,
        "model_name": "mc3_18",
        "state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
        "class_to_idx": CLASS_TO_LABEL,
        "protocol": {
            **_preprocessing_protocol(),
            "best_epoch": best_epoch,
            "best_validation_roc_auc": best_auc,
        },
    }
    _atomic_torch_save(payload, path, torch)


def _torch_load_training_checkpoint(path: Path, torch: Any) -> dict[str, Any]:
    try:
        # Keep saved RNG tensors on CPU; torch.set_rng_state requires CPU byte tensors.
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def _validate_checkpoint_config(payload: dict[str, Any], fingerprint: str, config: dict[str, Any]) -> None:
    if payload.get("checkpoint_version") != CHECKPOINT_VERSION:
        raise RuntimeError("Unsupported training checkpoint version.")
    if payload.get("dataset_fingerprint") != fingerprint:
        raise RuntimeError("Dataset files changed since this checkpoint; refusing an unsafe resume.")
    saved = payload.get("config", {})
    keys = (
        "seed", "epochs", "backbone_lr", "head_lr", "weight_decay", "grad_accum",
        "window_batch_size", "patience", "min_delta", "amp", "device", "protocol", "preprocessing",
    )
    differences = [key for key in keys if saved.get(key) != config.get(key)]
    if differences:
        raise RuntimeError("Resume settings differ from checkpoint: " + ", ".join(differences))


def _evaluate_split(
    records: Sequence[VideoRecord],
    model: Any,
    *,
    split: str,
    epoch: int,
    seed: int,
    np: Any,
    cv2: Any,
    torch: Any,
    device: Any,
    amp: bool,
    window_batch_size: int,
    logger: logging.Logger,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected = [record for record in records if record.split == split]
    predictions: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    for index, record in enumerate(selected, start=1):
        clips, windows = _record_bag_tensor(
            record,
            training=False,
            epoch=epoch,
            seed=seed,
            np=np,
            cv2=cv2,
            torch=torch,
        )
        video_probability, probabilities = _predict_bag(
            model,
            clips,
            torch,
            device,
            amp=amp,
            window_batch_size=window_batch_size,
        )
        predictions.append(
            {
                "dataset": record.dataset,
                "split": record.split,
                "class_name": record.class_name,
                "label": record.label,
                "path": str(record.path),
                "duration_seconds": record.duration,
                "window_count": len(windows),
                "probability_fight": video_probability,
            }
        )
        for window, probability in zip(windows, probabilities):
            window_rows.append(
                {
                    "dataset": record.dataset,
                    "split": record.split,
                    "class_name": record.class_name,
                    "label": record.label,
                    "path": str(record.path),
                    "start_seconds": window.start,
                    "end_seconds": window.end,
                    "probability_fight": probability,
                }
            )
        if index == 1 or index % 25 == 0 or index == len(selected):
            elapsed = time.perf_counter() - started
            eta = elapsed / index * (len(selected) - index) if index else 0.0
            logger.info("%s progress %d/%d videos | %.1f%% | elapsed %.1fs | ETA %.1fs", split, index, len(selected), 100 * index / max(len(selected), 1), elapsed, eta)
    return predictions, window_rows


def _aggregate_metrics(predictions: Sequence[dict[str, Any]], threshold: float) -> dict[str, Any]:
    return binary_metrics(
        [int(row["label"]) for row in predictions],
        [float(row["probability_fight"]) for row in predictions],
        threshold,
    )


def _mode_metrics(predictions: Sequence[dict[str, Any]], thresholds: dict[str, dict[str, float]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for mode, value in thresholds.items():
        result[mode] = _aggregate_metrics(predictions, float(value["threshold"]))
    return result


def _per_dataset_metrics(predictions: Sequence[dict[str, Any]], thresholds: dict[str, dict[str, float]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for dataset in sorted({str(row["dataset"]) for row in predictions}):
        subset = [row for row in predictions if row["dataset"] == dataset]
        result[dataset] = _mode_metrics(subset, thresholds)
    return result


def _write_predictions(run_dir: Path, split: str, predictions: Sequence[dict[str, Any]], windows: Sequence[dict[str, Any]]) -> None:
    _write_csv(run_dir / f"{split}_predictions.csv", predictions)
    _write_csv(run_dir / f"{split}_window_predictions.csv", windows)


def _count_split_samples(
    dataset_roots: Sequence[tuple[str, Path]],
    records: Sequence[VideoRecord],
    manifest_rows: Sequence[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int]]:
    eligible: dict[str, int] = {}
    excluded: dict[str, int] = {}
    for dataset, _ in dataset_roots:
        for split in ("train", "val", "test"):
            for class_name in CLASS_TO_LABEL:
                key = f"{dataset}/{split}/{class_name}"
                eligible[key] = sum(
                    1 for record in records
                    if record.dataset == dataset and record.split == split and record.class_name == class_name
                )
                excluded[key] = sum(
                    1 for row in manifest_rows
                    if row["dataset"] == dataset and row["split"] == split
                    and row["class_name"] == class_name and not int(row["eligible"])
                )
    return eligible, excluded


def _save_last_checkpoint(
    *,
    model: Any,
    optimizer: Any,
    scheduler: Any,
    scaler: Any,
    torch: Any,
    np: Any,
    run_dir: Path,
    completed_epoch: int,
    best_epoch: int,
    best_auc: float,
    patience_best_auc: float,
    epochs_without_improvement: int,
    history: list[dict[str, Any]],
    dataset_fingerprint: str,
    config: dict[str, Any],
) -> None:
    payload = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "model_state": model.state_dict(),
        "optimizer_state": optimizer.state_dict(),
        "scheduler_state": scheduler.state_dict(),
        "scaler_state": scaler.state_dict(),
        "completed_epoch": completed_epoch,
        "best_epoch": best_epoch,
        "best_auc": best_auc,
        "patience_best_auc": patience_best_auc,
        "epochs_without_improvement": epochs_without_improvement,
        "history": history,
        "dataset_fingerprint": dataset_fingerprint,
        "config": config,
        "rng_state": _state_rng(torch, np),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    _atomic_torch_save(payload, run_dir / "last.pt", torch)


def _run_training(args: argparse.Namespace) -> None:
    global _ACTIVE_RUN_DIR
    cv2, np, torch, nn, model_symbols = _import_runtime_dependencies()
    weights_enum, model_builder = model_symbols
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but PyTorch cannot see a CUDA device. Choose --device cpu explicitly to train on CPU.")
    if args.device == "cpu" and not args.allow_cpu:
        raise RuntimeError("CPU training is disabled by default because this dataset/model can take a long time. Add --allow-cpu to confirm.")
    device = torch.device("cuda:0" if args.device == "cuda" else "cpu")
    amp = device.type == "cuda" and not args.no_amp
    dataset_root = Path(args.dataset_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else output_root / f"run_{timestamp}"
    _ACTIVE_RUN_DIR = run_dir
    resume_payload: dict[str, Any] | None = None
    if args.resume:
        resume_path = Path(args.resume).expanduser().resolve()
        if not resume_path.is_file():
            raise FileNotFoundError(f"Resume checkpoint not found: {resume_path}")
        run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else resume_path.parent
        if run_dir != resume_path.parent:
            raise ValueError("--run-dir must be the parent directory of --resume so run artifacts stay together.")
        _ACTIVE_RUN_DIR = run_dir
        if (run_dir / "final_report.json").is_file():
            raise RuntimeError("This run already completed validation calibration and final test; refusing to rerun its test set.")
        resume_payload = _torch_load_training_checkpoint(resume_path, torch)
    if resume_payload is None and run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(f"Run directory already contains files: {run_dir}. Choose a new --run-dir or use --resume.")
    logger = _log_setup(run_dir, resume=resume_payload is not None)
    logger.info("MC3-18 MIL pipeline | device=%s | amp=%s | protocol=%s", device, amp, CACHE_PROTOCOL)
    logger.info("Dataset root: %s", dataset_root)
    dataset_roots = discover_dataset_roots(dataset_root)
    try:
        records, manifest_rows = _scan_datasets(dataset_root, cv2, logger)
    except Exception:
        logger.exception("Dataset preflight failed.")
        raise
    fingerprint = _dataset_fingerprint(records)
    _write_csv(run_dir / "dataset_manifest.csv", manifest_rows)
    _print_data_summary(records, manifest_rows, logger, [name for name, _ in dataset_roots])
    val_labels = {record.label for record in records if record.split == "val"}
    train_labels = {record.label for record in records if record.split == "train"}
    if len(val_labels) < 2 or len(train_labels) < 2:
        raise RuntimeError("After excluding sub-second videos, both Fight and NonFight must remain in train and val.")
    config = {
        "seed": args.seed,
        "epochs": args.epochs,
        "weight_decay": args.weight_decay,
        "grad_accum": args.grad_accum,
        "window_batch_size": args.window_batch_size,
        "backbone_lr": args.backbone_lr,
        "head_lr": args.head_lr,
        "patience": args.patience,
        "min_delta": args.min_delta,
        "amp": amp,
        "device": str(device),
        "protocol": CACHE_PROTOCOL,
        "preprocessing": _preprocessing_protocol(),
    }
    if resume_payload is not None:
        _validate_checkpoint_config(resume_payload, fingerprint, config)
    cache_root = Path(args.cache_dir).expanduser().resolve() if args.cache_dir else default_cache_root()
    (run_dir / "config.json").write_text(
        json.dumps({**config, "dataset_root": str(dataset_root), "cache_dir": str(cache_root)}, indent=2),
        encoding="utf-8",
    )
    try:
        _prepare_cache(records, cache_root, cv2, np, logger, rebuild=args.rebuild_cache)
    except Exception:
        logger.exception("Video frame decoding/cache preparation failed.")
        raise
    _set_seed(args.seed, torch, np)
    model = _build_model(torch, nn, weights_enum, model_builder, device, pretrained=resume_payload is None)
    optimizer = torch.optim.AdamW(
        [
            {"params": [p for name, p in model.named_parameters() if not name.startswith("fc.")], "lr": args.backbone_lr},
            {"params": list(model.fc.parameters()), "lr": args.head_lr},
        ],
        weight_decay=args.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="max", factor=0.5, patience=2, min_lr=1e-6)
    scaler = _make_scaler(torch, amp)
    start_epoch = 1
    best_epoch = 0
    best_auc = -float("inf")
    patience_best_auc = -float("inf")
    bad_epochs = 0
    history: list[dict[str, Any]] = []
    if resume_payload is not None:
        model.load_state_dict(resume_payload["model_state"])
        optimizer.load_state_dict(resume_payload["optimizer_state"])
        scheduler.load_state_dict(resume_payload["scheduler_state"])
        scaler.load_state_dict(resume_payload.get("scaler_state", {}))
        start_epoch = int(resume_payload["completed_epoch"]) + 1
        best_epoch = int(resume_payload["best_epoch"])
        best_auc = float(resume_payload["best_auc"])
        patience_best_auc = float(resume_payload.get("patience_best_auc", best_auc))
        bad_epochs = int(resume_payload["epochs_without_improvement"])
        history = list(resume_payload.get("history", []))
        _restore_rng(resume_payload["rng_state"], torch, np)
        logger.info("Resuming after completed epoch %d; next epoch is %d. Best validation AUC %.6f at epoch %d.", start_epoch - 1, start_epoch, best_auc, best_epoch)
    train_records = [r for r in records if r.split == "train"]
    val_records = [r for r in records if r.split == "val"]
    if not train_records or not val_records:
        raise RuntimeError("Training and validation splits must both contain eligible videos.")
    criterion = nn.BCEWithLogitsLoss()
    for epoch in range(start_epoch, args.epochs + 1):
        epoch_start = time.perf_counter()
        if device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(device)
        model.train()
        _freeze_batch_norm_running_stats(model, nn)
        ordered = list(train_records)
        random.Random(args.seed + epoch).shuffle(ordered)
        optimizer.zero_grad(set_to_none=True)
        accumulation_count = 0
        total_loss = 0.0
        train_labels: list[int] = []
        train_probs: list[float] = []
        for index, record in enumerate(ordered, start=1):
            clips, _ = _record_bag_tensor(record, training=True, epoch=epoch, seed=args.seed, np=np, cv2=cv2, torch=torch)
            best_window, _ = _choose_hard_instance(model, clips, torch, device, amp=amp, window_batch_size=args.window_batch_size)
            selected = clips[best_window : best_window + 1].to(device, non_blocking=True)
            target = torch.tensor([record.label], dtype=torch.float32, device=device)
            with _amp_context(torch, device, amp):
                logits = model(selected)
                bag_logit = _binary_scores(logits).reshape(1)
                loss = criterion(bag_logit, target)
            scaler.scale(loss / args.grad_accum).backward()
            accumulation_count += 1
            total_loss += float(loss.detach().float().item())
            train_probs.append(float(torch.sigmoid(torch.tensor(bag_logit.detach().float().item())).item()))
            train_labels.append(record.label)
            if accumulation_count == args.grad_accum:
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad(set_to_none=True)
                accumulation_count = 0
            if index == 1 or index % args.log_every == 0 or index == len(ordered):
                elapsed = time.perf_counter() - epoch_start
                eta = elapsed / index * (len(ordered) - index) if index else 0.0
                logger.info("epoch %d/%d train %d/%d bags | loss %.4f | lr %.2e | elapsed %.1fs | ETA %.1fs", epoch, args.epochs, index, len(ordered), total_loss / index, optimizer.param_groups[0]["lr"], elapsed, eta)
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
        train_auc = roc_auc(train_labels, train_probs)
        train_loss = total_loss / max(len(ordered), 1)

        val_predictions, val_windows = _evaluate_split(
            val_records,
            model,
            split="val",
            epoch=epoch,
            seed=args.seed,
            np=np,
            cv2=cv2,
            torch=torch,
            device=device,
            amp=amp,
            window_batch_size=args.window_batch_size,
            logger=logger,
        )
        val_auc = roc_auc([int(row["label"]) for row in val_predictions], [float(row["probability_fight"]) for row in val_predictions])
        if val_auc is None:
            raise RuntimeError("Validation ROC-AUC is undefined; both classes must be present after short-clip exclusions.")
        scheduler.step(val_auc)
        val_at_half = binary_metrics([int(row["label"]) for row in val_predictions], [float(row["probability_fight"]) for row in val_predictions], 0.5)
        improved = val_auc > best_auc
        significant_improvement = val_auc > patience_best_auc + args.min_delta
        if improved:
            best_auc = val_auc
            best_epoch = epoch
            _save_deployment_checkpoint(model, run_dir / "best_model.pt", torch, best_epoch=best_epoch, best_auc=best_auc)
        if significant_improvement:
            patience_best_auc = val_auc
            bad_epochs = 0
        else:
            bad_epochs += 1
        row = {
            "epoch": epoch,
            "train_loss": train_loss,
            "train_roc_auc": train_auc,
            "val_roc_auc": val_auc,
            "val_accuracy_at_0_5": val_at_half["accuracy"],
            "val_f1_at_0_5": val_at_half["f1"],
            "learning_rate_backbone": optimizer.param_groups[0]["lr"],
            "learning_rate_head": optimizer.param_groups[1]["lr"],
            "seconds": time.perf_counter() - epoch_start,
            "improved": int(improved),
        }
        peak_gpu_mb = torch.cuda.max_memory_allocated(device) / (1024 * 1024) if device.type == "cuda" else 0.0
        row["peak_gpu_memory_mb"] = peak_gpu_mb
        history.append(row)
        _write_csv(run_dir / "training_history.csv", history)
        _write_predictions(run_dir, "validation", val_predictions, val_windows)
        logger.info("epoch %d summary | train_loss=%.5f train_auc=%s val_auc=%.6f val_f1@0.5=%.4f best_auc=%.6f (%d) patience_best=%.6f patience=%d/%d peak_gpu=%.0f MiB", epoch, train_loss, "n/a" if train_auc is None else f"{train_auc:.6f}", val_auc, val_at_half["f1"], best_auc, best_epoch, patience_best_auc, bad_epochs, args.patience, peak_gpu_mb)
        _save_last_checkpoint(
            model=model,
            optimizer=optimizer,
            scheduler=scheduler,
            scaler=scaler,
            torch=torch,
            np=np,
            run_dir=run_dir,
            completed_epoch=epoch,
            best_epoch=best_epoch,
            best_auc=best_auc,
            patience_best_auc=patience_best_auc,
            epochs_without_improvement=bad_epochs,
            history=history,
            dataset_fingerprint=fingerprint,
            config=config,
        )
        if consume_pause_request(args.pause_file):
            logger.info("Training paused safely after epoch %d. Resume from %s", epoch, run_dir / "last.pt")
            return
        if bad_epochs >= args.patience:
            logger.info("Early stopping after epoch %d.", epoch)
            break
    if not (run_dir / "best_model.pt").is_file():
        raise RuntimeError("No best checkpoint was created; validation did not complete successfully.")
    best_payload = torch.load(run_dir / "best_model.pt", map_location="cpu", weights_only=True)
    model.load_state_dict(best_payload["state_dict"])
    logger.info("Restored best model from epoch %d (validation ROC-AUC %.6f).", best_epoch, best_auc)

    # Threshold calibration uses validation only. Test is opened only after
    # the best weights and all three operating points have been frozen.
    val_predictions, val_windows = _evaluate_split(
        val_records,
        model,
        split="val",
        epoch=best_epoch,
        seed=args.seed,
        np=np,
        cv2=cv2,
        torch=torch,
        device=device,
        amp=amp,
        window_batch_size=args.window_batch_size,
        logger=logger,
    )
    thresholds = choose_thresholds(
        [int(row["label"]) for row in val_predictions],
        [float(row["probability_fight"]) for row in val_predictions],
    )
    val_metrics = _mode_metrics(val_predictions, thresholds)
    _write_predictions(run_dir, "validation", val_predictions, val_windows)
    (run_dir / "thresholds.json").write_text(json.dumps({"protocol": CACHE_PROTOCOL, "selection": thresholds, "validation_metrics": val_metrics}, indent=2), encoding="utf-8")
    logger.info("Validation thresholds (locked before test): %s", json.dumps(thresholds, ensure_ascii=False))

    test_records = [r for r in records if r.split == "test"]
    if not test_records:
        raise RuntimeError("Test split has no eligible videos after short-clip exclusions.")
    test_start = time.perf_counter()
    test_predictions, test_windows = _evaluate_split(
        test_records,
        model,
        split="test",
        epoch=best_epoch,
        seed=args.seed,
        np=np,
        cv2=cv2,
        torch=torch,
        device=device,
        amp=amp,
        window_batch_size=args.window_batch_size,
        logger=logger,
    )
    test_duration = time.perf_counter() - test_start
    test_window_count = sum(int(row["window_count"]) for row in test_predictions)
    test_metrics = _mode_metrics(test_predictions, thresholds)
    per_dataset = _per_dataset_metrics(test_predictions, thresholds)
    _write_predictions(run_dir, "test", test_predictions, test_windows)
    eligible_counts, excluded_counts = _count_split_samples(dataset_roots, records, manifest_rows)
    report = {
        "model": "MC3-18 Kinetics-400 fine-tuned",
        "protocol": {
            **_preprocessing_protocol(),
            "evaluation_tail": "drop_incomplete",
            "training_tail": "one_end_aligned_window_per_video_when_needed",
            "video_aggregation": "maximum_window_probability",
        },
        "best_epoch": best_epoch,
        "best_validation_roc_auc": best_auc,
        "thresholds": thresholds,
        "validation_metrics": val_metrics,
        "test_metrics": test_metrics,
        "test_metrics_by_dataset": per_dataset,
        "test_evaluation_seconds": test_duration,
        "test_video_count": len(test_predictions),
        "test_window_count": test_window_count,
        "test_seconds_per_video": test_duration / len(test_predictions),
        "test_seconds_per_window": test_duration / test_window_count if test_window_count else None,
        "test_videos_per_second": len(test_predictions) / test_duration if test_duration else None,
        "eligible_counts": eligible_counts,
        "excluded_subsecond_counts": excluded_counts,
        "dataset_fingerprint": fingerprint,
        "deployment_checkpoint": str((run_dir / "best_model.pt").resolve()),
    }
    (run_dir / "final_report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    logger.info("Test throughput | %.3fs/video | %.3fs/window | %.2f videos/s", report["test_seconds_per_video"], report["test_seconds_per_window"] or 0.0, report["test_videos_per_second"] or 0.0)
    logger.info("Final test metrics:\n%s", json.dumps({"overall": test_metrics, "by_dataset": per_dataset}, indent=2, ensure_ascii=False, allow_nan=False))
    logger.info("Run complete. Artifacts: %s", run_dir)


def _run_evaluation(args: argparse.Namespace) -> None:
    """Calibrate validation thresholds and test an exported best_model.pt."""
    global _ACTIVE_RUN_DIR
    cv2, np, torch, nn, model_symbols = _import_runtime_dependencies()
    weights_enum, model_builder = model_symbols
    del weights_enum
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but PyTorch cannot see a CUDA device. Choose --device cpu explicitly to evaluate on CPU.")
    if args.device == "cpu" and not args.allow_cpu:
        raise RuntimeError("CPU evaluation is disabled by default. Add --allow-cpu to confirm.")
    device = torch.device("cuda:0" if args.device == "cuda" else "cpu")
    amp = device.type == "cuda" and not args.no_amp
    checkpoint_path = Path(args.checkpoint).expanduser().resolve()
    if not checkpoint_path.is_file():
        raise FileNotFoundError(f"Deployment checkpoint not found: {checkpoint_path}")
    output_root = Path(args.output_root).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else output_root / f"eval_{timestamp}"
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(f"Evaluation directory already contains files: {run_dir}. Choose a new --run-dir.")
    _ACTIVE_RUN_DIR = run_dir
    logger = _log_setup(run_dir)
    logger.info("MC3-18 MIL standalone evaluation | device=%s | amp=%s | checkpoint=%s", device, amp, checkpoint_path)

    dataset_root = Path(args.dataset_root).expanduser().resolve()
    dataset_roots = discover_dataset_roots(dataset_root)
    try:
        records, manifest_rows = _scan_datasets(dataset_root, cv2, logger)
    except Exception:
        logger.exception("Dataset preflight failed.")
        raise
    fingerprint = _dataset_fingerprint(records)
    _write_csv(run_dir / "dataset_manifest.csv", manifest_rows)
    _print_data_summary(records, manifest_rows, logger, [name for name, _ in dataset_roots])
    validation_records = [record for record in records if record.split == "val"]
    test_records = [record for record in records if record.split == "test"]
    if not validation_records or len({record.label for record in validation_records}) < 2:
        raise RuntimeError("Validation must contain eligible videos from both Fight and NonFight to calibrate thresholds.")
    if not test_records:
        raise RuntimeError("Test split has no eligible videos after short-clip exclusions.")

    cache_root = Path(args.cache_dir).expanduser().resolve() if args.cache_dir else default_cache_root()
    evaluation_cache_records = [record for record in records if record.split in {"val", "test"}]
    try:
        _prepare_cache(evaluation_cache_records, cache_root, cv2, np, logger, rebuild=args.rebuild_cache)
    except Exception:
        logger.exception("Video frame decoding/cache preparation failed.")
        raise
    try:
        payload = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    except TypeError:
        payload = torch.load(checkpoint_path, map_location="cpu")
    if (
        payload.get("deployment_checkpoint_version") != 1
        or payload.get("model_name") != "mc3_18"
        or payload.get("class_to_idx") != CLASS_TO_LABEL
        or not isinstance(payload.get("state_dict"), dict)
    ):
        raise ValueError("--checkpoint must point to an exported best_model.pt deployment checkpoint.")
    saved_protocol = payload.get("protocol", {})
    expected_protocol = _preprocessing_protocol()
    mismatches = [key for key, value in expected_protocol.items() if saved_protocol.get(key) != value]
    if mismatches:
        raise ValueError("Checkpoint preprocessing protocol does not match this evaluator: " + ", ".join(mismatches))
    model = _build_model(torch, nn, None, model_builder, device, pretrained=False)
    model.load_state_dict(payload["state_dict"])
    model.eval()
    best_epoch = int(saved_protocol.get("best_epoch", 0))
    best_auc = saved_protocol.get("best_validation_roc_auc")
    (run_dir / "config.json").write_text(
        json.dumps(
            {
                "run_mode": "evaluation_only",
                "dataset_root": str(dataset_root),
                "checkpoint": str(checkpoint_path),
                "device": str(device),
                "amp": amp,
                "protocol": expected_protocol,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    validation_predictions, validation_windows = _evaluate_split(
        validation_records,
        model,
        split="val",
        epoch=best_epoch,
        seed=args.seed,
        np=np,
        cv2=cv2,
        torch=torch,
        device=device,
        amp=amp,
        window_batch_size=args.window_batch_size,
        logger=logger,
    )
    thresholds = choose_thresholds(
        [int(row["label"]) for row in validation_predictions],
        [float(row["probability_fight"]) for row in validation_predictions],
    )
    validation_metrics = _mode_metrics(validation_predictions, thresholds)
    _write_predictions(run_dir, "validation", validation_predictions, validation_windows)
    (run_dir / "thresholds.json").write_text(
        json.dumps({"protocol": CACHE_PROTOCOL, "selection": thresholds, "validation_metrics": validation_metrics}, indent=2),
        encoding="utf-8",
    )
    logger.info("Validation thresholds locked before test: %s", json.dumps(thresholds, ensure_ascii=False))

    test_start = time.perf_counter()
    test_predictions, test_windows = _evaluate_split(
        test_records,
        model,
        split="test",
        epoch=best_epoch,
        seed=args.seed,
        np=np,
        cv2=cv2,
        torch=torch,
        device=device,
        amp=amp,
        window_batch_size=args.window_batch_size,
        logger=logger,
    )
    test_duration = time.perf_counter() - test_start
    test_window_count = sum(int(row["window_count"]) for row in test_predictions)
    test_metrics = _mode_metrics(test_predictions, thresholds)
    per_dataset = _per_dataset_metrics(test_predictions, thresholds)
    _write_predictions(run_dir, "test", test_predictions, test_windows)
    eligible_counts, excluded_counts = _count_split_samples(dataset_roots, records, manifest_rows)
    report = {
        "run_mode": "evaluation_only",
        "model": "MC3-18 Kinetics-400 fine-tuned",
        "protocol": {**expected_protocol, "evaluation_tail": "drop_incomplete", "video_aggregation": "maximum_window_probability"},
        "best_epoch": best_epoch,
        "best_validation_roc_auc": best_auc,
        "thresholds": thresholds,
        "validation_metrics": validation_metrics,
        "test_metrics": test_metrics,
        "test_metrics_by_dataset": per_dataset,
        "test_evaluation_seconds": test_duration,
        "test_video_count": len(test_predictions),
        "test_window_count": test_window_count,
        "test_seconds_per_video": test_duration / len(test_predictions),
        "test_seconds_per_window": test_duration / test_window_count if test_window_count else None,
        "test_videos_per_second": len(test_predictions) / test_duration if test_duration else None,
        "eligible_counts": eligible_counts,
        "excluded_subsecond_counts": excluded_counts,
        "dataset_fingerprint": fingerprint,
        "deployment_checkpoint": str(checkpoint_path),
    }
    (run_dir / "final_report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    logger.info("Test throughput | %.3fs/video | %.3fs/window | %.2f videos/s", report["test_seconds_per_video"], report["test_seconds_per_window"] or 0.0, report["test_videos_per_second"] or 0.0)
    logger.info("Final test metrics:\n%s", json.dumps({"overall": test_metrics, "by_dataset": per_dataset}, indent=2, ensure_ascii=False, allow_nan=False))
    logger.info("Evaluation complete. Artifacts: %s", run_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", default=str(default_dataset_root()))
    parser.add_argument("--output-root", default=str(project_root() / "model_runs" / "mc3_1s4f"))
    parser.add_argument("--run-dir", help="Use a fixed run directory; by default a timestamped directory is created.")
    parser.add_argument("--cache-dir", help="Reusable unaugmented decoded-frame cache.")
    parser.add_argument("--resume", help="Resume from this run's last.pt checkpoint.")
    parser.add_argument("--pause-file", help="Internal dashboard control marker; pause safely after the current complete epoch.")
    parser.add_argument("--evaluate-only", action="store_true", help="Calibrate thresholds on validation and evaluate a deployment checkpoint on test, without training.")
    parser.add_argument("--checkpoint", help="Exported best_model.pt used with --evaluate-only.")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--allow-cpu", action="store_true", help="Explicitly allow slow CPU training or evaluation.")
    parser.add_argument("--no-amp", action="store_true", help="Disable CUDA mixed precision.")
    parser.add_argument("--rebuild-cache", action="store_true")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--min-delta", type=float, default=0.001)
    parser.add_argument("--backbone-lr", type=float, default=2e-5)
    parser.add_argument("--head-lr", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--window-batch-size", type=int, default=8)
    parser.add_argument("--log-every", type=int, default=25)
    parser.add_argument("--seed", type=int, default=42)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.epochs < 1 or args.patience < 1 or args.grad_accum < 1 or args.window_batch_size < 1 or args.log_every < 1:
        parser.error("epochs, patience, grad-accum, window-batch-size, and log-every must be positive")
    if args.backbone_lr <= 0 or args.head_lr <= 0 or args.weight_decay < 0 or args.min_delta < 0:
        parser.error("learning rates must be positive; weight-decay and min-delta must be non-negative")
    if args.evaluate_only and (not args.checkpoint or args.resume):
        parser.error("--evaluate-only requires --checkpoint and cannot be combined with --resume")
    if args.checkpoint and not args.evaluate_only:
        parser.error("--checkpoint is only used together with --evaluate-only")
    if args.device == "auto":
        try:
            import torch
        except Exception:
            args.device = "cuda"
        else:
            args.device = "cuda" if torch.cuda.is_available() else "cpu"
            if args.device == "cpu" and not args.allow_cpu:
                action = "evaluation" if args.evaluate_only else "training"
                raise RuntimeError(f"No CUDA device was found. {action.title()} on CPU may be slow; rerun with --device cpu --allow-cpu to confirm.")
    try:
        if args.evaluate_only:
            _run_evaluation(args)
        else:
            _run_training(args)
    except KeyboardInterrupt:
        if args.evaluate_only:
            print("\nEvaluation interrupted. No final test report was completed; rerun the same --evaluate-only command to retry.", flush=True)
            return 130
        run_dir = _ACTIVE_RUN_DIR
        last_checkpoint = run_dir / "last.pt" if run_dir is not None else None
        if last_checkpoint is not None and last_checkpoint.is_file():
            print(f"\nInterrupted. Latest complete epoch checkpoint: {last_checkpoint}\nResume with --resume \"{last_checkpoint}\"", flush=True)
        else:
            print("\nInterrupted before a complete epoch checkpoint was saved. Start a new run to continue.", flush=True)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
