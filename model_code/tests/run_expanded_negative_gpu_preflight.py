"""Run disposable YOLO batch/worker probes without starting a training run.

This must be executed as a real Python file on Windows so DataLoader workers can
use multiprocessing's spawn mode. The guard also prevents child workers from
re-running the preflight matrix.
"""

from __future__ import annotations

import gc
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model_code"))

import yolov9_weapon_pipeline as pipeline  # noqa: E402
from yolov9_weapon_dashboard import BREV_INFERENCE_ARTIFACT_DIR, EXPANDED_NEGATIVE_FINETUNE_PROFILE  # noqa: E402
from yolov9_weapon_workspace import MALL_FINETUNE_OUTPUT_ROOT, default_training_config  # noqa: E402


def main() -> int:
    checkpoint = BREV_INFERENCE_ARTIFACT_DIR / "best_training.pt"
    if not checkpoint.is_file():
        raise FileNotFoundError(f"BREV starting checkpoint not found: {checkpoint}")

    candidates = []
    for report_path in (MALL_FINETUNE_OUTPUT_ROOT / "datasets").glob("*/audit_report.json"):
        try:
            candidate = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if candidate.get("profile") != EXPANDED_NEGATIVE_FINETUNE_PROFILE or candidate.get("total_images") != 41684:
            continue
        if not Path(candidate.get("data_yaml", "")).is_file():
            continue
        if not Path(candidate.get("manifest", "")).is_file():
            continue
        candidates.append((report_path.stat().st_mtime, candidate))
    if not candidates:
        raise FileNotFoundError("No completed 41,684-image expanded-negative cache was found")
    report = max(candidates, key=lambda row: row[0])[1]

    cache_root = Path(report["cache_root"])
    for split, expected in report["images"].items():
        image_root = cache_root / "images" / split
        label_root = cache_root / "labels" / split
        image_count = sum(path.is_file() for path in image_root.iterdir())
        label_count = sum(path.is_file() for path in label_root.iterdir())
        if image_count != expected or label_count != expected:
            raise RuntimeError(
                f"Cached {split} file counts do not match the audit report: "
                f"images={image_count}, labels={label_count}, expected={expected}"
            )

    print(json.dumps({
        "event": "dataset_ready",
        "cache_root": report["cache_root"],
        "total_images": report["total_images"],
        "images": report["images"],
        "negative_source_total": report["negative_source_total"],
    }, ensure_ascii=False), flush=True)

    base = default_training_config()
    base.update({
        "dataset_profile": EXPANDED_NEGATIVE_FINETUNE_PROFILE,
        "class_names": ["knife", "gun"],
        "initial_weights_path": str(checkpoint.resolve()),
        "probe_weights_path": str(checkpoint.resolve()),
        "epochs_cap": 5,
        "patience": 0,
        "device": "0",
    })
    base = pipeline._validated_config(base)

    hyp_path = Path(tempfile.gettempdir()) / f"expanded_negative_probe_{int(time.time())}.yaml"
    try:
        hyp = pipeline._write_hyp(base, hyp_path)
        results = []
        for batch_size, workers in ((16, 4), (16, 2), (8, 4), (8, 2)):
            config = dict(base, batch_size=batch_size, workers=workers)
            print(json.dumps({"event": "probe_start", "batch_size": batch_size, "workers": workers}), flush=True)
            started = time.perf_counter()
            try:
                result = pipeline._probe_batch_time(
                    config,
                    report,
                    hyp,
                    Path(report["data_yaml"]),
                    allow_batch_fallback=False,
                )
                row = {
                    "batch_size": batch_size,
                    "workers": workers,
                    "ok": True,
                    "elapsed_seconds": round(time.perf_counter() - started, 2),
                    "probe": result,
                }
                results.append(row)
                print(json.dumps({"event": "probe_result", **row}, ensure_ascii=False), flush=True)
            except Exception as exc:  # keep testing the user's prescribed matrix
                row = {
                    "batch_size": batch_size,
                    "workers": workers,
                    "ok": False,
                    "elapsed_seconds": round(time.perf_counter() - started, 2),
                    "error": f"{type(exc).__name__}: {exc}",
                }
                results.append(row)
                print(json.dumps({"event": "probe_result", **row}, ensure_ascii=False), flush=True)
            finally:
                gc.collect()
                try:
                    import torch

                    torch.cuda.empty_cache()
                except Exception:
                    pass

        print(json.dumps({"event": "matrix_complete", "results": results}, ensure_ascii=False), flush=True)
        return 0 if any(row["ok"] for row in results) else 1
    finally:
        hyp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
