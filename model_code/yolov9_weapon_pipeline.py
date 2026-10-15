"""Dataset preparation, YOLOv9-T training, calibration and test pipeline."""

from __future__ import annotations

import argparse
import copy
import csv
import gc
import hashlib
import json
import math
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from yolov9_weapon_workspace import (
    CLASS_NAMES,
    DATASET_SOURCE_IDS,
    HARD_NEGATIVE_FINETUNE_OUTPUT_ROOT,
    MALL_FINETUNE_OUTPUT_ROOT,
    OUTPUT_ROOT,
    PROJECT_ROOT,
    YOLO_ROOT,
    atomic_write_json,
    audit_and_build_dataset,
    default_training_config,
    json_safe,
)


REQUIRED_IMPORTS = {
    "torch": "torch",
    "numpy": "numpy",
    "yaml": "PyYAML",
    "PIL": "Pillow",
    "cv2": "opencv-python-headless",
    "pandas": "pandas",
    "seaborn": "seaborn",
    "scipy": "scipy",
    "tqdm": "tqdm",
    "matplotlib": "matplotlib",
    "thop": "thop",
    "requests": "requests",
    "IPython": "ipython",
    "albumentations": "albumentations",
    "tensorboard": "tensorboard",
    "git": "GitPython",
    "psutil": "psutil",
}

MALL_FINETUNE_PROFILE = "mall_negative_finetune_v1"
EXPANDED_NEGATIVE_FINETUNE_PROFILE = "expanded_negative_finetune_v1"
HARD_NEGATIVE_FINETUNE_PROFILE = "brev_hard_negative_finetune_v1"
SELECTED_SOURCES_FINETUNE_PROFILE = "selected_sources_50epoch_v1"
FINETUNE_PROFILES = {
    MALL_FINETUNE_PROFILE,
    EXPANDED_NEGATIVE_FINETUNE_PROFILE,
    HARD_NEGATIVE_FINETUNE_PROFILE,
    SELECTED_SOURCES_FINETUNE_PROFILE,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _upstream_source_info(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"root": str(root.resolve())}
    try:
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        result["git_commit"] = commit.stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, check=True)
        result["tracked_files_modified"] = bool(dirty.stdout.strip())
    except Exception as exc:
        result["git_info_error"] = str(exc)
    for name in ("train_dual.py", "val_dual.py", "models/detect/yolov9-t.yaml"):
        path = root / name
        if path.is_file():
            result.setdefault("file_sha256", {})[name] = _sha256(path)
    return result


def _read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default or {})


def _update_state(path: Path, update: dict[str, Any], event: str | None = None, events_path: Path | None = None) -> None:
    state = _read_json(path)
    state.update(json_safe(update))
    state["updated_at"] = time.time()
    atomic_write_json(path, state)
    if event and events_path:
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "time": time.time(), **json_safe(update)}, ensure_ascii=False) + "\n")


def check_dependencies() -> dict[str, Any]:
    import importlib
    import importlib.metadata

    missing = []
    versions: dict[str, str] = {}
    for module, package in REQUIRED_IMPORTS.items():
        try:
            imported = importlib.import_module(module)
            try:
                versions[package] = str(getattr(imported, "__version__")) if module == "cv2" and getattr(imported, "__version__", None) else importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = "installed; version unavailable"
        except Exception:
            missing.append(package)
    if missing:
        raise RuntimeError(
            "当前 Python 环境缺少 YOLOv9 训练依赖："
            + ", ".join(missing)
            + ". 请运行 `python -m pip install -r model_code/requirements-yolov9-dashboard.txt`。"
        )
    import torch

    return {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "torch": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "gpu_memory_bytes": torch.cuda.get_device_properties(0).total_memory if torch.cuda.is_available() else None,
        "packages": versions,
        "yolov9_source": _upstream_source_info(YOLO_ROOT),
    }


def _validated_config(payload: dict[str, Any]) -> dict[str, Any]:
    config = default_training_config()
    config.update(payload)
    if config.get("dataset_profile") == HARD_NEGATIVE_FINETUNE_PROFILE:
        config.setdefault("hard_negative_loss_weight", 1.0)
        config.setdefault("hard_negative_loss_gamma", 2.0)
    for key, default in (("dataset_sources", None), ("negative_only_sources", []), ("positive_only_sources", []), ("train_only_sources", [])):
        value = config.get(key, default)
        if value is None and key == "dataset_sources":
            config[key] = None
            continue
        if not isinstance(value, list) or any(not isinstance(source, str) for source in value):
            raise ValueError(f"{key} must be a list of dataset source IDs")
        unknown = set(value) - set(DATASET_SOURCE_IDS)
        if unknown:
            raise ValueError(f"Unknown {key}: {sorted(unknown)}")
        config[key] = sorted(set(value))
    if not config["dataset_sources"] and config["dataset_sources"] is not None:
        raise ValueError("dataset_sources cannot be an empty list")
    for policy_key in ("negative_only_sources", "positive_only_sources", "train_only_sources"):
        if not set(config[policy_key]).issubset(set(config["dataset_sources"] or DATASET_SOURCE_IDS)):
            raise ValueError(f"{policy_key} must be a subset of dataset_sources")
    if set(config["negative_only_sources"]) & set(config["positive_only_sources"]):
        raise ValueError("A source cannot be both positive-only and negative-only")
    class_names = config.get("class_names")
    if class_names is None:
        class_names = list(CLASS_NAMES)
    if not isinstance(class_names, list) or not class_names or any(not isinstance(name, str) for name in class_names):
        raise ValueError("class_names must be a non-empty list of weapon class names")
    unknown_classes = set(class_names) - set(CLASS_NAMES)
    if unknown_classes or len(class_names) != len(set(class_names)):
        raise ValueError(f"class_names contain unknown or duplicate values: {sorted(unknown_classes)}")
    config["class_names"] = class_names

    raw_device = config.get("device", "0")
    if isinstance(raw_device, str) and raw_device.strip().casefold() == "cpu":
        config["device"] = "cpu"
    elif isinstance(raw_device, int) and not isinstance(raw_device, bool) and raw_device >= 0:
        config["device"] = str(raw_device)
    elif isinstance(raw_device, str) and re.fullmatch(r"[0-9]+", raw_device.strip()):
        config["device"] = str(int(raw_device.strip()))
    else:
        raise ValueError("device must be 'cpu' or a non-negative GPU index such as '0'")

    int_ranges = {
        # Zero means there is no wall-clock cutoff.
        "budget_seconds": (0, 24 * 3600),
        "epochs_cap": (1, 500),
        "patience": (0, 100),
        "imgsz": (320, 1280),
        "batch_size": (1, 128),
        "workers": (0, 28),
        "seed": (0, 2**31 - 1),
    }
    for key, (minimum, maximum) in int_ranges.items():
        raw_value = config.get(key)
        if isinstance(raw_value, bool):
            raise ValueError(f"{key} must be an integer between {minimum} and {maximum}")
        try:
            numeric_value = float(raw_value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{key} must be an integer between {minimum} and {maximum}") from exc
        if not math.isfinite(numeric_value) or not numeric_value.is_integer():
            raise ValueError(f"{key} must be an integer between {minimum} and {maximum}")
        value = int(numeric_value)
        if not minimum <= value <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        config[key] = value
    if config.get("stop_after_epoch") is not None:
        raw_stop_epoch = config["stop_after_epoch"]
        if isinstance(raw_stop_epoch, bool):
            raise ValueError("stop_after_epoch must be an integer between 1 and epochs_cap")
        try:
            numeric_stop_epoch = float(raw_stop_epoch)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("stop_after_epoch must be an integer between 1 and epochs_cap") from exc
        if not math.isfinite(numeric_stop_epoch) or not numeric_stop_epoch.is_integer():
            raise ValueError("stop_after_epoch must be an integer between 1 and epochs_cap")
        stop_epoch = int(numeric_stop_epoch)
        if not 1 <= stop_epoch <= config["epochs_cap"]:
            raise ValueError("stop_after_epoch must be between 1 and epochs_cap")
        config["stop_after_epoch"] = stop_epoch
    float_ranges = {
        "lr0": (1e-6, 0.1),
        "lrf": (0.001, 1.0),
        "momentum": (0.0, 0.999),
        "weight_decay": (0.0, 0.01),
        "warmup_epochs": (0.0, 10.0),
        "mosaic": (0.0, 1.0),
        "close_mosaic_epochs": (0, 10),
        "mixup": (0.0, 1.0),
        "hsv_h": (0.0, 0.1),
        "hsv_s": (0.0, 1.0),
        "hsv_v": (0.0, 1.0),
        "translate": (0.0, 0.5),
        "scale": (0.0, 1.0),
        "fliplr": (0.0, 1.0),
        "flipud": (0.0, 1.0),
        "iou_threshold": (0.1, 0.95),
    }
    if config.get("dataset_profile") == HARD_NEGATIVE_FINETUNE_PROFILE:
        float_ranges.update({"hard_negative_loss_weight": (0.0, 5.0), "hard_negative_loss_gamma": (0.0, 5.0)})
    for key, (minimum, maximum) in float_ranges.items():
        raw_value = config.get(key)
        if isinstance(raw_value, bool):
            raise ValueError(f"{key} must be a number between {minimum} and {maximum}")
        try:
            value = float(raw_value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{key} must be a number between {minimum} and {maximum}") from exc
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        config[key] = value
    if not isinstance(config.get("optimizer"), str) or config["optimizer"] not in {"SGD", "Adam", "AdamW"}:
        raise ValueError("optimizer must be SGD, Adam or AdamW")
    for key in ("project_root", "data_root", "yolov9_root", "pretrained", "model_config", "output_root"):
        config[key] = str(Path(config[key]).expanduser().resolve())
    if Path(config["yolov9_root"]) != YOLO_ROOT.resolve():
        # This workspace is tied to its checked-out upstream YOLOv9 copy.
        # Require an explicit existing official copy but keep all cache paths local.
        if not (Path(config["yolov9_root"]) / "train_dual.py").is_file():
            raise FileNotFoundError(f"YOLOv9 training code not found under {config['yolov9_root']}")
    finetune = config.get("dataset_profile") in FINETUNE_PROFILES
    if not finetune and not Path(config["pretrained"]).is_file():
        raise FileNotFoundError(f"COCO pretrained YOLOv9-T weights not found: {config['pretrained']}")
    if not Path(config["model_config"]).is_file():
        raise FileNotFoundError(f"YOLOv9-T model configuration not found: {config['model_config']}")
    initial_weights = config.get("initial_weights_path")
    if initial_weights:
        initial_path = Path(initial_weights).expanduser().resolve()
        trusted_model_roots = (
            (PROJECT_ROOT / "artifacts").resolve(),
            (PROJECT_ROOT / "model_runs" / "yolov9_full_dataset_v1").resolve(),
            MALL_FINETUNE_OUTPUT_ROOT.resolve(),
            HARD_NEGATIVE_FINETUNE_OUTPUT_ROOT.resolve(),
        )
        if not initial_path.is_relative_to(PROJECT_ROOT.resolve()) or not any(initial_path.is_relative_to(root) for root in trusted_model_roots):
            raise ValueError("Fine-tuning checkpoints must come from the local trusted artifacts or YOLO run archives")
        if initial_path.suffix.lower() != ".pt" or not initial_path.is_file():
            raise FileNotFoundError(f"Initial fine-tuning checkpoint not found: {initial_path}")
        config["initial_weights_path"] = str(initial_path)
    if config.get("dataset_profile") in FINETUNE_PROFILES:
        if config["class_names"] != ["knife", "gun"]:
            raise ValueError("Negative fine-tuning requires the existing Knife/Gun class order")
        if not config.get("initial_weights_path"):
            raise ValueError("Negative fine-tuning requires a selected trusted starting checkpoint")
    return config


def _find_native_hyp(yolov9_root: Path) -> Path:
    candidates = [yolov9_root / "hyp.scratch-high.yaml", yolov9_root / "data" / "hyps" / "hyp.scratch-high.yaml"]
    candidates.extend(yolov9_root.rglob("hyp.scratch-high.yaml"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Official hyp.scratch-high.yaml not found under {yolov9_root}")


def _write_hyp(config: dict[str, Any], path: Path) -> dict[str, Any]:
    import yaml

    source = yaml.safe_load(_find_native_hyp(Path(config["yolov9_root"])).read_text(encoding="utf-8"))
    source.update(
        {
            "lr0": config["lr0"],
            "lrf": config["lrf"],
            "momentum": config["momentum"],
            "weight_decay": config["weight_decay"],
            "warmup_epochs": config["warmup_epochs"],
            "warmup_momentum": 0.8,
            "warmup_bias_lr": 0.0,
            "hsv_h": config["hsv_h"],
            "hsv_s": config["hsv_s"],
            "hsv_v": config["hsv_v"],
            "translate": config["translate"],
            "scale": config["scale"],
            "fliplr": config["fliplr"],
            "flipud": config["flipud"],
            "degrees": 0.0,
            "shear": 0.0,
            "perspective": 0.0,
            "mosaic": config["mosaic"],
            "mixup": config["mixup"],
            "copy_paste": 0.0,
        }
    )
    if config.get("dataset_profile") == HARD_NEGATIVE_FINETUNE_PROFILE:
        source["hard_negative_loss_weight"] = float(config.get("hard_negative_loss_weight", 1.0))
        source["hard_negative_loss_gamma"] = float(config.get("hard_negative_loss_gamma", 2.0))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
    return source


def _build_coco_dual_bootstrap(config: dict[str, Any], output_path: Path) -> dict[str, Any]:
    """Transfer COCO-T backbone/features into the configured dual training graph."""
    import torch

    yolo_root = Path(config["yolov9_root"])
    if str(yolo_root) not in sys.path:
        sys.path.insert(0, str(yolo_root))
    from models.yolo import Model

    pretrained = torch.load(config["pretrained"], map_location="cpu", weights_only=False)
    source_model = pretrained.get("ema") or pretrained.get("model")
    if source_model is None:
        raise ValueError("The selected COCO checkpoint has no model/EMA module")
    source_model = source_model.float().eval()
    class_names = tuple(config.get("class_names") or CLASS_NAMES)
    target_model = Model(config["model_config"], ch=3, nc=len(class_names)).float()
    target_state = target_model.state_dict()
    source_state = source_model.state_dict()
    copied: set[str] = set()
    source_to_target: list[tuple[str, str]] = []

    def copy_prefix(source_prefix: str, target_prefix: str) -> int:
        count = 0
        for source_key, value in source_state.items():
            if not source_key.startswith(source_prefix):
                continue
            target_key = target_prefix + source_key[len(source_prefix):]
            if target_key in target_state and target_state[target_key].shape == value.shape:
                target_state[target_key] = value.detach().clone().to(dtype=target_state[target_key].dtype)
                copied.add(target_key)
                source_to_target.append((source_key, target_key))
                count += 1
        return count

    for layer in range(22):
        copy_prefix(f"model.{layer}.", f"model.{layer}.")
    # The auxiliary path repeats three feature-fusion blocks from the primary path.
    for source_layer, target_layer in ((9, 22), (12, 25), (15, 28)):
        if copy_prefix(f"model.{source_layer}.", f"model.{target_layer}.") == 0:
            raise RuntimeError(f"No compatible COCO feature tensors mapped from layer {source_layer} to {target_layer}")
    # The converted graph's DDetect regression towers initialize both branches.
    # Classifier output/hidden shapes differ for the target nc and remain freshly initialized.
    for source_head, target_head in (
        ("model.22.cv2.", "model.29.cv2."),
        ("model.22.cv2.", "model.29.cv4."),
        ("model.22.dfl.", "model.29.dfl."),
        ("model.22.dfl.", "model.29.dfl2."),
    ):
        copy_prefix(source_head, target_head)

    # Concats and nearest-neighbour upsample blocks have no state tensors.
    parameterless_layers = {10, 11, 13, 14, 17, 20}
    required_layers = [layer for layer in range(22) if layer not in parameterless_layers] + [22, 25, 28]
    missing_layers = [layer for layer in required_layers if not any(key.startswith(f"model.{layer}.") for key in copied)]
    for branch_prefix in ("model.29.cv2.", "model.29.cv4."):
        if not any(key.startswith(branch_prefix) for key in copied):
            missing_layers.append(branch_prefix)
    if missing_layers:
        raise RuntimeError(f"COCO checkpoint transfer did not initialize expected backbone/regression layers: {missing_layers}")

    target_model.load_state_dict(target_state, strict=True)
    target_model.names = {index: name for index, name in enumerate(class_names)}
    target_model.nc = len(class_names)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model": target_model,
            "ema": copy.deepcopy(target_model).eval(),
            "optimizer": None,
            "epoch": -1,
            "best_fitness": 0.0,
            "updates": 0,
            "opt": {},
            "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
        output_path,
    )
    layer_report = {}
    for layer in range(22):
        layer_report[str(layer)] = sum(key.startswith(f"model.{layer}.") for key in copied)
    transfer = {
        "checkpoint": config["pretrained"],
        "checkpoint_sha256": _sha256(Path(config["pretrained"])),
        "source_architecture": type(source_model.model[-1]).__name__,
        "source_nc": int(getattr(source_model.model[-1], "nc", 0)),
        "target_architecture": type(target_model.model[-1]).__name__,
        "target_nc": len(class_names),
        "target_class_names": list(class_names),
        "source_tensor_count": len(source_state),
        "target_tensor_count": len(target_state),
        "copied_target_tensors": len(copied),
        "copied_target_parameters": sum(target_state[key].numel() for key in copied),
        "target_parameters_total": sum(value.numel() for value in target_state.values()),
        "copied_backbone_layer_tensor_counts": layer_report,
        "auxiliary_feature_layers": {"9": 22, "12": 25, "15": 28},
        "regression_towers": {"source": "model.22.cv2", "primary": "model.29.cv2", "auxiliary": "model.29.cv4"},
        "classification_towers": f"reinitialized for {len(class_names)} configured weapon classes",
        "all_copied_tensors_finite": all(bool(torch.isfinite(target_state[key]).all()) for key in copied),
        "bootstrap_checkpoint": str(output_path.resolve()),
        "yolov9_source": _upstream_source_info(yolo_root),
    }
    if not transfer["all_copied_tensors_finite"]:
        raise RuntimeError("COCO checkpoint transfer produced non-finite tensors")
    return transfer


def _batch_size_candidates(requested: int, total_memory_bytes: int | None, free_memory_bytes: int | None) -> list[int]:
    """Try the requested batch first; constrain only when current free VRAM is low."""
    candidate = max(1, int(requested))
    if free_memory_bytes is not None:
        if free_memory_bytes < 4 * 1024**3:
            candidate = min(candidate, 2)
        elif free_memory_bytes < 6 * 1024**3:
            candidate = min(candidate, 4)
    candidates = [candidate]
    while candidate > 1:
        candidate = max(1, candidate // 2)
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _is_cuda_oom_text(text: str) -> bool:
    lowered = str(text).casefold()
    return any(token in lowered for token in (
        "out of memory",
        "cuda_error_out_of_memory",
        "cudnn_status_alloc_failed",
        "cuda error: memory allocation",
    ))


def _is_dataloader_memory_error(text: str) -> bool:
    lowered = str(text).casefold()
    memory_error = any(token in lowered for token in (
        "insufficient memory",
        "failed to allocate",
        "not enough memory",
        "out of shared memory",
        "cannot allocate memory",
    ))
    loader_error = any(token in lowered for token in (
        "dataloader worker process",
        "dataloader worker",
        "opencv(",
        "cv2.error",
    ))
    return memory_error and loader_error


def _next_oom_retry_batch(batch_size: int, attempt_log: str, control_command: str, retries: int) -> int | None:
    if (
        not _is_cuda_oom_text(attempt_log)
        or int(batch_size) <= 1
        or control_command in {"pause", "stop"}
        or int(retries) >= 6
    ):
        return None
    return max(1, int(batch_size) // 2)


def _next_dataloader_worker_retry(
    workers: int,
    attempt_log: str,
    control_command: str,
    retries: int,
) -> int | None:
    if (
        not _is_dataloader_memory_error(attempt_log)
        or int(workers) <= 0
        or control_command in {"pause", "stop"}
        or int(retries) >= 4
    ):
        return None
    return max(0, int(workers) // 2)


def _checkpoint_for_batch_retry(
    source: Path,
    destination: Path,
    batch_size: int,
    *,
    workers: int | None = None,
    epochs: int | None = None,
    patience: int | None = None,
) -> Path:
    """Copy a full checkpoint while applying safe loader and epoch options."""
    import torch

    checkpoint = torch.load(source, map_location="cpu", weights_only=False)
    options = checkpoint.get("opt")
    updates = {"batch_size": int(batch_size)}
    if workers is not None:
        updates["workers"] = int(workers)
    if epochs is not None:
        updates["epochs"] = int(epochs)
    if patience is not None:
        updates["patience"] = int(patience)
    if isinstance(options, dict):
        options.update(updates)
    elif options is not None and hasattr(options, "batch_size"):
        for key, value in updates.items():
            setattr(options, key, value)
    else:
        raise RuntimeError("Cannot safely adjust loader settings in the saved YOLO resume checkpoint")
    destination.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, destination)
    return destination


def _probe_batch_time(
    config: dict[str, Any],
    report: dict[str, Any],
    hyp: dict[str, Any],
    data_yaml: Path,
    *,
    allow_batch_fallback: bool = True,
) -> dict[str, Any]:
    """Short, disposable forward/backward pass to choose batch size and epoch budget."""
    import torch

    yolo_root = Path(config["yolov9_root"])
    if str(yolo_root) not in sys.path:
        sys.path.insert(0, str(yolo_root))
    from models.yolo import Model
    from utils.dataloaders import create_dataloader
    from utils.loss_tal_dual import ComputeLoss

    if not torch.cuda.is_available() and config["device"] != "cpu":
        raise RuntimeError("CUDA is unavailable. Change the device to CPU explicitly to continue.")
    device = torch.device("cpu" if config["device"] == "cpu" else f"cuda:{int(config['device'])}")
    if device.type == "cuda":
        torch.cuda.empty_cache()
        total_memory = int(torch.cuda.get_device_properties(device).total_memory)
        free_memory = int(torch.cuda.mem_get_info(device)[0])
    else:
        total_memory = free_memory = None
    batch_candidates = (
        _batch_size_candidates(int(config["batch_size"]), total_memory, free_memory)
        if allow_batch_fallback else [int(config["batch_size"])]
    )
    errors = []
    for batch_size in batch_candidates:
        loader = None
        model = None
        optimizer = None
        validation_loader = None
        try:
            model = Model(config["model_config"], ch=3, nc=len(config.get("class_names") or CLASS_NAMES)).to(device).train()
            probe_weights = config.get("probe_weights_path")
            if probe_weights:
                checkpoint = torch.load(probe_weights, map_location="cpu", weights_only=False)
                source_model = checkpoint.get("ema") or checkpoint.get("model")
                if source_model is None:
                    raise ValueError("Selected fine-tuning checkpoint does not contain a YOLO model")
                source_state = source_model.float().state_dict()
                target_state = model.state_dict()
                compatible = {
                    key: value for key, value in source_state.items()
                    if key in target_state and tuple(value.shape) == tuple(target_state[key].shape)
                }
                if len(compatible) < math.ceil(len(target_state) * 0.95):
                    raise ValueError(
                        "Selected fine-tuning checkpoint is not sufficiently compatible with the local YOLOv9-T graph "
                        f"({len(compatible)}/{len(target_state)} tensors match)"
                    )
                model.load_state_dict(compatible, strict=False)
                del checkpoint, source_model, source_state, target_state, compatible
            model.hyp = dict(hyp)
            criterion = ComputeLoss(model)
            optimizer_name = str(config.get("optimizer", "AdamW"))
            if optimizer_name == "SGD":
                optimizer = torch.optim.SGD(model.parameters(), lr=float(config["lr0"]), momentum=float(config["momentum"]))
            elif optimizer_name == "Adam":
                optimizer = torch.optim.Adam(model.parameters(), lr=float(config["lr0"]))
            else:
                optimizer = torch.optim.AdamW(model.parameters(), lr=float(config["lr0"]))
            scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
            loader, dataset = create_dataloader(
                str(Path(report["cache_root"]) / "images" / "train"),
                int(config["imgsz"]),
                batch_size,
                max(int(model.stride.max()), 32),
                False,
                hyp=hyp,
                augment=True,
                cache=None,
                rect=False,
                rank=-1,
                workers=int(config["workers"]),
                image_weights=False,
                close_mosaic=True,
                quad=False,
                prefix="YOLO workspace speed probe: ",
                shuffle=True,
                min_items=0,
            )
            durations = []
            optimizer.zero_grad(set_to_none=True)
            if device.type == "cuda":
                torch.cuda.reset_peak_memory_stats(device)
            for index, (images, targets, _paths, _shapes) in enumerate(loader):
                if index >= 4:
                    break
                if device.type == "cuda":
                    images = images.to(device, non_blocking=True)
                    targets = targets.to(device)
                else:
                    images = images.to(device)
                    targets = targets.to(device)
                started = time.perf_counter()
                images = images.float() / 255.0
                if device.type == "cuda":
                    with torch.autocast(device_type="cuda", dtype=torch.float16):
                        predictions = model(images)
                        loss, _items = criterion(predictions, targets)
                else:
                    predictions = model(images)
                    loss, _items = criterion(predictions, targets)
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad(set_to_none=True)
                if device.type == "cuda":
                    torch.cuda.synchronize(device)
                duration = time.perf_counter() - started
                durations.append(duration)
            # The upstream trainer validates with twice the training batch.
            # Exercise that forward-only peak before allowing the run to start.
            validation_loader = create_dataloader(
                str(Path(report["cache_root"]) / "images" / "val"),
                int(config["imgsz"]),
                max(1, batch_size * 2),
                max(int(model.stride.max()), 32),
                False,
                hyp=hyp,
                augment=False,
                cache=None,
                rect=True,
                rank=-1,
                workers=int(config["workers"]),
                image_weights=False,
                close_mosaic=True,
                quad=False,
                prefix="YOLO workspace validation probe: ",
                shuffle=False,
                min_items=0,
            )[0]
            model.eval()
            with torch.inference_mode():
                val_images, _val_targets, _val_paths, _val_shapes = next(iter(validation_loader))
                val_images = val_images.to(device, non_blocking=device.type == "cuda").float() / 255.0
                if device.type == "cuda":
                    with torch.autocast(device_type="cuda", dtype=torch.float16):
                        model(val_images)
                    torch.cuda.synchronize(device)
                else:
                    model(val_images)
            if len(durations) < 2:
                raise RuntimeError("Not enough training batches to estimate a full epoch")
            stable = durations[1:]
            batch_time = statistics.median(stable)
            val_images = int(report["images"]["val"])
            train_images = int(report["images"]["train"])
            train_batches = math.ceil(train_images / batch_size)
            val_batches = math.ceil(val_images / max(1, batch_size * 2))
            # Validation runs one forward pass; use a conservative fraction of
            # measured forward+backward time until the first real validation.
            epoch_seconds = batch_time * train_batches + batch_time * val_batches * 0.48 + 8.0
            return {
                "batch_size": batch_size,
                "probe_batches": len(durations),
                "probe_seconds_per_batch": batch_time,
                "probe_batch_durations": durations,
                "probe_optimizer_step": True,
                "probe_validation_forward": True,
                "train_batches_per_epoch": train_batches,
                "validation_batches_per_epoch": val_batches,
                "estimated_epoch_seconds": epoch_seconds,
                "estimated_validation_batch_seconds": batch_time * 0.48,
                "gpu_memory_gb": round(torch.cuda.max_memory_reserved(device) / (1024**3), 2) if device.type == "cuda" else None,
                "requested_batch_size": int(config["batch_size"]),
                "batch_size_cap_reason": (
                    "free VRAM safety cap" if free_memory is not None and batch_size < int(config["batch_size"])
                    else None
                ),
                "validation_probe_batch_size": min(int(report["images"]["val"]), batch_size * 2),
                "total_gpu_memory_gb": round(total_memory / (1024**3), 2) if total_memory else None,
                "free_gpu_memory_gb": round(free_memory / (1024**3), 2) if free_memory is not None else None,
            }
        except torch.cuda.OutOfMemoryError as exc:
            errors.append(f"batch {batch_size}: CUDA out of memory")
            if device.type == "cuda":
                torch.cuda.empty_cache()
        except Exception as exc:
            errors.append(f"batch {batch_size}: {exc}")
            if not _is_cuda_oom_text(str(exc)):
                raise
            if device.type == "cuda":
                torch.cuda.empty_cache()
        finally:
            loader = None
            validation_loader = None
            optimizer = None
            model = None
            gc.collect()
            if device.type == "cuda":
                torch.cuda.empty_cache()
    raise RuntimeError("GPU training/validation probe could not fit any safe batch size: " + "; ".join(errors))


def _runtime_files(run_dir: Path) -> tuple[Path, Path]:
    runtime_dir = run_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    bootstrap_file = runtime_dir / "sitecustomize.py"
    hooks_path = Path(__file__).resolve().with_name("yolov9_weapon_hooks.py")
    if not hooks_path.is_file():
        raise FileNotFoundError(
            f"Training progress hook module is missing: {hooks_path}. "
            "Deploy yolov9_weapon_hooks.py beside yolov9_weapon_pipeline.py before starting a run."
        )
    # sitecustomize is isolated to the training subprocess, so the upstream
    # checkout remains read-only and its version can be recorded reliably.
    bootstrap_file.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(hooks_path.parent)!r})\n"
        "from yolov9_weapon_hooks import install\n"
        "install()\n",
        encoding="utf-8",
    )
    return runtime_dir, hooks_path


def _ensure_yolov9_font_assets() -> dict[str, Any]:
    """Seed YOLOv9's expected font files from a local system font, without network access."""
    configured_dir = os.environ.get("YOLOV5_CONFIG_DIR")
    if configured_dir:
        config_dir = Path(configured_dir).expanduser()
    else:
        config_subdirs = {
            "Windows": "AppData/Roaming",
            "Linux": ".config",
            "Darwin": "Library/Application Support",
        }
        base_dir = Path.home() / config_subdirs.get(platform.system(), "")
        config_dir = (base_dir if os.access(base_dir, os.W_OK) else Path("/tmp")) / "Ultralytics"
    config_dir.mkdir(parents=True, exist_ok=True)

    source: Path | None = None
    try:
        from matplotlib import font_manager

        candidate = Path(font_manager.findfont("DejaVu Sans", fallback_to_default=False))
        if candidate.is_file() and candidate.stat().st_size >= 10_000:
            source = candidate
    except Exception:
        pass
    if source is None:
        candidates = (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/segoeui.ttf"),
        )
        source = next((item for item in candidates if item.is_file() and item.stat().st_size >= 10_000), None)
    if source is None:
        raise RuntimeError(
            "YOLOv9 requires a local TrueType font, but no usable system font was found. "
            "Install a system font package (for example fonts-dejavu-core) and retry; "
            "the training process will not download fonts from the internet."
        )

    ready_files = []
    for filename in ("Arial.ttf", "Arial.Unicode.ttf"):
        destination = config_dir / filename
        if not destination.is_file() or destination.stat().st_size < 10_000:
            temporary = destination.with_name(destination.name + f".{os.getpid()}.tmp")
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
        ready_files.append({"name": filename, "size_bytes": destination.stat().st_size})
    return {"font_family": "DejaVu Sans fallback", "ready_files": ready_files, "network_download_required": False}


def _validate_child_runtime(
    cwd: Path,
    environment: dict[str, str],
    state_path: Path,
    events_path: Path,
) -> None:
    """Fail before training if Python cannot load the progress hooks cleanly."""
    if environment.get("YOLOV9_RUNTIME_VALIDATED") == "1":
        return
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sitecustomize; import yolov9_weapon_hooks; "
            "from PIL import ImageFont; ImageFont.load_default().getsize('runtime preflight')",
        ],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    state = _read_json(state_path)
    startup_error = "Error in sitecustomize" in (result.stderr or "")
    hook_error = state.get("hook_runtime_error")
    pillow_error = state.get("pillow_compatibility_error")
    if (
        result.returncode != 0
        or startup_error
        or not state.get("hook_runtime_ready")
        or hook_error
        or not state.get("pillow_font_api_ready")
        or pillow_error
    ):
        detail = (result.stderr or result.stdout or str(hook_error or pillow_error or "runtime compatibility checks did not pass"))[-3000:]
        raise RuntimeError(f"训练进度运行时预检失败，尚未启动训练子进程：{detail}")
    environment["YOLOV9_RUNTIME_VALIDATED"] = "1"
    _update_state(
        state_path,
        {
            "runtime_preflight": {
                "ok": True,
                "hook_version": state.get("hook_version"),
                "font_assets_ready": True,
                "pillow_version": state.get("pillow_version"),
                "pillow_compatibility_aliases": state.get("pillow_compatibility_aliases", []),
            }
        },
        "runtime_preflight_passed",
        events_path,
    )


def _child_environment(
    run_dir: Path,
    config_path: Path,
    state_path: Path,
    events_path: Path,
    control_path: Path,
    *,
    evaluation_split: str = "val",
    resume_epoch: int = 0,
    capture_validation: bool = False,
    val_predictions: Path | None = None,
    resume_state: Path | None = None,
    validation_batch: int | None = None,
) -> dict[str, str]:
    runtime_dir, hooks_path = _runtime_files(run_dir)
    environment = os.environ.copy()
    existing_python_path = environment.get("PYTHONPATH", "")
    # The configuration JSON is stored below run_dir; obtain the roots from it.
    cfg = _read_json(config_path)
    paths = [str(runtime_dir), str(hooks_path.parent), str(Path(cfg["yolov9_root"]).resolve())]
    if existing_python_path:
        paths.append(existing_python_path)
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    environment["PYTHONUNBUFFERED"] = "1"
    # Brev dependencies are installed before a run. Avoid network update checks
    # and implicit package installs inside the training subprocess.
    environment["NO_ALBUMENTATIONS_UPDATE"] = "1"
    environment["YOLOV5_AUTOINSTALL"] = "0"
    environment["YOLOV9_WORKSPACE_STATE"] = str(state_path.resolve())
    environment["YOLOV9_WORKSPACE_EVENTS"] = str(events_path.resolve())
    environment["YOLOV9_WORKSPACE_CONTROL"] = str(control_path.resolve())
    environment["YOLOV9_WORKSPACE_CONFIG"] = str(config_path.resolve())
    environment["YOLOV9_EVALUATION_SPLIT"] = evaluation_split
    environment["YOLOV9_RESUME_EPOCH"] = str(resume_epoch)
    environment["YOLOV9_VALIDATION_BATCH"] = str(validation_batch or cfg.get("batch_size", 16))
    if capture_validation:
        environment["YOLOV9_CAPTURE_VALIDATION"] = "1"
    else:
        environment.pop("YOLOV9_CAPTURE_VALIDATION", None)
    if val_predictions:
        environment["YOLOV9_VALIDATION_PREDICTIONS"] = str(val_predictions.resolve())
    if resume_state:
        environment["YOLOV9_RESUME_STATE"] = str(resume_state.resolve())
    return environment


def _write_hook_config(path: Path, payload: dict[str, Any]) -> None:
    current = _read_json(path)
    current.update(payload)
    atomic_write_json(path, current)


def _run_child(
    command: list[str],
    cwd: Path,
    environment: dict[str, str],
    log_path: Path,
    state_path: Path,
    events_path: Path,
    *,
    budget_deadline: float | None = None,
    reserve_seconds: int = 600,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _validate_child_runtime(cwd, environment, state_path, events_path)
    with log_path.open("a", encoding="utf-8", errors="replace") as log_stream:
        log_stream.write("\n$ " + subprocess.list2cmdline(command) + "\n")
        log_stream.flush()
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        def copy_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                log_stream.write(line)
                log_stream.flush()

        reader = threading.Thread(target=copy_output, name="yolov9-child-log", daemon=True)
        reader.start()
        budget_command_sent = False
        while process.poll() is None:
            time.sleep(0.5)
            if budget_deadline and not budget_command_sent:
                status = _read_json(state_path)
                metrics = status.get("last_epoch_metrics") or {}
                # Initial data-loader/CUDA startup can make the first batch's
                # extrapolation enormous. Budget decisions use complete epochs
                # or the warmed-up probe rather than that transient estimate.
                probe = status.get("probe") or {}
                epoch_seconds = float(metrics.get("epoch_seconds") or probe.get("estimated_epoch_seconds") or 0)
                remaining = budget_deadline - time.time()
                if epoch_seconds > 0 and remaining < epoch_seconds + reserve_seconds:
                    control_path = Path(environment["YOLOV9_WORKSPACE_CONTROL"])
                    atomic_write_json(control_path, {"command": "budget_finalize", "requested_at": time.time()})
                    _update_state(state_path, {"status": "running", "stage_label": "时间预算接近上限，等待当前完整 epoch 保存后评估", "budget_finalization_requested": True}, "budget_finalize_requested", events_path)
                    budget_command_sent = True
        reader.join(timeout=3)
        return int(process.returncode or 0)


def _write_preview_html(run_dir: Path, report: dict[str, Any]) -> None:
    payload_path = run_dir / "dataset_preview.json"
    payload_path.write_text(json.dumps(report.get("preview_samples", []), ensure_ascii=False), encoding="utf-8")


def audit_command(
    output_root: Path | None = None,
    state_path: Path | None = None,
    events_path: Path | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = output_root or OUTPUT_ROOT
    state = state_path or root / "audit_status.json"
    events = events_path or root / "audit_events.jsonl"
    _update_state(state, {"status": "running", "stage": "dataset_audit", "stage_label": "正在审计数据集", "started_at": time.time(), "error": None, "traceback": None}, "audit_started", events)
    try:
        audit_config = config or {}
        report = _build_dataset_for_config(
            audit_config,
            output_root=root,
            progress=lambda payload: _update_state(state, payload, payload.get("stage"), events),
        )
        _write_preview_html(root, report)
        _update_state(state, {"status": "completed", "stage": "completed", "stage_label": "数据审计和缓存已完成", "dataset": report, "error": None, "traceback": None, "completed_at": time.time()}, "audit_completed", events)
        return report
    except Exception as exc:
        _update_state(state, {"status": "failed", "stage": "failed", "error": str(exc), "traceback": traceback.format_exc(), "completed_at": time.time()}, "audit_failed", events)
        raise


def _build_dataset_for_config(
    config: dict[str, Any],
    *,
    output_root: Path,
    progress,
) -> dict[str, Any]:
    """Dispatch the selectable, fixed V1, or two-class fine-tuning recipe."""
    if config.get("dataset_profile") == MALL_FINETUNE_PROFILE:
        from yolov9_mall_finetune import build_finetune_dataset

        return build_finetune_dataset(
            output_root=output_root,
            base_output_root=PROJECT_ROOT / "model_runs" / "yolov9_full_dataset_v1",
            progress=progress,
        )
    if config.get("dataset_profile") == EXPANDED_NEGATIVE_FINETUNE_PROFILE:
        from yolov9_expanded_negative_finetune import build_expanded_negative_dataset

        return build_expanded_negative_dataset(output_root=output_root, progress=progress)
    if config.get("dataset_profile") == HARD_NEGATIVE_FINETUNE_PROFILE:
        from yolov9_expanded_negative_finetune import build_hard_negative_dataset

        return build_hard_negative_dataset(output_root=output_root, progress=progress)
    if config.get("dataset_profile") == SELECTED_SOURCES_FINETUNE_PROFILE:
        from yolov9_selected_sources_finetune import build_selected_source_dataset

        return build_selected_source_dataset(output_root=output_root, progress=progress)
    if config.get("dataset_profile") == "full_dataset_v1":
        from yolov9_full_dataset_v1 import build_dataset

        return build_dataset(output_root=output_root, progress=progress)
    return audit_and_build_dataset(
        output_root=output_root,
        progress=progress,
        include_sources=config.get("dataset_sources"),
        negative_only_sources=config.get("negative_only_sources"),
        positive_only_sources=config.get("positive_only_sources"),
        train_only_sources=config.get("train_only_sources"),
        class_names=config.get("class_names"),
    )


def _complete_hard_negative_fpr_tracking(
    config: dict[str, Any],
    report: dict[str, Any],
    run_dir: Path,
    state_path: Path,
    events_path: Path,
    control_path: Path,
    config_path: Path,
    yolo_root: Path,
) -> dict[str, Any]:
    """Compare BREV initialization and every epoch at one fixed confidence threshold."""
    threshold = float(config.get("fpr_confidence_threshold", 0.25))
    epoch_rows_path = run_dir / "epoch_metrics.csv"
    with epoch_rows_path.open("r", encoding="utf-8-sig", newline="") as stream:
        epoch_rows = {int(row["epoch"]): row for row in csv.DictReader(stream)}
    expected_epochs = int(config.get("epochs", config.get("epochs_cap", 5)))
    if set(epoch_rows) != set(range(1, expected_epochs + 1)):
        raise RuntimeError(f"Expected metrics for epochs 1-{expected_epochs}; found {sorted(epoch_rows)}")

    output_path = run_dir / "epoch_false_positive_metrics.json"
    existing = _read_json(output_path)
    models: list[dict[str, Any]] = existing.get("models", []) if existing.get("dataset_fingerprint") == report.get("fingerprint") else []
    by_id = {str(row.get("model_id")): row for row in models if isinstance(row, dict)}
    initial_path = Path(config["initial_weights_path"]).resolve()
    jobs = [("brev_best", 0, "BREV Best baseline", initial_path)]
    for epoch in range(1, expected_epochs + 1):
        checkpoint = run_dir / "checkpoints" / "epoch_models" / f"epoch_{epoch:02d}.pt"
        if not checkpoint.is_file():
            raise FileNotFoundError(f"Saved checkpoint for epoch {epoch} is missing: {checkpoint}")
        jobs.append((f"epoch_{epoch}", epoch, f"Epoch {epoch}", checkpoint))

    class_names = tuple(report.get("class_names") or config.get("class_names") or ("knife", "gun"))
    _write_hook_config(config_path, {
        "track_negative_fpr": True,
        "fpr_manifest_path": str(Path(report["manifest"]).resolve()),
        "fpr_confidence_threshold": threshold,
        "class_names": list(class_names),
    })
    # Measure both splits directly for every checkpoint. Relying on training-hook
    # rows for per-epoch validation FPR made evaluation fail when those optional
    # hook fields were absent from an otherwise complete epoch metrics row.
    total_jobs = len(jobs) * 2
    completed_jobs = 0

    def persist() -> None:
        payload = {
            "schema_version": 1,
            "metric": "negative_image_false_positive_rate",
            "definition": "Fraction of explicitly negative images with at least one post-NMS knife/gun detection at or above the fixed confidence threshold.",
            "confidence_threshold": threshold,
            "threshold_policy": "Fixed at 0.25 for BREV baseline and all epochs; not re-calibrated per model.",
            "dataset_fingerprint": report["fingerprint"],
            "dataset_manifest": str(Path(report["manifest"]).resolve()),
            "dataset_negative_images": {
                split: int(report.get("negative_source_split_counts", {}).get(split, 0))
                for split in ("val", "test")
            },
            "model_selection": "Unchanged: YOLO validation fitness selects the best checkpoint. FPR is a monitoring metric only.",
            "models": sorted(by_id.values(), key=lambda item: int(item.get("epoch", 0))),
            "updated_at": time.time(),
        }
        atomic_write_json(output_path, payload)
        _update_state(state_path, {"epoch_false_positive_metrics": payload, "epoch_fpr_progress": {"completed": completed_jobs, "total": total_jobs}}, "epoch_fpr_metrics_updated", events_path)

    for model_id, epoch, label, checkpoint in jobs:
        cached = by_id.get(model_id, {})
        splits_needed = ["val", "test"]
        row = {"model_id": model_id, "epoch": epoch, "label": label, "checkpoint": str(checkpoint), "fitness": None if epoch == 0 else float(epoch_rows[epoch]["fitness"]), **cached}
        row.update({"model_id": model_id, "epoch": epoch, "label": label, "checkpoint": str(checkpoint), "fitness": None if epoch == 0 else float(epoch_rows[epoch]["fitness"])})
        for split in splits_needed:
            if isinstance(row.get(split), dict) and row[split].get("dataset_fingerprint") == report.get("fingerprint"):
                completed_jobs += 1
                continue
            _update_state(state_path, {
                "stage": "epoch_fpr",
                "stage_label": f"固定阈值误报率对照：{label} · {split}",
                "epoch_fpr_progress": {"completed": completed_jobs, "total": total_jobs, "model": label, "split": split},
                "evaluation_split": split,
                "evaluation_images_total": int(report["images"][split]),
                "evaluation_images_done": 0,
            }, "epoch_fpr_evaluation_started", events_path)
            eval_state = _evaluation_command(
                config,
                report,
                run_dir,
                checkpoint,
                state_path,
                events_path,
                control_path,
                config_path,
                split=split,
                capture=False,
                run_label=f"fpr_{model_id}_{split}",
            )
            record = eval_state.get("negative_fpr_record") or {}
            if record.get("split") != split or record.get("negative_images_evaluated") != record.get("negative_images") or record.get("negative_images", 0) <= 0:
                raise RuntimeError(f"Incomplete {split} negative-FPR measurement for {label}: {record}")
            record["dataset_fingerprint"] = report["fingerprint"]
            row[split] = record
            completed_jobs += 1
            by_id[model_id] = row
            persist()

    return _read_json(output_path)


def _write_inference_checkpoint(best_path: Path, target_path: Path, yolo_root: Path) -> None:
    if str(yolo_root) not in sys.path:
        sys.path.insert(0, str(yolo_root))
    import torch

    # These are locally generated, trusted YOLO model-object checkpoints.
    # The upstream exporter relies on torch.load's old weights_only default.
    checkpoint = torch.load(best_path, map_location="cpu", weights_only=False)
    if checkpoint.get("ema") is not None:
        checkpoint["model"] = checkpoint["ema"]
    for key in ("optimizer", "best_fitness", "ema", "updates"):
        checkpoint[key] = None
    checkpoint["epoch"] = -1
    checkpoint["model"].half()
    for parameter in checkpoint["model"].parameters():
        parameter.requires_grad = False
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_path.with_name(target_path.name + f".{os.getpid()}.tmp")
    torch.save(checkpoint, temporary)
    os.replace(temporary, target_path)


def _calibrate_thresholds(
    predictions_path: Path,
    manifest_path: Path,
    output_path: Path,
    state_path: Path,
    events_path: Path,
    *,
    dataset_fingerprint: str,
    checkpoint_sha256: str,
    class_names: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, Any]]:
    validation_data = _prepare_threshold_split(predictions_path, manifest_path, "val", class_names)
    analysis = _build_sensitivity_analysis(
        validation_data,
        class_names=class_names,
        dataset_fingerprint=dataset_fingerprint,
        checkpoint_sha256=checkpoint_sha256,
    )
    moderate_profile = next(profile for profile in analysis["profiles"] if profile["profile"] == "Moderate")
    threshold_report = {
        "mode": "validation_f1_iou50_exact_confidence",
        "iou_threshold": 0.5,
        "prediction_floor": 0.001,
        "class_names": list(class_names),
        "threshold_selection": "Per-class maximum F1 on validation; exact saved confidence values.",
        "thresholds": moderate_profile["thresholds"],
        "per_class": moderate_profile["validation_micro"]["classes"],
        "negative_false_positives_by_source": moderate_profile["validation_negative_false_positives_by_source"],
        "source_split": "val",
    }
    atomic_write_json(output_path, threshold_report)
    sensitivity_path = output_path.with_name("sensitivity_thresholds.json")
    atomic_write_json(sensitivity_path, analysis)
    _update_state(
        state_path,
        {
            "validation_thresholds": threshold_report["thresholds"],
            "validation_threshold_report": str(output_path.resolve()),
            "sensitivity_threshold_analysis": analysis,
            "sensitivity_thresholds_report": str(sensitivity_path.resolve()),
            "stage_label": f"验证集已为 {len(class_names)} 个类别选出 {len(class_names) * len(_SENSITIVITY_PROFILES)} 个阈值，正在准备测试集评估",
        },
        "sensitivity_thresholds_selected",
        events_path,
    )
    return threshold_report, analysis


_SENSITIVITY_PROFILES = (
    ("Sensitive", "F2", 2.0),
    ("Moderate", "F1", 1.0),
    ("Certain", "F0.5", 0.5),
)


def _box_iou_xyxy(a: list[float], b: list[float]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0


def _prepare_threshold_split(
    predictions_path: Path,
    manifest_path: Path,
    split: str,
    class_names: tuple[str, ...],
) -> dict[str, Any]:
    if not predictions_path.is_file():
        raise FileNotFoundError(f"{split} predictions are missing: {predictions_path}")
    source_by_image: dict[str, str] = {}
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["split"] == split:
                for candidate in (row["image"], row["source_image"]):
                    source_by_image[str(Path(candidate).resolve())] = row["source"]

    class_records: dict[int, list[tuple[float, bool]]] = {class_id: [] for class_id in range(len(class_names))}
    ground_truth_counts = {class_id: 0 for class_id in range(len(class_names))}
    images: list[dict[str, Any]] = []
    with predictions_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            item = json.loads(line)
            image_path = str(Path(item["image"]).resolve())
            targets: dict[int, list[list[float]]] = {class_id: [] for class_id in range(len(class_names))}
            detections: dict[int, list[tuple[list[float], float]]] = {class_id: [] for class_id in range(len(class_names))}
            for target in item.get("targets", []):
                class_id = int(target[0])
                if class_id in targets:
                    targets[class_id].append([float(value) for value in target[1:5]])
                    ground_truth_counts[class_id] += 1
            for prediction in item.get("predictions", []):
                class_id = int(prediction[5])
                if class_id in detections:
                    detections[class_id].append(([float(value) for value in prediction[:4]], float(prediction[4])))

            for class_id in range(len(class_names)):
                unmatched = set(range(len(targets[class_id])))
                ordered = sorted(detections[class_id], key=lambda detection: detection[1], reverse=True)
                for box, confidence in ordered:
                    options = sorted(
                        ((_box_iou_xyxy(box, targets[class_id][target_index]), target_index) for target_index in unmatched),
                        reverse=True,
                    )
                    is_true_positive = bool(options and options[0][0] >= 0.5)
                    if is_true_positive:
                        unmatched.remove(options[0][1])
                    class_records[class_id].append((confidence, is_true_positive))
            images.append({
                "image": image_path,
                "source": source_by_image.get(image_path, "unknown"),
                "has_targets": any(targets.values()),
                "detections": detections,
            })
    return {
        "split": split,
        "images": images,
        "class_records": class_records,
        "ground_truth_counts": ground_truth_counts,
    }


def _counts_report(true_positives: int, false_positives: int, ground_truth_count: int, threshold: float) -> dict[str, Any]:
    false_negatives = ground_truth_count - true_positives
    predicted_count = true_positives + false_positives
    precision = true_positives / predicted_count if predicted_count else 0.0
    recall = true_positives / ground_truth_count if ground_truth_count else 0.0

    def f_score(beta: float) -> float:
        beta_squared = beta * beta
        denominator = beta_squared * precision + recall
        return (1 + beta_squared) * precision * recall / denominator if denominator else 0.0

    return {
        "threshold": float(threshold),
        "ground_truth_boxes": int(ground_truth_count),
        "predicted_boxes": int(predicted_count),
        "tp": int(true_positives),
        "fp": int(false_positives),
        "fn": int(false_negatives),
        "precision": precision,
        "recall": recall,
        "f1": f_score(1.0),
        "f2": f_score(2.0),
        "f0_5": f_score(0.5),
    }


def _threshold_counts(records: list[tuple[float, bool]], ground_truth_count: int, threshold: float) -> dict[str, Any]:
    selected = [is_true_positive for confidence, is_true_positive in records if confidence >= threshold]
    true_positives = sum(selected)
    false_positives = len(selected) - true_positives
    return _counts_report(true_positives, false_positives, ground_truth_count, threshold)


def _choose_threshold(data: dict[str, Any], class_id: int, beta: float, iou_floor: float = 0.001) -> dict[str, Any]:
    records = data["class_records"][class_id]
    grouped_scores: dict[float, list[int]] = {}
    for confidence, is_true_positive in records:
        if confidence >= iou_floor:
            counts = grouped_scores.setdefault(confidence, [0, 0])
            counts[0 if is_true_positive else 1] += 1

    candidates = sorted({float(iou_floor), *grouped_scores.keys()}, reverse=True)
    ordered_groups = sorted(grouped_scores.items(), reverse=True)
    group_index = 0
    tp = fp = 0
    ground_truth_count = data["ground_truth_counts"][class_id]
    scored: list[dict[str, Any]] = []
    for threshold in candidates:
        while group_index < len(ordered_groups) and ordered_groups[group_index][0] >= threshold:
            tp += ordered_groups[group_index][1][0]
            fp += ordered_groups[group_index][1][1]
            group_index += 1
        scored.append(_counts_report(tp, fp, ground_truth_count, threshold))
    if beta == 2.0:
        best = max(scored, key=lambda row: (row["f2"], row["recall"], -row["threshold"]))
    elif beta == 1.0:
        best = max(scored, key=lambda row: (row["f1"], row["precision"], row["threshold"]))
    else:
        best = max(scored, key=lambda row: (row["f0_5"], row["precision"], row["threshold"]))
    return best


def _negative_threshold_metrics(
    data: dict[str, Any], thresholds: dict[str, float], class_names: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, int]] = {}
    for image in data["images"]:
        if image["has_targets"]:
            continue
        row = grouped.setdefault(image["source"], {"images": 0, "images_with_false_positive": 0, "false_positive_boxes": 0})
        row["images"] += 1
        false_positives = sum(
            confidence >= thresholds[class_names[class_id]]
            for class_id, detections in image["detections"].items()
            for _box, confidence in detections
        )
        row["false_positive_boxes"] += int(false_positives)
        row["images_with_false_positive"] += int(false_positives > 0)
    for row in grouped.values():
        row["false_positive_image_rate"] = row["images_with_false_positive"] / row["images"] if row["images"] else 0.0
        row["false_positives_per_image"] = row["false_positive_boxes"] / row["images"] if row["images"] else 0.0
    return grouped


def _profile_split_metrics(
    data: dict[str, Any], thresholds: dict[str, float], class_names: tuple[str, ...]
) -> dict[str, Any]:
    per_class = {
        class_name: _threshold_counts(
            data["class_records"][class_id],
            data["ground_truth_counts"][class_id],
            thresholds[class_name],
        )
        for class_id, class_name in enumerate(class_names)
    }
    total_tp = sum(row["tp"] for row in per_class.values())
    total_fp = sum(row["fp"] for row in per_class.values())
    total_fn = sum(row["fn"] for row in per_class.values())
    total_ground_truth = sum(row["ground_truth_boxes"] for row in per_class.values())
    total_predictions = sum(row["predicted_boxes"] for row in per_class.values())
    precision = total_tp / (total_tp + total_fp) if total_tp + total_fp else 0.0
    recall = total_tp / total_ground_truth if total_ground_truth else 0.0

    def f_score(beta: float) -> float:
        beta_squared = beta * beta
        denominator = beta_squared * precision + recall
        return (1 + beta_squared) * precision * recall / denominator if denominator else 0.0

    return {
        "tp": total_tp,
        "fp": total_fp,
        "fn": total_fn,
        "ground_truth_boxes": total_ground_truth,
        "predicted_boxes": total_predictions,
        "precision": precision,
        "recall": recall,
        "f1": f_score(1.0),
        "f2": f_score(2.0),
        "f0_5": f_score(0.5),
        "classes": per_class,
    }


def _build_sensitivity_analysis(
    validation_data: dict[str, Any],
    test_data: dict[str, Any] | None = None,
    *,
    class_names: tuple[str, ...],
    dataset_fingerprint: str | None = None,
    checkpoint_sha256: str | None = None,
) -> dict[str, Any]:
    profiles = []
    for profile_name, objective, beta in _SENSITIVITY_PROFILES:
        selected = {
            class_name: _choose_threshold(validation_data, class_id, beta)
            for class_id, class_name in enumerate(class_names)
        }
        thresholds = {class_name: selected[class_name]["threshold"] for class_name in class_names}
        validation_metrics = _profile_split_metrics(validation_data, thresholds, class_names)
        profile: dict[str, Any] = {
            "profile": profile_name,
            "objective": objective,
            "beta": beta,
            "thresholds": thresholds,
            "validation_micro": validation_metrics,
            "validation_negative_false_positives_by_source": _negative_threshold_metrics(validation_data, thresholds, class_names),
            "classes": {
                class_name: {
                    "threshold": thresholds[class_name],
                    "objective": objective,
                    "selected_validation_score": selected[class_name][objective.lower().replace(".", "_")],
                    "validation": validation_metrics["classes"][class_name],
                    "tie_break": "higher recall then lower threshold" if beta == 2.0 else "higher precision then higher threshold",
                }
                for class_name in class_names
            },
        }
        if test_data is not None:
            profile["test_micro"] = _profile_split_metrics(test_data, thresholds, class_names)
            profile["test_negative_false_positives_by_source"] = _negative_threshold_metrics(test_data, thresholds, class_names)
        profiles.append(profile)
    return {
        "schema_version": 3,
        "status": "complete" if test_data is not None else "awaiting_test",
        "dataset_fingerprint": dataset_fingerprint,
        "best_checkpoint_sha256": checkpoint_sha256,
        "iou_threshold": 0.5,
        "prediction_floor": 0.001,
        "class_names": list(class_names),
        "method": {
            "profile_objectives": {name: f"maximize validation {objective}" for name, objective, _beta in _SENSITIVITY_PROFILES},
            "matching": "Per-image, class-aware, confidence-descending greedy one-to-one matching at IoU >= 0.50.",
            "threshold_candidates": "Every distinct saved post-NMS validation confidence >= 0.001 plus export floor 0.001.",
            "threshold_selection": "Choose each class threshold independently on validation; apply the locked thresholds to test.",
            "tie_break": "F2 ties: higher recall, then lower threshold. F1/F0.5 ties: higher precision, then higher threshold.",
        },
        "validation_images": len(validation_data["images"]),
        "test_images": len(test_data["images"]) if test_data is not None else None,
        "profiles": profiles,
    }


def _evaluation_command(
    config: dict[str, Any],
    report: dict[str, Any],
    run_dir: Path,
    checkpoint: Path,
    state_path: Path,
    events_path: Path,
    control_path: Path,
    config_path: Path,
    *,
    split: str,
    capture: bool = False,
    run_label: str | None = None,
) -> dict[str, Any]:
    yolo_root = Path(config["yolov9_root"])
    evaluator = yolo_root / "val_dual.py"
    if not evaluator.is_file():
        raise FileNotFoundError(f"YOLOv9 evaluator not found: {evaluator}")
    batch_candidates = [int(config["batch_size"])]
    for alternative in (8, 4, 2, 1):
        if alternative < batch_candidates[-1]:
            batch_candidates.append(alternative)
    prediction_name = "validation_predictions.jsonl" if split == "val" else f"{split}_predictions.jsonl"
    prediction_path = run_dir / prediction_name if capture else None
    for batch_size in batch_candidates:
        _write_hook_config(
            config_path,
            {
                "class_names": list(config.get("class_names") or report.get("class_names") or CLASS_NAMES),
                "evaluation_split": split,
                "evaluation_image_count": int(report["images"][split]),
                "capture_validation_predictions": capture,
                "validation_predictions_path": str(prediction_path) if prediction_path else None,
                "resume_state_path": str(run_dir / "checkpoints" / "resume_state.pt"),
                "batch_size": batch_size,
            },
        )
        cmd = [
            sys.executable,
            "-u",
            str(evaluator.name),
            "--data", str(Path(report["data_yaml"])),
            "--weights", str(checkpoint),
            "--batch-size", str(batch_size),
            "--imgsz", str(config["imgsz"]),
            "--device", str(config["device"]),
            "--task", split,
            "--conf-thres", "0.001",
            "--iou-thres", str(config.get("iou_threshold", 0.7)),
            "--workers", str(int(config["workers"])),
            "--project", str(run_dir / "evaluations"),
            "--name", f"{split}_{run_label or ('calibration' if split == 'val' else 'final')}_b{batch_size}",
            "--exist-ok",
            "--verbose",
        ]
        # Validation rows are captured from the on_val_batch_end hook above.
        # Exporting duplicate per-image TXT files adds no pipeline value and
        # can exceed Windows MAX_PATH for long source image stems.
        environment = _child_environment(
            run_dir,
            config_path,
            state_path,
            events_path,
            control_path,
            evaluation_split=split,
            capture_validation=capture,
            val_predictions=prediction_path,
            validation_batch=batch_size,
        )
        environment["YOLOV9_EVALUATION_SPLIT"] = split
        _update_state(state_path, {"status": "running", "stage": "evaluation", "stage_label": f"正在评估 {split}（batch={batch_size}）", "evaluation_split": split, "evaluation_images_total": int(report["images"][split]), "evaluation_images_done": 0, "evaluation_per_class": {}}, f"{split}_evaluation_started", events_path)
        return_code = _run_child(cmd, yolo_root, environment, run_dir / f"{split}_evaluation.log", state_path, events_path)
        state = _read_json(state_path)
        if return_code == 0:
            if capture:
                if prediction_path is None or not prediction_path.is_file():
                    raise RuntimeError(f"{split} evaluation completed without its per-image predictions")
                with prediction_path.open("r", encoding="utf-8") as stream:
                    captured_images = {json.loads(line)["image"] for line in stream if line.strip()}
                if len(captured_images) != int(report["images"][split]):
                    raise RuntimeError(f"{split} evaluation captured {len(captured_images)} images; expected {report['images'][split]}. See validation_capture_warning in progress.json")
            return state
        error = (run_dir / f"{split}_evaluation.log").read_text(encoding="utf-8", errors="replace")[-5000:]
        if "out of memory" in error.lower() and batch_size > 1:
            if torch_cuda_available():
                import torch

                torch.cuda.empty_cache()
            continue
        raise RuntimeError(f"YOLOv9 {split} evaluation failed (exit={return_code}):\n{error}")
    raise RuntimeError(f"YOLOv9 {split} evaluation ran out of memory at every batch size: {batch_candidates}")


def torch_cuda_available() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:
        return False


def _complete_final_evaluations(
    config: dict[str, Any],
    report: dict[str, Any],
    run_dir: Path,
    state_path: Path,
    events_path: Path,
    control_path: Path,
    config_path: Path,
    yolo_root: Path,
    *,
    started_at: float,
    elapsed_before: float = 0.0,
) -> dict[str, Any]:
    best_path = run_dir / "official_train" / "train" / "weights" / "best.pt"
    if not best_path.is_file():
        raise FileNotFoundError(f"Best training checkpoint not found: {best_path}")
    evaluation_manifest_path = run_dir / "evaluation_manifest.json"
    evaluation_manifest = _read_json(evaluation_manifest_path)
    completed_evaluations = evaluation_manifest.get("completed_evaluations", {})
    if not isinstance(completed_evaluations, dict):
        completed_evaluations = {}
    checkpoint_sha256 = _sha256(best_path)
    dataset_fingerprint = str(report["fingerprint"])
    class_names = tuple(report.get("class_names") or config.get("class_names") or CLASS_NAMES)

    def completed_record(split: str, required_artifacts: tuple[str, ...]) -> dict[str, Any] | None:
        record = completed_evaluations.get(split)
        if not isinstance(record, dict):
            return None
        if record.get("dataset_fingerprint") != dataset_fingerprint or record.get("best_checkpoint_sha256") != checkpoint_sha256:
            return None
        artifacts = record.get("artifacts", {})
        if not isinstance(artifacts, dict):
            return None
        for artifact_name in required_artifacts:
            artifact = artifacts.get(artifact_name)
            if not isinstance(artifact, dict):
                return None
            relative_path = Path(str(artifact.get("path", "")))
            if relative_path.is_absolute() or len(relative_path.parts) != 1:
                return None
            artifact_path = run_dir / relative_path
            if not artifact_path.is_file() or _sha256(artifact_path) != artifact.get("sha256"):
                return None
        return record

    def save_completed_record(
        split: str,
        per_class: dict[str, Any],
        artifact_names: tuple[str, ...],
        score_summary: dict[str, Any] | None,
    ) -> None:
        artifacts = {}
        for artifact_name in artifact_names:
            artifact_path = run_dir / artifact_name
            if not artifact_path.is_file():
                raise FileNotFoundError(f"Completed {split} evaluation is missing {artifact_path.name}")
            artifacts[artifact_name] = {"path": artifact_path.name, "sha256": _sha256(artifact_path)}
        completed_evaluations[split] = {
            "split": split,
            "dataset_fingerprint": dataset_fingerprint,
            "best_checkpoint_sha256": checkpoint_sha256,
            "completed_at": time.time(),
            "per_class": per_class,
            "score_summary": score_summary,
            "artifacts": artifacts,
        }
        atomic_write_json(evaluation_manifest_path, {"completed_evaluations": completed_evaluations})

    metrics_plot = _write_metrics_plot(run_dir)
    if metrics_plot:
        _update_state(state_path, {"metrics_curve": str(metrics_plot.resolve())}, "metrics_curve_saved", events_path)
    inference_path = run_dir / "best_inference.pt"
    if not inference_path.is_file():
        _write_inference_checkpoint(best_path, inference_path, yolo_root)

    thresholds_path = run_dir / "validation_thresholds.json"
    sensitivity_path = run_dir / "sensitivity_thresholds.json"
    validation_record = completed_record("validation", ("validation_predictions.jsonl", "validation_thresholds.json"))
    if validation_record:
        validation_state = {
            "evaluation_per_class": validation_record.get("per_class", {}),
            "validation_score_summary": validation_record.get("score_summary"),
        }
        _update_state(state_path, {"stage": "evaluation", "stage_label": "复用已完成的最终验证结果，准备测试集评估", "validation_per_class": validation_state["evaluation_per_class"]}, "validation_reused", events_path)
    else:
        config["capture_validation_predictions"] = True
        config["validation_predictions_path"] = str((run_dir / "validation_predictions.jsonl").resolve())
        _write_hook_config(config_path, config)
        validation_state = _evaluation_command(
            config,
            report,
            run_dir,
            best_path,
            state_path,
            events_path,
            control_path,
            config_path,
            split="val",
            capture=True,
        )

    threshold_report, validation_analysis = _calibrate_thresholds(
        run_dir / "validation_predictions.jsonl",
        Path(report["manifest"]),
        thresholds_path,
        state_path,
        events_path,
        dataset_fingerprint=dataset_fingerprint,
        checkpoint_sha256=checkpoint_sha256,
        class_names=class_names,
    )
    save_completed_record(
        "validation",
        validation_state.get("evaluation_per_class", {}),
        ("validation_predictions.jsonl", "validation_thresholds.json"),
        validation_state.get("validation_score_summary"),
    )

    config["capture_validation_predictions"] = False
    config["validation_predictions_path"] = None
    _write_hook_config(config_path, config)
    test_record = completed_record("test", ("test_predictions.jsonl",))
    if test_record:
        test_state = {
            "evaluation_per_class": test_record.get("per_class", {}),
            "test_score_summary": test_record.get("score_summary"),
        }
        _update_state(state_path, {"stage": "evaluation", "stage_label": "复用已完成的最终测试结果", "evaluation_split": "test", "evaluation_per_class": test_state["evaluation_per_class"]}, "test_reused", events_path)
    else:
        test_state = _evaluation_command(
            config,
            report,
            run_dir,
            best_path,
            state_path,
            events_path,
            control_path,
            config_path,
            split="test",
            capture=True,
        )
        save_completed_record(
            "test",
            test_state.get("evaluation_per_class", {}),
            ("test_predictions.jsonl",),
            test_state.get("test_score_summary"),
        )
    test_threshold_data = _prepare_threshold_split(
        run_dir / "test_predictions.jsonl",
        Path(report["manifest"]),
        "test",
        class_names,
    )
    validation_threshold_data = _prepare_threshold_split(
        run_dir / "validation_predictions.jsonl",
        Path(report["manifest"]),
        "val",
        class_names,
    )
    sensitivity_analysis = _build_sensitivity_analysis(
        validation_threshold_data,
        test_threshold_data,
        class_names=class_names,
        dataset_fingerprint=dataset_fingerprint,
        checkpoint_sha256=checkpoint_sha256,
    )
    atomic_write_json(sensitivity_path, sensitivity_analysis)
    moderate_profile = next(profile for profile in sensitivity_analysis["profiles"] if profile["profile"] == "Moderate")
    mall_fpr_path = None
    negative_fpr_path = None
    if config.get("dataset_profile") == MALL_FINETUNE_PROFILE:
        mall_fpr_path = run_dir / "mall_false_positive_summary.json"
        mall_fpr_summary = {
            "schema_version": 1,
            "source": "mall_negative",
            "negative_label_assumption": "All Mall images are treated as containing no Knife/Gun objects. The source provides crowd-counting labels, not weapon ground truth; any weapon-positive Mall image invalidates the corresponding false-negative and FPR interpretation.",
            "validation_images": int((report.get("mall_split") or {}).get("images", {}).get("val", 0)),
            "test_images": int((report.get("mall_split") or {}).get("images", {}).get("test", 0)),
            "thresholds_calibrated_on": "Validation split only",
            "profiles": [],
        }
        for profile in sensitivity_analysis["profiles"]:
            validation_mall = profile.get("validation_negative_false_positives_by_source", {}).get("mall_negative")
            test_mall = profile.get("test_negative_false_positives_by_source", {}).get("mall_negative")
            mall_fpr_summary["profiles"].append({
                "profile": profile["profile"],
                "thresholds": profile["thresholds"],
                "validation": validation_mall,
                "test": test_mall,
            })
        atomic_write_json(mall_fpr_path, mall_fpr_summary)
    elif config.get("dataset_profile") in {
        EXPANDED_NEGATIVE_FINETUNE_PROFILE,
        HARD_NEGATIVE_FINETUNE_PROFILE,
        SELECTED_SOURCES_FINETUNE_PROFILE,
    }:
        negative_fpr_path = run_dir / "negative_false_positive_summary.json"
        negative_fpr_summary = {
            "schema_version": 1,
            "sources": sorted((report.get("negative_sources") or {}).keys()),
            "negative_label_assumption": report.get("negative_label_assumption"),
            "thresholds_calibrated_on": "Validation split only",
            "profiles": [],
        }
        for profile in sensitivity_analysis["profiles"]:
            validation = profile.get("validation_negative_false_positives_by_source", {})
            test = profile.get("test_negative_false_positives_by_source", {})
            negative_fpr_summary["profiles"].append({
                "profile": profile["profile"],
                "thresholds": profile["thresholds"],
                "validation": {name: validation.get(name) for name in negative_fpr_summary["sources"]},
                "test": {name: test.get(name) for name in negative_fpr_summary["sources"]},
            })
        atomic_write_json(negative_fpr_path, negative_fpr_summary)
    _update_state(
        state_path,
        {
            "sensitivity_threshold_analysis": sensitivity_analysis,
            "sensitivity_thresholds_report": str(sensitivity_path.resolve()),
            "validation_thresholds": moderate_profile["thresholds"],
        },
        "sensitivity_threshold_analysis_completed",
        events_path,
    )
    now = time.time()
    with (run_dir / "epoch_metrics.csv").open(encoding="utf-8-sig", newline="") as stream:
        completed_epochs = list(csv.DictReader(stream))
    last_completed_epoch = max(int(row["epoch"]) for row in completed_epochs)
    best_epoch = int(max(completed_epochs, key=lambda row: float(row["fitness"]))["epoch"])
    final_state = {
        "status": "completed",
        "stage": "completed",
        "stage_label": "YOLOv9-T 训练、验证与测试全部完成",
        "failed_stage": None,
        "error": None,
        "traceback": None,
        "training_failure_diagnostic": None,
        "epoch": last_completed_epoch,
        "completed_epochs": last_completed_epoch,
        "best_epoch": best_epoch,
        "epoch_progress": 1.0,
        "completed_at": now,
        "elapsed_seconds": elapsed_before + max(0.0, now - started_at),
        "budget_exceeded_seconds": (
            None
            if int(config.get("budget_seconds", 0)) == 0
            else max(0.0, elapsed_before + now - started_at - int(config["budget_seconds"]))
        ),
        "best_checkpoint": str(best_path.resolve()),
        "best_checkpoint_sha256": checkpoint_sha256,
        "dataset_fingerprint": dataset_fingerprint,
        "class_names": list(class_names),
        "validation_per_class": validation_state.get("evaluation_per_class", {}),
        "validation_score_summary": validation_state.get("validation_score_summary"),
        "inference_checkpoint": str(inference_path.resolve()),
        "last_checkpoint": str((run_dir / "checkpoints" / "last.pt").resolve()) if (run_dir / "checkpoints" / "last.pt").is_file() else None,
        "thresholds": moderate_profile["thresholds"],
        "sensitivity_threshold_analysis": sensitivity_analysis,
        "sensitivity_thresholds_report": str(sensitivity_path.resolve()),
        "test_per_class": test_state.get("evaluation_per_class", {}),
        "test_score_summary": test_state.get("test_score_summary"),
        "test_negative_metrics": _negative_test_summary(test_state.get("evaluation_per_class", {}), report, run_dir),
    }
    if mall_fpr_path:
        final_state["mall_false_positive_summary"] = str(mall_fpr_path.resolve())
    if negative_fpr_path:
        final_state["negative_false_positive_summary"] = str(negative_fpr_path.resolve())
    _update_state(state_path, final_state, "run_completed", events_path)
    final_state = _read_json(state_path)
    atomic_write_json(run_dir / "final_report.json", final_state)
    return final_state


def _write_metrics_plot(run_dir: Path) -> Path | None:
    metrics_path = run_dir / "epoch_metrics.csv"
    if not metrics_path.is_file():
        return None
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import pandas as pd

        metrics = pd.read_csv(metrics_path)
        if metrics.empty:
            return None
        series = [
            ("metrics/mAP50-95", "mAP50-95"),
            ("metrics/mAP50", "mAP50"),
            ("metrics/precision", "Precision"),
            ("metrics/recall", "Recall"),
            ("train/box_loss", "Box loss"),
            ("train/cls_loss", "Class loss"),
            ("train/dfl_loss", "DFL loss"),
        ]
        fig, axes = plt.subplots(2, 2, figsize=(11, 7), constrained_layout=True)
        for axis, group in zip(axes.flat, (series[:2], series[2:4], series[4:6], series[6:])):
            for column, label in group:
                if column in metrics:
                    axis.plot(metrics["epoch"], metrics[column], marker="o", markersize=3, label=label)
            axis.set_xlabel("Epoch")
            axis.grid(True, alpha=0.25)
            axis.legend(loc="best")
        axes[0, 0].set_title("Detection metrics")
        axes[0, 1].set_title("Precision and recall")
        axes[1, 0].set_title("Training losses")
        axes[1, 1].set_title("DFL loss")
        path = run_dir / "metrics_curve.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        return path
    except Exception as exc:
        _update_state(run_dir / "progress.json", {"metrics_plot_warning": str(exc)})
        return None


def _resume_final_evaluations(run_dir: Path) -> dict[str, Any]:
    started_at = time.time()
    config_path = run_dir / "runtime_config.json"
    resolved_path = run_dir / "resolved_training_config.json"
    report_path = run_dir / "workspace_report.json"
    if not resolved_path.is_file() or not report_path.is_file():
        raise FileNotFoundError("Saved run is missing its resolved configuration or dataset report")
    config = _validated_config(_read_json(resolved_path))
    report = _read_json(report_path)
    if config.get("dataset_fingerprint") != report.get("fingerprint"):
        raise ValueError("Saved dataset fingerprint does not match the trained model")
    if not Path(report.get("data_yaml", "")).is_file():
        raise FileNotFoundError("The audited dataset cache is missing; final evaluation cannot resume safely")
    yolo_root = Path(config["yolov9_root"])
    best_path = run_dir / "official_train" / "train" / "weights" / "best.pt"
    if not best_path.is_file():
        raise FileNotFoundError("This run has no best checkpoint to evaluate")
    state_path = run_dir / "progress.json"
    events_path = run_dir / "events.jsonl"
    control_path = run_dir / "control.json"
    previous = _read_json(state_path)
    elapsed_before = float(previous.get("elapsed_seconds") or 0.0)
    atomic_write_json(control_path, {"command": "", "updated_at": time.time()})
    _write_hook_config(config_path, config)
    _update_state(
        state_path,
        {
            "status": "running",
            "stage": "evaluation",
            "stage_label": "从已保存的最佳模型继续最终验证和测试",
            "failed_stage": None,
            "error": None,
            "traceback": None,
            "training_failure_diagnostic": None,
            "evaluation_resume_started_at": started_at,
            "segment_started_at": started_at,
            "elapsed_seconds": elapsed_before,
        },
        "evaluation_resume_started",
        events_path,
    )
    try:
        if config.get("dataset_profile") == HARD_NEGATIVE_FINETUNE_PROFILE:
            _complete_hard_negative_fpr_tracking(
                config, report, run_dir, state_path, events_path, control_path, config_path, yolo_root
            )
        return _complete_final_evaluations(
            config,
            report,
            run_dir,
            state_path,
            events_path,
            control_path,
            config_path,
            yolo_root,
            started_at=started_at,
            elapsed_before=elapsed_before,
        )
    except Exception as exc:
        current = _read_json(state_path)
        _update_state(
            state_path,
            {"status": "failed", "failed_stage": current.get("stage", "evaluation"), "stage": "failed", "stage_label": "最终评估未完成，可继续评估", "error": str(exc), "traceback": traceback.format_exc(), "completed_at": time.time(), "elapsed_seconds": elapsed_before + time.time() - started_at},
            "evaluation_resume_failed",
            events_path,
        )
        raise


def _evaluate_last_checkpoint(run_dir: Path) -> dict[str, Any]:
    """Evaluate a completed run's last full checkpoint with validation-locked thresholds."""
    run_dir = run_dir.resolve()
    resolved_path = run_dir / "resolved_training_config.json"
    source_report_path = run_dir / "workspace_report.json"
    if not resolved_path.is_file() or not source_report_path.is_file():
        raise FileNotFoundError("Saved run is missing its resolved configuration or dataset report")
    config = _validated_config(_read_json(resolved_path))
    report = _read_json(source_report_path)
    source_state = _read_json(run_dir / "progress.json")
    if source_state.get("status") != "completed":
        raise ValueError("The last checkpoint comparison requires a completed training run")
    if config.get("dataset_fingerprint") != report.get("fingerprint"):
        raise ValueError("Saved dataset fingerprint does not match the trained model")
    if not Path(report.get("data_yaml", "")).is_file():
        raise FileNotFoundError("The audited dataset cache is missing; checkpoint evaluation cannot proceed")
    checkpoint = run_dir / "checkpoints" / "last.pt"
    saved_epoch = int(source_state.get("checkpoint_saved_epoch") or 0)
    if not checkpoint.is_file() or saved_epoch <= 0:
        raise FileNotFoundError("The run's latest full checkpoint is missing")
    if int(source_state.get("full_checkpoint_saved_epoch") or 0) != saved_epoch:
        raise ValueError("The latest checkpoint is not confirmed as a complete epoch checkpoint")

    evaluation_dir = run_dir / f"last_eval_epoch{saved_epoch}"
    if evaluation_dir.exists():
        evaluation_dir = run_dir / f"last_eval_epoch{saved_epoch}_{time.strftime('%Y%m%d_%H%M%S')}"
    evaluation_dir.mkdir(parents=True, exist_ok=False)
    state_path = evaluation_dir / "progress.json"
    events_path = evaluation_dir / "events.jsonl"
    control_path = evaluation_dir / "control.json"
    config_path = evaluation_dir / "runtime_config.json"
    atomic_write_json(control_path, {"command": "", "updated_at": time.time()})
    atomic_write_json(config_path, config)
    class_names = tuple(config.get("class_names") or report.get("class_names") or CLASS_NAMES)
    checkpoint_sha256 = _sha256(checkpoint)
    started_at = time.time()
    _update_state(
        state_path,
        {
            "status": "running",
            "stage": "evaluation",
            "stage_label": f"使用 epoch {saved_epoch} last checkpoint 校准并评估",
            "source_run_id": source_state.get("run_id", run_dir.name),
            "evaluation_checkpoint": str(checkpoint),
            "evaluation_checkpoint_epoch": saved_epoch,
            "dataset_fingerprint": report.get("fingerprint"),
            "started_at": started_at,
            "error": None,
            "traceback": None,
        },
        "last_checkpoint_evaluation_started",
        events_path,
    )
    try:
        validation_state = _evaluation_command(
            config,
            report,
            evaluation_dir,
            checkpoint,
            state_path,
            events_path,
            control_path,
            config_path,
            split="val",
            capture=True,
        )
        threshold_path = evaluation_dir / "validation_thresholds.json"
        threshold_report, _ = _calibrate_thresholds(
            evaluation_dir / "validation_predictions.jsonl",
            Path(report["manifest"]),
            threshold_path,
            state_path,
            events_path,
            dataset_fingerprint=str(report["fingerprint"]),
            checkpoint_sha256=checkpoint_sha256,
            class_names=class_names,
        )
        test_state = _evaluation_command(
            config,
            report,
            evaluation_dir,
            checkpoint,
            state_path,
            events_path,
            control_path,
            config_path,
            split="test",
            capture=True,
        )
        validation_data = _prepare_threshold_split(
            evaluation_dir / "validation_predictions.jsonl", Path(report["manifest"]), "val", class_names
        )
        test_data = _prepare_threshold_split(
            evaluation_dir / "test_predictions.jsonl", Path(report["manifest"]), "test", class_names
        )
        sensitivity = _build_sensitivity_analysis(
            validation_data,
            test_data,
            class_names=class_names,
            dataset_fingerprint=str(report["fingerprint"]),
            checkpoint_sha256=checkpoint_sha256,
        )
        sensitivity_path = evaluation_dir / "sensitivity_thresholds.json"
        atomic_write_json(sensitivity_path, sensitivity)
        moderate = next(profile for profile in sensitivity["profiles"] if profile["profile"] == "Moderate")
        negative_summary = _negative_test_summary(test_state.get("evaluation_per_class", {}), report, evaluation_dir)
        negative_path = evaluation_dir / "negative_false_positive_summary.json"
        atomic_write_json(negative_path, negative_summary)
        result = {
            "status": "completed",
            "source_run_id": source_state.get("run_id", run_dir.name),
            "source_run_dir": str(run_dir),
            "evaluation_checkpoint": str(checkpoint),
            "evaluation_checkpoint_epoch": saved_epoch,
            "evaluation_checkpoint_sha256": checkpoint_sha256,
            "training_best_epoch": source_state.get("best_epoch"),
            "dataset_fingerprint": report.get("fingerprint"),
            "validation_images": int(report["images"]["val"]),
            "test_images": int(report["images"]["test"]),
            "calibration_split": "val",
            "validation_thresholds": threshold_report["thresholds"],
            "validation_metrics_at_locked_thresholds": moderate["validation_micro"],
            "test_metrics_at_locked_thresholds": moderate["test_micro"],
            "validation_yolov9_metrics": validation_state.get("evaluation_per_class", {}),
            "test_yolov9_metrics": test_state.get("evaluation_per_class", {}),
            "sensitivity_thresholds_report": str(sensitivity_path.resolve()),
            "validation_threshold_report": str(threshold_path.resolve()),
            "negative_false_positive_summary": str(negative_path.resolve()),
            "test_negative_metrics": negative_summary,
        }
        report_path = evaluation_dir / "final_report.json"
        atomic_write_json(report_path, result)
        _update_state(
            state_path,
            {
                "status": "completed",
                "stage": "completed",
                "stage_label": f"Epoch {saved_epoch} last checkpoint 验证与测试完成",
                "validation_images_done": int(report["images"]["val"]),
                "test_images_done": int(report["images"]["test"]),
                "validation_thresholds": threshold_report["thresholds"],
                "validation_per_class": validation_state.get("evaluation_per_class", {}),
                "test_per_class": test_state.get("evaluation_per_class", {}),
                "final_report": str(report_path.resolve()),
                "completed_at": time.time(),
                "error": None,
                "traceback": None,
            },
            "last_checkpoint_evaluation_completed",
            events_path,
        )
        return result
    except Exception as exc:
        current = _read_json(state_path)
        _update_state(
            state_path,
            {
                "status": "failed",
                "stage": "failed",
                "failed_stage": current.get("stage", "evaluation"),
                "stage_label": f"Epoch {saved_epoch} last checkpoint 评估失败",
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "completed_at": time.time(),
            },
            "last_checkpoint_evaluation_failed",
            events_path,
        )
        raise


def _prepare_initial_finetune_checkpoint(config: dict[str, Any], target_path: Path) -> dict[str, Any]:
    """Validate a trusted two-class checkpoint and make a run-local copy."""
    import torch

    source_path = Path(config["initial_weights_path"]).resolve()
    if str(config["yolov9_root"]) not in sys.path:
        sys.path.insert(0, str(config["yolov9_root"]))
    from models.yolo import Model

    checkpoint = torch.load(source_path, map_location="cpu", weights_only=False)
    source_model = checkpoint.get("ema") or checkpoint.get("model")
    if source_model is None:
        raise ValueError("Selected checkpoint does not contain a YOLO model object")
    source_names = getattr(source_model, "names", None)
    if isinstance(source_names, dict):
        names = [str(source_names[key]) for key in sorted(source_names, key=lambda item: int(item))]
    elif isinstance(source_names, (list, tuple)):
        names = [str(name) for name in source_names]
    else:
        names = []
    model_nc = getattr(source_model, "nc", None)
    if model_nc is None and getattr(source_model, "model", None):
        model_nc = getattr(source_model.model[-1], "nc", None)
    if int(model_nc or 0) != 2 or names != ["knife", "gun"]:
        raise ValueError(f"Selected checkpoint must have exactly [knife, gun] classes; found nc={model_nc}, names={names}")

    target_model = Model(config["model_config"], ch=3, nc=2).float()
    source_state = source_model.float().state_dict()
    target_state = target_model.state_dict()
    compatible = {
        key: value for key, value in source_state.items()
        if key in target_state and tuple(value.shape) == tuple(target_state[key].shape)
    }
    ratio = len(compatible) / max(1, len(target_state))
    if ratio < 0.95:
        raise ValueError(
            "Selected checkpoint is not compatible with the local two-class YOLOv9-T architecture "
            f"({len(compatible)}/{len(target_state)} tensors match)"
        )
    del checkpoint, source_model, target_model, source_state, target_state, compatible
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_path.with_name(target_path.name + f".{uuid.uuid4().hex}.tmp")
    shutil.copy2(source_path, temporary)
    os.replace(temporary, target_path)
    return {
        "method": "initialize compatible YOLOv9-T layers from the selected best checkpoint; create a fresh five-epoch optimizer schedule",
        "source_checkpoint": str(source_path),
        "source_checkpoint_sha256": _sha256(source_path),
        "run_local_checkpoint": target_path.name,
        "run_local_checkpoint_sha256": _sha256(target_path),
        "source_class_names": names,
        "source_nc": int(model_nc),
        "compatible_tensor_ratio": ratio,
        "source_checkpoint_preserved": True,
    }


def _start_run(config_payload: dict[str, Any], *, resume_dir: Path | None = None) -> dict[str, Any]:
    started = time.time()
    payload = dict(config_payload)
    requested_run_dir = Path(payload.pop("_run_dir")) if payload.get("_run_dir") else None
    if resume_dir:
        resolved_path = resume_dir / "resolved_training_config.json"
        if not resolved_path.is_file():
            raise FileNotFoundError(f"Resolved training configuration not found: {resolved_path}")
        config = _validated_config(_read_json(resolved_path))
    else:
        config = _validated_config(payload)
    # Model-object checkpoints need the upstream model classes during unpickling,
    # including in a resumed pipeline that has not rebuilt the COCO model.
    if config["yolov9_root"] not in sys.path:
        sys.path.insert(0, config["yolov9_root"])
    output_root = Path(config["output_root"])
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = resume_dir.name if resume_dir else requested_run_dir.name if requested_run_dir else time.strftime("yolov9_%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    run_dir = resume_dir or requested_run_dir or output_root / "runs" / run_id
    if not run_dir.resolve().is_relative_to(output_root.resolve()):
        raise ValueError(f"Run directory must stay under output_root: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "checkpoints").mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "progress.json"
    events_path = run_dir / "events.jsonl"
    control_path = run_dir / "control.json"
    config_path = run_dir / "runtime_config.json"
    log_path = run_dir / "run.log"
    previous_state = _read_json(state_path) if resume_dir else {}
    elapsed_before = float(previous_state.get("elapsed_seconds") or 0.0)
    original_started_at = float(previous_state.get("started_at") or started)
    atomic_write_json(control_path, {"command": "", "updated_at": time.time()})
    _update_state(state_path, {"run_id": run_id, "run_dir": str(run_dir.resolve()), "status": "running", "stage": "preparing", "stage_label": "正在准备数据和模型", "started_at": original_started_at, "segment_started_at": started, "budget_seconds": config["budget_seconds"], "elapsed_seconds": elapsed_before, "dashboard_child_pid": os.getpid(), "config": config}, "run_started", events_path)
    native_run_dir = run_dir / "official_train"
    try:
        dependency_info = check_dependencies()
        font_assets = _ensure_yolov9_font_assets()
        _update_state(
            state_path,
            {"environment": dependency_info, "offline_font_assets": font_assets},
            "environment_checked",
            events_path,
        )
        finetune = config.get("dataset_profile") in FINETUNE_PROFILES
        bootstrap_path = run_dir / "checkpoints" / ("starting_model.pt" if finetune else "coco_dual_bootstrap.pt")
        if resume_dir:
            report_path = resume_dir / "workspace_report.json"
            if not report_path.is_file():
                raise FileNotFoundError(f"Saved dataset audit report not found: {report_path}")
            report = _read_json(report_path)
            probe = config.get("probe") or {}
            if not report.get("fingerprint") or not probe.get("batch_size"):
                raise ValueError("Saved run is missing its dataset fingerprint or measured training configuration")
            if config.get("dataset_fingerprint") != report.get("fingerprint"):
                raise ValueError("Saved dataset fingerprint does not match the resolved training configuration")
            if not Path(report["data_yaml"]).is_file():
                raise FileNotFoundError(f"Fingerprint dataset cache is missing: {report['data_yaml']}")
            initialization_report = resume_dir / ("initial_model_report.json" if finetune else "coco_transfer_report.json")
            if not initialization_report.is_file() or not bootstrap_path.is_file():
                raise FileNotFoundError("Saved run is missing its initialization report or starting checkpoint")
            hyp_path = resume_dir / "hyp.yaml"
            native_run_dir = resume_dir / "official_train"
            user_config = _read_json(resume_dir / "user_config.json")
            _update_state(state_path, {"stage": "resuming", "stage_label": "正在检查 checkpoint 并恢复训练", "dataset": {k: v for k, v in report.items() if k != "preview_samples"}, "environment": dependency_info}, "resume_preparing", events_path)
        else:
            user_config = dict(config)
            atomic_write_json(run_dir / "user_config.json", user_config)
            report = _build_dataset_for_config(
                config,
                output_root=output_root,
                progress=lambda e: _update_state(state_path, {"stage": e["stage"], "stage_label": e.get("message", e["stage"]), "preparation_completed": e.get("completed"), "preparation_total": e.get("total")}, e["stage"], events_path),
            )
            if config.get("dataset_profile") == HARD_NEGATIVE_FINETUNE_PROFILE:
                config.update({
                    "track_negative_fpr": True,
                    "fpr_manifest_path": str(Path(report["manifest"]).resolve()),
                    "fpr_confidence_threshold": 0.25,
                    "epoch_checkpoint_dir": str((run_dir / "checkpoints" / "epoch_models").resolve()),
                })
            if report.get("video_split_audit", {}).get("available") and not report["video_split_audit"].get("valid"):
                raise RuntimeError(f"ShanghaiTech video leakage found: {report['video_split_audit']['cross_split_video_ids']}")
            sohas_sequence_audit = (report.get("sohas_annotation_audit") or {}).get("sequence_split_audit")
            if sohas_sequence_audit and not sohas_sequence_audit.get("valid"):
                raise RuntimeError(
                    "SOHAS sequence leakage found: "
                    f"train/val groups={sohas_sequence_audit.get('trainval_cross_split_groups')}; "
                    f"trainval/test overlap={sohas_sequence_audit.get('trainval_test_group_overlap')}"
                )
            _write_preview_html(run_dir, report)
            atomic_write_json(run_dir / "workspace_report.json", report)
            if config.get("dataset_profile") in {"full_dataset_v1", *FINETUNE_PROFILES} and report.get("excluded_csv"):
                excluded_path = Path(report["excluded_csv"])
                if not excluded_path.is_file():
                    raise FileNotFoundError(f"完整数据集 V1 排除清单不存在：{excluded_path}")
                shutil.copy2(excluded_path, run_dir / "excluded_samples.csv")
            _update_state(
                state_path,
                {
                    "dataset": {k: v for k, v in report.items() if k != "preview_samples"},
                    "stage": "initializing",
                    "stage_label": "正在准备后训练起始模型" if finetune else "正在迁移 COCO 权重",
                },
                "dataset_ready",
                events_path,
            )
            hyp_path = run_dir / "hyp.yaml"
            hyp = _write_hyp(config, hyp_path)
            if finetune:
                initialization = _prepare_initial_finetune_checkpoint(config, bootstrap_path)
                atomic_write_json(run_dir / "initial_model_report.json", initialization)
                config["probe_weights_path"] = str(bootstrap_path.resolve())
                _update_state(
                    state_path,
                    {"initial_model": initialization, "stage": "probing", "stage_label": "正在用所选权重测量本机训练速度"},
                    "initial_model_loaded",
                    events_path,
                )
            else:
                initialization = _build_coco_dual_bootstrap(config, bootstrap_path)
                atomic_write_json(run_dir / "coco_transfer_report.json", initialization)
                _update_state(state_path, {"coco_weight_transfer": initialization, "stage": "probing", "stage_label": "正在测量本机训练速度"}, "coco_weights_loaded", events_path)
            probe = _probe_batch_time(config, report, hyp, Path(report["data_yaml"]))
            config["batch_size"] = probe["batch_size"]
            config["train_batches_per_epoch"] = probe["train_batches_per_epoch"]
            config["validation_batches_per_epoch"] = probe["validation_batches_per_epoch"]
            config["estimated_epoch_seconds"] = probe["estimated_epoch_seconds"]
            config["start_epoch"] = 0
            config["metrics_csv"] = str((run_dir / "epoch_metrics.csv").resolve())
            config["resume_state_path"] = str((run_dir / "checkpoints" / "resume_state.pt").resolve())
            config["resume_checkpoint_path"] = str((run_dir / "checkpoints" / "last.pt").resolve())
            if int(config["budget_seconds"]) == 0:
                config["epochs"] = int(config["epochs_cap"])
            else:
                config["epochs"] = min(int(config["epochs_cap"]), int((config["budget_seconds"] - (time.time() - started) - 600) // max(1, probe["estimated_epoch_seconds"])))
            if config["epochs"] < 1:
                raise RuntimeError(
                    "在预留至少 10 分钟用于最终评估后，一小时预算不足以完成一个估算 epoch。"
                    f" 本次实测 epoch 约 {probe['estimated_epoch_seconds']:.0f} 秒。"
                )
            close_mosaic = min(int(config["close_mosaic_epochs"]), max(0, config["epochs"] - 1))
            config["close_mosaic_effective"] = close_mosaic
            config["probe"] = probe
            config["dataset_fingerprint"] = report["fingerprint"]
            atomic_write_json(run_dir / "resolved_training_config.json", config)
            _write_hook_config(config_path, config)
            _update_state(
                state_path,
                {
                    "probe": probe,
                    "batch_size": probe["batch_size"],
                    "epochs": config["epochs"],
                    "estimated_epoch_seconds": probe["estimated_epoch_seconds"],
                    "estimated_training_seconds": config["epochs"] * probe["estimated_epoch_seconds"],
                    "stage": "training",
                    "stage_label": f"开始全模型微调：batch={probe['batch_size']}，计划 {config['epochs']} epochs",
                },
                "training_plan_ready",
                events_path,
            )
        native_run_dir = run_dir / "official_train"
        resume_epoch = 0
        resume_args: list[str] = []
        if resume_dir:
            last_path = run_dir / "checkpoints" / "last.pt"
            if not last_path.is_file():
                last_path = native_run_dir / "train" / "weights" / "last.pt"
            if not last_path.is_file():
                raise FileNotFoundError(f"No resumable last.pt at {last_path}")
            import torch

            checkpoint = torch.load(last_path, map_location="cpu", weights_only=False)
            if checkpoint.get("optimizer") is None:
                raise RuntimeError("The saved checkpoint is inference-only and cannot resume training; continue final evaluation if a best model exists")
            resume_epoch = int(checkpoint.get("epoch", -1)) + 1
            config["start_epoch"] = resume_epoch
            configured_resume_path = run_dir / "checkpoints" / (
                f"resume_e{resume_epoch}_b{int(config['batch_size'])}_w{int(config['workers'])}.pt"
            )
            resume_checkpoint = _checkpoint_for_batch_retry(
                last_path,
                configured_resume_path,
                int(config["batch_size"]),
                workers=int(config["workers"]),
                epochs=int(config.get("epochs") or config["epochs_cap"]),
                patience=int(config["patience"]),
            )
            atomic_write_json(run_dir / "resolved_training_config.json", config)
            _write_hook_config(config_path, config)
            resume_args = ["--resume", str(resume_checkpoint)]
        else:
            (native_run_dir / "train").mkdir(parents=True, exist_ok=True)
        config["start_epoch"] = resume_epoch
        _write_hook_config(config_path, config)
        yolo_root = Path(config["yolov9_root"])
        environment = _child_environment(
            run_dir,
            config_path,
            state_path,
            events_path,
            control_path,
            resume_epoch=resume_epoch,
            resume_state=run_dir / "checkpoints" / "resume_state.pt" if resume_epoch else None,
            validation_batch=probe["batch_size"],
        )
        if resume_dir:
            command = [sys.executable, "-u", "train_dual.py", *resume_args]
        else:
            command = [
                sys.executable,
                "-u",
                "train_dual.py",
                "--weights", str(bootstrap_path),
                "--cfg", config["model_config"],
                "--data", report["data_yaml"],
                "--hyp", str(hyp_path),
                "--epochs", str(config["epochs"]),
                "--batch-size", str(probe["batch_size"]),
                "--imgsz", str(config["imgsz"]),
                "--device", str(config["device"]),
                "--project", str(native_run_dir),
                "--name", "train",
                "--exist-ok",
                "--optimizer", str(config["optimizer"]),
                "--workers", str(int(config["workers"])),
                "--patience", str(config["patience"]),
                "--seed", str(config["seed"]),
                "--close-mosaic", str(close_mosaic),
                "--save-period", "-1",
                "--cos-lr",
                "--noplots",
            ]
        deadline = None if int(config["budget_seconds"]) == 0 else started + max(0, int(config["budget_seconds"]) - elapsed_before)
        oom_retry_count = len(config.get("batch_retry_history") or [])
        worker_retry_count = len(config.get("worker_memory_retry_history") or [])
        while True:
            attempt_log_offset = log_path.stat().st_size if log_path.is_file() else 0
            return_code = _run_child(command, yolo_root, environment, log_path, state_path, events_path, budget_deadline=deadline)
            if return_code == 0:
                break
            try:
                with log_path.open("rb") as stream:
                    stream.seek(attempt_log_offset)
                    attempt_log = stream.read().decode("utf-8", errors="replace")
            except OSError:
                attempt_log = ""
            control_after_attempt = _read_json(control_path)
            current_batch = int(config["batch_size"])
            current_workers = int(config["workers"])
            next_batch = _next_oom_retry_batch(
                current_batch,
                attempt_log,
                str(control_after_attempt.get("command") or ""),
                oom_retry_count,
            )
            next_workers = None
            if next_batch is None:
                next_workers = _next_dataloader_worker_retry(
                    current_workers,
                    attempt_log,
                    str(control_after_attempt.get("command") or ""),
                    worker_retry_count,
                )
            if next_batch is None and next_workers is None:
                break

            if next_batch is not None:
                oom_retry_count += 1
                config.setdefault("batch_retry_history", []).append({
                    "attempt": oom_retry_count,
                    "failed_batch_size": current_batch,
                    "retry_batch_size": next_batch,
                    "reason": "CUDA out of memory",
                    "time": time.time(),
                })
                config["batch_size"] = next_batch
                config["train_batches_per_epoch"] = math.ceil(int(report["images"]["train"]) / next_batch)
                config["validation_batches_per_epoch"] = math.ceil(int(report["images"]["val"]) / max(1, next_batch * 2))
                config["estimated_epoch_seconds"] = float(config.get("estimated_epoch_seconds") or 0) * current_batch / next_batch
                config.setdefault("probe", {})["batch_size"] = next_batch
                config["probe"]["train_batches_per_epoch"] = config["train_batches_per_epoch"]
                config["probe"]["validation_batches_per_epoch"] = config["validation_batches_per_epoch"]
                config["probe"]["estimated_epoch_seconds"] = config["estimated_epoch_seconds"]
                retry_event = "training_oom_batch_retry"
                retry_message = f"检测到 batch={current_batch} 显存不足，自动降到 {next_batch}"
            else:
                worker_retry_count += 1
                config["workers"] = int(next_workers)
                config.setdefault("worker_memory_retry_history", []).append({
                    "attempt": worker_retry_count,
                    "failed_workers": current_workers,
                    "retry_workers": int(next_workers),
                    "batch_size": current_batch,
                    "reason": "OpenCV/DataLoader host-memory allocation failure",
                    "time": time.time(),
                })
                retry_event = "training_dataloader_memory_retry"
                retry_message = (
                    f"检测到 DataLoader/OpenCV 系统内存不足，将 workers 从 {current_workers} 降到 {next_workers}，"
                    f"batch 保持 {current_batch}"
                )

            checkpoint_path = run_dir / "checkpoints" / "last.pt"
            if not checkpoint_path.is_file():
                checkpoint_path = native_run_dir / "train" / "weights" / "last.pt"
            if checkpoint_path.is_file():
                import torch

                saved_checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
                resume_epoch = int(saved_checkpoint.get("epoch", -1)) + 1
                retry_checkpoint_name = (
                    f"retry_e{resume_epoch}_b{int(config['batch_size'])}_w{int(config['workers'])}_"
                    f"{oom_retry_count + worker_retry_count}.pt"
                )
                retry_checkpoint = _checkpoint_for_batch_retry(
                    checkpoint_path,
                    run_dir / "checkpoints" / retry_checkpoint_name,
                    int(config["batch_size"]),
                    workers=int(config["workers"]),
                    epochs=int(config.get("epochs") or config["epochs_cap"]),
                    patience=int(config["patience"]),
                )
                config["start_epoch"] = resume_epoch
                atomic_write_json(run_dir / "resolved_training_config.json", config)
                _write_hook_config(config_path, config)
                command = [sys.executable, "-u", "train_dual.py", "--resume", str(retry_checkpoint)]
                environment = _child_environment(
                    run_dir,
                    config_path,
                    state_path,
                    events_path,
                    control_path,
                    resume_epoch=resume_epoch,
                    resume_state=run_dir / "checkpoints" / "resume_state.pt",
                    validation_batch=int(config["batch_size"]),
                )
                recovery_text = f"从 epoch {resume_epoch} 的最近完整 checkpoint 恢复"
            else:
                if next_batch is not None:
                    if "--batch-size" not in command:
                        break
                    command[command.index("--batch-size") + 1] = str(config["batch_size"])
                if next_workers is not None:
                    if "--workers" not in command:
                        break
                    command[command.index("--workers") + 1] = str(config["workers"])
                if next_batch is None and next_workers is None:
                    break
                environment = _child_environment(
                    run_dir,
                    config_path,
                    state_path,
                    events_path,
                    control_path,
                    resume_epoch=0,
                    validation_batch=int(config["batch_size"]),
                )
                recovery_text = (
                    "当前没有完整 epoch checkpoint，从所选后训练起始权重重新开始"
                    if finetune else "当前没有完整 epoch checkpoint，从 COCO 初始化权重重新开始"
                )
            _update_state(
                state_path,
                {
                    "status": "running",
                    "stage": "training",
                    "stage_label": f"{retry_message}；{recovery_text}",
                    "batch_size": int(config["batch_size"]),
                    "workers": int(config["workers"]),
                    "batches_per_epoch": config["train_batches_per_epoch"],
                    "estimated_epoch_seconds": config["estimated_epoch_seconds"],
                    "oom_retries": oom_retry_count,
                    "worker_memory_retries": worker_retry_count,
                    "last_checkpoint": str(checkpoint_path.resolve()) if checkpoint_path.is_file() else None,
                },
                retry_event,
                events_path,
            )
        state = _read_json(state_path)
        control = _read_json(control_path)
        if control.get("command") == "pause":
            full_checkpoint = run_dir / "checkpoints" / "last.pt"
            has_current_checkpoint = full_checkpoint.is_file() and state.get("full_checkpoint_saved_epoch") == state.get("checkpoint_saved_epoch")
            if has_current_checkpoint:
                _update_state(state_path, {"status": "paused", "stage": "paused", "stage_label": "训练已安全暂停，可继续训练", "completed_at": time.time(), "last_checkpoint": str(full_checkpoint.resolve()), "elapsed_seconds": elapsed_before + time.time() - started}, "run_paused", events_path)
                return _read_json(state_path)
            atomic_write_json(control_path, {"command": "", "updated_at": time.time()})
            _update_state(state_path, {"pause_request_not_honored": True, "pause_warning": "未能保存本轮独立完整 checkpoint，因此训练继续并进入最终评估。"}, "pause_request_not_honored", events_path)
            control = {}
        if control.get("command") == "stop":
            checkpoint = run_dir / "checkpoints" / "last.pt"
            if not checkpoint.is_file():
                checkpoint = native_run_dir / "train" / "weights" / "last.pt"
            _update_state(state_path, {"status": "stopped", "stage": "stopped", "stage_label": "训练已停止；最近完整 checkpoint 已保留", "completed_at": time.time(), "last_checkpoint": str(checkpoint.resolve()) if checkpoint.is_file() else None, "elapsed_seconds": elapsed_before + time.time() - started}, "run_stopped", events_path)
            return _read_json(state_path)
        if return_code != 0:
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-6000:]
            traceback_tail = log_tail.rsplit("Traceback (most recent call last):", 1)[-1]
            oom_lines = [line.strip() for line in log_tail.splitlines() if _is_cuda_oom_text(line)]
            diagnostic = (
                oom_lines[-1] if oom_lines
                else traceback_tail.strip().splitlines()[-1] if "Traceback (most recent call last):" in log_tail
                else "See the training log for details"
            )
            _update_state(state_path, {"stage": "training", "stage_label": "训练或逐轮验证失败", "training_exit_code": return_code, "training_failure_diagnostic": diagnostic}, "training_child_failed", events_path)
            raise RuntimeError(f"YOLOv9 training process exited with code {return_code}: {diagnostic}; see {log_path}")

        best_path = native_run_dir / "train" / "weights" / "best.pt"
        last_path = native_run_dir / "train" / "weights" / "last.pt"
        if not best_path.is_file():
            raise FileNotFoundError(f"YOLOv9 training did not save a best model: {best_path}")
        budget_finalize = control.get("command") == "budget_finalize" or bool(state.get("budget_finalization_requested"))
        _update_state(state_path, {"stage": "finalizing", "stage_label": "正在准备最佳推理模型", "best_checkpoint": str(best_path.resolve()), "last_checkpoint": str(last_path.resolve()), "budget_finalization": budget_finalize}, "training_complete", events_path)
        inference_path = run_dir / "best_inference.pt"
        _write_inference_checkpoint(best_path, inference_path, yolo_root)

        if config.get("dataset_profile") == HARD_NEGATIVE_FINETUNE_PROFILE:
            _complete_hard_negative_fpr_tracking(
                config, report, run_dir, state_path, events_path, control_path, config_path, yolo_root
            )

        return _complete_final_evaluations(config, report, run_dir, state_path, events_path, control_path, config_path, yolo_root, started_at=started, elapsed_before=elapsed_before)
    except Exception as exc:
        state = _read_json(state_path)
        if state.get("status") not in {"paused", "stopped"}:
            full_checkpoint = run_dir / "checkpoints" / "last.pt"
            if not full_checkpoint.is_file():
                full_checkpoint = native_run_dir / "train" / "weights" / "last.pt"
            _update_state(state_path, {"status": "failed", "failed_stage": state.get("stage"), "stage": "failed", "stage_label": "运行失败", "error": str(exc), "traceback": traceback.format_exc(), "completed_at": time.time(), "last_checkpoint": str(full_checkpoint.resolve()) if full_checkpoint.is_file() else None, "elapsed_seconds": elapsed_before + time.time() - started}, "run_failed", events_path)
        raise


def _negative_test_summary(_per_class: dict[str, Any], report: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    prediction_file = run_dir / "test_predictions.jsonl"
    if not prediction_file.is_file():
        return {"available": False, "message": "The final test evaluator did not export per-image prediction rows."}
    thresholds = _read_json(run_dir / "validation_thresholds.json").get("thresholds", {})
    class_names = tuple(report.get("class_names") or CLASS_NAMES)
    source_by_image: dict[str, str] = {}
    with Path(report["manifest"]).open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["split"] == "test":
                for candidate in (row["image"], row["source_image"]):
                    source_by_image[str(Path(candidate).resolve())] = row["source"]
    grouped: dict[str, dict[str, int]] = {}
    with prediction_file.open("r", encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            if item.get("targets"):
                continue
            source = source_by_image.get(item["image"], "unknown")
            row = grouped.setdefault(source, {"images": 0, "images_with_false_positive": 0, "false_positive_boxes": 0})
            row["images"] += 1
            false_positives = sum(float(prediction[4]) >= float(thresholds.get(class_names[int(prediction[5])], 1.1)) for prediction in item.get("predictions", []))
            row["false_positive_boxes"] += int(false_positives)
            row["images_with_false_positive"] += int(false_positives > 0)
    for row in grouped.values():
        row["false_positive_image_rate"] = row["images_with_false_positive"] / row["images"] if row["images"] else 0.0
        row["false_positives_per_image"] = row["false_positive_boxes"] / row["images"] if row["images"] else 0.0
    return {"available": True, "prediction_file": str(prediction_file.resolve()), "by_source": grouped}


def _cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    audit.add_argument("--state", type=Path)
    audit.add_argument("--events", type=Path)
    audit.add_argument("--config", type=Path)
    train = sub.add_parser("train")
    train.add_argument("--config", type=Path, required=True)
    resume = sub.add_parser("resume")
    resume.add_argument("--run-dir", type=Path, required=True)
    resume.add_argument("--config", type=Path)
    eval_resume = sub.add_parser("eval-resume")
    eval_resume.add_argument("--run-dir", type=Path, required=True)
    eval_last = sub.add_parser("eval-last")
    eval_last.add_argument("--run-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = _cli().parse_args()
    try:
        if args.command == "audit":
            audit_config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else None
            audit_command(args.output_root, args.state, args.events, audit_config)
        elif args.command == "train":
            payload = json.loads(args.config.read_text(encoding="utf-8"))
            _start_run(payload)
        elif args.command == "eval-resume":
            _resume_final_evaluations(args.run_dir.resolve())
        elif args.command == "eval-last":
            _evaluate_last_checkpoint(args.run_dir.resolve())
        else:
            run_dir = args.run_dir.resolve()
            config_path = args.config or run_dir / "user_config.json"
            if not config_path.is_file():
                raise FileNotFoundError(f"Run configuration not found: {config_path}")
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            _start_run(payload, resume_dir=run_dir)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
