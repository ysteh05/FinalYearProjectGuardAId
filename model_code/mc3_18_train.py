"""Train the GuardAId MC3-18 violence classifier on RWF-2000.

Expected dataset layout::

    RWF-2000/
      train/Fight/*.avi
      train/NonFight/*.avi
      val/Fight/*.avi
      val/NonFight/*.avi
      test/Fight/*.avi
      test/NonFight/*.avi

The script follows the project's locked protocol: uniformly sample 20 frames,
resize the short side to 128, use a 112x112 crop, normalise with the dataset
mean/std, replace MC3-18's final layer with two outputs, and select the best
checkpoint using validation ROC-AUC with patience-based early stopping.

Example::

    python model_code/mc3_18_train.py `
        --dataset-root "D:/datasets/RWF-2000" `
        --output-dir checkpoints_rwf2000 `
        --cache-dir rwf2000_cache_20f_112
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import random
import time
from functools import partial
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import (
    balanced_accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from torch.utils.data import DataLoader, Dataset
from torchvision.models.video import MC3_18_Weights, mc3_18


NUM_FRAMES = 20
SHORT_SIDE = 128
CROP_SIZE = 112
NUM_CLASSES = 2
CLASS_TO_LABEL = {"NonFight": 0, "Fight": 1}
LABEL_TO_CLASS = {value: key for key, value in CLASS_TO_LABEL.items()}
MEAN = np.array([0.43216, 0.394666, 0.37645], dtype=np.float32)
STD = np.array([0.22803, 0.22145, 0.216989], dtype=np.float32)


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def build_manifest(dataset_root: Path) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for split in ("train", "val", "test"):
        for class_name, label in CLASS_TO_LABEL.items():
            class_dir = dataset_root / split / class_name
            if not class_dir.is_dir():
                raise FileNotFoundError(f"Missing RWF-2000 directory: {class_dir}")
            for video_path in sorted(class_dir.glob("*.avi")):
                records.append(
                    {
                        "split": split,
                        "class_name": class_name,
                        "label": label,
                        "video_path": str(video_path.resolve()),
                        "video_name": video_path.name,
                    }
                )
    if not records:
        raise ValueError(f"No .avi videos found below {dataset_root}")
    return pd.DataFrame(records)


def stable_rng_from_key(key: str) -> np.random.Generator:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    seed = int(digest[:16], 16) % (2**32)
    return np.random.default_rng(seed)


def sample_frame_indices(frame_count: int, num_frames: int) -> np.ndarray:
    frame_count = max(int(frame_count), 1)
    indices = np.linspace(0, frame_count - 1, num=num_frames)
    return np.clip(np.round(indices).astype(np.int64), 0, frame_count - 1)


def load_uniform_frames(video_path: Path, num_frames: int) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Unable to open video: {video_path}")
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    frames: list[np.ndarray] = []
    last_frame: np.ndarray | None = None
    try:
        for frame_index in sample_frame_indices(frame_count, num_frames):
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
            ok, frame = capture.read()
            if not ok:
                if last_frame is None:
                    raise RuntimeError(f"Unable to read frame {frame_index} from {video_path}")
                frame = last_frame.copy()
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frames.append(frame)
            last_frame = frame
    finally:
        capture.release()
    return frames


def resize_short_side(frame: np.ndarray, short_side: int = SHORT_SIDE) -> np.ndarray:
    height, width = frame.shape[:2]
    if height <= width:
        new_height = short_side
        new_width = int(round(width * short_side / height))
    else:
        new_width = short_side
        new_height = int(round(height * short_side / width))
    return cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_LINEAR)


def crop_coordinates(
    height: int,
    width: int,
    crop_size: int,
    *,
    rng: np.random.Generator | None = None,
    center: bool,
) -> tuple[int, int]:
    if height < crop_size or width < crop_size:
        raise ValueError(f"Crop size {crop_size} is larger than frame {(height, width)}")
    if center:
        return (height - crop_size) // 2, (width - crop_size) // 2
    if rng is None:
        raise ValueError("rng is required for a non-centre crop")
    top = int(rng.integers(0, height - crop_size + 1))
    left = int(rng.integers(0, width - crop_size + 1))
    return top, left


def preprocess_video(video_path: Path, split: str) -> torch.Tensor:
    """Return a float tensor in C,T,H,W format for torchvision video models."""

    frames = load_uniform_frames(video_path, NUM_FRAMES)
    resized = [resize_short_side(frame) for frame in frames]
    height, width = resized[0].shape[:2]
    if split == "train":
        # The original experiment used a stable per-video RNG.  This makes a
        # cache reproducible while still adding random crop and flip variation
        # across different videos.
        rng = stable_rng_from_key(f"{split}:{video_path.as_posix()}")
        top, left = crop_coordinates(height, width, CROP_SIZE, rng=rng, center=False)
        flip = bool(rng.random() < 0.5)
    else:
        top, left = crop_coordinates(height, width, CROP_SIZE, center=True)
        flip = False

    processed: list[np.ndarray] = []
    for frame in resized:
        crop = frame[top : top + CROP_SIZE, left : left + CROP_SIZE]
        if flip:
            crop = np.ascontiguousarray(crop[:, ::-1])
        processed.append(crop)

    clip = np.stack(processed).astype(np.float32) / 255.0
    clip = (clip - MEAN) / STD
    clip = np.transpose(clip, (3, 0, 1, 2))
    return torch.from_numpy(clip).to(torch.float32)


def cache_manifest(manifest: pd.DataFrame, cache_root: Path, index_path: Path, rebuild: bool) -> pd.DataFrame:
    cached_records: list[dict[str, object]] = []
    for row in manifest.to_dict("records"):
        video_path = Path(str(row["video_path"]))
        cache_path = cache_root / str(row["split"]) / str(row["class_name"]) / f"{video_path.stem}.pt"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if rebuild or not cache_path.exists():
            clip_tensor = preprocess_video(video_path, str(row["split"]))
            torch.save({"clip": clip_tensor.half(), "label": int(row["label"])}, cache_path)
        cached_records.append({**row, "cache_path": str(cache_path.resolve())})
    cached = pd.DataFrame(cached_records)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    cached.to_csv(index_path, index=False)
    return cached


class CachedClipDataset(Dataset):
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame.reset_index(drop=True).copy()

    def __len__(self) -> int:
        return len(self.frame)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int]:
        row = self.frame.iloc[index]
        try:
            payload = torch.load(str(row["cache_path"]), map_location="cpu", weights_only=True)
        except TypeError:  # Compatibility with older PyTorch versions.
            payload = torch.load(str(row["cache_path"]), map_location="cpu")
        return payload["clip"].float(), int(payload["label"])


def worker_init(worker_id: int, seed: int) -> None:
    worker_seed = seed + worker_id
    np.random.seed(worker_seed)
    random.seed(worker_seed)


def build_loaders(
    cached_manifest: pd.DataFrame,
    batch_size: int,
    num_workers: int,
    seed: int,
    device: torch.device,
) -> tuple[DataLoader, DataLoader]:
    train_frame = cached_manifest[cached_manifest["split"] == "train"]
    val_frame = cached_manifest[cached_manifest["split"] == "val"]
    generator = torch.Generator()
    generator.manual_seed(seed)
    train_loader = DataLoader(
        CachedClipDataset(train_frame),
        batch_size=batch_size,
        shuffle=True,
        num_workers=num_workers,
        pin_memory=device.type == "cuda",
        worker_init_fn=partial(worker_init, seed=seed),
        generator=generator,
    )
    val_loader = DataLoader(
        CachedClipDataset(val_frame),
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=device.type == "cuda",
    )
    return train_loader, val_loader


def build_model(pretrained: bool, device: torch.device) -> nn.Module:
    weights = MC3_18_Weights.DEFAULT if pretrained else None
    model = mc3_18(weights=weights)
    model.fc = nn.Linear(model.fc.in_features, NUM_CLASSES)
    return model.to(device)


def positive_prob(logits: torch.Tensor) -> torch.Tensor:
    return torch.softmax(logits, dim=1)[:, 1]


def compute_metrics(labels: np.ndarray, probabilities: np.ndarray, threshold: float = 0.5) -> dict[str, float]:
    predictions = (probabilities >= threshold).astype(int)
    return {
        "roc_auc": float(roc_auc_score(labels, probabilities)),
        "precision": float(precision_score(labels, predictions, zero_division=0)),
        "recall": float(recall_score(labels, predictions, zero_division=0)),
        "f1": float(f1_score(labels, predictions, zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(labels, predictions)),
    }


def run_epoch(
    model: nn.Module,
    loader: DataLoader,
    criterion: nn.Module,
    device: torch.device,
    optimizer: torch.optim.Optimizer | None,
) -> tuple[dict[str, float], np.ndarray, np.ndarray]:
    training = optimizer is not None
    model.train(training)
    total_loss = 0.0
    labels: list[np.ndarray] = []
    probabilities: list[np.ndarray] = []
    with torch.set_grad_enabled(training):
        for clips, batch_labels in loader:
            clips = clips.to(device, non_blocking=True)
            batch_labels = batch_labels.to(device, non_blocking=True)
            logits = model(clips)
            loss = criterion(logits, batch_labels)
            if training:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
            total_loss += loss.item() * clips.size(0)
            labels.append(batch_labels.detach().cpu().numpy())
            probabilities.append(positive_prob(logits).detach().cpu().numpy())
    all_labels = np.concatenate(labels)
    all_probabilities = np.concatenate(probabilities)
    metrics = compute_metrics(all_labels, all_probabilities)
    metrics["loss"] = total_loss / len(loader.dataset)
    return metrics, all_labels, all_probabilities


def measure_speed(model: nn.Module, loader: DataLoader, device: torch.device) -> dict[str, float]:
    model.eval()
    elapsed = 0.0
    clip_count = 0
    with torch.inference_mode():
        for batch_index, (clips, _) in enumerate(loader):
            if batch_index >= 25:
                break
            clips = clips.to(device, non_blocking=True)
            if device.type == "cuda":
                torch.cuda.synchronize()
            started = time.perf_counter()
            model(clips)
            if device.type == "cuda":
                torch.cuda.synchronize()
            if batch_index >= 5:
                elapsed += time.perf_counter() - started
                clip_count += clips.size(0)
    if clip_count == 0 or elapsed <= 0:
        return {"clips_per_second": 0.0, "seconds_per_clip": 0.0}
    return {
        "clips_per_second": clip_count / elapsed,
        "seconds_per_clip": elapsed / clip_count,
    }


def train(args: argparse.Namespace) -> Path:
    set_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")
    dataset_root = Path(args.dataset_root).expanduser().resolve()
    manifest = build_manifest(dataset_root)
    cache_root = Path(args.cache_dir).expanduser().resolve()
    output_root = Path(args.output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    cache_index = cache_root / "cache_index.csv"
    if cache_index.is_file() and not args.rebuild_cache:
        cached_manifest = pd.read_csv(cache_index)
    else:
        cached_manifest = cache_manifest(manifest, cache_root, cache_index, args.rebuild_cache)
    # Fail early if a cache index came from another dataset or was incomplete.
    same_videos = (
        "video_path" in cached_manifest.columns
        and set(cached_manifest["video_path"].astype(str)) == set(manifest["video_path"].astype(str))
    )
    if (
        not same_videos
        or len(cached_manifest) != len(manifest)
        or "cache_path" not in cached_manifest.columns
        or not cached_manifest["cache_path"].map(lambda value: Path(value).is_file()).all()
    ):
        cached_manifest = cache_manifest(manifest, cache_root, cache_index, rebuild=True)

    summary = cached_manifest.groupby(["split", "class_name"]).size().unstack(fill_value=0)
    print("Dataset summary:\n", summary)
    print("Device:", device)
    train_loader, val_loader = build_loaders(cached_manifest, args.batch_size, args.num_workers, args.seed, device)
    model = build_model(pretrained=not args.no_pretrained, device=device)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="max", factor=0.5, patience=2)

    history: list[dict[str, float | int]] = []
    best_auc = -float("inf")
    best_epoch = -1
    epochs_without_improvement = 0
    best_state: dict[str, torch.Tensor] | None = None

    for epoch in range(1, args.epochs + 1):
        train_metrics, _, _ = run_epoch(model, train_loader, criterion, device, optimizer)
        val_metrics, _, _ = run_epoch(model, val_loader, criterion, device, optimizer=None)
        scheduler.step(val_metrics["roc_auc"])
        row: dict[str, float | int] = {"epoch": epoch}
        row.update({f"train_{key}": value for key, value in train_metrics.items()})
        row.update({f"val_{key}": value for key, value in val_metrics.items()})
        history.append(row)
        print(row)
        if val_metrics["roc_auc"] > best_auc + args.min_delta:
            best_auc = val_metrics["roc_auc"]
            best_epoch = epoch
            epochs_without_improvement = 0
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        else:
            epochs_without_improvement += 1
        if epochs_without_improvement >= args.patience:
            print(f"Early stopping at epoch {epoch}.")
            break

    if best_state is None:
        raise RuntimeError("No validation checkpoint was selected.")
    model.load_state_dict(best_state)
    checkpoint_path = output_root / "mc3_18_best.pt"
    torch.save({"model_name": "mc3_18", "state_dict": model.state_dict()}, checkpoint_path)
    val_metrics, val_labels, val_probabilities = run_epoch(model, val_loader, criterion, device, optimizer=None)
    speed = measure_speed(model, val_loader, device)
    pd.DataFrame(history).to_csv(output_root / "mc3_18_history.csv", index=False)
    (output_root / "mc3_18_val_probs.json").write_text(
        json.dumps({"labels": val_labels.tolist(), "probs": val_probabilities.tolist()}, indent=2),
        encoding="utf-8",
    )
    result = {
        "model_name": "mc3_18",
        "best_epoch": best_epoch,
        "val": val_metrics,
        "speed": speed,
        "total_params": sum(parameter.numel() for parameter in model.parameters()),
        "checkpoint_path": str(checkpoint_path),
        "pretrained_backbone": not args.no_pretrained,
    }
    (output_root / "mc3_18_training_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    del model, train_loader, val_loader, optimizer, scheduler, criterion
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return checkpoint_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--output-dir", default="checkpoints_rwf2000")
    parser.add_argument("--cache-dir", default="rwf2000_cache_20f_112")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--num-workers", type=int, default=2)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--patience", type=int, default=5)
    parser.add_argument("--min-delta", type=float, default=0.002)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-pretrained", action="store_true", help="Train without torchvision pretrained weights.")
    parser.add_argument("--cpu", action="store_true", help="Force CPU training.")
    parser.add_argument("--rebuild-cache", action="store_true")
    return parser


if __name__ == "__main__":
    # Required for Windows DataLoader worker processes.
    import multiprocessing

    multiprocessing.freeze_support()
    train(build_parser().parse_args())
