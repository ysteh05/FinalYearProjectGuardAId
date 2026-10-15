"""Train the selected YOLOv9-T weapon detector.

This script is the standalone version of the training protocol used in the
weapon-model comparison notebook.  It delegates the actual training loop to
the official YOLOv9 ``train_dual.py`` implementation, while keeping dataset
validation, hyper-parameter creation, reproducibility and logging here.

Example (PowerShell)::

    python model_code/yolov9_weapon_train.py `
        --dataset-root "Dangerous Items" `
        --yolov9-root "results (1)/yolov9" `
        --pretrained "results (1)/yolov9/yolov9-t-converted.pt" `
        --device 0

The YOLOv9 pretrained file is not included in this repository.  Download or
provide the trusted project checkpoint before starting a training run.
"""

from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
import yaml


CLASS_NAMES = (
    "parang_machete",
    "knife",
    "baseball_bat",
    "long_gun",
    "gun",
)

# Shared values used in the original five-model comparison.  YOLOv9 also
# needs native-only fields such as obj/anchor_t, so those are read from the
# official repository YAML and only the common values are overwritten.
COMMON_HYPERPARAMETERS: dict[str, float] = {
    "lr0": 0.01,
    "lrf": 0.01,
    "momentum": 0.937,
    "weight_decay": 0.0005,
    "warmup_epochs": 3.0,
    "warmup_momentum": 0.8,
    "warmup_bias_lr": 0.1,
    "box": 0.05,
    "cls": 0.5,
    "hsv_h": 0.015,
    "hsv_s": 0.70,
    "hsv_v": 0.40,
    "degrees": 0.0,
    "translate": 0.10,
    "scale": 0.50,
    "shear": 0.0,
    "perspective": 0.0,
    "flipud": 0.0,
    "fliplr": 0.50,
    "mosaic": 1.0,
    "mixup": 0.0,
    "copy_paste": 0.0,
}


def set_seed(seed: int) -> None:
    """Set the seeds used by Python, NumPy, PyTorch and CUDA."""

    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def validate_dataset(dataset_root: Path) -> None:
    """Check the expected YOLO folder layout and annotation format."""

    for split in ("train", "val", "test"):
        images_dir = dataset_root / "images" / split
        labels_dir = dataset_root / "labels" / split
        if not images_dir.is_dir() or not labels_dir.is_dir():
            raise FileNotFoundError(
                f"Expected {images_dir} and {labels_dir}. "
                "The dataset must contain images/{train,val,test} and "
                "labels/{train,val,test}."
            )

        image_paths = [
            path
            for path in images_dir.iterdir()
            if path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        ]
        for image_path in image_paths:
            label_path = labels_dir / f"{image_path.stem}.txt"
            if not label_path.is_file():
                raise ValueError(f"Missing label file for {image_path}: {label_path}")
            # Empty annotation files are valid and represent true negative
            # images.  Do not invent a background bounding box for them.
            for line in label_path.read_text(encoding="utf-8").splitlines():
                values = line.split()
                if len(values) != 5:
                    raise ValueError(f"Invalid YOLO annotation in {label_path}: {line}")
                class_id, *box = values
                if int(class_id) not in range(len(CLASS_NAMES)):
                    raise ValueError(f"Unknown class id in {label_path}: {class_id}")
                if not all(0.0 <= float(value) <= 1.0 for value in box):
                    raise ValueError(f"Box values must be normalised in {label_path}: {line}")


def write_dataset_yaml(dataset_root: Path, output_path: Path) -> Path:
    """Write the dataset definition consumed by YOLOv9."""

    payload = {
        "path": str(dataset_root.resolve()),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "names": {index: name for index, name in enumerate(CLASS_NAMES)},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return output_path


def _find_native_hyp(yolov9_root: Path) -> Path | None:
    candidates = [
        yolov9_root / "hyp.scratch-high.yaml",
        yolov9_root / "data" / "hyps" / "hyp.scratch-high.yaml",
    ]
    candidates.extend(sorted(yolov9_root.rglob("hyp.scratch-high.yaml")))
    return next((path for path in candidates if path.is_file()), None)


def write_hyperparameters(yolov9_root: Path, output_path: Path) -> Path:
    """Preserve YOLOv9 native fields while applying the project recipe."""

    native_path = _find_native_hyp(yolov9_root)
    if native_path is None:
        # This fallback is intentionally complete enough for YOLOv9.  Prefer
        # the official template whenever it is available.
        hyperparameters: dict[str, Any] = {
            "lr0": 0.01,
            "lrf": 0.01,
            "momentum": 0.937,
            "weight_decay": 0.0005,
            "warmup_epochs": 3.0,
            "warmup_momentum": 0.8,
            "warmup_bias_lr": 0.1,
            "box": 0.05,
            "cls": 0.5,
            "obj": 1.0,
            "obj_pw": 1.0,
            "iou_t": 0.20,
            "anchor_t": 4.0,
            "fl_gamma": 0.0,
            "hsv_h": 0.015,
            "hsv_s": 0.70,
            "hsv_v": 0.40,
            "degrees": 0.0,
            "translate": 0.10,
            "scale": 0.50,
            "shear": 0.0,
            "perspective": 0.0,
            "flipud": 0.0,
            "fliplr": 0.50,
            "mosaic": 1.0,
            "mixup": 0.0,
            "copy_paste": 0.0,
        }
    else:
        loaded = yaml.safe_load(native_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"Native hyperparameter file is not a mapping: {native_path}")
        hyperparameters = dict(loaded)

    hyperparameters.update(COMMON_HYPERPARAMETERS)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        yaml.safe_dump(hyperparameters, sort_keys=False), encoding="utf-8"
    )
    return output_path


def patch_torch26_loading(yolov9_root: Path) -> list[str]:
    """Patch only old YOLOv9 load calls that need full checkpoint loading.

    PyTorch 2.6 changed ``torch.load``'s default to ``weights_only=True``.
    YOLOv9 checkpoints contain trusted module metadata, so the official
    loader needs ``weights_only=False``.  This mirrors the narrow compatibility
    patch used by the original Kaggle notebook.
    """

    replacements = {
        "torch.load(weights, map_location='cpu')":
            "torch.load(weights, map_location='cpu', weights_only=False)",
        "torch.load(attempt_download(w), map_location='cpu')":
            "torch.load(attempt_download(w), map_location='cpu', weights_only=False)",
        "torch.load(last, map_location='cpu')":
            "torch.load(last, map_location='cpu', weights_only=False)",
        "torch.load(f, map_location=torch.device('cpu'))":
            "torch.load(f, map_location=torch.device('cpu'), weights_only=False)",
    }
    relative_files = (
        "train_dual.py",
        "models/experimental.py",
        "utils/torch_utils.py",
        "utils/general.py",
    )
    patched_files: list[str] = []
    for relative_path in relative_files:
        source_path = yolov9_root / relative_path
        if not source_path.is_file():
            continue
        source = source_path.read_text(encoding="utf-8")
        patched = source
        for old, new in replacements.items():
            patched = patched.replace(old, new)
        if patched != source:
            source_path.write_text(patched, encoding="utf-8")
            patched_files.append(relative_path)
    return patched_files


def resolve_path(raw: str, *, relative_to: Path | None = None) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    if relative_to is not None and (relative_to / path).exists():
        return (relative_to / path).resolve()
    return path.resolve()


def run_training(args: argparse.Namespace) -> Path:
    dataset_root = resolve_path(args.dataset_root)
    yolov9_root = resolve_path(args.yolov9_root)
    if not yolov9_root.is_dir():
        raise FileNotFoundError(f"YOLOv9 repository not found: {yolov9_root}")
    train_script = yolov9_root / "train_dual.py"
    if not train_script.is_file():
        raise FileNotFoundError(f"YOLOv9 train_dual.py not found: {train_script}")

    validate_dataset(dataset_root)
    set_seed(args.seed)

    output_root = resolve_path(args.output_dir)
    data_yaml = write_dataset_yaml(dataset_root, output_root / "dangerous_items.yaml")
    hyp_yaml = write_hyperparameters(yolov9_root, output_root / "yolov9_project_hyp.yaml")

    if not args.skip_compat_patch:
        patched_files = patch_torch26_loading(yolov9_root)
        if patched_files:
            print("Applied PyTorch checkpoint compatibility patch:", ", ".join(patched_files))

    pretrained = resolve_path(args.pretrained, relative_to=yolov9_root)
    if not pretrained.is_file():
        raise FileNotFoundError(
            f"YOLOv9 pretrained checkpoint not found: {pretrained}. "
            "Pass --pretrained with a valid .pt file."
        )
    cfg = resolve_path(args.cfg, relative_to=yolov9_root)
    if not cfg.is_file():
        raise FileNotFoundError(f"YOLOv9 model config not found: {cfg}")

    run_root = output_root / "runs"
    run_root.mkdir(parents=True, exist_ok=True)
    log_path = output_root / "training.log"
    command = [
        sys.executable,
        str(train_script.name),
        "--weights", str(pretrained),
        "--cfg", str(cfg),
        "--data", str(data_yaml),
        "--hyp", str(hyp_yaml),
        "--epochs", str(args.epochs),
        "--batch", str(args.batch_size),
        "--img", str(args.image_size),
        "--device", str(args.device),
        "--project", str(run_root),
        "--name", args.name,
        "--exist-ok",
        "--close-mosaic", "10",
        "--patience", str(args.patience),
        "--seed", str(args.seed),
    ]

    print("Starting YOLOv9-T training:")
    print(" ".join(f'"{item}"' if " " in item else item for item in command))
    started = time.perf_counter()
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        process = subprocess.Popen(
            command,
            cwd=yolov9_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log_file.write(line)
    return_code = process.wait()
    elapsed_minutes = (time.perf_counter() - started) / 60.0
    if return_code != 0:
        raise RuntimeError(
            f"YOLOv9 training failed with exit code {return_code}. "
            f"Read the full log at {log_path}."
        )

    run_dir = run_root / args.name
    print(f"Training completed in {elapsed_minutes:.1f} minutes.")
    print(f"Best checkpoint is normally saved at: {run_dir / 'weights' / 'best.pt'}")
    print(f"Training log: {log_path}")
    return run_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True, help="Root containing images/ and labels/.")
    parser.add_argument("--yolov9-root", required=True, help="Official YOLOv9 repository directory.")
    parser.add_argument("--pretrained", default="yolov9-t-converted.pt", help="COCO-pretrained YOLOv9-T checkpoint.")
    parser.add_argument("--cfg", default="models/detect/yolov9-t.yaml", help="YOLOv9-T model YAML.")
    parser.add_argument("--output-dir", default="model_runs/yolov9_weapon", help="Output directory for YAML, logs and runs.")
    parser.add_argument("--name", default="yolov9_t", help="Training run name.")
    parser.add_argument("--device", default="0", help="CUDA device such as 0, or 'cpu'.")
    parser.add_argument("--image-size", type=int, default=640)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--skip-compat-patch", action="store_true", help="Do not patch old torch.load calls.")
    return parser


if __name__ == "__main__":
    run_training(build_parser().parse_args())
