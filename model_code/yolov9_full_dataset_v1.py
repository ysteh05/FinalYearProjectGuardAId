"""Fixed, leakage-audited dataset recipe for the two-class YOLO full V1 run.

The source datasets are read-only.  This module creates a fingerprinted YOLO
view containing only ``knife`` and ``gun`` boxes plus explicitly target-free
images.  In particular, WeaponDataset V11's ``other`` annotations are dropped
as samples; they are never rewritten as either class or as negatives.
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from PIL import Image, ImageOps

from yolov9_weapon_workspace import DATA_ROOT, PROJECT_ROOT, atomic_write_json


PROFILE_ID = "full_dataset_v1"
CLASS_NAMES = ("knife", "gun")
CLASS_LABELS = ("刀", "枪")
SPLIT_SEED = 2026
POLICY_VERSION = "full-dataset-v1-two-class-no-other-v3"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
WEAPON_WORDS = {
    "pistol", "handgun", "gun", "firearm", "weapon", "long_gun", "long gun",
    "rifle", "shotgun", "revolver", "submachine_gun", "submachine gun", "ak47",
}


def _io_path(path: Path) -> Path:
    """Use the Windows extended path prefix for long Roboflow filenames."""
    absolute = str(path.resolve())
    if os.name == "nt" and len(absolute) >= 240 and not absolute.startswith("\\\\?\\"):
        return Path("\\\\?\\" + absolute)
    return path


def _read_text(path: Path) -> str:
    return _io_path(path).read_text(encoding="utf-8-sig", errors="strict")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _io_path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pixel_fingerprint(path: Path) -> tuple[str, tuple[int, int], int]:
    with Image.open(_io_path(path)) as opened:
        orientation = int(opened.getexif().get(274, 1) or 1)
        image = ImageOps.exif_transpose(opened).convert("RGB")
        size = image.size
        digest = hashlib.sha256()
        digest.update(f"{size[0]}x{size[1]}|RGB|".encode("ascii"))
        digest.update(image.tobytes())
        return digest.hexdigest(), size, orientation


def _images(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"图片目录不存在：{directory}")
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES),
        key=lambda path: path.name.casefold(),
    )


def _label_map(directory: Path, suffix: str = ".txt") -> dict[str, Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"标注目录不存在：{directory}")
    result: dict[str, Path] = {}
    for path in directory.glob(f"*{suffix}"):
        key = path.stem.casefold()
        if key in result:
            raise ValueError(f"标注 stem 重复：{path}")
        result[key] = path
    return result


def _largest_remainder(total: int, ratios: tuple[tuple[str, float], ...]) -> dict[str, int]:
    raw = {name: total * ratio for name, ratio in ratios}
    output = {name: math.floor(value) for name, value in raw.items()}
    for name in sorted(raw, key=lambda key: (raw[key] - output[key], -list(raw).index(key)), reverse=True)[: total - sum(output.values())]:
        output[name] += 1
    return output


def _group_assignment(
    rows: list["Sample"],
    ratios: tuple[tuple[str, float], ...],
    seed_label: str,
    *,
    group_attr: str = "group_key",
    stratify: bool = True,
) -> dict[str, str]:
    """Deterministically split indivisible groups, balancing target classes."""
    strata: dict[str, dict[str, list[Sample]]] = defaultdict(lambda: defaultdict(list))
    grouped_rows: dict[str, list[Sample]] = defaultdict(list)
    for row in rows:
        group = getattr(row, group_attr)
        grouped_rows[group].append(row)
    for group, group_rows in grouped_rows.items():
        classes = sorted({box[0] for row in group_rows for box in row.boxes})
        has_negative_frame = any(not row.boxes for row in group_rows)
        stratum_parts = [str(cls) for cls in classes]
        if has_negative_frame:
            stratum_parts.append("negative")
        stratum = "+".join(stratum_parts) or "negative"
        if not stratify:
            stratum = "all"
        strata[stratum][group] = group_rows

    assigned: dict[str, str] = {}
    for stratum, groups in sorted(strata.items()):
        total = sum(len(group_rows) for group_rows in groups.values())
        targets = _largest_remainder(total, ratios)
        current = {split: 0 for split, _ratio in ratios}
        ordered = sorted(
            groups,
            key=lambda key: (
                -len(groups[key]),
                hashlib.sha256(f"{POLICY_VERSION}|{SPLIT_SEED}|{seed_label}|{stratum}|{key}".encode("utf-8")).hexdigest(),
            ),
        )
        for key in ordered:
            size = len(groups[key])
            # Place each indivisible group where its proportional deficit is
            # largest. This gives exact 80/10/10 for single-image groups and
            # keeps SOHAS video sequences intact.
            split = max(
                (name for name, _ratio in ratios),
                key=lambda name: ((targets[name] - current[name]) / max(1, targets[name]), -list(targets).index(name)),
            )
            current[split] += size
            for row in groups[key]:
                assigned[row.key] = split
    return assigned


@dataclass
class Sample:
    source: str
    source_split: str
    split: str
    image: Path
    label: Path
    boxes: list[tuple[int, float, float, float, float]]
    raw_text: str
    source_classes: list[int | str] = field(default_factory=list)
    group_key: str = ""
    image_sha256: str = ""
    pixel_sha256: str = ""
    image_size: tuple[int, int] = (0, 0)
    exif_orientation: int = 1
    link_mode: str = ""

    @property
    def key(self) -> str:
        try:
            relative = self.image.relative_to(DATA_ROOT)
        except ValueError:
            relative = self.image
        return f"{self.source}:{self.source_split}:{relative.as_posix()}"


def _validate_box(values: Iterable[float], source_label: Path, line: int) -> tuple[float, float, float, float]:
    x, y, width, height = tuple(values)
    if not all(math.isfinite(value) for value in (x, y, width, height)):
        raise ValueError(f"{source_label}:{line}: bbox 含非有限数值")
    eps = 1e-5
    if width <= 0 or height <= 0:
        raise ValueError(f"{source_label}:{line}: bbox 宽高必须大于零")
    if any(value < -eps or value > 1 + eps for value in (x, y, width, height)):
        raise ValueError(f"{source_label}:{line}: YOLO bbox 坐标必须处于 [0,1]")
    if x - width / 2 < -eps or y - height / 2 < -eps or x + width / 2 > 1 + eps or y + height / 2 > 1 + eps:
        raise ValueError(f"{source_label}:{line}: bbox 超出图片范围")
    return x, y, width, height


def _read_yolo_boxes(path: Path, class_map: dict[int, int], allowed_ids: set[int]) -> tuple[list[tuple[int, float, float, float, float]], list[int], bool]:
    raw = _read_text(path)
    boxes: list[tuple[int, float, float, float, float]] = []
    class_ids: list[int] = []
    has_other = False
    for line_number, text in enumerate(raw.splitlines(), start=1):
        fields = text.strip().split()
        if not fields:
            continue
        if len(fields) != 5:
            raise ValueError(f"{path}:{line_number}: YOLO 标签应有 5 个值，实际 {len(fields)} 个")
        try:
            raw_class = float(fields[0])
            coords = [float(item) for item in fields[1:]]
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: 标签含非数值") from exc
        if not math.isfinite(raw_class) or not raw_class.is_integer():
            raise ValueError(f"{path}:{line_number}: 类别 ID 必须是有限整数")
        class_id = int(raw_class)
        if class_id not in allowed_ids:
            raise ValueError(f"{path}:{line_number}: 类别 ID {class_id} 不在此来源定义中")
        box = _validate_box(coords, path, line_number)
        class_ids.append(class_id)
        has_other = has_other or class_id == 2
        if class_id in class_map:
            boxes.append((class_map[class_id], *box))
    return boxes, class_ids, has_other


def _append_yolo_source(
    samples: list[Sample],
    excluded: list[dict[str, str]],
    inventory: list[dict[str, Any]],
    *,
    source: str,
    source_split: str,
    split: str,
    image_dir: Path,
    label_dir: Path,
    class_map: dict[int, int],
    allowed_ids: set[int],
    negative_only: bool = False,
    person_id: int | None = None,
    v11_other_policy: bool = False,
) -> list[Sample]:
    images = _images(image_dir)
    labels = _label_map(label_dir)
    image_stems = {path.stem.casefold() for path in images}
    orphans = sorted(set(labels) - image_stems)
    for orphan in orphans:
        orphan_path = labels[orphan]
        excluded.append({"source_sample": f"{source}:{source_split}:{orphan_path}", "reason": "label_without_matching_image"})
        stat = _io_path(orphan_path).stat()
        inventory.append({"path": str(orphan_path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "disposition": "orphan_label"})
    pending: list[Sample] = []
    for image_index, image in enumerate(images, start=1):
        label = labels.get(image.stem.casefold())
        if label is None:
            excluded.append({"source_sample": f"{source}:{source_split}:{image}", "reason": "image_without_matching_label"})
            stat = _io_path(image).stat()
            inventory.append({"path": str(image.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "disposition": "unlabeled_image"})
            continue
        raw_text = _read_text(label)
        # The annotation parser also validates every coordinate before any
        # source-specific class filtering is applied.
        try:
            boxes, classes, has_other = _read_yolo_boxes(label, class_map, allowed_ids)
        except (ValueError, OSError) as exc:
            excluded.append({"source_sample": f"{source}:{source_split}:{image}", "reason": f"invalid_yolo_annotation:{exc}"})
            for path in (image, label):
                stat = _io_path(path).stat()
                inventory.append({"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "disposition": "invalid_annotation"})
            continue
        if person_id is not None:
            # Class 0 in the supplementary folders describes the person, not
            # the weapon. Strip those boxes and retain only the weapon class.
            boxes = [box for box in boxes if box[0] == class_map.get(1)]
        reason = None
        if v11_other_policy and has_other:
            reason = "v11_other_class_sample_dropped"
        elif negative_only and classes:
            reason = "negative_source_has_nonempty_label"
        elif classes and not boxes and person_id is None:
            reason = "non_target_weapon_sample_dropped"
        if reason:
            excluded.append({"source_sample": f"{source}:{source_split}:{image}", "reason": reason})
            disposition = "excluded"
        else:
            disposition = "candidate"
            group = f"image:{image.stem.casefold()}"
            pending.append(Sample(source, source_split, split, image, label, boxes, raw_text, classes.copy(), group))
        for path in (image, label):
            stat = _io_path(path).stat()
            inventory.append({"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "disposition": disposition})
        if image_index % 500 == 0 or image_index == len(images):
            _REPORT_PROGRESS({"stage": "dataset_audit", "completed": image_index, "total": len(images), "message": f"读取 {source} {source_split} 图片与标签 {image_index}/{len(images)}"})
    return pending


def _grouped_image_key(name: str) -> str:
    stem = Path(name).stem.casefold()
    if re.fullmatch(r"billete_\d+", stem):
        return f"still:{stem}"
    match = re.fullmatch(r"(.+?)(\d+)", stem)
    if match:
        return f"sequence:{match.group(1)}"
    return f"image:{stem}"


def _xml_rows(
    samples: list[Sample],
    excluded: list[dict[str, str]],
    inventory: list[dict[str, Any]],
    *,
    source: str,
    split_kind: str,
    image_dir: Path,
    xml_dir: Path,
    native_split: str,
) -> list[Sample]:
    images = _images(image_dir)
    xmls = _label_map(xml_dir, ".xml")
    image_stems = {path.stem.casefold() for path in images}
    missing = [image.name for image in images if image.stem.casefold() not in xmls]
    if missing:
        raise ValueError(f"{source} 有 {len(missing)} 张图片缺少 XML 标签：{missing[:8]}")
    pending: list[Sample] = []
    for image_index, image in enumerate(images, start=1):
        xml_path = xmls[image.stem.casefold()]
        try:
            root = ET.parse(_io_path(xml_path)).getroot()
            with Image.open(_io_path(image)) as opened:
                width, height = opened.size
            declared_w = int(root.findtext("size/width", str(width)))
            declared_h = int(root.findtext("size/height", str(height)))
            if (declared_w, declared_h) != (width, height):
                raise ValueError(f"XML 图片尺寸 {declared_w}x{declared_h} 与实际 {width}x{height} 不符")
            boxes: list[tuple[int, float, float, float, float]] = []
            source_classes: list[str] = []
            for object_index, obj in enumerate(root.findall("object"), start=1):
                name = (obj.findtext("name") or "").strip().casefold().replace("-", "_")
                if not name:
                    raise ValueError(f"第 {object_index} 个目标缺少类别名")
                source_classes.append(name)
                class_id = 0 if name == "knife" else 1 if name in WEAPON_WORDS else None
                if class_id is None:
                    continue
                bounds = obj.find("bndbox")
                if bounds is None:
                    raise ValueError(f"{name} 目标缺少 bounding box")
                values = [float(bounds.findtext(key, "nan")) for key in ("xmin", "ymin", "xmax", "ymax")]
                if not all(math.isfinite(value) for value in values):
                    raise ValueError(f"{name} bbox 含非有限数值")
                xmin, ymin, xmax, ymax = values
                if xmin < 0 or ymin < 0 or xmax > width or ymax > height or xmax <= xmin or ymax <= ymin:
                    raise ValueError(f"{name} bbox 超出图片或面积无效：{values}")
                boxes.append((class_id, (xmin + xmax) / (2 * width), (ymin + ymax) / (2 * height), (xmax - xmin) / width, (ymax - ymin) / height))
        except (ET.ParseError, OSError, ValueError, TypeError) as exc:
            excluded.append({"source_sample": f"{source}:{native_split}:{image}", "reason": f"invalid_voc_annotation:{exc}"})
            for path in (image, xml_path):
                stat = _io_path(path).stat()
                inventory.append({"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "disposition": "invalid_annotation"})
            continue
        group = _grouped_image_key(image.name) if split_kind == "sohas_grouped" else f"image:{image.stem.casefold()}"
        pending.append(Sample(source, native_split, native_split, image, xml_path, boxes, "", source_classes, group))
        for path in (image, xml_path):
            stat = _io_path(path).stat()
            inventory.append({"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "disposition": "candidate"})
        if image_index % 500 == 0 or image_index == len(images):
            _REPORT_PROGRESS({"stage": "dataset_audit", "completed": image_index, "total": len(images), "message": f"读取 {source} 图片与 VOC 标签 {image_index}/{len(images)}"})
    orphan_xml = len(set(xmls) - image_stems)
    return pending


def _bbox_iou(a: tuple, b: tuple) -> float:
    _, ax, ay, aw, ah = a
    _, bx, by, bw, bh = b
    ax1, ay1, ax2, ay2 = ax - aw / 2, ay - ah / 2, ax + aw / 2, ay + ah / 2
    bx1, by1, bx2, by2 = bx - bw / 2, by - bh / 2, bx + bw / 2, by + bh / 2
    inter = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _annotation_conflict(group: list[Sample]) -> str | None:
    reference = group[0].boxes
    for row in group[1:]:
        if len(reference) != len(row.boxes):
            return "class_or_box_count_mismatch"
        if Counter(box[0] for box in reference) != Counter(box[0] for box in row.boxes):
            return "class_or_box_count_mismatch"
        unmatched = set(range(len(row.boxes)))
        for box in reference:
            candidates = [index for index in unmatched if row.boxes[index][0] == box[0] and _bbox_iou(box, row.boxes[index]) >= 0.8]
            if not candidates:
                return "bbox_iou_below_0.8"
            best = max(candidates, key=lambda index: _bbox_iou(box, row.boxes[index]))
            unmatched.remove(best)
    return None


def _source_rows(progress: Callable[[dict[str, Any]], None], excluded: list[dict[str, str]], inventory: list[dict[str, Any]]) -> tuple[list[Sample], dict[str, Any]]:
    root = DATA_ROOT
    rows: list[Sample] = []
    source_rules: dict[str, Any] = {}

    dangerous = root / "Dangerous Items"
    manifest_path = dangerous / "shanghaitech_negative_manifest.csv"
    shanghai: dict[str, dict[str, str]] = {}
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            filename = str(row.get("filename") or Path(row.get("training_image_path", "")).name).strip()
            split = str(row.get("split") or row.get("target_split") or "").strip().lower()
            video_id = str(row.get("video_id") or row.get("source_video") or "").strip()
            if filename in shanghai:
                raise ValueError(f"ShanghaiTech manifest 文件名重复：{filename}")
            if split not in {"train", "val", "test"} or not video_id:
                raise ValueError(f"ShanghaiTech manifest 含无效划分或视频 ID：{row}")
            shanghai[filename] = {"split": split, "video_id": video_id}
    video_splits: dict[str, set[str]] = defaultdict(set)
    video_frames: Counter[str] = Counter()
    for filename, meta in shanghai.items():
        video_splits[meta["video_id"]].add(meta["split"])
        video_frames[meta["video_id"]] += 1
    crossed = sorted(video for video, splits in video_splits.items() if len(splits) != 1)
    bad_frames = {video: count for video, count in video_frames.items() if count != 4}
    if crossed or bad_frames:
        raise ValueError(f"ShanghaiTech 视频级 split 检查失败：crossed={crossed[:5]}, frame_counts={list(bad_frames.items())[:5]}")

    dangerous_class_map = {1: 0, 3: 1, 4: 1}
    dangerous_ids = set(range(5))
    for split in ("train", "val", "test"):
        split_rows = _append_yolo_source(
            rows, excluded, inventory, source="dangerous_items", source_split=split, split=split,
            image_dir=dangerous / "images" / split, label_dir=dangerous / "labels" / split,
            class_map=dangerous_class_map, allowed_ids=dangerous_ids,
        )
        for sample in split_rows:
            meta = shanghai.get(sample.image.name)
            if meta:
                if meta["split"] != split:
                    raise ValueError(f"ShanghaiTech manifest split 与图片目录不一致：{sample.image.name}")
                # ShanghaiTech is a declared negative source, not generic
                # Dangerous Items. A weapon label here would invalidate that
                # source contract and is excluded for manual review.
                if sample.source_classes:
                    excluded.append({"source_sample": sample.key, "reason": "shanghaitech_negative_has_nonempty_label"})
                    continue
                sample.source = "shanghaitech_negative"
                sample.group_key = f"video:{meta['video_id']}"
            rows.append(sample)
    manifest_names = set(shanghai)
    observed_names = {sample.image.name for sample in rows if sample.source == "shanghaitech_negative"}
    if manifest_names - observed_names:
        raise ValueError(f"ShanghaiTech manifest 列出的图片缺失或未进入样本：{sorted(manifest_names - observed_names)[:8]}")
    source_rules["shanghaitech_video_split"] = {
        "videos": len(video_splits), "frames": len(shanghai), "frames_per_video": 4,
        "cross_split_video_ids": crossed, "videos_with_unexpected_frame_count": bad_frames, "valid": True,
    }
    source_rules["dangerous_items"] = "保留原 train/val/test；knife->knife，long_gun/gun->gun；仅非目标武器类别的图片排除；空标签保留为 negative。"

    # Supplementary Long Gun and Knife datasets use class 0 for person and 1
    # for the weapon. Person boxes are removed from this training copy.
    for source, folder, target_class in (
        ("long_gun_dataset", root / "Dataset", 1),
        ("knife_dataset", root / "Knife_Dataset" / "Knife_Dataset", 0),
    ):
        collected = _append_yolo_source(
            rows, excluded, inventory, source=source, source_split="unsplit", split="train",
            image_dir=folder / "images", label_dir=folder / "labels",
            class_map={1: target_class}, allowed_ids={0, 1}, person_id=0,
        )
        assigned = _group_assignment(collected, (("train", 0.8), ("val", 0.1), ("test", 0.1)), source)
        for sample in collected:
            sample.split = assigned[sample.key]
        rows.extend(collected)
        source_rules[source] = "移除 person 框后保留武器框；完整 source 按固定 seed 2026、按类别 80/10/10 分组划分。"

    v11 = root / "WeaponDataset.v11i.yolov9"
    v11_train = _append_yolo_source(
        rows, excluded, inventory, source="weapon_dataset_v11", source_split="train", split="train",
        image_dir=v11 / "train" / "images", label_dir=v11 / "train" / "labels",
        class_map={0: 0, 1: 1, 3: 1}, allowed_ids={0, 1, 2, 3}, v11_other_policy=True,
    )
    v11_valid = _append_yolo_source(
        rows, excluded, inventory, source="weapon_dataset_v11", source_split="valid", split="test",
        image_dir=v11 / "valid" / "images", label_dir=v11 / "valid" / "labels",
        class_map={0: 0, 1: 1, 3: 1}, allowed_ids={0, 1, 2, 3}, v11_other_policy=True,
    )
    v11_split = _group_assignment(v11_train, (("train", 0.875), ("val", 0.125)), "weapon_dataset_v11")
    for sample in v11_train:
        sample.split = v11_split[sample.key]
    rows.extend(v11_train)
    rows.extend(v11_valid)
    source_rules["weapon_dataset_v11"] = "ID 0 knife; ID 1 long_gun 与 ID 3 pistol 合并为 gun；含 ID 2 other 的样本直接排除；原 valid 作为 test，原 train 的可用样本按 7/8、1/8 划 train/val。"

    od_root = root / "OD-WeaponDetection-master"
    od_sohas = od_root / "Weapons and similar handled objects" / "Sohas_weapon-Detection"
    sohas_train = _xml_rows(rows, excluded, inventory, source="od_sohas_detection", split_kind="sohas_grouped", image_dir=od_sohas / "images", xml_dir=od_sohas / "annotations" / "xmls", native_split="trainval")
    sohas_test = _xml_rows(rows, excluded, inventory, source="od_sohas_detection", split_kind="sohas_grouped", image_dir=od_sohas / "images_test", xml_dir=od_sohas / "annotations_test" / "xmls", native_split="test")
    test_sequence_groups = {sample.group_key for sample in sohas_test}
    crossed_trainval = [sample for sample in sohas_train if sample.group_key in test_sequence_groups]
    if crossed_trainval:
        for sample in crossed_trainval:
            excluded.append({"source_sample": sample.key, "reason": "od_sohas_trainval_sequence_overlaps_native_test"})
        crossed_keys = {sample.key for sample in crossed_trainval}
        sohas_train = [sample for sample in sohas_train if sample.key not in crossed_keys]
    if not sohas_train or not sohas_test:
        raise ValueError("OD SOHAS Detection 的 trainval 或原生 test 清理后为空")
    trainval_groups = defaultdict(list)
    for sample in sohas_train:
        trainval_groups[sample.group_key].append(sample)
    # Allocate whole image-sequence groups to train or validation; original
    # test images remain fixed. Balance the knife/gun/negative strata.
    sohas_split = _group_assignment(sohas_train, (("train", 0.875), ("val", 0.125)), "od_sohas_detection")
    for sample in sohas_train:
        sample.split = sohas_split[sample.key]
    rows.extend(sohas_train)
    rows.extend(sohas_test)
    source_rules["od_sohas_detection"] = "保留 trainval 与原 test；trainval 按编号帧序列分组，7/8 train、1/8 val；knife->knife，pistol/枪械类别->gun，其他已标注对象且无目标框的图作为 negative。"

    od_knife = od_root / "Knife_detection"
    od_knife_rows = _xml_rows(rows, excluded, inventory, source="od_knife_detection", split_kind="unsplit", image_dir=od_knife / "Images", xml_dir=od_knife / "annotations", native_split="unsplit")
    od_knife_split = _group_assignment(od_knife_rows, (("train", 0.8), ("val", 0.1), ("test", 0.1)), "od_knife_detection")
    for sample in od_knife_rows:
        sample.split = od_knife_split[sample.key]
    rows.extend(od_knife_rows)
    source_rules["od_knife_detection"] = "Knife detection 的 XML 标注；固定 seed 2026、80/10/10 分组划分。"

    od_pistol = od_root / "Pistol detection"
    od_pistol_rows = _xml_rows(rows, excluded, inventory, source="od_pistol_detection", split_kind="unsplit", image_dir=od_pistol / "Weapons", xml_dir=od_pistol / "xmls", native_split="unsplit")
    od_pistol_split = _group_assignment(od_pistol_rows, (("train", 0.8), ("val", 0.1), ("test", 0.1)), "od_pistol_detection")
    for sample in od_pistol_rows:
        sample.split = od_pistol_split[sample.key]
    rows.extend(od_pistol_rows)
    source_rules["od_pistol_detection"] = "Pistol detection 的 XML 标注映射为 gun；固定 seed 2026、80/10/10 分组划分。"

    progress({"stage": "dataset_audit", "completed": len(rows), "total": len(rows), "message": f"读取固定数据方案：发现 {len(rows)} 个候选样本，排除 {len(excluded)} 个非目标样本"})
    return rows, source_rules


def _assign_duplicate_free(rows: list[Sample], excluded: list[dict[str, str]]) -> tuple[list[Sample], dict[str, Any]]:
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    seen: dict[tuple[str, str], int] = {}
    for index, row in enumerate(rows):
        row.image_sha256 = _sha256(row.image)
        row.pixel_sha256, row.image_size, row.exif_orientation = _pixel_fingerprint(row.image)
        for kind, digest in (("file", row.image_sha256), ("pixels", row.pixel_sha256)):
            key = (kind, digest)
            if key in seen:
                union(index, seen[key])
            else:
                seen[key] = index
        if index % 200 == 0 or index + 1 == len(rows):
            if index % 1000 == 0 or index + 1 == len(rows):
                _REPORT_PROGRESS({"stage": "dataset_hash", "completed": index + 1, "total": len(rows), "message": f"检查图片内容与跨集重复 {index + 1}/{len(rows)}"})

    groups: dict[int, list[Sample]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[find(index)].append(row)
    kept: list[Sample] = []
    duplicate_groups = 0
    conflicts = Counter()
    split_rank = {"test": 0, "val": 1, "train": 2}
    source_order = {
        "dangerous_items": 0, "shanghaitech_negative": 1, "weapon_dataset_v11": 2,
        "od_sohas_detection": 3, "long_gun_dataset": 4, "knife_dataset": 5,
        "od_knife_detection": 6, "od_pistol_detection": 7,
    }
    for group in groups.values():
        conflict = _annotation_conflict(group)
        if conflict:
            conflicts[conflict] += 1
            for sample in group:
                excluded.append({"source_sample": sample.key, "reason": f"duplicate_group_{conflict}"})
            continue
        best = min(group, key=lambda sample: (split_rank[sample.split], source_order.get(sample.source, 99), sample.key.casefold()))
        kept.append(best)
        if len(group) > 1:
            duplicate_groups += 1
            for sample in group:
                if sample is not best:
                    excluded.append({"source_sample": sample.key, "reason": f"duplicate_of:{best.key}"})
    return kept, {"duplicate_groups_removed": duplicate_groups, "annotation_conflict_groups_excluded": sum(conflicts.values()), "conflict_reasons": dict(conflicts)}


_REPORT_PROGRESS: Callable[[dict[str, Any]], None] = lambda _event: None


def build_dataset(
    *,
    output_root: Path | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Build or reuse the immutable, two-class complete V1 training view."""
    global _REPORT_PROGRESS
    root = (output_root or PROJECT_ROOT / "model_runs" / "yolov9_full_dataset_v1").resolve()
    _REPORT_PROGRESS = progress or (lambda _event: None)
    root.mkdir(parents=True, exist_ok=True)
    excluded: list[dict[str, str]] = []
    inventory: list[dict[str, Any]] = []
    samples, source_rules = _source_rows(_REPORT_PROGRESS, excluded, inventory)
    if not samples:
        raise ValueError("完整数据集 V1 没有可用图片")

    # The inventory shortcut reuses a prior audited cache without reading all
    # pixels again when every source file's size and modification time match.
    inventory_digest = hashlib.sha256((POLICY_VERSION + "\n").encode("utf-8"))
    for item in sorted(inventory, key=lambda row: row["path"].casefold()):
        inventory_digest.update(json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        inventory_digest.update(b"\n")
    inventory_fingerprint = inventory_digest.hexdigest()[:24]
    if not force:
        existing = sorted(root.glob("datasets/*/audit_report.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for report_path in existing:
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if report.get("profile") == PROFILE_ID and report.get("source_inventory_fingerprint") == inventory_fingerprint:
                if Path(report.get("data_yaml", "")).is_file() and Path(report.get("manifest", "")).is_file():
                    _REPORT_PROGRESS({"stage": "dataset_ready", "completed": report.get("total_images", 0), "total": report.get("total_images", 0), "message": "复用已审计的完整数据集 V1 缓存"})
                    return report

    duplicate_report: dict[str, Any] = {}
    all_excluded = list(excluded)
    _REPORT_PROGRESS({"stage": "dataset_hash", "completed": 0, "total": len(samples), "message": f"开始检查 {len(samples)} 张候选图的文件与像素重复"})
    kept, duplicate_report = _assign_duplicate_free(samples, all_excluded)
    if not kept:
        raise ValueError("数据集审计后没有剩余样本")

    # Stable identity depends on mappings, split assignment, content hashes,
    # and exclusion policy. The raw source paths remain unchanged.
    digest = hashlib.sha256((POLICY_VERSION + f"|seed={SPLIT_SEED}|other=drop|\n").encode("utf-8"))
    for row in sorted(kept, key=lambda item: item.key.casefold()):
        record = {"key": row.key, "source": row.source, "source_split": row.source_split, "split": row.split, "file": row.image_sha256, "pixels": row.pixel_sha256, "boxes": row.boxes}
        digest.update(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    fingerprint = digest.hexdigest()[:20]
    cache_root = root / "datasets" / fingerprint
    if (cache_root / "audit_report.json").is_file() and (cache_root / "data.yaml").is_file() and not force:
        return json.loads((cache_root / "audit_report.json").read_text(encoding="utf-8"))

    staging = cache_root.with_name(f".{cache_root.name}.staging-{uuid.uuid4().hex}")
    for split in ("train", "val", "test"):
        (staging / "images" / split).mkdir(parents=True, exist_ok=True)
        (staging / "labels" / split).mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, Any]] = []
    source_counts: Counter[tuple[str, str]] = Counter()
    positive_counts: Counter[tuple[str, str]] = Counter()
    negative_counts: Counter[tuple[str, str]] = Counter()
    class_images: Counter[int] = Counter()
    class_boxes: Counter[int] = Counter()
    for index, sample in enumerate(sorted(kept, key=lambda item: (item.split, item.source, item.key.casefold())), start=1):
        token = hashlib.sha256(sample.key.encode("utf-8")).hexdigest()[:20]
        ext = sample.image.suffix.lower()
        image_name = f"v1_{token}{ext}"
        label_name = f"v1_{token}.txt"
        staged_image = staging / "images" / sample.split / image_name
        staged_label = staging / "labels" / sample.split / label_name
        try:
            if sample.exif_orientation != 1:
                with Image.open(_io_path(sample.image)) as opened:
                    oriented = ImageOps.exif_transpose(opened)
                    oriented.save(staged_image, format="JPEG" if ext in {".jpg", ".jpeg"} else None)
                sample.link_mode = "exif_transposed_copy"
            else:
                # Keep the raw source immutable. YOLOv9's image loader may
                # repair malformed JPEGs in place; a hardlink here would let
                # that write-through alter the uploaded original dataset.
                shutil.copy2(_io_path(sample.image), _io_path(staged_image))
                sample.link_mode = "copy"
        except (OSError, ValueError):
            shutil.copy2(_io_path(sample.image), _io_path(staged_image))
            sample.link_mode = "copy"
        staged_label.write_text("".join(f"{cls} {x:.8f} {y:.8f} {w:.8f} {h:.8f}\n" for cls, x, y, w, h in sample.boxes), encoding="utf-8")
        source_counts[(sample.split, sample.source)] += 1
        if sample.boxes:
            positive_counts[(sample.split, sample.source)] += 1
            class_images.update({box[0] for box in sample.boxes})
            class_boxes.update(box[0] for box in sample.boxes)
        else:
            negative_counts[(sample.split, sample.source)] += 1
        final_image = cache_root / "images" / sample.split / image_name
        final_label = cache_root / "labels" / sample.split / label_name
        manifest_rows.append({
            "source": sample.source, "source_split": sample.source_split, "split": sample.split,
            "source_image": str(sample.image.resolve()), "source_label": str(sample.label.resolve()),
            "image": str(final_image.resolve()), "label": str(final_label.resolve()),
            "image_sha256": sample.image_sha256, "pixel_sha256": sample.pixel_sha256,
            "label_sha256": hashlib.sha256(staged_label.read_bytes()).hexdigest(),
            "image_width": sample.image_size[0], "image_height": sample.image_size[1],
            "exif_orientation": sample.exif_orientation, "link_mode": sample.link_mode,
            "classes": ",".join(str(box[0]) for box in sample.boxes), "box_count": len(sample.boxes),
            "negative": not bool(sample.boxes),
        })
        if index % 250 == 0 or index == len(kept):
            _REPORT_PROGRESS({"stage": "dataset_build", "completed": index, "total": len(kept), "message": f"建立只含 knife/gun 的安全训练副本 {index}/{len(kept)}"})

    data_yaml = staging / "data.yaml"
    data_yaml.write_text(
        f"path: {json.dumps(str(cache_root.resolve()), ensure_ascii=False)}\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n  0: knife\n  1: gun\n",
        encoding="utf-8",
    )
    with (staging / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(manifest_rows[0]))
        writer.writeheader()
        writer.writerows(manifest_rows)
    with (staging / "excluded_samples.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("source_sample", "reason"))
        writer.writeheader()
        writer.writerows(all_excluded)
    atomic_write_json(staging / "source_inventory.json", {"fingerprint": inventory_fingerprint, "files": inventory})

    by_split = Counter(row["split"] for row in manifest_rows)
    report = {
        "profile": PROFILE_ID, "policy_version": POLICY_VERSION, "fingerprint": fingerprint,
        "source_inventory_fingerprint": inventory_fingerprint,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cache_root": str(cache_root.resolve()), "data_yaml": str((cache_root / "data.yaml").resolve()),
        "manifest": str((cache_root / "manifest.csv").resolve()),
        "excluded_csv": str((cache_root / "excluded_samples.csv").resolve()),
        "source_inventory": str((cache_root / "source_inventory.json").resolve()),
        "class_names": list(CLASS_NAMES), "class_labels": list(CLASS_LABELS),
        "images": {split: by_split.get(split, 0) for split in ("train", "val", "test")},
        "total_images": len(manifest_rows),
        "class_counts": {
            name: {"name": name, "label": CLASS_LABELS[index], "images": class_images[index], "boxes": class_boxes[index]}
            for index, name in enumerate(CLASS_NAMES)
        },
        "positive_images": {f"{split}:{source}": count for (split, source), count in positive_counts.items()},
        "background_images": {f"{split}:{source}": count for (split, source), count in negative_counts.items()},
        "by_split_and_source": {f"{split}:{source}": count for (split, source), count in source_counts.items()},
        "selected_sources": sorted({row.source for row in kept}),
        "source_split_rules": source_rules,
        "duplicate_groups_removed": duplicate_report["duplicate_groups_removed"],
        "annotation_conflict_groups_excluded": duplicate_report["annotation_conflict_groups_excluded"],
        "duplicate_conflict_reasons": duplicate_report["conflict_reasons"],
        "excluded_samples": len(all_excluded),
        "excluded_reasons": dict(Counter(row["reason"] for row in all_excluded)),
        "v11_other_samples_dropped": sum(row["reason"] == "v11_other_class_sample_dropped" for row in all_excluded),
        "leakage_policy": "Exact file and decoded-pixel duplicates are grouped globally; conflicting labels exclude the whole group; otherwise test > val > train keeps a single copy.",
        "split_seed": SPLIT_SEED,
        "preview_samples": [
            {"image": row["source_image"], "source": row["source"], "split": row["split"], "boxes": next((sample.boxes for sample in kept if str(sample.image.resolve()) == row["source_image"]), []), "width": row["image_width"], "height": row["image_height"]}
            for row in manifest_rows[:120]
        ],
    }
    atomic_write_json(staging / "audit_report.json", report)
    cache_root.parent.mkdir(parents=True, exist_ok=True)
    if cache_root.exists():
        shutil.rmtree(staging)
        return json.loads((cache_root / "audit_report.json").read_text(encoding="utf-8"))
    os.replace(staging, cache_root)
    return report
