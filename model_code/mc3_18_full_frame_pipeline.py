"""Full-frame, short-side-112 MC3-18 4S16F2 fine-tuning profile.

This entry point reuses the audited native-video fine-tuning engine while
locking the selected sources and preserving the established source splits.
The existing 128-short-side frame caches remain valid inputs; every clip is
resized to short-side 112 at tensor construction, with no spatial crop.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import random
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

import mc3_18_four_second_pipeline as four
import mc3_18_mil_pipeline as core
import mc3_18_native_finetune_pipeline as native


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "GuardAId Video Classification Model Dataset"
BASE_RUN = PROJECT_ROOT / "model_runs" / "mc3_4s16f2" / "run_four_ui_20261007_234526_491833"
BASE_MANIFEST = BASE_RUN / "dataset_manifest.csv"
BASE_CHECKPOINT = BASE_RUN / "best_model.pt"
DANCETRACK_MANIFEST = DATA_ROOT / "DanceTrack" / "dancetrack_4s16f2_frame_manifest.json"
OUTPUT_ROOT = PROJECT_ROOT / "model_runs" / "mc3_full_frame_shortside112_4s16f2"
CACHE_PROTOCOL = "mc3-4s-2s-stride-16f-shortside112-fullframe-v1"
EXPERIMENT_PROTOCOL = "mc3-five-dataset-fullframe-shortside112-4s16f2-v1"
INPUT_REPRESENTATION = "native_video_full_frame_short_side_112"
INPUT_SHORT_SIDE = 112
LEGACY_DANCETRACK_CACHE_PROTOCOL = "mc3-native-four-dataset-finetune-4s16f2-v1"
LEGACY_DANCETRACK_CACHE_ROOT = native.DEFAULT_OUTPUT_ROOT / "_frame_cache" / "dancetrack"
_ORIGINAL_DANCETRACK_CACHE_PREPARER = native._prepare_dancetrack_cache
DATASET_NAMES = ("RWF-2000", "AIRTLab", "ShakeFive2", "ShanghaiTech", "DanceTrack")
BASE_DATASET_NAMES = ("RWF-2000", "AIRTLab", "ShanghaiTech")
SPLITS = ("train", "val", "test")
VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv"}


def experiment_protocol() -> dict[str, Any]:
    return {
        "protocol": EXPERIMENT_PROTOCOL,
        "input_representation": INPUT_REPRESENTATION,
        "input_modality": "native",
        "window_seconds": 4.0,
        "stride_seconds": 2.0,
        "frames_per_window": 16,
        "sampling_rate_fps": 4.0,
        "frame_sample_positions": [round((index + 0.5) / 16, 6) for index in range(16)],
        "short_side": INPUT_SHORT_SIDE,
        "crop_size": None,
        "resize_policy": "aspect-preserving short-side resize; retain the complete frame; no crop or letterbox",
        "batch_spatial_policy": "process windows per video; all clips in a batch share that video's aspect ratio",
        "color_space": "RGB",
        "normalization_mean": list(core.MEAN),
        "normalization_std": list(core.STD),
        "short_clip_policy": {
            "duration_below_seconds": four.WINDOW_SECONDS,
            "operation": "sample 16 frame centres uniformly over the real clip duration; no reverse or padding",
            "minimum_duration_seconds": four.MIN_SCAN_SECONDS,
        },
        "tail_policy": "full 4-second windows at 2-second stride, plus one end-aligned tail when needed",
        "video_aggregation": "maximum_window_probability",
        "selected_datasets": list(DATASET_NAMES),
        "source_roots": {
            "dataset_root": str(DATA_ROOT.resolve()),
            "base_manifest": str(BASE_MANIFEST.resolve()),
            "dancetrack_manifest": str(DANCETRACK_MANIFEST.resolve()),
            "shakefive2": str((DATA_ROOT / "ShakeFive2.videos").resolve()),
        },
        "split_policy": {
            "RWF-2000": "eligible train/val/test assignments from the audited 4S16F2 Best manifest",
            "AIRTLab": "exact train/val/test assignments from the audited 4S16F2 Best manifest; camera pairs remain grouped",
            "ShanghaiTech": "eligible train/val/test assignments from the audited 4S16F2 Best manifest",
            "DanceTrack": "official train1+train2=train, val=val, test1+test2=test mapping from retained-frame manifest",
            "ShakeFive2": "deterministic 80/10/10 split, seed 2026",
        },
        "negative_label_policy": "DanceTrack, ShanghaiTech, and ShakeFive2 are treated as NonFight samples as specified for this experiment.",
        "cache_policy": {
            "existing_4s16f2_frames": "reuse matching selected source frames cached at short-side 128, then resize the full frame to short-side 112 in memory",
            "missing_frames": "decode only missing 4S16F2 sample indices into this experiment's separate cache",
            "existing_dancetrack_frames": "reuse exact identity/index matches from the prior native 4S16F2 cache read-only; build only missing/corrupt entries from the retained JPEG frames",
            "prior_dancetrack_cache_root": str(LEGACY_DANCETRACK_CACHE_ROOT.resolve()),
            "source_cache_is_read_only": True,
            "cache_protocol": CACHE_PROTOCOL,
        },
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _random_splits(paths: Sequence[Path], seed: int = 2026) -> dict[Path, str]:
    ordered = sorted(paths, key=lambda path: path.as_posix().casefold())
    random.Random(seed).shuffle(ordered)
    raw = {"train": len(ordered) * 0.8, "val": len(ordered) * 0.1, "test": len(ordered) * 0.1}
    targets = {name: math.floor(raw[name]) for name in SPLITS}
    for name in sorted(SPLITS, key=lambda key: (-(raw[key] - targets[key]), SPLITS.index(key)))[:len(ordered) - sum(targets.values())]:
        targets[name] += 1
    train_end = targets["train"]
    val_end = train_end + targets["val"]
    return {path: "train" if index < train_end else "val" if index < val_end else "test"
            for index, path in enumerate(ordered)}


def _summary(records: Sequence[core.VideoRecord], manifest_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    datasets: dict[str, Any] = {}
    for dataset in DATASET_NAMES:
        by_split = {split: {"Fight": 0, "NonFight": 0, "bags": 0, "windows": 0} for split in SPLITS}
        for record in records:
            if record.dataset != dataset:
                continue
            row = by_split[record.split]
            row[record.class_name] += 1
            row["bags"] += 1
            row["windows"] += len(four.make_four_second_windows(record.duration, record.fps, record.frame_count))
        by_split["total_bags"] = sum(by_split[split]["bags"] for split in SPLITS)
        # These are all manifest rows rejected by the audited eligibility flag;
        # they are not necessarily short clips, so do not mislabel the count.
        by_split["excluded_manifest_rows"] = sum(
            1 for row in manifest_rows if row.get("dataset") == dataset
            and str(row.get("eligible")) in {"0", "False"})
        datasets[dataset] = by_split
    overall = {split: {"Fight": 0, "NonFight": 0, "bags": 0, "windows": 0} for split in SPLITS}
    for by_split in datasets.values():
        for split in SPLITS:
            for key in overall[split]:
                overall[split][key] += by_split[split][key]
    return {"datasets": datasets, "overall": overall,
            "total_bags": sum(overall[split]["bags"] for split in SPLITS),
            "eligible_manifest_bags": sum(1 for row in manifest_rows
                                           if str(row.get("eligible", 1)) in {"1", "True"})}


def quick_inventory(*, dataset_root: Path = DATA_ROOT, base_manifest: Path = BASE_MANIFEST,
                    dancetrack_manifest: Path = DANCETRACK_MANIFEST, seed: int = 2026) -> dict[str, Any]:
    """Read existing manifests and file names only; no video decoding or cache writes."""
    if not base_manifest.is_file() or not dancetrack_manifest.is_file():
        raise FileNotFoundError("The audited 4S16F2 base manifest or DanceTrack frame manifest is missing.")
    base_rows = [row for row in _read_csv(base_manifest)
                 if row.get("dataset") in BASE_DATASET_NAMES and row.get("eligible") == "1"]
    counts = {name: sum(row.get("dataset") == name for row in base_rows) for name in BASE_DATASET_NAMES}
    expected = {"RWF-2000": 1973, "AIRTLab": 350, "ShanghaiTech": 329}
    if counts != expected:
        raise RuntimeError(f"Selected base-manifest counts differ from the audited source: {counts} (expected {expected}).")
    dtrack = json.loads(dancetrack_manifest.read_text(encoding="utf-8"))
    if dtrack.get("protocol") != "4S16F2" or len(dtrack.get("sequences", [])) != 100:
        raise RuntimeError("The prepared DanceTrack 4S16F2 split/frame manifest must contain 100 sequences.")
    shake_root = dataset_root / "ShakeFive2.videos"
    shake = [path for path in shake_root.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES]
    if len(shake) != 153:
        raise RuntimeError(f"Expected 153 ShakeFive2 videos, found {len(shake)} under {shake_root}.")
    missing_sources = [Path(row.get("path", "")) for row in base_rows
                       if not Path(row.get("path", "")).is_file()]
    dance_root = dataset_root / "DanceTrack"
    for sequence in dtrack["sequences"]:
        rel = Path(str(sequence.get("sequence_path", "")))
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"Unsafe DanceTrack sequence path in manifest: {rel}")
        sequence_path = dance_root / rel
        required = {int(value) for window in sequence.get("windows", [])
                    for value in window.get("source_frame_numbers_1based", [])}
        for frame_number in required:
            frame_path = sequence_path / "img1" / f"{frame_number:08d}.jpg"
            if not frame_path.is_file():
                missing_sources.append(frame_path)
                if len(missing_sources) >= 10:
                    break
        if len(missing_sources) >= 10:
            break
    rows: list[dict[str, Any]] = [dict(row) for row in base_rows]
    for sequence in dtrack["sequences"]:
        split = dtrack.get("split_mapping", {}).get(str(sequence.get("source_shard", "")))
        if split not in SPLITS:
            raise ValueError(f"Unrecognized DanceTrack split shard: {sequence.get('source_shard')!r}")
        rows.append({"dataset": "DanceTrack", "split": split, "class_name": "NonFight", "label": 0, "eligible": 1})
    for path, split in _random_splits(shake, seed).items():
        rows.append({"dataset": "ShakeFive2", "split": split, "class_name": "NonFight", "label": 0, "eligible": 1})
    totals: dict[str, Any] = {"datasets": {}, "overall": {split: {"Fight": 0, "NonFight": 0, "bags": 0} for split in SPLITS}}
    for dataset in DATASET_NAMES:
        per_split = {split: {"Fight": 0, "NonFight": 0, "bags": 0} for split in SPLITS}
        for row in rows:
            if row.get("dataset") != dataset:
                continue
            split, name = row.get("split"), row.get("class_name")
            if split in SPLITS and name in {"Fight", "NonFight"}:
                per_split[split][name] += 1
                per_split[split]["bags"] += 1
        per_split["total_bags"] = sum(per_split[split]["bags"] for split in SPLITS)
        totals["datasets"][dataset] = per_split
        for split in SPLITS:
            for key in totals["overall"][split]:
                totals["overall"][split][key] += per_split[split][key]
    totals["total_bags"] = sum(totals["overall"][split]["bags"] for split in SPLITS)
    return {
        "ready": totals["total_bags"] == 2905 and not missing_sources,
        "missing_source_count_sampled": len(missing_sources),
        "first_missing_sources": [str(path) for path in missing_sources[:5]],
        "summary": totals,
        "selected_datasets": list(DATASET_NAMES),
        "base_checkpoint": str(native.DEFAULT_BASE_CHECKPOINT.resolve()),
        "base_checkpoint_exists": native.DEFAULT_BASE_CHECKPOINT.is_file(),
        "base_manifest": str(base_manifest.resolve()),
        "dancetrack_manifest": str(dancetrack_manifest.resolve()),
        "cache_root": str((OUTPUT_ROOT / "_frame_cache" / CACHE_PROTOCOL).resolve()),
        "prior_dancetrack_cache_root": str(LEGACY_DANCETRACK_CACHE_ROOT.resolve()),
        "split_policy": experiment_protocol()["split_policy"],
        "protocol": EXPERIMENT_PROTOCOL,
    }


def _resize_rgb_short_side(frame: Any, short_side: int, cv2: Any) -> Any:
    height, width = frame.shape[:2]
    if height <= 0 or width <= 0:
        raise ValueError("A sampled frame has an empty spatial dimension.")
    if height <= width:
        target_height = short_side
        target_width = max(1, int(round(width * short_side / height)))
    else:
        target_width = short_side
        target_height = max(1, int(round(height * short_side / width)))
    interpolation = cv2.INTER_AREA if target_height < height or target_width < width else cv2.INTER_LINEAR
    return cv2.resize(frame, (target_width, target_height), interpolation=interpolation)


def frames_to_clip(frames_rgb: Sequence[Any], *, np: Any, torch: Any, cv2: Any,
                   training: bool = False, seed: int = 0) -> Any:
    """Normalize a same-video frame sequence into C,T,H,W without cropping."""
    if len(frames_rgb) != four.NUM_FRAMES:
        raise ValueError(f"Expected {four.NUM_FRAMES} sampled frames, found {len(frames_rgb)}.")
    params = core._augment_params(np.random.default_rng(seed)) if training else {
        "flip": False, "brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "blur": False,
    }
    height_width: tuple[int, int] | None = None
    processed = []
    mean = np.asarray(core.MEAN, dtype=np.float32).reshape(1, 1, 3)
    std = np.asarray(core.STD, dtype=np.float32).reshape(1, 1, 3)
    for source in frames_rgb:
        frame = _resize_rgb_short_side(source, INPUT_SHORT_SIDE, cv2)
        current_shape = (int(frame.shape[0]), int(frame.shape[1]))
        if height_width is None:
            height_width = current_shape
        elif current_shape != height_width:
            raise ValueError("Video frame aspect/size changed within one clip; refusing to silently pad or crop it.")
        if params["flip"]:
            frame = frame[:, ::-1]
        image = frame.astype(np.float32) / 255.0
        saturation = float(params["saturation"])
        if saturation != 1.0:
            gray = image[..., 0:1] * 0.299 + image[..., 1:2] * 0.587 + image[..., 2:3] * 0.114
            image = gray + saturation * (image - gray)
        image = (image - 0.5) * float(params["contrast"]) + 0.5 + float(params["brightness"])
        image = np.clip(image, 0.0, 1.0)
        if params["blur"]:
            image = cv2.GaussianBlur(image, (3, 3), sigmaX=0.45)
        processed.append(np.ascontiguousarray((image - mean) / std, dtype=np.float32))
    # T,H,W,C -> C,T,H,W. The complete resized frame is kept.
    array = np.stack(processed, axis=0).transpose(3, 0, 1, 2)
    return torch.from_numpy(np.ascontiguousarray(array))


def record_bag_tensor(record: core.VideoRecord, *, training: bool, epoch: int, seed: int,
                      np: Any, cv2: Any, torch: Any) -> tuple[Any, list[core.Window]]:
    frames, index_map = core._load_cache(record, np)
    windows = four._windows_for_record(record, training=training)
    if not windows:
        raise RuntimeError(f"No 4S16F2 windows were generated for {record.path}.")
    rng = np.random.default_rng(core.stable_seed(seed, epoch, record.identity))
    clips = []
    for window in windows:
        source_frames = [frames[index_map[int(frame_index)]] for frame_index in window.frame_indices]
        clips.append(frames_to_clip(source_frames, np=np, torch=torch, cv2=cv2, training=training,
                                    seed=int(rng.integers(0, 2**32 - 1))))
    return torch.stack(clips, dim=0), windows


def _dancetrack_cache_matches(path: Path, required: Sequence[int], np: Any) -> bool:
    """Check NPZ indices and the uncompressed NPY header without loading frame payloads."""
    try:
        with zipfile.ZipFile(path) as archive:
            indices = np.load(io.BytesIO(archive.read("frame_indices.npy")), allow_pickle=False)
            with archive.open("frames.npy") as payload:
                version = np.lib.format.read_magic(payload)
                if version == (1, 0):
                    shape, _fortran_order, dtype = np.lib.format.read_array_header_1_0(payload)
                else:
                    shape, _fortran_order, dtype = np.lib.format.read_array_header_2_0(payload)
        return (indices.ndim == 1 and indices.astype(np.int64).tolist() == list(required)
                and len(shape) == 4 and shape[0] == len(required) and shape[-1] == 3
                and shape[1] >= core.CROP_SIZE and shape[2] >= core.CROP_SIZE
                and np.dtype(dtype) == np.dtype(np.uint8))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, EOFError):
        return False


def prepare_dancetrack_cache(records: Sequence[core.VideoRecord], cache_dir: Path, cv2: Any, np: Any,
                             logger: logging.Logger, update_status, *, cache_budget_bytes: int) -> None:
    """Reuse the exact prior DanceTrack frame cache read-only; build only cache misses."""
    samples = [record for record in records if record.dataset == "DanceTrack"]
    prior_root = LEGACY_DANCETRACK_CACHE_ROOT
    current_root = cache_dir / "dancetrack"
    reused_current = reused_prior = 0
    remaining: list[core.VideoRecord] = []
    current_root.mkdir(parents=True, exist_ok=True)

    for index, record in enumerate(samples, start=1):
        required = tuple(sorted(int(value) for value in record.required_frame_indices))
        if not required:
            raise RuntimeError(f"DanceTrack cache record has no 4S16F2 frame indices: {record.path}")
        frame_paths = {frame_index: record.path / "img1" / f"{frame_index + 1:08d}.jpg"
                       for frame_index in required}
        missing = [path for path in frame_paths.values() if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"DanceTrack retained input frame is missing: {missing[0]}")
        signature = [f"{frame_index}:{frame_paths[frame_index].stat().st_size}:{frame_paths[frame_index].stat().st_mtime_ns}"
                     for frame_index in required]
        identity = record.identity
        current_key = hashlib.sha256((EXPERIMENT_PROTOCOL + "|" + identity + "|" + "|".join(signature)).encode("utf-8")).hexdigest()
        prior_key = hashlib.sha256((LEGACY_DANCETRACK_CACHE_PROTOCOL + "|" + identity + "|" + "|".join(signature)).encode("utf-8")).hexdigest()
        current_path = current_root / f"{current_key}.npz"
        prior_path = prior_root / f"{prior_key}.npz"
        record.required_frame_indices = required
        record.legacy_cache_path = None
        if current_path.is_file() and _dancetrack_cache_matches(current_path, required, np):
            record.prior_supplement_cache_paths = ()
            record.supplement_cache_path = current_path
            record.cache_path = current_path
            reused_current += 1
        elif prior_path.is_file() and _dancetrack_cache_matches(prior_path, required, np):
            record.prior_supplement_cache_paths = (prior_path,)
            record.supplement_cache_path = None
            record.cache_path = prior_path
            reused_prior += 1
        else:
            record.prior_supplement_cache_paths = ()
            remaining.append(record)
        if index == 1 or index % 10 == 0 or index == len(samples):
            update_status(stage="核验并复用 DanceTrack 帧缓存", step=index, steps_total=len(samples),
                          stage_progress=index / max(1, len(samples)))

    def update_missing(**values):
        local_step = int(values.get("step", 0) or 0)
        combined_step = min(len(samples), reused_current + reused_prior + local_step)
        update_status(stage=values.get("stage", "准备 DanceTrack 帧缓存"), step=combined_step,
                      steps_total=len(samples), stage_progress=combined_step / max(1, len(samples)),
                      eta_seconds=values.get("eta_seconds"))

    if remaining:
        _ORIGINAL_DANCETRACK_CACHE_PREPARER(remaining, cache_dir, cv2, np, logger, update_missing,
                                            cache_budget_bytes=cache_budget_bytes)
    total_bytes = sum(path.stat().st_size for path in current_root.glob("*.npz") if path.is_file())
    logger.info("DanceTrack cache reuse | records=%d | current-profile=%d | prior read-only=%d | rebuilt=%d | prior_root=%s | new_bytes=%d",
                len(samples), reused_current, reused_prior, len(remaining), prior_root, total_bytes)
    if samples:
        update_status(stage="DanceTrack 帧缓存就绪", step=len(samples), steps_total=len(samples), stage_progress=1.0,
                      eta_seconds=0)


def load_records(*, dataset_root: Path = DATA_ROOT, base_manifest: Path = BASE_MANIFEST,
                 dancetrack_manifest: Path = DANCETRACK_MANIFEST, seed: int = 2026,
                 cv2: Any | None = None, logger=None, validate_dance_frames: bool = False,
                 progress_callback=None):
    """Combine base-run RWF/AIRT/Shanghai splits with prepared DanceTrack and ShakeFive2."""
    if cv2 is None:
        import cv2 as cv2_module
        cv2 = cv2_module
    if not base_manifest.is_file():
        raise FileNotFoundError(f"Audited base-run manifest is missing: {base_manifest}")
    rows = _read_csv(base_manifest)
    source_rows = [row for row in rows if row.get("dataset") in BASE_DATASET_NAMES]
    records: list[core.VideoRecord] = []
    manifest_rows: list[dict[str, Any]] = []
    group_splits: dict[str, set[str]] = defaultdict(set)
    group_labels: dict[str, set[str]] = defaultdict(set)
    for index, row in enumerate(source_rows, start=1):
        out = {**row, "split_source": row.get("split_source") or "audited_4s16f2_base_manifest"}
        if row.get("eligible") != "1":
            manifest_rows.append(out)
            continue
        dataset, split, class_name = row.get("dataset", ""), row.get("split", ""), row.get("class_name", "")
        if split not in SPLITS or class_name not in native.SOURCE_LABELS:
            raise ValueError(f"Unexpected base-manifest dataset/split/label row: {row}")
        label = int(row.get("label", -1))
        if label != native.SOURCE_LABELS[class_name]:
            raise ValueError(f"Base-manifest label conflicts with class name: {row}")
        path = Path(row.get("path", "")).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Base-manifest video is missing: {path}")
        fps, frame_count = float(row["fps"]), int(row["frame_count"])
        duration = float(row["duration_seconds"])
        if not math.isfinite(fps) or fps <= 0 or frame_count <= 0 or duration < four.MIN_SCAN_SECONDS:
            raise ValueError(f"Invalid eligible video metadata in base manifest: {path}")
        record = native._new_record(dataset, split, class_name, path, fps, frame_count, duration,
                                    source_group=row.get("source_group", ""))
        record.base_manifest_row = dict(row)
        records.append(record)
        manifest_rows.append({**out, "path": str(path), "source_group": record.source_group})
        if dataset == "AIRTLab":
            group_splits[record.source_group].add(split)
            group_labels[record.source_group].add(class_name)
        if progress_callback and (index == 1 or index % 100 == 0 or index == len(source_rows)):
            progress_callback(stage="核验 RWF-2000、AIRT Lab、ShanghaiTech 原始 split", step=index,
                              steps_total=len(source_rows), stage_progress=index / max(1, len(source_rows)))
    airt_groups: dict[str, int] = defaultdict(int)
    for record in records:
        if record.dataset == "AIRTLab":
            airt_groups[record.source_group] += 1
    if any(len(value) != 1 for value in group_splits.values()) or any(len(value) != 1 for value in group_labels.values()):
        raise RuntimeError("AIRT Lab camera-paired source group crossed splits or labels.")
    if len(air_records := [record for record in records if record.dataset == "AIRTLab"]) != 350:
        raise RuntimeError(f"Expected 350 eligible AIRT Lab views, found {len(air_records)}.")
    if not airt_groups or any(size != 2 for size in airt_groups.values()):
        raise RuntimeError("AIRT Lab split manifest no longer contains complete paired camera views.")

    dtrack = json.loads(dancetrack_manifest.read_text(encoding="utf-8"))
    if dtrack.get("protocol") != "4S16F2" or len(dtrack.get("sequences", [])) != 100:
        raise RuntimeError("Prepared DanceTrack 4S16F2 manifest must contain 100 sequences.")
    dance_root = dataset_root / "DanceTrack"
    if Path(str(dtrack.get("dataset_root", ""))).resolve() != dance_root.resolve():
        raise RuntimeError("DanceTrack frame manifest belongs to a different data folder.")
    split_mapping = dtrack.get("split_mapping", {})
    for sequence in dtrack["sequences"]:
        shard = str(sequence.get("source_shard", ""))
        split = split_mapping.get(shard)
        if split not in SPLITS:
            raise ValueError(f"Invalid DanceTrack official split shard: {shard!r}")
        rel = Path(str(sequence["sequence_path"]))
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"Unsafe DanceTrack sequence path: {rel}")
        sequence_path = dance_root / rel
        fps, frame_count = float(sequence["fps"]), int(sequence["original_frame_count"])
        record = native._new_record("DanceTrack", split, "NonFight", sequence_path, fps, frame_count,
                                    frame_count / fps, source_group=f"DanceTrack|{sequence_path.name.casefold()}")
        record.dancetrack_sequence = sequence
        record.dancetrack_root = dance_root
        record.retained_frame_numbers = set(int(value) for value in sequence["retained_frame_numbers_1based"])
        windows = four.make_four_second_windows(record.duration, record.fps, record.frame_count)
        expected_indices = {value for window in windows for value in window.frame_indices}
        manifest_indices = {int(value) - 1 for window in sequence.get("windows", [])
                            for value in window.get("source_frame_numbers_1based", [])}
        if expected_indices != manifest_indices:
            raise RuntimeError(f"DanceTrack sample indices differ from its retained-frame manifest: {sequence_path}")
        required_numbers = {value + 1 for value in expected_indices}
        if not required_numbers.issubset(record.retained_frame_numbers):
            raise RuntimeError(f"DanceTrack retained-frame manifest omits a required sample: {sequence_path}")
        if validate_dance_frames:
            missing = [sequence_path / "img1" / f"{number:08d}.jpg" for number in sorted(required_numbers)
                       if not (sequence_path / "img1" / f"{number:08d}.jpg").is_file()]
            if missing:
                raise FileNotFoundError(f"DanceTrack retained input frame is missing: {missing[0]}")
        record.required_frame_indices = tuple(sorted(expected_indices))
        records.append(record)
        manifest_rows.append({"dataset": "DanceTrack", "split": split, "class_name": "NonFight", "label": 0,
                              "path": str(sequence_path.resolve()), "source_group": record.source_group,
                              "fps": fps, "frame_count": frame_count, "duration_seconds": record.duration,
                              "eligible": 1, "exclusion_reason": "", "size_bytes": 0,
                              "split_source": f"official_{shard}_mapping", "window_count": len(windows)})

    shake_root = dataset_root / "ShakeFive2.videos"
    shake_paths = sorted((path for path in shake_root.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES),
                         key=lambda path: path.name.casefold())
    if len(shake_paths) != 153:
        raise RuntimeError(f"Expected 153 ShakeFive2 videos, found {len(shake_paths)} under {shake_root}.")
    shake_splits = _random_splits(shake_paths, seed)
    for index, path in enumerate(shake_paths, start=1):
        record, row = native._probe_video(path, "ShakeFive2", shake_splits[path], "NonFight", cv2,
                                           source_group=f"ShakeFive2|{path.stem.casefold()}")
        row["split_source"] = f"random_seed_{seed}_80_10_10"
        if record is not None:
            records.append(record)
        manifest_rows.append(row)
        if progress_callback and (index == 1 or index % 25 == 0 or index == len(shake_paths)):
            progress_callback(stage="读取 ShakeFive2 视频信息", step=index, steps_total=len(shake_paths),
                              stage_progress=index / max(1, len(shake_paths)))
    for dataset in DATASET_NAMES:
        for split in SPLITS:
            if not any(record.dataset == dataset and record.split == split for record in records):
                raise RuntimeError(f"{dataset} has no eligible {split} samples.")
    for split in SPLITS:
        if {record.label for record in records if record.split == split} != {0, 1}:
            raise RuntimeError(f"The {split} split must contain both positive and negative video bags.")
    metadata = {
        "summary": _summary(records, manifest_rows),
        "base_manifest": str(base_manifest.resolve()),
        "base_manifest_sha256": hashlib.sha256(base_manifest.read_bytes()).hexdigest(),
        "dancetrack_manifest": str(dancetrack_manifest.resolve()),
        "dancetrack_manifest_sha256": hashlib.sha256(dancetrack_manifest.read_bytes()).hexdigest(),
        "split_policy": "RWF-2000/AIRTLab/ShanghaiTech exact eligible assignments from 4S16F2 base manifest; AIRT Lab camera views stay grouped; DanceTrack official shard mapping; ShakeFive2 deterministic 80/10/10 seed 2026",
        "negative_source_label_note": "DanceTrack, ShanghaiTech, and ShakeFive2 use the requested NonFight label mapping for this experiment.",
    }
    return records, manifest_rows, metadata


def configure_runtime() -> None:
    """Install this profile in the child process before native training starts."""
    native.PROTOCOL_ID = EXPERIMENT_PROTOCOL
    native.INPUT_REPRESENTATION = INPUT_REPRESENTATION
    native.INPUT_PROTOCOL_ID = EXPERIMENT_PROTOCOL
    native.DATASET_NAMES = DATASET_NAMES
    native.load_records = load_records
    native._experiment_protocol = experiment_protocol
    native._prepare_dancetrack_cache = prepare_dancetrack_cache
    original_install = native._install_runtime_protocol

    def install(cache_root: Path) -> None:
        original_install(cache_root)
        # Cache decoding remains keyed to the audited 128-short-side 4S16F2
        # source caches. The tensor transform below downsizes each full frame.
        core.SHORT_SIDE = 128
        core.CROP_SIZE = 112
        core._record_bag_tensor = record_bag_tensor

    native._install_runtime_protocol = install


def main(argv: Sequence[str] | None = None) -> int:
    configure_runtime()
    return native.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
