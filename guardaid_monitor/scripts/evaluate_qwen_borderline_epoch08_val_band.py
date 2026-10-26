from __future__ import annotations

import csv
import configparser
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
TRAIN_RUN = (
    ROOT
    / "model_runs"
    / "mc3_temporal_shift_letterbox_4dataset_4s16f2_k400"
    / "run_temporal_shift_letterbox_ui_20261015_194612_288156"
)
OUTPUT = ROOT / "model_runs" / "qwen3vl_borderline_epoch08_val29_20261017"
MODEL_CACHE = Path("C:/tmp/guardaid-hf")


def emit(message: str, log_path: Path | None = None) -> None:
    print(message, flush=True)
    if log_path is not None:
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(message + "\n")


def safe_divide(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def confusion(labels: list[int], predictions: list[int]) -> dict[str, int | float | None]:
    tp = sum(y == 1 and p == 1 for y, p in zip(labels, predictions))
    tn = sum(y == 0 and p == 0 for y, p in zip(labels, predictions))
    fp = sum(y == 0 and p == 1 for y, p in zip(labels, predictions))
    fn = sum(y == 1 and p == 0 for y, p in zip(labels, predictions))
    precision = safe_divide(tp, tp + fp)
    recall = safe_divide(tp, tp + fn)
    specificity = safe_divide(tn, tn + fp)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else 0.0
    )
    return {
        "n": len(labels),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "accuracy": safe_divide(tp + tn, len(labels)),
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "f1": f1,
    }


def find_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for package in local.glob("Gyan.FFmpeg*"):
        matches = list(package.glob("ffmpeg-*-full_build*/bin/ffmpeg.exe"))
        if matches:
            return str(matches[0])
    raise FileNotFoundError("FFmpeg executable was not found.")


def prepare_clip(
    ffmpeg: str,
    source: Path,
    start: float,
    end: float,
    destination: Path,
) -> float:
    duration = end - start
    if duration <= 0:
        raise ValueError(f"Invalid review window: {start}..{end}")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if source.is_dir():
        metadata = configparser.ConfigParser()
        metadata.read(source / "seqinfo.ini", encoding="utf-8")
        if not metadata.has_section("Sequence"):
            raise ValueError(f"DanceTrack sequence has no [Sequence] metadata: {source}")
        sequence = metadata["Sequence"]
        frame_rate = float(sequence["frameRate"])
        image_dir = source / sequence.get("imDir", "img1")
        image_extension = sequence.get("imExt", ".jpg")
        first_frame = math.floor(start * frame_rate + 1e-8) + 1
        first_image = image_dir / f"{first_frame:08d}{image_extension}"
        if not first_image.is_file():
            raise FileNotFoundError(first_image)
        pattern = image_dir / f"%08d{image_extension}"
        command.extend(
            [
                "-framerate",
                f"{frame_rate:g}",
                "-start_number",
                str(first_frame),
                "-i",
                str(pattern),
            ]
        )
    else:
        command.extend(["-ss", f"{start:.6f}", "-i", str(source)])
    command.extend(
        [
            "-t",
            f"{duration:.6f}",
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            "fps=12",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "26",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(destination),
        ]
    )
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError(
            f"Could not prepare review clip ({completed.stderr.strip()[-500:]})"
        )
    return duration


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    log_path = OUTPUT / "progress.log"
    emit("[preflight] Loading saved Epoch 8 validation outputs and frozen thresholds.", log_path)
    thresholds = json.loads((TRAIN_RUN / "thresholds.json").read_text(encoding="utf-8"))
    report = json.loads((TRAIN_RUN / "final_report.json").read_text(encoding="utf-8"))
    sensitive = float(thresholds["selection"]["sensitive"]["threshold"])
    certain = float(thresholds["selection"]["certain"]["threshold"])
    if report.get("best_epoch") != 8:
        raise RuntimeError(f"Expected Epoch 8 best checkpoint; report says {report.get('best_epoch')}")

    with (TRAIN_RUN / "validation_predictions.csv").open(encoding="utf-8-sig", newline="") as stream:
        all_rows = list(csv.DictReader(stream))
    with (TRAIN_RUN / "validation_window_predictions.csv").open(encoding="utf-8-sig", newline="") as stream:
        window_rows = list(csv.DictReader(stream))

    candidates = [
        row
        for row in all_rows
        if sensitive <= float(row["probability_fight"]) < certain
    ]
    if len(candidates) != 29:
        raise RuntimeError(f"Expected 29 borderline validation bags, found {len(candidates)}")

    selected: list[dict[str, str | float | int]] = []
    for row in candidates:
        matches = [item for item in window_rows if item["path"] == row["path"]]
        if not matches:
            raise RuntimeError(f"No window predictions found for {Path(row['path']).name}")
        top_window = max(matches, key=lambda item: float(item["probability_fight"]))
        bag_probability = float(row["probability_fight"])
        window_probability = float(top_window["probability_fight"])
        if not math.isclose(bag_probability, window_probability, rel_tol=1e-5, abs_tol=1e-6):
            raise RuntimeError(
                f"Bag/window max mismatch for {Path(row['path']).name}: "
                f"{bag_probability} versus {window_probability}"
            )
        source = Path(row["path"])
        if not source.is_file() and not (source.is_dir() and (source / "seqinfo.ini").is_file()):
            raise FileNotFoundError(source)
        selected.append(
            {
                "dataset": row["dataset"],
                "class_name": row["class_name"],
                "label": int(row["label"]),
                "source_name": source.name,
                "source_relative": str(source.relative_to(ROOT / "GuardAId Video Classification Model Dataset")),
                "source_path": str(source),
                "bag_probability": bag_probability,
                "start_seconds": float(top_window["start_seconds"]),
                "end_seconds": float(top_window["end_seconds"]),
                "window_probability": window_probability,
            }
        )
    if len({str(item["source_path"]) for item in selected}) != len(selected):
        raise RuntimeError("The selected validation bags contain duplicate video paths.")

    ffmpeg = find_ffmpeg()
    if not (MODEL_CACHE / "models--Qwen--Qwen3-VL-2B-Instruct" / "refs" / "main").is_file():
        raise FileNotFoundError("The Qwen3-VL-2B-Instruct snapshot is not cached locally.")

    # Keep inference strictly offline: this evaluation may only read the existing snapshot.
    os.environ["HF_HOME"] = str(MODEL_CACHE)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    sys.path.insert(0, str(ROOT / "guardaid_monitor"))

    from app.domain import VlmSettings
    from app.inference.vlm import QwenNativeVideoRunner

    settings = VlmSettings(
        model_id="Qwen/Qwen3-VL-2B-Instruct",
        quantization="NF4 4-bit",
        input_fps=2.0,
        video_token_budget=2048,
        max_output_tokens=220,
        borderline_review_enabled=True,
    )
    runner = QwenNativeVideoRunner(MODEL_CACHE)
    emit(
        f"[preflight] candidates={len(selected)}; interval=[{sensitive:.9f}, {certain:.9f}); "
        f"model={settings.model_id}; fps={settings.input_fps}; output={OUTPUT}",
        log_path,
    )
    emit("[preflight] Qwen reasoning mode: no thinking flag is passed; Instruct checkpoint selected.", log_path)
    prior_results: dict[int, dict[str, object]] = {}
    previous_model_loads: list[float] = []
    if log_path.is_file():
        log_text = log_path.read_text(encoding="utf-8")
        prior_pattern = re.compile(
            r"^\[review (\d{2})/29\] (.+?) / (.+?) MC3=([0-9.]+) "
            r"Qwen=(Fight|NonFight) actual=(Fight|NonFight) "
            r"uncertainty=(low|medium|high) infer=([0-9.]+)s$"
        )
        for line in log_text.splitlines():
            match = prior_pattern.match(line)
            if not match:
                continue
            index = int(match.group(1))
            item = selected[index - 1]
            prediction = int(match.group(5) == "Fight")
            prior_results[index] = {
                "index": index,
                "dataset": item["dataset"],
                "source_name": item["source_name"],
                "source_relative": item["source_relative"],
                "actual": item["label"],
                "actual_class": item["class_name"],
                "mc3_probability": item["bag_probability"],
                "review_window_start_seconds": item["start_seconds"],
                "review_window_end_seconds": item["end_seconds"],
                "review_window_seconds": float(item["end_seconds"]) - float(item["start_seconds"]),
                "qwen_prediction": prediction,
                "qwen_label": "Fight" if prediction else "NonFight",
                "uncertainty": match.group(7),
                "brief_reason": "",
                "visible_evidence": [],
                "explicit_thinking_marker": None,
                "clip_preparation_seconds": None,
                "qwen_inference_seconds": float(match.group(8)),
                "resumed_from_initial_progress_log": True,
                "status": "completed",
            }
        previous_model_loads = [
            float(value)
            for value in re.findall(r"\[model\] Ready in ([0-9.]+)s", log_text)
        ]

    # The first attempt completed 24 valid reviews; review 25 was rejected by
    # the production parser because its JSON label contradicted raise_alert.
    # Preserve that observed failure rather than silently rerunning/guessing it.
    if 25 not in prior_results and all(index in prior_results for index in range(1, 25)):
        item = selected[24]
        prior_results[25] = {
            "index": 25,
            "dataset": item["dataset"],
            "source_name": item["source_name"],
            "source_relative": item["source_relative"],
            "actual": item["label"],
            "actual_class": item["class_name"],
            "mc3_probability": item["bag_probability"],
            "review_window_start_seconds": item["start_seconds"],
            "review_window_end_seconds": item["end_seconds"],
            "review_window_seconds": float(item["end_seconds"]) - float(item["start_seconds"]),
            "qwen_prediction": None,
            "qwen_label": "invalid_response",
            "uncertainty": None,
            "brief_reason": "Rejected: decision_label did not match raise_alert.",
            "visible_evidence": [],
            "explicit_thinking_marker": None,
            "clip_preparation_seconds": None,
            "qwen_inference_seconds": None,
            "status": "failed_invalid_model_response",
        }
        emit(
            f"[review 25/29] {item['dataset']} / {item['source_name']} FAILED: "
            "ValueError (decision_label did not match raise_alert); not retried, matching app behavior.",
            log_path,
        )

    pending_indices = [index for index in range(1, len(selected) + 1) if index not in prior_results]
    load_seconds = 0.0
    run_started = time.perf_counter()
    if pending_indices:
        emit(
            f"[model] Loading local NF4 model for remaining {len(pending_indices)} reviews; "
            "network access disabled.",
            log_path,
        )
        load_started = time.perf_counter()
        runner.load(settings.model_id, settings.video_token_budget)
        load_seconds = time.perf_counter() - load_started
        emit(f"[model] Ready in {load_seconds:.2f}s; continue at {pending_indices[0]}/29.", log_path)

        with tempfile.TemporaryDirectory(prefix="qwen_borderline_", dir=str(OUTPUT)) as temp_dir:
            temp_root = Path(temp_dir)
            for index in pending_indices:
                item = selected[index - 1]
                clip_path = temp_root / f"review_{index:02d}.mp4"
                prep_started = time.perf_counter()
                clip_seconds = prepare_clip(
                    ffmpeg,
                    Path(str(item["source_path"])),
                    float(item["start_seconds"]),
                    float(item["end_seconds"]),
                    clip_path,
                )
                preparation_seconds = time.perf_counter() - prep_started
                inference_started = time.perf_counter()
                try:
                    review = runner.review_borderline(clip_path, settings)
                    inference_seconds = time.perf_counter() - inference_started
                    raw = str(review.get("raw_output", ""))
                    has_think_marker: bool | None = bool(
                        re.search(r"<(?:think|analysis)>", raw, flags=re.I)
                    )
                    prediction: int | None = int(bool(review["raise_alert"]))
                    result = {
                        "index": index,
                        "dataset": item["dataset"],
                        "source_name": item["source_name"],
                        "source_relative": item["source_relative"],
                        "actual": item["label"],
                        "actual_class": item["class_name"],
                        "mc3_probability": item["bag_probability"],
                        "review_window_start_seconds": item["start_seconds"],
                        "review_window_end_seconds": item["end_seconds"],
                        "review_window_seconds": clip_seconds,
                        "qwen_prediction": prediction,
                        "qwen_label": "Fight" if prediction else "NonFight",
                        "uncertainty": review["uncertainty"],
                        "brief_reason": review["brief_reason"],
                        "visible_evidence": review["visible_evidence"],
                        "explicit_thinking_marker": has_think_marker,
                        "clip_preparation_seconds": preparation_seconds,
                        "qwen_inference_seconds": inference_seconds,
                        "status": "completed",
                    }
                    emit(
                        f"[review {index:02d}/{len(selected)}] {item['dataset']} / {item['source_name']} "
                        f"MC3={float(item['bag_probability']):.4f} "
                        f"Qwen={'Fight' if prediction else 'NonFight'} "
                        f"actual={item['class_name']} uncertainty={review['uncertainty']} "
                        f"infer={inference_seconds:.2f}s",
                        log_path,
                    )
                except ValueError as exc:
                    inference_seconds = time.perf_counter() - inference_started
                    result = {
                        "index": index,
                        "dataset": item["dataset"],
                        "source_name": item["source_name"],
                        "source_relative": item["source_relative"],
                        "actual": item["label"],
                        "actual_class": item["class_name"],
                        "mc3_probability": item["bag_probability"],
                        "review_window_start_seconds": item["start_seconds"],
                        "review_window_end_seconds": item["end_seconds"],
                        "review_window_seconds": clip_seconds,
                        "qwen_prediction": None,
                        "qwen_label": "invalid_response",
                        "uncertainty": None,
                        "brief_reason": str(exc),
                        "visible_evidence": [],
                        "explicit_thinking_marker": None,
                        "clip_preparation_seconds": preparation_seconds,
                        "qwen_inference_seconds": inference_seconds,
                        "status": "failed_invalid_model_response",
                    }
                    emit(
                        f"[review {index:02d}/{len(selected)}] {item['dataset']} / {item['source_name']} "
                        f"FAILED: {type(exc).__name__} ({exc}); not retried, matching app behavior.",
                        log_path,
                    )
                prior_results[index] = result
                clip_path.unlink(missing_ok=True)
        runner.unload()
    total_seconds = time.perf_counter() - run_started

    outcomes = [prior_results[index] for index in range(1, len(selected) + 1)]
    valid_outcomes = [item for item in outcomes if item["qwen_prediction"] in {0, 1}]
    labels = [int(item["actual"]) for item in valid_outcomes]
    qwen_predictions = [int(item["qwen_prediction"]) for item in valid_outcomes]
    baseline_candidate_predictions = [1] * len(selected)
    candidate_qwen_metrics = confusion(labels, qwen_predictions)
    candidate_baseline_metrics = confusion(
        [int(item["label"]) for item in selected], baseline_candidate_predictions
    )

    all_labels = [int(row["label"]) for row in all_rows]
    all_probabilities = [float(row["probability_fight"]) for row in all_rows]
    baseline_all = [int(probability >= sensitive) for probability in all_probabilities]
    outcome_by_path = {
        str(selected[int(item["index"]) - 1]["source_path"]): int(item["qwen_prediction"])
        for item in valid_outcomes
    }
    hybrid_all = [
        outcome_by_path.get(row["path"], int(float(row["probability_fight"]) >= sensitive))
        for row in all_rows
    ]
    summary = {
        "experiment": "Offline Qwen Borderline review of Epoch 8 MC3 validation band",
        "status": "completed" if len(valid_outcomes) == len(selected) else "completed_with_review_failures",
        "source_training_run": str(TRAIN_RUN),
        "source_epoch": 8,
        "validation_total_bags": len(all_rows),
        "validation_borderline_bags": len(selected),
        "qwen_valid_decisions": len(valid_outcomes),
        "qwen_invalid_or_failed_decisions": len(selected) - len(valid_outcomes),
        "threshold_interval": {
            "sensitive": sensitive,
            "moderate": float(thresholds["selection"]["moderate"]["threshold"]),
            "certain": certain,
            "candidate_rule": "sensitive <= bag_probability < certain",
        },
        "candidate_label_distribution": {
            "Fight": sum(int(item["label"]) for item in selected),
            "NonFight": len(selected) - sum(int(item["label"]) for item in selected),
        },
        "qwen": {
            "model_id": settings.model_id,
            "quantization": settings.quantization,
            "input_fps": settings.input_fps,
            "video_token_budget": settings.video_token_budget,
            "max_output_tokens": settings.max_output_tokens,
            "thinking_flag_passed": False,
            "explicit_thinking_marker_outputs_scanned": sum(
                item["explicit_thinking_marker"] is True for item in valid_outcomes
            ),
            "returned_texts_scanned_for_thinking_markers": sum(
                item["explicit_thinking_marker"] is not None for item in valid_outcomes
            ),
            "interpretation": "Instruct checkpoint; no explicit thinking mode requested. Earlier completed outputs were not retained verbatim, so marker scan is partial; hidden internal computation cannot be inspected.",
        },
        "candidate_band_baseline_no_review": candidate_baseline_metrics,
        "candidate_band_qwen_as_decision": candidate_qwen_metrics,
        "whole_validation_mc3_sensitive_threshold": confusion(all_labels, baseline_all),
        "whole_validation_counterfactual_qwen_override_in_borderline_band": confusion(all_labels, hybrid_all),
        "timing": {
            "model_load_seconds_this_resume": load_seconds,
            "previous_model_load_seconds": previous_model_loads,
            "valid_decision_count": len(valid_outcomes),
            "invalid_response_count": len(selected) - len(valid_outcomes),
            "measured_successful_qwen_inference_seconds": sum(
                float(item["qwen_inference_seconds"]) for item in valid_outcomes
            ),
            "mean_successful_qwen_inference_seconds_per_clip": statistics.mean(
                float(item["qwen_inference_seconds"]) for item in valid_outcomes
            ),
            "median_successful_qwen_inference_seconds_per_clip": statistics.median(
                float(item["qwen_inference_seconds"]) for item in valid_outcomes
            ),
            "clip_preparation_seconds_recorded_for": sum(
                item["clip_preparation_seconds"] is not None for item in outcomes
            ),
            "resume_session_wall_seconds": total_seconds,
        },
        "production_behavior_note": "This is an offline counterfactual classification evaluation. The deployed Borderline feature records the alert first and adds notifications only after a valid Qwen-positive decision; it does not replace MC3 classifications or retract an alert.",
        "per_sample_results": outcomes,
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    result_fields = [
        "index", "dataset", "source_name", "source_relative", "actual", "actual_class", "mc3_probability",
        "review_window_start_seconds", "review_window_end_seconds", "review_window_seconds",
        "qwen_prediction", "qwen_label", "uncertainty", "brief_reason", "visible_evidence",
        "explicit_thinking_marker", "clip_preparation_seconds", "qwen_inference_seconds", "status",
        "resumed_from_initial_progress_log",
    ]
    with (OUTPUT / "per_sample_results.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=result_fields)
        writer.writeheader()
        for item in outcomes:
            row = dict(item)
            row["visible_evidence"] = json.dumps(row["visible_evidence"], ensure_ascii=False)
            writer.writerow(row)
    emit(
        f"[complete] candidates={len(selected)} valid={len(valid_outcomes)} "
        f"failed={len(selected)-len(valid_outcomes)}; "
        f"mean_successful_inference={summary['timing']['mean_successful_qwen_inference_seconds_per_clip']:.2f}s/clip; "
        f"output={OUTPUT}",
        log_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
