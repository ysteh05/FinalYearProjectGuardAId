"""Build the isolated two-class dataset used by Mall-negative fine-tuning.

The existing curated full-dataset V1 cache is treated as immutable. Mall frames
are copied into a separate cache with empty YOLO labels, grouped into contiguous
frame blocks before deterministic 80/10/10 assignment to limit adjacent-frame
leakage. This module prepares data only; it never launches training.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import random
import shutil
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageOps

from yolov9_weapon_workspace import MALL_FINETUNE_OUTPUT_ROOT, PROJECT_ROOT


PROFILE_ID = "mall_negative_finetune_v1"
MALL_DATA_ROOT = PROJECT_ROOT / "Weapon Detection Model Dataset" / "mall_dataset" / "mall_dataset"
MALL_FRAMES_ROOT = MALL_DATA_ROOT / "frames"
EXPECTED_MALL_IMAGES = 2000
MALL_SPLIT_SEED = 2026
MALL_BLOCK_SIZE = 20
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
MANIFEST_FIELDS = (
    "source", "source_split", "split", "source_image", "source_label", "image", "label",
    "image_sha256", "pixel_sha256", "label_sha256", "image_width", "image_height",
    "exif_orientation", "link_mode", "classes", "box_count", "negative",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pixel_info(path: Path) -> tuple[str, tuple[int, int], int]:
    with Image.open(path) as opened:
        orientation = int(opened.getexif().get(274, 1) or 1)
        image = ImageOps.exif_transpose(opened).convert("RGB")
        digest = hashlib.sha256()
        digest.update(f"{image.width}x{image.height}|RGB|".encode("ascii"))
        digest.update(image.tobytes())
        return digest.hexdigest(), image.size, orientation


def _ordered_frames(frames: list[Path]) -> list[Path]:
    def sequence_key(path: Path) -> tuple[int, str]:
        digits = "".join(character for character in path.stem if character.isdigit())
        return (int(digits) if digits else 2**63 - 1, path.name.casefold())

    return sorted(frames, key=sequence_key)


def assign_mall_splits(
    frames: list[Path],
    *,
    expected_count: int | None = EXPECTED_MALL_IMAGES,
    block_size: int = MALL_BLOCK_SIZE,
    seed: int = MALL_SPLIT_SEED,
) -> dict[Path, str]:
    """Assign contiguous frame groups to exact 80/10/10 image counts."""
    ordered = _ordered_frames(frames)
    if expected_count is not None and len(ordered) != expected_count:
        raise ValueError(f"Mall frames: expected {expected_count} images, found {len(ordered)}")
    if not ordered or block_size < 1 or len(ordered) % block_size:
        raise ValueError("Mall frame count must be a non-empty multiple of the temporal block size")
    groups = [ordered[index:index + block_size] for index in range(0, len(ordered), block_size)]
    if len(groups) % 10:
        raise ValueError("Temporal block count must be divisible by 10 for an exact 80/10/10 split")
    random.Random(seed).shuffle(groups)
    train_groups = len(groups) * 8 // 10
    val_groups = len(groups) // 10
    assigned: dict[Path, str] = {}
    for index, group in enumerate(groups):
        split = "train" if index < train_groups else "val" if index < train_groups + val_groups else "test"
        for frame in group:
            assigned[frame] = split
    counts = Counter(assigned.values())
    expected = {"train": len(ordered) * 8 // 10, "val": len(ordered) // 10, "test": len(ordered) // 10}
    if dict(counts) != expected:
        raise AssertionError(f"Unexpected Mall split counts: {dict(counts)} != {expected}")
    return assigned


def inspect_mall_source(frames_root: Path = MALL_FRAMES_ROOT) -> dict[str, Any]:
    if not frames_root.is_dir():
        raise FileNotFoundError(f"Mall frames folder not found: {frames_root}")
    frames = _ordered_frames([path for path in frames_root.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES])
    if len(frames) != EXPECTED_MALL_IMAGES:
        raise ValueError(f"Expected exactly {EXPECTED_MALL_IMAGES} Mall images, found {len(frames)}")
    return {
        "ready": True,
        "frames_root": str(frames_root.resolve()),
        "images": len(frames),
        "split_counts": {"train": 1600, "val": 200, "test": 200},
        "split_seed": MALL_SPLIT_SEED,
        "temporal_block_size": MALL_BLOCK_SIZE,
        "negative_ground_truth": "No weapon-specific labels are provided by this crowd-counting dataset; negative status must be confirmed by the operator.",
    }


def _progress(callback: Callable[[dict[str, Any]], None] | None, stage: str, completed: int, total: int, message: str) -> None:
    if callback:
        callback({"stage": stage, "completed": completed, "total": total, "message": message})


def build_finetune_dataset(
    *,
    output_root: Path = MALL_FINETUNE_OUTPUT_ROOT,
    base_output_root: Path | None = None,
    frames_root: Path = MALL_FRAMES_ROOT,
    progress: Callable[[dict[str, Any]], None] | None = None,
    expected_mall_images: int | None = EXPECTED_MALL_IMAGES,
    block_size: int = MALL_BLOCK_SIZE,
    seed: int = MALL_SPLIT_SEED,
    base_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an immutable merged cache: prior V1 data plus Mall negatives."""
    if base_report is None:
        from yolov9_full_dataset_v1 import build_dataset

        base_report = build_dataset(output_root=base_output_root, progress=progress)
    base_root = Path(base_report["cache_root"]).resolve()
    manifest_path = Path(base_report["manifest"]).resolve()
    if not base_root.is_dir() or not manifest_path.is_file():
        raise FileNotFoundError("The audited full-dataset V1 cache is unavailable")

    if not frames_root.is_dir():
        raise FileNotFoundError(f"Mall frames folder not found: {frames_root}")
    frames = _ordered_frames([path for path in frames_root.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES])
    if expected_mall_images is not None and len(frames) != expected_mall_images:
        raise ValueError(f"Expected exactly {expected_mall_images} Mall images, found {len(frames)}")
    splits = assign_mall_splits(frames, expected_count=expected_mall_images, block_size=block_size, seed=seed)

    inventory: list[dict[str, Any]] = []
    frame_metadata: dict[Path, dict[str, Any]] = {}
    for index, frame in enumerate(frames, start=1):
        pixel_hash, size, orientation = _pixel_info(frame)
        file_hash = _sha256_file(frame)
        stat = frame.stat()
        frame_metadata[frame] = {"image_sha256": file_hash, "pixel_sha256": pixel_hash, "size": size, "orientation": orientation}
        inventory.append({"path": str(frame.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "image_sha256": file_hash, "pixel_sha256": pixel_hash, "split": splits[frame]})
        if index % 200 == 0 or index == len(frames):
            _progress(progress, "mall_dataset_audit", index, len(frames), f"检查 Mall 负样本 {index}/{len(frames)}")

    fingerprint_hash = hashlib.sha256()
    fingerprint_hash.update(f"{PROFILE_ID}|base={base_report['fingerprint']}|seed={seed}|block={block_size}|ratio=80-10-10\n".encode("utf-8"))
    for item in inventory:
        fingerprint_hash.update(json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        fingerprint_hash.update(b"\n")
    fingerprint = fingerprint_hash.hexdigest()[:24]
    output_root = output_root.resolve()
    cache_root = output_root / "datasets" / fingerprint
    report_file = cache_root / "audit_report.json"
    if report_file.is_file() and (cache_root / "data.yaml").is_file() and (cache_root / "manifest.csv").is_file():
        return json.loads(report_file.read_text(encoding="utf-8"))

    output_root.mkdir(parents=True, exist_ok=True)
    staging = cache_root.with_name(f".{cache_root.name}.staging-{uuid.uuid4().hex}")
    rows: list[dict[str, Any]] = []
    try:
        for split in ("train", "val", "test"):
            (staging / "images" / split).mkdir(parents=True, exist_ok=True)
            (staging / "labels" / split).mkdir(parents=True, exist_ok=True)

        with manifest_path.open("r", encoding="utf-8-sig", newline="") as stream:
            base_rows = list(csv.DictReader(stream))
        if not base_rows:
            raise ValueError("The audited full-dataset V1 manifest is empty")
        for index, row in enumerate(base_rows, start=1):
            split = row.get("split", "")
            if split not in {"train", "val", "test"}:
                raise ValueError(f"Invalid base dataset split: {split!r}")
            source_image = Path(row["image"]).resolve()
            source_label = Path(row["label"]).resolve()
            if not source_image.is_relative_to(base_root) or not source_label.is_relative_to(base_root):
                raise ValueError("Base manifest contains a file outside its audited cache")
            if not source_image.is_file() or not source_label.is_file():
                raise FileNotFoundError(f"Base dataset file is missing: {source_image if not source_image.is_file() else source_label}")
            target_image = staging / "images" / split / source_image.name
            target_label = staging / "labels" / split / source_label.name
            shutil.copy2(source_image, target_image)
            shutil.copy2(source_label, target_label)
            new_row = {key: row.get(key, "") for key in MANIFEST_FIELDS}
            new_row["image"] = str((cache_root / "images" / split / target_image.name).resolve())
            new_row["label"] = str((cache_root / "labels" / split / target_label.name).resolve())
            rows.append(new_row)
            if index % 500 == 0 or index == len(base_rows):
                _progress(progress, "mall_dataset_build", index, len(base_rows) + len(frames), f"复制既有已审计样本 {index}/{len(base_rows)}")

        for index, frame in enumerate(frames, start=1):
            split = splits[frame]
            ext = frame.suffix.lower()
            image_name = f"mall_{frame.stem}{ext}"
            label_name = f"mall_{frame.stem}.txt"
            target_image = staging / "images" / split / image_name
            target_label = staging / "labels" / split / label_name
            shutil.copy2(frame, target_image)
            target_label.write_text("", encoding="utf-8")
            meta = frame_metadata[frame]
            label_hash = hashlib.sha256(b"").hexdigest()
            width, height = meta["size"]
            rows.append({
                "source": "mall_negative",
                "source_split": "sequence",
                "split": split,
                "source_image": str(frame.resolve()),
                "source_label": "(empty YOLO label created; Mall has no weapon annotations)",
                "image": str((cache_root / "images" / split / image_name).resolve()),
                "label": str((cache_root / "labels" / split / label_name).resolve()),
                "image_sha256": meta["image_sha256"],
                "pixel_sha256": meta["pixel_sha256"],
                "label_sha256": label_hash,
                "image_width": width,
                "image_height": height,
                "exif_orientation": meta["orientation"],
                "link_mode": "copy_with_empty_label",
                "classes": "",
                "box_count": 0,
                "negative": True,
            })
            if index % 200 == 0 or index == len(frames):
                _progress(progress, "mall_dataset_build", len(base_rows) + index, len(base_rows) + len(frames), f"复制 Mall 负样本 {index}/{len(frames)}")

        data_yaml = staging / "data.yaml"
        data_yaml.write_text(
            f"path: {json.dumps(str(cache_root), ensure_ascii=False)}\n"
            "train: images/train\nval: images/val\ntest: images/test\n"
            "names:\n  0: knife\n  1: gun\n",
            encoding="utf-8",
        )
        with (staging / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        inventory_payload = {
            "profile": PROFILE_ID,
            "base_dataset_fingerprint": base_report["fingerprint"],
            "mall_split_seed": seed,
            "mall_temporal_block_size": block_size,
            "mall_frames": inventory,
        }
        (staging / "source_inventory.json").write_text(json.dumps(inventory_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        excluded_path = staging / "excluded_samples.csv"
        base_excluded = Path(base_report.get("excluded_csv", ""))
        if base_excluded.is_file():
            shutil.copy2(base_excluded, excluded_path)
        else:
            excluded_path.write_text("source_sample,reason\n", encoding="utf-8-sig")

        mall_counts = Counter(splits.values())
        base_counts = base_report.get("images", {})
        split_counts = {split: int(base_counts.get(split, 0)) + int(mall_counts.get(split, 0)) for split in ("train", "val", "test")}
        report = {
            "profile": PROFILE_ID,
            "policy_version": "mall-negative-80-10-10-temporal-blocks-v1",
            "fingerprint": fingerprint,
            "base_dataset_fingerprint": base_report["fingerprint"],
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "cache_root": str(cache_root.resolve()),
            "data_yaml": str((cache_root / "data.yaml").resolve()),
            "manifest": str((cache_root / "manifest.csv").resolve()),
            "excluded_csv": str((cache_root / "excluded_samples.csv").resolve()),
            "source_inventory": str((cache_root / "source_inventory.json").resolve()),
            "class_names": ["knife", "gun"],
            "class_labels": ["刀", "枪"],
            "images": split_counts,
            "total_images": len(rows),
            "class_counts": base_report.get("class_counts", {}),
            "selected_sources": sorted(set(base_report.get("selected_sources", [])) | {"mall_negative"}),
            "source_split_rules": {
                "base_full_dataset_v1": "Reuse the immutable, audited full-dataset V1 image/label splits unchanged.",
                "mall_negative": f"{len(frames)} Mall frames; contiguous groups of {block_size} frames are deterministically assigned 80/10/10 with seed {seed} to reduce adjacent-frame leakage.",
            },
            "mall_negative_assumption": "Mall is a crowd-counting dataset and supplies no weapon-specific ground truth. Its frames are treated as negative only under operator confirmation.",
            "mall_split": {
                "images": dict(mall_counts),
                "ratios": {"train": 0.8, "val": 0.1, "test": 0.1},
                "seed": seed,
                "temporal_block_size": block_size,
                "ground_truth": "empty weapon labels supplied by the negative-dataset workflow; not weapon-annotated source labels",
            },
            "base_dataset_report": str((base_root / "audit_report.json").resolve()),
            "v11_other_samples_dropped": base_report.get("v11_other_samples_dropped", 0),
            "excluded_samples": base_report.get("excluded_samples", 0),
            "preview_samples": [],
        }
        (staging / "audit_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        cache_root.parent.mkdir(parents=True, exist_ok=True)
        if cache_root.exists():
            shutil.rmtree(staging)
            return json.loads((cache_root / "audit_report.json").read_text(encoding="utf-8"))
        os.replace(staging, cache_root)
        return report
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
