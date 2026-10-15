"""Measure the YOLOv9 training DataLoader on a stratified 10% train sample.

The benchmark uses the audited V1 manifest, copies raw source images into a
small isolated package, and copies only the mapped labels needed by YOLO.
It reports loader throughput (decode + training augmentation), not model speed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deterministic_key(row: dict[str, str], seed: int) -> str:
    identity = f"{seed}|{row['source']}|{row['source_image']}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def source_relative(path: Path, data_root: Path) -> str:
    return path.absolute().relative_to(data_root.absolute()).as_posix()


def choose_rows(
    rows: list[dict[str, str]], fraction: float, seed: int
) -> list[dict[str, str]]:
    target = max(1, min(len(rows), int(round(len(rows) * fraction))))
    strata: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        class_key = "negative" if row["negative"].casefold() == "true" else row["classes"]
        strata[(row["source"], class_key)].append(row)

    exact = {key: len(group) * target / len(rows) for key, group in strata.items()}
    allocations = {key: math.floor(value) for key, value in exact.items()}
    left = target - sum(allocations.values())
    order = sorted(exact, key=lambda key: (-(exact[key] - allocations[key]), key))
    for key in order[:left]:
        allocations[key] += 1

    selected: list[dict[str, str]] = []
    for key in sorted(strata):
        group = sorted(strata[key], key=lambda row: deterministic_key(row, seed))
        selected.extend(group[: allocations[key]])
    return sorted(selected, key=lambda row: deterministic_key(row, seed))


def load_exclusions(path: Path | None) -> set[str]:
    if not path or not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("exclude_source_relative_paths", [])
    return {str(value).replace("\\", "/").casefold() for value in values}


def prepare_subset(
    report_path: Path,
    data_root: Path,
    output_root: Path,
    exclusion_file: Path | None,
    fraction: float,
    seed: int,
) -> tuple[Path, int, list[dict[str, Any]]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest_path = Path(report["manifest"])
    rows: list[dict[str, str]] = []
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("split") == "train":
                rows.append(row)
    if not rows:
        raise RuntimeError("No training rows found in the V1 manifest")

    excluded_relpaths = load_exclusions(exclusion_file)
    eligible: list[dict[str, str]] = []
    for row in rows:
        relative = source_relative(Path(row["source_image"]), data_root).casefold()
        if relative not in excluded_relpaths:
            row["source_relative_path"] = relative
            eligible.append(row)
    target = max(1, min(len(eligible), int(round(len(rows) * fraction))))
    initial = choose_rows(eligible, target / len(eligible), seed)
    initial_keys = {row["source_image"] for row in initial}
    selected: list[dict[str, str]] = []
    rejected: list[dict[str, Any]] = []
    checked: set[str] = set()

    def validate_copy(row: dict[str, str], index: int) -> bool:
        image_src = Path(row["source_image"])
        label_src = Path(row["label"])
        if not image_src.is_file() or not label_src.is_file():
            rejected.append({"source": row["source"], "source_relative_path": row["source_relative_path"], "reason": "missing_image_or_mapped_label"})
            return False
        if sha256_file(image_src) != row["image_sha256"]:
            rejected.append({"source": row["source"], "source_relative_path": row["source_relative_path"], "reason": "source_image_hash_changed_since_audit"})
            return False
        if sha256_file(label_src) != row["label_sha256"]:
            rejected.append({"source": row["source"], "source_relative_path": row["source_relative_path"], "reason": "mapped_label_hash_changed_since_audit"})
            return False
        destination_name = f"sample_{index:05d}{image_src.suffix.lower()}"
        image_dest = output_root / "images" / "train" / destination_name
        label_dest = output_root / "labels" / "train" / f"sample_{index:05d}.txt"
        image_dest.parent.mkdir(parents=True, exist_ok=True)
        label_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image_src, image_dest)
        shutil.copy2(label_src, label_dest)
        selected.append({
            "benchmark_name": destination_name,
            "source": row["source"],
            "source_split": row["source_split"],
            "source_relative_path": row["source_relative_path"],
            "classes": row["classes"],
            "negative": row["negative"],
            "image_sha256": row["image_sha256"],
            "label_sha256": row["label_sha256"],
        })
        return True

    candidates = initial + sorted(
        (row for row in eligible if row["source_image"] not in initial_keys),
        key=lambda row: deterministic_key(row, seed + 1),
    )
    for row in candidates:
        if row["source_image"] in checked:
            continue
        checked.add(row["source_image"])
        if validate_copy(row, len(selected) + 1) and len(selected) >= target:
            break
    if len(selected) != target:
        raise RuntimeError(f"Could only prepare {len(selected)} of {target} requested samples")

    import yaml

    output_root.mkdir(parents=True, exist_ok=True)
    data_yaml = output_root / "data.yaml"
    data_yaml.write_text(
        yaml.safe_dump(
            {
                # Keep the uploaded sample self-contained; never serialize the
                # local Windows workspace path into a transfer artifact.
                "path": ".",
                "train": "images/train",
                "val": "images/train",
                "test": "images/train",
                "names": {0: "knife", 1: "gun"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with (output_root / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(selected[0]))
        writer.writeheader()
        writer.writerows(selected)
    (output_root / "benchmark_exclusions.json").write_text(
        json.dumps({"excluded": rejected, "known_source_recovery_exclusions": sorted(excluded_relpaths)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data_yaml, target, rejected


def run_loader_benchmark(
    data_yaml: Path,
    yolo_root: Path | None,
    output_root: Path,
    workers: list[int],
    batch_size: int,
    imgsz: int,
    repeats: int,
) -> dict[str, Any]:
    import yaml
    import torch

    if yolo_root is None:
        project_root = Path(__file__).resolve().parents[1]
        candidates = [project_root / "yolov9"]
        candidates.extend(sorted(project_root.glob("results*/yolov9")))
        yolo_root = next(
            (candidate for candidate in candidates if (candidate / "utils" / "dataloaders.py").is_file()),
            None,
        )
        if yolo_root is None:
            raise RuntimeError("Could not locate the uploaded YOLOv9 source tree")
    if str(yolo_root) not in sys.path:
        sys.path.insert(0, str(yolo_root))
    from utils.dataloaders import create_dataloader

    config_path = Path(__file__).resolve().parent / "yolov9_weapon_pipeline.py"
    if str(config_path.parent) not in sys.path:
        sys.path.insert(0, str(config_path.parent))
    from yolov9_weapon_pipeline import _write_hyp, default_training_config

    config = default_training_config()
    config.update({
        "yolov9_root": str(yolo_root.resolve()),
        "lr0": 0.001,
        "lrf": 0.1,
        "momentum": 0.9,
        "weight_decay": 0.0005,
        "warmup_epochs": 1.0,
        "hsv_h": 0.015,
        "hsv_s": 0.5,
        "hsv_v": 0.3,
        "translate": 0.1,
        "scale": 0.3,
        "fliplr": 0.5,
        "flipud": 0.0,
        "mosaic": 0.5,
        "mixup": 0.0,
    })
    hyp = _write_hyp(config, output_root / "benchmark_hyp.yaml")
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    import gc

    for repetition in range(repeats):
        order = workers if repetition % 2 == 0 else list(reversed(workers))
        for requested_workers in order:
            loader = None
            dataset = None
            batch_durations: list[float] = []
            batch_sizes: list[int] = []
            image_count = 0
            actual_workers: int | None = None
            error: str | None = None
            total_seconds: float | None = None
            steady_rate: float | None = None
            dataset_path = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))["train"]
            if not Path(dataset_path).is_absolute():
                dataset_path = str(data_yaml.parent / dataset_path)
            try:
                started = time.perf_counter()
                loader, dataset = create_dataloader(
                    str(dataset_path),
                    imgsz,
                    batch_size,
                    32,
                    single_cls=False,
                    hyp=hyp,
                    augment=True,
                    cache=False,
                    rect=False,
                    rank=-1,
                    workers=requested_workers,
                    image_weights=False,
                    close_mosaic=False,
                    quad=False,
                    min_items=0,
                    prefix=f"Brev loader benchmark ({requested_workers} workers): ",
                    shuffle=True,
                )
                actual_workers = int(loader.num_workers)
                previous = started
                for images, _targets, _paths, _shapes in loader:
                    now = time.perf_counter()
                    batch_durations.append(now - previous)
                    batch_sizes.append(int(images.shape[0]))
                    image_count += int(images.shape[0])
                    previous = now
                total_seconds = time.perf_counter() - started
                # Ignore worker startup and the first decode batches when
                # reporting steady-state throughput; retain them in total.
                steady_from = min(3, len(batch_durations))
                steady_seconds = sum(batch_durations[steady_from:])
                steady_images = sum(batch_sizes[steady_from:])
                steady_rate = steady_images / steady_seconds if steady_seconds else None
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            finally:
                del loader, dataset
                gc.collect()
            result = {
                "repeat": repetition + 1,
                "requested_workers": requested_workers,
                "actual_workers": actual_workers,
                "batch_size": batch_size,
                "imgsz": imgsz,
                "images": image_count,
                "batches": len(batch_durations),
                "seconds": total_seconds,
                "images_per_second": image_count / total_seconds if total_seconds else None,
                "steady_state_images_per_second": steady_rate,
                "error": error,
                "note": "CPU decode + YOLO train augmentation; excludes GPU model forward/backward.",
            }
            results.append(result)
            print(json.dumps({"event": "run_complete", **result}, ensure_ascii=False), flush=True)
            (output_root / "benchmark_progress.json").write_text(json.dumps({"completed_runs": len(results), "total_runs": repeats * len(workers), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")

    summaries = []
    for worker_count in workers:
        group = [row for row in results if row["requested_workers"] == worker_count]
        rates = [row["images_per_second"] for row in group if row["images_per_second"]]
        summaries.append({
            "workers": worker_count,
            "actual_workers": group[0]["actual_workers"] if group else None,
            "repeats": len(rates),
            "median_images_per_second": statistics.median(rates) if rates else None,
            "individual_runs": rates,
        })
    report = {
        "benchmark": "yolov9_v1_dataloader_only",
        # Keep the report portable and avoid embedding machine-specific paths.
        "data_yaml": data_yaml.name,
        "environment": {
            "python": sys.version.split()[0],
            "torch": torch.__version__,
            "cuda_build": torch.version.cuda,
            "cuda_available": torch.cuda.is_available(),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "gpu_memory_gib": round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 2)
            if torch.cuda.is_available()
            else None,
        },
        "requested_workers": workers,
        "batch_size": batch_size,
        "imgsz": imgsz,
        "repeats_per_worker": repeats,
        "results": results,
        "summary": summaries,
    }
    (output_root / "benchmark_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_root / "environment.json").write_text(json.dumps(report["environment"], ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Path to remote audit_report.json, used when preparing a new sample")
    parser.add_argument("--data-root", type=Path, help="Uploaded raw dataset directory, used when preparing a new sample")
    parser.add_argument("--prebuilt-data-yaml", type=Path, help="Use an already prepared, isolated sample package")
    parser.add_argument("--yolo-root", type=Path, help="YOLOv9 source tree; auto-detected from the project if omitted")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclude-file", type=Path)
    parser.add_argument("--fraction", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--workers", type=int, nargs="+", default=[8, 12, 16, 20, 24, 28])
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--prepare-only", action="store_true", help="Build the sample package but do not run the worker benchmark")
    args = parser.parse_args()
    if not 0.01 <= args.fraction <= 0.5:
        raise SystemExit("fraction must be between 0.01 and 0.5")
    if any(worker < 0 or worker > 28 for worker in args.workers):
        raise SystemExit("workers must be between 0 and 28")
    if args.prebuilt_data_yaml:
        data_yaml = args.prebuilt_data_yaml
        if not data_yaml.is_file():
            raise SystemExit(f"prebuilt data YAML does not exist: {data_yaml.name}")
        manifest_path = data_yaml.parent / "manifest.csv"
        with manifest_path.open("r", encoding="utf-8-sig", newline="") as stream:
            count = sum(1 for _ in csv.DictReader(stream))
        rejected: list[dict[str, Any]] = []
    else:
        if not args.report or not args.data_root:
            parser.error("--report and --data-root are required unless --prebuilt-data-yaml is supplied")
        data_yaml, count, rejected = prepare_subset(args.report, args.data_root, args.output / "dataset", args.exclude_file, args.fraction, args.seed)
    print(json.dumps({"event": "subset_ready", "images": count, "data_yaml": data_yaml.name, "excluded_hash_mismatches": rejected}, ensure_ascii=False), flush=True)
    if args.prepare_only:
        print(json.dumps({"event": "prepare_complete", "images": count, "output": data_yaml.parent.name}, ensure_ascii=False), flush=True)
        return 0
    report = run_loader_benchmark(data_yaml, args.yolo_root, args.output, args.workers, args.batch_size, args.imgsz, args.repeats)
    print(json.dumps({"event": "benchmark_complete", "summary": report["summary"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
