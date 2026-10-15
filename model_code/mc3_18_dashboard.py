"""Local browser dashboard for the MC3-18 MIL training and inference workflow.

Run from the project root with the same Python environment used for training:
    python model_code/mc3_18_dashboard.py

The server binds to 127.0.0.1 only. It uses Python's standard-library HTTP
server; PyTorch, TorchVision, NumPy and OpenCV are loaded only for model work.
"""

from __future__ import annotations

import argparse
import cgi
import csv
import hashlib
import importlib.util
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import warnings
import webbrowser
import zipfile
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRAINING_SCRIPT = Path(__file__).resolve().with_name("mc3_18_mil_pipeline.py")
REVERSE_TRAINING_SCRIPT = Path(__file__).resolve().with_name("mc3_18_reverse_concat_pipeline.py")
FOUR_SECOND_TRAINING_SCRIPT = Path(__file__).resolve().with_name("mc3_18_four_second_pipeline.py")
SKELETON_TRAINING_SCRIPT = Path(__file__).resolve().with_name("mc3_18_skeleton_four_second_pipeline.py")
NATIVE_FINETUNE_TRAINING_SCRIPT = Path(__file__).resolve().with_name("mc3_18_native_finetune_pipeline.py")
FULL_FRAME_TRAINING_SCRIPT = Path(__file__).resolve().with_name("mc3_18_full_frame_pipeline.py")
HARVARD_LETTERBOX_TRAINING_SCRIPT = Path(__file__).resolve().with_name("mc3_18_harvard_letterbox_pipeline.py")
TEMPORAL_SHIFT_TRAINING_SCRIPT = Path(__file__).resolve().with_name("mc3_18_temporal_shift_letterbox_pipeline.py")
HTML_PATH = Path(__file__).resolve().with_name("mc3_18_dashboard.html")
SKELETON_HTML_PATH = Path(__file__).resolve().with_name("mc3_18_skeleton_dashboard.html")
NATIVE_FINETUNE_HTML_PATH = Path(__file__).resolve().with_name("mc3_18_native_finetune_dashboard.html")
FULL_FRAME_HTML_PATH = Path(__file__).resolve().with_name("mc3_18_full_frame_dashboard.html")
HARVARD_LETTERBOX_HTML_PATH = Path(__file__).resolve().with_name("mc3_18_harvard_letterbox_dashboard.html")
TEMPORAL_SHIFT_HTML_PATH = Path(__file__).resolve().with_name("mc3_18_temporal_shift_dashboard.html")
DEFAULT_DATASET_ROOT = PROJECT_ROOT / "GuardAId Video Classification Model Dataset"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_1s4f"
DEFAULT_REVERSE_UCF_ROOT = DEFAULT_DATASET_ROOT / "UCF"
DEFAULT_REVERSE_LEGACY_ROOT = DEFAULT_DATASET_ROOT
DEFAULT_REVERSE_AIRTLAB_ROOT = (
    DEFAULT_DATASET_ROOT / "AIRTLab" / "A-Dataset-for-Automatic-Violence-Detection-in-Videos-master"
    / "violence-detection-dataset"
)
DEFAULT_REVERSE_GUARDAID_ROOT = DEFAULT_DATASET_ROOT / "GuardAId"
DEFAULT_REVERSE_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_2s8f_reverse_concat"
DEFAULT_FOUR_SECOND_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_4s16f"
DEFAULT_SKELETON_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_multidataset_skeleton_4s16f2"
DEFAULT_NATIVE_FINETUNE_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_native_four_dataset_finetune_4s16f2"
DEFAULT_FULL_FRAME_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_full_frame_shortside112_4s16f2"
DEFAULT_HARVARD_LETTERBOX_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_harvard_letterbox_3dataset_4s16f2_k400"
DEFAULT_TEMPORAL_SHIFT_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_temporal_shift_letterbox_4dataset_4s16f2_k400"
NATIVE_FINETUNE_EXPERIMENT_PROTOCOL = "mc3-native-four-dataset-finetune-4s16f2-v1"
FULL_FRAME_EXPERIMENT_PROTOCOL = "mc3-five-dataset-fullframe-shortside112-4s16f2-v1"
HARVARD_LETTERBOX_EXPERIMENT_PROTOCOL = "mc3-harvard-rwf-airtlab-letterbox160-4s16f2-k400-v2"
TEMPORAL_SHIFT_EXPERIMENT_PROTOCOL = "mc3-harvard-rwf-airtlab-dancetrack-letterbox160-4s16f2-temporal-jitter-k400-v1"
NATIVE_FINETUNE_DEFAULT_CONFIG = {
    "epochs": 15, "patience": 4, "min_delta": 0.001,
    "backbone_lr": 2e-5, "head_lr": 1e-4, "weight_decay": 1e-4,
    "grad_accum": 8, "window_batch_size": 2, "seed": 2026,
    "lr_reduce_after": 2, "lr_factor": 0.5, "min_lr": 1e-6,
}
HARVARD_LETTERBOX_DEFAULT_CONFIG = {
    "epochs": 15, "patience": 4, "min_delta": 0.001,
    "backbone_lr": 2e-5, "head_lr": 1e-4, "weight_decay": 1e-4,
    "grad_accum": 8, "window_batch_size": 2, "seed": 2026,
    "lr_reduce_after": 2, "lr_factor": 0.5, "min_lr": 1e-6,
}
TEMPORAL_SHIFT_DEFAULT_CONFIG = {
    **HARVARD_LETTERBOX_DEFAULT_CONFIG,
    "epochs": 20,
    "patience": 4,
    "lr_reduce_after": 2,
}
SKELETON_EXPERIMENT_PROTOCOL = "mc3-multidataset-black-skeleton-4s2s16f-v1"
SKELETON_DEFAULT_TRAIN_CONFIG = {
    # Keep the requested skeleton-experiment limits. Initial LRs are half of
    # the audited native-video 4S16F2 values; other optimizer defaults match it.
    "epochs": 15, "patience": 4, "min_delta": 0.001,
    "backbone_lr": 1e-5, "head_lr": 5e-5, "weight_decay": 1e-4,
    "grad_accum": 8, "window_batch_size": 2, "seed": 2026,
    "lr_reduce_after": 2, "lr_factor": 0.5, "min_lr": 1e-6,
}
DEFAULT_FOUR_SECOND_SUPPLEMENT_CACHE = DEFAULT_FOUR_SECOND_OUTPUT_ROOT / "_supplemental_cache"
DEFAULT_TWO_SECOND_FRAME_CACHE = (
    DEFAULT_REVERSE_OUTPUT_ROOT / "_frame_cache"
    / "mc3-2s-1s-stride-8f-128short-112crop-reverse-concat-v2"
)
REVERSE_LEGACY_DATASET_LAYOUT = (
    ("RWF-2000", Path("RWF-2000")),
    ("Real Life Violence Dataset", Path("archive") / "Real Life Violence Dataset"),
    ("bus-violence", Path("bus-violence")),
    ("seymanurakti-fight-detection-surv-dataset-ff83b7c", Path("seymanurakti-fight-detection-surv-dataset-ff83b7c")),
    ("ShanghaiTech", Path("ShanghaiTech")),
)
AIRTLAB_MODALITIES_PROTOCOL = "airtlab-native-vs-rtmpose-skeleton-mc3-4s2s-16f-v1"
AIRTLAB_MODALITIES_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_airtlab_modalities_4s16f2"
MAX_UPLOAD_BYTES = 300 * 1024 * 1024
LOG_TAIL_BYTES = 32_000
_POSE_ENGINE: Any = None
_POSE_ENGINE_LOCK = threading.Lock()
_POSE_PIPELINE_MODULE: Any = None
_POSE_PIPELINE_LOCK = threading.Lock()


class DashboardState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.train_process: subprocess.Popen[str] | None = None
        self.run_dir: Path | None = None
        self.pause_file: Path | None = None
        self.training_status = "idle"
        self.stop_requested = False
        self.pause_requested = False
        self.command: list[str] = []
        self.console = deque(maxlen=400)
        self.media_jobs: dict[str, dict[str, Any]] = {}
        self.inference: dict[str, Any] = {
            "job_id": None,
            "status": "idle",
            "stage": "",
            "progress": 0.0,
            "processed_windows": 0,
            "total_windows": 0,
            "result": None,
            "error": None,
        }


STATE = DashboardState()


class ReverseTrainingState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.run_dir: Path | None = None
        self.output_root: Path = DEFAULT_REVERSE_OUTPUT_ROOT
        self.pause_file: Path | None = None
        self.status = "idle"
        self.stop_requested = False
        self.pause_requested = False
        self.started_at: float | None = None
        self.command: list[str] = []
        self.console = deque(maxlen=400)


REVERSE_STATE = ReverseTrainingState()


class SkeletonTrainingState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.run_dir: Path | None = None
        self.output_root = DEFAULT_SKELETON_OUTPUT_ROOT
        self.pause_file: Path | None = None
        self.status = "idle"
        self.pause_requested = False
        self.stop_requested = False
        self.started_at: float | None = None
        self.command: list[str] = []
        self.console = deque(maxlen=500)


SKELETON_STATE = SkeletonTrainingState()


class NativeFinetuneState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.run_dir: Path | None = None
        self.pause_file: Path | None = None
        self.status = "idle"
        self.pause_requested = False
        self.stop_requested = False
        self.started_at: float | None = None
        self.command: list[str] = []
        self.console = deque(maxlen=600)


NATIVE_FINETUNE_STATE = NativeFinetuneState()


class FullFrameTrainingState(NativeFinetuneState):
    pass


FULL_FRAME_STATE = FullFrameTrainingState()


class HarvardLetterboxTrainingState(NativeFinetuneState):
    pass


HARVARD_LETTERBOX_STATE = HarvardLetterboxTrainingState()


class TemporalShiftTrainingState(NativeFinetuneState):
    pass


TEMPORAL_SHIFT_STATE = TemporalShiftTrainingState()


def _mc3_training_active(except_name: str | None = None) -> bool:
    """Report any active MC3 training process across the dashboard pages."""
    candidates = (
        ("ordinary", STATE, "train_process", "training_status"),
        ("reverse_four_second", REVERSE_STATE, "process", "status"),
        ("skeleton", SKELETON_STATE, "process", "status"),
        ("native_finetune", NATIVE_FINETUNE_STATE, "process", "status"),
        ("full_frame", FULL_FRAME_STATE, "process", "status"),
        ("harvard_letterbox", HARVARD_LETTERBOX_STATE, "process", "status"),
        ("temporal_shift", TEMPORAL_SHIFT_STATE, "process", "status"),
    )
    for name, state, process_name, status_name in candidates:
        if name == except_name:
            continue
        with state.lock:
            process = getattr(state, process_name)
            status = getattr(state, status_name)
            if status == "starting" or (process is not None and process.poll() is None):
                return True
    return False


def _media_tool(name: str) -> str:
    tool = shutil.which(name)
    if tool:
        return tool
    packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for candidate in sorted(packages.glob(f"Gyan.FFmpeg_*/ffmpeg*/bin/{name}.exe")):
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("未找到 FFmpeg。请安装 FFmpeg 后重新启动工作台，以启用兼容音频预览。")


def _media_payload(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if key not in {"directory", "path", "process", "released"}}


def _release_media(job_id: str) -> None:
    with STATE.lock:
        job = STATE.media_jobs.get(job_id)
        if not job:
            return
        job["released"] = True
        process = job.get("process")
        if process and process.poll() is None:
            process.terminate()
        if job["status"] not in {"queued", "running"}:
            STATE.media_jobs.pop(job_id, None)
            shutil.rmtree(job["directory"], ignore_errors=True)


def _prepare_media_worker(job_id: str, source: Path) -> None:
    with STATE.lock:
        job = STATE.media_jobs[job_id]
        job["status"] = "running"
    try:
        probe = subprocess.run(
            [_media_tool("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(source)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if probe.returncode:
            raise RuntimeError("无法读取视频音轨：" + probe.stderr[-600:])
        streams = json.loads(probe.stdout).get("streams", [])
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        if not video:
            raise RuntimeError("文件没有可播放的视频轨道。")
        destination = source.parent / "preview.mp4"
        video_options = (["-c:v", "copy"] if video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p"
                         else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"])
        command = [
            _media_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", *video_options,
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(destination),
        ]
        # Register and start under the same lock so release cannot miss a newly spawned process.
        with STATE.lock:
            if job.get("released"):
                return
            process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            job["process"] = process
        try:
            _, error = process.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise RuntimeError("兼容预览准备超时，请尝试较短的视频。")
        if process.returncode or not destination.is_file():
            raise RuntimeError("无法生成带音轨的兼容预览：" + (error or b"").decode("utf-8", errors="replace")[-600:])
        with STATE.lock:
            job.update(status="completed", path=destination, has_audio=bool(audio),
                       source_audio_codec=audio.get("codec_name") if audio else None,
                       url=f"/api/media/{job_id}/preview.mp4", error=None)
    except Exception as exc:
        with STATE.lock:
            job.update(status="failed", error=str(exc))
    finally:
        try:
            source.unlink(missing_ok=True)
        except OSError:
            pass
        with STATE.lock:
            job.pop("process", None)
            if job.get("released"):
                STATE.media_jobs.pop(job_id, None)
                shutil.rmtree(job["directory"], ignore_errors=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _tail_file(path: Path, byte_limit: int = LOG_TAIL_BYTES) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - byte_limit), os.SEEK_SET)
            content = handle.read().decode("utf-8", errors="replace")
        return "\n".join(content.splitlines()[-180:])
    except OSError:
        return ""


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _metric_summary(rows: list[dict[str, str]]) -> dict[str, Any] | None:
    labels: list[int] = []
    probabilities: list[float] = []
    for row in rows:
        try:
            labels.append(int(row["label"]))
            probabilities.append(float(row["probability_fight"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not labels:
        return None
    predicted = [int(score >= 0.5) for score in probabilities]
    tp = sum(y == 1 and p == 1 for y, p in zip(labels, predicted))
    tn = sum(y == 0 and p == 0 for y, p in zip(labels, predicted))
    fp = sum(y == 0 and p == 1 for y, p in zip(labels, predicted))
    fn = sum(y == 1 and p == 0 for y, p in zip(labels, predicted))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    positive_count = sum(label == 1 for label in labels)
    negative_count = len(labels) - positive_count
    auc: float | None = None
    if positive_count and negative_count:
        # Rank formula keeps periodic dashboard refreshes fast even when the
        # validation set contains thousands of videos.
        ranked = sorted(enumerate(probabilities), key=lambda pair: pair[1])
        positive_rank_sum = 0.0
        start = 0
        while start < len(ranked):
            end = start + 1
            while end < len(ranked) and ranked[end][1] == ranked[start][1]:
                end += 1
            average_rank = ((start + 1) + end) / 2.0
            positive_rank_sum += sum(labels[index] == 1 for index, _ in ranked[start:end]) * average_rank
            start = end
        auc = (positive_rank_sum - positive_count * (positive_count + 1) / 2.0) / (positive_count * negative_count)
    return {
        "n": len(labels),
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "accuracy": (tp + tn) / len(labels),
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "roc_auc": auc,
    }


def _validation_by_dataset(run_dir: Path) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in _read_csv(run_dir / "validation_predictions.csv"):
        grouped.setdefault(row.get("dataset", "unknown"), []).append(row)
    output = []
    for dataset, rows in sorted(grouped.items()):
        metrics = _metric_summary(rows)
        if metrics is not None:
            output.append({"dataset": dataset, **metrics})
    return output


def _training_status_payload() -> dict[str, Any]:
    with STATE.lock:
        process = STATE.train_process
        run_dir = STATE.run_dir
        requested_stop = STATE.stop_requested
        requested_pause = STATE.pause_requested
        status = STATE.training_status
        command = list(STATE.command)
        console_tail = list(STATE.console)[-100:]
    if run_dir is None:
        return {
            "status": status,
            "run_dir": None,
            "last_checkpoint": None,
            "console": console_tail,
            "history": [],
            "validation_by_dataset": [],
        }

    return_code = process.poll() if process is not None else None
    final_report = _read_json(run_dir / "final_report.json")
    if process is not None and return_code is None:
        current_status = "pause_requested" if requested_pause else "running"
    elif requested_stop:
        current_status = "stopped"
    elif final_report is not None:
        current_status = "completed"
    elif (run_dir / "last.pt").is_file() and return_code == 0:
        current_status = "paused"
    elif process is not None and return_code is not None:
        current_status = "failed"
    else:
        current_status = status

    log_text = _tail_file(run_dir / "run.log")
    if not log_text:
        log_text = "\n".join(console_tail)
    history_rows = _read_csv(run_dir / "training_history.csv")
    history: list[dict[str, Any]] = []
    for row in history_rows:
        normalized: dict[str, Any] = {}
        for key, value in row.items():
            parsed = _safe_float(value)
            normalized[key] = parsed if parsed is not None else value
        history.append(normalized)

    current_epoch = int(history[-1].get("epoch", 0)) + 1 if history else 1
    total_epochs = 30
    stage = "准备中"
    stage_progress = 0.0
    eta_seconds: float | None = None
    train_match: re.Match[str] | None = None
    validation_match: re.Match[str] | None = None
    for line in reversed(log_text.splitlines()):
        if train_match is None:
            train_match = re.search(r"epoch\s+(\d+)/(\d+)\s+train\s+(\d+)/(\d+)\s+bags", line)
        if validation_match is None:
            validation_match = re.search(r"(val|test) progress\s+(\d+)/(\d+)\s+videos", line)
        eta_match = re.search(r"ETA\s+([0-9.]+)s", line)
        if eta_seconds is None and eta_match:
            eta_seconds = _safe_float(eta_match.group(1))
        if train_match and validation_match and eta_seconds is not None:
            break
    if train_match:
        current_epoch = int(train_match.group(1))
        total_epochs = int(train_match.group(2))
        stage_progress = int(train_match.group(3)) / max(1, int(train_match.group(4)))
        stage = "训练中"
    elif validation_match:
        stage = "验证中" if validation_match.group(1) == "val" else "测试中"
        stage_progress = int(validation_match.group(2)) / max(1, int(validation_match.group(3)))
        total_epochs = int(history[-1].get("epoch", 30)) if history else 30
        current_epoch = int(total_epochs)
    elif history:
        total_epochs = int(history[-1].get("epoch", 30))
        current_epoch = total_epochs + 1
        stage = "保存本轮结果"
    config = _read_json(run_dir / "config.json") or {}
    manifest = _read_csv(run_dir / "dataset_manifest.csv")
    sample_total = sum(1 for row in manifest if row.get("eligible") == "1")
    excluded_total = sum(1 for row in manifest if row.get("eligible") == "0")
    best_payload = final_report or {}
    if current_status == "completed":
        stage = "训练、校准与测试已完成"
        stage_progress = 1.0
        current_epoch = int(best_payload.get("best_epoch", current_epoch))
        total_epochs = max(total_epochs, int(config.get("epochs", total_epochs)))
    elif current_status in {"paused", "stopped"}:
        stage = "已暂停，可从最近 checkpoint 恢复" if current_status == "paused" else "已停止，可从最近 checkpoint 恢复"

    return {
        "status": current_status,
        "run_dir": str(run_dir),
        "run_name": run_dir.name,
        "last_checkpoint": str(run_dir / "last.pt") if (run_dir / "last.pt").is_file() else None,
        "best_model": str(run_dir / "best_model.pt") if (run_dir / "best_model.pt").is_file() else None,
        "return_code": return_code,
        "stage": stage,
        "stage_progress": max(0.0, min(1.0, stage_progress)),
        "current_epoch": current_epoch,
        "total_epochs": total_epochs,
        "completed_epochs": len(history),
        "eta_seconds": eta_seconds,
        "sample_total": sample_total,
        "excluded_total": excluded_total,
        "manifest_count": len(manifest),
        "history": history,
        "latest_epoch": history[-1] if history else None,
        "validation_by_dataset": _validation_by_dataset(run_dir),
        "final_report": best_payload,
        "log_text": log_text,
        "config": config,
        "command": command,
    }


def _inference_threshold_payload(checkpoint: Path) -> dict[str, Any] | None:
    """Load calibrated threshold modes for a checkpoint, including the AIRT Lab arms."""
    threshold_payload = _read_json(checkpoint.parent / "thresholds.json")
    if threshold_payload and threshold_payload.get("protocol") in {
        "mc3-1s-4f-128short-112crop-v1",
        "mc3-2s-1s-stride-8f-128short-112crop-reverse-concat-v2",
        "mc3-4s-2s-stride-16f-128short-112crop-short-tail-v1",
        SKELETON_EXPERIMENT_PROTOCOL,
        FULL_FRAME_EXPERIMENT_PROTOCOL,
        HARVARD_LETTERBOX_EXPERIMENT_PROTOCOL,
        TEMPORAL_SHIFT_EXPERIMENT_PROTOCOL,
    }:
        selection = threshold_payload.get("selection")
        if isinstance(selection, dict) and all(key in selection for key in ("sensitive", "moderate", "certain")):
            return {
                "protocol": threshold_payload["protocol"],
                "selection": selection,
                "threshold_modes": ["sensitive", "moderate", "certain"],
                "default_threshold_mode": "moderate",
            }

    report = _read_json(checkpoint.parent / "final_report.json") or {}
    config = _read_json(checkpoint.parent / "config.json") or _read_json(checkpoint.parent.parent / "config.json") or {}
    representation = report.get("input_representation")
    if (report.get("experiment_protocol") or config.get("experiment_protocol")) != AIRTLAB_MODALITIES_PROTOCOL or representation not in {
        "native_video", "black_background_skeleton"
    }:
        return None
    try:
        validation_threshold = float(report["validation_best_f1_threshold"]["threshold"])
        if not math.isfinite(validation_threshold) or not 0 <= validation_threshold <= 1:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "protocol": "mc3-4s-2s-stride-16f-128short-112crop-short-tail-v1",
        "selection": {
            "fixed_0_5": {"threshold": 0.5, "objective": "fixed threshold"},
            "validation_best_f1": {
                "threshold": validation_threshold,
                "objective": "maximize validation video-level F1",
                "validation_f1": report["validation_best_f1_threshold"].get("validation_f1"),
            },
        },
        "threshold_modes": ["fixed_0_5", "validation_best_f1"],
        "default_threshold_mode": "validation_best_f1",
    }


def _list_runs(output_root: Path, *, include_nested: bool = False) -> list[dict[str, Any]]:
    if not output_root.is_dir():
        return []
    runs: list[dict[str, Any]] = []
    candidates = [path for path in output_root.iterdir() if path.is_dir()]
    if include_nested:
        # Modality experiments store checkpoints one level below each experiment run.
        candidates.extend(
            child for run_dir in candidates if not run_dir.name.startswith("_") and run_dir.is_dir()
            for child in run_dir.iterdir() if child.is_dir()
        )
    candidates = sorted(set(candidates), key=lambda path: path.stat().st_mtime, reverse=True)
    for run_dir in candidates:
        if run_dir.name.startswith("_") or not any((run_dir / name).is_file() for name in ("last.pt", "best_model.pt", "run.log")):
            continue
        report = _read_json(run_dir / "final_report.json") or {}
        config = _read_json(run_dir / "config.json") or _read_json(run_dir.parent / "config.json") or {}
        protocol = config.get("protocol")
        if not isinstance(protocol, dict):
            protocol = config.get("preprocessing")
        if not isinstance(protocol, dict):
            protocol = report.get("protocol")
        if not isinstance(protocol, dict):
            protocol = {}
        has_last = (run_dir / "last.pt").is_file()
        has_best = (run_dir / "best_model.pt").is_file()
        threshold_payload = _inference_threshold_payload(run_dir / "best_model.pt") if has_best else None
        has_thresholds = threshold_payload is not None
        status = "completed" if report else "resumable" if has_last else "incomplete"
        representation = report.get("input_representation")
        modality_name = {
            "native_video": "原生视频",
            "native_video_full_frame_short_side_112": "全画面原生视频·短边112",
            "native_video_letterbox_160x160_rgb_mean": "全画面均值补边·160×160",
            "black_background_skeleton": "黑底骨架",
        }.get(representation)
        display_name = run_dir.name
        if (report.get("experiment_protocol") or config.get("experiment_protocol")) == SKELETON_EXPERIMENT_PROTOCOL:
            epoch_mark = f" · Epoch {report.get('best_epoch')}" if report.get("epoch_checkpoint") else ""
            display_name = f"四数据集黑底骨架 · {run_dir.parent.name}{epoch_mark}"
        elif modality_name:
            display_name = f"AIRT Lab · {modality_name} · {run_dir.parent.name}"
        if (report.get("experiment_protocol") or config.get("experiment_protocol")) == NATIVE_FINETUNE_EXPERIMENT_PROTOCOL:
            if run_dir.name.startswith("epoch_"):
                display_name = f"四数据集原生视频后训练 · {run_dir.parent.name} · {run_dir.name.replace('_', ' ').title()}"
            else:
                display_name = f"四数据集原生视频后训练 · {run_dir.name} · Best"
        if (report.get("experiment_protocol") or config.get("experiment_protocol")) == FULL_FRAME_EXPERIMENT_PROTOCOL:
            if run_dir.name.startswith("epoch_"):
                display_name = f"五数据集全画面训练 · {run_dir.parent.name} · {run_dir.name.replace('_', ' ').title()}"
            else:
                display_name = f"五数据集全画面训练 · {run_dir.name} · Best"
        if (report.get("experiment_protocol") or config.get("experiment_protocol")) == HARVARD_LETTERBOX_EXPERIMENT_PROTOCOL:
            if run_dir.name.startswith("epoch_"):
                display_name = f"Harvard/RWF/AIRT 均值补边训练 · {run_dir.parent.name} · {run_dir.name.replace('_', ' ').title()}"
            else:
                display_name = f"Harvard/RWF/AIRT 均值补边训练 · {run_dir.name} · Best"
        if (report.get("experiment_protocol") or config.get("experiment_protocol")) == TEMPORAL_SHIFT_EXPERIMENT_PROTOCOL:
            if run_dir.name.startswith("epoch_"):
                display_name = f"四数据集±2源帧补边训练 · {run_dir.parent.name} · {run_dir.name.replace('_', ' ').title()}"
            else:
                display_name = f"四数据集±2源帧补边训练 · {run_dir.name} · Best"
        runs.append(
            {
                "name": display_name,
                "run_dir": str(run_dir.resolve()),
                "last_checkpoint": str((run_dir / "last.pt").resolve()) if has_last else None,
                "best_model": str((run_dir / "best_model.pt").resolve()) if has_best else None,
                "can_infer": has_best and has_thresholds,
                "status": status,
                "best_epoch": report.get("best_epoch"),
                "best_validation_roc_auc": report.get("best_validation_roc_auc"),
                "epochs": config.get("epochs"),
                "window_seconds": protocol.get("window_seconds", config.get("window_seconds", 4 if modality_name else None)),
                "stride_seconds": protocol.get("stride_seconds", config.get("stride_seconds", 2 if modality_name else None)),
                "frames_per_window": protocol.get("frames_per_window", config.get("frames_per_window", 16 if modality_name else None)),
                "input_representation": representation,
                "threshold_modes": threshold_payload["threshold_modes"] if threshold_payload else [],
                "default_threshold_mode": threshold_payload["default_threshold_mode"] if threshold_payload else None,
            }
        )
    return runs[:80]


def _require_child_path(path_value: str, root_value: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    root = Path(root_value).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("The selected run/checkpoint must be inside the chosen output folder.") from exc
    return path


def _read_form_number(payload: dict[str, Any], key: str, default: int | float, *, integer: bool = False) -> int | float:
    value = payload.get(key, default)
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a valid number.") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{key} must be a finite number.")
    if integer:
        if not numeric.is_integer():
            raise ValueError(f"{key} must be a whole number.")
        return int(numeric)
    return numeric


def _training_arguments(payload: dict[str, Any], output_root: Path, pause_file: Path) -> tuple[list[str], Path, str]:
    resume_value = str(payload.get("resume_checkpoint") or "").strip()
    if resume_value:
        checkpoint = _require_child_path(resume_value, str(output_root))
        if checkpoint.name.lower() != "last.pt" or not checkpoint.is_file():
            raise ValueError("Choose an existing last.pt checkpoint to resume training.")
        run_dir = checkpoint.parent
        if (run_dir / "final_report.json").is_file():
            raise ValueError("This run already finished its final test and cannot be resumed. Start a new run instead.")
        saved = _read_json(run_dir / "config.json")
        if saved is None:
            raise ValueError("The selected checkpoint has no config.json; its training settings cannot be restored safely.")
        dataset_root = Path(str(saved.get("dataset_root", ""))).expanduser().resolve()
        if not dataset_root.is_dir():
            raise ValueError(f"Saved dataset folder no longer exists: {dataset_root}")
        config = saved
        device_value = "cpu" if str(config.get("device", "cuda")).startswith("cpu") else "cuda"
        cache_protocol = str(config.get("protocol") or "mc3-1s-4f-128short-112crop-v1")
        legacy_cache_dir = run_dir.parent / "_frame_cache" / cache_protocol
        shared_cache_dir = PROJECT_ROOT / "model_runs" / "_frame_cache" / cache_protocol
        cache_dir = str(config.get("cache_dir") or (legacy_cache_dir if legacy_cache_dir.is_dir() else shared_cache_dir))
        args = [
            "--dataset-root", str(dataset_root),
            "--output-root", str(run_dir.parent),
            "--resume", str(checkpoint),
            "--device", device_value,
            "--epochs", str(config.get("epochs", 30)),
            "--patience", str(config.get("patience", 6)),
            "--min-delta", str(config.get("min_delta", 0.001)),
            "--backbone-lr", str(config.get("backbone_lr", 2e-5)),
            "--head-lr", str(config.get("head_lr", 1e-4)),
            "--weight-decay", str(config.get("weight_decay", 1e-4)),
            "--grad-accum", str(config.get("grad_accum", 8)),
            "--window-batch-size", str(config.get("window_batch_size", 8)),
            "--seed", str(config.get("seed", 42)),
            "--cache-dir", str(Path(cache_dir).expanduser().resolve()),
            "--pause-file", str(pause_file),
        ]
        if device_value == "cpu":
            args.append("--allow-cpu")
        if not bool(config.get("amp", False)):
            args.append("--no-amp")
        return args, run_dir, str(dataset_root)

    dataset_root = Path(str(payload.get("dataset_root") or DEFAULT_DATASET_ROOT)).expanduser().resolve()
    if not dataset_root.is_dir():
        raise ValueError(f"Dataset folder not found: {dataset_root}")
    params = {
        "epochs": _read_form_number(payload, "epochs", 30, integer=True),
        "patience": _read_form_number(payload, "patience", 6, integer=True),
        "min_delta": _read_form_number(payload, "min_delta", 0.001),
        "backbone_lr": _read_form_number(payload, "backbone_lr", 2e-5),
        "head_lr": _read_form_number(payload, "head_lr", 1e-4),
        "weight_decay": _read_form_number(payload, "weight_decay", 1e-4),
        "grad_accum": _read_form_number(payload, "grad_accum", 8, integer=True),
        "window_batch_size": _read_form_number(payload, "window_batch_size", 8, integer=True),
        "seed": _read_form_number(payload, "seed", 42, integer=True),
    }
    if any(params[key] < 1 for key in ("epochs", "patience", "grad_accum", "window_batch_size")):
        raise ValueError("Epochs, patience, gradient accumulation and window batch size must be positive.")
    if params["backbone_lr"] <= 0 or params["head_lr"] <= 0 or params["weight_decay"] < 0 or params["min_delta"] < 0:
        raise ValueError("Learning rates must be positive; weight decay and min delta must be non-negative.")
    device = str(payload.get("device", "auto"))
    if device not in {"auto", "cuda", "cpu"}:
        raise ValueError("Device must be auto, cuda or cpu.")
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / f"run_ui_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    args = [
        "--dataset-root", str(dataset_root),
        "--output-root", str(output_root),
        "--run-dir", str(run_dir),
        "--device", device,
        "--epochs", str(params["epochs"]),
        "--patience", str(params["patience"]),
        "--min-delta", str(params["min_delta"]),
        "--backbone-lr", str(params["backbone_lr"]),
        "--head-lr", str(params["head_lr"]),
        "--weight-decay", str(params["weight_decay"]),
        "--grad-accum", str(params["grad_accum"]),
        "--window-batch-size", str(params["window_batch_size"]),
        "--seed", str(params["seed"]),
        "--pause-file", str(pause_file),
    ]
    if bool(payload.get("allow_cpu", False)):
        args.append("--allow-cpu")
    if not bool(payload.get("amp", True)):
        args.append("--no-amp")
    cache_dir = str(payload.get("cache_dir") or "").strip()
    if cache_dir:
        args.extend(["--cache-dir", str(Path(cache_dir).expanduser().resolve())])
    return args, run_dir, str(dataset_root)


def _count_videos_under(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file() and item.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv"})


def _legacy_dataset_inventory(root: Path) -> dict[str, Any]:
    datasets = []
    all_directories_exist = True
    for name, relative_root in REVERSE_LEGACY_DATASET_LAYOUT:
        dataset_root = root / relative_root
        splits: dict[str, dict[str, int]] = {}
        missing: list[str] = []
        for split in ("train", "val", "test"):
            splits[split] = {}
            for class_name in ("Fight", "NonFight"):
                class_dir = dataset_root / split / class_name
                if not class_dir.is_dir():
                    missing.append(str(class_dir))
                    all_directories_exist = False
                    splits[split][class_name] = 0
                    continue
                splits[split][class_name] = sum(
                    1 for path in class_dir.iterdir()
                    if path.is_file() and path.suffix.lower() in {".avi", ".mp4"}
                )
        total = sum(count for values in splits.values() for count in values.values())
        datasets.append({"name": name, "root": str(dataset_root), "total": total, "splits": splits, "missing": missing})
    return {
        "root": str(root),
        "datasets": datasets,
        "total": sum(item["total"] for item in datasets),
        "complete": all_directories_exist,
        "populated": all(item["total"] > 0 for item in datasets),
    }


def _reverse_dataset_summary(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    ucf_root = Path(str(payload.get("ucf_root") or DEFAULT_REVERSE_UCF_ROOT)).expanduser().resolve()
    legacy_root = Path(str(payload.get("legacy_dataset_root") or DEFAULT_REVERSE_LEGACY_ROOT)).expanduser().resolve()
    airtlab_root = Path(str(payload.get("airtlab_root") or DEFAULT_REVERSE_AIRTLAB_ROOT)).expanduser().resolve()
    guardaid_root = Path(str(payload.get("guardaid_root") or DEFAULT_REVERSE_GUARDAID_ROOT)).expanduser().resolve()
    output_root = Path(str(payload.get("output_root") or DEFAULT_REVERSE_OUTPUT_ROOT)).expanduser().resolve()
    legacy = _legacy_dataset_inventory(legacy_root)
    fight_count = _count_videos_under(ucf_root / "Violence")
    assault_count = _count_videos_under(ucf_root / "Violence" / "Assault")
    fighting_count = _count_videos_under(ucf_root / "Violence" / "Fighting")
    nonfight_folder_count = _count_videos_under(ucf_root / "Non-Violence")
    archives = []
    for archive in sorted(ucf_root.glob("Training-Normal-Videos-Part-*.zip")) if ucf_root.is_dir() else []:
        try:
            with zipfile.ZipFile(archive) as zf:
                infos = [info for info in zf.infolist() if not info.is_dir() and Path(info.filename).suffix.lower() in {".mp4", ".avi", ".mov", ".mkv"}]
            archives.append({
                "name": archive.name,
                "path": str(archive),
                "videos": len(infos),
                "uncompressed_bytes": sum(info.file_size for info in infos),
                "largest_file_bytes": max((info.file_size for info in infos), default=0),
                "error": None,
            })
        except (OSError, zipfile.BadZipFile) as exc:
            archives.append({"name": archive.name, "path": str(archive), "videos": 0, "error": str(exc)})
    zip_negative_count = sum(int(item.get("videos", 0)) for item in archives)
    nonfight_count = nonfight_folder_count + zip_negative_count
    class_root = airtlab_root
    if not ((class_root / "violent").is_dir() and (class_root / "non-violent").is_dir()):
        nested = class_root / "violence-detection-dataset"
        if (nested / "violent").is_dir() and (nested / "non-violent").is_dir():
            class_root = nested
    airt_fight = _count_videos_under(class_root / "violent")
    airt_nonfight = _count_videos_under(class_root / "non-violent")
    guardaid_count = _count_videos_under(guardaid_root)
    try:
        available = shutil.disk_usage(output_root if output_root.exists() else output_root.parent).free
    except OSError:
        available = None

    def split_counts(count: int) -> dict[str, int]:
        train = int(count * 0.8 + 0.5)
        val = int(count * 0.1 + 0.5)
        val = min(val, max(0, count - train))
        return {"train": train, "val": val, "test": count - train - val}

    fight_splits = split_counts(fight_count)
    nonfight_splits = split_counts(nonfight_count)
    normal_uncompressed = sum(int(item.get("uncompressed_bytes", 0)) for item in archives)
    max_zip_video = max((int(item.get("largest_file_bytes", 0)) for item in archives), default=0)
    return {
        "protocol": "mc3-2s-1s-stride-8f-128short-112crop-reverse-concat-v2",
        "roots": {"legacy": str(legacy_root), "ucf": str(ucf_root), "airtlab": str(class_root), "guardaid": str(guardaid_root), "output": str(output_root)},
        "legacy": legacy,
        "ucf": {
            "fight_folder_videos": fight_count,
            "assault_videos": assault_count,
            "fighting_videos": fighting_count,
            "nonfight_folder_videos": nonfight_folder_count,
            "normal_archives": archives,
            "normal_archive_videos": zip_negative_count,
            "raw_fight": fight_count,
            "raw_nonfight_including_archive": nonfight_count,
            "raw_total_before_dedup_and_duration_filter": fight_count + nonfight_count,
            "split_preview": {split: {"Fight": fight_splits[split], "NonFight": nonfight_splits[split], "total": fight_splits[split] + nonfight_splits[split]} for split in ("train", "val", "test")},
        },
        "airtlab": {"fight": airt_fight, "nonfight": airt_nonfight, "total": airt_fight + airt_nonfight, "role": "最终有标签留出测试集"},
        "guardaid": {"videos": guardaid_count, "has_ground_truth_labels": False, "role": "最终无标签留出集；单列预测和假设全为 NonFight 时的条件性误报率"},
        "storage": {
            "free_bytes": available,
            "free_gib": round(available / (1024 ** 3), 2) if available is not None else None,
            "normal_archive_uncompressed_gib": round(normal_uncompressed / (1024 ** 3), 2),
            "largest_archive_video_gib": round(max_zip_video / (1024 ** 3), 2),
            "archive_handling": "压缩包视频逐条临时解压用于读取；不会由程序永久解压整包。若你自行解压到 Non-Violence，程序会优先读取文件夹并按内容去重。",
        },
        "ready": bool(legacy["complete"] and legacy["populated"] and fight_count and nonfight_count and airt_fight and airt_nonfight and guardaid_count),
        "notes": [
            "五个原有数据集保留各自 train/val/test 文件夹中的 split，不重新划分；所有训练 split 的 1–2 秒样本接原片倒放。",
            "UCF 以视频来源组为单位，按类别和暴力子类别分层，再按 80/10/10 划分；同组及完全重复视频不会跨 split。",
            "训练集 1–2 秒样本采用原片接倒放；验证/测试保持自然视频，少于 2 秒的评估样本会列入排除清单。",
            "所有 AIRTLab 与 GuardAId 样本都固定为外部测试集；AIRTLab 的 cam1/cam2 可能是相同行为的双视角。",
            "GuardAId 目前没有 ground-truth 标签，所以不会混入有标签总成绩。",
            "页面里的划分数量是源文件数预览；最终 manifest 会显示去重、时长过滤后的准确数量。",
        ],
    }


def _four_second_airtlab_split_preview(class_root: Path) -> dict[str, Any]:
    """Mirror the four-second runner's filename-group split for the UI preview."""
    suffixes = {".mp4", ".avi", ".mov", ".mkv"}
    group_files: dict[str, dict[str, int]] = {}
    group_labels: dict[str, set[str]] = {}
    for directory_name, class_name in (("violent", "Fight"), ("non-violent", "NonFight")):
        directory = class_root / directory_name
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            stem = path.stem.casefold()
            for suffix in ("_x264", "_264", "_copy", "_trimmed"):
                if stem.endswith(suffix):
                    stem = stem[:-len(suffix)]
            stem = "".join(character for character in stem if character.isalnum())
            if not stem:
                stem = path.name.casefold()
            group = f"{class_name.casefold()}|{stem}"
            group_files.setdefault(group, {}).setdefault(class_name, 0)
            group_files[group][class_name] += 1
            group_labels.setdefault(group, set()).add(class_name)

    strata: dict[str, list[str]] = {}
    for group in group_files:
        signature = "|".join(sorted(group_labels[group]))
        strata.setdefault(signature, []).append(group)

    assignment: dict[str, str] = {}
    for signature, groups in strata.items():
        seed = 2026 + sum(signature.encode("utf-8"))
        ordered = sorted(groups, key=lambda value: hashlib.sha256(f"{seed}|airtlab|{value.split('|', 1)[0]}|sourcevideo|{value.split('|', 1)[1]}".encode("utf-8")).digest())
        count = len(ordered)
        train_count = int(math.floor(count * 0.80 + 0.5))
        val_count = int(math.floor(count * 0.10 + 0.5))
        while train_count + val_count > count:
            val_count -= 1
        for index, group in enumerate(ordered):
            assignment[group] = "train" if index < train_count else "val" if index < train_count + val_count else "test"

    result = {
        split: {"Fight": 0, "NonFight": 0, "groups": 0, "total": 0}
        for split in ("train", "val", "test")
    }
    class_group_totals = {"Fight": 0, "NonFight": 0}
    paired_groups = 0
    for group, counts in group_files.items():
        split_row = result[assignment[group]]
        split_row["groups"] += 1
        paired_groups += int(sum(counts.values()) > 1)
        for class_name, count in counts.items():
            split_row[class_name] += count
            split_row["total"] += count
            class_group_totals[class_name] += 1
    return {
        "split_preview": result,
        "source_groups": len(group_files),
        "fight_source_groups": class_group_totals["Fight"],
        "nonfight_source_groups": class_group_totals["NonFight"],
        "paired_filename_groups": paired_groups,
    }


def _four_second_dataset_summary(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Summarize only the sources included in the four-second protocol."""
    payload = payload or {}
    ucf_root = Path(str(payload.get("ucf_root") or DEFAULT_REVERSE_UCF_ROOT)).expanduser().resolve()
    legacy_root = Path(str(payload.get("legacy_dataset_root") or DEFAULT_REVERSE_LEGACY_ROOT)).expanduser().resolve()
    airtlab_root = Path(str(payload.get("airtlab_root") or DEFAULT_REVERSE_AIRTLAB_ROOT)).expanduser().resolve()
    guardaid_root = Path(str(payload.get("guardaid_root") or DEFAULT_REVERSE_GUARDAID_ROOT)).expanduser().resolve()
    output_root = Path(str(payload.get("output_root") or DEFAULT_FOUR_SECOND_OUTPUT_ROOT)).expanduser().resolve()
    layout = (
        ("RWF-2000", Path("RWF-2000")),
        ("Real Life Violence Dataset", Path("archive") / "Real Life Violence Dataset"),
        ("ShanghaiTech", Path("ShanghaiTech")),
    )
    legacy_rows: list[dict[str, Any]] = []
    all_directories_exist = True
    for name, relative in layout:
        dataset_root = legacy_root / relative
        splits: dict[str, dict[str, int]] = {}
        missing: list[str] = []
        for split in ("train", "val", "test"):
            splits[split] = {}
            for class_name in ("Fight", "NonFight"):
                class_dir = dataset_root / split / class_name
                if not class_dir.is_dir():
                    missing.append(str(class_dir))
                    all_directories_exist = False
                    splits[split][class_name] = 0
                else:
                    splits[split][class_name] = sum(
                        1 for item in class_dir.iterdir()
                        if item.is_file() and item.suffix.lower() in {".avi", ".mp4"}
                    )
        legacy_rows.append({
            "name": name,
            "root": str(dataset_root),
            "total": sum(amount for split_values in splits.values() for amount in split_values.values()),
            "splits": splits,
            "missing": missing,
        })
    legacy = {
        "root": str(legacy_root),
        "datasets": legacy_rows,
        "total": sum(row["total"] for row in legacy_rows),
        "complete": all_directories_exist,
        "populated": all(row["total"] > 0 for row in legacy_rows),
    }

    fight_count = _count_videos_under(ucf_root / "Violence")
    assault_count = _count_videos_under(ucf_root / "Violence" / "Assault")
    fighting_count = _count_videos_under(ucf_root / "Violence" / "Fighting")
    nonfight_folder_count = _count_videos_under(ucf_root / "Non-Violence")
    archives: list[dict[str, Any]] = []
    for archive in sorted(ucf_root.glob("Training-Normal-Videos-Part-*.zip")) if ucf_root.is_dir() else []:
        try:
            with zipfile.ZipFile(archive) as zf:
                infos = [info for info in zf.infolist() if not info.is_dir() and Path(info.filename).suffix.lower() in {".mp4", ".avi", ".mov", ".mkv"}]
            archives.append({"name": archive.name, "path": str(archive), "videos": len(infos),
                             "uncompressed_bytes": sum(info.file_size for info in infos), "error": None})
        except (OSError, zipfile.BadZipFile) as exc:
            archives.append({"name": archive.name, "path": str(archive), "videos": 0, "error": str(exc)})
    zipped_normals = sum(int(row.get("videos", 0)) for row in archives)
    nonfight_count = nonfight_folder_count + zipped_normals

    def split_counts(total: int) -> dict[str, int]:
        train = int(total * 0.80 + 0.5)
        validation = int(total * 0.10 + 0.5)
        validation = min(validation, max(0, total - train))
        return {"train": train, "val": validation, "test": total - train - validation}

    fight_split = split_counts(fight_count)
    nonfight_split = split_counts(nonfight_count)
    class_root = airtlab_root
    if not ((class_root / "violent").is_dir() and (class_root / "non-violent").is_dir()):
        nested = class_root / "violence-detection-dataset"
        if (nested / "violent").is_dir() and (nested / "non-violent").is_dir():
            class_root = nested
    airt_fight = _count_videos_under(class_root / "violent")
    airt_nonfight = _count_videos_under(class_root / "non-violent")
    air_split_preview = _four_second_airtlab_split_preview(class_root)
    guardaid_count = _count_videos_under(guardaid_root)
    supplement_root = Path(str(payload.get("cache_dir") or DEFAULT_FOUR_SECOND_SUPPLEMENT_CACHE)).expanduser().resolve()
    cache_bytes = sum(path.stat().st_size for path in DEFAULT_TWO_SECOND_FRAME_CACHE.glob("*.npz") if path.is_file()) if DEFAULT_TWO_SECOND_FRAME_CACHE.is_dir() else 0
    supplement_bytes = sum(path.stat().st_size for path in supplement_root.glob("*.npz") if path.is_file()) if supplement_root.is_dir() else 0
    cache_roots_disjoint = (
        DEFAULT_TWO_SECOND_FRAME_CACHE.resolve() != supplement_root
        and DEFAULT_TWO_SECOND_FRAME_CACHE.resolve() not in supplement_root.parents
        and supplement_root not in DEFAULT_TWO_SECOND_FRAME_CACHE.resolve().parents
    )
    try:
        free_bytes = shutil.disk_usage(output_root if output_root.exists() else output_root.parent).free
    except OSError:
        free_bytes = None
    selected_ready = legacy["complete"] and legacy["populated"]
    return {
        "protocol": "mc3-4s-2s-stride-16f-128short-112crop-short-tail-v1",
        "roots": {"legacy": str(legacy_root), "ucf": str(ucf_root), "airtlab": str(class_root),
                  "guardaid": str(guardaid_root), "output": str(output_root)},
        "legacy": legacy,
        "ucf": {"fight_folder_videos": fight_count, "assault_videos": assault_count, "fighting_videos": fighting_count,
                "nonfight_folder_videos": nonfight_folder_count, "normal_archives": archives,
                "normal_archive_videos": zipped_normals, "raw_fight": fight_count, "raw_nonfight_including_archive": nonfight_count,
                "raw_total_before_dedup_and_duration_filter": fight_count + nonfight_count,
                "split_preview": {split: {"Fight": fight_split[split], "NonFight": nonfight_split[split],
                                           "total": fight_split[split] + nonfight_split[split]}
                                  for split in ("train", "val", "test")}},
        "airtlab": {"fight": airt_fight, "nonfight": airt_nonfight, "total": airt_fight + airt_nonfight,
                    **air_split_preview, "role": "有标签数据；按视频名组划分 80/10/10"},
        "guardaid": {"videos": guardaid_count, "has_ground_truth_labels": False,
                     "role": "独立额外无标签测试；不参与 train/val/test"},
        "cache": {
            "legacy_read_only": str(DEFAULT_TWO_SECOND_FRAME_CACHE),
            "legacy_bytes": cache_bytes,
            "legacy_gib": round(cache_bytes / (1024**3), 2),
            "supplemental": str(supplement_root),
            "supplemental_bytes": supplement_bytes,
            "supplemental_limit_bytes": 5 * 1024**3,
            "supplemental_gib": round(supplement_bytes / (1024**3), 2),
            "ready": DEFAULT_TWO_SECOND_FRAME_CACHE.is_dir() and cache_roots_disjoint and supplement_bytes <= 5 * 1024**3,
            "roots_disjoint": cache_roots_disjoint,
        },
        "storage": {"free_bytes": free_bytes, "free_gib": round(free_bytes / (1024**3), 2) if free_bytes is not None else None},
        "ready": bool(selected_ready and (fight_count > 0) and (nonfight_count > 0) and airt_fight and airt_nonfight and guardaid_count and DEFAULT_TWO_SECOND_FRAME_CACHE.is_dir() and cache_roots_disjoint and supplement_bytes <= 5 * 1024**3),
        "notes": [
            "只使用 RWF-2000、Real Life Violence Dataset、ShanghaiTech 与 UCF；bus-violence 和 Seymanurakti 不纳入。",
            "三个原有数据集沿用原 train/val/test；UCF 和 AIRTLab 按来源组进行 80/10/10 划分。",
            "AIRTLab 的同名 cam1/cam2 视频固定进入同一 split；GuardAId 独立于 train/val/test，在最终评估后另作无标签额外测试。",
            "Real Life Violence Dataset 中短于 4 秒的视频排除；其他至少 1 秒的短视频用真实片段均匀采样，不倒放、不补黑帧。",
            "长视频使用 4 秒窗口、2 秒步长；最后一个未覆盖尾段会增加一个末尾对齐窗口。整视频分数取窗口最高 Fight 概率。",
            "现有两秒缓存只读复用；新缺帧放到独立补充缓存，最多 5 GiB；写满后按需解码，不会复制旧缓存。",
            "缓存命中数量及最终按需解码帧数以每次训练目录中的 run.log 为准。",
        ],
    }


def _four_second_training_arguments(payload: dict[str, Any], output_root: Path, pause_file: Path) -> tuple[list[str], Path, dict[str, str]]:
    ucf_root = Path(str(payload.get("ucf_root") or DEFAULT_REVERSE_UCF_ROOT)).expanduser().resolve()
    legacy_root = Path(str(payload.get("legacy_dataset_root") or DEFAULT_REVERSE_LEGACY_ROOT)).expanduser().resolve()
    airtlab_root = Path(str(payload.get("airtlab_root") or DEFAULT_REVERSE_AIRTLAB_ROOT)).expanduser().resolve()
    guardaid_root = Path(str(payload.get("guardaid_root") or DEFAULT_REVERSE_GUARDAID_ROOT)).expanduser().resolve()
    supplement_root = Path(str(payload.get("cache_dir") or DEFAULT_FOUR_SECOND_SUPPLEMENT_CACHE)).expanduser().resolve()
    for name, relative in (("RWF-2000", Path("RWF-2000")),
                           ("Real Life Violence Dataset", Path("archive") / "Real Life Violence Dataset"),
                           ("ShanghaiTech", Path("ShanghaiTech"))):
        dataset_root = legacy_root / relative
        for split in ("train", "val", "test"):
            for class_name in ("Fight", "NonFight"):
                class_dir = dataset_root / split / class_name
                if not class_dir.is_dir():
                    raise ValueError(f"{name} 缺少目录：{class_dir}")
    if not (ucf_root / "Violence").is_dir():
        raise ValueError(f"找不到 UCF Violence 文件夹：{ucf_root / 'Violence'}")
    if not (ucf_root / "Non-Violence").is_dir() and not list(ucf_root.glob("Training-Normal-Videos-Part-*.zip")):
        raise ValueError("UCF Non-Violence 文件夹与正常视频压缩包都不存在。")
    if not airtlab_root.is_dir() or not guardaid_root.is_dir():
        raise ValueError("AIRTLab 模型数据路径或 GuardAId 独立额外测试路径不存在。")
    summary = _four_second_dataset_summary({
        "legacy_dataset_root": str(legacy_root), "ucf_root": str(ucf_root), "airtlab_root": str(airtlab_root),
        "guardaid_root": str(guardaid_root), "output_root": str(output_root), "cache_dir": str(supplement_root),
    })
    if not summary["cache"]["ready"]:
        raise ValueError("补充缓存路径必须与只读两秒缓存分开，且现有补充缓存不能超过 5 GiB。")
    if not summary["ready"]:
        raise ValueError("四秒方案的数据目录、GuardAId 独立额外测试集或现有两秒帧缓存尚未准备完整。请先修正页面显示的路径。")
    params = {
        "epochs": _read_form_number(payload, "epochs", 30, integer=True),
        "patience": _read_form_number(payload, "patience", 6, integer=True),
        "min_delta": _read_form_number(payload, "min_delta", 0.001),
        "backbone_lr": _read_form_number(payload, "backbone_lr", 2e-5),
        "head_lr": _read_form_number(payload, "head_lr", 1e-4),
        "weight_decay": _read_form_number(payload, "weight_decay", 1e-4),
        "grad_accum": _read_form_number(payload, "grad_accum", 8, integer=True),
        "window_batch_size": _read_form_number(payload, "window_batch_size", 2, integer=True),
        "seed": _read_form_number(payload, "seed", 2026, integer=True),
    }
    if any(params[key] < 1 for key in ("epochs", "patience", "grad_accum", "window_batch_size")):
        raise ValueError("Epoch、早停、梯度累积和窗口批次必须是正整数。")
    if params["backbone_lr"] <= 0 or params["head_lr"] <= 0 or params["weight_decay"] < 0 or params["min_delta"] < 0:
        raise ValueError("学习率必须为正数；weight decay 与最小提升不能小于零。")
    device = str(payload.get("device", "auto"))
    if device not in {"auto", "cuda", "cpu"}:
        raise ValueError("计算设备只能是 auto、cuda 或 cpu。")
    resume_checkpoint = str(payload.get("resume_checkpoint") or "").strip()
    if resume_checkpoint:
        checkpoint = _require_child_path(resume_checkpoint, str(output_root))
        if checkpoint.name != "last.pt" or not checkpoint.is_file():
            raise ValueError("只能从四秒方案输出目录中的 last.pt 恢复。")
        run_dir = checkpoint.parent
    else:
        run_dir = output_root / f"run_four_ui_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    output_root.mkdir(parents=True, exist_ok=True)
    supplement_cache = supplement_root
    args = [
        "--ucf-root", str(ucf_root), "--legacy-dataset-root", str(legacy_root),
        "--airtlab-root", str(airtlab_root), "--guardaid-root", str(guardaid_root),
        "--output-root", str(output_root), "--run-dir", str(run_dir), "--device", device,
        "--epochs", str(params["epochs"]), "--patience", str(params["patience"]), "--min-delta", str(params["min_delta"]),
        "--backbone-lr", str(params["backbone_lr"]), "--head-lr", str(params["head_lr"]),
        "--weight-decay", str(params["weight_decay"]), "--grad-accum", str(params["grad_accum"]),
        "--window-batch-size", str(params["window_batch_size"]), "--seed", str(params["seed"]),
        "--pause-file", str(pause_file), "--cache-dir", str(supplement_cache),
        "--legacy-cache-dir", str(DEFAULT_TWO_SECOND_FRAME_CACHE),
    ]
    if resume_checkpoint:
        args.extend(["--resume", resume_checkpoint])
    if bool(payload.get("allow_cpu", False)):
        args.append("--allow-cpu")
    if not bool(payload.get("amp", True)):
        args.append("--no-amp")
    return args, run_dir, {
        "legacy_dataset_root": str(legacy_root), "ucf_root": str(ucf_root),
        "airtlab_root": str(airtlab_root), "guardaid_root": str(guardaid_root),
        "output_root": str(output_root), "legacy_cache_root": str(DEFAULT_TWO_SECOND_FRAME_CACHE),
        "supplemental_cache_root": str(supplement_cache),
    }


def _start_four_second_training(payload: dict[str, Any]) -> dict[str, Any]:
    output_root = Path(str(payload.get("output_root") or DEFAULT_FOUR_SECOND_OUTPUT_ROOT)).expanduser().resolve()
    control_root = output_root / ".dashboard_control"
    control_root.mkdir(parents=True, exist_ok=True)
    pause_file = control_root / f"{uuid.uuid4().hex}.pause"
    args, run_dir, roots = _four_second_training_arguments(payload, output_root, pause_file)
    with STATE.lock:
        ordinary_active = STATE.train_process is not None and STATE.train_process.poll() is None
        inference_active = STATE.inference.get("status") == "running"
    with REVERSE_STATE.lock:
        if REVERSE_STATE.process is not None and REVERSE_STATE.process.poll() is None:
            raise RuntimeError("已有 2 秒或 4 秒训练正在运行。")
        if ordinary_active:
            raise RuntimeError("原 1 秒训练正在运行；请先等它完成或暂停。")
        if _mc3_training_active(except_name="reverse_four_second"):
            raise RuntimeError("已有其他 MC3-18 训练正在运行；请先暂停或等待其结束。")
        if inference_active:
            raise RuntimeError("视频推理正在占用本机资源；请先等推理完成。")
        REVERSE_STATE.status = "starting"
        REVERSE_STATE.run_dir = run_dir
        REVERSE_STATE.output_root = output_root
        REVERSE_STATE.pause_file = pause_file
        REVERSE_STATE.pause_requested = False
        REVERSE_STATE.stop_requested = False
        REVERSE_STATE.started_at = time.time()
        REVERSE_STATE.console.clear()
    command = [sys.executable, "-u", str(FOUR_SECOND_TRAINING_SCRIPT), *args]
    try:
        process = subprocess.Popen(
            command, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        with REVERSE_STATE.lock:
            REVERSE_STATE.status = "failed"
        raise RuntimeError(f"无法启动四秒训练进程：{exc}") from exc
    with REVERSE_STATE.lock:
        REVERSE_STATE.process = process
        REVERSE_STATE.command = command
        REVERSE_STATE.status = "running"
    threading.Thread(target=_drain_reverse_stdout, args=(process,), daemon=True, name="mc3-four-second-train-stdout").start()
    return {"status": "running", "run_dir": str(run_dir), "roots": roots, "resuming": "--resume" in args,
            "command": subprocess.list2cmdline(command)}


def _four_second_status_payload(output_root_value: str | None = None, run_dir_value: str | None = None) -> dict[str, Any]:
    output_root = Path(output_root_value or DEFAULT_FOUR_SECOND_OUTPUT_ROOT).expanduser().resolve()
    selected = run_dir_value
    if not selected:
        with REVERSE_STATE.lock:
            active = REVERSE_STATE.run_dir
            active_process = REVERSE_STATE.process
        if active is not None and active_process is not None and active_process.poll() is None and active.is_relative_to(output_root):
            selected = str(active)
        else:
            runs = [path for path in output_root.iterdir() if path.is_dir() and (path / "run.log").is_file()] if output_root.is_dir() else []
            if runs:
                selected = str(max(runs, key=lambda path: path.stat().st_mtime))
    if not selected:
        with REVERSE_STATE.lock:
            active_status = REVERSE_STATE.status
            process = REVERSE_STATE.process
        return {"status": "running" if process is not None and process.poll() is None else "idle",
                "run_dir": None, "last_checkpoint": None, "console": [], "history": [],
                "validation_by_dataset": [], "final_report": {}, "protocol": "four_second", "stage_progress": 0.0}
    result = _reverse_status_payload(str(output_root), selected)
    result["protocol"] = "four_second"
    manifest = _read_csv(Path(result["run_dir"]) / "dataset_manifest.csv") if result.get("run_dir") else []
    model_rows = [row for row in manifest if row.get("split") in {"train", "val", "test"}]
    external_rows = [row for row in manifest if row.get("dataset") == "GuardAId" and row.get("split") == "external_test"]
    result["sample_total"] = sum(row.get("eligible") == "1" for row in model_rows)
    result["excluded_total"] = sum(row.get("eligible") != "1" for row in model_rows)
    result["guardaid_unlabeled_sample_total"] = sum(row.get("eligible") == "1" for row in external_rows)
    result["short_tail_sample_total"] = sum(row.get("short_tail_window_used") == "1" and row.get("eligible") == "1" for row in manifest)
    config = result.get("config") or {}
    result["supplemental_cache_bytes"] = sum(
        path.stat().st_size for path in Path(str(config.get("cache_dir", DEFAULT_FOUR_SECOND_SUPPLEMENT_CACHE))).glob("*.npz") if path.is_file()
    )
    return result


def _four_second_pause() -> dict[str, Any]:
    result = _reverse_pause()
    result["message"] = "当前 epoch 的验证与 checkpoint 保存完成后会安全暂停。"
    return result


def _four_second_cancel_pause() -> dict[str, Any]:
    return _reverse_cancel_pause()


def _four_second_stop() -> dict[str, Any]:
    result = _reverse_stop()
    result["message"] = "四秒方案已立即停止；可以从最近完整 epoch 的 last.pt 恢复。"
    return result


def _skeleton_dataset_summary() -> dict[str, Any]:
    import mc3_18_skeleton_four_second_pipeline as skeleton

    samples, metadata = skeleton.load_samples()
    summary = skeleton.split_summary(samples)
    video_rows = skeleton._split_manifest(samples)
    window_rows = skeleton._window_manifest(samples)
    return {
        "ready": True,
        "protocol": skeleton.PROTOCOL_ID,
        "skeleton_root": str(skeleton.DEFAULT_SKELETON_ROOT.resolve()),
        "airtlab_skeleton_root": str(skeleton.DEFAULT_AIRTLAB_ROOT.resolve()),
        "output_root": str(DEFAULT_SKELETON_OUTPUT_ROOT.resolve()),
        "split_policy": metadata["split_policy"],
        "split_ratios": skeleton.SPLIT_RATIOS,
        "serialization_preflight": {"video_rows": len(video_rows), "window_rows": len(window_rows)},
        "summary": summary,
    }


def _skeleton_training_arguments(payload: dict[str, Any], pause_file: Path) -> tuple[list[str], Path]:
    output_root = DEFAULT_SKELETON_OUTPUT_ROOT.resolve()
    resume_value = str(payload.get("resume_checkpoint") or "").strip()
    saved_config: dict[str, Any] | None = None
    if resume_value:
        checkpoint = _require_child_path(resume_value, str(output_root))
        if checkpoint.name != "last.pt" or not checkpoint.is_file():
            raise ValueError("只能从黑底骨架训练输出目录中的 last.pt 恢复。")
        if (checkpoint.parent / "final_report.json").is_file():
            raise ValueError("这次黑底骨架训练已经完成最终测试，不能再恢复；请开始新的运行。")
        saved_config = _read_json(checkpoint.parent / "config.json")
        required_saved = {
            "epochs", "patience", "min_delta", "backbone_lr", "head_lr", "weight_decay",
            "grad_accum", "window_batch_size", "seed", "lr_scheduler_factor",
            "lr_scheduler_no_improvement_epochs", "min_lr", "amp", "device",
        }
        if saved_config is None or required_saved.difference(saved_config):
            raise ValueError("该 last.pt 缺少完整超参数配置，不能安全恢复；请新建训练运行。")
        run_dir = checkpoint.parent
    else:
        run_dir = output_root / f"run_skeleton_ui_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    integer_keys = {"epochs", "patience", "grad_accum", "window_batch_size", "seed", "lr_reduce_after"}
    saved_key_map = {
        "lr_reduce_after": "lr_scheduler_no_improvement_epochs",
        "lr_factor": "lr_scheduler_factor",
    }
    params: dict[str, int | float] = {}
    for key, default in SKELETON_DEFAULT_TRAIN_CONFIG.items():
        if saved_config is None:
            value = _read_form_number(payload, key, default, integer=key in integer_keys)
        else:
            value = saved_config[saved_key_map.get(key, key)]
            value = int(value) if key in integer_keys else float(value)
        params[key] = value
    if not 1 <= params["epochs"] <= 300 or not 1 <= params["patience"] <= 300:
        raise ValueError("最大 epochs 和早停 patience 必须介于 1 到 300。")
    if not 1 <= params["lr_reduce_after"] <= 300:
        raise ValueError("学习率降低等待轮数必须介于 1 到 300。")
    if not 1 <= params["grad_accum"] <= 128 or not 1 <= params["window_batch_size"] <= 8:
        raise ValueError("Gradient accumulation 需为 1–128，window batch 需为 1–8。")
    if not 0 <= params["seed"] <= 2**32 - 1:
        raise ValueError("Seed 必须介于 0 到 2^32−1。")
    float_keys = ("min_delta", "backbone_lr", "head_lr", "weight_decay", "lr_factor", "min_lr")
    if not all(math.isfinite(float(params[key])) for key in float_keys):
        raise ValueError("超参数必须是有限数值。")
    if (not 0 <= params["min_delta"] <= 1 or not 0 < params["backbone_lr"] <= 1
            or not 0 < params["head_lr"] <= 1):
        raise ValueError("min_delta 必须为 0–1，两个初始学习率必须大于 0 且不超过 1。")
    if not 0 <= params["weight_decay"] <= 1:
        raise ValueError("Weight decay 必须介于 0 到 1。")
    if not 0 < params["lr_factor"] < 1 or not 0 <= params["min_lr"] <= min(params["backbone_lr"], params["head_lr"]):
        raise ValueError("LR factor 必须介于 0 和 1 之间；minimum LR 不能大于初始学习率。")
    device = str(payload.get("device", "auto"))
    amp = bool(payload.get("amp", True))
    if saved_config is not None:
        saved_device = str(saved_config.get("device", "auto"))
        device = "cpu" if saved_device.startswith("cpu") else "cuda" if saved_device.startswith("cuda") else "auto"
        amp = bool(saved_config["amp"])
    if device not in {"auto", "cuda", "cpu"}:
        raise ValueError("计算设备只能是 auto、cuda 或 cpu。")
    allow_cpu = device == "cpu" if saved_config is not None else bool(payload.get("allow_cpu", False))
    if device == "cpu" and not allow_cpu:
        raise ValueError("CPU 训练较慢；若要使用 CPU，请明确勾选确认。")
    output_root.mkdir(parents=True, exist_ok=True)
    args = [
        "--skeleton-root", str(DEFAULT_DATASET_ROOT / "RTMPose_M_Skeleton_MC3_4s2s_16frames"),
        "--airtlab-skeleton-root", str(DEFAULT_DATASET_ROOT / "AIRTLab" / "RTMPose_M_Skeleton_MC3_4s2s_16frames"),
        "--output-root", str(output_root), "--run-dir", str(run_dir),
        "--cache-dir", str(output_root / "_frame_cache" / SKELETON_EXPERIMENT_PROTOCOL),
        "--pause-file", str(pause_file), "--device", device,
        "--epochs", str(params["epochs"]), "--patience", str(params["patience"]),
        "--min-delta", str(params["min_delta"]),
        "--backbone-lr", str(params["backbone_lr"]), "--head-lr", str(params["head_lr"]),
        "--weight-decay", str(params["weight_decay"]),
        "--lr-reduce-after", str(params["lr_reduce_after"]), "--lr-factor", str(params["lr_factor"]),
        "--min-lr", str(params["min_lr"]), "--grad-accum", str(params["grad_accum"]),
        "--window-batch-size", str(params["window_batch_size"]), "--seed", str(params["seed"]),
    ]
    if resume_value:
        args.extend(["--resume", resume_value])
    if allow_cpu:
        args.append("--allow-cpu")
    if not amp:
        args.append("--no-amp")
    return args, run_dir


def _drain_skeleton_stdout(process: subprocess.Popen[str]) -> None:
    if process.stdout is not None:
        for line in process.stdout:
            clean = line.rstrip("\r\n")
            with SKELETON_STATE.lock:
                SKELETON_STATE.console.append(clean)
    return_code = process.wait()
    with SKELETON_STATE.lock:
        if SKELETON_STATE.process is process:
            if SKELETON_STATE.stop_requested:
                SKELETON_STATE.status = "stopped"
            elif SKELETON_STATE.run_dir and (SKELETON_STATE.run_dir / "final_report.json").is_file():
                SKELETON_STATE.status = "completed" if return_code == 0 else "failed"
            elif SKELETON_STATE.run_dir and (SKELETON_STATE.run_dir / "last.pt").is_file() and return_code == 0:
                SKELETON_STATE.status = "paused"
            else:
                SKELETON_STATE.status = "failed"


def _start_skeleton_training(payload: dict[str, Any]) -> dict[str, Any]:
    # Fail fast before creating a run if any manifest cannot be serialized in
    # the training pipeline's shared video/window schema.
    _skeleton_dataset_summary()
    output_root = DEFAULT_SKELETON_OUTPUT_ROOT.resolve()
    control_root = output_root / ".dashboard_control"
    control_root.mkdir(parents=True, exist_ok=True)
    pause_file = control_root / f"skeleton_{uuid.uuid4().hex}.pause"
    args, run_dir = _skeleton_training_arguments(payload, pause_file)
    with STATE.lock:
        ordinary_active = STATE.train_process is not None and STATE.train_process.poll() is None
        inference_active = STATE.inference.get("status") in {"running", "queued"}
    with REVERSE_STATE.lock:
        reverse_active = REVERSE_STATE.process is not None and REVERSE_STATE.process.poll() is None
    with SKELETON_STATE.lock:
        if SKELETON_STATE.process is not None and SKELETON_STATE.process.poll() is None:
            raise RuntimeError("黑底骨架训练已经在运行。")
        if ordinary_active or reverse_active or _mc3_training_active(except_name="skeleton"):
            raise RuntimeError("已有其他 MC3-18 训练正在运行；请先暂停或等待该训练完成。")
        if inference_active:
            raise RuntimeError("视频推理正在占用本机资源；请先等推理完成。")
        # Make the run visible to status polling immediately, including while
        # the child performs its read-only data preflight.
        run_dir.mkdir(parents=True, exist_ok=True)
        SKELETON_STATE.status = "starting"
        SKELETON_STATE.run_dir = run_dir
        SKELETON_STATE.output_root = output_root
        SKELETON_STATE.pause_file = pause_file
        SKELETON_STATE.pause_requested = False
        SKELETON_STATE.stop_requested = False
        SKELETON_STATE.started_at = time.time()
        SKELETON_STATE.console.clear()
    command = [sys.executable, "-u", str(SKELETON_TRAINING_SCRIPT), *args]
    try:
        process = subprocess.Popen(
            command, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        with SKELETON_STATE.lock:
            SKELETON_STATE.status = "failed"
        raise RuntimeError(f"无法启动黑底骨架训练：{exc}") from exc
    with SKELETON_STATE.lock:
        SKELETON_STATE.process = process
        SKELETON_STATE.command = command
        SKELETON_STATE.status = "running"
    threading.Thread(target=_drain_skeleton_stdout, args=(process,), daemon=True,
                     name="mc3-skeleton-train-stdout").start()
    return {"status": "running", "run_dir": str(run_dir), "resuming": "--resume" in args,
            "command": subprocess.list2cmdline(command)}


def _skeleton_pause() -> dict[str, Any]:
    with SKELETON_STATE.lock:
        process, marker = SKELETON_STATE.process, SKELETON_STATE.pause_file
        if process is None or process.poll() is not None or marker is None:
            raise RuntimeError("当前没有正在运行的黑底骨架训练。")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
        SKELETON_STATE.pause_requested = True
        SKELETON_STATE.status = "pause_requested"
    return {"status": "pause_requested", "message": "将在当前 epoch 完成、验证并保存权重后安全暂停。"}


def _skeleton_cancel_pause() -> dict[str, Any]:
    with SKELETON_STATE.lock:
        marker = SKELETON_STATE.pause_file
        if marker:
            marker.unlink(missing_ok=True)
        SKELETON_STATE.pause_requested = False
        if SKELETON_STATE.process is not None and SKELETON_STATE.process.poll() is None:
            SKELETON_STATE.status = "running"
    return {"status": "running", "message": "已取消待处理的安全暂停。"}


def _skeleton_stop() -> dict[str, Any]:
    with SKELETON_STATE.lock:
        process = SKELETON_STATE.process
        if process is None or process.poll() is not None:
            raise RuntimeError("当前没有正在运行的黑底骨架训练。")
        SKELETON_STATE.stop_requested = True
        SKELETON_STATE.status = "stopping"
        process.terminate()
    return {"status": "stopping", "message": "已请求立即停止；可从最后一个完整 epoch 的 last.pt 恢复。"}


def _skeleton_runs() -> list[dict[str, Any]]:
    root = DEFAULT_SKELETON_OUTPUT_ROOT
    if not root.is_dir():
        return []
    runs = []
    for run_dir in sorted((path for path in root.iterdir() if path.is_dir() and path.name.startswith("run_skeleton_ui_")),
                          key=lambda path: path.stat().st_mtime, reverse=True):
        checkpoint = run_dir / "last.pt"
        report = _read_json(run_dir / "final_report.json") or {}
        live = _read_json(run_dir / "live_status.json") or {}
        with SKELETON_STATE.lock:
            active = bool(SKELETON_STATE.run_dir and SKELETON_STATE.run_dir.resolve() == run_dir.resolve()
                          and SKELETON_STATE.process is not None and SKELETON_STATE.process.poll() is None)
            active_status = SKELETON_STATE.status if active else None
        run_status = ("completed" if report else active_status or
                      ("failed" if live.get("status") == "failed" else
                       "paused" if live.get("status") == "paused" and checkpoint.is_file() else
                       "resumable" if checkpoint.is_file() else "incomplete"))
        config = _read_json(run_dir / "config.json") or {}
        runs.append({"run_dir": str(run_dir.resolve()), "name": run_dir.name,
                     "last_checkpoint": str(checkpoint.resolve()) if checkpoint.is_file() and not report else None,
                     "status": run_status,
                     "epochs": int(config.get("epochs", 15) or 15),
                     "error": live.get("error"),
                     "completed_epochs": int(live.get("completed_epochs", len(_read_csv(run_dir / "training_history.csv")))) if (run_dir / "training_history.csv").is_file() else 0,
                     "best_epoch": report.get("best_epoch", live.get("best_epoch")),
                     "best_validation_roc_auc": report.get("best_validation_roc_auc", live.get("best_val_roc_auc"))})
    return runs[:40]


def _skeleton_status_payload(run_dir_value: str | None = None) -> dict[str, Any]:
    with SKELETON_STATE.lock:
        process = SKELETON_STATE.process
        active_dir = SKELETON_STATE.run_dir
        status = SKELETON_STATE.status
        pause_requested = SKELETON_STATE.pause_requested
        stop_requested = SKELETON_STATE.stop_requested
        started_at = SKELETON_STATE.started_at
        console = list(SKELETON_STATE.console)[-120:]
        command = list(SKELETON_STATE.command)
    if run_dir_value:
        run_dir = _require_child_path(run_dir_value, str(DEFAULT_SKELETON_OUTPUT_ROOT))
        if not run_dir.is_dir():
            raise ValueError("所选骨架训练运行目录不存在。")
    else:
        run_dir = active_dir
        if run_dir is None or not run_dir.exists():
            runs = _skeleton_runs()
            run_dir = Path(runs[0]["run_dir"]) if runs else None
    if run_dir is None:
        return {"status": status, "run_dir": None, "history": [], "console": console,
                "dataset_summary": {}, "final_report": {}, "epoch_checkpoints": [],
                "global_process_active": bool(process is not None and process.poll() is None),
                "active_run_dir": str(active_dir.resolve()) if active_dir and active_dir.exists() else None}
    current = bool(active_dir and active_dir.resolve() == run_dir.resolve() and process is not None)
    active = bool(current and process is not None and process.poll() is None)
    return_code = process.poll() if current and process is not None else None
    live = _read_json(run_dir / "live_status.json") or {}
    report = _read_json(run_dir / "final_report.json") or {}
    config = _read_json(run_dir / "config.json") or {}
    summary = _read_json(run_dir / "dataset_summary.json") or {}
    history = []
    for row in _read_csv(run_dir / "training_history.csv"):
        history.append({key: (_safe_float(value) if _safe_float(value) is not None else value) for key, value in row.items()})
    if active:
        current_status = "pause_requested" if pause_requested else "running"
    elif stop_requested and current:
        current_status = "stopped"
    elif report:
        current_status = "completed"
    elif (run_dir / "last.pt").is_file() and (return_code == 0 or status == "paused"):
        current_status = "paused"
    elif (run_dir / "last.pt").is_file():
        current_status = "resumable"
    elif return_code not in (None, 0):
        current_status = "failed"
    else:
        current_status = live.get("status", "incomplete")
    epoch_paths = sorted(run_dir.glob("epoch_[0-9][0-9]/best_model.pt"))
    epoch_checkpoints = [{"epoch": int(path.parent.name.split("_")[-1]), "path": str(path.resolve())}
                         for path in epoch_paths]
    log_text = _tail_file(run_dir / "run.log") or "\n".join(console)
    epochs = int(config.get("epochs", 15) or 15)
    history_last = history[-1] if history else {}
    completed_epochs = len(history)
    if current_status == "completed":
        stage, progress = "训练、阈值校准与测试已完成", 1.0
    else:
        stage = str(live.get("stage") or "准备训练")
        progress = max(0.0, min(1.0, float(live.get("stage_progress", 0.0) or 0.0)))
    if current_status in {"paused", "stopped", "resumable"}:
        stage = "已保存，可从最近完整 epoch 恢复"
    elif current_status == "failed":
        stage = "运行失败；请查看日志末尾"
    return {"status": current_status, "process_active": active, "run_dir": str(run_dir.resolve()),
            "global_process_active": bool(process is not None and process.poll() is None),
            "active_run_dir": str(active_dir.resolve()) if active_dir and active_dir.exists() else None,
            "return_code": return_code, "stage": stage, "stage_progress": progress,
            "current_epoch": int(live.get("epoch", completed_epochs + 1)), "total_epochs": epochs,
            "completed_epochs": completed_epochs, "step": live.get("step"), "steps_total": live.get("steps_total"),
            "eta_seconds": live.get("eta_seconds"), "elapsed_seconds": max(0, int(time.time() - started_at)) if started_at and current else None,
            "sample_total": summary.get("overall", {}).get("total_videos"),
            "dataset_summary": summary, "history": history, "latest_epoch": history_last,
            "validation_by_dataset": live.get("validation_by_dataset", {}),
            "final_report": report, "config": config, "last_checkpoint": str(run_dir / "last.pt") if (run_dir / "last.pt").is_file() else None,
            "error": live.get("error"),
            "best_model": str(run_dir / "best_model.pt") if (run_dir / "best_model.pt").is_file() else None,
            "epoch_checkpoints": epoch_checkpoints, "console": console, "log_text": log_text,
            "command": command}


def _native_finetune_dataset_summary() -> dict[str, Any]:
    import mc3_18_native_finetune_pipeline as native

    base_run = native.DEFAULT_BASE_RUN
    checkpoint = native.DEFAULT_BASE_CHECKPOINT
    summary = native.quick_inventory()
    return {"ready": checkpoint.is_file(), "protocol": native.PROTOCOL_ID,
            "base_checkpoint": str(checkpoint.resolve()),
            "base_checkpoint_bytes": checkpoint.stat().st_size if checkpoint.is_file() else 0,
            "base_manifest": str(native.DEFAULT_AIR_MANIFEST.resolve()),
            "base_run": str(base_run.resolve()), "output_root": str(DEFAULT_NATIVE_FINETUNE_OUTPUT_ROOT.resolve()),
            "cache_dir": str((DEFAULT_NATIVE_FINETUNE_OUTPUT_ROOT / "_frame_cache").resolve()),
            "split_policy": summary["split_policy"], "summary": summary}


def _native_finetune_training_arguments(payload: dict[str, Any], pause_file: Path) -> tuple[list[str], Path]:
    output_root = DEFAULT_NATIVE_FINETUNE_OUTPUT_ROOT.resolve()
    resume_value = str(payload.get("resume_checkpoint") or "").strip()
    saved_config: dict[str, Any] | None = None
    if resume_value:
        checkpoint = _require_child_path(resume_value, str(output_root))
        if checkpoint.name != "last.pt" or not checkpoint.is_file():
            raise ValueError("只能从本训练输出目录内所选 run 的 last.pt 恢复。")
        if (checkpoint.parent / "final_report.json").is_file():
            raise ValueError("该运行已完成最终测试，不能再恢复；请开始新运行。")
        saved_config = _read_json(checkpoint.parent / "config.json")
        required = {"epochs", "patience", "min_delta", "backbone_lr", "head_lr", "weight_decay",
                    "grad_accum", "window_batch_size", "seed", "lr_scheduler_factor",
                    "lr_scheduler_no_improvement_epochs", "min_lr", "amp", "device"}
        if saved_config is None or required.difference(saved_config):
            raise ValueError("该 last.pt 缺少完整配置，无法安全续训。")
        if (int(saved_config["epochs"]) != 15 or int(saved_config["patience"]) != 4
                or int(saved_config["lr_scheduler_no_improvement_epochs"]) != 2):
            raise ValueError("所选运行不是本页面设定的 15 epochs／4 patience／2 epochs 降学习率配置。")
        run_dir = checkpoint.parent
    else:
        run_dir = output_root / f"run_native_finetune_ui_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    integer_keys = {"grad_accum", "window_batch_size", "seed"}
    saved_map = {"lr_reduce_after": "lr_scheduler_no_improvement_epochs", "lr_factor": "lr_scheduler_factor"}
    params: dict[str, int | float] = {}
    for key, default in NATIVE_FINETUNE_DEFAULT_CONFIG.items():
        if saved_config is None:
            params[key] = _read_form_number(payload, key, default, integer=key in integer_keys)
        else:
            value = saved_config[saved_map.get(key, key)]
            params[key] = int(value) if key in integer_keys else float(value)
    if not 1 <= params["grad_accum"] <= 128 or not 1 <= params["window_batch_size"] <= 8:
        raise ValueError("Gradient accumulation 必须为 1–128，window batch 必须为 1–8。")
    if not 0 <= params["seed"] <= 2**32 - 1:
        raise ValueError("Seed 必须介于 0 到 2^32−1。")
    float_keys = ("min_delta", "backbone_lr", "head_lr", "weight_decay", "lr_factor", "min_lr")
    if not all(math.isfinite(float(params[key])) for key in float_keys):
        raise ValueError("超参数必须是有限数值。")
    if (not 0 <= params["min_delta"] <= 1 or not 0 < params["backbone_lr"] <= 1
            or not 0 < params["head_lr"] <= 1 or not 0 <= params["weight_decay"] <= 1):
        raise ValueError("请检查 min_delta、学习率和 weight decay 的范围。")
    if not 0 < params["lr_factor"] < 1 or not 0 <= params["min_lr"] <= min(params["backbone_lr"], params["head_lr"]):
        raise ValueError("LR factor 必须在 0–1 之间，min LR 不得大于初始学习率。")
    device = str(payload.get("device", "cuda"))
    amp = bool(payload.get("amp", True))
    if saved_config is not None:
        saved_device = str(saved_config.get("device", "cuda"))
        device = "cpu" if saved_device.startswith("cpu") else "cuda"
        amp = bool(saved_config.get("amp", True))
    if device not in {"cuda", "cpu"}:
        raise ValueError("设备必须选择 CUDA 或 CPU。")
    allow_cpu = device == "cpu" if saved_config is not None else bool(payload.get("allow_cpu", False))
    if device == "cpu" and not allow_cpu:
        raise ValueError("CPU 训练较慢；请明确勾选 CPU 确认项。")
    output_root.mkdir(parents=True, exist_ok=True)
    import mc3_18_native_finetune_pipeline as native

    args = ["--dataset-root", str(native.DATA_ROOT), "--base-manifest", str(native.DEFAULT_AIR_MANIFEST),
            "--dancetrack-manifest", str(native.DEFAULT_DANCETRACK_MANIFEST),
            "--base-checkpoint", str(native.DEFAULT_BASE_CHECKPOINT), "--output-root", str(output_root),
            "--run-dir", str(run_dir), "--cache-dir", str(output_root / "_frame_cache"),
            "--pause-file", str(pause_file), "--device", device,
            "--epochs", "15", "--patience", "4", "--lr-reduce-after", "2",
            "--min-delta", str(params["min_delta"]), "--backbone-lr", str(params["backbone_lr"]),
            "--head-lr", str(params["head_lr"]), "--weight-decay", str(params["weight_decay"]),
            "--grad-accum", str(params["grad_accum"]), "--window-batch-size", str(params["window_batch_size"]),
            "--seed", str(params["seed"]), "--lr-factor", str(params["lr_factor"]), "--min-lr", str(params["min_lr"])]
    if resume_value:
        args.extend(["--resume", resume_value])
    if allow_cpu:
        args.append("--allow-cpu")
    if not amp:
        args.append("--no-amp")
    return args, run_dir


def _drain_native_finetune_stdout(process: subprocess.Popen[str]) -> None:
    if process.stdout is not None:
        for line in process.stdout:
            with NATIVE_FINETUNE_STATE.lock:
                NATIVE_FINETUNE_STATE.console.append(line.rstrip("\r\n"))
    return_code = process.wait()
    with NATIVE_FINETUNE_STATE.lock:
        run_dir = NATIVE_FINETUNE_STATE.run_dir
        if NATIVE_FINETUNE_STATE.process is process:
            if NATIVE_FINETUNE_STATE.stop_requested:
                NATIVE_FINETUNE_STATE.status = "stopped"
            elif run_dir and (run_dir / "final_report.json").is_file():
                NATIVE_FINETUNE_STATE.status = "completed" if return_code == 0 else "failed"
            elif run_dir and (run_dir / "last.pt").is_file() and return_code == 0:
                NATIVE_FINETUNE_STATE.status = "paused"
            else:
                NATIVE_FINETUNE_STATE.status = "failed"


def _start_native_finetune(payload: dict[str, Any]) -> dict[str, Any]:
    inventory = _native_finetune_dataset_summary()
    if not inventory.get("ready"):
        raise RuntimeError("指定的 4S16F2 Best Checkpoint 不存在；训练尚未启动。")
    with STATE.lock:
        ordinary_active = STATE.train_process is not None and STATE.train_process.poll() is None
        inference_active = STATE.inference.get("status") in {"running", "queued"}
    with REVERSE_STATE.lock:
        reverse_active = REVERSE_STATE.process is not None and REVERSE_STATE.process.poll() is None
    with SKELETON_STATE.lock:
        skeleton_active = SKELETON_STATE.process is not None and SKELETON_STATE.process.poll() is None
    output_root = DEFAULT_NATIVE_FINETUNE_OUTPUT_ROOT.resolve()
    control_root = output_root / ".dashboard_control"
    control_root.mkdir(parents=True, exist_ok=True)
    pause_file = control_root / f"native_{uuid.uuid4().hex}.pause"
    args, run_dir = _native_finetune_training_arguments(payload, pause_file)
    with NATIVE_FINETUNE_STATE.lock:
        if NATIVE_FINETUNE_STATE.status == "starting":
            raise RuntimeError("原生视频后训练正在启动，请不要重复提交。")
        if NATIVE_FINETUNE_STATE.process is not None and NATIVE_FINETUNE_STATE.process.poll() is None:
            raise RuntimeError("多数据集原生视频后训练已经在运行。")
        if ordinary_active or reverse_active or skeleton_active or _mc3_training_active(except_name="native_finetune"):
            raise RuntimeError("已有其他 MC3-18 训练正在运行；请先暂停或等待其结束。")
        if inference_active:
            raise RuntimeError("视频推理正在使用计算资源，请等待推理完成。")
        run_dir.mkdir(parents=True, exist_ok=True)
        NATIVE_FINETUNE_STATE.run_dir = run_dir
        NATIVE_FINETUNE_STATE.pause_file = pause_file
        NATIVE_FINETUNE_STATE.status = "starting"
        NATIVE_FINETUNE_STATE.pause_requested = False
        NATIVE_FINETUNE_STATE.stop_requested = False
        NATIVE_FINETUNE_STATE.started_at = time.time()
        NATIVE_FINETUNE_STATE.console.clear()
    command = [sys.executable, "-u", str(NATIVE_FINETUNE_TRAINING_SCRIPT), *args]
    try:
        process = subprocess.Popen(command, cwd=PROJECT_ROOT, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                   errors="replace", bufsize=1,
                                   creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    except OSError as exc:
        with NATIVE_FINETUNE_STATE.lock:
            NATIVE_FINETUNE_STATE.status = "failed"
        raise RuntimeError(f"无法启动原生视频后训练：{exc}") from exc
    with NATIVE_FINETUNE_STATE.lock:
        NATIVE_FINETUNE_STATE.process = process
        NATIVE_FINETUNE_STATE.command = command
        NATIVE_FINETUNE_STATE.status = "running"
    threading.Thread(target=_drain_native_finetune_stdout, args=(process,), daemon=True,
                     name="mc3-native-finetune-stdout").start()
    return {"status": "running", "run_dir": str(run_dir), "resuming": "--resume" in args,
            "command": subprocess.list2cmdline(command)}


def _native_finetune_pause() -> dict[str, Any]:
    with NATIVE_FINETUNE_STATE.lock:
        process, marker = NATIVE_FINETUNE_STATE.process, NATIVE_FINETUNE_STATE.pause_file
        if process is None or process.poll() is not None or marker is None:
            raise RuntimeError("当前没有正在运行的原生视频后训练。")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
        NATIVE_FINETUNE_STATE.pause_requested = True
        NATIVE_FINETUNE_STATE.status = "pause_requested"
    return {"status": "pause_requested", "message": "当前 epoch 的验证和逐 epoch 权重保存完成后会安全暂停。"}


def _native_finetune_cancel_pause() -> dict[str, Any]:
    with NATIVE_FINETUNE_STATE.lock:
        marker = NATIVE_FINETUNE_STATE.pause_file
        if marker:
            marker.unlink(missing_ok=True)
        NATIVE_FINETUNE_STATE.pause_requested = False
        if NATIVE_FINETUNE_STATE.process is not None and NATIVE_FINETUNE_STATE.process.poll() is None:
            NATIVE_FINETUNE_STATE.status = "running"
    return {"status": "running", "message": "已取消待处理的安全暂停。"}


def _native_finetune_stop() -> dict[str, Any]:
    with NATIVE_FINETUNE_STATE.lock:
        process = NATIVE_FINETUNE_STATE.process
        if process is None or process.poll() is not None:
            raise RuntimeError("当前没有正在运行的原生视频后训练。")
        NATIVE_FINETUNE_STATE.stop_requested = True
        NATIVE_FINETUNE_STATE.status = "stopping"
        process.terminate()
    return {"status": "stopping", "message": "已请求停止；可从最近完整 epoch 的 last.pt 恢复。"}


def _native_finetune_runs() -> list[dict[str, Any]]:
    root = DEFAULT_NATIVE_FINETUNE_OUTPUT_ROOT
    if not root.is_dir():
        return []
    runs = []
    candidates = sorted((path for path in root.iterdir() if path.is_dir() and path.name.startswith("run_native_finetune_ui_")),
                       key=lambda path: path.stat().st_mtime, reverse=True)
    for run_dir in candidates:
        checkpoint = run_dir / "last.pt"
        report = _read_json(run_dir / "final_report.json") or {}
        live = _read_json(run_dir / "live_status.json") or {}
        with NATIVE_FINETUNE_STATE.lock:
            active = bool(NATIVE_FINETUNE_STATE.run_dir and NATIVE_FINETUNE_STATE.run_dir.resolve() == run_dir.resolve()
                          and NATIVE_FINETUNE_STATE.process is not None
                          and NATIVE_FINETUNE_STATE.process.poll() is None)
            active_status = NATIVE_FINETUNE_STATE.status if active else None
        status = ("completed" if report else active_status or
                  ("failed" if live.get("status") == "failed" else
                   "paused" if live.get("status") == "paused" and checkpoint.is_file() else
                   "resumable" if checkpoint.is_file() else "incomplete"))
        config = _read_json(run_dir / "config.json") or {}
        runs.append({"run_dir": str(run_dir.resolve()), "name": run_dir.name, "status": status,
                     "last_checkpoint": str(checkpoint.resolve()) if checkpoint.is_file() and not report else None,
                     "epochs": int(config.get("epochs", 15) or 15),
                     "completed_epochs": int(live.get("completed_epochs", len(_read_csv(run_dir / "training_history.csv")))),
                     "best_epoch": report.get("best_epoch", live.get("best_epoch")),
                     "best_validation_roc_auc": report.get("best_validation_roc_auc", live.get("best_val_roc_auc")),
                     "error": live.get("error")})
    return runs[:40]


def _native_finetune_status_payload(run_dir_value: str | None = None) -> dict[str, Any]:
    with NATIVE_FINETUNE_STATE.lock:
        process = NATIVE_FINETUNE_STATE.process
        active_dir = NATIVE_FINETUNE_STATE.run_dir
        status = NATIVE_FINETUNE_STATE.status
        pause_requested = NATIVE_FINETUNE_STATE.pause_requested
        stop_requested = NATIVE_FINETUNE_STATE.stop_requested
        started_at = NATIVE_FINETUNE_STATE.started_at
        console = list(NATIVE_FINETUNE_STATE.console)[-140:]
        command = list(NATIVE_FINETUNE_STATE.command)
    if run_dir_value:
        run_dir = _require_child_path(run_dir_value, str(DEFAULT_NATIVE_FINETUNE_OUTPUT_ROOT))
        if not run_dir.is_dir():
            raise ValueError("所选后训练运行目录不存在。")
    else:
        run_dir = active_dir
        if run_dir is None or not run_dir.exists():
            runs = _native_finetune_runs()
            run_dir = Path(runs[0]["run_dir"]) if runs else None
    if run_dir is None:
        return {"status": status, "run_dir": None, "history": [], "console": console,
                "dataset_summary": {}, "final_report": {}, "epoch_checkpoints": [],
                "active_run_dir": str(active_dir.resolve()) if active_dir and active_dir.exists() else None}
    current = bool(active_dir and active_dir.resolve() == run_dir.resolve() and process is not None)
    active = bool(current and process is not None and process.poll() is None)
    return_code = process.poll() if current and process is not None else None
    live = _read_json(run_dir / "live_status.json") or {}
    report = _read_json(run_dir / "final_report.json") or {}
    config = _read_json(run_dir / "config.json") or {}
    summary = _read_json(run_dir / "dataset_summary.json") or {}
    history = [{key: (_safe_float(value) if _safe_float(value) is not None else value) for key, value in row.items()}
               for row in _read_csv(run_dir / "training_history.csv")]
    if active:
        current_status = "pause_requested" if pause_requested else "running"
    elif stop_requested and current:
        current_status = "stopped"
    elif report.get("status") == "completed":
        current_status = "completed"
    elif live.get("status") == "failed":
        current_status = "failed"
    elif (run_dir / "last.pt").is_file() and (return_code == 0 or status == "paused"):
        current_status = "paused"
    elif (run_dir / "last.pt").is_file():
        current_status = "resumable"
    elif return_code not in (None, 0):
        current_status = "failed"
    else:
        current_status = live.get("status", "incomplete")
    epoch_paths = sorted(run_dir.glob("epoch_[0-9][0-9]/best_model.pt"))
    checkpoints = [{"epoch": int(path.parent.name.split("_")[-1]), "path": str(path.resolve())}
                   for path in epoch_paths]
    completed_epochs = len(history)
    if current_status == "completed":
        stage, progress = "训练、验证、最终测试均已完成", 1.0
    else:
        stage = str(live.get("stage") or "准备训练")
        progress = max(0.0, min(1.0, float(live.get("stage_progress", 0.0) or 0.0)))
    if current_status in {"paused", "stopped", "resumable"}:
        stage = "已保存，可从最近完整 epoch 恢复"
    elif current_status == "failed":
        stage = "运行失败；请查看日志与错误详情"
    log_text = _tail_file(run_dir / "run.log") or "\n".join(console)
    return {"status": current_status, "process_active": active, "global_process_active": bool(process and process.poll() is None),
            "active_run_dir": str(active_dir.resolve()) if active_dir and active_dir.exists() else None,
            "run_dir": str(run_dir.resolve()), "return_code": return_code, "stage": stage,
            "stage_progress": progress, "current_epoch": int(live.get("epoch", completed_epochs + 1)),
            "total_epochs": int(config.get("epochs", 15) or 15), "completed_epochs": completed_epochs,
            "step": live.get("step"), "steps_total": live.get("steps_total"), "eta_seconds": live.get("eta_seconds"),
            "elapsed_seconds": max(0, int(time.time() - started_at)) if started_at and current else None,
            "dataset_summary": summary, "history": history, "latest_epoch": history[-1] if history else None,
            "train_metrics_at_0_5": live.get("train_metrics_at_0_5"),
            "val_metrics_at_0_5": live.get("val_metrics_at_0_5"),
            "validation_by_dataset": live.get("validation_by_dataset", {}),
            "validation_thresholds": live.get("validation_thresholds", {}),
            "test_metrics": live.get("test_metrics", report.get("test_metrics", {})),
            "test_by_dataset": live.get("test_by_dataset", report.get("test_metrics_by_dataset", {})),
            "final_report": report, "config": config,
            "last_checkpoint": str(run_dir / "last.pt") if (run_dir / "last.pt").is_file() and not report else None,
            "best_model": str(run_dir / "best_model.pt") if (run_dir / "best_model.pt").is_file() else None,
            "epoch_checkpoints": checkpoints, "error": live.get("error"), "console": console,
            "log_text": log_text, "command": command}


def _full_frame_dataset_summary() -> dict[str, Any]:
    import mc3_18_full_frame_pipeline as full_frame

    summary = full_frame.quick_inventory()
    checkpoint = full_frame.BASE_CHECKPOINT
    return {**summary, "ready": bool(summary.get("ready") and checkpoint.is_file()),
            "base_checkpoint": str(checkpoint.resolve()),
            "base_checkpoint_bytes": checkpoint.stat().st_size if checkpoint.is_file() else 0,
            "output_root": str(DEFAULT_FULL_FRAME_OUTPUT_ROOT.resolve()),
            "cache_dir": str((DEFAULT_FULL_FRAME_OUTPUT_ROOT / "_frame_cache" / full_frame.CACHE_PROTOCOL).resolve())}


def _full_frame_training_arguments(payload: dict[str, Any], pause_file: Path) -> tuple[list[str], Path]:
    import mc3_18_full_frame_pipeline as full_frame

    output_root = DEFAULT_FULL_FRAME_OUTPUT_ROOT.resolve()
    resume_value = str(payload.get("resume_checkpoint") or "").strip()
    saved: dict[str, Any] | None = None
    if resume_value:
        checkpoint = _require_child_path(resume_value, str(output_root))
        if checkpoint.name != "last.pt" or not checkpoint.is_file():
            raise ValueError("只能从本训练输出目录内所选运行的 last.pt 恢复。")
        if (checkpoint.parent / "final_report.json").is_file():
            raise ValueError("该运行已完成最终测试，不能恢复；请启动新运行。")
        saved = _read_json(checkpoint.parent / "config.json")
        required = {"experiment_protocol", "input_representation", "epochs", "patience", "lr_scheduler_no_improvement_epochs",
                    "lr_scheduler_factor", "min_lr", "min_delta", "backbone_lr", "head_lr", "weight_decay",
                    "grad_accum", "window_batch_size", "seed", "amp", "device", "protocol",
                    "initial_checkpoint_sha256", "dataset_fingerprint"}
        if saved is None or required.difference(saved):
            raise ValueError("last.pt 缺少完整的全画面训练配置，不能安全恢复。")
        if (saved["experiment_protocol"] != FULL_FRAME_EXPERIMENT_PROTOCOL
                or saved["input_representation"] != "native_video_full_frame_short_side_112"
                or int(saved["epochs"]) != 15 or int(saved["patience"]) != 4
                or int(saved["lr_scheduler_no_improvement_epochs"]) != 2):
            raise ValueError("所选运行与本页 15 epochs／早停4／学习率patience 2 的方案不一致。")
        run_dir = checkpoint.parent
    else:
        run_dir = output_root / f"run_full_frame_ui_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    integer_keys = {"grad_accum", "window_batch_size", "seed"}
    saved_names = {"lr_reduce_after": "lr_scheduler_no_improvement_epochs", "lr_factor": "lr_scheduler_factor"}
    params: dict[str, int | float] = {}
    for key, default in NATIVE_FINETUNE_DEFAULT_CONFIG.items():
        if saved is None:
            params[key] = _read_form_number(payload, key, default, integer=key in integer_keys)
        else:
            value = saved[saved_names.get(key, key)]
            params[key] = int(value) if key in integer_keys else float(value)
    if not 1 <= params["grad_accum"] <= 128 or not 1 <= params["window_batch_size"] <= 8:
        raise ValueError("Gradient accumulation 必须为 1–128，窗口评分 batch 必须为 1–8。")
    if not 0 <= params["seed"] <= 2**32 - 1:
        raise ValueError("Seed 必须介于 0 到 2^32−1。")
    float_keys = ("min_delta", "backbone_lr", "head_lr", "weight_decay", "lr_factor", "min_lr")
    if not all(math.isfinite(float(params[key])) for key in float_keys):
        raise ValueError("超参数必须为有限数值。")
    if (not 0 <= params["min_delta"] <= 1 or not 0 < params["backbone_lr"] <= 1
            or not 0 < params["head_lr"] <= 1 or not 0 <= params["weight_decay"] <= 1):
        raise ValueError("请检查 min_delta、学习率和 weight decay 的范围。")
    if not 0 < params["lr_factor"] < 1 or not 0 <= params["min_lr"] <= min(params["backbone_lr"], params["head_lr"]):
        raise ValueError("LR factor 必须在 0–1 之间，min LR 不得高于初始学习率。")
    saved_device = str(saved.get("device", "cuda") if saved else payload.get("device", "cuda"))
    # torch.device("cuda") is persisted as "cuda:0"; map it back to the
    # user-facing choice accepted by this page before rebuilding resume args.
    device = "cuda" if saved_device.startswith("cuda") else saved_device
    if device not in {"cuda", "cpu"}:
        raise ValueError("设备必须选择 CUDA 或 CPU。")
    amp = bool(saved.get("amp", True) if saved else payload.get("amp", True))
    allow_cpu = device == "cpu" if saved else bool(payload.get("allow_cpu", False))
    if device == "cpu" and not allow_cpu:
        raise ValueError("CPU 训练较慢；请明确勾选 CPU 确认项。")

    output_root.mkdir(parents=True, exist_ok=True)
    cache_root = output_root / "_frame_cache" / full_frame.CACHE_PROTOCOL
    args = ["--dataset-root", str(full_frame.DATA_ROOT), "--base-manifest", str(full_frame.BASE_MANIFEST),
            "--dancetrack-manifest", str(full_frame.DANCETRACK_MANIFEST),
            "--base-checkpoint", str(full_frame.BASE_CHECKPOINT), "--output-root", str(output_root),
            "--run-dir", str(run_dir), "--cache-dir", str(cache_root), "--pause-file", str(pause_file),
            "--device", device, "--epochs", "15", "--patience", "4", "--lr-reduce-after", "2",
            "--min-delta", str(params["min_delta"]), "--backbone-lr", str(params["backbone_lr"]),
            "--head-lr", str(params["head_lr"]), "--weight-decay", str(params["weight_decay"]),
            "--grad-accum", str(params["grad_accum"]), "--window-batch-size", str(params["window_batch_size"]),
            "--seed", str(params["seed"]), "--lr-factor", str(params["lr_factor"]), "--min-lr", str(params["min_lr"])]
    if resume_value:
        args.extend(["--resume", resume_value])
    if allow_cpu:
        args.append("--allow-cpu")
    if not amp:
        args.append("--no-amp")
    return args, run_dir


def _full_frame_runs() -> list[dict[str, Any]]:
    root = DEFAULT_FULL_FRAME_OUTPUT_ROOT
    if not root.is_dir():
        return []
    result = []
    candidates = sorted((path for path in root.iterdir()
                         if path.is_dir() and path.name.startswith("run_full_frame_ui_")),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    for run_dir in candidates:
        live = _read_json(run_dir / "live_status.json") or {}
        report = _read_json(run_dir / "final_report.json") or {}
        config = _read_json(run_dir / "config.json") or {}
        with FULL_FRAME_STATE.lock:
            active = bool(FULL_FRAME_STATE.run_dir and FULL_FRAME_STATE.run_dir.resolve() == run_dir.resolve()
                          and FULL_FRAME_STATE.process is not None and FULL_FRAME_STATE.process.poll() is None)
            active_status = FULL_FRAME_STATE.status if active else None
        checkpoint = run_dir / "last.pt"
        status = ("completed" if report else active_status or
                  ("failed" if live.get("status") == "failed" else
                   "paused" if live.get("status") == "paused" and checkpoint.is_file() else
                   "resumable" if checkpoint.is_file() else "incomplete"))
        result.append({"run_dir": str(run_dir.resolve()), "name": run_dir.name, "status": status,
                       "last_checkpoint": str(checkpoint.resolve()) if checkpoint.is_file() and not report else None,
                       "epochs": int(config.get("epochs", 15) or 15),
                       "completed_epochs": int(live.get("completed_epochs", len(_read_csv(run_dir / "training_history.csv")))),
                       "best_epoch": report.get("best_epoch", live.get("best_epoch")),
                       "best_validation_roc_auc": report.get("best_validation_roc_auc", live.get("best_val_auc")),
                       "error": live.get("error")})
    return result[:40]


def _full_frame_status_payload(run_dir_value: str | None = None) -> dict[str, Any]:
    with FULL_FRAME_STATE.lock:
        process = FULL_FRAME_STATE.process
        active_dir = FULL_FRAME_STATE.run_dir
        status = FULL_FRAME_STATE.status
        pause_requested = FULL_FRAME_STATE.pause_requested
        stop_requested = FULL_FRAME_STATE.stop_requested
        started_at = FULL_FRAME_STATE.started_at
        console = list(FULL_FRAME_STATE.console)[-140:]
        command = list(FULL_FRAME_STATE.command)
    if run_dir_value:
        run_dir = _require_child_path(run_dir_value, str(DEFAULT_FULL_FRAME_OUTPUT_ROOT))
        if not run_dir.is_dir() or not run_dir.name.startswith("run_full_frame_ui_"):
            raise ValueError("所选全画面训练运行目录不存在。")
    else:
        run_dir = active_dir
        if run_dir is None or not run_dir.exists():
            runs = _full_frame_runs()
            run_dir = Path(runs[0]["run_dir"]) if runs else None
    if run_dir is None:
        return {"status": status, "run_dir": None, "history": [], "console": console,
                "dataset_summary": {}, "final_report": {}, "epoch_checkpoints": [], "config": {}}
    current = bool(active_dir and active_dir.resolve() == run_dir.resolve() and process is not None)
    active = bool(current and process is not None and process.poll() is None)
    return_code = process.poll() if current and process is not None else None
    live = _read_json(run_dir / "live_status.json") or {}
    report = _read_json(run_dir / "final_report.json") or {}
    config = _read_json(run_dir / "config.json") or {}
    summary = _read_json(run_dir / "dataset_summary.json") or {}
    history = [{key: (_safe_float(value) if _safe_float(value) is not None else value) for key, value in row.items()}
               for row in _read_csv(run_dir / "training_history.csv")]
    if active:
        current_status = "pause_requested" if pause_requested else "running"
    elif stop_requested and current:
        current_status = "stopped"
    elif report.get("status") == "completed":
        current_status = "completed"
    elif live.get("status") == "failed":
        current_status = "failed"
    elif (run_dir / "last.pt").is_file() and (return_code == 0 or status == "paused"):
        current_status = "paused"
    elif (run_dir / "last.pt").is_file():
        current_status = "resumable"
    elif return_code not in (None, 0):
        current_status = "failed"
    else:
        current_status = live.get("status", "incomplete")
    checkpoints = [{"epoch": int(path.parent.name.split("_")[-1]), "path": str(path.resolve())}
                   for path in sorted(run_dir.glob("epoch_[0-9][0-9]/best_model.pt"))]
    completed = len(history)
    stage = "训练、验证和最终测试已完成" if current_status == "completed" else str(live.get("stage") or "准备训练")
    if current_status in {"paused", "stopped", "resumable"}:
        stage = "已保存，可从最近完整 epoch 恢复"
    elif current_status == "failed":
        stage = "运行失败；请查看日志和错误详情"
    return {"status": current_status, "process_active": active, "global_process_active": bool(process and process.poll() is None),
            "active_run_dir": str(active_dir.resolve()) if active_dir and active_dir.exists() else None,
            "run_dir": str(run_dir.resolve()), "return_code": return_code, "stage": stage,
            "stage_progress": max(0.0, min(1.0, float(live.get("stage_progress", 0.0) or 0.0))),
            "current_epoch": int(live.get("epoch", completed + 1)), "total_epochs": int(config.get("epochs", 15) or 15),
            "completed_epochs": completed, "step": live.get("step"), "steps_total": live.get("steps_total"),
            "best_epoch": report.get("best_epoch", live.get("best_epoch")),
            "best_validation_roc_auc": report.get("best_validation_roc_auc", live.get("best_val_roc_auc")),
            "eta_seconds": live.get("eta_seconds"),
            "elapsed_seconds": max(0, int(time.time() - started_at)) if started_at and current else None,
            "dataset_summary": summary, "history": history, "latest_epoch": history[-1] if history else None,
            "validation_thresholds": live.get("validation_thresholds", {}),
            "validation_metrics": live.get("validation_metrics", {}), "test_metrics": live.get("test_metrics", report.get("test_metrics", {})),
            "validation_by_dataset": live.get("validation_by_dataset", report.get("validation_metrics_by_dataset", {})),
            "test_by_dataset": live.get("test_by_dataset", report.get("test_metrics_by_dataset", {})),
            "final_report": report, "config": config,
            "last_checkpoint": str(run_dir / "last.pt") if (run_dir / "last.pt").is_file() and not report else None,
            "best_model": str(run_dir / "best_model.pt") if (run_dir / "best_model.pt").is_file() else None,
            "epoch_checkpoints": checkpoints, "error": live.get("error"), "console": console,
            "log_text": _tail_file(run_dir / "run.log") or "\n".join(console), "command": command}


def _drain_full_frame_stdout(process: subprocess.Popen[str]) -> None:
    if process.stdout is not None:
        for line in process.stdout:
            with FULL_FRAME_STATE.lock:
                FULL_FRAME_STATE.console.append(line.rstrip("\r\n"))
    return_code = process.wait()
    with FULL_FRAME_STATE.lock:
        run_dir = FULL_FRAME_STATE.run_dir
        if FULL_FRAME_STATE.process is process:
            if FULL_FRAME_STATE.stop_requested:
                FULL_FRAME_STATE.status = "stopped"
            elif run_dir and (run_dir / "final_report.json").is_file():
                FULL_FRAME_STATE.status = "completed" if return_code == 0 else "failed"
            elif run_dir and (run_dir / "last.pt").is_file() and return_code == 0:
                FULL_FRAME_STATE.status = "paused"
            else:
                FULL_FRAME_STATE.status = "failed"


def _full_frame_training_active() -> bool:
    with FULL_FRAME_STATE.lock:
        return FULL_FRAME_STATE.status == "starting" or bool(
            FULL_FRAME_STATE.process is not None and FULL_FRAME_STATE.process.poll() is None)


def _start_full_frame_training(payload: dict[str, Any]) -> dict[str, Any]:
    inventory = _full_frame_dataset_summary()
    if not inventory.get("ready"):
        raise RuntimeError("所需数据清单、DanceTrack 留帧清单或 4S16F2 Best Checkpoint 缺失；训练尚未启动。")
    with STATE.lock:
        ordinary_active = STATE.train_process is not None and STATE.train_process.poll() is None
        inference_active = STATE.inference.get("status") in {"running", "queued"}
    with REVERSE_STATE.lock:
        reverse_active = REVERSE_STATE.process is not None and REVERSE_STATE.process.poll() is None
    with SKELETON_STATE.lock:
        skeleton_active = SKELETON_STATE.process is not None and SKELETON_STATE.process.poll() is None
    with NATIVE_FINETUNE_STATE.lock:
        native_active = NATIVE_FINETUNE_STATE.process is not None and NATIVE_FINETUNE_STATE.process.poll() is None
    output_root = DEFAULT_FULL_FRAME_OUTPUT_ROOT.resolve()
    control_root = output_root / ".dashboard_control"
    control_root.mkdir(parents=True, exist_ok=True)
    pause_file = control_root / f"full_frame_{uuid.uuid4().hex}.pause"
    args, run_dir = _full_frame_training_arguments(payload, pause_file)
    with FULL_FRAME_STATE.lock:
        if _full_frame_training_active():
            raise RuntimeError("全画面 MC3-18 训练已在运行。")
        if ordinary_active or reverse_active or skeleton_active or native_active or _mc3_training_active(except_name="full_frame"):
            raise RuntimeError("已有其他 MC3-18 训练正在运行；请等待或安全暂停后再启动。")
        if inference_active:
            raise RuntimeError("视频推理正在占用计算资源，请等待推理完成。")
        run_dir.mkdir(parents=True, exist_ok=True)
        FULL_FRAME_STATE.run_dir = run_dir
        FULL_FRAME_STATE.pause_file = pause_file
        FULL_FRAME_STATE.status = "starting"
        FULL_FRAME_STATE.pause_requested = False
        FULL_FRAME_STATE.stop_requested = False
        FULL_FRAME_STATE.started_at = time.time()
        FULL_FRAME_STATE.console.clear()
    command = [sys.executable, "-u", str(FULL_FRAME_TRAINING_SCRIPT), *args]
    try:
        process = subprocess.Popen(command, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", bufsize=1,
                                   creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    except OSError as exc:
        with FULL_FRAME_STATE.lock:
            FULL_FRAME_STATE.status = "failed"
        raise RuntimeError(f"无法启动全画面 MC3-18 训练：{exc}") from exc
    with FULL_FRAME_STATE.lock:
        FULL_FRAME_STATE.process = process
        FULL_FRAME_STATE.command = command
        FULL_FRAME_STATE.status = "running"
    threading.Thread(target=_drain_full_frame_stdout, args=(process,), daemon=True,
                     name="mc3-full-frame-train-stdout").start()
    return {"status": "running", "run_dir": str(run_dir), "resuming": "--resume" in args,
            "command": subprocess.list2cmdline(command)}


def _full_frame_pause() -> dict[str, Any]:
    with FULL_FRAME_STATE.lock:
        process, marker = FULL_FRAME_STATE.process, FULL_FRAME_STATE.pause_file
        if process is None or process.poll() is not None or marker is None:
            raise RuntimeError("当前没有正在运行的全画面训练。")
        if FULL_FRAME_STATE.pause_requested:
            raise RuntimeError("已经请求安全暂停。")
        marker.touch(exist_ok=True)
        FULL_FRAME_STATE.pause_requested = True
        FULL_FRAME_STATE.status = "pause_requested"
    return {"status": "pause_requested", "message": "当前 epoch 完成验证并保存 checkpoint 后会安全暂停。"}


def _full_frame_cancel_pause() -> dict[str, Any]:
    with FULL_FRAME_STATE.lock:
        process, marker = FULL_FRAME_STATE.process, FULL_FRAME_STATE.pause_file
        if process is None or process.poll() is not None or not FULL_FRAME_STATE.pause_requested:
            raise RuntimeError("当前没有待执行的安全暂停请求。")
        if marker:
            marker.unlink(missing_ok=True)
        FULL_FRAME_STATE.pause_requested = False
        FULL_FRAME_STATE.status = "running"
    return {"status": "running", "message": "已取消安全暂停请求。"}


def _full_frame_stop() -> dict[str, Any]:
    with FULL_FRAME_STATE.lock:
        process = FULL_FRAME_STATE.process
        if process is None or process.poll() is not None:
            raise RuntimeError("当前没有正在运行的全画面训练。")
        FULL_FRAME_STATE.stop_requested = True
        FULL_FRAME_STATE.status = "stopping"
        if FULL_FRAME_STATE.pause_file:
            FULL_FRAME_STATE.pause_file.unlink(missing_ok=True)
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=4)
    return {"status": "stopped", "message": "已请求停止；可从最近完整 epoch 的 last.pt 恢复。"}


def _harvard_letterbox_dataset_summary() -> dict[str, Any]:
    import mc3_18_harvard_letterbox_pipeline as profile

    summary = profile.quick_inventory()
    return {**summary,
            "ready": bool(summary.get("ready")),
            "output_root": str(DEFAULT_HARVARD_LETTERBOX_OUTPUT_ROOT.resolve()),
            "cache_dir": str((DEFAULT_HARVARD_LETTERBOX_OUTPUT_ROOT / "_frame_cache" /
                              profile.CACHE_PROTOCOL).resolve())}


def _harvard_letterbox_training_arguments(payload: dict[str, Any], pause_file: Path) -> tuple[list[str], Path]:
    import mc3_18_harvard_letterbox_pipeline as profile

    output_root = DEFAULT_HARVARD_LETTERBOX_OUTPUT_ROOT.resolve()
    resume_value = str(payload.get("resume_checkpoint") or "").strip()
    saved: dict[str, Any] | None = None
    if resume_value:
        checkpoint = _require_child_path(resume_value, str(output_root))
        if checkpoint.name != "last.pt" or not checkpoint.is_file():
            raise ValueError("只能从本方案输出目录内所选运行的 last.pt 恢复。")
        if (checkpoint.parent / "final_report.json").is_file():
            raise ValueError("该运行已完成最终测试，不能恢复；请启动新运行。")
        saved = _read_json(checkpoint.parent / "config.json")
        required = {"experiment_protocol", "input_representation", "epochs", "patience",
                    "lr_scheduler_no_improvement_epochs", "lr_scheduler_factor", "min_lr", "min_delta",
                    "backbone_lr", "head_lr", "weight_decay", "grad_accum", "window_batch_size", "seed",
                    "amp", "device", "protocol", "initialization_mode", "initialization",
                    "dataset_fingerprint"}
        if saved is None or required.difference(saved):
            raise ValueError("last.pt 缺少完整的 letterbox 训练配置，不能安全恢复。")
        if (saved["experiment_protocol"] != HARVARD_LETTERBOX_EXPERIMENT_PROTOCOL
                or saved["input_representation"] != "native_video_letterbox_160x160_rgb_mean"
                or saved["initialization_mode"] != "kinetics400"
                or saved["initialization"].get("weights") != "MC3_18_Weights.KINETICS400_V1"):
            raise ValueError("所选 checkpoint 不属于 Harvard/RWF/AIRT 160×160 letterbox 训练方案。")
        run_dir = checkpoint.parent
    else:
        run_dir = output_root / f"run_harvard_letterbox_ui_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    integer_keys = {"epochs", "patience", "lr_reduce_after", "grad_accum", "window_batch_size", "seed"}
    saved_names = {"lr_reduce_after": "lr_scheduler_no_improvement_epochs",
                   "lr_factor": "lr_scheduler_factor"}
    params: dict[str, int | float] = {}
    for key, default in HARVARD_LETTERBOX_DEFAULT_CONFIG.items():
        value = _read_form_number(payload, key, default, integer=key in integer_keys) if saved is None else saved[saved_names.get(key, key)]
        params[key] = int(value) if key in integer_keys else float(value)
    if not 1 <= params["epochs"] <= 300 or not 1 <= params["patience"] <= 300:
        raise ValueError("Epoch 和早停 patience 必须介于 1 到 300。")
    if (not 1 <= params["lr_reduce_after"] <= 300 or not 1 <= params["grad_accum"] <= 128
            or not 1 <= params["window_batch_size"] <= 8):
        raise ValueError("学习率 patience 必须为 1–300，梯度累积为 1–128，窗口 batch 为 1–8。")
    if not 0 <= params["seed"] <= 2**32 - 1:
        raise ValueError("Seed 必须介于 0 到 2^32−1。")
    float_keys = ("min_delta", "backbone_lr", "head_lr", "weight_decay", "lr_factor", "min_lr")
    if not all(math.isfinite(float(params[key])) for key in float_keys):
        raise ValueError("超参数必须为有限数值。")
    if (not 0 <= params["min_delta"] <= 1 or not 0 < params["backbone_lr"] <= 1
            or not 0 < params["head_lr"] <= 1 or not 0 <= params["weight_decay"] <= 1):
        raise ValueError("请检查 min_delta、学习率和 weight decay 的范围。")
    if not 0 < params["lr_factor"] < 1 or not 0 <= params["min_lr"] <= min(params["backbone_lr"], params["head_lr"]):
        raise ValueError("LR factor 必须在 0–1 之间，min LR 不得高于初始学习率。")
    device_value = str(saved.get("device", "cuda") if saved else payload.get("device", "cuda"))
    device = "cuda" if device_value.startswith("cuda") else device_value
    if device not in {"cuda", "cpu"}:
        raise ValueError("设备必须选择 CUDA 或 CPU。")
    amp = bool(saved.get("amp", True) if saved else payload.get("amp", True))
    allow_cpu = device == "cpu" if saved else bool(payload.get("allow_cpu", False))
    if device == "cpu" and not allow_cpu:
        raise ValueError("CPU 训练较慢；请明确勾选 CPU 确认项。")

    output_root.mkdir(parents=True, exist_ok=True)
    cache_root = output_root / "_frame_cache" / profile.CACHE_PROTOCOL
    args = ["--dataset-root", str(profile.DATA_ROOT), "--base-manifest", str(profile.BASE_MANIFEST),
            "--dancetrack-manifest", "", "--initialization-mode", "kinetics400",
            "--output-root", str(output_root), "--run-dir", str(run_dir), "--cache-dir", str(cache_root),
            "--pause-file", str(pause_file), "--device", device,
            "--epochs", str(params["epochs"]), "--patience", str(params["patience"]),
            "--lr-reduce-after", str(params["lr_reduce_after"]),
            "--min-delta", str(params["min_delta"]), "--backbone-lr", str(params["backbone_lr"]),
            "--head-lr", str(params["head_lr"]), "--weight-decay", str(params["weight_decay"]),
            "--grad-accum", str(params["grad_accum"]), "--window-batch-size", str(params["window_batch_size"]),
            "--seed", str(params["seed"]), "--lr-factor", str(params["lr_factor"]),
            "--min-lr", str(params["min_lr"])]
    if resume_value:
        args.extend(["--resume", resume_value])
    if allow_cpu:
        args.append("--allow-cpu")
    if not amp:
        args.append("--no-amp")
    return args, run_dir


def _drain_harvard_letterbox_stdout(process: subprocess.Popen[str]) -> None:
    if process.stdout is not None:
        for line in process.stdout:
            with HARVARD_LETTERBOX_STATE.lock:
                HARVARD_LETTERBOX_STATE.console.append(line.rstrip("\r\n"))
    return_code = process.wait()
    with HARVARD_LETTERBOX_STATE.lock:
        run_dir = HARVARD_LETTERBOX_STATE.run_dir
        if HARVARD_LETTERBOX_STATE.process is process:
            if HARVARD_LETTERBOX_STATE.stop_requested:
                HARVARD_LETTERBOX_STATE.status = "stopped"
            elif run_dir and (run_dir / "final_report.json").is_file():
                HARVARD_LETTERBOX_STATE.status = "completed" if return_code == 0 else "failed"
            elif run_dir and (run_dir / "last.pt").is_file() and return_code == 0:
                HARVARD_LETTERBOX_STATE.status = "paused"
            else:
                HARVARD_LETTERBOX_STATE.status = "failed"


def _start_harvard_letterbox_training(payload: dict[str, Any]) -> dict[str, Any]:
    inventory = _harvard_letterbox_dataset_summary()
    if not inventory.get("ready"):
        raise RuntimeError("数据清单或审计划分未通过预检；训练尚未启动。")
    with STATE.lock:
        inference_active = STATE.inference.get("status") in {"running", "queued"}
    output_root = DEFAULT_HARVARD_LETTERBOX_OUTPUT_ROOT.resolve()
    control_root = output_root / ".dashboard_control"
    control_root.mkdir(parents=True, exist_ok=True)
    pause_file = control_root / f"harvard_letterbox_{uuid.uuid4().hex}.pause"
    args, run_dir = _harvard_letterbox_training_arguments(payload, pause_file)
    with HARVARD_LETTERBOX_STATE.lock:
        if HARVARD_LETTERBOX_STATE.status == "starting":
            raise RuntimeError("Harvard/RWF/AIRT letterbox 训练正在启动，请勿重复提交。")
        if (HARVARD_LETTERBOX_STATE.process is not None
                and HARVARD_LETTERBOX_STATE.process.poll() is None):
            raise RuntimeError("Harvard/RWF/AIRT letterbox 训练已经在运行。")
        if _mc3_training_active(except_name="harvard_letterbox"):
            raise RuntimeError("已有其他 MC3-18 训练正在运行；请先暂停或等待其结束。")
        if inference_active:
            raise RuntimeError("视频推理正在使用计算资源，请等待推理完成。")
        run_dir.mkdir(parents=True, exist_ok=True)
        HARVARD_LETTERBOX_STATE.run_dir = run_dir
        HARVARD_LETTERBOX_STATE.output_root = output_root
        HARVARD_LETTERBOX_STATE.pause_file = pause_file
        HARVARD_LETTERBOX_STATE.status = "starting"
        HARVARD_LETTERBOX_STATE.pause_requested = False
        HARVARD_LETTERBOX_STATE.stop_requested = False
        HARVARD_LETTERBOX_STATE.started_at = time.time()
        HARVARD_LETTERBOX_STATE.console.clear()
    command = [sys.executable, "-u", str(HARVARD_LETTERBOX_TRAINING_SCRIPT), *args]
    try:
        process = subprocess.Popen(command, cwd=PROJECT_ROOT, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                   errors="replace", bufsize=1,
                                   creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    except OSError as exc:
        with HARVARD_LETTERBOX_STATE.lock:
            HARVARD_LETTERBOX_STATE.status = "failed"
        raise RuntimeError(f"无法启动 Harvard/RWF/AIRT letterbox 训练：{exc}") from exc
    with HARVARD_LETTERBOX_STATE.lock:
        HARVARD_LETTERBOX_STATE.process = process
        HARVARD_LETTERBOX_STATE.command = command
        HARVARD_LETTERBOX_STATE.status = "running"
    threading.Thread(target=_drain_harvard_letterbox_stdout, args=(process,), daemon=True,
                     name="mc3-harvard-letterbox-stdout").start()
    return {"status": "running", "run_dir": str(run_dir), "resuming": "--resume" in args,
            "command": subprocess.list2cmdline(command)}


def _harvard_letterbox_pause() -> dict[str, Any]:
    with HARVARD_LETTERBOX_STATE.lock:
        process, marker = HARVARD_LETTERBOX_STATE.process, HARVARD_LETTERBOX_STATE.pause_file
        if process is None or process.poll() is not None or marker is None:
            raise RuntimeError("当前没有正在运行的 Harvard/RWF/AIRT letterbox 训练。")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
        HARVARD_LETTERBOX_STATE.pause_requested = True
        HARVARD_LETTERBOX_STATE.status = "pause_requested"
    return {"status": "pause_requested", "message": "本 epoch 验证并保存权重后会安全暂停。"}


def _harvard_letterbox_cancel_pause() -> dict[str, Any]:
    with HARVARD_LETTERBOX_STATE.lock:
        marker = HARVARD_LETTERBOX_STATE.pause_file
        if marker:
            marker.unlink(missing_ok=True)
        HARVARD_LETTERBOX_STATE.pause_requested = False
        if (HARVARD_LETTERBOX_STATE.process is not None
                and HARVARD_LETTERBOX_STATE.process.poll() is None):
            HARVARD_LETTERBOX_STATE.status = "running"
    return {"status": "running", "message": "已取消待执行的安全暂停请求。"}


def _harvard_letterbox_stop() -> dict[str, Any]:
    with HARVARD_LETTERBOX_STATE.lock:
        process = HARVARD_LETTERBOX_STATE.process
        if process is None or process.poll() is not None:
            raise RuntimeError("当前没有正在运行的 Harvard/RWF/AIRT letterbox 训练。")
        HARVARD_LETTERBOX_STATE.stop_requested = True
        HARVARD_LETTERBOX_STATE.status = "stopping"
        if HARVARD_LETTERBOX_STATE.pause_file:
            HARVARD_LETTERBOX_STATE.pause_file.unlink(missing_ok=True)
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=4)
    return {"status": "stopped", "message": "已请求停止；可从最近完整 epoch 的 last.pt 恢复。"}


def _harvard_letterbox_runs() -> list[dict[str, Any]]:
    root = DEFAULT_HARVARD_LETTERBOX_OUTPUT_ROOT
    if not root.is_dir():
        return []
    result = []
    candidates = sorted((path for path in root.iterdir()
                         if path.is_dir() and path.name.startswith("run_harvard_letterbox_ui_")),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    for run_dir in candidates:
        live = _read_json(run_dir / "live_status.json") or {}
        report = _read_json(run_dir / "final_report.json") or {}
        config = _read_json(run_dir / "config.json") or {}
        with HARVARD_LETTERBOX_STATE.lock:
            active = bool(HARVARD_LETTERBOX_STATE.run_dir and
                          HARVARD_LETTERBOX_STATE.run_dir.resolve() == run_dir.resolve() and
                          HARVARD_LETTERBOX_STATE.process is not None and
                          HARVARD_LETTERBOX_STATE.process.poll() is None)
            active_status = HARVARD_LETTERBOX_STATE.status if active else None
        checkpoint = run_dir / "last.pt"
        status = ("completed" if report else active_status or
                  ("failed" if live.get("status") == "failed" else
                   "paused" if live.get("status") == "paused" and checkpoint.is_file() else
                   "resumable" if checkpoint.is_file() else "incomplete"))
        result.append({"run_dir": str(run_dir.resolve()), "name": run_dir.name, "status": status,
                       "last_checkpoint": str(checkpoint.resolve()) if checkpoint.is_file() and not report else None,
                       "epochs": int(config.get("epochs", 15) or 15),
                       "completed_epochs": int(live.get("completed_epochs", len(_read_csv(run_dir / "training_history.csv")))),
                       "best_epoch": report.get("best_epoch", live.get("best_epoch")),
                       "best_validation_roc_auc": report.get("best_validation_roc_auc", live.get("best_val_roc_auc")),
                       "error": live.get("error")})
    return result[:40]


def _harvard_letterbox_status_payload(run_dir_value: str | None = None) -> dict[str, Any]:
    with HARVARD_LETTERBOX_STATE.lock:
        process = HARVARD_LETTERBOX_STATE.process
        active_dir = HARVARD_LETTERBOX_STATE.run_dir
        status = HARVARD_LETTERBOX_STATE.status
        pause_requested = HARVARD_LETTERBOX_STATE.pause_requested
        stop_requested = HARVARD_LETTERBOX_STATE.stop_requested
        started_at = HARVARD_LETTERBOX_STATE.started_at
        console = list(HARVARD_LETTERBOX_STATE.console)[-140:]
        command = list(HARVARD_LETTERBOX_STATE.command)
    if run_dir_value:
        run_dir = _require_child_path(run_dir_value, str(DEFAULT_HARVARD_LETTERBOX_OUTPUT_ROOT))
        if not run_dir.is_dir() or not run_dir.name.startswith("run_harvard_letterbox_ui_"):
            raise ValueError("所选 letterbox 训练运行目录不存在。")
    else:
        run_dir = active_dir
        if run_dir is None or not run_dir.exists():
            runs = _harvard_letterbox_runs()
            run_dir = Path(runs[0]["run_dir"]) if runs else None
    if run_dir is None:
        return {"status": status, "run_dir": None, "history": [], "console": console,
                "dataset_summary": {}, "final_report": {}, "epoch_checkpoints": [], "config": {}}
    current = bool(active_dir and active_dir.resolve() == run_dir.resolve() and process is not None)
    active = bool(current and process is not None and process.poll() is None)
    return_code = process.poll() if current and process is not None else None
    live = _read_json(run_dir / "live_status.json") or {}
    report = _read_json(run_dir / "final_report.json") or {}
    config = _read_json(run_dir / "config.json") or {}
    summary = _read_json(run_dir / "dataset_summary.json") or {}
    history = [{key: (_safe_float(value) if _safe_float(value) is not None else value) for key, value in row.items()}
               for row in _read_csv(run_dir / "training_history.csv")]
    if active:
        current_status = "pause_requested" if pause_requested else "running"
    elif stop_requested and current:
        current_status = "stopped"
    elif report.get("status") == "completed":
        current_status = "completed"
    elif live.get("status") == "failed":
        current_status = "failed"
    elif (run_dir / "last.pt").is_file() and (return_code == 0 or status == "paused"):
        current_status = "paused"
    elif (run_dir / "last.pt").is_file():
        current_status = "resumable"
    elif return_code not in (None, 0):
        current_status = "failed"
    else:
        current_status = live.get("status", "incomplete")
    checkpoints = [{"epoch": int(path.parent.name.split("_")[-1]), "path": str(path.resolve())}
                   for path in sorted(run_dir.glob("epoch_[0-9][0-9]/best_model.pt"))]
    completed_epochs = len(history)
    latest_epoch_dir = run_dir / f"epoch_{completed_epochs:02d}" if completed_epochs else None
    latest_epoch_thresholds = (_read_json(latest_epoch_dir / "thresholds.json")
                               if latest_epoch_dir and latest_epoch_dir.is_dir() else {}) or {}
    stage = ("训练、验证、最终测试均已完成" if current_status == "completed" else
             "已保存，可从最近完整 epoch 恢复" if current_status in {"paused", "stopped", "resumable"} else
             "运行失败；请查看日志与错误详情" if current_status == "failed" else
             str(live.get("stage") or "准备训练"))
    progress = 1.0 if current_status == "completed" else max(0.0, min(1.0, float(live.get("stage_progress", 0.0) or 0.0)))
    return {"status": current_status, "process_active": active,
            "global_process_active": bool(process and process.poll() is None),
            "active_run_dir": str(active_dir.resolve()) if active_dir and active_dir.exists() else None,
            "run_dir": str(run_dir.resolve()), "return_code": return_code, "stage": stage,
            "stage_progress": progress, "current_epoch": int(live.get("epoch", completed_epochs + 1)),
            "total_epochs": int(config.get("epochs", 15) or 15), "completed_epochs": completed_epochs,
            "step": live.get("step"), "steps_total": live.get("steps_total"), "eta_seconds": live.get("eta_seconds"),
            "elapsed_seconds": max(0, int(time.time() - started_at)) if started_at and current else None,
            "dataset_summary": summary, "history": history, "latest_epoch": history[-1] if history else None,
            "train_metrics_at_0_5": live.get("train_metrics_at_0_5"),
            "val_metrics_at_0_5": live.get("val_metrics_at_0_5"),
            "validation_by_dataset": live.get("validation_by_dataset", {}),
            "validation_thresholds": live.get("validation_thresholds", {}),
            "epoch_validation_thresholds": latest_epoch_thresholds.get("selection", {}),
            "epoch_validation_metrics": latest_epoch_thresholds.get("validation_metrics", {}),
            "validation_metrics": live.get("validation_metrics", report.get("validation_metrics", {})),
            "test_metrics": live.get("test_metrics", report.get("test_metrics", {})),
            "test_by_dataset": live.get("test_by_dataset", report.get("test_metrics_by_dataset", {})),
            "final_report": report, "config": config,
            "last_checkpoint": str(run_dir / "last.pt") if (run_dir / "last.pt").is_file() and not report else None,
            "best_model": str(run_dir / "best_model.pt") if (run_dir / "best_model.pt").is_file() else None,
            "epoch_checkpoints": checkpoints, "error": live.get("error"), "console": console,
            "log_text": _tail_file(run_dir / "run.log") or "\n".join(console), "command": command}


def _temporal_shift_dataset_summary() -> dict[str, Any]:
    import mc3_18_temporal_shift_letterbox_pipeline as profile

    summary = profile.quick_inventory()
    output_root = DEFAULT_TEMPORAL_SHIFT_OUTPUT_ROOT.resolve()
    return {**summary, "ready": bool(summary.get("ready")),
            "output_root": str(output_root),
            "cache_dir": str((output_root / "_frame_cache" / profile.CACHE_PROTOCOL).resolve())}


def _temporal_shift_training_arguments(payload: dict[str, Any], pause_file: Path) -> tuple[list[str], Path]:
    import mc3_18_temporal_shift_letterbox_pipeline as profile

    output_root = DEFAULT_TEMPORAL_SHIFT_OUTPUT_ROOT.resolve()
    resume_value = str(payload.get("resume_checkpoint") or "").strip()
    saved: dict[str, Any] | None = None
    if resume_value:
        checkpoint = _require_child_path(resume_value, str(output_root))
        if checkpoint.name != "last.pt" or not checkpoint.is_file():
            raise ValueError("只能从本方案输出目录内所选运行的 last.pt 恢复。")
        if (checkpoint.parent / "final_report.json").is_file():
            raise ValueError("该运行已完成最终测试，不能恢复；请启动新运行。")
        saved = _read_json(checkpoint.parent / "config.json")
        required = {"experiment_protocol", "input_representation", "epochs", "patience",
                    "lr_scheduler_no_improvement_epochs", "lr_scheduler_factor", "min_lr", "min_delta",
                    "backbone_lr", "head_lr", "weight_decay", "grad_accum", "window_batch_size", "seed",
                    "amp", "device", "protocol", "initialization_mode", "initialization", "dataset_fingerprint"}
        if saved is None or required.difference(saved):
            raise ValueError("last.pt 缺少完整的时间偏移训练配置，不能安全恢复。")
        if (saved["experiment_protocol"] != TEMPORAL_SHIFT_EXPERIMENT_PROTOCOL
                or saved["input_representation"] != "native_video_letterbox_160x160_rgb_mean"
                or saved["initialization_mode"] != "kinetics400"
                or saved["initialization"].get("weights") != "MC3_18_Weights.KINETICS400_V1"):
            raise ValueError("该 checkpoint 不属于四数据集 160×160 时间偏移训练方案。")
        run_dir = checkpoint.parent
    else:
        run_dir = output_root / f"run_temporal_shift_letterbox_ui_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    integer_keys = {"epochs", "patience", "lr_reduce_after", "grad_accum", "window_batch_size", "seed"}
    saved_names = {"lr_reduce_after": "lr_scheduler_no_improvement_epochs",
                   "lr_factor": "lr_scheduler_factor"}
    params: dict[str, int | float] = {}
    for key, default in TEMPORAL_SHIFT_DEFAULT_CONFIG.items():
        value = _read_form_number(payload, key, default, integer=key in integer_keys) if saved is None else saved[saved_names.get(key, key)]
        params[key] = int(value) if key in integer_keys else float(value)
    if not 1 <= params["epochs"] <= 20:
        raise ValueError("Epoch 数必须介于 1 到 20；本页面上限为 20 Epoch。")
    if not 1 <= params["patience"] <= params["epochs"]:
        raise ValueError("Early-stop patience 必须介于 1 到 Epoch 上限之间。")
    if (not 1 <= params["lr_reduce_after"] <= 20 or not 1 <= params["grad_accum"] <= 128
            or not 1 <= params["window_batch_size"] <= 8):
        raise ValueError("学习率 patience 必须为 1–20，梯度累积为 1–128，窗口 batch 为 1–8。")
    if not 0 <= params["seed"] <= 2**32 - 1:
        raise ValueError("Seed 必须介于 0 到 2^32−1。")
    float_keys = ("min_delta", "backbone_lr", "head_lr", "weight_decay", "lr_factor", "min_lr")
    if not all(math.isfinite(float(params[key])) for key in float_keys):
        raise ValueError("超参数必须为有限数值。")
    if (not 0 <= params["min_delta"] <= 1 or not 0 < params["backbone_lr"] <= 1
            or not 0 < params["head_lr"] <= 1 or not 0 <= params["weight_decay"] <= 1):
        raise ValueError("请检查 min_delta、学习率和 weight decay 的范围。")
    if not 0 < params["lr_factor"] < 1 or not 0 <= params["min_lr"] <= min(params["backbone_lr"], params["head_lr"]):
        raise ValueError("LR factor 必须在 0–1 之间，min LR 不得高于初始学习率。")
    device_value = str(saved.get("device", "cuda") if saved else payload.get("device", "cuda"))
    device = "cuda" if device_value.startswith("cuda") else device_value
    if device not in {"cuda", "cpu"}:
        raise ValueError("设备必须选择 CUDA 或 CPU。")
    amp = bool(saved.get("amp", True) if saved else payload.get("amp", True))
    allow_cpu = device == "cpu" if saved else bool(payload.get("allow_cpu", False))
    if device == "cpu" and not allow_cpu:
        raise ValueError("CPU 训练较慢；请明确勾选 CPU 确认项。")

    output_root.mkdir(parents=True, exist_ok=True)
    cache_root = output_root / "_frame_cache" / profile.CACHE_PROTOCOL
    args = ["--dataset-root", str(profile.DATA_ROOT), "--base-manifest", str(profile.BASE_MANIFEST),
            "--dancetrack-manifest", "", "--initialization-mode", "kinetics400",
            "--output-root", str(output_root), "--run-dir", str(run_dir), "--cache-dir", str(cache_root),
            "--pause-file", str(pause_file), "--device", device,
            "--epochs", str(params["epochs"]), "--patience", str(params["patience"]),
            "--lr-reduce-after", str(params["lr_reduce_after"]), "--min-delta", str(params["min_delta"]),
            "--backbone-lr", str(params["backbone_lr"]), "--head-lr", str(params["head_lr"]),
            "--weight-decay", str(params["weight_decay"]), "--grad-accum", str(params["grad_accum"]),
            "--window-batch-size", str(params["window_batch_size"]), "--seed", str(params["seed"]),
            "--lr-factor", str(params["lr_factor"]), "--min-lr", str(params["min_lr"])]
    if resume_value:
        args.extend(["--resume", resume_value])
    if allow_cpu:
        args.append("--allow-cpu")
    if not amp:
        args.append("--no-amp")
    return args, run_dir


def _drain_temporal_shift_stdout(process: subprocess.Popen[str]) -> None:
    if process.stdout is not None:
        for line in process.stdout:
            with TEMPORAL_SHIFT_STATE.lock:
                TEMPORAL_SHIFT_STATE.console.append(line.rstrip("\r\n"))
    return_code = process.wait()
    with TEMPORAL_SHIFT_STATE.lock:
        run_dir = TEMPORAL_SHIFT_STATE.run_dir
        if TEMPORAL_SHIFT_STATE.process is process:
            if TEMPORAL_SHIFT_STATE.stop_requested:
                TEMPORAL_SHIFT_STATE.status = "stopped"
            elif run_dir and (run_dir / "final_report.json").is_file():
                TEMPORAL_SHIFT_STATE.status = "completed" if return_code == 0 else "failed"
            elif run_dir and (run_dir / "last.pt").is_file() and return_code == 0:
                TEMPORAL_SHIFT_STATE.status = "paused"
            else:
                TEMPORAL_SHIFT_STATE.status = "failed"


def _start_temporal_shift_training(payload: dict[str, Any]) -> dict[str, Any]:
    inventory = _temporal_shift_dataset_summary()
    if not inventory.get("ready"):
        raise RuntimeError("四数据集来源、官方 split 或 ±2 源帧所需图片未通过预检；训练尚未启动。")
    with STATE.lock:
        inference_active = STATE.inference.get("status") in {"running", "queued"}
    output_root = DEFAULT_TEMPORAL_SHIFT_OUTPUT_ROOT.resolve()
    control_root = output_root / ".dashboard_control"
    control_root.mkdir(parents=True, exist_ok=True)
    pause_file = control_root / f"temporal_shift_{uuid.uuid4().hex}.pause"
    args, run_dir = _temporal_shift_training_arguments(payload, pause_file)
    with TEMPORAL_SHIFT_STATE.lock:
        if TEMPORAL_SHIFT_STATE.status == "starting":
            raise RuntimeError("四数据集时间偏移训练正在启动，请勿重复提交。")
        if TEMPORAL_SHIFT_STATE.process is not None and TEMPORAL_SHIFT_STATE.process.poll() is None:
            raise RuntimeError("四数据集时间偏移训练已经在运行。")
        if _mc3_training_active(except_name="temporal_shift"):
            raise RuntimeError("已有其他 MC3-18 训练正在运行；请先暂停或等待其结束。")
        if inference_active:
            raise RuntimeError("视频推理正在使用计算资源，请等待推理完成。")
        run_dir.mkdir(parents=True, exist_ok=True)
        TEMPORAL_SHIFT_STATE.run_dir = run_dir
        TEMPORAL_SHIFT_STATE.pause_file = pause_file
        TEMPORAL_SHIFT_STATE.status = "starting"
        TEMPORAL_SHIFT_STATE.pause_requested = False
        TEMPORAL_SHIFT_STATE.stop_requested = False
        TEMPORAL_SHIFT_STATE.started_at = time.time()
        TEMPORAL_SHIFT_STATE.console.clear()
    command = [sys.executable, "-u", str(TEMPORAL_SHIFT_TRAINING_SCRIPT), *args]
    try:
        process = subprocess.Popen(command, cwd=PROJECT_ROOT, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                   errors="replace", bufsize=1,
                                   creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    except OSError as exc:
        with TEMPORAL_SHIFT_STATE.lock:
            TEMPORAL_SHIFT_STATE.status = "failed"
        raise RuntimeError(f"无法启动四数据集时间偏移训练：{exc}") from exc
    with TEMPORAL_SHIFT_STATE.lock:
        TEMPORAL_SHIFT_STATE.process = process
        TEMPORAL_SHIFT_STATE.command = command
        TEMPORAL_SHIFT_STATE.status = "running"
    threading.Thread(target=_drain_temporal_shift_stdout, args=(process,), daemon=True,
                     name="mc3-temporal-shift-stdout").start()
    return {"status": "running", "run_dir": str(run_dir), "resuming": "--resume" in args,
            "command": subprocess.list2cmdline(command)}


def _temporal_shift_pause() -> dict[str, Any]:
    with TEMPORAL_SHIFT_STATE.lock:
        process, marker = TEMPORAL_SHIFT_STATE.process, TEMPORAL_SHIFT_STATE.pause_file
        if process is None or process.poll() is not None or marker is None:
            raise RuntimeError("当前没有正在运行的四数据集时间偏移训练。")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
        TEMPORAL_SHIFT_STATE.pause_requested = True
        TEMPORAL_SHIFT_STATE.status = "pause_requested"
    return {"status": "pause_requested", "message": "本 epoch 验证并保存权重后会安全暂停。"}


def _temporal_shift_cancel_pause() -> dict[str, Any]:
    with TEMPORAL_SHIFT_STATE.lock:
        marker = TEMPORAL_SHIFT_STATE.pause_file
        if marker:
            marker.unlink(missing_ok=True)
        TEMPORAL_SHIFT_STATE.pause_requested = False
        if TEMPORAL_SHIFT_STATE.process is not None and TEMPORAL_SHIFT_STATE.process.poll() is None:
            TEMPORAL_SHIFT_STATE.status = "running"
    return {"status": "running", "message": "已取消待执行的安全暂停请求。"}


def _temporal_shift_stop() -> dict[str, Any]:
    with TEMPORAL_SHIFT_STATE.lock:
        process = TEMPORAL_SHIFT_STATE.process
        if process is None or process.poll() is not None:
            raise RuntimeError("当前没有正在运行的四数据集时间偏移训练。")
        TEMPORAL_SHIFT_STATE.stop_requested = True
        TEMPORAL_SHIFT_STATE.status = "stopping"
        if TEMPORAL_SHIFT_STATE.pause_file:
            TEMPORAL_SHIFT_STATE.pause_file.unlink(missing_ok=True)
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=4)
    return {"status": "stopped", "message": "已请求停止；可从最近完整 epoch 的 last.pt 恢复。"}


def _temporal_shift_runs() -> list[dict[str, Any]]:
    root = DEFAULT_TEMPORAL_SHIFT_OUTPUT_ROOT
    if not root.is_dir():
        return []
    candidates = sorted((path for path in root.iterdir()
                         if path.is_dir() and path.name.startswith("run_temporal_shift_letterbox_ui_")),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    result = []
    for run_dir in candidates:
        live = _read_json(run_dir / "live_status.json") or {}
        report = _read_json(run_dir / "final_report.json") or {}
        config = _read_json(run_dir / "config.json") or {}
        with TEMPORAL_SHIFT_STATE.lock:
            active = bool(TEMPORAL_SHIFT_STATE.run_dir and TEMPORAL_SHIFT_STATE.run_dir.resolve() == run_dir.resolve()
                          and TEMPORAL_SHIFT_STATE.process is not None
                          and TEMPORAL_SHIFT_STATE.process.poll() is None)
            active_status = TEMPORAL_SHIFT_STATE.status if active else None
        checkpoint = run_dir / "last.pt"
        status = ("completed" if report else active_status or
                  ("failed" if live.get("status") == "failed" else
                   "paused" if live.get("status") == "paused" and checkpoint.is_file() else
                   "resumable" if checkpoint.is_file() else "incomplete"))
        result.append({"run_dir": str(run_dir.resolve()), "name": run_dir.name, "status": status,
                       "last_checkpoint": str(checkpoint.resolve()) if checkpoint.is_file() and not report else None,
                       "epochs": int(config.get("epochs", 20) or 20),
                       "completed_epochs": int(live.get("completed_epochs", len(_read_csv(run_dir / "training_history.csv")))),
                       "best_epoch": report.get("best_epoch", live.get("best_epoch")),
                       "best_validation_roc_auc": report.get("best_validation_roc_auc", live.get("best_val_roc_auc")),
                       "error": live.get("error")})
    return result[:40]


def _temporal_shift_status_payload(run_dir_value: str | None = None) -> dict[str, Any]:
    with TEMPORAL_SHIFT_STATE.lock:
        process = TEMPORAL_SHIFT_STATE.process
        active_dir = TEMPORAL_SHIFT_STATE.run_dir
        status = TEMPORAL_SHIFT_STATE.status
        pause_requested = TEMPORAL_SHIFT_STATE.pause_requested
        stop_requested = TEMPORAL_SHIFT_STATE.stop_requested
        started_at = TEMPORAL_SHIFT_STATE.started_at
        console = list(TEMPORAL_SHIFT_STATE.console)[-160:]
        command = list(TEMPORAL_SHIFT_STATE.command)
    if run_dir_value:
        run_dir = _require_child_path(run_dir_value, str(DEFAULT_TEMPORAL_SHIFT_OUTPUT_ROOT))
        if not run_dir.is_dir() or not run_dir.name.startswith("run_temporal_shift_letterbox_ui_"):
            raise ValueError("所选时间偏移训练运行目录不存在。")
    else:
        run_dir = active_dir
        if run_dir is None or not run_dir.exists():
            runs = _temporal_shift_runs()
            run_dir = Path(runs[0]["run_dir"]) if runs else None
    if run_dir is None:
        return {"status": status, "run_dir": None, "history": [], "console": console,
                "dataset_summary": {}, "final_report": {}, "epoch_checkpoints": [], "config": {}}
    current = bool(active_dir and active_dir.resolve() == run_dir.resolve() and process is not None)
    active = bool(current and process is not None and process.poll() is None)
    return_code = process.poll() if current and process is not None else None
    live = _read_json(run_dir / "live_status.json") or {}
    report = _read_json(run_dir / "final_report.json") or {}
    config = _read_json(run_dir / "config.json") or {}
    summary = _read_json(run_dir / "dataset_summary.json") or {}
    history = [{key: (_safe_float(value) if _safe_float(value) is not None else value)
                for key, value in row.items()} for row in _read_csv(run_dir / "training_history.csv")]
    if active:
        current_status = "pause_requested" if pause_requested else "running"
    elif stop_requested and current:
        current_status = "stopped"
    elif report.get("status") == "completed":
        current_status = "completed"
    elif live.get("status") == "failed":
        current_status = "failed"
    elif (run_dir / "last.pt").is_file() and (return_code == 0 or status == "paused"):
        current_status = "paused"
    elif (run_dir / "last.pt").is_file():
        current_status = "resumable"
    elif return_code not in (None, 0):
        current_status = "failed"
    else:
        current_status = live.get("status", "incomplete")
    checkpoint_paths = sorted(run_dir.glob("epoch_[0-9][0-9]/best_model.pt"))
    checkpoints = [{"epoch": int(path.parent.name.split("_")[-1]), "path": str(path.resolve())}
                   for path in checkpoint_paths]
    completed_epochs = len(history)
    latest = run_dir / f"epoch_{completed_epochs:02d}" if completed_epochs else None
    epoch_thresholds = (_read_json(latest / "thresholds.json") if latest and latest.is_dir() else {}) or {}
    try:
        progress = float(live.get("stage_progress", 0.0) or 0.0)
        progress = max(0.0, min(1.0, progress)) if math.isfinite(progress) else 0.0
    except (TypeError, ValueError):
        progress = 0.0
    return {"status": current_status, "process_active": active,
            "global_process_active": bool(process and process.poll() is None),
            "active_run_dir": str(active_dir.resolve()) if active_dir and active_dir.exists() else None,
            "run_dir": str(run_dir.resolve()), "return_code": return_code,
            "stage": live.get("stage", "训练、验证、最终测试均已完成" if current_status == "completed" else "准备训练"),
            "stage_progress": 1.0 if current_status == "completed" else progress,
            "current_epoch": int(live.get("epoch", completed_epochs + 1)),
            "total_epochs": int(config.get("epochs", 20) or 20), "completed_epochs": completed_epochs,
            "best_epoch": report.get("best_epoch", live.get("best_epoch")),
            "best_validation_roc_auc": report.get("best_validation_roc_auc", live.get("best_val_roc_auc")),
            "step": live.get("step"), "steps_total": live.get("steps_total"),
            "eta_seconds": live.get("eta_seconds"),
            "elapsed_seconds": max(0, int(time.time() - started_at)) if started_at and current else None,
            "dataset_summary": summary, "history": history,
            "latest_epoch": history[-1] if history else None,
            "validation_thresholds": live.get("validation_thresholds", {}),
            "epoch_validation_thresholds": epoch_thresholds.get("selection", {}),
            "epoch_validation_metrics": epoch_thresholds.get("validation_metrics", {}),
            "validation_metrics": live.get("validation_metrics", report.get("validation_metrics", {})),
            "test_metrics": live.get("test_metrics", report.get("test_metrics", {})),
            "final_report": report, "config": config,
            "last_checkpoint": str(run_dir / "last.pt") if (run_dir / "last.pt").is_file() and not report else None,
            "best_model": str(run_dir / "best_model.pt") if (run_dir / "best_model.pt").is_file() else None,
            "epoch_checkpoints": checkpoints, "error": live.get("error"), "console": console,
            "log_text": _tail_file(run_dir / "run.log") or "\n".join(console), "command": command}


def _reverse_training_arguments(payload: dict[str, Any], output_root: Path, pause_file: Path) -> tuple[list[str], Path, dict[str, str]]:
    ucf_root = Path(str(payload.get("ucf_root") or DEFAULT_REVERSE_UCF_ROOT)).expanduser().resolve()
    legacy_root = Path(str(payload.get("legacy_dataset_root") or DEFAULT_REVERSE_LEGACY_ROOT)).expanduser().resolve()
    airtlab_root = Path(str(payload.get("airtlab_root") or DEFAULT_REVERSE_AIRTLAB_ROOT)).expanduser().resolve()
    guardaid_root = Path(str(payload.get("guardaid_root") or DEFAULT_REVERSE_GUARDAID_ROOT)).expanduser().resolve()
    if not (ucf_root / "Violence").is_dir():
        raise ValueError(f"找不到 UCF Violence 文件夹：{ucf_root / 'Violence'}")
    if not (ucf_root / "Non-Violence").is_dir() and not list(ucf_root.glob("Training-Normal-Videos-Part-*.zip")):
        raise ValueError("UCF Non-Violence 文件夹与正常视频压缩包都不存在。")
    if not airtlab_root.is_dir() or not guardaid_root.is_dir():
        raise ValueError("AIRTLab 或 GuardAId 留出测试集路径不存在。")
    legacy_inventory = _legacy_dataset_inventory(legacy_root)
    if not legacy_inventory["complete"]:
        missing = [path for item in legacy_inventory["datasets"] for path in item["missing"]]
        raise ValueError("原有五个数据集的 train/val/test/Fight/NonFight 目录不完整：\n" + "\n".join(missing[:20]))
    empty = [item["name"] for item in legacy_inventory["datasets"] if item["total"] == 0]
    if empty:
        raise ValueError("以下原有数据集没有可读取的 AVI/MP4 样本：" + "、".join(empty))
    params = {
        "epochs": _read_form_number(payload, "epochs", 30, integer=True),
        "patience": _read_form_number(payload, "patience", 6, integer=True),
        "min_delta": _read_form_number(payload, "min_delta", 0.001),
        "backbone_lr": _read_form_number(payload, "backbone_lr", 2e-5),
        "head_lr": _read_form_number(payload, "head_lr", 1e-4),
        "weight_decay": _read_form_number(payload, "weight_decay", 1e-4),
        "grad_accum": _read_form_number(payload, "grad_accum", 8, integer=True),
        "window_batch_size": _read_form_number(payload, "window_batch_size", 4, integer=True),
        "seed": _read_form_number(payload, "seed", 2026, integer=True),
    }
    if any(params[key] < 1 for key in ("epochs", "patience", "grad_accum", "window_batch_size")):
        raise ValueError("Epoch、早停、梯度累积和窗口批次必须是正整数。")
    if params["backbone_lr"] <= 0 or params["head_lr"] <= 0 or params["weight_decay"] < 0 or params["min_delta"] < 0:
        raise ValueError("学习率必须为正数；weight decay 与最小提升不能小于零。")
    device = str(payload.get("device", "auto"))
    if device not in {"auto", "cuda", "cpu"}:
        raise ValueError("计算设备只能是 auto、cuda 或 cpu。")
    resume_checkpoint = str(payload.get("resume_checkpoint") or "").strip()
    if resume_checkpoint:
        checkpoint = _require_child_path(resume_checkpoint, str(output_root))
        if checkpoint.name != "last.pt" or not checkpoint.is_file():
            raise ValueError("只能从本方案输出目录中的 last.pt 恢复。")
        run_dir = checkpoint.parent
    else:
        run_dir = output_root / f"run_reverse_ui_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    output_root.mkdir(parents=True, exist_ok=True)
    args = [
        "--ucf-root", str(ucf_root), "--legacy-dataset-root", str(legacy_root),
        "--airtlab-root", str(airtlab_root), "--guardaid-root", str(guardaid_root),
        "--output-root", str(output_root), "--run-dir", str(run_dir), "--device", device,
        "--epochs", str(params["epochs"]), "--patience", str(params["patience"]), "--min-delta", str(params["min_delta"]),
        "--backbone-lr", str(params["backbone_lr"]), "--head-lr", str(params["head_lr"]),
        "--weight-decay", str(params["weight_decay"]), "--grad-accum", str(params["grad_accum"]),
        "--window-batch-size", str(params["window_batch_size"]), "--seed", str(params["seed"]),
        "--pause-file", str(pause_file),
    ]
    if resume_checkpoint:
        args.extend(["--resume", resume_checkpoint])
    cache_dir = str(payload.get("cache_dir") or "").strip()
    if cache_dir:
        args.extend(["--cache-dir", str(Path(cache_dir).expanduser().resolve())])
    if bool(payload.get("allow_cpu", False)):
        args.append("--allow-cpu")
    if not bool(payload.get("amp", True)):
        args.append("--no-amp")
    return args, run_dir, {"legacy_dataset_root": str(legacy_root), "ucf_root": str(ucf_root), "airtlab_root": str(airtlab_root), "guardaid_root": str(guardaid_root), "output_root": str(output_root)}


def _drain_reverse_stdout(process: subprocess.Popen[str]) -> None:
    stream = process.stdout
    if stream is None:
        return
    for line in stream:
        with REVERSE_STATE.lock:
            REVERSE_STATE.console.append(line.rstrip("\r\n"))
    stream.close()


def _start_reverse_training(payload: dict[str, Any]) -> dict[str, Any]:
    output_root = Path(str(payload.get("output_root") or DEFAULT_REVERSE_OUTPUT_ROOT)).expanduser().resolve()
    control_root = output_root / ".dashboard_control"
    control_root.mkdir(parents=True, exist_ok=True)
    pause_file = control_root / f"{uuid.uuid4().hex}.pause"
    args, run_dir, roots = _reverse_training_arguments(payload, output_root, pause_file)
    with STATE.lock:
        ordinary_active = STATE.train_process is not None and STATE.train_process.poll() is None
        inference_active = STATE.inference.get("status") == "running"
    with REVERSE_STATE.lock:
        if REVERSE_STATE.process is not None and REVERSE_STATE.process.poll() is None:
            raise RuntimeError("已有倒放拼接训练正在运行。")
        if ordinary_active:
            raise RuntimeError("原 1 秒训练正在运行；请先等它完成或暂停，再启动新方案。")
        if _mc3_training_active(except_name="reverse_four_second"):
            raise RuntimeError("已有其他 MC3-18 训练正在运行；请先暂停或等待其结束。")
        if inference_active:
            raise RuntimeError("视频推理正在占用本机资源；请先等推理完成。")
        REVERSE_STATE.status = "starting"
        REVERSE_STATE.run_dir = run_dir
        REVERSE_STATE.output_root = output_root
        REVERSE_STATE.pause_file = pause_file
        REVERSE_STATE.pause_requested = False
        REVERSE_STATE.stop_requested = False
        REVERSE_STATE.started_at = time.time()
        REVERSE_STATE.console.clear()
    command = [sys.executable, "-u", str(REVERSE_TRAINING_SCRIPT), *args]
    try:
        process = subprocess.Popen(
            command, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        with REVERSE_STATE.lock:
            REVERSE_STATE.status = "failed"
        raise RuntimeError(f"无法启动倒放拼接训练进程：{exc}") from exc
    with REVERSE_STATE.lock:
        REVERSE_STATE.process = process
        REVERSE_STATE.command = command
        REVERSE_STATE.status = "running"
    threading.Thread(target=_drain_reverse_stdout, args=(process,), daemon=True, name="mc3-reverse-train-stdout").start()
    return {"status": "running", "run_dir": str(run_dir), "roots": roots, "resuming": "--resume" in args, "command": subprocess.list2cmdline(command)}


def _reverse_pause() -> dict[str, Any]:
    with REVERSE_STATE.lock:
        process, marker = REVERSE_STATE.process, REVERSE_STATE.pause_file
        if process is None or process.poll() is not None or marker is None:
            raise RuntimeError("当前没有正在运行的倒放拼接训练。")
        if REVERSE_STATE.pause_requested:
            raise RuntimeError("已经请求在本轮完成后暂停。")
        marker.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        REVERSE_STATE.pause_requested = True
        REVERSE_STATE.status = "pause_requested"
    return {"status": "pause_requested", "message": "本轮验证完成并保存 last.pt 后会安全暂停。"}


def _reverse_cancel_pause() -> dict[str, Any]:
    with REVERSE_STATE.lock:
        process, marker = REVERSE_STATE.process, REVERSE_STATE.pause_file
        if process is None or process.poll() is not None or not REVERSE_STATE.pause_requested:
            raise RuntimeError("当前没有待执行的安全暂停请求。")
        if marker is not None:
            marker.unlink(missing_ok=True)
        REVERSE_STATE.pause_requested = False
        REVERSE_STATE.status = "running"
    return {"status": "running", "message": "已取消安全暂停请求。"}


def _reverse_stop() -> dict[str, Any]:
    with REVERSE_STATE.lock:
        process = REVERSE_STATE.process
        if process is None or process.poll() is not None:
            raise RuntimeError("当前没有正在运行的倒放拼接训练。")
        REVERSE_STATE.stop_requested = True
        REVERSE_STATE.status = "stopping"
        if REVERSE_STATE.pause_file is not None:
            REVERSE_STATE.pause_file.unlink(missing_ok=True)
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=4)
    with REVERSE_STATE.lock:
        REVERSE_STATE.status = "stopped"
    return {"status": "stopped", "message": "已立即停止；可以从最近完整的 last.pt 恢复。"}


def _reverse_status_payload(output_root_value: str | None = None, run_dir_value: str | None = None) -> dict[str, Any]:
    with REVERSE_STATE.lock:
        process = REVERSE_STATE.process
        active_run_dir = REVERSE_STATE.run_dir
        run_dir = active_run_dir
        output_root = Path(output_root_value).expanduser().resolve() if output_root_value else REVERSE_STATE.output_root
        status = REVERSE_STATE.status
        requested_stop = REVERSE_STATE.stop_requested
        requested_pause = REVERSE_STATE.pause_requested
        started_at = REVERSE_STATE.started_at
        console_tail = list(REVERSE_STATE.console)[-100:]
        command = list(REVERSE_STATE.command)
    if run_dir_value:
        run_dir = _require_child_path(run_dir_value, str(output_root))
        if not run_dir.is_dir() or not (run_dir / "run.log").is_file():
            raise ValueError("所选倒放拼接运行目录不存在，或缺少 run.log。")
    elif run_dir is None or (not run_dir.exists() and output_root.is_dir()):
        candidates = [path for path in output_root.iterdir() if path.is_dir() and (path / "run.log").is_file()] if output_root.is_dir() else []
        run_dir = max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None
    if run_dir is None:
        return {"status": status, "run_dir": None, "last_checkpoint": None, "console": console_tail, "history": [],
                "validation_by_dataset": [], "final_report": {}, "protocol": "reverse_concat"}

    is_current_run = bool(process is not None and active_run_dir is not None and active_run_dir.resolve() == run_dir.resolve())
    process_active = bool(is_current_run and process is not None and process.poll() is None)
    return_code = process.poll() if is_current_run else None
    requested_stop = requested_stop if is_current_run else False
    requested_pause = requested_pause if is_current_run else False
    started_at = started_at if is_current_run else None
    report = _read_json(run_dir / "final_report.json") or {}
    has_last = (run_dir / "last.pt").is_file()
    log_text = _tail_file(run_dir / "run.log") or "\n".join(console_tail)
    if is_current_run and process is not None and return_code is None:
        current_status = "pause_requested" if requested_pause else "running"
    elif requested_stop:
        current_status = "stopped"
    elif report:
        current_status = "completed"
    elif has_last and return_code == 0:
        current_status = "paused"
    elif process is not None and return_code is not None:
        current_status = "failed"
    elif has_last:
        current_status = "resumable"
    else:
        current_status = status if is_current_run and status != "idle" else "incomplete"

    history: list[dict[str, Any]] = []
    for row in _read_csv(run_dir / "training_history.csv"):
        normalized: dict[str, Any] = {}
        for key, value in row.items():
            number = _safe_float(value)
            normalized[key] = number if number is not None else value
        history.append(normalized)
    config = _read_json(run_dir / "config.json") or {}
    manifest = _read_csv(run_dir / "dataset_manifest.csv")
    sample_total = sum(row.get("eligible") == "1" for row in manifest)
    excluded_total = sum(row.get("eligible") != "1" for row in manifest)
    augmented_total = sum(row.get("reverse_concat_applied") == "1" for row in manifest)
    guardaid_unlabeled = sum(row.get("dataset") == "GuardAId" and row.get("eligible") == "1" for row in manifest)
    epochs = int(config.get("epochs", 30) or 30)
    current_epoch = int(history[-1].get("epoch", 0)) + 1 if history else 1
    total_epochs = epochs
    stage = "准备数据与模型"
    stage_progress = 0.0
    eta_seconds: float | None = None
    latest_progress: tuple[str, re.Match[str]] | None = None
    for line in reversed(log_text.splitlines()):
        for kind, pattern in (
            ("train", r"epoch\s+(\d+)/(\d+)\s+train\s+(\d+)/(\d+)\s+bags"),
            ("evaluation", r"(val|test|external_test) progress\s+(\d+)/(\d+)\s+videos"),
            ("cache", r"Frame cache progress:\s*(\d+)/(\d+)\s*\(([0-9.]+)%\)"),
            ("hybrid_cache", r"Hybrid frame cache\s+(\d+)/(\d+)"),
            ("metadata", r"Video metadata preflight\s+(\d+)/(\d+)"),
        ):
                match = re.search(pattern, line)
                if match:
                    latest_progress = (kind, match)
                    eta_match = re.search(r"ETA\s+([0-9.]+)s", line)
                    eta_seconds = _safe_float(eta_match.group(1)) if eta_match else None
                    if eta_seconds is None and kind == "cache":
                        elapsed_match = re.search(r"elapsed=([0-9.]+)s", line)
                        done, total = int(match.group(1)), int(match.group(2))
                        elapsed = _safe_float(elapsed_match.group(1)) if elapsed_match else None
                        if elapsed is not None and done > 0:
                            eta_seconds = elapsed / done * max(0, total - done)
                    break
        if latest_progress is not None:
            break

    if latest_progress and latest_progress[0] == "metadata":
        metadata_match = latest_progress[1]
        stage = "读取视频时长与建立无泄漏数据清单"
        stage_progress = int(metadata_match.group(1)) / max(1, int(metadata_match.group(2)))
    elif latest_progress and latest_progress[0] == "cache":
        cache_match = latest_progress[1]
        stage = "解码并缓存每个视频的抽样帧"
        stage_progress = int(cache_match.group(1)) / max(1, int(cache_match.group(2)))
    elif latest_progress and latest_progress[0] == "hybrid_cache":
        cache_match = latest_progress[1]
        stage = "复用两秒缓存并补齐四秒帧"
        stage_progress = int(cache_match.group(1)) / max(1, int(cache_match.group(2)))
    if latest_progress and latest_progress[0] == "train":
        train_match = latest_progress[1]
        current_epoch = int(train_match.group(1))
        total_epochs = int(train_match.group(2))
        stage_progress = int(train_match.group(3)) / max(1, int(train_match.group(4)))
        stage = "训练中"
    elif latest_progress and latest_progress[0] == "evaluation":
        validation_match = latest_progress[1]
        stage = {"val": "验证中", "test": "最终测试中", "external_test": "GuardAId 独立额外测试中"}[validation_match.group(1)]
        stage_progress = int(validation_match.group(2)) / max(1, int(validation_match.group(3)))
        current_epoch = int(history[-1].get("epoch", 0)) if history else 0
    elif history and latest_progress is None:
        current_epoch = int(history[-1].get("epoch", 0)) + 1
        stage = "整理验证阈值与报告"
        stage_progress = 0.92
    if current_status == "completed":
        stage, stage_progress = "训练、阈值校准与最终留出评估已完成", 1.0
        current_epoch = int(report.get("best_epoch", current_epoch))
    elif current_status in {"paused", "stopped", "resumable"}:
        stage = "已保存，可从最近完整 epoch 恢复" if current_status != "stopped" else "已停止，可从最近完整 epoch 恢复"
    elif current_status == "failed":
        stage = "运行失败；请查看日志末尾"
    elapsed = max(0, int(time.time() - started_at)) if started_at else None
    return {
        "status": current_status,
        "process_active": process_active,
        "is_current_run": is_current_run,
        "run_dir": str(run_dir),
        "run_name": run_dir.name,
        "last_checkpoint": str(run_dir / "last.pt") if has_last else None,
        "best_model": str(run_dir / "best_model.pt") if (run_dir / "best_model.pt").is_file() else None,
        "return_code": return_code,
        "stage": stage,
        "stage_progress": max(0.0, min(1.0, stage_progress)),
        "current_epoch": current_epoch,
        "total_epochs": total_epochs,
        "completed_epochs": len(history),
        "eta_seconds": eta_seconds,
        "elapsed_seconds": elapsed,
        "sample_total": sample_total,
        "excluded_total": excluded_total,
        "reverse_concat_sample_total": augmented_total,
        "guardaid_unlabeled_sample_total": guardaid_unlabeled,
        "manifest_count": len(manifest),
        "history": history,
        "latest_epoch": history[-1] if history else None,
        "validation_by_dataset": _validation_by_dataset(run_dir),
        "final_report": report,
        "log_text": log_text,
        "config": config,
        "command": command,
        "protocol": "reverse_concat",
    }


def _drain_stdout(process: subprocess.Popen[str]) -> None:
    stream = process.stdout
    if stream is None:
        return
    for line in stream:
        with STATE.lock:
            STATE.console.append(line.rstrip("\r\n"))
    stream.close()


def _start_training(payload: dict[str, Any]) -> dict[str, Any]:
    output_root = Path(str(payload.get("output_root") or DEFAULT_OUTPUT_ROOT)).expanduser().resolve()
    control_root = output_root / ".dashboard_control"
    control_root.mkdir(parents=True, exist_ok=True)
    pause_file = control_root / f"{uuid.uuid4().hex}.pause"
    args, run_dir, dataset_root = _training_arguments(payload, output_root, pause_file)
    if args[0] == "--dataset-root":
        saved_output_root = Path(args[args.index("--output-root") + 1]).resolve()
        control_root = saved_output_root / ".dashboard_control"
        control_root.mkdir(parents=True, exist_ok=True)
        args[args.index("--pause-file") + 1] = str(control_root / f"{uuid.uuid4().hex}.pause")
        pause_file = Path(args[args.index("--pause-file") + 1])
    with REVERSE_STATE.lock:
        reverse_active = REVERSE_STATE.process is not None and REVERSE_STATE.process.poll() is None
    if reverse_active or _mc3_training_active(except_name="ordinary"):
        raise RuntimeError("已有其他 MC3-18 训练正在运行；请先等待或安全暂停后再启动原 1 秒训练。")
    with STATE.lock:
        if STATE.train_process is not None and STATE.train_process.poll() is None:
            raise RuntimeError("A training job is already running.")
        STATE.training_status = "starting"
        STATE.run_dir = run_dir
        STATE.pause_file = pause_file
        STATE.pause_requested = False
        STATE.stop_requested = False
        STATE.console.clear()
    command = [sys.executable, "-u", str(TRAINING_SCRIPT), *args]
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
    except OSError as exc:
        with STATE.lock:
            STATE.training_status = "failed"
        raise RuntimeError(f"Could not start the training process: {exc}") from exc
    with STATE.lock:
        STATE.train_process = process
        STATE.command = command
        STATE.training_status = "running"
    threading.Thread(target=_drain_stdout, args=(process,), daemon=True, name="mc3-train-stdout").start()
    return {
        "status": "running",
        "run_dir": str(run_dir),
        "dataset_root": dataset_root,
        "command": subprocess.list2cmdline(command),
        "resuming": "--resume" in args,
    }


def _request_pause() -> dict[str, Any]:
    with STATE.lock:
        process = STATE.train_process
        marker = STATE.pause_file
        if process is None or process.poll() is not None or marker is None:
            raise RuntimeError("There is no active training process to pause.")
        if STATE.pause_requested:
            raise RuntimeError("A safe pause is already requested.")
        marker.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        STATE.pause_requested = True
        STATE.training_status = "pause_requested"
    return {"status": "pause_requested", "message": "The current epoch will finish, save last.pt and then pause before threshold calibration or test."}


def _cancel_pause() -> dict[str, Any]:
    with STATE.lock:
        process = STATE.train_process
        marker = STATE.pause_file
        if process is None or process.poll() is not None or not STATE.pause_requested:
            raise RuntimeError("There is no pending pause request to cancel.")
        if marker is not None:
            marker.unlink(missing_ok=True)
        STATE.pause_requested = False
        STATE.training_status = "running"
    return {"status": "running", "message": "Pause request cancelled."}


def _stop_training() -> dict[str, Any]:
    with STATE.lock:
        process = STATE.train_process
        if process is None or process.poll() is not None:
            raise RuntimeError("There is no active training process to stop.")
        STATE.stop_requested = True
        STATE.training_status = "stopping"
        if STATE.pause_file is not None:
            STATE.pause_file.unlink(missing_ok=True)
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=4)
    with STATE.lock:
        STATE.training_status = "stopped"
    return {"status": "stopped", "message": "Training stopped. Resume from the last.pt checkpoint shown on the page."}


def _inference_worker(
    job_id: str,
    video_path: Path,
    original_filename: str,
    checkpoint: Path,
    mode: str,
    threshold: float,
) -> None:
    def update(**values: Any) -> None:
        with STATE.lock:
            if STATE.inference.get("job_id") == job_id:
                STATE.inference.update(values)

    try:
        update(status="running", stage="读取视频与模型", progress=0.03, error=None, result=None)
        result = _predict_video_checkpoint(video_path, original_filename, checkpoint, mode, threshold, update)
        update(status="completed", stage="推理完成", progress=1.0, result=result, error=None)
    except Exception as exc:
        update(status="failed", stage="推理失败", error=str(exc), progress=0.0)
    finally:
        try:
            # The upload directory is created exclusively for this inference job;
            # skeleton frames, when used, are temporary children of that directory.
            shutil.rmtree(video_path.parent, ignore_errors=True)
        except OSError:
            pass


def _predict_video_checkpoint(
    video_path: Path,
    original_filename: str,
    checkpoint_path: Path,
    mode: str,
    threshold: float,
    progress_callback,
) -> dict[str, Any]:
    from mc3_18_mil_pipeline import (
        CACHE_PROTOCOL,
        CROP_SIZE,
        NUM_FRAMES,
        SHORT_SIDE,
        STRIDE_SECONDS,
        WINDOW_SECONDS,
        VideoRecord,
        _amp_context,
        _build_model,
        _import_runtime_dependencies,
        _predict_bag,
        _preprocessing_protocol,
        _prepare_cache,
        _record_bag_tensor,
        make_windows,
    )

    started = time.perf_counter()
    cv2, np, torch, nn, model_symbols = _import_runtime_dependencies()
    weights_enum, model_builder = model_symbols
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    amp = device.type == "cuda"
    try:
        payload = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    except TypeError:
        payload = torch.load(checkpoint_path, map_location="cpu")
    if (
        payload.get("deployment_checkpoint_version") != 1
        or payload.get("model_name") != "mc3_18"
        or payload.get("class_to_idx") != {"NonFight": 0, "Fight": 1}
    ):
        raise ValueError("This is not a supported MC3-18 deployment checkpoint.")
    expected_protocol = _preprocessing_protocol()
    saved_protocol = payload.get("protocol", {})
    if saved_protocol.get("experiment_protocol") == TEMPORAL_SHIFT_EXPERIMENT_PROTOCOL:
        import mc3_18_temporal_shift_letterbox_pipeline as profile

        expected_mean = [float(value) for value in profile.core.MEAN]
        expected_std = [float(value) for value in profile.core.STD]
        letterbox_protocol = saved_protocol.get("letterbox", {})
        if (saved_protocol.get("input_representation") != profile.INPUT_REPRESENTATION
                or float(saved_protocol.get("window_seconds", 0)) != 4.0
                or float(saved_protocol.get("stride_seconds", 0)) != 2.0
                or int(saved_protocol.get("frames_per_window", 0)) != 16
                or saved_protocol.get("input_size") != [160, 160]
                or saved_protocol.get("normalization_mean") != expected_mean
                or saved_protocol.get("normalization_std") != expected_std
                or letterbox_protocol.get("target_width") != 160
                or letterbox_protocol.get("target_height") != 160
                or letterbox_protocol.get("padding_color_rgb_0_1") != expected_mean
                or letterbox_protocol.get("alignment") != "center"
                or letterbox_protocol.get("crop") is not False):
            raise ValueError("Checkpoint does not match the four-dataset RGB-mean letterbox 160×160 protocol.")
        result = _predict_video_harvard_letterbox_checkpoint(
            video_path, original_filename, checkpoint_path, mode, threshold,
            progress_callback, payload, cv2, np, torch, nn, model_symbols,
        )
        result["protocol"] = profile.EXPERIMENT_PROTOCOL
        result["temporal_augmentation_applied"] = False
        result["inference_source_frame_offsets"] = [0]
        return result
    if saved_protocol.get("experiment_protocol") == HARVARD_LETTERBOX_EXPERIMENT_PROTOCOL:
        import mc3_18_harvard_letterbox_pipeline as profile

        expected_mean = [float(value) for value in profile.core.MEAN]
        expected_std = [float(value) for value in profile.core.STD]
        letterbox = saved_protocol.get("letterbox", {})
        if (saved_protocol.get("input_representation") != profile.INPUT_REPRESENTATION
                or float(saved_protocol.get("window_seconds", 0)) != 4.0
                or float(saved_protocol.get("stride_seconds", 0)) != 2.0
                or int(saved_protocol.get("frames_per_window", 0)) != 16
                or saved_protocol.get("input_size") != [160, 160]
                or saved_protocol.get("short_side") is not None
                or saved_protocol.get("crop_size") is not None
                or saved_protocol.get("normalization_mean") != expected_mean
                or saved_protocol.get("normalization_std") != expected_std
                or letterbox.get("target_width") != 160 or letterbox.get("target_height") != 160
                or letterbox.get("padding_color_rgb_0_1") != expected_mean
                or letterbox.get("alignment") != "center" or letterbox.get("crop") is not False
                or letterbox.get("padding_after_normalization") != "exact zero in each channel"):
            raise ValueError("The checkpoint does not match the recorded RGB-mean letterbox 160×160 4S16F2 protocol.")
        return _predict_video_harvard_letterbox_checkpoint(
            video_path, original_filename, checkpoint_path, mode, threshold,
            progress_callback, payload, cv2, np, torch, nn, model_symbols,
        )
    if saved_protocol.get("experiment_protocol") == FULL_FRAME_EXPERIMENT_PROTOCOL:
        if (saved_protocol.get("input_representation") != "native_video_full_frame_short_side_112"
                or float(saved_protocol.get("window_seconds", 0)) != 4.0
                or float(saved_protocol.get("stride_seconds", 0)) != 2.0
                or int(saved_protocol.get("frames_per_window", 0)) != 16
                or int(saved_protocol.get("short_side", 0)) != 112
                or saved_protocol.get("crop_size") is not None
                or "resize_policy" not in saved_protocol):
            raise ValueError("Full-frame short-side-112 checkpoint does not match its recorded 4S16F2 protocol.")
        return _predict_video_full_frame_checkpoint(
            video_path, original_filename, checkpoint_path, mode, threshold,
            progress_callback, payload, cv2, np, torch, nn, model_symbols,
        )
    if saved_protocol.get("experiment_protocol") == SKELETON_EXPERIMENT_PROTOCOL:
        if (
            saved_protocol.get("input_modality") != "skeleton"
            or saved_protocol.get("input_representation") != "black_background_skeleton"
            or float(saved_protocol.get("window_seconds", 0)) != 4.0
            or float(saved_protocol.get("stride_seconds", 0)) != 2.0
            or int(saved_protocol.get("frames_per_window", 0)) != 16
            or int(saved_protocol.get("short_side", 0)) != 128
            or int(saved_protocol.get("crop_size", 0)) != 112
            or saved_protocol.get("color_space") != "RGB"
        ):
            raise ValueError("Black-background skeleton checkpoint is not the supported 4s16f2 protocol.")
        return _predict_video_airtlab_skeleton_checkpoint(
            video_path, original_filename, checkpoint_path, mode, threshold,
            progress_callback, payload, cv2, np, torch, nn, model_symbols,
        )
    if saved_protocol.get("experiment_protocol") == AIRTLAB_MODALITIES_PROTOCOL:
        if (
            float(saved_protocol.get("window_seconds", 0)) != 4.0
            or float(saved_protocol.get("stride_seconds", 0)) != 2.0
            or int(saved_protocol.get("frames_per_window", 0)) != 16
            or int(saved_protocol.get("short_side", 0)) != 128
            or int(saved_protocol.get("crop_size", 0)) != 112
            or saved_protocol.get("color_space") != "RGB"
        ):
            raise ValueError("AIRT Lab checkpoint preprocessing is not the supported 4s16f2 protocol.")
        modality = saved_protocol.get("input_modality")
        if modality == "skeleton":
            return _predict_video_airtlab_skeleton_checkpoint(
                video_path, original_filename, checkpoint_path, mode, threshold,
                progress_callback, payload, cv2, np, torch, nn, model_symbols,
            )
        if modality == "native":
            result = _predict_video_four_second_checkpoint(
                video_path, original_filename, checkpoint_path, mode, threshold,
                progress_callback, payload, cv2, np, torch, nn, model_symbols,
            )
            result["input_representation"] = "native_video"
            return result


        raise ValueError(f"Unsupported AIRT Lab model input modality: {modality!r}")
    if (
        float(saved_protocol.get("window_seconds", 0)) == 4.0
        and float(saved_protocol.get("stride_seconds", 0)) == 2.0
        and int(saved_protocol.get("frames_per_window", 0)) == 16
        and int(saved_protocol.get("short_side", 0)) == 128
        and int(saved_protocol.get("crop_size", 0)) == 112
        and saved_protocol.get("color_space") == "RGB"
    ):
        return _predict_video_four_second_checkpoint(
            video_path,
            original_filename,
            checkpoint_path,
            mode,
            threshold,
            progress_callback,
            payload,
            cv2,
            np,
            torch,
            nn,
            model_symbols,
        )
    if (
        float(saved_protocol.get("window_seconds", 0)) == 2.0
        and float(saved_protocol.get("stride_seconds", 0)) == 1.0
        and int(saved_protocol.get("frames_per_window", 0)) == 8
        and int(saved_protocol.get("short_side", 0)) == 128
        and int(saved_protocol.get("crop_size", 0)) == 112
        and saved_protocol.get("color_space") == "RGB"
    ):
        return _predict_video_reverse_concat_checkpoint(
            video_path,
            original_filename,
            checkpoint_path,
            mode,
            threshold,
            progress_callback,
            payload,
            cv2,
            np,
            torch,
            nn,
            model_symbols,
        )
    if any(saved_protocol.get(key) != value for key, value in expected_protocol.items()):
        raise ValueError("Checkpoint preprocessing differs from the 1-second/0.5-second/4-frame UI protocol.")

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"OpenCV could not open the uploaded video: {video_path.name}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.release()
    if not fps > 0 or frame_count <= 0:
        raise RuntimeError("The video has invalid FPS or frame-count metadata.")
    duration = frame_count / fps
    windows = make_windows(duration, fps, frame_count, include_training_tail=False)
    if not windows:
        raise ValueError(f"This video is {duration:.2f}s long. The selected model needs at least one complete 1-second window.")
    stat = video_path.stat()
    record = VideoRecord(
        dataset="uploaded_video",
        split="inference",
        class_name="Unknown",
        label=0,
        path=video_path.resolve(),
        fps=fps,
        frame_count=frame_count,
        duration=duration,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )
    progress_callback(stage="准备窗口帧", progress=0.12, processed_windows=0, total_windows=len(windows))
    with tempfile.TemporaryDirectory(prefix="mc3_infer_cache_") as cache_dir:
        _prepare_cache([record], Path(cache_dir), cv2, np, logging.getLogger("mc3_dashboard_inference"), rebuild=True)
        clips, windows = _record_bag_tensor(record, training=False, epoch=0, seed=0, np=np, cv2=cv2, torch=torch)
    progress_callback(stage="加载 MC3-18", progress=0.20, processed_windows=0, total_windows=len(windows))
    model = _build_model(torch, nn, weights_enum, model_builder, device, pretrained=False)
    model.load_state_dict(payload["state_dict"])
    model.eval()
    probabilities: list[float] = []
    batch_size = 8
    with torch.inference_mode():
        for start in range(0, clips.shape[0], batch_size):
            batch = clips[start : start + batch_size].to(device, non_blocking=True)
            with _amp_context(torch, device, amp):
                logits = model(batch)
            window_probs = torch.softmax(logits.float(), dim=1)[:, 1].cpu().tolist()
            probabilities.extend(float(score) for score in window_probs)
            done = len(probabilities)
            progress_callback(
                stage="逐窗口推理",
                progress=0.2 + 0.78 * done / len(windows),
                processed_windows=done,
                total_windows=len(windows),
            )
    score = max(probabilities)
    return {
        "filename": Path(original_filename).name,
        "duration_seconds": duration,
        "window_seconds": WINDOW_SECONDS,
        "stride_seconds": STRIDE_SECONDS,
        "frames_per_window": NUM_FRAMES,
        "short_side": SHORT_SIDE,
        "crop_size": CROP_SIZE,
        "mode": mode,
        "threshold": threshold,
        "probability_fight": score,
        "prediction": "Fight" if score >= threshold else "NonFight",
        "window_count": len(windows),
        "elapsed_seconds": time.perf_counter() - started,
        "device": str(device),
        "windows": [
            {
                "start_seconds": window.start,
                "end_seconds": window.end,
                "probability_fight": probability,
                "prediction": "Fight" if probability >= threshold else "NonFight",
            }
            for window, probability in zip(windows, probabilities)
        ],
        "protocol": CACHE_PROTOCOL,
    }


def _predict_video_harvard_letterbox_checkpoint(
    video_path: Path,
    original_filename: str,
    checkpoint_path: Path,
    mode: str,
    threshold: float,
    progress_callback,
    payload: dict[str, Any],
    cv2: Any,
    np: Any,
    torch: Any,
    nn: Any,
    model_symbols: tuple[Any, Any],
) -> dict[str, Any]:
    """Apply the 4S16F2 RGB-mean letterbox transform recorded by this model."""
    import mc3_18_harvard_letterbox_pipeline as profile
    import mc3_18_mil_pipeline as core
    import mc3_18_four_second_pipeline as four

    started = time.perf_counter()
    weights_enum, model_builder = model_symbols
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    amp = device.type == "cuda"
    model = core._build_model(torch, nn, weights_enum, model_builder, device, pretrained=False)
    model.load_state_dict(payload["state_dict"])
    model.eval()
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"OpenCV could not open the uploaded video: {video_path.name}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0 or frame_count <= 0:
            raise RuntimeError("The video has invalid FPS or frame-count metadata.")
        duration = frame_count / fps
        windows = four.make_four_second_windows(duration, fps, frame_count)
        if not windows:
            raise ValueError(f"This video is {duration:.2f}s long; at least {four.MIN_SCAN_SECONDS:.0f}s is required.")
        scores: list[float] = []
        window_rows: list[dict[str, Any]] = []
        progress_callback(stage="按 4S16F2 抽帧并准备 160×160 均值补边", progress=0.08,
                          processed_windows=0, total_windows=len(windows))
        with torch.inference_mode():
            for index, window in enumerate(windows, start=1):
                frames = []
                for frame_index in window.frame_indices:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
                    ok, bgr = capture.read()
                    if not ok or bgr is None:
                        raise RuntimeError(f"Could not decode sampled frame {frame_index} for window {index}.")
                    frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                clip = profile.frames_to_clip(frames, np=np, torch=torch, cv2=cv2, training=False)
                with core._amp_context(torch, device, amp):
                    logits = model(clip.unsqueeze(0).to(device, non_blocking=True))
                probability = float(torch.softmax(logits.float(), dim=1)[0, 1].item())
                scores.append(probability)
                window_rows.append({"start_seconds": window.start, "end_seconds": window.end,
                                    "probability_fight": probability,
                                    "prediction": "Fight" if probability >= threshold else "NonFight"})
                progress_callback(stage="均值补边窗口推理", progress=0.08 + 0.9 * index / len(windows),
                                  processed_windows=index, total_windows=len(windows))
    finally:
        capture.release()
    score = max(scores)
    return {"filename": Path(original_filename).name, "duration_seconds": duration,
            "window_seconds": 4.0, "stride_seconds": 2.0, "frames_per_window": 16,
            "input_size": [160, 160], "letterbox": True,
            "padding_color_rgb_0_1": [float(value) for value in core.MEAN],
            "resize_policy": "preserve full RGB frame aspect ratio, fit within 160×160, center-pad with normalization mean",
            "mode": mode, "threshold": threshold, "probability_fight": score,
            "prediction": "Fight" if score >= threshold else "NonFight", "window_count": len(windows),
            "elapsed_seconds": time.perf_counter() - started, "device": str(device),
            "input_representation": profile.INPUT_REPRESENTATION, "windows": window_rows,
            "protocol": profile.EXPERIMENT_PROTOCOL}


def _predict_video_full_frame_checkpoint(
    video_path: Path,
    original_filename: str,
    checkpoint_path: Path,
    mode: str,
    threshold: float,
    progress_callback,
    payload: dict[str, Any],
    cv2: Any,
    np: Any,
    torch: Any,
    nn: Any,
    model_symbols: tuple[Any, Any],
) -> dict[str, Any]:
    """Run the recorded full-frame 4S16F2 transform, retaining every image edge."""
    import mc3_18_four_second_pipeline as four
    import mc3_18_full_frame_pipeline as full_frame
    import mc3_18_mil_pipeline as core

    started = time.perf_counter()
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    amp = device.type == "cuda"
    weights_enum, model_builder = model_symbols
    model = core._build_model(torch, nn, weights_enum, model_builder, device, pretrained=False)
    model.load_state_dict(payload["state_dict"])
    model.eval()
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"OpenCV could not open the uploaded video: {video_path.name}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0 or frame_count <= 0:
            raise RuntimeError("The video has invalid FPS or frame-count metadata.")
        duration = frame_count / fps
        windows = four.make_four_second_windows(duration, fps, frame_count)
        if not windows:
            raise ValueError(f"This video is {duration:.2f}s long; at least {four.MIN_SCAN_SECONDS:.0f}s is required.")
        scores: list[float] = []
        window_rows: list[dict[str, Any]] = []
        progress_callback(stage="按 4S16F2 读取完整画面", progress=0.08, processed_windows=0,
                          total_windows=len(windows))
        for index, window in enumerate(windows, start=1):
            frames = []
            for frame_index in window.frame_indices:
                capture.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
                ok, bgr = capture.read()
                if not ok or bgr is None:
                    raise RuntimeError(f"Could not decode sampled frame {frame_index} for window {index}.")
                frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            clip = full_frame.frames_to_clip(frames, np=np, torch=torch, cv2=cv2, training=False)
            batch = clip.unsqueeze(0).to(device, non_blocking=True)
            with core._amp_context(torch, device, amp):
                logits = model(batch)
            probability = float(torch.softmax(logits.float(), dim=1)[0, 1].item())
            scores.append(probability)
            window_rows.append({"start_seconds": window.start, "end_seconds": window.end,
                                "probability_fight": probability,
                                "prediction": "Fight" if probability >= threshold else "NonFight"})
            progress_callback(stage="完整画面窗口推理", progress=0.08 + 0.9 * index / len(windows),
                              processed_windows=index, total_windows=len(windows))
    finally:
        capture.release()
    score = max(scores)
    return {
        "filename": Path(original_filename).name,
        "duration_seconds": duration,
        "window_seconds": 4.0,
        "stride_seconds": 2.0,
        "frames_per_window": 16,
        "short_side": 112,
        "crop_size": None,
        "resize_policy": "keep full frame, preserve aspect ratio, no crop",
        "mode": mode,
        "threshold": threshold,
        "probability_fight": score,
        "prediction": "Fight" if score >= threshold else "NonFight",
        "window_count": len(windows),
        "elapsed_seconds": time.perf_counter() - started,
        "device": str(device),
        "input_representation": "native_video_full_frame_short_side_112",
        "windows": window_rows,
        "protocol": FULL_FRAME_EXPERIMENT_PROTOCOL,
    }


def _predict_video_reverse_concat_checkpoint(
    video_path: Path,
    original_filename: str,
    checkpoint_path: Path,
    mode: str,
    threshold: float,
    progress_callback,
    payload: dict[str, Any],
    cv2: Any,
    np: Any,
    torch: Any,
    nn: Any,
    model_symbols: tuple[Any, Any],
) -> dict[str, Any]:
    """Infer with the isolated 2-second/1-second-stride/8-frame protocol."""
    import mc3_18_mil_pipeline as core

    started = time.perf_counter()
    weights_enum, model_builder = model_symbols
    protocol = payload["protocol"]
    window_seconds = 2.0
    stride_seconds = 1.0
    num_frames = 8
    short_side = int(protocol["short_side"])
    crop_size = int(protocol["crop_size"])
    mean = np.asarray(protocol["normalization_mean"], dtype=np.float32).reshape(1, 1, 3)
    std = np.asarray(protocol["normalization_std"], dtype=np.float32).reshape(1, 1, 3)

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    amp = device.type == "cuda"
    model = core._build_model(torch, nn, weights_enum, model_builder, device, pretrained=False)
    model.load_state_dict(payload["state_dict"])
    model.eval()
    progress_callback(stage="读取 2 秒视频窗口", progress=0.06, processed_windows=0, total_windows=0)

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"OpenCV could not open the uploaded video: {video_path.name}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0 or frame_count <= 0:
            raise RuntimeError("The video has invalid FPS or frame-count metadata.")
        duration = frame_count / fps
        windows: list[tuple[float, float, tuple[int, ...]]] = []
        start = 0.0
        while start + window_seconds <= duration + 1e-9:
            indices = tuple(
                max(
                    0,
                    min(
                        frame_count - 1,
                        int(math.floor((start + (index + 0.5) * window_seconds / num_frames) * fps)),
                    ),
                )
                for index in range(num_frames)
            )
            windows.append((round(start, 3), round(start + window_seconds, 3), indices))
            start += stride_seconds
        if not windows:
            raise ValueError(
                f"This video is {duration:.2f}s long. The selected model needs at least one complete 2-second window."
            )

        progress_callback(stage="加载 MC3-18 2 秒模型", progress=0.12, processed_windows=0, total_windows=len(windows))
        frame_store: dict[int, Any] = {}
        next_frame_index = 0
        probabilities: list[float] = []
        batch_size = 4
        for batch_start in range(0, len(windows), batch_size):
            batch_windows = windows[batch_start : batch_start + batch_size]
            requested = sorted({index for _, _, frame_indices in batch_windows for index in frame_indices})
            requested_set = set(requested)
            missing_old = [index for index in requested if index < next_frame_index and index not in frame_store]
            if missing_old:
                raise RuntimeError(f"Internal frame buffer lost requested video frames: {missing_old[:5]}")
            final_requested = requested[-1]
            while next_frame_index <= final_requested:
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(
                        f"Could not sequentially decode frame {next_frame_index} from {video_path.name}."
                    )
                if next_frame_index in requested_set:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    frame_store[next_frame_index] = core._resize_short_side(rgb, cv2)
                next_frame_index += 1

            clips = []
            for _, _, frame_indices in batch_windows:
                resized = [frame_store[index] for index in frame_indices]
                height, width = resized[0].shape[:2]
                if height < crop_size or width < crop_size:
                    raise ValueError(f"Input frame is smaller than the {crop_size}x{crop_size} crop after resize.")
                top, left = (height - crop_size) // 2, (width - crop_size) // 2
                clip = np.stack(
                    [frame[top : top + crop_size, left : left + crop_size] for frame in resized],
                    axis=0,
                ).astype(np.float32)
                clip /= 255.0
                clip = (clip - mean) / std
                clip = clip.transpose(3, 0, 1, 2)
                clips.append(torch.from_numpy(np.ascontiguousarray(clip, dtype=np.float32)))
            batch = torch.stack(clips, dim=0).to(device, non_blocking=True)
            with torch.inference_mode():
                with core._amp_context(torch, device, amp):
                    logits = model(batch)
                batch_probabilities = torch.softmax(logits.float(), dim=1)[:, 1].cpu().tolist()
            probabilities.extend(float(value) for value in batch_probabilities)

            next_batch_start = batch_start + batch_size
            if next_batch_start < len(windows):
                next_minimum = min(min(indices) for _, _, indices in windows[next_batch_start : next_batch_start + batch_size])
                frame_store = {index: frame for index, frame in frame_store.items() if index >= next_minimum}
            processed = len(probabilities)
            progress_callback(
                stage="逐窗口推理（2 秒协议）",
                progress=0.12 + 0.86 * processed / len(windows),
                processed_windows=processed,
                total_windows=len(windows),
            )
        score = max(probabilities)
        stat = video_path.stat()
        return {
            "filename": Path(original_filename).name,
            "duration_seconds": duration,
            "window_seconds": window_seconds,
            "stride_seconds": stride_seconds,
            "frames_per_window": num_frames,
            "short_side": short_side,
            "crop_size": crop_size,
            "mode": mode,
            "threshold": threshold,
            "probability_fight": score,
            "prediction": "Fight" if score >= threshold else "NonFight",
            "window_count": len(windows),
            "elapsed_seconds": time.perf_counter() - started,
            "device": str(device),
            "windows": [
                {
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                    "probability_fight": probability,
                    "prediction": "Fight" if probability >= threshold else "NonFight",
                }
                for (start_seconds, end_seconds, _), probability in zip(windows, probabilities)
            ],
            "protocol": "mc3-2s-1s-stride-8f-128short-112crop-reverse-concat-v2",
            "file_size_bytes": stat.st_size,
        }
    finally:
        capture.release()


def _predict_video_four_second_checkpoint(
    video_path: Path,
    original_filename: str,
    checkpoint_path: Path,
    mode: str,
    threshold: float,
    progress_callback,
    payload: dict[str, Any],
    cv2: Any,
    np: Any,
    torch: Any,
    nn: Any,
    model_symbols: tuple[Any, Any],
) -> dict[str, Any]:
    """Infer with four-second windows, a 2-second stride, and a short tail."""
    import mc3_18_mil_pipeline as core
    from mc3_18_four_second_pipeline import PROTOCOL as FOUR_SECOND_PROTOCOL
    from mc3_18_four_second_pipeline import make_four_second_windows

    started = time.perf_counter()
    weights_enum, model_builder = model_symbols
    protocol = payload["protocol"]
    window_seconds = 4.0
    stride_seconds = 2.0
    num_frames = 16
    short_side = int(protocol["short_side"])
    crop_size = int(protocol["crop_size"])
    mean = np.asarray(protocol["normalization_mean"], dtype=np.float32).reshape(1, 1, 3)
    std = np.asarray(protocol["normalization_std"], dtype=np.float32).reshape(1, 1, 3)
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    amp = device.type == "cuda"
    model = core._build_model(torch, nn, weights_enum, model_builder, device, pretrained=False)
    model.load_state_dict(payload["state_dict"])
    model.eval()
    progress_callback(stage="读取 4 秒窗口与短尾片段", progress=0.06, processed_windows=0, total_windows=0)

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"OpenCV could not open the uploaded video: {video_path.name}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0 or frame_count <= 0:
            raise RuntimeError("The video has invalid FPS or frame-count metadata.")
        duration = frame_count / fps
        windows = make_four_second_windows(duration, fps, frame_count)
        if not windows:
            raise ValueError("视频无法生成有效的四秒窗口或短尾窗口。")
        progress_callback(stage="加载 MC3-18 四秒模型", progress=0.12, processed_windows=0, total_windows=len(windows))
        frame_store: dict[int, Any] = {}
        next_frame_index = 0
        probabilities: list[float] = []
        batch_size = 2
        for batch_start in range(0, len(windows), batch_size):
            batch_windows = windows[batch_start : batch_start + batch_size]
            requested = sorted({index for window in batch_windows for index in window.frame_indices})
            requested_set = set(requested)
            missing_old = [index for index in requested if index < next_frame_index and index not in frame_store]
            if missing_old:
                raise RuntimeError(f"Internal frame buffer lost requested video frames: {missing_old[:5]}")
            next_batch = batch_start + batch_size
            next_minimum = None
            if next_batch < len(windows):
                next_minimum = min(
                    min(window.frame_indices)
                    for window in windows[next_batch : next_batch + batch_size]
                )
            while next_frame_index <= requested[-1]:
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(f"Could not sequentially decode frame {next_frame_index} from {video_path.name}.")
                # Keep every decoded frame in the overlap needed by the next
                # batch. An end-aligned short-tail window may start between
                # regular stride boundaries, so its 16 frame indices are not
                # guaranteed to match the previous window's sampling grid.
                if next_frame_index in requested_set or (
                    next_minimum is not None and next_frame_index >= next_minimum
                ):
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    frame_store[next_frame_index] = core._resize_short_side(rgb, cv2)
                next_frame_index += 1

            clips = []
            for window in batch_windows:
                resized = [frame_store[index] for index in window.frame_indices]
                height, width = resized[0].shape[:2]
                if height < crop_size or width < crop_size:
                    raise ValueError(f"Input frame is smaller than the {crop_size}x{crop_size} crop after resize.")
                top, left = (height - crop_size) // 2, (width - crop_size) // 2
                clip = np.stack([frame[top : top + crop_size, left : left + crop_size] for frame in resized], axis=0).astype(np.float32)
                clip /= 255.0
                clip = ((clip - mean) / std).transpose(3, 0, 1, 2)
                clips.append(torch.from_numpy(np.ascontiguousarray(clip, dtype=np.float32)))
            batch = torch.stack(clips, dim=0).to(device, non_blocking=True)
            with torch.inference_mode():
                with core._amp_context(torch, device, amp):
                    logits = model(batch)
                probabilities.extend(float(value) for value in torch.softmax(logits.float(), dim=1)[:, 1].cpu().tolist())

            if next_minimum is not None:
                frame_store = {index: frame for index, frame in frame_store.items() if index >= next_minimum}
            progress_callback(
                stage="逐窗口推理（4 秒协议）",
                progress=0.12 + 0.86 * len(probabilities) / len(windows),
                processed_windows=len(probabilities),
                total_windows=len(windows),
            )
        score = max(probabilities)
        stat = video_path.stat()
        return {
            "filename": Path(original_filename).name,
            "duration_seconds": duration,
            "window_seconds": window_seconds,
            "stride_seconds": stride_seconds,
            "frames_per_window": num_frames,
            "short_side": short_side,
            "crop_size": crop_size,
            "mode": mode,
            "threshold": threshold,
            "probability_fight": score,
            "prediction": "Fight" if score >= threshold else "NonFight",
            "window_count": len(windows),
            "elapsed_seconds": time.perf_counter() - started,
            "device": str(device),
            "tail_policy": "short clips sampled uniformly over real duration; long clips add one end-aligned tail when needed",
            "windows": [
                {"start_seconds": window.start, "end_seconds": window.end,
                 "probability_fight": probability,
                 "prediction": "Fight" if probability >= threshold else "NonFight"}
                for window, probability in zip(windows, probabilities)
            ],
            "protocol": FOUR_SECOND_PROTOCOL,
            "file_size_bytes": stat.st_size,
        }
    finally:
        capture.release()


def _load_pose_pipeline_module() -> Any:
    global _POSE_PIPELINE_MODULE
    with _POSE_PIPELINE_LOCK:
        if _POSE_PIPELINE_MODULE is not None:
            return _POSE_PIPELINE_MODULE
        pipeline_path = PROJECT_ROOT / "pose_inference_dashboard" / "pipeline.py"
        if not pipeline_path.is_file():
            raise FileNotFoundError(f"RTMPose pipeline is missing: {pipeline_path}")
        module_name = "mc3_dashboard_pose_pipeline"
        spec = importlib.util.spec_from_file_location(module_name, pipeline_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load the RTMPose pipeline from {pipeline_path}.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        _POSE_PIPELINE_MODULE = module
        return module


def _get_pose_engine(progress_callback) -> tuple[Any, Any]:
    global _POSE_ENGINE
    with _POSE_ENGINE_LOCK:
        pipeline = _load_pose_pipeline_module()
        if _POSE_ENGINE is not None:
            return pipeline, _POSE_ENGINE

        def report_status(message: str) -> None:
            progress_callback(stage="准备 RTMPose 人物骨架模型", progress=0.06, detail=message)

        try:
            engine = pipeline.PoseEngine.load("auto", status=report_status)
        except Exception as auto_error:
            progress_callback(
                stage="RTMPose 自动设备初始化失败，尝试 CPU",
                progress=0.06,
                detail=str(auto_error)[-500:],
            )
            try:
                engine = pipeline.PoseEngine.load("cpu", status=report_status)
            except Exception as cpu_error:
                raise RuntimeError(
                    "RTMPose/YOLOX 初始化失败；自动设备错误："
                    f"{auto_error}；CPU 重试错误：{cpu_error}"
                ) from cpu_error
        _POSE_ENGINE = engine
        return pipeline, engine


def _predict_video_airtlab_skeleton_checkpoint(
    video_path: Path,
    original_filename: str,
    checkpoint_path: Path,
    mode: str,
    threshold: float,
    progress_callback,
    payload: dict[str, Any],
    cv2: Any,
    np: Any,
    torch: Any,
    nn: Any,
    model_symbols: tuple[Any, Any],
) -> dict[str, Any]:
    """Extract the training-style black-background skeleton frames, then infer with MC3-18."""
    import mc3_18_mil_pipeline as core
    from mc3_18_four_second_pipeline import PROTOCOL as FOUR_SECOND_PROTOCOL
    from mc3_18_four_second_pipeline import make_four_second_windows

    started = time.perf_counter()
    protocol = payload["protocol"]
    weights_enum, model_builder = model_symbols
    window_seconds, stride_seconds, num_frames = 4.0, 2.0, 16
    short_side, crop_size = int(protocol["short_side"]), int(protocol["crop_size"])
    mean = np.asarray(protocol["normalization_mean"], dtype=np.float32).reshape(1, 1, 3)
    std = np.asarray(protocol["normalization_std"], dtype=np.float32).reshape(1, 1, 3)

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"OpenCV could not open the uploaded video: {video_path.name}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0 or frame_count <= 0:
            raise RuntimeError("The video has invalid FPS or frame-count metadata.")
        duration = frame_count / fps
        windows = make_four_second_windows(duration, fps, frame_count)
        if not windows:
            raise ValueError("视频时长不足或无法按 4s16f2 规则生成有效窗口。")
        requested_indices = sorted({int(index) for window in windows for index in window.frame_indices})
        if not requested_indices:
            raise RuntimeError("4s16f2 方案没有生成需要抽取的骨架帧。")

        progress_callback(
            stage="加载黑底骨架提取模型",
            progress=0.04,
            processed_windows=0,
            total_windows=len(windows),
            detail=f"视频 {duration:.2f} 秒；将抽取 {len(requested_indices)} 张唯一采样帧。",
        )
        pose_pipeline, pose_engine = _get_pose_engine(progress_callback)
        frame_dir = video_path.parent / "skeleton_frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        people_frames = 0
        people_total = 0
        next_frame_index = 0
        for ordinal, frame_index in enumerate(requested_indices, start=1):
            decoded_index = -1
            while next_frame_index <= frame_index:
                ok, bgr = capture.read()
                if not ok:
                    raise RuntimeError(
                        f"骨架抽取时无法顺序解码源视频第 {next_frame_index} 帧（目标帧 {frame_index}）。"
                    )
                decoded_index = next_frame_index
                next_frame_index += 1
            if decoded_index != frame_index:
                raise RuntimeError(f"Internal pose extraction index mismatch at frame {frame_index}.")

            boxes, _detection_scores = pose_engine.detect(bgr)
            points, point_scores = pose_engine.estimate(bgr, boxes)
            people_frames += int(len(boxes) > 0)
            people_total += int(len(boxes))
            canvas_bgr = pose_pipeline.draw_skeleton_frame(
                int(bgr.shape[1]), int(bgr.shape[0]), points, point_scores
            )
            resized_rgb = core._resize_short_side(cv2.cvtColor(canvas_bgr, cv2.COLOR_BGR2RGB), cv2)
            frame_path = frame_dir / f"frame_{frame_index:09d}.png"
            if not cv2.imwrite(str(frame_path), cv2.cvtColor(resized_rgb, cv2.COLOR_RGB2BGR)):
                raise RuntimeError(f"Could not save temporary skeleton frame {frame_index}.")
            if ordinal == 1 or ordinal % 25 == 0 or ordinal == len(requested_indices):
                progress_callback(
                    stage="提取黑底骨架",
                    progress=0.08 + 0.48 * ordinal / len(requested_indices),
                    processed_windows=0,
                    total_windows=len(windows),
                    detail=f"已提取 {ordinal}/{len(requested_indices)} 张采样帧；检测到人物的帧 {people_frames} 张。",
                )
        capture.release()

        progress_callback(
            stage="加载 MC3-18 黑底骨架模型",
            progress=0.58,
            processed_windows=0,
            total_windows=len(windows),
            detail=f"骨架帧已完成（{people_total} 次人物检测）；开始构建 4s16f2 推理窗口。",
        )
        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        amp = device.type == "cuda"
        model = core._build_model(torch, nn, weights_enum, model_builder, device, pretrained=False)
        model.load_state_dict(payload["state_dict"])
        model.eval()

        probabilities: list[float] = []
        batch_size = 2
        for batch_start in range(0, len(windows), batch_size):
            batch_windows = windows[batch_start : batch_start + batch_size]
            clips = []
            for window in batch_windows:
                resized_frames = []
                for frame_index in window.frame_indices:
                    frame_path = frame_dir / f"frame_{int(frame_index):09d}.png"
                    bgr = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
                    if bgr is None:
                        raise RuntimeError(f"Could not read generated skeleton frame {frame_index}.")
                    resized_frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                height, width = resized_frames[0].shape[:2]
                if height < crop_size or width < crop_size:
                    raise ValueError(f"Skeleton frame is smaller than the {crop_size}x{crop_size} model crop.")
                top, left = (height - crop_size) // 2, (width - crop_size) // 2
                clip = np.stack(
                    [frame[top : top + crop_size, left : left + crop_size] for frame in resized_frames], axis=0
                ).astype(np.float32)
                clip /= 255.0
                clip = ((clip - mean) / std).transpose(3, 0, 1, 2)
                clips.append(torch.from_numpy(np.ascontiguousarray(clip, dtype=np.float32)))

            batch = torch.stack(clips, dim=0).to(device, non_blocking=True)
            with torch.inference_mode():
                with core._amp_context(torch, device, amp):
                    logits = model(batch)
                probabilities.extend(float(value) for value in torch.softmax(logits.float(), dim=1)[:, 1].cpu().tolist())
            progress_callback(
                stage="逐窗口推理（黑底骨架 4s16f2）",
                progress=0.58 + 0.40 * len(probabilities) / len(windows),
                processed_windows=len(probabilities),
                total_windows=len(windows),
                detail=f"已完成 {len(probabilities)}/{len(windows)} 个 MC3-18 窗口。",
            )

        score = max(probabilities)
        stat = video_path.stat()
        return {
            "filename": Path(original_filename).name,
            "duration_seconds": duration,
            "window_seconds": window_seconds,
            "stride_seconds": stride_seconds,
            "frames_per_window": num_frames,
            "short_side": short_side,
            "crop_size": crop_size,
            "mode": mode,
            "threshold": threshold,
            "probability_fight": score,
            "prediction": "Fight" if score >= threshold else "NonFight",
            "window_count": len(windows),
            "elapsed_seconds": time.perf_counter() - started,
            "device": str(device),
            "input_representation": "black_background_skeleton",
            "pose_execution_provider": pose_engine.provider,
            "skeleton_frames_extracted": len(requested_indices),
            "skeleton_frames_with_people": people_frames,
            "people_detections": people_total,
            "tail_policy": "short clips sampled uniformly over real duration; long clips add one end-aligned tail when needed",
            "windows": [
                {
                    "start_seconds": window.start,
                    "end_seconds": window.end,
                    "probability_fight": probability,
                    "prediction": "Fight" if probability >= threshold else "NonFight",
                }
                for window, probability in zip(windows, probabilities)
            ],
            "protocol": FOUR_SECOND_PROTOCOL,
            "file_size_bytes": stat.st_size,
        }
    finally:
        capture.release()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "MC3Dashboard/1.0"

    @property
    def state(self) -> DashboardState:
        return self.server.dashboard_state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[dashboard] " + fmt % args + "\n")

    def _send_bytes(self, content: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 1_000_000:
            raise ValueError("Request body is too large.")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _check_same_origin(self) -> None:
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host', '')}":
            raise PermissionError("This local dashboard accepts changes only from its own page.")

    def _prepare_media_upload(self, parsed) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        filename = parse_qs(parsed.query).get("filename", [""])[0]
        extension = Path(filename).suffix.lower()
        if extension not in {".mp4", ".avi"} or not 0 < length <= MAX_UPLOAD_BYTES:
            raise ValueError("请选择不超过 300 MB 的 MP4 或 AVI 视频。")
        _media_tool("ffmpeg")
        _media_tool("ffprobe")
        with self.state.lock:
            stale = [key for key, job in self.state.media_jobs.items() if time.time() - job["created"] > 3600]
            for key in stale:
                _release_media(key)
            if sum(job["status"] in {"queued", "running"} and not job.get("released")
                   for job in self.state.media_jobs.values()) >= 2:
                raise ValueError("正在准备其他视频预览，请稍后再选择。")
            job_id = uuid.uuid4().hex
            directory = Path(tempfile.mkdtemp(prefix="mc3_browser_preview_"))
            source = directory / f"source{extension}"
            self.state.media_jobs[job_id] = {
                "job_id": job_id, "status": "queued", "created": time.time(),
                "directory": directory, "error": None, "released": False,
            }
        try:
            remaining = length
            with source.open("wb") as handle:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("视频上传中断，请重新选择文件。")
                    handle.write(chunk)
                    remaining -= len(chunk)
        except Exception:
            with self.state.lock:
                self.state.media_jobs.pop(job_id, None)
            shutil.rmtree(directory, ignore_errors=True)
            raise
        threading.Thread(target=_prepare_media_worker, args=(job_id, source),
                         daemon=True, name="mc3-browser-preview").start()
        self._send_json({"job_id": job_id, "status": "queued"}, 202)

    def _send_media(self, job_id: str) -> None:
        with self.state.lock:
            job = self.state.media_jobs.get(job_id)
            if not job or job["status"] != "completed" or job.get("released"):
                self._send_json({"error": "预览已过期，请重新选择视频。"}, 404)
                return
            path = job["path"]
        try:
            with path.open("rb") as handle:
                size = path.stat().st_size
                start, end = 0, size - 1
                range_header = self.headers.get("Range")
                if range_header:
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
                    if match and any(match.groups()):
                        first, last = match.groups()
                        if first:
                            start = int(first)
                            end = min(int(last), end) if last else end
                        else:
                            start = max(0, size - int(last))
                    else:
                        start = size
                    if start >= size or start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                self.send_response(206 if range_header else 200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                if range_header:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                handle.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = handle.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except FileNotFoundError:
            self._send_json({"error": "预览已过期，请重新选择视频。"}, 404)
        finally:
            if job.get("released"):
                shutil.rmtree(job["directory"], ignore_errors=True)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        media_match = re.fullmatch(r"/api/media/([a-f0-9]{32})/preview\.mp4", parsed.path)
        if media_match:
            self._send_media(media_match.group(1))
            return
        if parsed.path == "/api/media/status":
            job_id = parse_qs(parsed.query).get("id", [""])[0]
            with self.state.lock:
                job = self.state.media_jobs.get(job_id)
                self._send_json(_media_payload(job) if job else {"error": "预览不存在或已过期。"},
                                200 if job else 404)
            return
        if parsed.path == "/":
            try:
                content = HTML_PATH.read_bytes()
            except OSError:
                self._send_bytes(b"Dashboard HTML is missing.", "text/plain; charset=utf-8", 500)
                return
            self._send_bytes(content, "text/html; charset=utf-8")
            return
        if parsed.path == "/skeleton-training":
            try:
                content = SKELETON_HTML_PATH.read_bytes()
            except OSError:
                self._send_bytes(b"Skeleton training page is missing.", "text/plain; charset=utf-8", 500)
                return
            self._send_bytes(content, "text/html; charset=utf-8")
            return
        if parsed.path == "/native-finetune":
            try:
                content = NATIVE_FINETUNE_HTML_PATH.read_bytes()
            except OSError:
                self._send_bytes(b"Native fine-tuning page is missing.", "text/plain; charset=utf-8", 500)
                return
            self._send_bytes(content, "text/html; charset=utf-8")
            return
        if parsed.path == "/full-frame-training":
            try:
                content = FULL_FRAME_HTML_PATH.read_bytes()
            except OSError:
                self._send_bytes(b"Full-frame training page is missing.", "text/plain; charset=utf-8", 500)
                return
            self._send_bytes(content, "text/html; charset=utf-8")
            return
        if parsed.path == "/harvard-letterbox-training":
            try:
                content = HARVARD_LETTERBOX_HTML_PATH.read_bytes()
            except OSError:
                self._send_bytes(b"Harvard letterbox training page is missing.", "text/plain; charset=utf-8", 500)
                return
            self._send_bytes(content, "text/html; charset=utf-8")
            return
        if parsed.path == "/temporal-shift-training":
            try:
                content = TEMPORAL_SHIFT_HTML_PATH.read_bytes()
            except OSError:
                self._send_bytes(b"Temporal-shift training page is missing.", "text/plain; charset=utf-8", 500)
                return
            self._send_bytes(content, "text/html; charset=utf-8")
            return
        if parsed.path == "/api/health":
            available = {name: importlib.util.find_spec(name) is not None for name in ("torch", "torchvision", "numpy", "cv2")}
            pose_available = {
                name: importlib.util.find_spec(name) is not None for name in ("rtmlib", "onnxruntime")
            }
            self._send_json(
                {
                    "status": "ok",
                    "python": sys.executable,
                    "python_version": sys.version.split()[0],
                    "dependencies": available,
                    "pose_dependencies": pose_available,
                    "model_ready": all(available.values()),
                    "dataset_root": str(DEFAULT_DATASET_ROOT),
                    "output_root": str(DEFAULT_OUTPUT_ROOT),
                }
            )
            return
        if parsed.path == "/api/status":
            self._send_json(_training_status_payload())
            return
        if parsed.path == "/api/reverse/summary":
            query = parse_qs(parsed.query)
            payload = {
                "legacy_dataset_root": query.get("legacy_dataset_root", [str(DEFAULT_REVERSE_LEGACY_ROOT)])[0],
                "ucf_root": query.get("ucf_root", [str(DEFAULT_REVERSE_UCF_ROOT)])[0],
                "airtlab_root": query.get("airtlab_root", [str(DEFAULT_REVERSE_AIRTLAB_ROOT)])[0],
                "guardaid_root": query.get("guardaid_root", [str(DEFAULT_REVERSE_GUARDAID_ROOT)])[0],
                "output_root": query.get("output_root", [str(DEFAULT_REVERSE_OUTPUT_ROOT)])[0],
            }
            try:
                self._send_json(_reverse_dataset_summary(payload))
            except (OSError, ValueError, RuntimeError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/reverse/status":
            query = parse_qs(parsed.query)
            output_root = query.get("output_root", [None])[0]
            run_dir = query.get("run_dir", [None])[0]
            try:
                self._send_json(_reverse_status_payload(output_root, run_dir))
            except (ValueError, OSError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/reverse/runs":
            query = parse_qs(parsed.query)
            output_root = Path(query.get("output_root", [str(DEFAULT_REVERSE_OUTPUT_ROOT)])[0]).expanduser().resolve()
            self._send_json({"runs": _list_runs(output_root)})
            return
        if parsed.path == "/api/reverse/run-config":
            query = parse_qs(parsed.query)
            root_value = query.get("output_root", [str(DEFAULT_REVERSE_OUTPUT_ROOT)])[0]
            run_value = query.get("run_dir", [""])[0]
            try:
                run_dir = _require_child_path(run_value, root_value)
                config = _read_json(run_dir / "config.json")
                if config is None:
                    raise ValueError("无法读取所选运行的 config.json。")
                self._send_json({"config": config})
            except ValueError as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/four/summary":
            query = parse_qs(parsed.query)
            payload = {
                "legacy_dataset_root": query.get("legacy_dataset_root", [str(DEFAULT_REVERSE_LEGACY_ROOT)])[0],
                "ucf_root": query.get("ucf_root", [str(DEFAULT_REVERSE_UCF_ROOT)])[0],
                "airtlab_root": query.get("airtlab_root", [str(DEFAULT_REVERSE_AIRTLAB_ROOT)])[0],
                "guardaid_root": query.get("guardaid_root", [str(DEFAULT_REVERSE_GUARDAID_ROOT)])[0],
                "output_root": query.get("output_root", [str(DEFAULT_FOUR_SECOND_OUTPUT_ROOT)])[0],
                "cache_dir": query.get("cache_dir", [str(DEFAULT_FOUR_SECOND_SUPPLEMENT_CACHE)])[0],
            }
            try:
                self._send_json(_four_second_dataset_summary(payload))
            except (OSError, ValueError, RuntimeError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/skeleton/summary":
            try:
                self._send_json(_skeleton_dataset_summary())
            except (OSError, ValueError, RuntimeError, ImportError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/skeleton/runs":
            self._send_json({"runs": _skeleton_runs()})
            return
        if parsed.path == "/api/skeleton/run-config":
            query = parse_qs(parsed.query)
            try:
                run_dir = _require_child_path(query.get("run_dir", [""])[0], str(DEFAULT_SKELETON_OUTPUT_ROOT))
                config = _read_json(run_dir / "config.json")
                if config is None:
                    raise ValueError("所选骨架训练运行没有可读取的 config.json。")
                self._send_json({"config": config})
            except ValueError as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/skeleton/status":
            query = parse_qs(parsed.query)
            try:
                self._send_json(_skeleton_status_payload(query.get("run_dir", [None])[0]))
            except (ValueError, OSError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/native-finetune/summary":
            try:
                self._send_json(_native_finetune_dataset_summary())
            except (OSError, ValueError, RuntimeError, ImportError) as exc:
                # A missing dataset is a readiness state on this read-only GET,
                # not a malformed request. Keep the page usable for diagnostics.
                self._send_json({"ready": False, "error": str(exc)}, 200)
            return
        if parsed.path == "/api/native-finetune/runs":
            self._send_json({"runs": _native_finetune_runs()})
            return
        if parsed.path == "/api/native-finetune/status":
            query = parse_qs(parsed.query)
            try:
                self._send_json(_native_finetune_status_payload(query.get("run_dir", [None])[0]))
            except (ValueError, OSError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/full-frame/summary":
            try:
                self._send_json(_full_frame_dataset_summary())
            except (OSError, ValueError, RuntimeError, ImportError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/full-frame/runs":
            self._send_json({"runs": _full_frame_runs()})
            return
        if parsed.path == "/api/full-frame/status":
            query = parse_qs(parsed.query)
            try:
                self._send_json(_full_frame_status_payload(query.get("run_dir", [None])[0]))
            except (ValueError, OSError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/harvard-letterbox/summary":
            try:
                self._send_json(_harvard_letterbox_dataset_summary())
            except (OSError, ValueError, RuntimeError, ImportError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/harvard-letterbox/runs":
            self._send_json({"runs": _harvard_letterbox_runs()})
            return
        if parsed.path == "/api/harvard-letterbox/status":
            query = parse_qs(parsed.query)
            try:
                self._send_json(_harvard_letterbox_status_payload(query.get("run_dir", [None])[0]))
            except (ValueError, OSError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/temporal-shift/summary":
            try:
                self._send_json(_temporal_shift_dataset_summary())
            except (OSError, ValueError, RuntimeError, ImportError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/temporal-shift/runs":
            self._send_json({"runs": _temporal_shift_runs()})
            return
        if parsed.path == "/api/temporal-shift/status":
            query = parse_qs(parsed.query)
            try:
                self._send_json(_temporal_shift_status_payload(query.get("run_dir", [None])[0]))
            except (ValueError, OSError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/four/status":
            query = parse_qs(parsed.query)
            try:
                self._send_json(_four_second_status_payload(
                    query.get("output_root", [None])[0], query.get("run_dir", [None])[0]
                ))
            except (ValueError, OSError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/four/runs":
            query = parse_qs(parsed.query)
            output_root = Path(query.get("output_root", [str(DEFAULT_FOUR_SECOND_OUTPUT_ROOT)])[0]).expanduser().resolve()
            self._send_json({"runs": _list_runs(output_root)})
            return
        if parsed.path == "/api/four/run-config":
            query = parse_qs(parsed.query)
            root_value = query.get("output_root", [str(DEFAULT_FOUR_SECOND_OUTPUT_ROOT)])[0]
            run_value = query.get("run_dir", [""])[0]
            try:
                run_dir = _require_child_path(run_value, root_value)
                config = _read_json(run_dir / "config.json")
                if config is None:
                    raise ValueError("无法读取所选四秒运行的 config.json。")
                self._send_json({"config": config})
            except ValueError as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/runs":
            query = parse_qs(parsed.query)
            root_value = query.get("output_root", [str(DEFAULT_OUTPUT_ROOT)])[0]
            include_nested = query.get("inference", ["0"])[0] == "1"
            self._send_json({"runs": _list_runs(Path(root_value).expanduser().resolve(), include_nested=include_nested)})
            return
        if parsed.path == "/api/run-config":
            query = parse_qs(parsed.query)
            root_value = query.get("output_root", [str(DEFAULT_OUTPUT_ROOT)])[0]
            run_value = query.get("run_dir", [""])[0]
            try:
                run_dir = _require_child_path(run_value, root_value)
                config = _read_json(run_dir / "config.json")
                if config is None:
                    raise ValueError("Could not read this run's config.json.")
                self._send_json({"config": config})
            except ValueError as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/inference/status":
            with self.state.lock:
                self._send_json(dict(self.state.inference))
            return
        self._send_json({"error": "Not found."}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            self._check_same_origin()
            if parsed.path == "/api/media/prepare":
                self._prepare_media_upload(parsed)
                return
            if parsed.path == "/api/inference/start":
                self._start_inference_upload()
                return
            payload = self._json_body()
            if parsed.path == "/api/media/release":
                _release_media(str(payload.get("job_id", "")))
                self._send_json({"status": "released"})
            elif parsed.path == "/api/training/start":
                self._send_json(_start_training(payload), 202)
            elif parsed.path == "/api/training/pause":
                self._send_json(_request_pause())
            elif parsed.path == "/api/training/cancel-pause":
                self._send_json(_cancel_pause())
            elif parsed.path == "/api/training/stop":
                self._send_json(_stop_training())
            elif parsed.path == "/api/reverse/training/start":
                self._send_json(_start_reverse_training(payload), 202)
            elif parsed.path == "/api/reverse/training/pause":
                self._send_json(_reverse_pause())
            elif parsed.path == "/api/reverse/training/cancel-pause":
                self._send_json(_reverse_cancel_pause())
            elif parsed.path == "/api/reverse/training/stop":
                self._send_json(_reverse_stop())
            elif parsed.path == "/api/four/training/start":
                self._send_json(_start_four_second_training(payload), 202)
            elif parsed.path == "/api/four/training/pause":
                self._send_json(_four_second_pause())
            elif parsed.path == "/api/four/training/cancel-pause":
                self._send_json(_four_second_cancel_pause())
            elif parsed.path == "/api/four/training/stop":
                self._send_json(_four_second_stop())
            elif parsed.path == "/api/skeleton/training/start":
                self._send_json(_start_skeleton_training(payload), 202)
            elif parsed.path == "/api/skeleton/training/pause":
                self._send_json(_skeleton_pause())
            elif parsed.path == "/api/skeleton/training/cancel-pause":
                self._send_json(_skeleton_cancel_pause())
            elif parsed.path == "/api/skeleton/training/stop":
                self._send_json(_skeleton_stop())
            elif parsed.path == "/api/native-finetune/training/start":
                self._send_json(_start_native_finetune(payload), 202)
            elif parsed.path == "/api/native-finetune/training/pause":
                self._send_json(_native_finetune_pause())
            elif parsed.path == "/api/native-finetune/training/cancel-pause":
                self._send_json(_native_finetune_cancel_pause())
            elif parsed.path == "/api/native-finetune/training/stop":
                self._send_json(_native_finetune_stop())
            elif parsed.path == "/api/full-frame/training/start":
                self._send_json(_start_full_frame_training(payload), 202)
            elif parsed.path == "/api/full-frame/training/pause":
                self._send_json(_full_frame_pause())
            elif parsed.path == "/api/full-frame/training/cancel-pause":
                self._send_json(_full_frame_cancel_pause())
            elif parsed.path == "/api/full-frame/training/stop":
                self._send_json(_full_frame_stop())
            elif parsed.path == "/api/harvard-letterbox/training/start":
                self._send_json(_start_harvard_letterbox_training(payload), 202)
            elif parsed.path == "/api/harvard-letterbox/training/pause":
                self._send_json(_harvard_letterbox_pause())
            elif parsed.path == "/api/harvard-letterbox/training/cancel-pause":
                self._send_json(_harvard_letterbox_cancel_pause())
            elif parsed.path == "/api/harvard-letterbox/training/stop":
                self._send_json(_harvard_letterbox_stop())
            elif parsed.path == "/api/temporal-shift/training/start":
                self._send_json(_start_temporal_shift_training(payload), 202)
            elif parsed.path == "/api/temporal-shift/training/pause":
                self._send_json(_temporal_shift_pause())
            elif parsed.path == "/api/temporal-shift/training/cancel-pause":
                self._send_json(_temporal_shift_cancel_pause())
            elif parsed.path == "/api/temporal-shift/training/stop":
                self._send_json(_temporal_shift_stop())
            else:
                self._send_json({"error": "Not found."}, 404)
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, 403)
        except (ValueError, RuntimeError, OSError) as exc:
            self._send_json({"error": str(exc)}, 400)

    def _start_inference_upload(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            self._send_json({"error": "Upload a video using multipart form data."}, 400)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self._send_json({"error": "Video upload must be between 1 byte and 300 MB."}, 413)
            return
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": content_type,
                    "CONTENT_LENGTH": str(length),
                },
                keep_blank_values=True,
            )
        video_field = form["video"] if "video" in form else None
        if video_field is None or not getattr(video_field, "filename", None):
            self._send_json({"error": "Select a video file first."}, 400)
            return
        extension = Path(video_field.filename).suffix.lower()
        if extension not in {".mp4", ".avi"}:
            self._send_json({"error": "Supported video formats are MP4 and AVI."}, 400)
            return
        query_output_root = form.getfirst("output_root", str(DEFAULT_OUTPUT_ROOT))
        checkpoint_value = form.getfirst("checkpoint", "")
        mode = form.getfirst("mode", "moderate")
        if mode not in {"sensitive", "moderate", "certain", "fixed_0_5", "validation_best_f1"}:
            self._send_json({"error": "Choose a threshold mode offered for the selected checkpoint."}, 400)
            return
        try:
            checkpoint = _require_child_path(checkpoint_value, query_output_root)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        if checkpoint.name != "best_model.pt" or not checkpoint.is_file():
            self._send_json({"error": "Choose a completed run's best_model.pt checkpoint."}, 400)
            return
        threshold_payload = _inference_threshold_payload(checkpoint)
        if not threshold_payload:
            self._send_json({"error": "This checkpoint has no matching calibrated thresholds. Finish training/validation first."}, 400)
            return
        if mode not in threshold_payload["threshold_modes"]:
            self._send_json({"error": "The selected threshold mode is not available for this checkpoint."}, 400)
            return
        try:
            threshold = float(threshold_payload["selection"][mode]["threshold"])
        except (KeyError, TypeError, ValueError):
            self._send_json({"error": "The selected threshold is missing from this model's validation report."}, 400)
            return

        upload_dir = Path(tempfile.mkdtemp(prefix="mc3_dashboard_upload_"))
        video_path = upload_dir / f"upload_{uuid.uuid4().hex}{extension}"
        try:
            with video_path.open("wb") as handle:
                while True:
                    chunk = video_field.file.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
        except OSError as exc:
            video_path.unlink(missing_ok=True)
            upload_dir.rmdir()
            self._send_json({"error": f"Could not save upload: {exc}"}, 500)
            return
        with self.state.lock:
            if _mc3_training_active():
                video_path.unlink(missing_ok=True)
                upload_dir.rmdir()
                self._send_json({"error": "MC3-18 训练正在占用计算资源；请等待该 epoch 边界暂停或训练完成后再推理。"}, 409)
                return
            if self.state.inference.get("status") == "running":
                video_path.unlink(missing_ok=True)
                upload_dir.rmdir()
                self._send_json({"error": "One video is already being analyzed. Wait for it to finish."}, 409)
                return
            job_id = uuid.uuid4().hex
            self.state.inference = {
                "job_id": job_id,
                "status": "queued",
                "stage": "已接收视频",
                "progress": 0.0,
                "processed_windows": 0,
                "total_windows": 0,
                "detail": "",
                "result": None,
                "error": None,
            }
        threading.Thread(
            target=_inference_worker,
            args=(job_id, video_path, Path(video_field.filename).name, checkpoint, mode, threshold),
            daemon=True,
            name="mc3-video-inference",
        ).start()
        self._send_json({"job_id": job_id, "status": "queued"}, 202)


def _make_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    server.dashboard_state = STATE  # type: ignore[attr-defined]
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Do not open the local dashboard automatically.")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        server = _make_server(args.port)
    except OSError as exc:
        parser.error(f"Could not start local dashboard on 127.0.0.1:{args.port}: {exc}")
    address = f"http://127.0.0.1:{args.port}"
    print(f"MC3-18 local dashboard: {address}", flush=True)
    print("This server is local-only. Press Ctrl+C here to close the dashboard.", flush=True)
    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nDashboard closed.", flush=True)
    finally:
        server.server_close()
        with STATE.lock:
            media_ids = list(STATE.media_jobs)
        for job_id in media_ids:
            _release_media(job_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
