"""Build an immutable Knife/Gun training cache from the selected sources only."""

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

from yolov9_expanded_negative_finetune import (
    HARD_NEGATIVE_OUTPUT_ROOT,
    HARD_NEGATIVE_PROFILE_ID,
    MANIFEST_FIELDS,
    build_hard_negative_dataset,
)
from yolov9_weapon_workspace import PROJECT_ROOT


PROFILE_ID = "selected_sources_50epoch_v1"
OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "yolov9_selected_sources_50epoch"
SPLITS = ("train", "val", "test")
SELECTED_SOURCES = (
    "airt_lab_nonviolent",
    "bus_violence_nonfight",
    "dangerous_items",
    "knife_dataset",
    "long_gun_dataset",
    "mall_negative",
    "nogun",
    "od_sohas_detection",
    "shanghaitech_negative",
)
SOURCE_LABELS = {
    "airt_lab_nonviolent": "AIRTLab Non-Violent",
    "bus_violence_nonfight": "Bus Violence Non-Fight",
    "dangerous_items": "Dangerous Items",
    "knife_dataset": "Knife Dataset",
    "long_gun_dataset": "Long Gun Dataset",
    "mall_negative": "Mall Negative",
    "nogun": "NoGUN",
    "od_sohas_detection": "OD SOHAS",
    "shanghaitech_negative": "ShanghaiTech Negative",
}


def _read_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def latest_base_report(base_output_root: Path = HARD_NEGATIVE_OUTPUT_ROOT) -> dict[str, Any] | None:
    """Return the newest complete hard-negative cache report without rebuilding it."""
    datasets_root = Path(base_output_root) / "datasets"
    if not datasets_root.is_dir():
        return None
    reports = sorted(
        datasets_root.glob("*/audit_report.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in reports:
        report = _read_report(path)
        cache_root = Path(str(report.get("cache_root") or "")).resolve()
        manifest = Path(str(report.get("manifest") or "")).resolve()
        if (
            report.get("profile") == HARD_NEGATIVE_PROFILE_ID
            and cache_root.is_dir()
            and manifest.is_relative_to(cache_root)
            and manifest.is_file()
        ):
            return report
    return None


def _as_bool(value: Any) -> bool:
    return str(value).strip().casefold() in {"true", "1", "yes"}


def _source_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_split = {split: 0 for split in SPLITS}
    by_source: dict[str, dict[str, Any]] = {
        source: {
            "label": SOURCE_LABELS[source],
            "images": 0,
            "split_counts": {split: 0 for split in SPLITS},
            "knife_images": 0,
            "gun_images": 0,
            "negative_images": 0,
        }
        for source in SELECTED_SOURCES
    }
    class_images = {"knife": 0, "gun": 0}
    negative_split_counts = {split: 0 for split in SPLITS}
    for row in rows:
        source = str(row.get("source", ""))
        split = str(row.get("split", ""))
        if source not in by_source or split not in by_split:
            raise ValueError(f"Unexpected source or split in selected-source manifest: {source}/{split}")
        item = by_source[source]
        item["images"] += 1
        item["split_counts"][split] += 1
        by_split[split] += 1
        classes = set(filter(None, str(row.get("classes", "")).split(",")))
        for class_id, class_name in (("0", "knife"), ("1", "gun")):
            if class_id in classes:
                item[f"{class_name}_images"] += 1
                class_images[class_name] += 1
        if _as_bool(row.get("negative", "")):
            item["negative_images"] += 1
            negative_split_counts[split] += 1
    return {
        "images": by_split,
        "total_images": sum(by_split.values()),
        "class_counts": class_images,
        "negative_source_total": sum(negative_split_counts.values()),
        "negative_source_split_counts": negative_split_counts,
        "sources": by_source,
    }


def summarize_existing_sources(base_report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Summarize the selected source rows in the existing immutable cache, if available."""
    report = base_report or latest_base_report()
    if not report:
        return {"ready": False, "message": "No audited source cache is available yet."}
    manifest = Path(str(report.get("manifest") or "")).resolve()
    if not manifest.is_file():
        return {"ready": False, "message": "The audited source manifest is missing."}
    with manifest.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row.get("source") in SELECTED_SOURCES]
    present = {row.get("source") for row in rows}
    missing = sorted(set(SELECTED_SOURCES) - present)
    summary = _source_summary(rows)
    return {
        "ready": not missing and all(summary["images"].values()),
        "missing_sources": missing,
        "source_cache_fingerprint": report.get("fingerprint"),
        **summary,
    }


def _link_or_copy(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
        return "hardlink_from_audited_source_cache"
    except OSError:
        shutil.copy2(source, target)
        return "copy_from_audited_source_cache"


def _staging_path(cache_root: Path) -> Path:
    return cache_root.parent / f"stg_{uuid.uuid4().hex[:12]}"


def build_selected_source_dataset(
    *,
    output_root: Path = OUTPUT_ROOT,
    base_output_root: Path = HARD_NEGATIVE_OUTPUT_ROOT,
    base_report: dict[str, Any] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Filter the audited cache to the nine requested sources, preserving their splits."""
    base_report = base_report or latest_base_report(base_output_root)
    if base_report is None:
        base_report = build_hard_negative_dataset(output_root=base_output_root, progress=progress)
    if base_report.get("profile") != HARD_NEGATIVE_PROFILE_ID:
        raise ValueError("Selected-source training requires the audited BREV hard-negative source cache")

    base_root = Path(str(base_report.get("cache_root") or "")).resolve()
    base_manifest = Path(str(base_report.get("manifest") or "")).resolve()
    if not base_root.is_dir() or not base_manifest.is_relative_to(base_root) or not base_manifest.is_file():
        raise FileNotFoundError("The audited source manifest/cache is missing or outside its cache directory")
    with base_manifest.open("r", encoding="utf-8-sig", newline="") as stream:
        base_rows = list(csv.DictReader(stream))
    rows = [row for row in base_rows if row.get("source") in SELECTED_SOURCES]
    present = {row.get("source") for row in rows}
    missing = sorted(set(SELECTED_SOURCES) - present)
    if missing:
        raise ValueError(f"Required selected dataset sources are missing: {missing}")

    pixel_splits: dict[str, str] = {}
    digest = hashlib.sha256()
    digest.update(f"{PROFILE_ID}|base={base_report.get('fingerprint')}\n".encode("utf-8"))
    for row in rows:
        split = str(row.get("split", ""))
        if split not in SPLITS:
            raise ValueError(f"Invalid source split {split!r} for {row.get('source')}")
        image = Path(os.path.abspath(str(row.get("image", ""))))
        label = Path(os.path.abspath(str(row.get("label", ""))))
        if not image.is_relative_to(base_root) or not label.is_relative_to(base_root):
            raise ValueError("Audited manifest contains a sample outside its source cache")
        pixel_hash = str(row.get("pixel_sha256") or row.get("image_sha256") or "")
        if pixel_hash:
            previous_split = pixel_splits.setdefault(pixel_hash, split)
            if previous_split != split:
                raise ValueError(f"Duplicate image content crosses dataset splits: {pixel_hash}")
        digest.update("|".join((
            str(row.get("source", "")), split, str(row.get("image_sha256", "")),
            str(row.get("label_sha256", "")), str(row.get("classes", "")),
        )).encode("utf-8"))
        digest.update(b"\n")

    summary = _source_summary(rows)
    if any(summary["images"][split] <= 0 for split in SPLITS):
        raise ValueError(f"Each split must contain samples: {summary['images']}")
    fingerprint = digest.hexdigest()[:24]
    output_root = Path(output_root).resolve()
    cache_root = output_root / "datasets" / fingerprint
    report_path = cache_root / "audit_report.json"
    if report_path.is_file() and (cache_root / "data.yaml").is_file() and (cache_root / "manifest.csv").is_file():
        return _read_report(report_path)

    output_root.mkdir(parents=True, exist_ok=True)
    staging = _staging_path(cache_root)
    output_rows: list[dict[str, Any]] = []
    try:
        for split in SPLITS:
            (staging / "images" / split).mkdir(parents=True, exist_ok=True)
            (staging / "labels" / split).mkdir(parents=True, exist_ok=True)
        for index, row in enumerate(rows, start=1):
            split = str(row["split"])
            source_image = Path(os.path.abspath(row["image"]))
            source_label = Path(os.path.abspath(row["label"]))
            suffix = source_image.suffix.lower()
            name = f"{row['source']}__{source_image.stem}{suffix}"
            label_name = f"{row['source']}__{source_image.stem}.txt"
            target_image = staging / "images" / split / name
            target_label = staging / "labels" / split / label_name
            if target_image.exists() or target_label.exists():
                raise FileExistsError(f"Selected-source cache filename collision: {name}")
            image_mode = _link_or_copy(source_image, target_image)
            label_mode = _link_or_copy(source_label, target_label)
            copied = {key: row.get(key, "") for key in MANIFEST_FIELDS}
            copied["image"] = str((cache_root / "images" / split / name).resolve())
            copied["label"] = str((cache_root / "labels" / split / label_name).resolve())
            copied["link_mode"] = f"{image_mode};{label_mode}"
            output_rows.append(copied)
            if progress and (index % 1000 == 0 or index == len(rows)):
                progress({
                    "stage": "selected_sources_cache",
                    "completed": index,
                    "total": len(rows),
                    "message": f"复用所选来源缓存 {index}/{len(rows)}",
                })

        (staging / "data.yaml").write_text(
            f"path: {json.dumps(str(cache_root), ensure_ascii=False)}\n"
            "train: images/train\nval: images/val\ntest: images/test\n"
            "names:\n  0: knife\n  1: gun\n",
            encoding="utf-8",
        )
        with (staging / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(output_rows)

        excluded_csv = staging / "excluded_samples.csv"
        with excluded_csv.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("source", "reason"))
            for source in sorted(set(base_report.get("selected_sources", [])) - set(SELECTED_SOURCES)):
                writer.writerow((source, "Not selected for this experiment."))

        source_summaries = summary["sources"]
        negative_sources = {
            source: {
                "label": values["label"],
                "images": values["negative_images"],
                "split_counts": {
                    split: sum(
                        1 for row in rows
                        if row.get("source") == source
                        and row.get("split") == split
                        and _as_bool(row.get("negative", ""))
                    )
                    for split in SPLITS
                },
            }
            for source, values in source_summaries.items()
            if values["negative_images"] > 0
        }
        report = {
            "profile": PROFILE_ID,
            "policy_version": "selected-nine-sources-preserve-audited-splits-v1",
            "fingerprint": fingerprint,
            "base_dataset_fingerprint": base_report.get("fingerprint"),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "cache_root": str(cache_root.resolve()),
            "data_yaml": str((cache_root / "data.yaml").resolve()),
            "manifest": str((cache_root / "manifest.csv").resolve()),
            "excluded_csv": str((cache_root / "excluded_samples.csv").resolve()),
            "class_names": ["knife", "gun"],
            "class_labels": ["刀", "枪"],
            "images": summary["images"],
            "total_images": summary["total_images"],
            "class_counts": summary["class_counts"],
            "negative_source_total": summary["negative_source_total"],
            "negative_source_split_counts": summary["negative_source_split_counts"],
            "selected_sources": list(SELECTED_SOURCES),
            "excluded_sources": sorted(set(base_report.get("selected_sources", [])) - set(SELECTED_SOURCES)),
            "negative_sources": negative_sources,
            "source_summaries": source_summaries,
            "source_split_rules": {
                source: "Preserve the split assigned in the audited source manifest; do not reshuffle images or video-derived frames."
                for source in SELECTED_SOURCES
            },
            "split_recommendation": "Keep source-provided, video-grouped, or temporal-block splits intact to prevent near-duplicate frame leakage; resulting aggregate ratios need not equal 80:10:10.",
            "negative_label_assumption": base_report.get("negative_label_assumption"),
            "base_manifest": str(base_manifest),
            "preview_samples": [],
        }
        (staging / "audit_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        cache_root.parent.mkdir(parents=True, exist_ok=True)
        if cache_root.exists():
            shutil.rmtree(staging)
            return _read_report(cache_root / "audit_report.json")
        os.replace(staging, cache_root)
        return report
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

