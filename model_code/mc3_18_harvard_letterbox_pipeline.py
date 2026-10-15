"""Fresh Kinetics-400 MC3-18 training with full-frame RGB-mean letterboxing.

Harvard Dataverse uses a deterministic class-stratified 80:10:10 split. RWF-
2000 and AIRT Lab keep the exact eligible assignments in the audited 4S16F2
split manifest. The native MC3-18 training engine supplies epoch-boundary
pause/resume, per-epoch checkpoints, validation threshold calibration, and
final held-out testing.
"""

from __future__ import annotations

import csv
import hashlib
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

import mc3_18_four_second_pipeline as four
import mc3_18_mil_pipeline as core
import mc3_18_native_finetune_pipeline as native


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "GuardAId Video Classification Model Dataset"
HARVARD_ROOT = DATA_ROOT / "Harvard Dataverse"
BASE_RUN = PROJECT_ROOT / "model_runs" / "mc3_4s16f2" / "run_four_ui_20261007_234526_491833"
BASE_MANIFEST = BASE_RUN / "dataset_manifest.csv"
OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_harvard_letterbox_3dataset_4s16f2_k400"
CACHE_PROTOCOL = "mc3-4s-2s-stride-16f-shortside128-raw-letterbox160-v1"
EXPERIMENT_PROTOCOL = "mc3-harvard-rwf-airtlab-letterbox160-4s16f2-k400-v2"
INPUT_REPRESENTATION = "native_video_letterbox_160x160_rgb_mean"
INPUT_SIZE = 160
DATASET_NAMES = ("Harvard Dataverse", "RWF-2000", "AIRTLab")
KINETICS_INITIALIZATION = {
    "kind": "torchvision_mc3_18_kinetics400",
    "weights": "MC3_18_Weights.KINETICS400_V1",
    "url": "https://download.pytorch.org/models/mc3_18-a90a0ba3.pth",
    "classifier": "Kinetics-400 pretrained backbone; replace classifier with a fresh Fight/NonFight head",
}
SPLITS = ("train", "val", "test")
VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv"}
EXPECTED_COUNTS = {"Harvard Dataverse": {"Fight": 616, "NonFight": 1510},
                  "RWF-2000": {"Fight": 981, "NonFight": 992},
                  "AIRTLab": {"Fight": 230, "NonFight": 120}}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _split_targets(count: int) -> dict[str, int]:
    raw = {"train": count * 0.8, "val": count * 0.1, "test": count * 0.1}
    targets = {name: math.floor(raw[name]) for name in SPLITS}
    remainder = count - sum(targets.values())
    for name in sorted(SPLITS, key=lambda item: (-(raw[item] - targets[item]), SPLITS.index(item)))[:remainder]:
        targets[name] += 1
    return targets


def stratified_random_splits(paths: Sequence[Path], seed: int) -> dict[Path, str]:
    """Shuffle a class's sorted paths using the user-visible fixed seed."""
    ordered = sorted((Path(path) for path in paths), key=lambda item: item.as_posix().casefold())
    random.Random(seed).shuffle(ordered)
    targets = _split_targets(len(ordered))
    train_end = targets["train"]
    val_end = train_end + targets["val"]
    return {path: "train" if index < train_end else "val" if index < val_end else "test"
            for index, path in enumerate(ordered)}


def experiment_protocol() -> dict[str, Any]:
    base_splits = {"source_manifest": str(BASE_MANIFEST.resolve()),
                   "source_manifest_sha256": hashlib.sha256(BASE_MANIFEST.read_bytes()).hexdigest()
                   if BASE_MANIFEST.is_file() else None}
    return {
        "protocol": EXPERIMENT_PROTOCOL,
        "experiment_protocol": EXPERIMENT_PROTOCOL,
        "input_modality": "native",
        "input_representation": INPUT_REPRESENTATION,
        "window_seconds": four.WINDOW_SECONDS,
        "stride_seconds": four.STRIDE_SECONDS,
        "frames_per_window": four.NUM_FRAMES,
        "sampling_rate_fps": four.NUM_FRAMES / four.WINDOW_SECONDS,
        "frame_sample_positions": [round((index + 0.5) / four.NUM_FRAMES, 6)
                                   for index in range(four.NUM_FRAMES)],
        "input_size": [INPUT_SIZE, INPUT_SIZE],
        "short_side": None,
        "crop_size": None,
        "resize_policy": "scale the complete RGB frame to fit 160x160; preserve aspect ratio; center-pad to square",
        "letterbox": {
            "target_width": INPUT_SIZE,
            "target_height": INPUT_SIZE,
            "scale": "min(160 / source_width, 160 / source_height)",
            "alignment": "center",
            "padding_color_rgb_0_1": list(core.MEAN),
            "padding_after_normalization": "exact zero in each channel",
            "crop": False,
        },
        "batch_spatial_policy": "every window is letterboxed to the same 160x160 tensor shape",
        "color_space": "RGB",
        "normalization_mean": list(core.MEAN),
        "normalization_std": list(core.STD),
        "short_clip_policy": {
            "duration_below_seconds": four.WINDOW_SECONDS,
            "operation": "sample 16 frame centres uniformly over the real clip duration; do not reverse or pad",
            "minimum_duration_seconds": four.MIN_SCAN_SECONDS,
        },
        "tail_policy": "full 4-second windows at 2-second stride, plus one end-aligned tail when needed",
        "video_aggregation": "maximum_window_probability",
        "selected_datasets": list(DATASET_NAMES),
        "initialization": dict(KINETICS_INITIALIZATION),
        "source_roots": {
            "dataset_root": str(DATA_ROOT.resolve()),
            "harvard_dataverse": str(HARVARD_ROOT.resolve()),
            "split_manifest": str(BASE_MANIFEST.resolve()),
        },
        "split_policy": {
            "Harvard Dataverse": "deterministic class-stratified random 80:10:10; seed supplied in run config",
            "RWF-2000": "exact eligible train/val/test assignments from the audited 4S16F2 Best manifest",
            "AIRTLab": "exact eligible train/val/test assignments from the audited 4S16F2 Best manifest; camera-paired views remain grouped",
        },
        "cache_policy": {
            "RWF-2000_and_AIRTLab": "reuse exact matching 4S16F2 raw RGB sample-frame caches read-only where present; cache misses go to this experiment's separate cache",
            "Harvard_Dataverse": "decode only the 4S16F2 selected frame indices and retain them in this experiment's cache",
            "cached_frame_representation": "uncropped RGB, 128-pixel short side; letterbox is applied in memory",
            "source_cache_is_read_only": True,
            "cache_protocol": CACHE_PROTOCOL,
        },
    }


def _manifest_counts(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    datasets: dict[str, Any] = {}
    for dataset in DATASET_NAMES:
        by_split = {split: {"Fight": 0, "NonFight": 0, "bags": 0} for split in SPLITS}
        for row in rows:
            if row.get("dataset") != dataset or str(row.get("eligible", 1)) not in {"1", "True"}:
                continue
            split, class_name = str(row.get("split", "")), str(row.get("class_name", ""))
            if split in SPLITS and class_name in {"Fight", "NonFight"}:
                by_split[split][class_name] += 1
                by_split[split]["bags"] += 1
        by_split["total_bags"] = sum(by_split[split]["bags"] for split in SPLITS)
        datasets[dataset] = by_split
    overall = {split: {"Fight": 0, "NonFight": 0, "bags": 0} for split in SPLITS}
    for values in datasets.values():
        for split in SPLITS:
            for key in overall[split]:
                overall[split][key] += values[split][key]
    return {"datasets": datasets, "overall": overall,
            "total_bags": sum(overall[split]["bags"] for split in SPLITS)}


def quick_inventory(*, dataset_root: Path = DATA_ROOT, base_manifest: Path = BASE_MANIFEST,
                    seed: int = 2026) -> dict[str, Any]:
    """Cheap file/manifest preflight; no video decode, cache write, or training."""
    dataset_root = Path(dataset_root).expanduser().resolve()
    harvard_root = dataset_root / "Harvard Dataverse"
    if not Path(base_manifest).is_file():
        raise FileNotFoundError(f"The audited 4S16F2 split manifest is missing: {base_manifest}")
    rows = [row for row in _read_csv(Path(base_manifest))
            if row.get("dataset") in {"RWF-2000", "AIRTLab"}]
    base_eligible = [row for row in rows if row.get("eligible") == "1"]
    for dataset in ("RWF-2000", "AIRTLab"):
        counts = {name: sum(row.get("dataset") == dataset and row.get("class_name") == name
                            for row in base_eligible) for name in ("Fight", "NonFight")}
        if counts != EXPECTED_COUNTS[dataset]:
            raise RuntimeError(f"Audited {dataset} eligible counts differ from the expected split manifest: {counts}")
    missing = [Path(row.get("path", "")) for row in base_eligible if not Path(row.get("path", "")).is_file()]

    harvard_rows: list[dict[str, Any]] = []
    harvard_counts: dict[str, int] = {}
    for folder_name, class_name in (("Violence", "Fight"), ("NonViolence", "NonFight")):
        class_root = harvard_root / folder_name
        if not class_root.is_dir():
            raise FileNotFoundError(f"Harvard Dataverse class directory is missing: {class_root}")
        paths = sorted((path for path in class_root.rglob("*")
                        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES),
                       key=lambda path: path.as_posix().casefold())
        harvard_counts[class_name] = len(paths)
        if len(paths) != EXPECTED_COUNTS["Harvard Dataverse"][class_name]:
            raise RuntimeError(f"Harvard Dataverse {class_name} file count changed: {len(paths)}; expected "
                               f"{EXPECTED_COUNTS['Harvard Dataverse'][class_name]}.")
        split_map = stratified_random_splits(paths, seed + (1 if class_name == "Fight" else 0))
        for path in paths:
            harvard_rows.append({"dataset": "Harvard Dataverse", "split": split_map[path],
                                 "class_name": class_name, "label": native.SOURCE_LABELS[class_name],
                                 "path": str(path.resolve()), "eligible": 1})
    rows.extend(harvard_rows)
    summary = _manifest_counts(rows)
    expected_total = sum(sum(classes.values()) for classes in EXPECTED_COUNTS.values())
    return {
        "ready": (len(base_eligible) == 2323 and len(harvard_rows) == 2126
                  and not missing and summary["total_bags"] == expected_total),
        "missing_source_count": len(missing),
        "first_missing_sources": [str(path) for path in missing[:5]],
        "source_counts": {key: dict(value) for key, value in EXPECTED_COUNTS.items()},
        "summary": summary,
        "selected_datasets": list(DATASET_NAMES),
        "initialization": dict(KINETICS_INITIALIZATION),
        "base_manifest": str(Path(base_manifest).expanduser().resolve()),
        "base_manifest_sha256": hashlib.sha256(Path(base_manifest).read_bytes()).hexdigest(),
        "split_policy": experiment_protocol()["split_policy"],
        "seed": seed,
        "protocol": EXPERIMENT_PROTOCOL,
        "output_root": str(OUTPUT_ROOT.resolve()),
        "cache_dir": str((OUTPUT_ROOT / "_frame_cache" / CACHE_PROTOCOL).resolve()),
        "cache_note": "RWF/AIRT matching 4S16F2 cache entries are read-only reused; Harvard's selected frames are cached separately.",
    }


def _summary(records: Sequence[core.VideoRecord], manifest_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    summary = _manifest_counts(manifest_rows)
    for dataset in DATASET_NAMES:
        for split in SPLITS:
            summary["datasets"][dataset][split]["windows"] = sum(
                len(four.make_four_second_windows(record.duration, record.fps, record.frame_count))
                for record in records if record.dataset == dataset and record.split == split)
    for split in SPLITS:
        summary["overall"][split]["windows"] = sum(
            summary["datasets"][dataset][split]["windows"] for dataset in DATASET_NAMES)
    return summary


def load_records(*, dataset_root: Path = DATA_ROOT, base_manifest: Path = BASE_MANIFEST,
                 seed: int = 2026, cv2: Any | None = None, logger=None,
                 validate_dance_frames: bool = False, progress_callback=None,
                 dancetrack_manifest: Path | None = None):
    """Load RWF/AIRT from the audited split manifest and seed-split Harvard."""
    del validate_dance_frames, dancetrack_manifest
    if cv2 is None:
        import cv2 as cv2_module
        cv2 = cv2_module
    dataset_root = Path(dataset_root).expanduser().resolve()
    base_manifest = Path(base_manifest).expanduser().resolve()
    if not base_manifest.is_file():
        raise FileNotFoundError(f"The audited 4S16F2 split manifest is missing: {base_manifest}")
    source_rows = [row for row in _read_csv(base_manifest)
                   if row.get("dataset") in {"RWF-2000", "AIRTLab"}]
    records: list[core.VideoRecord] = []
    manifest_rows: list[dict[str, Any]] = []
    airt_groups: dict[str, set[str]] = defaultdict(set)
    airt_group_labels: dict[str, set[str]] = defaultdict(set)
    for index, row in enumerate(source_rows, start=1):
        out = {**row, "split_source": row.get("split_source") or "audited_4s16f2_base_manifest"}
        if row.get("eligible") != "1":
            manifest_rows.append(out)
            continue
        dataset, split, class_name = row.get("dataset", ""), row.get("split", ""), row.get("class_name", "")
        label = int(row.get("label", "-1"))
        if dataset not in {"RWF-2000", "AIRTLab"} or split not in SPLITS or class_name not in native.SOURCE_LABELS:
            raise ValueError(f"Unexpected base-manifest dataset/split/class row: {row}")
        if label != native.SOURCE_LABELS[class_name]:
            raise ValueError(f"Base-manifest label conflicts with its class name: {row}")
        path = Path(row.get("path", "")).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Base-manifest video is missing: {path}")
        fps, frame_count, duration = float(row["fps"]), int(row["frame_count"]), float(row["duration_seconds"])
        if not math.isfinite(fps) or fps <= 0 or frame_count <= 0 or duration < four.MIN_SCAN_SECONDS:
            raise ValueError(f"Invalid eligible video metadata in base manifest: {path}")
        record = native._new_record(dataset, split, class_name, path, fps, frame_count, duration,
                                    source_group=row.get("source_group", ""))
        records.append(record)
        manifest_rows.append({**out, "path": str(path), "source_group": record.source_group})
        if dataset == "AIRTLab":
            airt_groups[record.source_group].add(split)
            airt_group_labels[record.source_group].add(class_name)
        if progress_callback and (index == 1 or index % 100 == 0 or index == len(source_rows)):
            if logger:
                logger.info("Audited source manifest %d/%d | preserving RWF/AIRT split", index, len(source_rows))
            progress_callback(stage="核验 RWF-2000 与 AIRT Lab 原有划分", step=index,
                              steps_total=len(source_rows), stage_progress=index / max(1, len(source_rows)))
    for dataset in ("RWF-2000", "AIRTLab"):
        counts = {name: sum(record.dataset == dataset and record.class_name == name for record in records)
                  for name in ("Fight", "NonFight")}
        if counts != EXPECTED_COUNTS[dataset]:
            raise RuntimeError(f"Audited {dataset} sample counts changed: {counts}")
    if any(len(value) != 1 for value in airt_groups.values()) or any(len(value) != 1 for value in airt_group_labels.values()):
        raise RuntimeError("AIRT Lab camera-paired source group crosses a split or class.")
    if not airt_groups or any(sum(record.dataset == "AIRTLab" and record.source_group == group for record in records) != 2
                              for group in airt_groups):
        raise RuntimeError("AIRT Lab base manifest no longer has both camera views for every paired source.")

    harvard_root = dataset_root / "Harvard Dataverse"
    scanned = 0
    for folder_name, class_name in (("Violence", "Fight"), ("NonViolence", "NonFight")):
        class_root = harvard_root / folder_name
        if not class_root.is_dir():
            raise FileNotFoundError(f"Harvard Dataverse class directory is missing: {class_root}")
        paths = sorted((path for path in class_root.rglob("*")
                        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES),
                       key=lambda path: path.as_posix().casefold())
        eligible: list[tuple[core.VideoRecord, dict[str, Any]]] = []
        for path in paths:
            relative = path.relative_to(harvard_root).as_posix().casefold()
            source_group = f"Harvard Dataverse|{relative}"
            candidate, row = native._probe_video(path, "Harvard Dataverse", "train", class_name,
                                                  cv2, source_group=source_group)
            if candidate is None:
                row["split"] = "excluded"
                row["split_source"] = f"random_seed_{seed}_class_stratified_80_10_10"
                manifest_rows.append(row)
                continue
            eligible.append((candidate, row))
            scanned += 1
            if progress_callback and (scanned == 1 or scanned % 100 == 0 or scanned == sum(EXPECTED_COUNTS["Harvard Dataverse"].values())):
                progress_callback(stage="检查 Harvard Dataverse 视频", step=scanned,
                                  steps_total=sum(EXPECTED_COUNTS["Harvard Dataverse"].values()),
                                  stage_progress=scanned / max(1, sum(EXPECTED_COUNTS["Harvard Dataverse"].values())))
        expected = EXPECTED_COUNTS["Harvard Dataverse"][class_name]
        if len(eligible) != expected:
            raise RuntimeError(f"Harvard Dataverse {class_name} has {len(eligible)} eligible videos; expected {expected}.")
        split_map = stratified_random_splits([record.path for record, _ in eligible],
                                             seed + (1 if class_name == "Fight" else 0))
        for record, row in eligible:
            record.split = split_map[record.path]
            row["split"] = record.split
            row["split_source"] = f"random_seed_{seed}_class_stratified_80_10_10"
            records.append(record)
            manifest_rows.append(row)

    for split in SPLITS:
        if {record.label for record in records if record.split == split} != {0, 1}:
            raise RuntimeError(f"The {split} split must contain both positive and negative bags.")
    summary = _summary(records, manifest_rows)
    if summary["total_bags"] != sum(sum(values.values()) for values in EXPECTED_COUNTS.values()):
        raise RuntimeError(f"Unexpected eligible record total: {summary['total_bags']}")
    metadata = {
        "summary": summary,
        "base_manifest": str(base_manifest),
        "base_manifest_sha256": hashlib.sha256(base_manifest.read_bytes()).hexdigest(),
        "split_policy": "RWF-2000 and AIRT Lab exact eligible assignments from audited 4S16F2 Best manifest; AIRT Lab paired camera views remain together; Harvard Dataverse class-stratified random 80:10:10 using the configured seed",
        "harvard_split_seed": seed,
        "harvard_class_counts": EXPECTED_COUNTS["Harvard Dataverse"],
    }
    return records, manifest_rows, metadata


def _letterbox_rgb(frame: Any, *, np: Any, cv2: Any) -> tuple[Any, tuple[int, int, int, int]]:
    height, width = frame.shape[:2]
    if height <= 0 or width <= 0:
        raise ValueError("A sampled RGB frame has an empty dimension.")
    scale = min(INPUT_SIZE / width, INPUT_SIZE / height)
    resized_width = max(1, min(INPUT_SIZE, int(round(width * scale))))
    resized_height = max(1, min(INPUT_SIZE, int(round(height * scale))))
    interpolation = cv2.INTER_AREA if resized_width < width or resized_height < height else cv2.INTER_LINEAR
    resized = cv2.resize(frame, (resized_width, resized_height), interpolation=interpolation)
    left = (INPUT_SIZE - resized_width) // 2
    top = (INPUT_SIZE - resized_height) // 2
    return resized, (top, left, resized_height, resized_width)


def frames_to_clip(frames_rgb: Sequence[Any], *, np: Any, torch: Any, cv2: Any,
                   training: bool = False, seed: int = 0) -> Any:
    """Convert 16 RGB frames to normalized C,T,160,160 mean-padded letterboxes."""
    if len(frames_rgb) != four.NUM_FRAMES:
        raise ValueError(f"Expected {four.NUM_FRAMES} sampled frames, found {len(frames_rgb)}.")
    params = core._augment_params(np.random.default_rng(seed)) if training else {
        "flip": False, "brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "blur": False,
    }
    mean = np.asarray(core.MEAN, dtype=np.float32).reshape(1, 1, 3)
    std = np.asarray(core.STD, dtype=np.float32).reshape(1, 1, 3)
    processed = []
    expected_geometry: tuple[int, int, int, int] | None = None
    for source in frames_rgb:
        frame = np.asarray(source)
        if frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
            raise ValueError("Letterbox input frames must be uint8 RGB images with three channels.")
        if params["flip"]:
            frame = np.ascontiguousarray(frame[:, ::-1])
        resized, geometry = _letterbox_rgb(frame, np=np, cv2=cv2)
        if expected_geometry is None:
            expected_geometry = geometry
        elif geometry != expected_geometry:
            raise ValueError("Video aspect/size changed within a clip; refusing inconsistent letterbox geometry.")
        image = resized.astype(np.float32) / 255.0
        saturation = float(params["saturation"])
        if saturation != 1.0:
            gray = image[..., 0:1] * 0.299 + image[..., 1:2] * 0.587 + image[..., 2:3] * 0.114
            image = gray + saturation * (image - gray)
        image = (image - 0.5) * float(params["contrast"]) + 0.5 + float(params["brightness"])
        image = np.clip(image, 0.0, 1.0)
        if params["blur"]:
            image = cv2.GaussianBlur(image, (3, 3), sigmaX=0.45)
        top, left, content_height, content_width = geometry
        canvas = np.broadcast_to(mean, (INPUT_SIZE, INPUT_SIZE, 3)).copy()
        canvas[top:top + content_height, left:left + content_width] = image
        normalized = (canvas - mean) / std
        processed.append(np.ascontiguousarray(normalized, dtype=np.float32))
    return torch.from_numpy(np.ascontiguousarray(np.stack(processed).transpose(3, 0, 1, 2)))


def record_bag_tensor(record: core.VideoRecord, *, training: bool, epoch: int, seed: int,
                      np: Any, cv2: Any, torch: Any) -> tuple[Any, list[core.Window]]:
    frames, index_map = core._load_cache(record, np)
    windows = four._windows_for_record(record, training=training)
    if not windows:
        raise RuntimeError(f"No 4S16F2 windows were generated for {record.path}.")
    rng = np.random.default_rng(core.stable_seed(seed, epoch, record.identity))
    clips = []
    for window in windows:
        sampled = [frames[index_map[int(frame_index)]] for frame_index in window.frame_indices]
        clips.append(frames_to_clip(sampled, np=np, torch=torch, cv2=cv2, training=training,
                                    seed=int(rng.integers(0, 2**32 - 1))))
    return torch.stack(clips, dim=0), windows


def configure_runtime() -> None:
    """Install this isolated input/data profile in the training subprocess."""
    native.PROTOCOL_ID = EXPERIMENT_PROTOCOL
    native.INPUT_REPRESENTATION = INPUT_REPRESENTATION
    native.INPUT_PROTOCOL_ID = EXPERIMENT_PROTOCOL
    native.DATASET_NAMES = DATASET_NAMES
    native.load_records = load_records
    native._experiment_protocol = experiment_protocol
    original_install = native._install_runtime_protocol

    def install(cache_root: Path) -> None:
        original_install(cache_root)
        # Existing common-profile caches contain uncropped RGB frames at 128
        # short side; all spatial letterboxing happens after cache lookup.
        core.SHORT_SIDE = 128
        core.CROP_SIZE = 112
        core._record_bag_tensor = record_bag_tensor

    native._install_runtime_protocol = install


def main(argv: Sequence[str] | None = None) -> int:
    configure_runtime()
    return native.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
