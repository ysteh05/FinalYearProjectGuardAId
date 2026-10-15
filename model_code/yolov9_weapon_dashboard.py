"""Loopback web workbench for local YOLOv9-T five-class weapon training."""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import io
import json
import mimetypes
import os
import platform
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from PIL import Image, ImageDraw, ImageOps

from yolov9_weapon_pipeline import check_dependencies, default_training_config
from yolov9_weapon_workspace import (
    CLASS_LABELS,
    CLASS_NAMES,
    DATA_ROOT,
    HARD_NEGATIVE_FINETUNE_OUTPUT_ROOT,
    MALL_FINETUNE_OUTPUT_ROOT,
    OUTPUT_ROOT,
    PROJECT_ROOT,
    atomic_write_json,
    json_safe,
)


HTML_PATH = Path(__file__).with_name("yolov9_weapon_dashboard.html")
FULL_V1_HTML_PATH = Path(__file__).with_name("yolov9_full_dataset_v1.html")
MALL_FINETUNE_HTML_PATH = Path(__file__).with_name("yolov9_mall_finetune.html")
HARD_NEGATIVE_FINETUNE_HTML_PATH = Path(__file__).with_name("yolov9_brev_hard_negative_finetune.html")
SELECTED_SOURCES_FINETUNE_HTML_PATH = Path(__file__).with_name("yolov9_selected_sources_50epoch.html")
MALL_INFERENCE_HTML_PATH = Path(__file__).with_name("yolov9_mall_inference.html")
PIPELINE_PATH = Path(__file__).with_name("yolov9_weapon_pipeline.py")
MAX_JSON_BODY = 20 * 1024 * 1024
PAGE_LIMIT = 48
FULL_V1_PROFILE = "full_dataset_v1"
FULL_V1_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "yolov9_full_dataset_v1"
FULL_V1_CLASSES = ["knife", "gun"]
MALL_FINETUNE_PROFILE = "mall_negative_finetune_v1"
EXPANDED_NEGATIVE_FINETUNE_PROFILE = "expanded_negative_finetune_v1"
HARD_NEGATIVE_FINETUNE_PROFILE = "brev_hard_negative_finetune_v1"
SELECTED_SOURCES_FINETUNE_PROFILE = "selected_sources_50epoch_v1"
SELECTED_SOURCES_FINETUNE_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "yolov9_selected_sources_50epoch"
MALL_FINETUNE_CLASSES = ["knife", "gun"]
HARD_NEGATIVE_FPR_THRESHOLD = 0.25
HARD_NEGATIVE_MODEL_DEFAULTS = {
    "epochs_cap": 5,
    "patience": 0,
    "imgsz": 640,
    "batch_size": 8,
    "workers": 2,
    "device": "0",
    "optimizer": "AdamW",
    "lr0": 0.0001,
    "lrf": 0.1,
    "momentum": 0.9,
    "weight_decay": 0.0005,
    "warmup_epochs": 0.5,
    "mosaic": 0.2,
    "close_mosaic_epochs": 1,
    "mixup": 0.0,
    "hsv_h": 0.015,
    "hsv_s": 0.5,
    "hsv_v": 0.3,
    "translate": 0.1,
    "scale": 0.3,
    "fliplr": 0.5,
    "flipud": 0.0,
    "seed": 2026,
    "iou_threshold": 0.7,
    "hard_negative_loss_weight": 1.0,
    "hard_negative_loss_gamma": 2.0,
}
SELECTED_SOURCES_MODEL_DEFAULTS = {
    "epochs_cap": 10,
    "patience": 3,
    "early_stop_metric": "f05_fitness_mean",
    "imgsz": 640,
    "batch_size": 8,
    "workers": 2,
    "device": "0",
    "optimizer": "AdamW",
    "lr0": 0.0001,
    "lrf": 0.1,
    "momentum": 0.9,
    "weight_decay": 0.0005,
    "warmup_epochs": 0.5,
    "mosaic": 0.2,
    "close_mosaic_epochs": 3,
    "mixup": 0.0,
    "hsv_h": 0.015,
    "hsv_s": 0.5,
    "hsv_v": 0.3,
    "translate": 0.1,
    "scale": 0.3,
    "fliplr": 0.5,
    "flipud": 0.0,
    "seed": 2026,
    "iou_threshold": 0.7,
}
MALL_INFERENCE_NMS_IOU = 0.70  # Matches the NMS setting used to calibrate saved profile thresholds.
MALL_FINETUNE_MODEL_DEFAULTS = {
    "epochs_cap": 5,
    "patience": 0,
    "imgsz": 640,
    "batch_size": 16,
    "workers": 4,
    "device": "0",
    "optimizer": "AdamW",
    "lr0": 0.0001,
    "lrf": 0.1,
    "momentum": 0.9,
    "weight_decay": 0.0005,
    "warmup_epochs": 0.5,
    "mosaic": 0.2,
    "close_mosaic_epochs": 1,
    "mixup": 0.0,
    "hsv_h": 0.015,
    "hsv_s": 0.5,
    "hsv_v": 0.3,
    "translate": 0.1,
    "scale": 0.3,
    "fliplr": 0.5,
    "flipud": 0.0,
    "seed": 2026,
    "iou_threshold": 0.7,
}
BREV_INFERENCE_MODEL_ID = "brev_fullv1_20261009_063503_10ce0a"
BREV_INFERENCE_MODEL_LABEL = "BREV YOLOv9-T Best · imported · Knife / Gun"
BREV_INFERENCE_ARTIFACT_DIR = PROJECT_ROOT / "artifacts" / BREV_INFERENCE_MODEL_ID
DEPLOYED_SELECTED_SOURCES_MODEL_ID = "selected_sources_f05_10ep_20261011_193754_83560d"
DEPLOYED_SELECTED_SOURCES_MODEL_LABEL = "Selected 9-source F0.5 Best · Epoch 6 · Knife / Gun"
DEPLOYED_SELECTED_SOURCES_ARTIFACT_DIR = PROJECT_ROOT / "artifacts" / DEPLOYED_SELECTED_SOURCES_MODEL_ID


class DashboardState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen | None = None
        self.worker: threading.Thread | None = None
        self.kind: str | None = None
        self.run_id: str | None = None
        self.run_dir: Path | None = None
        self.status_path: Path | None = None
        self.error: str | None = None


STATE = DashboardState()
_INFERENCE_LOCK = threading.Lock()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(json_safe(value), ensure_ascii=False).encode("utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _patch_json(path: Path, updates: dict[str, Any]) -> None:
    state = _read_json(path)
    state.update(updates)
    state["updated_at"] = time.time()
    atomic_write_json(path, state)


def _pid_exists(pid: Any) -> bool:
    try:
        pid_value = int(pid)
        if pid_value <= 0:
            return False
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            open_process.restype = wintypes.HANDLE
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            handle = open_process(0x1000, False, pid_value)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                close_handle(handle)
                return True
            return ctypes.get_last_error() == 5  # Access denied still means the PID exists.
        os.kill(pid_value, 0)
        return True
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False


def _recover_saved_runs() -> None:
    audit_status = OUTPUT_ROOT / "audit_status.json"
    if audit_status.is_file():
        state = _read_json(audit_status)
        if state.get("status") in {"running", "pause_requested", "stopping"}:
            if _pid_exists(state.get("dashboard_child_pid")):
                with STATE.lock:
                    STATE.kind = "audit"
                    STATE.status_path = audit_status
                return
            _patch_json(audit_status, {"status": "interrupted", "stage": "interrupted", "stage_label": "程序重启后发现数据审计进程已停止"})
    bases = [OUTPUT_ROOT / "runs", FULL_V1_OUTPUT_ROOT / "runs", MALL_FINETUNE_OUTPUT_ROOT / "runs", HARD_NEGATIVE_FINETUNE_OUTPUT_ROOT / "runs", SELECTED_SOURCES_FINETUNE_OUTPUT_ROOT / "runs"]
    progress_files = sorted(
        (path for base in bases if base.is_dir() for path in base.glob("*/progress.json")),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not progress_files:
        return
    latest_terminal: tuple[Path, dict[str, Any]] | None = None
    for path in progress_files:
        state = _read_json(path)
        if state.get("status") not in {"running", "pause_requested", "stopping", "interrupted"}:
            if latest_terminal is None:
                latest_terminal = (path, state)
            continue
        pid = state.get("dashboard_child_pid")
        if _pid_exists(pid):
            with STATE.lock:
                STATE.kind = "evaluation" if state.get("stage") in {"evaluation", "epoch_fpr", "finalizing"} else "training"
                STATE.run_id = path.parent.name
                STATE.run_dir = path.parent
                STATE.status_path = path
            return
        stage = state.get("stage")
        if stage in {"evaluation", "finalizing", "epoch_fpr"}:
            _patch_json(path, {"status": "failed", "failed_stage": stage, "stage": "failed", "stage_label": "程序重启后发现最终评估中断，可继续评估"})
        else:
            checkpoint = path.parent / "checkpoints" / "last.pt"
            _patch_json(path, {"status": "interrupted", "stage": "interrupted", "stage_label": "程序重启后发现训练进程已停止", "last_checkpoint": str(checkpoint.resolve()) if checkpoint.is_file() else None})
        if latest_terminal is None:
            latest_terminal = (path, _read_json(path))
    if latest_terminal:
        path, _state = latest_terminal
        with STATE.lock:
            STATE.kind = "evaluation" if _state.get("failed_stage") in {"evaluation", "finalizing", "epoch_fpr"} else "training"
            STATE.run_id = path.parent.name
            STATE.run_dir = path.parent
            STATE.status_path = path


def _safe_run_dir(run_id: str) -> Path:
    if not run_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in run_id):
        raise ValueError("Invalid run id")
    if run_id.startswith("fullv1_"):
        base = FULL_V1_OUTPUT_ROOT
    elif run_id.startswith("mallft_"):
        base = MALL_FINETUNE_OUTPUT_ROOT
    elif run_id.startswith("hnft_"):
        base = HARD_NEGATIVE_FINETUNE_OUTPUT_ROOT
    elif run_id.startswith("sel50_"):
        base = SELECTED_SOURCES_FINETUNE_OUTPUT_ROOT
    else:
        base = OUTPUT_ROOT
    path = (base / "runs" / run_id).resolve()
    if not path.is_relative_to((base / "runs").resolve()):
        raise ValueError("Invalid run directory")
    return path


def _is_mall_finetune_run(run_dir: Path) -> bool:
    config = _read_json(run_dir / "user_config.json") or _read_json(run_dir / "resolved_training_config.json")
    return config.get("dataset_profile") in {MALL_FINETUNE_PROFILE, EXPANDED_NEGATIVE_FINETUNE_PROFILE} or run_dir.name.startswith("mallft_")


def _mall_finetune_capabilities(run_dir: Path, state: dict[str, Any]) -> dict[str, bool]:
    status = state.get("status")
    failed_stage = state.get("failed_stage")
    has_config = (run_dir / "resolved_training_config.json").is_file()
    has_last = (run_dir / "checkpoints" / "last.pt").is_file() or (run_dir / "official_train" / "train" / "weights" / "last.pt").is_file()
    has_best = (run_dir / "official_train" / "train" / "weights" / "best.pt").is_file()
    return {
        "can_resume_training": bool(status in {"paused", "stopped", "interrupted", "failed"} and failed_stage not in {"evaluation", "finalizing"} and has_config and has_last),
        "can_resume_evaluation": bool(status == "failed" and failed_stage in {"evaluation", "finalizing"} and has_best),
    }


def _mall_finetune_runs() -> list[dict[str, Any]]:
    runs = []
    base = MALL_FINETUNE_OUTPUT_ROOT / "runs"
    if not base.is_dir():
        return runs
    for progress_path in base.glob("*/progress.json"):
        run_dir = progress_path.parent
        if not _is_mall_finetune_run(run_dir):
            continue
        state = _read_json(progress_path)
        if not state:
            continue
        test_per_class = state.get("test_per_class") or {}
        map_values = [float(row["map50"]) for row in test_per_class.values() if row.get("map50") is not None]
        runs.append({
            "run_id": state.get("run_id", run_dir.name),
            "status": state.get("status", "unknown"),
            "stage": state.get("stage"),
            "stage_label": state.get("stage_label"),
            "created_at": state.get("started_at", progress_path.stat().st_mtime),
            "updated_at": state.get("updated_at", progress_path.stat().st_mtime),
            "epoch": state.get("epoch", state.get("completed_epochs", 0)),
            "epochs": state.get("epochs", 5),
            "best_epoch": state.get("best_epoch"),
            "map50": sum(map_values) / len(map_values) if map_values else None,
            "failed_stage": state.get("failed_stage"),
            **_mall_finetune_capabilities(run_dir, state),
        })
    runs.sort(key=lambda item: item["updated_at"], reverse=True)
    return runs


def _mall_finetune_models() -> dict[str, Any]:
    """Return only server-allowlisted, local two-class checkpoints."""
    models: list[dict[str, Any]] = []
    default_checkpoint = BREV_INFERENCE_ARTIFACT_DIR / "best_training.pt"
    if default_checkpoint.is_file():
        models.append({
            "model_id": BREV_INFERENCE_MODEL_ID,
            "label": "BREV YOLOv9-T Best · Knife / Gun",
            "source": "BREV imported best_training.pt",
            "is_default": True,
            "ready": True,
        })
    for run_id, root, source_label in (
        *((run.get("run_id"), FULL_V1_OUTPUT_ROOT, "Full dataset V1 fine-tune") for run in _full_v1_runs() if run.get("status") == "completed"),
        *((run.get("run_id"), MALL_FINETUNE_OUTPUT_ROOT, "Expanded-negative fine-tune") for run in _mall_finetune_runs() if run.get("status") == "completed"),
    ):
        if not run_id:
            continue
        run_dir = (root / "runs" / str(run_id)).resolve()
        config = _read_json(run_dir / "resolved_training_config.json") or _read_json(run_dir / "user_config.json")
        if config.get("class_names") != MALL_FINETUNE_CLASSES:
            continue
        checkpoint = run_dir / "official_train" / "train" / "weights" / "best.pt"
        if checkpoint.is_file():
            models.append({
                "model_id": str(run_id),
                "label": f"{source_label} · {run_id}",
                "source": source_label,
                "is_default": False,
                "ready": True,
            })
    default_id = next((item["model_id"] for item in models if item.get("is_default")), models[0]["model_id"] if models else None)
    return {"models": models, "default_model_id": default_id}


def _resolve_mall_finetune_model(model_id: str) -> tuple[Path, dict[str, Any]]:
    catalog = _mall_finetune_models()
    model = next((item for item in catalog["models"] if item["model_id"] == model_id), None)
    if model is None:
        raise ValueError("所选模型不在本地可信的两类 YOLO 模型目录中")
    if model_id == BREV_INFERENCE_MODEL_ID:
        checkpoint = BREV_INFERENCE_ARTIFACT_DIR / "best_training.pt"
    else:
        run_dir = _safe_run_dir(model_id)
        if not (run_dir / "progress.json").is_file() or not _is_full_v1_run(run_dir) and not _is_mall_finetune_run(run_dir):
            raise ValueError("所选模型运行记录无效")
        config = _read_json(run_dir / "resolved_training_config.json") or _read_json(run_dir / "user_config.json")
        if config.get("class_names") != MALL_FINETUNE_CLASSES:
            raise ValueError("所选模型不是兼容的 Knife/Gun 两类模型")
        checkpoint = run_dir / "official_train" / "train" / "weights" / "best.pt"
    if not checkpoint.is_file():
        raise FileNotFoundError("所选模型权重不存在")
    return checkpoint.resolve(), model


def _read_mall_threshold_profiles(report_path: Path) -> dict[str, dict[str, float]]:
    report = _read_json(report_path)
    if report.get("class_names") != MALL_FINETUNE_CLASSES:
        raise ValueError("阈值报告不是 Knife/Gun 两类模型格式")
    profiles: dict[str, dict[str, float]] = {}
    for item in report.get("profiles", []):
        if not isinstance(item, dict):
            continue
        profile = str(item.get("profile", ""))
        if profile not in {"Sensitive", "Moderate", "Certain"}:
            continue
        values = item.get("thresholds") or {}
        if not isinstance(values, dict) or set(values) != set(MALL_FINETUNE_CLASSES):
            continue
        try:
            parsed = {name: float(values[name]) for name in MALL_FINETUNE_CLASSES}
        except (TypeError, ValueError):
            continue
        if all(0.001 <= value <= 1.0 for value in parsed.values()):
            profiles[profile] = parsed
    if set(profiles) != {"Sensitive", "Moderate", "Certain"}:
        raise FileNotFoundError("模型缺少完整的 Sensitive、Moderate、Certain 验证集阈值")
    return profiles


def _mall_inference_model_catalog() -> dict[str, Any]:
    """Allowlist inference-ready baseline and completed two-class checkpoints."""
    candidates: list[dict[str, Any]] = []
    brev_checkpoint = BREV_INFERENCE_ARTIFACT_DIR / "best_inference.pt"
    brev_thresholds = BREV_INFERENCE_ARTIFACT_DIR / "sensitivity_thresholds.json"
    if brev_checkpoint.is_file() and brev_thresholds.is_file():
        try:
            profiles = _read_mall_threshold_profiles(brev_thresholds)
            candidates.append({
                "model_id": BREV_INFERENCE_MODEL_ID,
                "label": "Before Mall fine-tuning · BREV best",
                "source": "baseline",
                "class_names": MALL_FINETUNE_CLASSES.copy(),
                "profiles": profiles,
            })
        except (TypeError, ValueError, OSError, json.JSONDecodeError):
            pass

    deployed_checkpoint = DEPLOYED_SELECTED_SOURCES_ARTIFACT_DIR / "best_inference.pt"
    deployed_thresholds = DEPLOYED_SELECTED_SOURCES_ARTIFACT_DIR / "sensitivity_thresholds.json"
    if deployed_checkpoint.is_file() and deployed_thresholds.is_file():
        try:
            profiles = _read_mall_threshold_profiles(deployed_thresholds)
            candidates.append({
                "model_id": DEPLOYED_SELECTED_SOURCES_MODEL_ID,
                "label": DEPLOYED_SELECTED_SOURCES_MODEL_LABEL,
                "source": "selected_sources_deployed",
                "class_names": MALL_FINETUNE_CLASSES.copy(),
                "profiles": profiles,
            })
        except (TypeError, ValueError, OSError, json.JSONDecodeError):
            pass

    completed_runs: list[tuple[str, str]] = []
    completed_runs.extend(
        (str(run["run_id"]), "Full Dataset V1")
        for run in _full_v1_runs()
        if run.get("status") == "completed" and run.get("run_id")
    )
    completed_runs.extend(
        (str(run["run_id"]), "Mall Negative Fine-tuning")
        for run in _mall_finetune_runs()
        if run.get("status") == "completed" and run.get("run_id")
    )
    for run_id, source in completed_runs:
        try:
            run_dir = _safe_run_dir(run_id)
        except ValueError:
            continue
        config = _read_json(run_dir / "resolved_training_config.json") or _read_json(run_dir / "user_config.json")
        checkpoint = run_dir / "best_inference.pt"
        thresholds_path = run_dir / "sensitivity_thresholds.json"
        if config.get("class_names") != MALL_FINETUNE_CLASSES or not checkpoint.is_file() or not thresholds_path.is_file():
            continue
        try:
            profiles = _read_mall_threshold_profiles(thresholds_path)
        except (TypeError, ValueError, OSError, json.JSONDecodeError):
            continue
        candidates.append({
            "model_id": run_id,
            "label": f"{source} · {run_id}",
            "source": "mall_finetune" if source == "Mall Negative Fine-tuning" else "training_run",
            "class_names": MALL_FINETUNE_CLASSES.copy(),
            "profiles": profiles,
        })

    if candidates:
        preferred = next(
            (model["model_id"] for model in candidates if model["source"] == "selected_sources_deployed"),
            next((model["model_id"] for model in candidates if model["source"] == "mall_finetune"), candidates[0]["model_id"]),
        )
        for model in candidates:
            model["is_default"] = model["model_id"] == preferred
    else:
        preferred = None
    return {"models": candidates, "default_model_id": preferred, "profiles": ["Sensitive", "Moderate", "Certain"]}


def _resolve_mall_inference_model(model_id: str) -> tuple[Path, dict[str, float], dict[str, Any]]:
    catalog = _mall_inference_model_catalog()
    model = next((item for item in catalog["models"] if item["model_id"] == model_id), None)
    if model is None:
        raise ValueError("所选模型不在本地可信的 Knife/Gun 推理模型目录中")
    if model_id == BREV_INFERENCE_MODEL_ID:
        checkpoint = BREV_INFERENCE_ARTIFACT_DIR / "best_inference.pt"
    elif model_id == DEPLOYED_SELECTED_SOURCES_MODEL_ID:
        checkpoint = DEPLOYED_SELECTED_SOURCES_ARTIFACT_DIR / "best_inference.pt"
    else:
        run_dir = _safe_run_dir(model_id)
        if not _is_full_v1_run(run_dir) and not _is_mall_finetune_run(run_dir):
            raise ValueError("所选模型运行记录无效")
        checkpoint = run_dir / "best_inference.pt"
    checkpoint = checkpoint.resolve()
    if not checkpoint.is_file():
        raise FileNotFoundError("所选模型权重不存在")
    return checkpoint, model["profiles"], model


def _run_mall_finetune_inference(payload: dict[str, Any]) -> dict[str, Any]:
    """Infer on one uploaded image with a server-allowlisted local checkpoint."""
    if not _INFERENCE_LOCK.acquire(blocking=False):
        raise RuntimeError("已有图片推理正在运行，请稍后重试")
    try:
        status = _current_status()
        if status.get("process_active") and status.get("job_kind") in {"training", "evaluation"}:
            raise RuntimeError("训练或评估正在使用 GPU，暂时无法进行图片推理")
        model_id = str(payload.get("model_id") or "")
        checkpoint, profiles, model_info = _resolve_mall_inference_model(model_id)
        profile_name = str(payload.get("threshold_mode") or "Moderate").title()
        if profile_name not in profiles:
            raise ValueError("阈值档位无效，请选择 Sensitive、Moderate 或 Certain")
        thresholds = profiles[profile_name].copy()
        overrides = payload.get("thresholds")
        if overrides is not None:
            if not isinstance(overrides, dict) or set(overrides) != set(MALL_FINETUNE_CLASSES):
                raise ValueError("请为 Knife 和 Gun 分别提供有效阈值")
            try:
                thresholds = {name: float(overrides[name]) for name in MALL_FINETUNE_CLASSES}
            except (TypeError, ValueError):
                raise ValueError("阈值必须是 0.001 到 1 之间的数字") from None
            if any(not 0.001 <= value <= 1.0 for value in thresholds.values()):
                raise ValueError("阈值必须在 0.001 到 1 之间")

        encoded = str(payload.get("image_base64", ""))
        if "," in encoded:
            encoded = encoded.split(",", 1)[1]
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("上传图片编码无效，请重新选择图片") from None
        if not raw:
            raise ValueError("请先上传一张图片")
        if len(raw) > 12 * 1024 * 1024:
            raise ValueError("图片需小于 12 MB")

        try:
            with Image.open(io.BytesIO(raw)) as uploaded:
                if uploaded.format not in {"JPEG", "PNG", "WEBP", "BMP"}:
                    raise ValueError("请使用 JPG、PNG、WebP 或 BMP 图片")
                width, height = uploaded.size
                if width < 1 or height < 1 or width > 8192 or height > 8192 or width * height > 32_000_000:
                    raise ValueError("图片尺寸过大；最大边长为 8,192 像素且最多 32 megapixels")
                uploaded.verify()
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("图片文件损坏或无法读取") from exc

        import cv2
        import numpy as np
        frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("无法读取上传图片；请使用 JPG、PNG 或 WebP 图片")
        from yolov9_weapon_infer import Yolov9WeaponRunner

        cache_key = str(checkpoint)
        runner = _INFERENCE_RUNNERS.get(cache_key)
        if runner is None:
            runner = Yolov9WeaponRunner(checkpoint, Path(default_training_config()["yolov9_root"]))
            _INFERENCE_RUNNERS[cache_key] = runner
        runner.load()
        if tuple(runner.class_names) != tuple(MALL_FINETUNE_CLASSES):
            raise ValueError("加载的 checkpoint 类别顺序不是 Knife / Gun，已拒绝推理")
        detections = runner.infer(
            frame,
            thresholds,
            candidate_floor=0.001,
            iou_threshold=MALL_INFERENCE_NMS_IOU,
        )
        annotated = runner.annotate(frame, detections)
        ok, buffer = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            raise RuntimeError("无法生成标注预览图")
        used_profile = "Custom" if thresholds != profiles[profile_name] else profile_name
        return {
            "model_id": model_id,
            "model_label": model_info["label"],
            "model_source": model_info["source"],
            "threshold_mode": used_profile,
            "selected_profile": profile_name,
            "thresholds": thresholds,
            "nms_iou_threshold": MALL_INFERENCE_NMS_IOU,
            "image_width": int(frame.shape[1]),
            "image_height": int(frame.shape[0]),
            "detections": [
                {"class_id": item.class_id, "class_name": item.class_name, "confidence": item.confidence, "xyxy": item.xyxy, "threshold": item.threshold}
                for item in detections
            ],
            "image_base64": base64.b64encode(buffer).decode("ascii"),
        }
    finally:
        _INFERENCE_LOCK.release()


def _mall_finetune_health() -> dict[str, Any]:
    from yolov9_expanded_negative_finetune import inspect_negative_sources

    try:
        source_summary = inspect_negative_sources()
        source_error = None
    except Exception as exc:
        source_summary = {"ready": False, "sources": {}, "negative_images": 0, "negative_split_counts": {"train": 0, "val": 0, "test": 0}}
        source_error = str(exc)
    try:
        environment = check_dependencies()
        dependency_error = None
    except Exception as exc:
        environment = None
        dependency_error = str(exc)
    base_ready = any(
        report.get("profile") == FULL_V1_PROFILE
        for path in (FULL_V1_OUTPUT_ROOT / "datasets").glob("*/audit_report.json")
        if (report := _read_json(path))
    ) if (FULL_V1_OUTPUT_ROOT / "datasets").is_dir() else False
    return {
        "ok": True,
        "profile": EXPANDED_NEGATIVE_FINETUNE_PROFILE,
        "config": MALL_FINETUNE_MODEL_DEFAULTS.copy(),
        "classes": MALL_FINETUNE_CLASSES.copy(),
        "negative_source_summary": source_summary,
        "negative_source_error": source_error,
        "base_full_dataset_v1_cache_ready": base_ready,
        "models": _mall_finetune_models(),
        "environment": environment,
        "dependency_error": dependency_error,
        "output_root_label": "model_runs/yolov9_mall_negative_finetune",
        "negative_assumption": "Mall, AIRTLab NonViolent, BusViolence NonFight, and remaining NoGUN images are treated as weapon-negative and receive empty Knife/Gun labels. The confirmed true-knife NoGUN image is removed and explicitly excluded. Any other weapon-positive image in these sources would become a false negative.",
    }


def _mall_finetune_active_state() -> dict[str, Any]:
    current = _current_status()
    if current.get("process_active"):
        active_id = str(current.get("run_id") or "")
        if active_id.startswith("mallft_"):
            return current
        return {"status": "busy", "stage": "busy", "process_active": True, "job_kind": current.get("job_kind"), "run_id": active_id or None, "profile": EXPANDED_NEGATIVE_FINETUNE_PROFILE}
    runs = _mall_finetune_runs()
    if runs:
        run_dir = _safe_run_dir(runs[0]["run_id"])
        state = _read_json(run_dir / "progress.json")
        state["process_active"] = False
        state.update(_mall_finetune_capabilities(run_dir, state))
        return state
    return {"status": "idle", "stage": "idle", "process_active": False, "profile": EXPANDED_NEGATIVE_FINETUNE_PROFILE}


def _is_hard_negative_finetune_run(run_dir: Path) -> bool:
    config = _read_json(run_dir / "user_config.json") or _read_json(run_dir / "resolved_training_config.json")
    return config.get("dataset_profile") == HARD_NEGATIVE_FINETUNE_PROFILE or run_dir.name.startswith("hnft_")


def _hard_negative_finetune_runs() -> list[dict[str, Any]]:
    runs = []
    base = HARD_NEGATIVE_FINETUNE_OUTPUT_ROOT / "runs"
    if not base.is_dir():
        return runs
    for progress_path in base.glob("*/progress.json"):
        run_dir = progress_path.parent
        if not _is_hard_negative_finetune_run(run_dir):
            continue
        state = _read_json(progress_path)
        if not state:
            continue
        runs.append({
            "run_id": state.get("run_id", run_dir.name),
            "status": state.get("status", "unknown"),
            "stage": state.get("stage"),
            "stage_label": state.get("stage_label"),
            "created_at": state.get("started_at", progress_path.stat().st_mtime),
            "updated_at": state.get("updated_at", progress_path.stat().st_mtime),
            "epoch": state.get("epoch", state.get("completed_epochs", 0)),
            "epochs": state.get("epochs", 5),
            "best_epoch": state.get("best_epoch"),
            "run_dir": str(run_dir.resolve()),
        })
    runs.sort(key=lambda item: item["updated_at"], reverse=True)
    return runs


def _hard_negative_finetune_active_state() -> dict[str, Any]:
    current = _current_status()
    if current.get("process_active"):
        active_id = str(current.get("run_id") or "")
        if active_id.startswith("hnft_"):
            return current
        return {"status": "busy", "stage": "busy", "process_active": True, "job_kind": current.get("job_kind"), "run_id": active_id or None, "profile": HARD_NEGATIVE_FINETUNE_PROFILE}
    runs = _hard_negative_finetune_runs()
    if runs:
        run_dir = _safe_run_dir(runs[0]["run_id"])
        state = _read_json(run_dir / "progress.json")
        state["process_active"] = False
        has_config = (run_dir / "resolved_training_config.json").is_file()
        has_last = (run_dir / "checkpoints" / "last.pt").is_file() or (run_dir / "official_train" / "train" / "weights" / "last.pt").is_file()
        has_best = (run_dir / "official_train" / "train" / "weights" / "best.pt").is_file()
        state["can_resume_training"] = bool(state.get("status") in {"paused", "stopped", "interrupted", "failed"} and state.get("failed_stage") not in {"evaluation", "finalizing", "epoch_fpr"} and has_config and has_last)
        state["can_resume_evaluation"] = bool(state.get("status") == "failed" and state.get("failed_stage") in {"evaluation", "finalizing", "epoch_fpr"} and has_best)
        return state
    return {"status": "idle", "stage": "idle", "process_active": False, "profile": HARD_NEGATIVE_FINETUNE_PROFILE}


def _hard_negative_finetune_health() -> dict[str, Any]:
    checkpoint = BREV_INFERENCE_ARTIFACT_DIR / "best_training.pt"
    try:
        environment = check_dependencies()
        dependency_error = None
    except Exception as exc:
        environment = None
        dependency_error = str(exc)
    return {
        "ok": True,
        "profile": HARD_NEGATIVE_FINETUNE_PROFILE,
        "config": HARD_NEGATIVE_MODEL_DEFAULTS.copy(),
        "initial_checkpoint": str(checkpoint.resolve()),
        "initial_checkpoint_ready": checkpoint.is_file(),
        "initial_model_label": "BREV Best · Knife / Gun",
        "classes": MALL_FINETUNE_CLASSES.copy(),
        "fpr_confidence_threshold": HARD_NEGATIVE_FPR_THRESHOLD,
        "environment": environment,
        "dependency_error": dependency_error,
        "output_root_label": str(HARD_NEGATIVE_FINETUNE_OUTPUT_ROOT.relative_to(PROJECT_ROOT)),
        "negative_policy": "Use the previous Mall/AIRTLab/Bus/remaining-NoGUN recipe, drop only weapon_dataset_v11 rows with zero boxes, keep V11 weapon-labelled images, and exclude the already removed confirmed-knife NoGUN image.",
    }


def _is_selected_sources_finetune_run(run_dir: Path) -> bool:
    config = _read_json(run_dir / "user_config.json") or _read_json(run_dir / "resolved_training_config.json")
    return config.get("dataset_profile") == SELECTED_SOURCES_FINETUNE_PROFILE or run_dir.name.startswith("sel50_")


def _selected_sources_finetune_runs() -> list[dict[str, Any]]:
    runs = []
    base = SELECTED_SOURCES_FINETUNE_OUTPUT_ROOT / "runs"
    if not base.is_dir():
        return runs
    for progress_path in base.glob("*/progress.json"):
        run_dir = progress_path.parent
        if not _is_selected_sources_finetune_run(run_dir):
            continue
        state = _read_json(progress_path)
        if not state:
            continue
        runs.append({
            "run_id": state.get("run_id", run_dir.name),
            "status": state.get("status", "unknown"),
            "stage": state.get("stage"),
            "stage_label": state.get("stage_label"),
            "created_at": state.get("started_at", progress_path.stat().st_mtime),
            "updated_at": state.get("updated_at", progress_path.stat().st_mtime),
            "epoch": state.get("epoch", state.get("completed_epochs", 0)),
            "epochs": state.get("epochs", 10),
            "best_epoch": state.get("best_epoch"),
            "run_dir": str(run_dir.resolve()),
        })
    runs.sort(key=lambda item: item["updated_at"], reverse=True)
    return runs


def _selected_sources_finetune_capabilities(run_dir: Path, state: dict[str, Any]) -> dict[str, bool]:
    status = state.get("status")
    failed_stage = state.get("failed_stage")
    has_config = (run_dir / "resolved_training_config.json").is_file()
    has_last = (run_dir / "checkpoints" / "last.pt").is_file() or (run_dir / "official_train" / "train" / "weights" / "last.pt").is_file()
    has_best = (run_dir / "official_train" / "train" / "weights" / "best.pt").is_file()
    return {
        "can_resume_training": bool(status in {"paused", "stopped", "interrupted", "failed"} and failed_stage not in {"evaluation", "finalizing"} and has_config and has_last),
        "can_resume_evaluation": bool(status == "failed" and failed_stage in {"evaluation", "finalizing"} and has_best),
    }


def _selected_sources_finetune_active_state() -> dict[str, Any]:
    current = _current_status()
    if current.get("process_active"):
        active_id = str(current.get("run_id") or "")
        if active_id.startswith("sel50_"):
            return current
        return {"status": "busy", "stage": "busy", "process_active": True, "job_kind": current.get("job_kind"), "run_id": active_id or None, "profile": SELECTED_SOURCES_FINETUNE_PROFILE}
    runs = _selected_sources_finetune_runs()
    if runs:
        run_dir = _safe_run_dir(runs[0]["run_id"])
        state = _read_json(run_dir / "progress.json")
        state["process_active"] = False
        state.update(_selected_sources_finetune_capabilities(run_dir, state))
        return state
    return {"status": "idle", "stage": "idle", "process_active": False, "profile": SELECTED_SOURCES_FINETUNE_PROFILE}


def _selected_sources_finetune_health() -> dict[str, Any]:
    checkpoint = BREV_INFERENCE_ARTIFACT_DIR / "best_training.pt"
    try:
        environment = check_dependencies()
        dependency_error = None
    except Exception as exc:
        environment = None
        dependency_error = str(exc)
    try:
        from yolov9_selected_sources_finetune import SELECTED_SOURCES, SOURCE_LABELS, summarize_existing_sources

        dataset = summarize_existing_sources()
        source_list = [{"id": source, "label": SOURCE_LABELS[source]} for source in SELECTED_SOURCES]
    except Exception as exc:
        dataset = {"ready": False, "message": str(exc)}
        source_list = []
    return {
        "ok": True,
        "profile": SELECTED_SOURCES_FINETUNE_PROFILE,
        "config": SELECTED_SOURCES_MODEL_DEFAULTS.copy(),
        "initial_checkpoint": str(checkpoint.resolve()),
        "initial_checkpoint_ready": checkpoint.is_file(),
        "initial_model_label": "BREV Best · Knife / Gun",
        "classes": MALL_FINETUNE_CLASSES.copy(),
        "sources": source_list,
        "dataset": dataset,
        "environment": environment,
        "dependency_error": dependency_error,
        "output_root_label": str(SELECTED_SOURCES_FINETUNE_OUTPUT_ROOT.relative_to(PROJECT_ROOT)),
        "split_policy": "Preserve the source manifest splits, including source-provided, video-grouped and temporal-block assignments; do not reshuffle adjacent frames across splits.",
        "negative_policy": "Use the selected sources' audited empty labels as negative samples. Non-V11 sources excluded by this page are not included in the experiment.",
    }


def _require_selected_sources_active_training() -> Path:
    run_dir = _require_active_training()
    if not run_dir.name.startswith("sel50_") or not _is_selected_sources_finetune_run(run_dir):
        raise RuntimeError("当前运行不是所选九来源的训练")
    return run_dir


def _require_mall_finetune_active_training() -> Path:
    run_dir = _require_active_training()
    if not run_dir.name.startswith("mallft_") or not _is_mall_finetune_run(run_dir):
        raise RuntimeError("当前运行不是 Mall 负样本后训练")
    return run_dir


def _is_full_v1_run(run_dir: Path) -> bool:
    config = _read_json(run_dir / "user_config.json") or _read_json(run_dir / "resolved_training_config.json")
    return config.get("dataset_profile") == FULL_V1_PROFILE or run_dir.name.startswith("fullv1_")


def _full_v1_runs() -> list[dict[str, Any]]:
    runs = []
    base = FULL_V1_OUTPUT_ROOT / "runs"
    if not base.is_dir():
        return runs
    for progress_path in base.glob("*/progress.json"):
        run_dir = progress_path.parent
        state = _read_json(progress_path)
        if not state:
            continue
        metric = state.get("last_epoch_metrics") or {}
        runs.append({
            "run_id": state.get("run_id", run_dir.name),
            "status": state.get("status", "unknown"),
            "stage": state.get("stage"),
            "stage_label": state.get("stage_label"),
            "created_at": state.get("started_at", progress_path.stat().st_mtime),
            "updated_at": state.get("updated_at", progress_path.stat().st_mtime),
            "epoch": state.get("epoch", state.get("completed_epochs", 0)),
            "epochs": state.get("epochs"),
            "map50": metric.get("map50"),
            "best_epoch": state.get("best_epoch"),
            "last_checkpoint": state.get("last_checkpoint"),
            "run_dir": str(run_dir.resolve()),
        })
    runs.sort(key=lambda item: item["updated_at"], reverse=True)
    return runs


def _full_v1_inference_models() -> dict[str, Any]:
    """Return completed training runs plus explicitly allowlisted imported models.

    Imported checkpoints are not represented as fake training runs. Their paths
    remain server-side constants and are never accepted from a request payload.
    """
    models: list[dict[str, Any]] = []
    brev_checkpoint = BREV_INFERENCE_ARTIFACT_DIR / "best_inference.pt"
    brev_thresholds = BREV_INFERENCE_ARTIFACT_DIR / "sensitivity_thresholds.json"
    if brev_checkpoint.is_file() and brev_thresholds.is_file():
        models.append({
            "model_id": BREV_INFERENCE_MODEL_ID,
            "label": BREV_INFERENCE_MODEL_LABEL,
            "source": "imported",
            "class_names": FULL_V1_CLASSES.copy(),
        })

    deployed_checkpoint = DEPLOYED_SELECTED_SOURCES_ARTIFACT_DIR / "best_inference.pt"
    deployed_thresholds = DEPLOYED_SELECTED_SOURCES_ARTIFACT_DIR / "sensitivity_thresholds.json"
    if deployed_checkpoint.is_file() and deployed_thresholds.is_file():
        try:
            _read_mall_threshold_profiles(deployed_thresholds)
            models.append({
                "model_id": DEPLOYED_SELECTED_SOURCES_MODEL_ID,
                "label": DEPLOYED_SELECTED_SOURCES_MODEL_LABEL,
                "source": "imported",
                "class_names": FULL_V1_CLASSES.copy(),
            })
        except (TypeError, ValueError, OSError, json.JSONDecodeError):
            pass

    for run in _full_v1_runs():
        if run.get("status") != "completed":
            continue
        run_id = str(run.get("run_id", ""))
        try:
            run_dir = _safe_run_dir(run_id)
        except ValueError:
            continue
        if not (run_dir / "best_inference.pt").is_file():
            continue
        models.append({
            "model_id": run_id,
            "label": f"Training run · {run_id}",
            "source": "training_run",
            "class_names": FULL_V1_CLASSES.copy(),
        })
    default_id = next(
        (model["model_id"] for model in models if model["model_id"] == DEPLOYED_SELECTED_SOURCES_MODEL_ID),
        next((model["model_id"] for model in models if model["model_id"] == BREV_INFERENCE_MODEL_ID), models[0]["model_id"] if models else None),
    )
    return {
        "models": models,
        "default_model_id": default_id,
    }


def _resolve_full_v1_inference_model(model_id: str) -> tuple[Path, Path, str, str]:
    """Resolve a UI model ID to trusted checkpoint/report paths."""
    if model_id == BREV_INFERENCE_MODEL_ID:
        checkpoint = BREV_INFERENCE_ARTIFACT_DIR / "best_inference.pt"
        report = BREV_INFERENCE_ARTIFACT_DIR / "sensitivity_thresholds.json"
        if not checkpoint.is_file() or not report.is_file():
            raise FileNotFoundError("BREV imported model or its calibrated thresholds are unavailable")
        return checkpoint, report, BREV_INFERENCE_MODEL_LABEL, "imported"
    if model_id == DEPLOYED_SELECTED_SOURCES_MODEL_ID:
        checkpoint = DEPLOYED_SELECTED_SOURCES_ARTIFACT_DIR / "best_inference.pt"
        report = DEPLOYED_SELECTED_SOURCES_ARTIFACT_DIR / "sensitivity_thresholds.json"
        if not checkpoint.is_file() or not report.is_file():
            raise FileNotFoundError("九来源模型或其校准阈值不可用")
        return checkpoint, report, DEPLOYED_SELECTED_SOURCES_MODEL_LABEL, "imported"

    run_dir = _safe_run_dir(model_id)
    checkpoint = run_dir / "best_inference.pt"
    if not checkpoint.is_file():
        raise FileNotFoundError("所选运行尚未生成可推理模型")
    return checkpoint, run_dir / "sensitivity_thresholds.json", f"Training run · {model_id}", "training_run"


def _full_v1_active_state() -> dict[str, Any]:
    with STATE.lock:
        status_path = STATE.status_path
        active_id = STATE.run_id
        active = STATE.process is not None and STATE.process.poll() is None
    active_kind = STATE.kind
    if active and active_kind in {"training", "evaluation"} and active_id and active_id.startswith("fullv1_"):
        return _current_status()
    if active:
        return {"status": "busy", "stage": "busy", "process_active": True, "job_kind": active_kind, "run_id": active_id, "profile": FULL_V1_PROFILE}
    runs = _full_v1_runs()
    if runs:
        path = Path(runs[0]["run_dir"]) / "progress.json"
        state = _read_json(path)
        state["process_active"] = bool(path == status_path and active)
        state["job_kind"] = "training" if state.get("stage") not in {"evaluation", "finalizing"} else "evaluation"
        return state
    return {"status": "idle", "stage": "idle", "process_active": False, "profile": FULL_V1_PROFILE}


def _require_full_v1_active_training() -> Path:
    run_dir = _require_active_training()
    if not _is_full_v1_run(run_dir):
        raise RuntimeError("当前运行不是“完整数据集 V1”训练")
    return run_dir


def _full_v1_default_config() -> dict[str, Any]:
    config = default_training_config()
    # The Windows development laptop has 8 GB VRAM and benefits from a small
    # process pool. On Linux Brev, expose the larger A6000/CPU baseline; the
    # actual CUDA probe still verifies the requested batch before training.
    remote_defaults = os.name != "nt"
    config.update({
        "dataset_profile": FULL_V1_PROFILE,
        "dataset_sources": None,
        "negative_only_sources": [],
        "positive_only_sources": [],
        "train_only_sources": [],
        "class_names": FULL_V1_CLASSES.copy(),
        "budget_seconds": 0,
        "epochs_cap": 50,
        "patience": 5,
        "batch_size": 32 if remote_defaults else 8,
        "workers": min(24, max(2, (os.cpu_count() or 4) - 2)) if remote_defaults else 2,
        "seed": 2026,
        "output_root": str(FULL_V1_OUTPUT_ROOT),
    })
    return config


def _full_v1_health() -> dict[str, Any]:
    sources = [
        ("Dangerous Items", DATA_ROOT / "Dangerous Items" / "images" / "train"),
        ("ShanghaiTech Negative", DATA_ROOT / "Dangerous Items" / "shanghaitech_negative_manifest.csv"),
        ("WeaponDataset V11", DATA_ROOT / "WeaponDataset.v11i.yolov9" / "train" / "images"),
        ("OD SOHAS Detection", DATA_ROOT / "OD-WeaponDetection-master" / "Weapons and similar handled objects" / "Sohas_weapon-Detection" / "images"),
        ("Long Gun Dataset", DATA_ROOT / "Dataset" / "images"),
        ("Knife Dataset", DATA_ROOT / "Knife_Dataset" / "Knife_Dataset" / "images"),
        ("OD Knife Detection", DATA_ROOT / "OD-WeaponDetection-master" / "Knife_detection" / "Images"),
        ("OD Pistol Detection", DATA_ROOT / "OD-WeaponDetection-master" / "Pistol detection" / "Weapons"),
    ]
    try:
        environment = check_dependencies()
        dependency_error = None
    except Exception as exc:
        environment = None
        dependency_error = str(exc)
    latest_report = None
    report_paths = sorted(
        (FULL_V1_OUTPUT_ROOT / "datasets").glob("*/audit_report.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if (FULL_V1_OUTPUT_ROOT / "datasets").is_dir() else []
    for path in report_paths:
        report = _read_json(path)
        if report.get("profile") == FULL_V1_PROFILE:
            latest_report = report
            break
    return {
        "ok": True,
        "profile": FULL_V1_PROFILE,
        "config": _full_v1_default_config(),
        "classes": [{"id": 0, "name": "knife", "label": "刀"}, {"id": 1, "name": "gun", "label": "枪"}],
        "sources": [{"name": name, "path": str(path), "ready": path.exists()} for name, path in sources],
        "environment": environment,
        "host": {"platform": platform.platform(), "cpu_count": os.cpu_count()},
        "dependency_error": dependency_error,
        "latest_dataset_report": latest_report,
    }


def _latest_dataset_report() -> tuple[Path | None, dict[str, Any]]:
    root = OUTPUT_ROOT / "datasets"
    if not root.is_dir():
        return None, {}
    options = sorted(root.glob("*/audit_report.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in options:
        report = _read_json(path)
        if report.get("fingerprint") and Path(report.get("manifest", "")).is_file():
            return path.parent, report
    return None, {}


def _dataset_samples(query: dict[str, list[str]]) -> dict[str, Any]:
    cache_root, report = _latest_dataset_report()
    if not cache_root:
        return {"ready": False, "items": [], "page": 1, "pages": 0, "total": 0}
    source_filter = query.get("source", [""])[0]
    split_filter = query.get("split", [""])[0]
    class_filter = query.get("class_id", [""])[0]
    search = query.get("q", [""])[0].strip().casefold()
    page = max(1, int(query.get("page", ["1"])[0]))
    rows: list[dict[str, Any]] = []
    with Path(report["manifest"]).open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if source_filter and row["source"] != source_filter:
                continue
            if split_filter and row["split"] != split_filter:
                continue
            class_ids = {int(x) for x in row["classes"].split(",") if x.strip()}
            if class_filter == "negative" and class_ids:
                continue
            if class_filter not in {"", "negative"} and int(class_filter) not in class_ids:
                continue
            if search and search not in (row["source_image"] + " " + Path(row["source_image"]).name).casefold():
                continue
            rows.append(row)
    total = len(rows)
    pages = (total + PAGE_LIMIT - 1) // PAGE_LIMIT
    rows = rows[(page - 1) * PAGE_LIMIT : page * PAGE_LIMIT]
    items = []
    for row in rows:
        label_path = Path(row["label"])
        source_label_path = Path(row["source_label"])
        raw_label_text = source_label_path.read_text(encoding="utf-8-sig") if source_label_path.is_file() else ""
        boxes = []
        if label_path.is_file():
            for line in label_path.read_text(encoding="utf-8").splitlines():
                values = line.split()
                if len(values) == 5:
                    cls, x, y, w, h = values
                    boxes.append({"class_id": int(cls), "name": CLASS_NAMES[int(cls)], "label": CLASS_LABELS[int(cls)], "xywh": [float(x), float(y), float(w), float(h)]})
        image_path = Path(row["image"])
        items.append(
            {
                "source": row["source"],
                "source_split": row["source_split"],
                "split": row["split"],
                "source_image": row["source_image"],
                "source_label": row["source_label"],
                "raw_label_text": raw_label_text,
                "image": str(image_path.resolve()),
                "label": str(label_path.resolve()),
                "image_url": "/api/image?path=" + quote(str(image_path.resolve()), safe=""),
                "width": int(row["image_width"]),
                "height": int(row["image_height"]),
                "boxes": boxes,
                "negative": not boxes,
            }
        )
    return {"ready": True, "fingerprint": report["fingerprint"], "items": items, "page": page, "pages": pages, "total": total}


def _open_dataset_image(query: dict[str, list[str]]) -> tuple[bytes, str]:
    raw = query.get("path", [""])[0]
    image_path = Path(unquote(raw)).resolve()
    allowed = [DATA_ROOT.resolve(), OUTPUT_ROOT.resolve()]
    if not any(image_path.is_relative_to(root) for root in allowed):
        raise ValueError("Image path is outside the configured dataset and run folders")
    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")
    with Image.open(image_path) as image:
        canvas = ImageOps.exif_transpose(image).convert("RGB")
    label_raw = query.get("label", [""])[0]
    if label_raw:
        label_path = Path(unquote(label_raw)).resolve()
        if not any(label_path.is_relative_to(root) for root in allowed) or not label_path.is_file():
            raise ValueError("Label path is outside the configured workspace")
        draw = ImageDraw.Draw(canvas)
        for line in label_path.read_text(encoding="utf-8").splitlines():
            row = line.split()
            if len(row) != 5:
                continue
            cls, x, y, w, h = int(row[0]), *[float(v) for v in row[1:]]
            left = (x - w / 2) * canvas.width
            top = (y - h / 2) * canvas.height
            right = (x + w / 2) * canvas.width
            bottom = (y + h / 2) * canvas.height
            draw.rectangle((left, top, right, bottom), outline=(255, 65, 85), width=max(2, canvas.width // 600))
            draw.text((left + 3, max(0, top - 18)), CLASS_NAMES[cls], fill=(255, 65, 85), stroke_width=2, stroke_fill="white")
    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=88)
    return output.getvalue(), "image/jpeg"


def _run_inference(payload: dict[str, Any]) -> dict[str, Any]:
    if not _INFERENCE_LOCK.acquire(blocking=False):
        raise RuntimeError("已有图片推理正在运行，请稍后重试")
    try:
        status = _current_status()
        if status.get("process_active") and status.get("job_kind") in {"training", "evaluation"}:
            raise RuntimeError("训练或评估正在使用 GPU，暂时无法进行图片推理")
        model_id = str(payload.get("model_id") or payload.get("run_id", ""))
        if not model_id:
            raise ValueError("请选择一个可推理模型")
        checkpoint, threshold_report_path, model_label, model_source = _resolve_full_v1_inference_model(model_id)
        encoded = str(payload.get("image_base64", ""))
        if "," in encoded:
            encoded = encoded.split(",", 1)[1]
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 12 * 1024 * 1024:
            raise ValueError("图片需小于 12 MB")
        import cv2
        import numpy as np

        frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("无法读取上传的图片")
        from yolov9_weapon_infer import Yolov9WeaponRunner

        cache_key = str(checkpoint.resolve())
        runner = _INFERENCE_RUNNERS.get(cache_key)
        if runner is None:
            runner = Yolov9WeaponRunner(checkpoint, Path(default_training_config()["yolov9_root"]))
            _INFERENCE_RUNNERS[cache_key] = runner
        runner.load()
        threshold_mode = str(payload.get("threshold_mode", "Moderate")).casefold()
        if threshold_mode not in {"sensitive", "moderate", "certain"}:
            raise ValueError("阈值等级无效，请选择 Sensitive、Moderate 或 Certain")
        threshold_report = _read_json(threshold_report_path)
        selected_profile = next(
            (item for item in threshold_report.get("profiles", []) if str(item.get("profile", "")).casefold() == threshold_mode),
            None,
        )
        threshold_payload = (selected_profile or {}).get("thresholds") or {}
        if model_source == "training_run" and not threshold_payload:
            run_dir = checkpoint.parent
            threshold_payload = _read_json(run_dir / "validation_thresholds.json").get("thresholds", {})
        if model_source == "imported" and not selected_profile:
            raise ValueError(f"BREV 阈值报告中缺少 {threshold_mode.title()} profile")
        thresholds = {}
        for name in runner.class_names:
            value = threshold_payload.get(name)
            if value is None and model_source == "training_run":
                value = 0.25
            try:
                threshold = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"阈值报告缺少有效的 {name} 阈值") from None
            if not 0.0 <= threshold <= 1.0:
                raise ValueError(f"{name} 阈值必须在 0 到 1 之间")
            thresholds[name] = threshold
        detections = runner.infer(frame, thresholds)
        annotated = runner.annotate(frame, detections)
        ok, buffer = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            raise RuntimeError("Failed to encode the annotated preview")
        return {
            "detections": [
                {"class_id": item.class_id, "class_name": item.class_name, "confidence": item.confidence, "xyxy": item.xyxy, "threshold": item.threshold}
                for item in detections
            ],
            "thresholds": thresholds,
            "threshold_mode": (selected_profile or {}).get("profile", "Moderate"),
            "model_id": model_id,
            "model_label": model_label,
            "model_source": model_source,
            "image_base64": base64.b64encode(buffer).decode("ascii"),
        }
    finally:
        _INFERENCE_LOCK.release()


def _list_runs() -> list[dict[str, Any]]:
    base = OUTPUT_ROOT / "runs"
    if not base.is_dir():
        return []
    runs = []
    for progress_path in base.glob("*/progress.json"):
        state = _read_json(progress_path)
        run_dir = progress_path.parent
        if not state:
            continue
        test_per_class = state.get("test_per_class") or {}
        map_values = [float(row["map50"]) for row in test_per_class.values() if row.get("map50") is not None]
        map50 = sum(map_values) / len(map_values) if map_values else None
        runs.append(
            {
                "run_id": state.get("run_id", run_dir.name),
                "status": state.get("status", "unknown"),
                "stage": state.get("stage"),
                "stage_label": state.get("stage_label"),
                "created_at": state.get("started_at", progress_path.stat().st_mtime),
                "updated_at": state.get("updated_at", progress_path.stat().st_mtime),
                "epochs": state.get("epochs"),
                "epoch": state.get("epoch"),
                "map50": map50,
                "last_checkpoint": state.get("last_checkpoint"),
                "run_dir": str(run_dir.resolve()),
            }
        )
    runs.sort(key=lambda item: item["updated_at"], reverse=True)
    return runs


def _current_status() -> dict[str, Any]:
    with STATE.lock:
        status_path = STATE.status_path
        kind = STATE.kind
        process = STATE.process
        run_id = STATE.run_id
        error = STATE.error
        process_active = process is not None and process.poll() is None
    if status_path and status_path.is_file():
        result = _read_json(status_path)
    elif kind == "audit":
        result = _read_json(OUTPUT_ROOT / "audit_status.json")
    else:
        result = {}
    result.setdefault("status", "running" if process_active else "idle")
    active = process_active or (
        result.get("status") in {"running", "pause_requested", "stopping"}
        and _pid_exists(result.get("dashboard_child_pid"))
    )
    if not active and result.get("status") in {"running", "pause_requested", "stopping"}:
        failed_stage = result.get("stage")
        is_evaluation = failed_stage in {"evaluation", "finalizing", "epoch_fpr"}
        updates = {"status": "failed" if is_evaluation else "interrupted", "stage": "failed" if is_evaluation else "interrupted", "stage_label": "评估进程已停止，可继续评估" if is_evaluation else "训练进程已停止，可从最近完整 checkpoint 恢复"}
        if is_evaluation:
            updates["failed_stage"] = failed_stage
        if status_path:
            _patch_json(status_path, updates)
            result.update(updates)
    result["process_active"] = active
    result["job_kind"] = kind
    result["run_id"] = result.get("run_id") or run_id
    if error:
        result["dashboard_error"] = error
    if active and result.get("started_at"):
        segment_start = float(result.get("segment_started_at") or result["started_at"])
        elapsed_base = float(result.get("elapsed_seconds") or 0.0)
        result["elapsed_seconds"] = elapsed_base + max(0, time.time() - segment_start)
        if result.get("epoch") and result.get("epochs"):
            epoch_fraction = float(result.get("epoch_progress") or 0.0)
            result["overall_progress"] = min(1.0, (float(result["epoch"]) - 1 + epoch_fraction) / float(result["epochs"]))
        if result.get("stage") == "training" and result.get("estimated_epoch_seconds") and result.get("epochs"):
            left_epochs = max(0.0, float(result["epochs"]) - (float(result.get("epoch") or 0) - 1 + float(result.get("epoch_progress") or 0)))
            eta = left_epochs * float(result["estimated_epoch_seconds"])
            probe = result.get("probe") or {}
            one_eval = float(probe.get("estimated_validation_batch_seconds") or 0) * float(probe.get("validation_batches_per_epoch") or 0)
            result["eta_seconds"] = eta + 2 * one_eval
        elif result.get("stage") == "evaluation" and result.get("evaluation_images_done") and result.get("evaluation_images_total"):
            done = float(result["evaluation_images_done"])
            total = float(result["evaluation_images_total"])
            eval_elapsed = max(0.0, time.time() - float(result.get("evaluation_started_at") or segment_start))
            rate = eval_elapsed / max(1.0, done)
            remaining = max(0.0, total - done) * rate
            if result.get("evaluation_split") == "val":
                dataset_images = ((result.get("dataset") or {}).get("images") or {}).get("test", 0)
                remaining += float(dataset_images) * rate
            result["eta_seconds"] = remaining
    result["inference_active"] = _INFERENCE_LOCK.locked()
    return result


def _monitor_process(process: subprocess.Popen, kind: str, run_id: str | None, run_dir: Path | None, status_path: Path, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("a", encoding="utf-8", errors="replace") as log:
            assert process.stdout is not None
            for line in process.stdout:
                log.write(line)
                log.flush()
        result = process.wait()
    except Exception as exc:
        result = -1
        with STATE.lock:
            STATE.error = str(exc)
    with STATE.lock:
        if STATE.process is process:
            STATE.process = None
            if STATE.worker is threading.current_thread():
                STATE.worker = None
        if result != 0:
            STATE.error = f"后台工作进程退出，代码 {result}。详见 {log_path}。"


def _spawn_job(kind: str, command: list[str], *, run_id: str | None = None, run_dir: Path | None = None, status_path: Path) -> None:
    existing = _current_status()
    if existing.get("process_active"):
        raise RuntimeError("已有数据审计、训练或评估任务正在运行")
    with STATE.lock:
        if STATE.process is not None and STATE.process.poll() is None:
            raise RuntimeError("已有数据审计、训练或评估任务正在运行")
        if kind in {"training", "evaluation"} and _INFERENCE_LOCK.locked():
            raise RuntimeError("当前有图片推理任务正在使用模型；请等待推理结束后再开始训练或评估")
        STATE.error = None
        STATE.kind = kind
        STATE.run_id = run_id
        STATE.run_dir = run_dir
        STATE.status_path = status_path
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        STATE.process = process
        _patch_json(status_path, {"dashboard_child_pid": process.pid})
        log_path = (run_dir / "dashboard_driver.log") if run_dir else OUTPUT_ROOT / "audit_driver.log"
        worker = threading.Thread(target=_monitor_process, args=(process, kind, run_id, run_dir, status_path, log_path), daemon=True)
        STATE.worker = worker
        worker.start()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "WeaponYOLOWorkspace/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8", extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, value: Any, status: int = 200) -> None:
        self._send(status, _json_bytes(value))

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_JSON_BODY:
            raise ValueError("Request body is too large")
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/" or parsed.path == "/index.html":
                self._send(200, HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            elif parsed.path == "/full-dataset-v1":
                self._send(200, FULL_V1_HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            elif parsed.path == "/mall-negative-finetune":
                self._send(200, MALL_FINETUNE_HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            elif parsed.path == "/brev-hard-negative-finetune":
                self._send(200, HARD_NEGATIVE_FINETUNE_HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            elif parsed.path == "/selected-sources-50epoch":
                self._send(200, SELECTED_SOURCES_FINETUNE_HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            elif parsed.path == "/mall-negative-inference":
                self._send(200, MALL_INFERENCE_HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            elif parsed.path == "/api/hardneg-ft/health":
                self._send_json(_hard_negative_finetune_health())
            elif parsed.path == "/api/hardneg-ft/status":
                self._send_json(_hard_negative_finetune_active_state())
            elif parsed.path == "/api/hardneg-ft/runs":
                self._send_json({"runs": _hard_negative_finetune_runs()})
            elif parsed.path == "/api/hardneg-ft/metrics":
                run_id = query.get("run_id", [""])[0]
                run_dir = _safe_run_dir(run_id)
                if not _is_hard_negative_finetune_run(run_dir):
                    raise ValueError("指定运行不属于 BREV Hard-negative 后训练")
                metrics = []
                metrics_path = run_dir / "epoch_metrics.csv"
                if metrics_path.is_file():
                    with metrics_path.open("r", encoding="utf-8-sig", newline="") as stream:
                        metrics = list(csv.DictReader(stream))
                state = _read_json(run_dir / "progress.json")
                fpr = _read_json(run_dir / "epoch_false_positive_metrics.json")
                self._send_json({"metrics": metrics, "state": state, "false_positive_metrics": fpr})
            elif parsed.path == "/api/hardneg-ft/log":
                run_dir = _safe_run_dir(query.get("run_id", [""])[0])
                if not _is_hard_negative_finetune_run(run_dir):
                    raise ValueError("指定运行不属于 BREV Hard-negative 后训练")
                path = run_dir / "run.log"
                tail = path.read_text(encoding="utf-8", errors="replace")[-60000:] if path.is_file() else ""
                self._send_json({"log": tail})
            elif parsed.path == "/api/selected-ft/health":
                self._send_json(_selected_sources_finetune_health())
            elif parsed.path == "/api/selected-ft/status":
                self._send_json(_selected_sources_finetune_active_state())
            elif parsed.path == "/api/selected-ft/runs":
                self._send_json({"runs": _selected_sources_finetune_runs()})
            elif parsed.path == "/api/selected-ft/metrics":
                run_dir = _safe_run_dir(query.get("run_id", [""])[0])
                if not _is_selected_sources_finetune_run(run_dir):
                    raise ValueError("指定运行不属于所选九来源的训练")
                metrics = []
                metrics_path = run_dir / "epoch_metrics.csv"
                if metrics_path.is_file():
                    with metrics_path.open("r", encoding="utf-8-sig", newline="") as stream:
                        metrics = list(csv.DictReader(stream))
                self._send_json({
                    "metrics": metrics,
                    "state": _read_json(run_dir / "progress.json"),
                    "thresholds": _read_json(run_dir / "sensitivity_thresholds.json"),
                    "negative_summary": _read_json(run_dir / "negative_false_positive_summary.json"),
                })
            elif parsed.path == "/api/selected-ft/log":
                run_dir = _safe_run_dir(query.get("run_id", [""])[0])
                if not _is_selected_sources_finetune_run(run_dir):
                    raise ValueError("指定运行不属于所选九来源的训练")
                path = run_dir / "run.log"
                tail = path.read_text(encoding="utf-8", errors="replace")[-60000:] if path.is_file() else ""
                self._send_json({"log": tail})
            elif parsed.path == "/api/mall-ft/health":
                self._send_json(_mall_finetune_health())
            elif parsed.path == "/api/mall-ft/inference-models":
                self._send_json(_mall_inference_model_catalog())
            elif parsed.path == "/api/mall-ft/status":
                self._send_json(_mall_finetune_active_state())
            elif parsed.path == "/api/mall-ft/models":
                self._send_json(_mall_finetune_models())
            elif parsed.path == "/api/mall-ft/runs":
                self._send_json({"runs": _mall_finetune_runs()})
            elif parsed.path == "/api/mall-ft/metrics":
                run_id = query.get("run_id", [""])[0]
                run_dir = _safe_run_dir(run_id)
                if not _is_mall_finetune_run(run_dir):
                    raise ValueError("指定运行不属于 Mall 负样本后训练")
                metrics = []
                metrics_path = run_dir / "epoch_metrics.csv"
                if metrics_path.is_file():
                    with metrics_path.open("r", encoding="utf-8-sig", newline="") as stream:
                        metrics = list(csv.DictReader(stream))
                state = _read_json(run_dir / "progress.json")
                sensitivity = _read_json(run_dir / "sensitivity_thresholds.json")
                self._send_json({"metrics": metrics, "state": state, "sensitivity": sensitivity})
            elif parsed.path == "/api/mall-ft/log":
                run_dir = _safe_run_dir(query.get("run_id", [""])[0])
                if not _is_mall_finetune_run(run_dir):
                    raise ValueError("指定运行不属于 Mall 负样本后训练")
                path = run_dir / "run.log"
                tail = path.read_text(encoding="utf-8", errors="replace")[-60000:] if path.is_file() else ""
                self._send_json({"log": tail})
            elif parsed.path == "/api/full-v1/health":
                self._send_json(_full_v1_health())
            elif parsed.path == "/api/full-v1/status":
                self._send_json(_full_v1_active_state())
            elif parsed.path == "/api/full-v1/runs":
                self._send_json({"runs": _full_v1_runs()})
            elif parsed.path == "/api/full-v1/inference-models":
                self._send_json(_full_v1_inference_models())
            elif parsed.path == "/api/full-v1/metrics":
                run_id = query.get("run_id", [""])[0]
                run_dir = _safe_run_dir(run_id)
                if not _is_full_v1_run(run_dir):
                    raise ValueError("指定运行不属于完整数据集 V1")
                metrics = []
                metrics_path = run_dir / "epoch_metrics.csv"
                if metrics_path.is_file():
                    with metrics_path.open("r", encoding="utf-8-sig", newline="") as stream:
                        metrics = list(csv.DictReader(stream))
                state = _read_json(run_dir / "progress.json")
                sensitivity_path = run_dir / "sensitivity_thresholds.json"
                if sensitivity_path.is_file():
                    state["sensitivity_threshold_analysis"] = _read_json(sensitivity_path)
                self._send_json({"metrics": metrics, "state": state})
            elif parsed.path == "/api/full-v1/log":
                run_dir = _safe_run_dir(query.get("run_id", [""])[0])
                if not _is_full_v1_run(run_dir):
                    raise ValueError("指定运行不属于完整数据集 V1")
                path = run_dir / "run.log"
                tail = path.read_text(encoding="utf-8", errors="replace")[-60000:] if path.is_file() else ""
                self._send_json({"log": tail})
            elif parsed.path == "/api/health":
                config = default_training_config()
                try:
                    environment = check_dependencies()
                    dependency_error = None
                except Exception as exc:
                    environment = None
                    dependency_error = str(exc)
                self._send_json({"ok": True, "config": config, "environment": environment, "dependency_error": dependency_error, "classes": [{"id": i, "name": name, "label": CLASS_LABELS[i]} for i, name in enumerate(CLASS_NAMES)]})
            elif parsed.path == "/api/status":
                self._send_json(_current_status())
            elif parsed.path == "/api/runs":
                self._send_json({"runs": _list_runs()})
            elif parsed.path == "/api/dataset/summary":
                cache_root, report = _latest_dataset_report()
                self._send_json({"ready": bool(cache_root), "cache_root": str(cache_root.resolve()) if cache_root else None, "report": report})
            elif parsed.path == "/api/dataset/samples":
                self._send_json(_dataset_samples(query))
            elif parsed.path == "/api/image":
                body, kind = _open_dataset_image(query)
                self._send(200, body, kind)
            elif parsed.path == "/api/runs/metrics":
                run_dir = _safe_run_dir(query.get("run_id", [""])[0])
                metrics = []
                metrics_path = run_dir / "epoch_metrics.csv"
                if metrics_path.is_file():
                    with metrics_path.open("r", encoding="utf-8-sig", newline="") as stream:
                        metrics = list(csv.DictReader(stream))
                state = _read_json(run_dir / "progress.json")
                sensitivity_path = run_dir / "sensitivity_thresholds.json"
                if sensitivity_path.is_file():
                    state["sensitivity_threshold_analysis"] = _read_json(sensitivity_path)
                self._send_json({"metrics": metrics, "state": state})
            elif parsed.path == "/api/runs/log":
                run_dir = _safe_run_dir(query.get("run_id", [""])[0])
                path = run_dir / "run.log"
                tail = path.read_text(encoding="utf-8", errors="replace")[-60000:] if path.is_file() else ""
                self._send_json({"log": tail})
            elif parsed.path == "/api/runs/negative-metrics":
                run_dir = _safe_run_dir(query.get("run_id", [""])[0])
                state = _read_json(run_dir / "progress.json")
                self._send_json({"metrics": state.get("test_negative_metrics", {}), "thresholds": state.get("thresholds", {})})
            elif parsed.path == "/api/download":
                run_dir = _safe_run_dir(query.get("run_id", [""])[0])
                name = unquote(query.get("name", [""])[0])
                allowed_roots = [run_dir.resolve()]
                artifact = (run_dir / name).resolve()
                if not any(artifact.is_relative_to(root) for root in allowed_roots) or not artifact.is_file():
                    raise FileNotFoundError("Artifact not found")
                mime = mimetypes.guess_type(artifact.name)[0] or "application/octet-stream"
                self._send(200, artifact.read_bytes(), mime, {"Content-Disposition": f'attachment; filename="{artifact.name}"'})
            else:
                self._send_json({"error": "Not found"}, 404)
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, 404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 400)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._body()
            if parsed.path == "/api/selected-ft/start":
                environment = check_dependencies()
                initial_weights, selected_model = _resolve_mall_finetune_model(BREV_INFERENCE_MODEL_ID)
                config = default_training_config()
                config.update(SELECTED_SOURCES_MODEL_DEFAULTS)
                config.update({
                    "dataset_profile": SELECTED_SOURCES_FINETUNE_PROFILE,
                    "class_names": MALL_FINETUNE_CLASSES.copy(),
                    "dataset_sources": None,
                    "negative_only_sources": [],
                    "positive_only_sources": [],
                    "train_only_sources": [],
                    "initial_weights_path": str(initial_weights),
                    "selected_initial_model_id": BREV_INFERENCE_MODEL_ID,
                    "selected_initial_model_label": selected_model["label"],
                    "negative_source_assumption_confirmed": True,
                    "negative_source_assumption_confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "output_root": str(SELECTED_SOURCES_FINETUNE_OUTPUT_ROOT),
                    "budget_seconds": 0,
                    "epochs_cap": 10,
                    "patience": 3,
                    "early_stop_metric": "f05_fitness_mean",
                })
                if config.get("device") != "cpu" and not environment.get("cuda_available"):
                    raise RuntimeError("当前 Python/PyTorch 环境未检测到 CUDA；该训练页面默认使用 GPU，请先修复 CUDA 环境")
                from yolov9_weapon_pipeline import _validated_config

                config = _validated_config(config)
                run_id = time.strftime("sel50_%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
                run_dir = _safe_run_dir(run_id)
                run_dir.mkdir(parents=True, exist_ok=False)
                config["_run_dir"] = str(run_dir.resolve())
                config_file = run_dir / "user_config.json"
                atomic_write_json(config_file, config)
                state_path = run_dir / "progress.json"
                command = [sys.executable, "-u", str(PIPELINE_PATH), "train", "--config", str(config_file)]
                _spawn_job("training", command, run_id=run_id, run_dir=run_dir, status_path=state_path)
                self._send_json({
                    "started": True,
                    "run_id": run_id,
                    "message": "九来源训练已启动：最多 10 epochs，patience 3；早停指标为 0.5×F0.5 + 0.5×fitness，best checkpoint 仍由 validation fitness 选择。",
                }, 202)
            elif parsed.path == "/api/selected-ft/pause":
                run_dir = _require_selected_sources_active_training()
                atomic_write_json(run_dir / "control.json", {"command": "pause", "requested_at": time.time()})
                _patch_json(run_dir / "progress.json", {"status": "pause_requested"})
                self._send_json({"status": "pause_requested", "message": "将在当前完整 epoch 保存 checkpoint 后安全暂停"}, 202)
            elif parsed.path == "/api/selected-ft/cancel-pause":
                run_dir = _require_selected_sources_active_training()
                control = _read_json(run_dir / "control.json")
                if control.get("command") != "pause":
                    raise RuntimeError("当前没有待取消的安全暂停请求")
                atomic_write_json(run_dir / "control.json", {"command": "", "updated_at": time.time()})
                _patch_json(run_dir / "progress.json", {"status": "running"})
                self._send_json({"status": "running", "message": "已取消安全暂停请求"})
            elif parsed.path == "/api/selected-ft/stop":
                run_dir = _require_selected_sources_active_training()
                atomic_write_json(run_dir / "control.json", {"command": "stop", "requested_at": time.time()})
                _patch_json(run_dir / "progress.json", {"status": "stopping"})
                self._send_json({"status": "stopping", "message": "停止请求已发送；最近完整 checkpoint 会保留"}, 202)
            elif parsed.path == "/api/selected-ft/resume":
                run_id = str(payload.get("run_id", ""))
                run_dir = _safe_run_dir(run_id)
                state = _read_json(run_dir / "progress.json")
                if not _is_selected_sources_finetune_run(run_dir) or state.get("status") not in {"paused", "stopped", "interrupted", "failed"}:
                    raise RuntimeError("此九来源训练当前不能恢复训练")
                if state.get("failed_stage") in {"evaluation", "finalizing"}:
                    raise RuntimeError("训练已结束；请使用继续评估恢复最终评估")
                if not (run_dir / "resolved_training_config.json").is_file() or not (run_dir / "checkpoints" / "last.pt").is_file():
                    raise FileNotFoundError("找不到训练配置或最近完整 checkpoint，无法恢复")
                command = [sys.executable, "-u", str(PIPELINE_PATH), "resume", "--run-dir", str(run_dir)]
                _spawn_job("training", command, run_id=run_id, run_dir=run_dir, status_path=run_dir / "progress.json")
                self._send_json({"started": True, "run_id": run_id, "message": "正在从最近完整 epoch checkpoint 恢复训练"}, 202)
            elif parsed.path == "/api/selected-ft/evaluation/resume":
                run_id = str(payload.get("run_id", ""))
                run_dir = _safe_run_dir(run_id)
                state = _read_json(run_dir / "progress.json")
                if not _is_selected_sources_finetune_run(run_dir) or state.get("status") != "failed" or state.get("failed_stage") not in {"evaluation", "finalizing"}:
                    raise RuntimeError("此九来源训练当前没有可继续的最终评估")
                command = [sys.executable, "-u", str(PIPELINE_PATH), "eval-resume", "--run-dir", str(run_dir)]
                _spawn_job("evaluation", command, run_id=run_id, run_dir=run_dir, status_path=run_dir / "progress.json")
                self._send_json({"started": True, "run_id": run_id, "message": "正在从最佳验证权重继续最终评估"}, 202)
            elif parsed.path == "/api/hardneg-ft/start":
                environment = check_dependencies()
                model_id = BREV_INFERENCE_MODEL_ID
                initial_weights, selected_model = _resolve_mall_finetune_model(model_id)
                config = default_training_config()
                config.update(HARD_NEGATIVE_MODEL_DEFAULTS)
                config.update({
                    "dataset_profile": HARD_NEGATIVE_FINETUNE_PROFILE,
                    "class_names": MALL_FINETUNE_CLASSES.copy(),
                    "dataset_sources": None,
                    "negative_only_sources": [],
                    "positive_only_sources": [],
                    "train_only_sources": [],
                    "initial_weights_path": str(initial_weights),
                    "selected_initial_model_id": model_id,
                    "selected_initial_model_label": selected_model["label"],
                    "negative_source_assumption_confirmed": True,
                    "negative_source_assumption_confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "output_root": str(HARD_NEGATIVE_FINETUNE_OUTPUT_ROOT),
                    "budget_seconds": 0,
                    "epochs_cap": 5,
                    "patience": 0,
                    "track_negative_fpr": True,
                    "fpr_confidence_threshold": HARD_NEGATIVE_FPR_THRESHOLD,
                })
                if config.get("device") != "cpu" and not environment.get("cuda_available"):
                    raise RuntimeError("所选设备要求 CUDA，但当前 Python/PyTorch 环境未检测到 CUDA；请选择 CPU 或先修复匹配的 CUDA PyTorch 环境")
                from yolov9_weapon_pipeline import _validated_config

                config = _validated_config(config)
                run_id = time.strftime("hnft_%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
                run_dir = _safe_run_dir(run_id)
                run_dir.mkdir(parents=True, exist_ok=False)
                config["_run_dir"] = str(run_dir.resolve())
                config_file = run_dir / "user_config.json"
                atomic_write_json(config_file, config)
                state_path = run_dir / "progress.json"
                command = [sys.executable, "-u", str(PIPELINE_PATH), "train", "--config", str(config_file)]
                _spawn_job("training", command, run_id=run_id, run_dir=run_dir, status_path=state_path)
                self._send_json({"started": True, "run_id": run_id, "message": "BREV Best 五 epoch hard-negative 后训练已启动；误报率将使用固定 0.25 阈值逐 epoch 汇报，最终仍按 validation fitness 选模。"}, 202)
            elif parsed.path == "/api/hardneg-ft/resume":
                run_id = str(payload.get("run_id", ""))
                run_dir = _safe_run_dir(run_id)
                state = _read_json(run_dir / "progress.json")
                if not _is_hard_negative_finetune_run(run_dir) or state.get("status") not in {"paused", "stopped", "interrupted", "failed"}:
                    raise RuntimeError("此 BREV hard-negative 运行当前不能恢复训练")
                if state.get("failed_stage") in {"evaluation", "finalizing", "epoch_fpr"}:
                    raise RuntimeError("训练已结束；请使用继续评估恢复误报率与最终评估")
                if not (run_dir / "resolved_training_config.json").is_file() or not (run_dir / "checkpoints" / "last.pt").is_file():
                    raise FileNotFoundError("找不到训练配置或最近完整 checkpoint，无法恢复")
                command = [sys.executable, "-u", str(PIPELINE_PATH), "resume", "--run-dir", str(run_dir)]
                _spawn_job("training", command, run_id=run_id, run_dir=run_dir, status_path=run_dir / "progress.json")
                self._send_json({"started": True, "run_id": run_id, "message": "正在从最近完整 epoch checkpoint 恢复训练"}, 202)
            elif parsed.path == "/api/hardneg-ft/evaluation/resume":
                run_id = str(payload.get("run_id", ""))
                run_dir = _safe_run_dir(run_id)
                state = _read_json(run_dir / "progress.json")
                if not _is_hard_negative_finetune_run(run_dir) or state.get("status") != "failed" or state.get("failed_stage") not in {"evaluation", "finalizing", "epoch_fpr"}:
                    raise RuntimeError("此 BREV hard-negative 运行当前没有可继续的评估")
                command = [sys.executable, "-u", str(PIPELINE_PATH), "eval-resume", "--run-dir", str(run_dir)]
                _spawn_job("evaluation", command, run_id=run_id, run_dir=run_dir, status_path=run_dir / "progress.json")
                self._send_json({"started": True, "run_id": run_id, "message": "正在从保存的 checkpoint 继续误报率与最终评估"}, 202)
            elif parsed.path == "/api/mall-ft/inference":
                self._send_json(_run_mall_finetune_inference(payload))
            elif parsed.path == "/api/mall-ft/start":
                if payload.get("expanded_negative_confirmed") is not True:
                    raise ValueError("开始前必须确认：Mall、AIRTLab NonViolent、BusViolence NonFight 和剩余 NoGUN 图像都将作为 Knife/Gun 负样本")
                from yolov9_expanded_negative_finetune import inspect_negative_sources

                inspect_negative_sources()
                environment = check_dependencies()
                model_id = str(payload.get("model_id", ""))
                initial_weights, selected_model = _resolve_mall_finetune_model(model_id)
                if not isinstance(payload.get("config", {}), dict):
                    raise ValueError("config 必须是 JSON 对象")
                config = default_training_config()
                config.update(MALL_FINETUNE_MODEL_DEFAULTS)
                allowed = {
                    "imgsz", "batch_size", "workers", "device", "optimizer", "lr0", "lrf", "momentum",
                    "weight_decay", "warmup_epochs", "seed", "mosaic", "close_mosaic_epochs", "mixup",
                    "hsv_h", "hsv_s", "hsv_v", "translate", "scale", "fliplr", "flipud", "iou_threshold",
                }
                config.update({key: value for key, value in payload.get("config", {}).items() if key in allowed})
                config.update({
                    "dataset_profile": EXPANDED_NEGATIVE_FINETUNE_PROFILE,
                    "class_names": MALL_FINETUNE_CLASSES.copy(),
                    "dataset_sources": None,
                    "negative_only_sources": [],
                    "positive_only_sources": [],
                    "train_only_sources": [],
                    "initial_weights_path": str(initial_weights),
                    "selected_initial_model_id": model_id,
                    "selected_initial_model_label": selected_model["label"],
                    "negative_source_assumption_confirmed": True,
                    "negative_source_assumption_confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "output_root": str(MALL_FINETUNE_OUTPUT_ROOT),
                    "budget_seconds": 0,
                    "epochs_cap": 5,
                    "patience": 0,
                })
                if config.get("device") != "cpu" and not environment.get("cuda_available"):
                    raise RuntimeError("所选设备要求 CUDA，但当前 Python/PyTorch 环境未检测到 CUDA；请选择 CPU 或先修复匹配的 CUDA PyTorch 环境")
                from yolov9_weapon_pipeline import _validated_config

                config = _validated_config(config)
                run_id = time.strftime("mallft_%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
                run_dir = _safe_run_dir(run_id)
                run_dir.mkdir(parents=True, exist_ok=False)
                config["_run_dir"] = str(run_dir.resolve())
                config_file = run_dir / "user_config.json"
                atomic_write_json(config_file, config)
                state_path = run_dir / "progress.json"
                command = [sys.executable, "-u", str(PIPELINE_PATH), "train", "--config", str(config_file)]
                _spawn_job("training", command, run_id=run_id, run_dir=run_dir, status_path=state_path)
                self._send_json({"started": True, "run_id": run_id, "message": "扩展负样本后训练已启动；训练结束后将自动进行验证、阈值校准和锁定测试集评估"}, 202)
            elif parsed.path == "/api/mall-ft/pause":
                run_dir = _require_mall_finetune_active_training()
                atomic_write_json(run_dir / "control.json", {"command": "pause", "requested_at": time.time()})
                _patch_json(run_dir / "progress.json", {"status": "pause_requested"})
                self._send_json({"status": "pause_requested", "message": "将在当前完整 epoch 及验证结束后安全暂停"}, 202)
            elif parsed.path == "/api/mall-ft/cancel-pause":
                run_dir = _require_mall_finetune_active_training()
                control = _read_json(run_dir / "control.json")
                if control.get("command") != "pause":
                    raise RuntimeError("当前没有待取消的安全暂停请求")
                atomic_write_json(run_dir / "control.json", {"command": "", "updated_at": time.time()})
                _patch_json(run_dir / "progress.json", {"status": "running"})
                self._send_json({"status": "running", "message": "已取消安全暂停请求"})
            elif parsed.path == "/api/mall-ft/stop":
                run_dir = _require_mall_finetune_active_training()
                atomic_write_json(run_dir / "control.json", {"command": "stop", "requested_at": time.time()})
                _patch_json(run_dir / "progress.json", {"status": "stopping"})
                self._send_json({"status": "stopping", "message": "停止请求已发送；最近完整 checkpoint 会保留"}, 202)
            elif parsed.path == "/api/mall-ft/resume":
                run_id = str(payload.get("run_id", ""))
                run_dir = _safe_run_dir(run_id)
                if not _is_mall_finetune_run(run_dir):
                    raise ValueError("指定运行不属于 Mall 负样本后训练")
                state = _read_json(run_dir / "progress.json")
                if state.get("status") not in {"paused", "stopped", "interrupted", "failed"}:
                    raise RuntimeError(f"当前状态不能恢复训练：{state.get('status', 'unknown')}")
                if state.get("failed_stage") in {"evaluation", "finalizing"}:
                    raise RuntimeError("训练已结束；请使用继续评估恢复最终评估")
                if not (run_dir / "resolved_training_config.json").is_file() or not (run_dir / "checkpoints" / "last.pt").is_file():
                    raise FileNotFoundError("找不到完整配置或最近 epoch checkpoint，无法恢复")
                command = [sys.executable, "-u", str(PIPELINE_PATH), "resume", "--run-dir", str(run_dir)]
                _spawn_job("training", command, run_id=run_id, run_dir=run_dir, status_path=run_dir / "progress.json")
                self._send_json({"started": True, "run_id": run_id, "message": "正在从最近完整 epoch 恢复后训练"}, 202)
            elif parsed.path == "/api/mall-ft/evaluation/resume":
                run_id = str(payload.get("run_id", ""))
                run_dir = _safe_run_dir(run_id)
                state = _read_json(run_dir / "progress.json")
                if not _is_mall_finetune_run(run_dir) or state.get("status") != "failed" or state.get("failed_stage") not in {"evaluation", "finalizing"}:
                    raise RuntimeError("此运行当前没有可继续的最终评估")
                command = [sys.executable, "-u", str(PIPELINE_PATH), "eval-resume", "--run-dir", str(run_dir)]
                _spawn_job("evaluation", command, run_id=run_id, run_dir=run_dir, status_path=run_dir / "progress.json")
                self._send_json({"started": True, "run_id": run_id, "message": "正在从最佳验证权重继续评估"}, 202)
            elif parsed.path == "/api/full-v1/start":
                if not isinstance(payload.get("config", {}), dict):
                    raise ValueError("config 必须是 JSON 对象")
                config = _full_v1_default_config()
                allowed = {
                    "epochs_cap", "patience", "imgsz", "batch_size", "workers", "device",
                    "optimizer", "lr0", "lrf", "momentum", "weight_decay", "warmup_epochs",
                    "seed", "mosaic", "close_mosaic_epochs", "mixup", "hsv_h", "hsv_s",
                    "hsv_v", "translate", "scale", "fliplr", "flipud", "iou_threshold",
                }
                config.update({key: value for key, value in payload.get("config", {}).items() if key in allowed})
                # Server-owned fixed recipe: the page cannot silently add or
                # remove sources or change the two-class mapping.
                config.update({"dataset_profile": FULL_V1_PROFILE, "class_names": FULL_V1_CLASSES.copy(), "dataset_sources": None, "output_root": str(FULL_V1_OUTPUT_ROOT), "budget_seconds": 0})
                from yolov9_weapon_pipeline import _validated_config

                config = _validated_config(config)  # Fail on a missing weight/path before launching a child process.
                run_id = time.strftime("fullv1_%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
                run_dir = _safe_run_dir(run_id)
                run_dir.mkdir(parents=True, exist_ok=False)
                config["_run_dir"] = str(run_dir.resolve())
                config_file = run_dir / "user_config.json"
                atomic_write_json(config_file, config)
                state_path = run_dir / "progress.json"
                command = [sys.executable, "-u", str(PIPELINE_PATH), "train", "--config", str(config_file)]
                _spawn_job("training", command, run_id=run_id, run_dir=run_dir, status_path=state_path)
                self._send_json({"started": True, "run_id": run_id, "run_dir": str(run_dir.resolve()), "message": "完整数据集 V1 训练已启动"}, 202)
            elif parsed.path == "/api/full-v1/pause":
                run_dir = _require_full_v1_active_training()
                atomic_write_json(run_dir / "control.json", {"command": "pause", "requested_at": time.time()})
                _patch_json(run_dir / "progress.json", {"status": "pause_requested"})
                self._send_json({"status": "pause_requested", "message": "将在当前完整 epoch 保存 checkpoint 后安全暂停"}, 202)
            elif parsed.path == "/api/full-v1/cancel-pause":
                run_dir = _require_full_v1_active_training()
                control = _read_json(run_dir / "control.json")
                if control.get("command") != "pause":
                    raise RuntimeError("当前没有待取消的安全暂停请求")
                atomic_write_json(run_dir / "control.json", {"command": "", "updated_at": time.time()})
                _patch_json(run_dir / "progress.json", {"status": "running"})
                self._send_json({"status": "running", "message": "已取消安全暂停请求"})
            elif parsed.path == "/api/full-v1/stop":
                run_dir = _require_full_v1_active_training()
                atomic_write_json(run_dir / "control.json", {"command": "stop", "requested_at": time.time()})
                _patch_json(run_dir / "progress.json", {"status": "stopping"})
                self._send_json({"status": "stopping", "message": "停止请求已发送，最近完整 checkpoint 会保留"}, 202)
            elif parsed.path == "/api/full-v1/resume":
                run_id = str(payload.get("run_id", ""))
                run_dir = _safe_run_dir(run_id)
                if not _is_full_v1_run(run_dir):
                    raise ValueError("指定运行不属于完整数据集 V1")
                state = _read_json(run_dir / "progress.json")
                if state.get("status") not in {"paused", "stopped", "interrupted", "failed"}:
                    raise RuntimeError(f"当前状态不能恢复训练：{state.get('status', 'unknown')}")
                if state.get("failed_stage") in {"evaluation", "finalizing"}:
                    raise RuntimeError("训练已经结束；请使用继续评估恢复最终评估")
                if not (run_dir / "resolved_training_config.json").is_file() or not (run_dir / "checkpoints" / "last.pt").is_file():
                    raise FileNotFoundError("找不到完整配置或最近 epoch checkpoint，无法恢复")
                command = [sys.executable, "-u", str(PIPELINE_PATH), "resume", "--run-dir", str(run_dir)]
                _spawn_job("training", command, run_id=run_id, run_dir=run_dir, status_path=run_dir / "progress.json")
                self._send_json({"started": True, "run_id": run_id, "message": "正在从最近完整 epoch 恢复训练"}, 202)
            elif parsed.path == "/api/full-v1/evaluation/resume":
                run_id = str(payload.get("run_id", ""))
                run_dir = _safe_run_dir(run_id)
                state = _read_json(run_dir / "progress.json")
                if not _is_full_v1_run(run_dir) or state.get("status") != "failed" or state.get("failed_stage") not in {"evaluation", "finalizing"}:
                    raise RuntimeError("此运行当前没有可继续的最终评估")
                command = [sys.executable, "-u", str(PIPELINE_PATH), "eval-resume", "--run-dir", str(run_dir)]
                _spawn_job("evaluation", command, run_id=run_id, run_dir=run_dir, status_path=run_dir / "progress.json")
                self._send_json({"started": True, "run_id": run_id, "message": "正在从最佳权重继续验证和测试"}, 202)
            elif parsed.path == "/api/full-v1/inference":
                self._send_json(_run_inference(payload))
            elif parsed.path == "/api/dataset/audit":
                status_path = OUTPUT_ROOT / "audit_status.json"
                events_path = OUTPUT_ROOT / "audit_events.jsonl"
                audit_config = default_training_config()
                audit_config.update(payload.get("config", {}))
                audit_config_path = OUTPUT_ROOT / "audit_user_config.json"
                atomic_write_json(audit_config_path, audit_config)
                command = [sys.executable, "-u", str(PIPELINE_PATH), "audit", "--output-root", str(OUTPUT_ROOT), "--state", str(status_path), "--events", str(events_path), "--config", str(audit_config_path)]
                _spawn_job("audit", command, status_path=status_path)
                self._send_json({"started": True, "message": "数据审计已开始"}, 202)
            elif parsed.path == "/api/training/start":
                config = default_training_config()
                config.update(payload.get("config", {}))
                run_id = time.strftime("yolov9_%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
                run_dir = _safe_run_dir(run_id)
                run_dir.mkdir(parents=True, exist_ok=False)
                config["_run_dir"] = str(run_dir.resolve())
                config_file = run_dir / "user_config.json"
                atomic_write_json(config_file, config)
                state_path = run_dir / "progress.json"
                command = [sys.executable, "-u", str(PIPELINE_PATH), "train", "--config", str(config_file)]
                _spawn_job("training", command, run_id=run_id, run_dir=run_dir, status_path=state_path)
                self._send_json({"started": True, "run_id": run_id, "run_dir": str(run_dir.resolve())}, 202)
            elif parsed.path == "/api/training/pause":
                run_dir = _require_active_training()
                atomic_write_json(run_dir / "control.json", {"command": "pause", "requested_at": time.time()})
                _patch_json(run_dir / "progress.json", {"status": "pause_requested"})
                self._send_json({"status": "pause_requested", "message": "将在完整 epoch 保存 checkpoint 后暂停"}, 202)
            elif parsed.path == "/api/training/cancel-pause":
                run_dir = _require_active_training()
                control = _read_json(run_dir / "control.json")
                if control.get("command") != "pause":
                    raise RuntimeError("没有待取消的安全暂停请求")
                atomic_write_json(run_dir / "control.json", {"command": "", "updated_at": time.time()})
                _patch_json(run_dir / "progress.json", {"status": "running"})
                self._send_json({"status": "running", "message": "已取消安全暂停请求"})
            elif parsed.path == "/api/training/stop":
                run_dir = _require_active_training()
                atomic_write_json(run_dir / "control.json", {"command": "stop", "requested_at": time.time()})
                _patch_json(run_dir / "progress.json", {"status": "stopping"})
                self._send_json({"status": "stopping", "message": "训练将在当前 batch 结束后停止，并保留最近完整 checkpoint"}, 202)
            elif parsed.path == "/api/training/finalize":
                run_dir = _require_active_training()
                atomic_write_json(run_dir / "control.json", {"command": "budget_finalize", "requested_at": time.time()})
                _patch_json(run_dir / "progress.json", {"finalization_requested": True})
                self._send_json({"status": "finalize_requested", "message": "当前完整 epoch 保存后，将开始最终验证和测试"}, 202)
            elif parsed.path == "/api/training/resume":
                run_id = str(payload.get("run_id", ""))
                run_dir = _safe_run_dir(run_id)
                state = _read_json(run_dir / "progress.json")
                if state.get("status") not in {"paused", "stopped", "interrupted", "failed"}:
                    raise RuntimeError(f"此运行不能恢复（当前状态：{state.get('status', 'unknown')}）")
                if state.get("failed_stage") in {"evaluation", "finalizing"}:
                    raise RuntimeError("训练已完成，请使用“继续最终评估”从最佳模型恢复")
                config_file = run_dir / "resolved_training_config.json"
                if not config_file.is_file():
                    raise FileNotFoundError("该运行没有可恢复的完整训练配置")
                if not (run_dir / "checkpoints" / "last.pt").is_file() and not (run_dir / "official_train" / "train" / "weights" / "last.pt").is_file():
                    raise FileNotFoundError("该运行没有可恢复的 last.pt checkpoint")
                command = [sys.executable, "-u", str(PIPELINE_PATH), "resume", "--run-dir", str(run_dir)]
                _spawn_job("training", command, run_id=run_id, run_dir=run_dir, status_path=run_dir / "progress.json")
                self._send_json({"started": True, "run_id": run_id, "message": "正在恢复 checkpoint"}, 202)
            elif parsed.path == "/api/evaluation/resume":
                run_id = str(payload.get("run_id", ""))
                run_dir = _safe_run_dir(run_id)
                state = _read_json(run_dir / "progress.json")
                if state.get("status") != "failed" or state.get("failed_stage") not in {"evaluation", "finalizing"}:
                    raise RuntimeError("该运行当前没有可继续的最终评估")
                if not (run_dir / "official_train" / "train" / "weights" / "best.pt").is_file():
                    raise FileNotFoundError("没有可继续评估的最佳模型")
                command = [sys.executable, "-u", str(PIPELINE_PATH), "eval-resume", "--run-dir", str(run_dir)]
                _spawn_job("evaluation", command, run_id=run_id, run_dir=run_dir, status_path=run_dir / "progress.json")
                self._send_json({"started": True, "run_id": run_id, "message": "正在从最佳权重继续最终评估"}, 202)
            elif parsed.path == "/api/inference":
                self._send_json(_run_inference(payload))
            else:
                self._send_json({"error": "Not found"}, 404)
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, 409)
        except Exception as exc:
            self._send_json({"error": str(exc), "traceback": traceback.format_exc(limit=2)}, 400)


_INFERENCE_RUNNERS: dict[str, Any] = {}


def _require_active_training() -> Path:
    with STATE.lock:
        process_active = STATE.process is not None and STATE.process.poll() is None
        persisted_active = bool(STATE.status_path and _pid_exists(_read_json(STATE.status_path).get("dashboard_child_pid")))
        if STATE.kind != "training" or STATE.run_dir is None or not (process_active or persisted_active):
            raise RuntimeError("当前没有运行中的训练任务")
        return STATE.run_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Bind only to loopback by default")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--no-browser", action="store_true", help="Do not open the local page")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("For safety, this dashboard only binds to loopback addresses.")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    _recover_saved_runs()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{args.port}/"
    print(f"YOLOv9-T weapon training dashboard: {url}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
