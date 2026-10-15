"""Build an isolated V1 + Mall + expanded-negative YOLOv9 fine-tuning cache.

The prior Mall-negative cache remains immutable and supplies the exact curated
V1 and Mall split used by the previous fine-tune recipe. AIRTLab NonViolent,
Bus Violence NonFight, and the remaining NoGUN images are appended as
weapon-negative samples with empty two-class labels.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from yolov9_mall_finetune import (
    IMAGE_SUFFIXES,
    _pixel_info,
    _progress,
    _sha256_file,
    assign_mall_splits,
    build_finetune_dataset,
    inspect_mall_source,
)
from yolov9_weapon_workspace import MALL_FINETUNE_OUTPUT_ROOT, PROJECT_ROOT


PROFILE_ID = "expanded_negative_finetune_v1"
HARD_NEGATIVE_PROFILE_ID = "brev_hard_negative_finetune_v1"
DATA_ROOT = PROJECT_ROOT / "Weapon Detection Model Dataset"
OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "yolov9_mall_negative_finetune"
HARD_NEGATIVE_OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "yolov9_brev_hard_negative_finetune"
EXCLUDED_NOGUN_IMAGE = "tools_in_hand_002621_jpg.rf.25b39f4128624411dd22d3b2858ffd2a.jpg"
SOURCES = {
    "airt_lab_nonviolent": {
        "folder": "AIRTLab_NonViolent",
        "expected_split_counts": {"train": 508, "val": 58, "test": 74},
        "empty_source_labels": True,
    },
    "bus_violence_nonfight": {
        "folder": "BusViolence_NonFight",
        "expected_split_counts": {"train": 560, "val": 70, "test": 70},
        "empty_source_labels": True,
    },
    "nogun": {
        "folder": "NoGUN.v5i.yolov9",
        "expected_split_counts": {"train": 2828, "val": 88, "test": 30},
        "empty_source_labels": False,
    },
}
SPLIT_DIRECTORIES = {
    "airt_lab_nonviolent": {"train": "train", "val": "val", "test": "test"},
    "bus_violence_nonfight": {"train": "train", "val": "val", "test": "test"},
    "nogun": {"train": "train", "val": "valid", "test": "test"},
}
MANIFEST_FIELDS = (
    "source", "source_split", "split", "source_image", "source_label", "image", "label",
    "image_sha256", "pixel_sha256", "label_sha256", "image_width", "image_height",
    "exif_orientation", "link_mode", "classes", "box_count", "negative",
)


def _matching_files(directory: Path, suffixes: set[str]) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Dataset folder not found: {directory}")
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in suffixes),
        key=lambda path: path.name.casefold(),
    )


def inspect_negative_sources(
    *,
    data_root: Path = DATA_ROOT,
    mall_frames_root: Path | None = None,
    expected_source_counts: dict[str, dict[str, int]] | None = None,
    expected_mall_images: int = 2000,
    mall_block_size: int = 20,
    seed: int = 2026,
) -> dict[str, Any]:
    """Fast source readiness/count check, with strict image/label pairing."""
    data_root = Path(data_root)
    if mall_frames_root is None:
        mall = inspect_mall_source()
    else:
        frames_root = Path(mall_frames_root)
        frames = _matching_files(frames_root, IMAGE_SUFFIXES)
        if expected_mall_images is not None and len(frames) != expected_mall_images:
            raise ValueError(f"Expected {expected_mall_images} Mall images, found {len(frames)}")
        assigned = assign_mall_splits(
            frames, expected_count=expected_mall_images, block_size=mall_block_size, seed=seed,
        )
        split_counts = {split: sum(value == split for value in assigned.values()) for split in ("train", "val", "test")}
        mall = {"ready": True, "frames_root": str(frames_root.resolve()), "images": len(frames), "split_counts": split_counts}
    if expected_mall_images is not None and mall["images"] != expected_mall_images:
        raise ValueError(f"Expected {expected_mall_images} Mall images, found {mall['images']}")

    expected = expected_source_counts if expected_source_counts is not None else {
        source_id: value["expected_split_counts"] for source_id, value in SOURCES.items()
    }
    source_summaries: dict[str, dict[str, Any]] = {
        "mall_negative": {
            "images": mall["images"],
            "split_counts": mall["split_counts"],
            "folder": mall["frames_root"],
            "labels": "created empty Knife/Gun labels",
        }
    }
    for source_id, definition in SOURCES.items():
        source_root = data_root / definition["folder"]
        split_counts: dict[str, int] = {}
        file_counts: dict[str, int] = {}
        for split, source_split in SPLIT_DIRECTORIES[source_id].items():
            images_dir = source_root / source_split / "images"
            labels_dir = source_root / source_split / "labels"
            images = _matching_files(images_dir, IMAGE_SUFFIXES)
            if source_id == "nogun" and split == "train":
                images = [image for image in images if image.name.casefold() != EXCLUDED_NOGUN_IMAGE.casefold()]
            labels = _matching_files(labels_dir, {".txt"})
            if source_id == "nogun" and split == "train":
                excluded_label = Path(EXCLUDED_NOGUN_IMAGE).stem.casefold()
                labels = [label for label in labels if label.stem.casefold() != excluded_label]
            image_stems = {image.stem.casefold() for image in images}
            label_stems = {label.stem.casefold() for label in labels}
            if image_stems != label_stems:
                missing_labels = sorted(image_stems - label_stems)[:3]
                extra_labels = sorted(label_stems - image_stems)[:3]
                raise ValueError(
                    f"{source_id}/{split} image-label pairing mismatch; "
                    f"missing labels={missing_labels}, extra labels={extra_labels}"
                )
            if definition["empty_source_labels"]:
                nonempty = [label.name for label in labels if label.read_text(encoding="utf-8-sig").strip()]
                if nonempty:
                    raise ValueError(f"Expected empty negative labels in {source_id}/{split}; found {nonempty[:3]}")
            split_counts[split] = len(images)
            file_counts[split] = len(labels)
        if source_id in expected and split_counts != expected[source_id]:
            raise ValueError(f"Unexpected {source_id} split counts: {split_counts}; expected {expected[source_id]}")
        source_summaries[source_id] = {
            "images": sum(split_counts.values()),
            "split_counts": split_counts,
            "folder": str(source_root.resolve()),
            "labels": "source labels validated as empty" if definition["empty_source_labels"] else "source NoGUN labels ignored; all remaining images become weapon-negative",
        }

    negative_split_counts = {split: 0 for split in ("train", "val", "test")}
    for summary in source_summaries.values():
        for split in negative_split_counts:
            negative_split_counts[split] += int(summary["split_counts"].get(split, 0))
    return {
        "ready": True,
        "sources": source_summaries,
        "negative_images": sum(item["images"] for item in source_summaries.values()),
        "negative_split_counts": negative_split_counts,
        "excluded_samples": [{
            "source": "nogun",
            "source_split": "train",
            "file": EXCLUDED_NOGUN_IMAGE,
            "reason": "Confirmed true knife; removed from the source dataset and explicitly excluded from this recipe.",
        }],
        "source_root": str(data_root.resolve()),
    }


def _collect_source_inventory(
    data_root: Path,
    *,
    progress: Callable[[dict[str, Any]], None] | None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    inventory: list[dict[str, Any]] = []
    source_items: dict[str, list[dict[str, Any]]] = {}
    total = sum(
        sum(value["expected_split_counts"].values()) for value in SOURCES.values()
    )
    completed = 0
    for source_id, definition in SOURCES.items():
        source_items[source_id] = []
        source_root = data_root / definition["folder"]
        for split, source_split in SPLIT_DIRECTORIES[source_id].items():
            images_dir = source_root / source_split / "images"
            labels_dir = source_root / source_split / "labels"
            images = _matching_files(images_dir, IMAGE_SUFFIXES)
            if source_id == "nogun" and split == "train":
                images = [image for image in images if image.name.casefold() != EXCLUDED_NOGUN_IMAGE.casefold()]
                excluded_label = Path(EXCLUDED_NOGUN_IMAGE).stem.casefold()
                labels = _matching_files(labels_dir, {".txt"})
                labels = [label for label in labels if label.stem.casefold() != excluded_label]
            else:
                labels = _matching_files(labels_dir, {".txt"})
            for image in images:
                label = labels_dir / f"{image.stem}.txt"
                if not label.is_file():
                    raise FileNotFoundError(f"Matching label missing for negative image: {image}")
                if definition["empty_source_labels"] and label.read_text(encoding="utf-8-sig").strip():
                    raise ValueError(f"Expected an empty negative label: {label}")
                with Image.open(image) as opened:
                    opened.verify()
                pixel_hash, size, orientation = _pixel_info(image)
                entry = {
                    "source": source_id,
                    "source_split": source_split,
                    "split": split,
                    "path": str(image.resolve()),
                    "label_path": str(label.resolve()),
                    "size": image.stat().st_size,
                    "mtime_ns": image.stat().st_mtime_ns,
                    "image_sha256": _sha256_file(image),
                    "pixel_sha256": pixel_hash,
                    "image_size": size,
                    "exif_orientation": orientation,
                }
                inventory.append(entry)
                source_items[source_id].append(entry)
                completed += 1
                if completed % 250 == 0 or completed == total:
                    _progress(progress, "expanded_negative_audit", completed, total, f"审计新增负样本 {completed}/{total}")
    return inventory, source_items


def _latest_previous_mall_report(output_root: Path) -> dict[str, Any] | None:
    datasets_root = Path(output_root) / "datasets"
    if not datasets_root.is_dir():
        return None
    reports = sorted(
        datasets_root.glob("*/audit_report.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for report_path in reports:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cache_root = Path(str(report.get("cache_root", ""))).resolve()
        manifest = Path(str(report.get("manifest", ""))).resolve()
        if (
            report.get("profile") == "mall_negative_finetune_v1"
            and report.get("total_images")
            and cache_root == report_path.parent.resolve()
            and manifest.is_relative_to(cache_root)
            and manifest.is_file()
            and (cache_root / "data.yaml").is_file()
        ):
            return report
    return None


def _reuse_prior_cache_file(source: Path, target: Path) -> str:
    """Reuse immutable prior-cache files locally, falling back to a physical copy."""
    try:
        os.link(source, target)
        return "hardlink_previous_mall_cache"
    except OSError:
        try:
            shutil.copy2(source, target)
            return "copy_previous_mall_cache"
        except OSError as exc:
            raise OSError(f"Could not reuse prior Mall-cache file {source} at {target}: {exc}") from exc


def _staging_path(cache_root: Path) -> Path:
    """Use a short temporary folder name so Windows image paths stay below MAX_PATH."""
    return cache_root.parent / f"stg_{uuid.uuid4().hex[:12]}"


def build_expanded_negative_dataset(
    *,
    output_root: Path = OUTPUT_ROOT,
    mall_output_root: Path = MALL_FINETUNE_OUTPUT_ROOT,
    data_root: Path = DATA_ROOT,
    mall_frames_root: Path | None = None,
    expected_source_counts: dict[str, dict[str, int]] | None = None,
    expected_mall_images: int = 2000,
    block_size: int = 20,
    seed: int = 2026,
    progress: Callable[[dict[str, Any]], None] | None = None,
    mall_report: dict[str, Any] | None = None,
    profile_id: str = PROFILE_ID,
    exclude_v11_empty_labels: bool = False,
) -> dict[str, Any]:
    """Build a new cache by extending the previously audited Mall fine-tune."""
    source_summary = inspect_negative_sources(
        data_root=data_root,
        mall_frames_root=mall_frames_root,
        expected_source_counts=expected_source_counts,
        expected_mall_images=expected_mall_images,
        mall_block_size=block_size,
        seed=seed,
    )
    if mall_report is None:
        mall_report = _latest_previous_mall_report(Path(mall_output_root))
        if mall_report is None:
            mall_report = build_finetune_dataset(
                output_root=mall_output_root,
                base_output_root=PROJECT_ROOT / "model_runs" / "yolov9_full_dataset_v1",
                progress=progress,
                expected_mall_images=expected_mall_images,
                block_size=block_size,
                seed=seed,
            )
    mall_root = Path(mall_report["cache_root"]).resolve()
    mall_manifest = Path(mall_report["manifest"]).resolve()
    if not mall_root.is_dir() or not mall_manifest.is_file():
        raise FileNotFoundError("The audited previous Mall fine-tune cache is unavailable")
    if mall_report.get("profile") != "mall_negative_finetune_v1":
        raise ValueError("Expanded-negative fine-tuning must start from the audited Mall-negative dataset version")

    inventory, source_items = _collect_source_inventory(Path(data_root), progress=progress)
    digest = hashlib.sha256()
    exclusion_policy = "drop-v11-empty-labels-v1" if exclude_v11_empty_labels else "keep-v11-empty-labels-v1"
    digest.update(f"{profile_id}|mall={mall_report['fingerprint']}|empty-labels-v1|{exclusion_policy}|{seed}|{block_size}\n".encode("utf-8"))
    for item in inventory:
        digest.update(json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    fingerprint = digest.hexdigest()[:24]
    output_root = Path(output_root).resolve()
    cache_root = output_root / "datasets" / fingerprint
    report_path = cache_root / "audit_report.json"
    if report_path.is_file() and (cache_root / "data.yaml").is_file() and (cache_root / "manifest.csv").is_file():
        return json.loads(report_path.read_text(encoding="utf-8"))

    output_root.mkdir(parents=True, exist_ok=True)
    staging = _staging_path(cache_root)
    rows: list[dict[str, Any]] = []
    try:
        for split in ("train", "val", "test"):
            (staging / "images" / split).mkdir(parents=True, exist_ok=True)
            (staging / "labels" / split).mkdir(parents=True, exist_ok=True)

        with mall_manifest.open("r", encoding="utf-8-sig", newline="") as stream:
            mall_rows = list(csv.DictReader(stream))
        if not mall_rows:
            raise ValueError("The audited Mall fine-tune manifest is empty")
        excluded_v11_by_split = {split: 0 for split in ("train", "val", "test")}
        excluded_v11_rows: list[dict[str, str]] = []
        for index, row in enumerate(mall_rows, start=1):
            split = row.get("split", "")
            if split not in {"train", "val", "test"}:
                raise ValueError(f"Invalid Mall-cache dataset split: {split!r}")
            if (
                exclude_v11_empty_labels
                and row.get("source") == "weapon_dataset_v11"
                and int(row.get("box_count") or 0) == 0
            ):
                excluded_v11_by_split[split] += 1
                excluded_v11_rows.append({"source_sample": row.get("source_image", ""), "reason": "V11 empty-labelled sample; excluded from this hard-negative fine-tuning dataset."})
                continue
            source_image = Path(row["image"]).resolve()
            source_label = Path(row["label"]).resolve()
            if not source_image.is_relative_to(mall_root) or not source_label.is_relative_to(mall_root):
                raise ValueError("Mall manifest contains a file outside its audited cache")
            if not source_image.is_file() or not source_label.is_file():
                raise FileNotFoundError(f"Mall dataset file is missing: {source_image if not source_image.is_file() else source_label}")
            target_image = staging / "images" / split / source_image.name
            target_label = staging / "labels" / split / source_label.name
            image_link_mode = _reuse_prior_cache_file(source_image, target_image)
            label_link_mode = _reuse_prior_cache_file(source_label, target_label)
            copied = {key: row.get(key, "") for key in MANIFEST_FIELDS}
            copied["image"] = str((cache_root / "images" / split / target_image.name).resolve())
            copied["label"] = str((cache_root / "labels" / split / target_label.name).resolve())
            copied["link_mode"] = f"{image_link_mode};{label_link_mode}"
            rows.append(copied)
            if index % 1000 == 0 or index == len(mall_rows):
                _progress(progress, "expanded_negative_build", index, len(mall_rows) + len(inventory), f"沿用 Mall/V1 已审计样本 {index}/{len(mall_rows)}")

        added_counts: dict[str, dict[str, int]] = {}
        for source_id, entries in source_items.items():
            added_counts[source_id] = {split: 0 for split in ("train", "val", "test")}
            for entry in entries:
                image = Path(entry["path"])
                split = entry["split"]
                stem = f"{source_id}__{entry['source_split']}__{image.stem}"
                image_name = f"{stem}{image.suffix.lower()}"
                label_name = f"{stem}.txt"
                target_image = staging / "images" / split / image_name
                target_label = staging / "labels" / split / label_name
                if target_image.exists() or target_label.exists():
                    raise FileExistsError(f"Expanded negative cache filename collision: {stem}")
                try:
                    shutil.copy2(image, target_image)
                except OSError as exc:
                    raise OSError(f"Could not copy negative source image {image} to {target_image}: {exc}") from exc
                target_label.write_text("", encoding="utf-8")
                width, height = entry["image_size"]
                rows.append({
                    "source": source_id,
                    "source_split": entry["source_split"],
                    "split": split,
                    "source_image": entry["path"],
                    "source_label": entry["label_path"],
                    "image": str((cache_root / "images" / split / image_name).resolve()),
                    "label": str((cache_root / "labels" / split / label_name).resolve()),
                    "image_sha256": entry["image_sha256"],
                    "pixel_sha256": entry["pixel_sha256"],
                    "label_sha256": hashlib.sha256(b"").hexdigest(),
                    "image_width": width,
                    "image_height": height,
                    "exif_orientation": entry["exif_orientation"],
                    "link_mode": "copy_with_empty_weapon_label",
                    "classes": "",
                    "box_count": 0,
                    "negative": True,
                })
                added_counts[source_id][split] += 1

        (staging / "data.yaml").write_text(
            f"path: {json.dumps(str(cache_root), ensure_ascii=False)}\n"
            "train: images/train\nval: images/val\ntest: images/test\n"
            "names:\n  0: knife\n  1: gun\n",
            encoding="utf-8",
        )
        with (staging / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

        base_counts = mall_report.get("images", {})
        negative_counts = {
            split: sum(
                str(row.get("negative", "")).strip().casefold() in {"true", "1", "yes"}
                for row in rows
                if row.get("split") == split
            )
            for split in ("train", "val", "test")
        }
        final_counts = {split: int(base_counts.get(split, 0)) - excluded_v11_by_split[split] + sum(int(added_counts[source_id].get(split, 0)) for source_id in added_counts) for split in ("train", "val", "test")}
        if sum(final_counts.values()) != len(rows):
            raise AssertionError(f"Final split counts do not sum to manifest rows: {final_counts} vs {len(rows)}")

        inventory_payload = {
            "profile": profile_id,
            "mall_dataset_fingerprint": mall_report["fingerprint"],
            "sources": inventory,
            "excluded_samples": source_summary["excluded_samples"],
            "v11_empty_label_excluded_by_split": excluded_v11_by_split,
            "negative_split_counts_including_mall": negative_counts,
        }
        (staging / "source_inventory.json").write_text(json.dumps(inventory_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        exclusions = staging / "excluded_samples.csv"
        with exclusions.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("source_sample", "reason"))
            previous_exclusions = Path(mall_report.get("excluded_csv", ""))
            if previous_exclusions.is_file():
                with previous_exclusions.open("r", encoding="utf-8-sig", newline="") as prior_stream:
                    prior_rows = list(csv.DictReader(prior_stream))
                for prior in prior_rows:
                    writer.writerow((prior.get("source_sample", ""), prior.get("reason", "")))
            for excluded in excluded_v11_rows:
                writer.writerow((excluded["source_sample"], excluded["reason"]))
            writer.writerow((f"nogun/train/images/{EXCLUDED_NOGUN_IMAGE}", "Confirmed true knife; removed from NoGUN source and excluded from negative training."))

        source_summary["sources"]["mall_negative"]["source_id"] = "mall_negative"
        for source_id, counts in added_counts.items():
            source_summary["sources"][source_id]["split_counts"] = counts
        total_negative = sum(negative_counts.values())
        report = {
            "profile": profile_id,
            "policy_version": "brev-hard-negative-v1-drop-v11-empty-labels-mall-cache-plus-airtlab-bus-nogun" if exclude_v11_empty_labels else "expanded-negative-v1-mall-cache-plus-airtlab-bus-nogun",
            "fingerprint": fingerprint,
            "base_dataset_fingerprint": mall_report["fingerprint"],
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "cache_root": str(cache_root.resolve()),
            "data_yaml": str((cache_root / "data.yaml").resolve()),
            "manifest": str((cache_root / "manifest.csv").resolve()),
            "excluded_csv": str((cache_root / "excluded_samples.csv").resolve()),
            "source_inventory": str((cache_root / "source_inventory.json").resolve()),
            "class_names": ["knife", "gun"],
            "class_labels": ["刀", "枪"],
            "images": final_counts,
            "total_images": len(rows),
            "class_counts": mall_report.get("class_counts", {}),
            "selected_sources": sorted(set(mall_report.get("selected_sources", [])) | set(SOURCES)),
            "negative_source_total": total_negative,
            "negative_source_split_counts": negative_counts,
            "v11_empty_label_excluded": {
                "by_split": excluded_v11_by_split,
                "total": sum(excluded_v11_by_split.values()),
                "source": "weapon_dataset_v11",
            },
            "negative_sources": source_summary["sources"],
            "source_split_rules": {
                "curated_full_dataset_v1": "Retain its existing audited train/val/test splits unchanged via the previous Mall cache.",
                "mall_negative": "Retain the previous Mall-negative cache and its deterministic temporal-block split unchanged.",
                "airt_lab_nonviolent": "Use source train/val/test directories; verify camera/action pairs stay assigned to the source's existing split; all labels are empty.",
                "bus_violence_nonfight": "Use the source train/val/test directories unchanged; all labels are empty.",
                "nogun": "Map source valid to val; retain source train/valid/test assignments; ignore its NoGUN class annotations and write empty Knife/Gun labels for every remaining image.",
            },
            "negative_label_assumption": "Mall, AIRTLab NonViolent, BusViolence NonFight, and remaining NoGUN images are treated as containing no Knife/Gun objects. The confirmed true-knife NoGUN image was removed and is explicitly excluded.",
            "excluded_samples": int(mall_report.get("excluded_samples", 0)) + 1 + sum(excluded_v11_by_split.values()),
            "confirmed_knife_excluded": {
                "source": "nogun",
                "source_split": "train",
                "file": EXCLUDED_NOGUN_IMAGE,
                "sha256": "6cde825300c9d13ae1513fbf8b247835e88d6612ca381183dfdbabad706435e0",
            },
            "base_dataset_report": str((mall_root / "audit_report.json").resolve()),
            "mall_dataset_fingerprint": mall_report["fingerprint"],
            "mall_negative_split": (mall_report.get("mall_split") or {}).get("images", {}),
            "added_negative_source_split_counts": added_counts,
            "v11_other_samples_dropped": mall_report.get("v11_other_samples_dropped", 0),
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


def build_hard_negative_dataset(**kwargs: Any) -> dict[str, Any]:
    """Build the isolated BREV recipe, excluding all V11 empty-label rows."""
    kwargs.setdefault("output_root", HARD_NEGATIVE_OUTPUT_ROOT)
    kwargs["profile_id"] = HARD_NEGATIVE_PROFILE_ID
    kwargs["exclude_v11_empty_labels"] = True
    return build_expanded_negative_dataset(**kwargs)
