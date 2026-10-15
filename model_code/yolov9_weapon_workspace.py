"""Dataset preparation and audit helpers for the local YOLOv9 weapon workspace.

The source datasets are treated as immutable. Mapped labels and any filtered
image copies are written only to a fingerprinted cache under model_runs.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from PIL import Image, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "Weapon Detection Model Dataset"
OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "yolov9_weapon_workspace"
MALL_FINETUNE_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "yolov9_mall_negative_finetune"
HARD_NEGATIVE_FINETUNE_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "yolov9_brev_hard_negative_finetune"
YOLO_ROOT = PROJECT_ROOT / "results (4)" / "yolov9"
PRETRAINED_PATH = YOLO_ROOT / "yolov9-t-converted.pt"
MODEL_CONFIG_PATH = YOLO_ROOT / "models" / "detect" / "yolov9-t.yaml"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
CLASS_NAMES = ("parang_machete", "knife", "baseball_bat", "long_gun", "gun")
CLASS_LABELS = ("砍刀／大刀", "刀", "棒球棒", "长枪", "手枪")
LEGACY_DATASET_SOURCE_IDS = ("dangerous_items", "shanghaitech_negative", "long_gun_dataset", "knife_dataset")
DATASET_SOURCE_IDS = (*LEGACY_DATASET_SOURCE_IDS, "sohas_weapon")
SUPPLEMENTARY_SPLIT_SEED = 2026
SUPPLEMENTARY_SPLIT_RATIOS = (("train", 0.70), ("val", 0.15), ("test", 0.15))
IMAGE_ROOTS = (
    ("Dangerous Items", DATA_ROOT / "Dangerous Items"),
    ("Dataset", DATA_ROOT / "Dataset"),
    ("Knife Dataset", DATA_ROOT / "Knife_Dataset" / "Knife_Dataset"),
    ("SOHAS Weapon Detection", DATA_ROOT / "Sohas_weapon-Detection"),
)


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value.resolve())
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(v) for v in value]
    if isinstance(value, set):
        return sorted(json_safe(v) for v in value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        # On Windows, a dashboard request may briefly hold the destination
        # open while reading progress.json. Retry transient sharing violations
        # so an otherwise healthy training callback is not terminated.
        for attempt in range(50):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.05)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pixel_fingerprint(path: Path) -> tuple[str, tuple[int, int]]:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        size = image.size
        digest = hashlib.sha256()
        digest.update(f"{size[0]}x{size[1]}|RGB|".encode("ascii"))
        digest.update(image.tobytes())
        return digest.hexdigest(), size


def _iter_images(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Image directory not found: {directory}")
    return sorted(
        (p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES),
        key=lambda p: p.name.casefold(),
    )


def _sohas_sequence_group_key(image_name: str) -> str:
    """Group numbered video frames by their shared sequence prefix.

    SOHAS also contains independent banknote stills named ``billete_####``;
    those are individual images, not one 219-frame video, so keep each in its
    own group. Other numbered stems in trainval are source/video sequences.
    """
    stem = Path(image_name).stem.casefold()
    if re.fullmatch(r"billete_\d+", stem):
        return f"still:{stem}"
    match = re.fullmatch(r"(.+?)(\d+)", stem)
    if match:
        return f"sequence:{match.group(1)}"
    return f"image:{stem}"


def _pick_whole_groups(group_sizes: dict[str, int], target: int, *, stratum: str) -> tuple[set[str], int]:
    """Pick a deterministic whole-group subset no larger than the target."""
    ordered = sorted(
        group_sizes,
        key=lambda key: hashlib.sha256(f"sohas-group-split-v2|2026|{stratum}|{key}".encode("utf-8")).hexdigest(),
    )
    reachable: dict[int, tuple[str, ...]] = {0: ()}
    for key in ordered:
        size = int(group_sizes[key])
        for current, chosen in sorted(reachable.items(), reverse=True):
            total = current + size
            if total <= target and total not in reachable:
                reachable[total] = chosen + (key,)
    selected_count = max(reachable)
    return set(reachable[selected_count]), selected_count


def _prepare_sohas_source_view(output_root: Path) -> dict[str, Any]:
    """Convert SOHAS VOC annotations to knife-only YOLO labels in a separate cache."""
    source_root = DATA_ROOT / "Sohas_weapon-Detection"
    groups = (
        ("trainval", source_root / "images", source_root / "annotations" / "xmls"),
        ("test", source_root / "images_test", source_root / "annotations_test" / "xmls"),
    )
    parsed: list[dict[str, Any]] = []
    orphan_xml_counts: dict[str, int] = {}
    fingerprint = hashlib.sha256(
        b"sohas-voc-knife-only-v2|trainval=grouped-sequence-7/8:1/8|seed=2026|"
        b"billete-stills=individual|test=original\n"
    )

    for source_split, image_dir, xml_dir in groups:
        images = _iter_images(image_dir)
        if not xml_dir.is_dir():
            raise FileNotFoundError(f"SOHAS VOC annotations not found: {xml_dir}")
        xml_paths = sorted(xml_dir.glob("*.xml"), key=lambda path: path.name.casefold())
        xml_by_stem: dict[str, Path] = {}
        for xml_path in xml_paths:
            stem = xml_path.stem.casefold()
            if stem in xml_by_stem:
                raise ValueError(f"SOHAS has duplicate XML annotation stems: {xml_path.stem}")
            xml_by_stem[stem] = xml_path
        image_stems = {image.stem.casefold() for image in images}
        orphan_xml_counts[source_split] = len(set(xml_by_stem) - image_stems)

        for image in images:
            xml_path = xml_by_stem.get(image.stem.casefold())
            if xml_path is None:
                raise ValueError(f"SOHAS image is missing its matching VOC XML: {image}")
            try:
                document = ET.parse(xml_path).getroot()
                with Image.open(image) as opened:
                    image_width, image_height = opened.size
                declared_width = int(document.findtext("size/width", str(image_width)))
                declared_height = int(document.findtext("size/height", str(image_height)))
                if (declared_width, declared_height) != (image_width, image_height):
                    raise ValueError(
                        f"VOC size {declared_width}x{declared_height} does not match image "
                        f"{image_width}x{image_height}"
                    )
                boxes: list[tuple[float, float, float, float]] = []
                categories: Counter[str] = Counter()
                for obj in document.findall("object"):
                    category = (obj.findtext("name") or "").strip().casefold()
                    if not category:
                        raise ValueError("VOC object is missing its category name")
                    categories[category] += 1
                    if category != "knife":
                        continue
                    bounds = obj.find("bndbox")
                    if bounds is None:
                        raise ValueError("knife object is missing its bndbox")
                    xmin, ymin, xmax, ymax = (float(bounds.findtext(k, "nan")) for k in ("xmin", "ymin", "xmax", "ymax"))
                    if not all(math.isfinite(value) for value in (xmin, ymin, xmax, ymax)):
                        raise ValueError("knife bbox contains a non-finite coordinate")
                    if xmin < 0 or ymin < 0 or xmax > image_width or ymax > image_height or xmax <= xmin or ymax <= ymin:
                        raise ValueError(f"knife bbox is outside the image or has invalid area: {(xmin, ymin, xmax, ymax)}")
                    boxes.append(((xmin + xmax) / (2 * image_width), (ymin + ymax) / (2 * image_height), (xmax - xmin) / image_width, (ymax - ymin) / image_height))
            except Exception as exc:
                raise ValueError(f"Invalid SOHAS annotation {xml_path}: {exc}") from exc
            parsed.append({
                "image": image.resolve(),
                "xml": xml_path.resolve(),
                "source_split": source_split,
                "group_key": _sohas_sequence_group_key(image.name),
                "boxes": boxes,
                "categories": categories,
                "image_size": (image_width, image_height),
            })
            fingerprint.update(f"{source_split}|{image.name}|{_sha256_file(xml_path)}\n".encode("utf-8"))

    trainval = [row for row in parsed if row["source_split"] == "trainval"]
    positive = [row for row in trainval if row["boxes"]]
    negative = [row for row in trainval if not row["boxes"]]
    trainval_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trainval:
        trainval_groups[row["group_key"]].append(row)

    positive_group_sizes = {
        key: sum(bool(row["boxes"]) for row in rows)
        for key, rows in trainval_groups.items()
        if any(row["boxes"] for row in rows)
    }
    positive_val_target = int(math.floor(len(positive) / 8 + 0.5))
    positive_val_groups, positive_val_selected = _pick_whole_groups(
        positive_group_sizes, positive_val_target, stratum="positive-sequences"
    )
    negative_only_group_sizes = {
        key: len(rows)
        for key, rows in trainval_groups.items()
        if not any(row["boxes"] for row in rows)
    }
    val_total_target = int(math.floor(len(trainval) / 8 + 0.5))
    selected_positive_rows = sum(len(trainval_groups[key]) for key in positive_val_groups)
    negative_val_target = max(0, val_total_target - selected_positive_rows)
    negative_val_groups, negative_val_selected = _pick_whole_groups(
        negative_only_group_sizes, negative_val_target, stratum="negative-stills-and-sequences"
    )
    validation_group_keys = positive_val_groups | negative_val_groups
    assigned: dict[str, str] = {}
    for row in trainval:
        assigned[row["image"].as_posix()] = "val" if row["group_key"] in validation_group_keys else "train"
    for row in parsed:
        if row["source_split"] == "test":
            assigned[row["image"].as_posix()] = "test"

    trainval_group_splits: dict[str, set[str]] = defaultdict(set)
    all_group_splits: dict[str, set[str]] = defaultdict(set)
    for row in parsed:
        split = assigned[row["image"].as_posix()]
        all_group_splits[row["group_key"]].add(split)
        if row["source_split"] == "trainval":
            trainval_group_splits[row["group_key"]].add(split)
    crossed_trainval_groups = sorted(key for key, splits in trainval_group_splits.items() if len(splits) > 1)
    crossed_test_groups = sorted(key for key, splits in all_group_splits.items() if "test" in splits and len(splits) > 1)
    if crossed_trainval_groups:
        raise ValueError(f"SOHAS trainval sequence groups cross train/val: {crossed_trainval_groups[:10]}")
    if crossed_test_groups:
        raise ValueError(f"SOHAS sequence groups cross trainval/test: {crossed_test_groups[:10]}")
    sequence_split_audit = {
        "grouping_rule": "remove trailing frame digits from numbered source names; billete_<id> stills remain per-image groups",
        "seed": 2026,
        "validation_fraction_target": 0.125,
        "positive_validation_target_images": positive_val_target,
        "positive_validation_selected_images": positive_val_selected,
        "positive_validation_groups": sorted(positive_val_groups),
        "negative_validation_target_images": negative_val_target,
        "negative_validation_selected_images": negative_val_selected,
        "negative_validation_groups": sorted(negative_val_groups),
        "trainval_groups": len(trainval_group_splits),
        "validation_groups": len(validation_group_keys),
        "trainval_cross_split_groups": crossed_trainval_groups,
        "trainval_test_group_overlap": crossed_test_groups,
        "valid": not crossed_trainval_groups and not crossed_test_groups,
    }

    fingerprint_id = fingerprint.hexdigest()[:20]
    view_root = output_root.resolve() / "source_views" / f"sohas_knife_{fingerprint_id}"
    view_report_path = view_root / "source_view_report.json"
    if view_report_path.is_file():
        return json.loads(view_report_path.read_text(encoding="utf-8"))

    staging = view_root.with_name(f".{view_root.name}.staging-{uuid.uuid4().hex}")
    try:
        for split in ("train", "val", "test"):
            (staging / "images" / split).mkdir(parents=True, exist_ok=True)
            (staging / "labels" / split).mkdir(parents=True, exist_ok=True)
        split_stats: dict[str, dict[str, int]] = {
            split: {"images": 0, "positive_images": 0, "negative_images": 0, "knife_boxes": 0}
            for split in ("train", "val", "test")
        }
        conversion_rows = []
        for row in parsed:
            split = assigned[row["image"].as_posix()]
            view_image_path = staging / "images" / split / row["image"].name
            try:
                os.link(row["image"], view_image_path)
            except OSError:
                shutil.copy2(row["image"], view_image_path)
            label_path = staging / "labels" / split / f"{row['image'].stem}.txt"
            label_path.write_text("".join(f"1 {x:.8f} {y:.8f} {w:.8f} {h:.8f}\n" for x, y, w, h in row["boxes"]), encoding="utf-8")
            stat = split_stats[split]
            stat["images"] += 1
            stat["positive_images"] += bool(row["boxes"])
            stat["negative_images"] += not bool(row["boxes"])
            stat["knife_boxes"] += len(row["boxes"])
            conversion_rows.append({
                "source_split": row["source_split"], "split": split,
                "group_key": row["group_key"],
                "image": str(row["image"]), "source_view_image": str((view_root / "images" / split / row["image"].name).resolve()), "source_xml": str(row["xml"]),
                "yolo_label": str((view_root / "labels" / split / label_path.name).resolve()),
                "original_categories": dict(row["categories"]),
                "knife_boxes": len(row["boxes"]), "negative_after_knife_filter": not bool(row["boxes"]),
            })
        conversion_manifest = staging / "conversion_manifest.json"
        atomic_write_json(conversion_manifest, {"rows": conversion_rows})
        source_view_report = {
            "source_root": str(source_root.resolve()),
            "source_view_root": str(view_root.resolve()),
            "fingerprint": fingerprint_id,
            "split_seed": 2026,
            "trainval_policy": "group-aware 7/8 train, 1/8 val; keep numbered video/source sequences whole",
            "test_policy": "keep original SOHAS test split unchanged",
            "trainval_images": len(trainval),
            "trainval_positive_images": len(positive),
            "trainval_negative_images": len(negative),
            "trainval_validation_images": sum(stat["images"] for split, stat in split_stats.items() if split == "val"),
            "trainval_validation_positive_images": split_stats["val"]["positive_images"],
            "trainval_validation_negative_images": split_stats["val"]["negative_images"],
            "split_counts": split_stats,
            "sequence_split_audit": sequence_split_audit,
            "orphan_xml_without_matching_image": orphan_xml_counts,
            "ignored_object_categories": sorted({name for row in parsed for name in row["categories"] if name != "knife"}),
            "conversion_manifest": str((view_root / "conversion_manifest.json").resolve()),
        }
        atomic_write_json(staging / "source_view_report.json", source_view_report)
        view_root.parent.mkdir(parents=True, exist_ok=True)
        if view_root.exists():
            shutil.rmtree(staging)
            return json.loads(view_report_path.read_text(encoding="utf-8"))
        os.replace(staging, view_root)
        return source_view_report
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _source_locations(
    selected_sources: Iterable[str] | None = None,
    output_root: Path | None = None,
    sohas_view_report: dict[str, Any] | None = None,
) -> list[tuple[str, str, Path, Path, Path, int | None]]:
    main = DATA_ROOT / "Dangerous Items"
    long_gun = DATA_ROOT / "Dataset"
    knife = DATA_ROOT / "Knife_Dataset" / "Knife_Dataset"
    selected = set(selected_sources or LEGACY_DATASET_SOURCE_IDS)
    for required in (YOLO_ROOT, PRETRAINED_PATH, MODEL_CONFIG_PATH):
        if not required.exists():
            raise FileNotFoundError(f"Required YOLO workspace path not found: {required}")
    result = []
    if selected & {"dangerous_items", "shanghaitech_negative"}:
        if not main.is_dir():
            raise FileNotFoundError(f"Dangerous Items dataset not found: {main}")
        for split in ("train", "val", "test"):
            result.append(("dangerous_items", split, main / "images" / split, main / "labels" / split, main, None))
    if "long_gun_dataset" in selected:
        if not long_gun.is_dir():
            raise FileNotFoundError(f"Long Gun dataset not found: {long_gun}")
        result.append(("long_gun_dataset", "unsplit", long_gun / "images", long_gun / "labels", long_gun, 3))
    if "knife_dataset" in selected:
        if not knife.is_dir():
            raise FileNotFoundError(f"Knife Dataset not found: {knife}")
        result.append(("knife_dataset", "unsplit", knife / "images", knife / "labels", knife, 1))
    if "sohas_weapon" in selected:
        view_report = sohas_view_report or _prepare_sohas_source_view(output_root or OUTPUT_ROOT)
        view_root = Path(view_report["source_view_root"])
        source_root = DATA_ROOT / "Sohas_weapon-Detection"
        for split, image_dir in (
            ("train", view_root / "images" / "train"),
            ("val", view_root / "images" / "val"),
            ("test", view_root / "images" / "test"),
        ):
            result.append(("sohas_weapon", split, image_dir, view_root / "labels" / split, source_root, 1))
    return result


def _read_yolo_label(path: Path, source: str, aux_class: int | None) -> list[tuple[int, float, float, float, float]]:
    if not path.is_file():
        raise ValueError(f"Missing label file: {path}")
    output = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        raw = raw.strip()
        if not raw:
            continue
        values = raw.split()
        if len(values) != 5:
            raise ValueError(f"{path}:{line_number}: expected 5 values, found {len(values)}")
        try:
            class_value = float(values[0])
            box = tuple(float(value) for value in values[1:])
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: label contains a non-numeric value") from exc
        if not math.isfinite(class_value) or not class_value.is_integer():
            raise ValueError(f"{path}:{line_number}: class id must be a finite integer")
        if not all(math.isfinite(value) for value in box):
            raise ValueError(f"{path}:{line_number}: bbox contains NaN or infinity")
        cls = int(class_value)
        if source in {"dangerous_items", "shanghaitech_negative"}:
            if cls not in range(len(CLASS_NAMES)):
                raise ValueError(f"{path}:{line_number}: unknown weapon class {cls}")
        else:
            if cls == 0:  # person boxes do not belong to the five weapon classes
                continue
            if cls != 1:
                raise ValueError(f"{path}:{line_number}: unexpected auxiliary class id {cls}")
            assert aux_class is not None
            cls = aux_class
        x, y, width, height = box
        eps = 1e-5
        if width <= 0 or height <= 0:
            raise ValueError(f"{path}:{line_number}: bbox width and height must be positive")
        if any(value < -eps or value > 1 + eps for value in box):
            raise ValueError(f"{path}:{line_number}: bbox coordinates must be normalized to [0, 1]")
        if x - width / 2 < -eps or y - height / 2 < -eps or x + width / 2 > 1 + eps or y + height / 2 > 1 + eps:
            raise ValueError(f"{path}:{line_number}: bbox corners extend outside the image")
        output.append((cls, x, y, width, height))
    return output


@dataclass
class Sample:
    source: str
    source_split: str
    split: str
    image: Path
    label: Path
    mapped_boxes: list[tuple[int, float, float, float, float]]
    file_hash: str
    pixel_hash: str
    image_size: tuple[int, int]
    exif_orientation: int
    jpeg_needs_repair: bool

    @property
    def key(self) -> str:
        try:
            relative = self.image.relative_to(DATA_ROOT)
        except ValueError:
            try:
                relative = self.image.relative_to(PROJECT_ROOT)
            except ValueError:
                relative = Path(self.image.name)
        return f"{self.source}:{self.source_split}:{relative.as_posix()}"


def _bbox_iou(a: Iterable[float], b: Iterable[float]) -> float:
    _, ax, ay, aw, ah = a
    _, bx, by, bw, bh = b
    ax1, ay1, ax2, ay2 = ax - aw / 2, ay - ah / 2, ax + aw / 2, ay + ah / 2
    bx1, by1, bx2, by2 = bx - bw / 2, by - bh / 2, bx + bw / 2, by + bh / 2
    inter = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _has_iou_matching(a: list[tuple], b: list[tuple], threshold: float) -> bool:
    """Return whether every same-class box has a one-to-one IoU match."""
    if len(a) != len(b):
        return False
    adjacency = [[j for j, other in enumerate(b) if _bbox_iou(box, other) >= threshold] for box in a]
    matched: dict[int, int] = {}

    def augment(i: int, seen: set[int]) -> bool:
        for j in adjacency[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in matched or augment(matched[j], seen):
                matched[j] = i
                return True
        return False

    return all(augment(i, set()) for i in range(len(a)))


def _annotation_conflict(group: list[Sample], iou_threshold: float = 0.8) -> str | None:
    if len(group) < 2:
        return None
    base = group[0].mapped_boxes
    base_counts = Counter(box[0] for box in base)
    for sample in group[1:]:
        current_counts = Counter(box[0] for box in sample.mapped_boxes)
        if current_counts != base_counts:
            return "class_or_box_count_mismatch"
        for cls in base_counts:
            a = [box for box in base if box[0] == cls]
            b = [box for box in sample.mapped_boxes if box[0] == cls]
            if not _has_iou_matching(a, b, iou_threshold):
                return "bbox_iou_below_0.8"
    return None


def _record_sample(
    source: str,
    source_split: str,
    image: Path,
    labels_dir: Path,
    label_class: int | None,
) -> Sample:
    label = labels_dir / f"{image.stem}.txt"
    boxes = _read_yolo_label(label, source, label_class)
    with Image.open(image) as raw_image:
        orientation = int(raw_image.getexif().get(274, 1) or 1)
        image_format = str(raw_image.format or "").lower()
    with Image.open(image) as verification_image:
        verification_image.verify()
    jpeg_needs_repair = False
    if image_format in {"jpg", "jpeg"}:
        try:
            with image.open("rb") as stream:
                stream.seek(-2, os.SEEK_END)
                jpeg_needs_repair = stream.read() != b"\xff\xd9"
        except OSError:
            jpeg_needs_repair = True
    pixel_hash, size = _pixel_fingerprint(image)
    source_split = source_split.lower()
    # Flat supplementary datasets are assigned to stable split strata after audit.
    target_split = source_split if source in {"dangerous_items", "shanghaitech_negative", "sohas_weapon"} else "train"
    return Sample(source, source_split, target_split, image.resolve(), label.resolve(), boxes, _sha256_file(image), pixel_hash, size, orientation, jpeg_needs_repair)


def _largest_remainder_counts(total: int) -> dict[str, int]:
    exact = {split: total * ratio for split, ratio in SUPPLEMENTARY_SPLIT_RATIOS}
    counts = {split: math.floor(value) for split, value in exact.items()}
    remainder = total - sum(counts.values())
    order = sorted(
        (split for split, _ in SUPPLEMENTARY_SPLIT_RATIOS),
        key=lambda split: (-(exact[split] - counts[split]), ["train", "val", "test"].index(split)),
    )
    for split in order[:remainder]:
        counts[split] += 1
    return counts


def _assign_supplementary_splits(
    samples: list[Sample], train_only_sources: Iterable[str] = ()
) -> dict[str, dict[str, Any]]:
    """Deterministically stratify flat auxiliary datasets by positive/empty label."""
    summaries: dict[str, dict[str, Any]] = {}
    train_only = set(train_only_sources)
    for source in ("long_gun_dataset", "knife_dataset"):
        rows = [sample for sample in samples if sample.source == source]
        if not rows:
            continue
        if source in train_only:
            for sample in rows:
                sample.split = "train"
            summaries[source] = {
                "total": len(rows),
                "positive": sum(bool(sample.mapped_boxes) for sample in rows),
                "negative": sum(not sample.mapped_boxes for sample in rows),
                "policy": "all samples assigned to train by request",
                "by_split": {
                    "train": {
                        "total": len(rows),
                        "positive": sum(bool(sample.mapped_boxes) for sample in rows),
                        "negative": sum(not sample.mapped_boxes for sample in rows),
                    },
                    "val": {"total": 0, "positive": 0, "negative": 0},
                    "test": {"total": 0, "positive": 0, "negative": 0},
                },
            }
            continue
        target_counts = _largest_remainder_counts(len(rows))
        negatives = [sample for sample in rows if not sample.mapped_boxes]
        positives = [sample for sample in rows if sample.mapped_boxes]
        negative_counts = _largest_remainder_counts(len(negatives))
        positive_counts = {split: target_counts[split] - negative_counts[split] for split, _ in SUPPLEMENTARY_SPLIT_RATIOS}
        if min(positive_counts.values()) < 0 or sum(positive_counts.values()) != len(positives):
            raise ValueError(f"Cannot make the requested stratified split for {source}: {target_counts}")

        assigned_counts = {split: {"positive": 0, "negative": 0} for split, _ in SUPPLEMENTARY_SPLIT_RATIOS}
        for stratum, stratum_rows, quotas in (
            ("negative", negatives, negative_counts),
            ("positive", positives, positive_counts),
        ):
            shuffled = sorted(
                stratum_rows,
                key=lambda sample: hashlib.sha256(
                    f"weapon-supplement-split-v1|{SUPPLEMENTARY_SPLIT_SEED}|{source}|{sample.key}".encode("utf-8")
                ).hexdigest(),
            )
            offset = 0
            for split, _ratio in SUPPLEMENTARY_SPLIT_RATIOS:
                count = quotas[split]
                for sample in shuffled[offset : offset + count]:
                    sample.split = split
                    assigned_counts[split][stratum] += 1
                offset += count
            if offset != len(shuffled):
                raise AssertionError(f"Split assignment lost {source} samples in stratum {stratum}")
        summaries[source] = {
            "total": len(rows),
            "positive": len(positives),
            "negative": len(negatives),
            "by_split": {
                split: {
                    "total": assigned_counts[split]["positive"] + assigned_counts[split]["negative"],
                    **assigned_counts[split],
                }
                for split, _ratio in SUPPLEMENTARY_SPLIT_RATIOS
            },
        }
    return summaries


def _shanghaitech_rows() -> dict[str, dict[str, str]]:
    manifest = DATA_ROOT / "Dangerous Items" / "shanghaitech_negative_manifest.csv"
    if not manifest.is_file():
        return {}
    rows: dict[str, dict[str, str]] = {}
    with manifest.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            filename = str(row.get("filename") or Path(row.get("training_image_path", "")).name).strip()
            if filename:
                if filename in rows:
                    raise ValueError(f"ShanghaiTech manifest has a duplicate filename: {filename}")
                rows[filename] = {
                    "split": str(row.get("split") or row.get("target_split") or "").strip().lower(),
                    "video_id": str(row.get("video_id") or row.get("source_video") or row.get("video") or "").strip(),
                }
    return rows


def audit_and_build_dataset(
    *,
    output_root: Path | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    force: bool = False,
    include_sources: Iterable[str] | None = None,
    negative_only_sources: Iterable[str] | None = None,
    positive_only_sources: Iterable[str] | None = None,
    train_only_sources: Iterable[str] | None = None,
    class_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Audit selected sources and create an immutable, fingerprinted YOLO view."""
    root = (output_root or OUTPUT_ROOT).resolve()
    progress = progress or (lambda _event: None)
    selected_sources = set(include_sources) if include_sources is not None else set(LEGACY_DATASET_SOURCE_IDS)
    negative_sources = set(negative_only_sources or ())
    positive_sources = set(positive_only_sources or ())
    train_only = set(train_only_sources or ())
    unknown_sources = (selected_sources | negative_sources | positive_sources | train_only) - set(DATASET_SOURCE_IDS)
    if unknown_sources:
        raise ValueError(f"Unknown dataset source(s): {sorted(unknown_sources)}")
    if not selected_sources:
        raise ValueError("At least one dataset source must be selected")
    if not negative_sources.issubset(selected_sources):
        raise ValueError("negative_only_sources must be a subset of include_sources")
    if not positive_sources.issubset(selected_sources):
        raise ValueError("positive_only_sources must be a subset of include_sources")
    if negative_sources & positive_sources:
        raise ValueError("A source cannot be both positive-only and negative-only")
    if not train_only.issubset(selected_sources):
        raise ValueError("train_only_sources must be a subset of include_sources")
    active_class_names = tuple(class_names) if class_names is not None else CLASS_NAMES
    if not active_class_names or len(active_class_names) != len(set(active_class_names)):
        raise ValueError("class_names must contain unique model classes")
    unknown_classes = set(active_class_names) - set(CLASS_NAMES)
    if unknown_classes:
        raise ValueError(f"Unknown weapon class name(s): {sorted(unknown_classes)}")
    source_to_active_class = {CLASS_NAMES.index(name): index for index, name in enumerate(active_class_names)}
    label_by_name = {name: CLASS_LABELS[index] for index, name in enumerate(CLASS_NAMES)}
    sohas_annotation_audit = _prepare_sohas_source_view(root) if "sohas_weapon" in selected_sources else None
    source_specs = _source_locations(selected_sources, root, sohas_annotation_audit)
    image_rows: list[tuple[str, str, Path, Path, int | None]] = []
    source_images: dict[tuple[str, str], list[Path]] = {}
    shanghai_rows = _shanghaitech_rows()
    for source, split, image_dir, labels_dir, _base, map_class in source_specs:
        images = _iter_images(image_dir)
        if not images:
            raise ValueError(f"No images found in {image_dir}")
        image_names = {image.name for image in images}
        for image in images:
            actual_source = source
            if source == "dangerous_items" and image.name in shanghai_rows:
                manifest_split = shanghai_rows[image.name]["split"]
                if manifest_split != split:
                    raise ValueError(f"ShanghaiTech manifest split mismatch for {image.name}: {manifest_split} != {split}")
                actual_source = "shanghaitech_negative"
            if actual_source not in selected_sources:
                continue
            source_images.setdefault((actual_source, split), []).append(image)
            image_rows.append((actual_source, split, image, labels_dir, map_class))
        labels = {p.stem for p in labels_dir.glob("*.txt")}
        image_stems = {p.stem for p in images}
        orphans = sorted(labels - image_stems)
        if orphans:
            raise ValueError(f"Found {len(orphans)} label files without images in {labels_dir}: {orphans[:8]}")
        if source == "dangerous_items" and split == "train":
            missing_shanghai = sorted(name for name, row in shanghai_rows.items() if row["split"] == split and name not in image_names)
            if missing_shanghai:
                raise ValueError(f"ShanghaiTech manifest lists {len(missing_shanghai)} images missing from train: {missing_shanghai[:8]}")

    video_split_audit = _audit_shanghaitech_video_splits()
    if video_split_audit.get("available") and not video_split_audit.get("valid"):
        raise ValueError("ShanghaiTech video-level split audit failed: " + json.dumps(video_split_audit, ensure_ascii=False))

    samples: list[Sample] = []
    errors: list[dict[str, str]] = []
    total = len(image_rows)
    for index, (source, split, image, labels_dir, map_class) in enumerate(image_rows, start=1):
        try:
            samples.append(_record_sample(source, split, image, labels_dir, map_class))
        except Exception as exc:
            errors.append({"image": str(image), "error": str(exc)})
        if index % 100 == 0 or index == total:
            progress({"stage": "dataset_audit", "completed": index, "total": total, "message": f"检查图片与标签 {index}/{total}"})
    if errors:
        raise ValueError("Dataset audit found invalid samples: " + json.dumps(errors[:30], ensure_ascii=False))

    source_policy_exclusions: dict[str, str] = {}
    retained_samples = []
    for sample in samples:
        if sample.source in negative_sources and sample.mapped_boxes:
            source_policy_exclusions[sample.key] = "negative_only_source_has_non_empty_label"
        elif sample.source in positive_sources and not sample.mapped_boxes:
            source_policy_exclusions[sample.key] = "positive_only_source_has_empty_label"
        else:
            retained_samples.append(sample)
    samples = retained_samples
    remapped_samples = []
    for sample in samples:
        selected_boxes = [box for box in sample.mapped_boxes if box[0] in source_to_active_class]
        if sample.mapped_boxes and not selected_boxes:
            source_policy_exclusions[sample.key] = "contains_class_outside_active_model"
            continue
        sample.mapped_boxes = [
            (source_to_active_class[class_id], x, y, width, height)
            for class_id, x, y, width, height in selected_boxes
        ]
        remapped_samples.append(sample)
    samples = remapped_samples
    if not samples:
        raise ValueError("The selected sources produced no samples after applying class and negative-only policies")

    supplementary_split = _assign_supplementary_splits(samples, train_only)
    actual_source_images = Counter((sample.source, sample.split) for sample in samples)

    # Build connected groups from both file hashes and canonical decoded pixels.
    parent = list(range(len(samples)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        a, b = find(a), find(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    seen: dict[tuple[str, str], int] = {}
    for i, sample in enumerate(samples):
        for kind, fingerprint in (("file", sample.file_hash), ("pixels", sample.pixel_hash)):
            key = (kind, fingerprint)
            if key in seen:
                union(i, seen[key])
            else:
                seen[key] = i
    groups: dict[int, list[Sample]] = defaultdict(list)
    for i, sample in enumerate(samples):
        groups[find(i)].append(sample)

    removed: dict[str, str] = dict(source_policy_exclusions)
    duplicate_groups = 0
    conflict_groups = 0
    class_conflicts = 0
    bbox_conflicts = 0
    kept: list[Sample] = []
    split_rank = {"test": 0, "val": 1, "train": 2}
    source_rank = {"shanghaitech_negative": 0, "dangerous_items": 1, "long_gun_dataset": 2, "knife_dataset": 2}
    for group in groups.values():
        reason = _annotation_conflict(group)
        if reason:
            conflict_groups += 1
            class_conflicts += reason == "class_or_box_count_mismatch"
            bbox_conflicts += reason == "bbox_iou_below_0.8"
            for sample in group:
                removed[sample.key] = f"duplicate_group_{reason}"
            continue
        # First preserve the held-out split priority; within a split, retain
        # ShanghaiTech provenance when an identical empty-label image exists
        # in both the organized negative source and the main dataset.
        ordered = sorted(group, key=lambda s: (split_rank.get(s.split, 3), source_rank.get(s.source, 3), s.key.casefold()))
        kept.append(ordered[0])
        if len(ordered) > 1:
            duplicate_groups += 1
            for sample in ordered[1:]:
                removed[sample.key] = f"duplicate_of:{ordered[0].key}"

    # Stable fingerprint based on actual image and mapped-label contents plus split policy.
    fingerprint_digest = hashlib.sha256()
    fingerprint_digest.update(
        (
            f"yolov9-filtered-dataset-v10|source-write-isolation|stable-manifest-paths|duplicate-test-val-train|iou=0.8|shanghaitech-source|sohas-voc-knife-only-grouped|active-class-box-filter|"
            f"sources={','.join(sorted(selected_sources))}|negative-only={','.join(sorted(negative_sources))}|positive-only={','.join(sorted(positive_sources))}|train-only={','.join(sorted(train_only))}|"
            f"classes={','.join(active_class_names)}|"
            f"supplementary-split=70-15-15|stratify-positive-negative|seed={SUPPLEMENTARY_SPLIT_SEED}|sohas-trainval=grouped-sequences:test-original:seed=2026\n"
        ).encode("utf-8")
    )
    for sample in sorted(kept, key=lambda s: s.key):
        record = {
            "key": sample.key,
            "source": sample.source,
            "split": sample.split,
            "image_sha256": sample.file_hash,
            "pixel_sha256": sample.pixel_hash,
            "boxes": sample.mapped_boxes,
        }
        fingerprint_digest.update(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        fingerprint_digest.update(b"\n")
    fingerprint = fingerprint_digest.hexdigest()[:20]
    cache_root = root / "datasets" / fingerprint
    if not force and (cache_root / "audit_report.json").is_file() and (cache_root / "data.yaml").is_file():
        return json.loads((cache_root / "audit_report.json").read_text(encoding="utf-8"))

    staging = root / "datasets" / f".{fingerprint}.staging-{uuid.uuid4().hex}"
    for split in ("train", "val", "test"):
        (staging / "images" / split).mkdir(parents=True, exist_ok=True)
        (staging / "labels" / split).mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, Any]] = []
    class_boxes: Counter[int] = Counter()
    split_source: Counter[tuple[str, str]] = Counter()
    split_background: Counter[tuple[str, str]] = Counter()
    split_positive: Counter[tuple[str, str]] = Counter()
    preview_rows: list[dict[str, Any]] = []
    for index, sample in enumerate(sorted(kept, key=lambda s: (s.split, s.key)), start=1):
        token = hashlib.sha256(sample.key.encode("utf-8")).hexdigest()[:10]
        new_stem = f"{sample.source}_{sample.source_split}_{sample.image.stem}_{token}"
        relative_image = Path("images") / sample.split / f"{new_stem}{sample.image.suffix.lower()}"
        relative_label = Path("labels") / sample.split / f"{new_stem}.txt"
        staged_image = staging / relative_image
        staged_label = staging / relative_label
        final_image = cache_root / relative_image
        final_label = cache_root / relative_label
        try:
            if sample.jpeg_needs_repair:
                raise OSError("YOLOv9 may repair a JPEG with a missing EOI marker; isolate this sample from its source")
            os.link(sample.image, staged_image)
            link_mode = "hardlink"
        except OSError:
            shutil.copy2(sample.image, staged_image)
            link_mode = "copy_jpeg_repair_safe" if sample.jpeg_needs_repair else "copy"
        staged_label.write_text(
            "".join(f"{cls} {x:.8f} {y:.8f} {w:.8f} {h:.8f}\n" for cls, x, y, w, h in sample.mapped_boxes),
            encoding="utf-8",
        )
        class_boxes.update(box[0] for box in sample.mapped_boxes)
        split_source[(sample.split, sample.source)] += 1
        if not sample.mapped_boxes:
            split_background[(sample.split, sample.source)] += 1
        else:
            split_positive[(sample.split, sample.source)] += 1
        manifest_rows.append(
            {
                "source": sample.source,
                "source_split": sample.source_split,
                "split": sample.split,
                "source_image": str(sample.image),
                "source_label": str(sample.label),
                "image": str(final_image),
                "label": str(final_label),
                "image_sha256": sample.file_hash,
                "pixel_sha256": sample.pixel_hash,
                "label_sha256": _sha256_file(staged_label),
                "image_width": sample.image_size[0],
                "image_height": sample.image_size[1],
                "exif_orientation": sample.exif_orientation,
                "jpeg_needs_repair": sample.jpeg_needs_repair,
                "link_mode": link_mode,
                "classes": ",".join(str(box[0]) for box in sample.mapped_boxes),
                "box_count": len(sample.mapped_boxes),
                "negative": not sample.mapped_boxes,
            }
        )
        if len(preview_rows) < 600:
            preview_rows.append({"image": str(sample.image), "source": sample.source, "split": sample.split, "boxes": sample.mapped_boxes, "width": sample.image_size[0], "height": sample.image_size[1]})
        if index % 100 == 0 or index == len(kept):
            progress({"stage": "dataset_build", "completed": index, "total": len(kept), "message": f"建立安全训练副本 {index}/{len(kept)}"})

    yaml_lines = [f"path: {json.dumps(str(cache_root.resolve()), ensure_ascii=False)}", "train: images/train", "val: images/val", "test: images/test", "names:"]
    yaml_lines.extend(f"  {i}: {name}" for i, name in enumerate(active_class_names))
    (staging / "data.yaml").write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")
    with (staging / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(manifest_rows[0]))
        writer.writeheader()
        writer.writerows(manifest_rows)
    with (staging / "excluded_samples.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["source_sample", "reason"])
        writer.writeheader()
        writer.writerows({"source_sample": key, "reason": reason} for key, reason in sorted(removed.items()))
    supplemental_manifest = staging / "supplementary_split_manifest.csv"
    with supplemental_manifest.open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["source", "seed", "stratum", "split", "image", "label", "disposition"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for sample in sorted(
            (item for item in samples if item.source in {"long_gun_dataset", "knife_dataset", "sohas_weapon"}),
            key=lambda item: (item.source, item.split, item.key.casefold()),
        ):
            writer.writerow(
                {
                    "source": sample.source,
                    "seed": SUPPLEMENTARY_SPLIT_SEED,
                    "stratum": "negative" if not sample.mapped_boxes else "positive",
                    "split": sample.split,
                    "image": str(sample.image),
                    "label": str(sample.label),
                    "disposition": removed.get(sample.key, "kept"),
                }
            )
    manifest_by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in manifest_rows:
        manifest_by_split[row["split"]].append(row)
    for split_rows in manifest_by_split.values():
        names = {Path(row["image"]).stem for row in split_rows}
        label_names = {Path(row["label"]).stem for row in split_rows}
        if names != label_names:
            raise AssertionError("Generated image and label names do not match")
    split_counts = {split: len(manifest_by_split.get(split, [])) for split in ("train", "val", "test")}
    class_counts = {
        str(cls): {
            "name": name,
            "label": label_by_name[name],
            "boxes": class_boxes[cls],
            "images": sum(cls in [box[0] for box in sample.mapped_boxes] for sample in kept),
        }
        for cls, name in enumerate(active_class_names)
    }
    report = {
        "fingerprint": fingerprint,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cache_root": str(cache_root.resolve()),
        "data_yaml": str((cache_root / "data.yaml").resolve()),
        "manifest": str((cache_root / "manifest.csv").resolve()),
        "excluded_csv": str((cache_root / "excluded_samples.csv").resolve()),
        "supplementary_split_manifest": str((cache_root / "supplementary_split_manifest.csv").resolve()),
        "supplementary_split": {
            "seed": SUPPLEMENTARY_SPLIT_SEED,
            "ratios": {split: ratio for split, ratio in SUPPLEMENTARY_SPLIT_RATIOS},
            "stratified_by": "positive_vs_empty_label",
            "sources": supplementary_split,
        },
        "selected_sources": sorted(selected_sources),
        "negative_only_sources": sorted(negative_sources),
        "positive_only_sources": sorted(positive_sources),
        "train_only_sources": sorted(train_only),
        "source_policy_excluded_samples": len(source_policy_exclusions),
        "class_names": list(active_class_names),
        "source_images": {f"{source}:{split}": count for (source, split), count in actual_source_images.items()},
        "images": split_counts,
        "total_images": sum(split_counts.values()),
        "class_counts": class_counts,
        "positive_images": {f"{split}:{source}": count for (split, source), count in split_positive.items()},
        "background_images": {f"{split}:{source}": count for (split, source), count in split_background.items()},
        "by_split_and_source": {f"{split}:{source}": count for (split, source), count in split_source.items()},
        "duplicate_groups_removed": duplicate_groups,
        "annotation_conflict_groups_excluded": conflict_groups,
        "class_or_count_conflict_groups": class_conflicts,
        "bbox_conflict_groups": bbox_conflicts,
        "excluded_samples": len(removed),
        "source_roots": [str(path.resolve()) for label, path in IMAGE_ROOTS if any(
            source == {"Dangerous Items": "dangerous_items", "Dataset": "long_gun_dataset", "Knife Dataset": "knife_dataset", "SOHAS Weapon Detection": "sohas_weapon"}[label]
            for source in selected_sources
        ) or (label == "Dangerous Items" and "shanghaitech_negative" in selected_sources)],
        "preview_samples": preview_rows,
        "video_split_audit": video_split_audit,
        "sohas_annotation_audit": sohas_annotation_audit,
        "exif_orientation_images": sum(sample.exif_orientation != 1 for sample in kept),
        "jpeg_repair_isolated_images": sum(sample.jpeg_needs_repair for sample in kept),
        "source_image_write_protection": "JPEGs that YOLO may repair are copied into the cache; all generated labels are independent files.",
    }
    atomic_write_json(staging / "audit_report.json", report)
    # A fingerprint directory is immutable. If a prior run won a creation race,
    # retain that already-complete cache and remove only this private staging tree.
    cache_root.parent.mkdir(parents=True, exist_ok=True)
    if cache_root.exists():
        shutil.rmtree(staging)
        return json.loads((cache_root / "audit_report.json").read_text(encoding="utf-8"))
    os.replace(staging, cache_root)
    report["cache_root"] = str(cache_root.resolve())
    report["data_yaml"] = str((cache_root / "data.yaml").resolve())
    report["manifest"] = str((cache_root / "manifest.csv").resolve())
    report["excluded_csv"] = str((cache_root / "excluded_samples.csv").resolve())
    report["supplementary_split_manifest"] = str((cache_root / "supplementary_split_manifest.csv").resolve())
    # Re-write after atomic move so report paths point to the stable cache.
    atomic_write_json(cache_root / "audit_report.json", report)
    return report


def _audit_shanghaitech_video_splits() -> dict[str, Any]:
    manifest = DATA_ROOT / "Dangerous Items" / "shanghaitech_negative_manifest.csv"
    if not manifest.is_file():
        return {"available": False, "message": "ShanghaiTech manifest not found; no supplemental video rows detected"}
    videos: dict[str, set[str]] = defaultdict(set)
    video_row_counts: Counter[str] = Counter()
    missing_images: list[str] = []
    invalid_rows: list[int] = []
    rows = 0
    with manifest.open("r", encoding="utf-8-sig", newline="") as stream:
        for row_number, row in enumerate(csv.DictReader(stream), start=2):
            split = str(row.get("split") or row.get("target_split") or "").strip().lower()
            video = str(row.get("video_id") or row.get("source_video") or row.get("video") or "").strip()
            filename = str(row.get("filename") or Path(row.get("training_image_path", "")).name).strip()
            if split not in {"train", "val", "test"} or not video or not filename:
                invalid_rows.append(row_number)
                continue
            videos[video].add(split)
            video_row_counts[video] += 1
            image_path = DATA_ROOT / "Dangerous Items" / "images" / split / filename
            if not image_path.is_file():
                missing_images.append(filename)
            rows += 1
    crossing = sorted(video for video, splits in videos.items() if len(splits) > 1)
    wrong_frame_counts = {video: count for video, count in video_row_counts.items() if count != 4}
    return {
        "available": True,
        "rows": rows,
        "videos": len(videos),
        "expected_frames_per_video": 4,
        "cross_split_video_ids": crossing,
        "missing_image_filenames": sorted(set(missing_images)),
        "rows_with_missing_fields": invalid_rows,
        "videos_with_unexpected_frame_count": wrong_frame_counts,
        "valid": not crossing and not missing_images and not invalid_rows and not wrong_frame_counts,
    }


def default_training_config() -> dict[str, Any]:
    return {
        "project_root": str(PROJECT_ROOT),
        "data_root": str(DATA_ROOT),
        "yolov9_root": str(YOLO_ROOT),
        "pretrained": str(PRETRAINED_PATH),
        "model_config": str(MODEL_CONFIG_PATH),
        "output_root": str(OUTPUT_ROOT),
        "dataset_sources": None,
        "negative_only_sources": [],
        "positive_only_sources": [],
        "train_only_sources": [],
        "class_names": None,
        "budget_seconds": 0,
        "epochs_cap": 50,
        "patience": 5,
        "imgsz": 640,
        "batch_size": 16,
        "workers": 2,
        "device": "0",
        "optimizer": "AdamW",
        "lr0": 0.001,
        "lrf": 0.1,
        "momentum": 0.9,
        "weight_decay": 0.0005,
        "warmup_epochs": 1.0,
        "seed": 2026,
        "mosaic": 0.5,
        "close_mosaic_epochs": 3,
        "mixup": 0.0,
        "hsv_h": 0.015,
        "hsv_s": 0.5,
        "hsv_v": 0.3,
        "translate": 0.1,
        "scale": 0.3,
        "fliplr": 0.5,
        "flipud": 0.0,
        "iou_threshold": 0.7,
    }
