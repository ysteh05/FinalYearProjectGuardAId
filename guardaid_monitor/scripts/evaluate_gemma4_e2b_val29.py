from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime
import hashlib
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
GUARD_ROOT = ROOT / "guardaid_monitor"
SCRIPTS_ROOT = GUARD_ROOT / "scripts"
sys.path.insert(0, str(GUARD_ROOT))
sys.path.insert(0, str(SCRIPTS_ROOT))

from app.vlm_contract import SCENE_SCHEMA, SCENE_SYSTEM_PROMPT, scene_prompt  # noqa: E402
from evaluate_minicpm47_borderline_epoch08_val29 import (  # noqa: E402
    extract_frames,
    find_ffmpeg,
    resize_frames_to_short_side,
    select_candidates,
)


MODEL = "gemma4:e2b"
FRAME_SOURCE: Path | None = None
RUN_THINK_ON = True
RUN_BINARY_ONLY = False
API = "http://127.0.0.1:11434"
FPS = 2
FRAME_COUNT = 8
SHORT_SIDE = 360
SEED = 20261017
BINARY_NUM_CTX = 24576
BINARY_NUM_PREDICT = 12
SCENE_NUM_CTX = 32768
SCENE_NUM_PREDICT = 1024
SCENE_SYSTEM = SCENE_SYSTEM_PROMPT
SCENE_USER = scene_prompt("violence")
SOURCE_MINICPM_RUN = ROOT / "model_runs" / "minicpmv47_1b_ollama_borderline_val29_binary_rerun_20261017"
SOURCE_TRAIN_RUN = (
    ROOT
    / "model_runs"
    / "mc3_temporal_shift_letterbox_4dataset_4s16f2_k400"
    / "run_temporal_shift_letterbox_ui_20261015_194612_288156"
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def emit(message: str, log_path: Path) -> None:
    print(message, flush=True)
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(message + "\n")


def api(path: str, payload: dict[str, Any] | None = None, timeout: int = 600) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(API + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"Ollama HTTP {exc.code} at {path}: {body}") from exc


def duplicate_key_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate JSON key: {key}")
        value[key] = item
    return value


def validate_scene(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(raw, object_pairs_hook=duplicate_key_object)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"invalid_json: {exc}"
    if not isinstance(value, dict) or set(value) != {"summary", "details", "uncertainty"}:
        return None, "expected exactly summary, details, uncertainty"
    summary = value["summary"]
    details = value["details"]
    if not isinstance(summary, str) or not summary.strip():
        return None, "summary must be a nonempty string"
    if len(summary) > 240 or len(summary.split()) > 25:
        return None, "summary exceeds production length limit"
    if not isinstance(details, list):
        return None, "details must be an array"
    for index, point in enumerate(details):
        if not isinstance(point, str) or len(point) > 80 or len(point.split()) > 12:
            return None, f"detail {index} exceeds production length limit"
        if not point.strip() or not point.endswith((".", "!", "?")) or any(mark in point for mark in (",", ";")):
            return None, f"detail {index} is not a complete standalone sentence"
    if value["uncertainty"] not in {"low", "medium", "high"}:
        return None, "uncertainty must be low, medium or high"
    return value, None


def binary_metrics(labels: list[int], predictions: list[int]) -> dict[str, Any]:
    if len(labels) != len(predictions):
        raise ValueError("Metric inputs must have equal lengths")
    tp = sum(y == 1 and p == 1 for y, p in zip(labels, predictions))
    tn = sum(y == 0 and p == 0 for y, p in zip(labels, predictions))
    fp = sum(y == 0 and p == 1 for y, p in zip(labels, predictions))
    fn = sum(y == 1 and p == 0 for y, p in zip(labels, predictions))

    def ratio(a: int, b: int) -> float | None:
        return a / b if b else None

    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    return {
        "n": len(labels),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "accuracy": ratio(tp + tn, len(labels)),
        "precision": precision,
        "recall": recall,
        "specificity": ratio(tn, tn + fp),
        "false_positive_rate": ratio(fp, fp + tn),
        "f1": (2 * precision * recall / (precision + recall)) if precision is not None and recall is not None and precision + recall else 0.0,
    }


def percentile_nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def load_existing_rows() -> dict[tuple[str, int], dict[str, Any]]:
    result: dict[tuple[str, int], dict[str, Any]] = {}
    jsonl_path = OUTPUT / "results.jsonl"
    if not jsonl_path.exists():
        return result
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = (str(row["task"]), int(row["index"]))
        result[key] = row
    return result


def append_result(row: dict[str, Any]) -> None:
    with (OUTPUT / "results.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def sanitize_saved_thinking() -> int:
    path = OUTPUT / "results.jsonl"
    if not path.is_file():
        raise FileNotFoundError(path)
    serialized: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row.pop("think_text", None)
        serialized.append(json.dumps(row, ensure_ascii=False, allow_nan=False))
    temp = path.with_suffix(".jsonl.tmp")
    temp.write_text("\n".join(serialized) + "\n", encoding="utf-8")
    temp.replace(path)
    return len(serialized)


def prepare_sample_frames(ffmpeg: str, item: dict[str, Any]) -> tuple[list[bytes], dict[str, Any]]:
    index = int(item["index"])
    sample_dir = OUTPUT / "input_frames" / f"sample_{index:02d}"
    expected_paths = [sample_dir / f"frame_{frame_index:02d}.jpg" for frame_index in range(FRAME_COUNT)]
    if all(path.is_file() for path in expected_paths):
        frames = [path.read_bytes() for path in expected_paths]
        digests = [hashlib.sha256(frame).hexdigest() for frame in frames]
        return frames, {"cached": True, "jpeg_sha256": digests, "frame_count": len(frames)}

    if FRAME_SOURCE is not None:
        source_dir = FRAME_SOURCE / "input_frames" / f"sample_{index:02d}"
        source_manifest_path = source_dir / "frame_manifest.json"
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        source_sample = source_manifest.get("sample", {})
        expected_sample = {
            "source_relative": item["source_relative"],
            "window_start_seconds": float(item["window_start_seconds"]),
            "window_end_seconds": float(item["window_end_seconds"]),
            "actual": int(item["label"]),
        }
        for key, expected in expected_sample.items():
            if source_sample.get(key) != expected:
                raise RuntimeError(
                    f"Cached input mismatch for sample {index}, field {key}: "
                    f"expected {expected!r}, got {source_sample.get(key)!r}"
                )
        source_paths = [source_dir / f"frame_{frame_index:02d}.jpg" for frame_index in range(FRAME_COUNT)]
        if not all(path.is_file() for path in source_paths):
            raise FileNotFoundError(f"Incomplete reusable frame set for sample {index}: {source_dir}")
        frames = [path.read_bytes() for path in source_paths]
        digests = [hashlib.sha256(frame).hexdigest() for frame in frames]
        if source_manifest.get("jpeg_sha256") != digests:
            raise RuntimeError(f"Cached frame hash mismatch for sample {index}: {source_dir}")
        sample_dir.mkdir(parents=True, exist_ok=True)
        for path, frame in zip(expected_paths, frames):
            path.write_bytes(frame)
        metadata = {key: value for key, value in source_manifest.items() if key != "sample"}
        metadata["cached"] = True
        metadata["reused_from"] = str(FRAME_SOURCE.resolve())
        return frames, metadata

    raw_frames = extract_frames(
        ffmpeg,
        item,
        OUTPUT / "frame_work" / f"sample_{index:02d}",
        fps=FPS,
        frame_count=FRAME_COUNT,
    )
    frames, original_dimensions, model_dimensions = resize_frames_to_short_side(raw_frames, SHORT_SIDE)
    sample_dir.mkdir(parents=True, exist_ok=True)
    for frame_index, frame in enumerate(frames):
        expected_paths[frame_index].write_bytes(frame)
    digests = [hashlib.sha256(frame).hexdigest() for frame in frames]
    metadata = {
        "cached": False,
        "frame_count": len(frames),
        "fps": FPS,
        "offsets_seconds": [round(frame_index / FPS, 3) for frame_index in range(FRAME_COUNT)],
        "source_dimensions": original_dimensions,
        "model_dimensions": model_dimensions,
        "jpeg_sha256": digests,
    }
    write_json(sample_dir / "frame_manifest.json", metadata)
    return frames, metadata


def call_model(
    *,
    prompt: str,
    frames: list[bytes],
    think: bool,
    task: str,
) -> dict[str, Any]:
    scene = task.startswith("scene_")
    options = {
        "temperature": 0,
        "seed": SEED,
        "num_predict": SCENE_NUM_PREDICT if scene else BINARY_NUM_PREDICT,
        "num_ctx": SCENE_NUM_CTX if scene else BINARY_NUM_CTX,
    }
    messages: list[dict[str, Any]] = []
    qwen_no_think_workaround = MODEL.startswith("qwen3-vl:") and not think
    if scene and not qwen_no_think_workaround:
        messages.append({"role": "system", "content": SCENE_SYSTEM})
    prompt_content = prompt
    if qwen_no_think_workaround:
        if scene:
            prompt_content = SCENE_SYSTEM + "\n\n" + prompt_content
        prompt_content = prompt_content.rstrip() + "\n/no_think"
    messages.append(
        {
            "role": "user",
            "content": prompt_content,
            "images": [base64.b64encode(frame).decode("ascii") for frame in frames],
        }
    )
    if qwen_no_think_workaround:
        # Some Ollama Qwen3-VL tags use a bare template that ignores the API's
        # think=false switch. Mirror Ollama's documented workaround: explicit
        # no-think marker plus an empty closed think block before the answer.
        messages.append({"role": "assistant", "content": "<think>\n\n</think>\n\n"})
    payload: dict[str, Any] = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "think": think,
        "options": options,
    }
    if qwen_no_think_workaround:
        payload["raw"] = True
    if scene:
        payload["format"] = SCENE_SCHEMA
    started = time.perf_counter()
    try:
        response = api("/api/chat", payload)
        wall_seconds = time.perf_counter() - started
        message = response.get("message", {})
        raw = str(message.get("content", ""))
        thinking = str(message.get("thinking", ""))
        parsed = None
        validation_error = None
        prediction = None
        if task == "binary_nonthinking":
            stripped = raw.strip()
            if stripped in {"0", "1"}:
                prediction = int(stripped)
            else:
                validation_error = "binary output must be exactly one character: 0 or 1"
        else:
            parsed, validation_error = validate_scene(raw)
        return {
            "task": task,
            "index": None,
            "model": MODEL,
            "think_requested": think,
            "think_text_nonempty": bool(thinking.strip()),
            "raw_response": raw,
            "parsed": parsed,
            "prediction": prediction,
            "response_valid": validation_error is None,
            "validation_error": validation_error,
            "options": options,
            "wall_seconds": wall_seconds,
            "ollama_total_seconds": response.get("total_duration", 0) / 1e9,
            "load_seconds": response.get("load_duration", 0) / 1e9,
            "prompt_eval_seconds": response.get("prompt_eval_duration", 0) / 1e9,
            "generation_seconds": response.get("eval_duration", 0) / 1e9,
            "prompt_eval_count": response.get("prompt_eval_count"),
            "generation_count": response.get("eval_count"),
            "done_reason": response.get("done_reason"),
            "error": None,
        }
    except Exception as exc:  # Preserve per-call failures; do not silently retry.
        return {
            "task": task,
            "index": None,
            "model": MODEL,
            "think_requested": think,
            "think_text_nonempty": False,
            "raw_response": "",
            "parsed": None,
            "prediction": None,
            "response_valid": False,
            "validation_error": None,
            "options": options,
            "wall_seconds": time.perf_counter() - started,
            "error": f"{type(exc).__name__}: {exc}",
        }


def create_metadata(output: Path, candidates: list[dict[str, Any]], tags: dict[str, Any], show: dict[str, Any], ps: dict[str, Any]) -> dict[str, Any]:
    prior = json.loads((SOURCE_MINICPM_RUN / "run_config.json").read_text(encoding="utf-8"))
    binary_prompt = str(prior["prompt"])
    metadata = {
        "experiment": f"{MODEL} 29-clip borderline validation" + (" binary-only" if RUN_BINARY_ONLY else " and scene-description thinking comparison"),
        "created_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "running",
        "model": MODEL,
        "ollama_version": api("/api/version"),
        "model_tag": next((model for model in tags.get("models", []) if model.get("name") == MODEL), None),
        "model_show": show,
        "initial_loaded_models": ps,
        "candidate_count": len(candidates),
        "labels": {"positive": sum(int(item["label"]) for item in candidates), "negative": sum(1 - int(item["label"]) for item in candidates)},
        "source_training_run": str(SOURCE_TRAIN_RUN),
        "source_epoch": 8,
        "input": {
            "selection": "The same saved 29 validation bags whose MC3 probability is sensitive <= p < certain.",
            "window": "The same highest-MC3-probability 4-second window used in the earlier MiniCPM/Qwen binary comparison.",
            "fps": FPS,
            "frame_count": FRAME_COUNT,
            "offsets_relative_to_window_seconds": [round(i / FPS, 3) for i in range(FRAME_COUNT)],
            "resize": f"Resize preserving aspect ratio so the short side is {SHORT_SIDE}px; Lanczos; JPEG quality 95, subsampling 0.",
            "same_prepared_frames_reused_for_all_three_tasks": not RUN_BINARY_ONLY,
            "same_prepared_frames_reused_for_binary_only": RUN_BINARY_ONLY,
        },
        "binary_task": {
            "think": False,
            "prompt": binary_prompt,
            "options": {"temperature": 0, "seed": SEED, "num_predict": BINARY_NUM_PREDICT, "num_ctx": BINARY_NUM_CTX},
        },
        "scene_task": {
            "system_prompt": SCENE_SYSTEM,
            "user_prompt": SCENE_USER,
            "format_schema": SCENE_SCHEMA,
            "comparison": "Not run in this binary-only evaluation." if RUN_BINARY_ONLY else ("Thinking-off and thinking-on use identical prompt, frames, schema, and generation options." if RUN_THINK_ON else "Full run evaluates thinking-off only; thinking-on was separately preflighted and failed the output contract."),
            "options_both_modes": {"temperature": 0, "seed": SEED, "num_predict": SCENE_NUM_PREDICT, "num_ctx": SCENE_NUM_CTX},
            "think_modes": [] if RUN_BINARY_ONLY else ([False, True] if RUN_THINK_ON else [False]),
            "sample_order": "Per clip, alternate which thinking mode runs first; calls remain serial, never concurrent.",
            "thinking_on_omission_reason": None if RUN_THINK_ON else "Thinking-on was preflighted separately and exhausted 2048 generated tokens in 276.21 seconds without valid JSON; it was excluded from the full run.",
            "qwen_no_think_control": (
                "For qwen3-vl tags, think=false is supplemented with the Ollama-documented raw/no-think marker and empty closed think prefix because the installed model template is bare and ignores the API switch."
                if MODEL.startswith("qwen3-vl:") else None
            ),
        },
        "references": {
            "source_binary_baseline_run": str(SOURCE_MINICPM_RUN),
            "production_scene_prompt_version": "minicpm_alert_context_v9 from app.vlm_contract.py; unchanged for both thinking modes",
            "interpretation": "This is an offline evaluation, not a production model or prompt change.",
        },
    }
    write_json(output / "run_config.json", metadata)
    (output / "binary_prompt.txt").write_text(binary_prompt + "\n", encoding="utf-8")
    (output / "scene_system_prompt.txt").write_text(SCENE_SYSTEM + "\n", encoding="utf-8")
    (output / "scene_user_prompt.txt").write_text(SCENE_USER + "\n", encoding="utf-8")
    return metadata


def update_progress_summary(candidates: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
    rows = list(load_existing_rows().values())
    by_task_index = {(str(row["task"]), int(row["index"])): row for row in rows}
    evaluated_scene_tasks = [] if RUN_BINARY_ONLY else (["scene_think_off"] + (["scene_think_on"] if RUN_THINK_ON else []))
    expected = {
        ("binary_nonthinking", int(item["index"])) for item in candidates
    } | {
        (task, int(item["index"]))
        for item in candidates
        for task in evaluated_scene_tasks
    }
    complete = expected.issubset(by_task_index.keys())
    result_by_index = {
        index: by_task_index.get(("binary_nonthinking", index))
        for index in range(1, len(candidates) + 1)
    }
    valid_binary = [row for row in result_by_index.values() if row and row.get("prediction") in {0, 1}]
    labels = [int(row["actual"]) for row in valid_binary]
    preds = [int(row["prediction"]) for row in valid_binary]
    candidate_metrics = binary_metrics(labels, preds) if valid_binary else None

    all_validation_rows: list[dict[str, Any]] = []
    validation_path = SOURCE_TRAIN_RUN / "validation_predictions.csv"
    with validation_path.open(encoding="utf-8-sig", newline="") as stream:
        all_validation_rows = list(csv.DictReader(stream))
    threshold_json = json.loads((SOURCE_TRAIN_RUN / "thresholds.json").read_text(encoding="utf-8"))
    sensitive = float(threshold_json["selection"]["sensitive"]["threshold"])
    certain = float(threshold_json["selection"]["certain"]["threshold"])
    gemma_by_path = {
        str(item["source_path"]): row.get("prediction")
        for item in candidates
        if (row := result_by_index.get(int(item["index"]))) is not None
    }
    all_labels = [int(row["label"]) for row in all_validation_rows]
    baseline = [int(float(row["probability_fight"]) >= sensitive) for row in all_validation_rows]
    hybrid = [
        gemma_by_path.get(row["path"], int(float(row["probability_fight"]) >= sensitive))
        if float(row["probability_fight"]) < certain
        else int(float(row["probability_fight"]) >= sensitive)
        for row in all_validation_rows
    ]
    hybrid_coverage = sum(path in gemma_by_path and gemma_by_path[path] in {0, 1} for path in gemma_by_path)

    scene_rows = {
        task: [by_task_index.get((task, index)) for index in range(1, len(candidates) + 1)]
        for task in evaluated_scene_tasks
    }
    review_path = OUTPUT / "scene_accuracy_review.csv"
    review_by_index: dict[int, dict[str, str]] = {}
    if review_path.is_file():
        with review_path.open(encoding="utf-8-sig", newline="") as stream:
            review_by_index = {int(row["index"]): row for row in csv.DictReader(stream)}
    scene_accuracy: dict[str, Any] = {}
    review_fields = {
        "scene_think_off": ("think_off_main_event_correct", "think_off_useful"),
        "scene_think_on": ("think_on_main_event_correct", "think_on_useful"),
    }
    for mode in evaluated_scene_tasks:
        correct_key, useful_key = review_fields[mode]
        rated = [review_by_index[index] for index in range(1, len(candidates) + 1) if index in review_by_index]
        scene_accuracy[mode] = {
            "reviewed_samples": len(rated),
            "main_event_correct_count": sum(int(row[correct_key]) for row in rated),
            "main_event_correct_out_of_29": f"{sum(int(row[correct_key]) for row in rated)}/{len(candidates)}" if len(rated) == len(candidates) else None,
            "helpful_count": sum(int(row[useful_key]) for row in rated),
            "helpful_out_of_29": f"{sum(int(row[useful_key]) for row in rated)}/{len(candidates)}" if len(rated) == len(candidates) else None,
        }
    timing: dict[str, Any] = {}
    for task in ("binary_nonthinking", "scene_think_off", "scene_think_on"):
        task_rows = [row for row in rows if row.get("task") == task and row.get("error") is None]
        durations = [float(row["wall_seconds"]) for row in task_rows]
        warm_durations = [max(0.0, float(row["wall_seconds"]) - float(row.get("load_seconds", 0))) for row in task_rows]
        steady_durations = warm_durations[1:] if task == "binary_nonthinking" and len(warm_durations) > 1 else warm_durations
        timing[task] = {
            "completed_calls": len(task_rows),
            "failed_calls": sum(row.get("task") == task and row.get("error") is not None for row in rows),
            "mean_wall_seconds_including_load": statistics.mean(durations) if durations else None,
            "median_wall_seconds_including_load": statistics.median(durations) if durations else None,
            "p95_wall_seconds_including_load_nearest_rank": percentile_nearest_rank(durations, 0.95),
            "mean_warm_request_seconds_excluding_load": statistics.mean(warm_durations) if warm_durations else None,
            "median_warm_request_seconds_excluding_load": statistics.median(warm_durations) if warm_durations else None,
            "p95_warm_request_seconds_excluding_load_nearest_rank": percentile_nearest_rank(warm_durations, 0.95),
            "steady_state_call_count_after_initial_visual_prefill": len(steady_durations),
            "mean_steady_state_request_seconds": statistics.mean(steady_durations) if steady_durations else None,
            "median_steady_state_request_seconds": statistics.median(steady_durations) if steady_durations else None,
            "p95_steady_state_request_seconds_nearest_rank": percentile_nearest_rank(steady_durations, 0.95),
            "mean_model_load_seconds": statistics.mean([float(row.get("load_seconds", 0)) for row in task_rows]) if task_rows else None,
            "mean_ollama_total_seconds": statistics.mean([float(row.get("ollama_total_seconds", 0)) for row in task_rows]) if task_rows else None,
            "mean_prompt_eval_seconds": statistics.mean([float(row.get("prompt_eval_seconds", 0)) for row in task_rows]) if task_rows else None,
            "mean_generation_seconds": statistics.mean([float(row.get("generation_seconds", 0)) for row in task_rows]) if task_rows else None,
            "nonempty_thinking_outputs": sum(bool(row.get("think_text_nonempty")) for row in task_rows),
        }
    report = {
        "experiment": metadata["experiment"],
        "status": "completed" if complete else "in_progress",
        "candidate_count": len(candidates),
        "label_distribution": metadata["labels"],
        "binary_nonthinking": {
            "valid_predictions": len(valid_binary),
            "invalid_or_failed": len(candidates) - len(valid_binary),
            "metrics_with_invalid_outputs_excluded": candidate_metrics,
            "baseline_mc3_sensitive_threshold": binary_metrics(all_labels, baseline),
            f"whole_validation_counterfactual_{MODEL.replace(':', '_').replace('-', '_')}_override_in_sensitive_to_certain_band": binary_metrics(all_labels, hybrid),
            "counterfactual_override_valid_count": hybrid_coverage,
            "thresholds": {"sensitive": sensitive, "certain": certain},
            "scope_note": f"Counterfactual assumes {MODEL} replaces MC3 decisions only for the 29 validation samples in the sensitive-to-certain interval; invalid responses fall back to MC3.",
        },
        "scene_description": {
            "scene_descriptions_generated": {task: sum(row is not None and row.get("error") is None for row in task_rows) for task, task_rows in scene_rows.items()},
            "schema_valid": {task: sum(row is not None and row.get("response_valid") is True for row in task_rows) for task, task_rows in scene_rows.items()},
            "content_accuracy": scene_accuracy if review_by_index else None,
            "review_method": "Binary 0/1 per sample: 1 only if the summary correctly identifies the main visible event/activity and is useful for alert review; otherwise 0. Assistant visual audit of the same eight 360p sampled frames; not independent expert annotation.",
        },
        "timing": timing,
        "results_file": str(OUTPUT / "results.jsonl"),
        "input_frame_directory": str(OUTPUT / "input_frames"),
        "progress_log": str(OUTPUT / "progress.log"),
    }
    write_json(OUTPUT / "summary.json", report)
    write_csv_exports(rows)
    return report


def write_csv_exports(rows: list[dict[str, Any]]) -> None:
    fields = [
        "task", "index", "dataset", "source_name", "actual", "mc3_probability",
        "window_start_seconds", "window_end_seconds", "prediction", "raw_response",
        "think_requested", "think_text_nonempty", "parsed", "response_valid", "validation_error",
        "wall_seconds", "ollama_total_seconds", "load_seconds", "prompt_eval_seconds",
        "generation_seconds", "prompt_eval_count", "generation_count", "done_reason", "error",
    ]
    with (OUTPUT / "per_sample_results.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in sorted(rows, key=lambda item: (int(item["index"]), str(item["task"]))):
            serializable = dict(row)
            for key in ("parsed",):
                if isinstance(serializable.get(key), (dict, list)):
                    serializable[key] = json.dumps(serializable[key], ensure_ascii=False)
            writer.writerow(serializable)


def make_contact_sheets(candidates: list[dict[str, Any]]) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return
    sheet_dir = OUTPUT / "visual_review_sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    tile_width, tile_height, label_height = 240, 135, 24
    samples_per_sheet = 4
    for batch_start in range(0, len(candidates), samples_per_sheet):
        batch = candidates[batch_start : batch_start + samples_per_sheet]
        columns, rows = 2, 2
        sheet_width = columns * (4 * tile_width) + (columns + 1) * 16
        sheet_height = rows * (2 * tile_height + label_height) + (rows + 1) * 16
        sheet = Image.new("RGB", (sheet_width, sheet_height), "#111827")
        draw = ImageDraw.Draw(sheet)
        for offset, item in enumerate(batch):
            grid_x, grid_y = offset % columns, offset // columns
            block_x = 16 + grid_x * (4 * tile_width + 16)
            block_y = 16 + grid_y * (2 * tile_height + label_height + 16)
            title = f"{int(item['index']):02d} {item['dataset']} | {item['source_name']} | y={item['label']} MC3={float(item['mc3_probability']):.3f}"
            draw.text((block_x, block_y), title[:100], fill="white", font=font)
            for frame_index in range(FRAME_COUNT):
                frame_path = OUTPUT / "input_frames" / f"sample_{int(item['index']):02d}" / f"frame_{frame_index:02d}.jpg"
                with Image.open(frame_path) as source:
                    thumb = source.convert("RGB")
                    thumb.thumbnail((tile_width, tile_height), Image.Resampling.LANCZOS)
                    x = block_x + (frame_index % 4) * tile_width
                    y = block_y + label_height + (frame_index // 4) * tile_height
                    sheet.paste(thumb, (x, y))
                    draw.text((x + 2, y + 2), str(frame_index), fill="#fbbf24", font=font)
        sheet.save(sheet_dir / f"samples_{batch[0]['index']:02d}_{batch[-1]['index']:02d}.jpg", quality=90)


def run_task(item: dict[str, Any], frames: list[bytes], task: str, think: bool, prompt: str, existing: dict[tuple[str, int], dict[str, Any]], log_path: Path) -> dict[str, Any]:
    index = int(item["index"])
    key = (task, index)
    if key in existing:
        return existing[key]
    row = call_model(prompt=prompt, frames=frames, think=think, task=task)
    row.update({
        "index": index,
        "dataset": item["dataset"],
        "source_name": item["source_name"],
        "source_relative": item["source_relative"],
        "source_path": item["source_path"],
        "actual": int(item["label"]),
        "actual_class": item["class_name"],
        "mc3_probability": float(item["mc3_probability"]),
        "window_start_seconds": float(item["window_start_seconds"]),
        "window_end_seconds": float(item["window_end_seconds"]),
        "frame_count": FRAME_COUNT,
        "fps": FPS,
        "short_side": SHORT_SIDE,
    })
    append_result(row)
    emit(
        f"[{task} {index:02d}/29] {item['dataset']} / {item['source_name']} "
        f"actual={item['class_name']} result={row.get('prediction') if task == 'binary_nonthinking' else ('valid' if row.get('response_valid') else 'invalid')} "
        f"think={think} wall={float(row.get('wall_seconds') or 0):.2f}s"
        + (f" ERROR={row['error']}" if row.get("error") else ""),
        log_path,
    )
    return row


def run_preflight(candidates: list[dict[str, Any]], ffmpeg: str, existing: dict[tuple[str, int], dict[str, Any]], log_path: Path) -> None:
    item = candidates[0]
    frames, frame_metadata = prepare_sample_frames(ffmpeg, item)
    write_json(OUTPUT / "input_frames" / "sample_01" / "frame_manifest.json", {
        **frame_metadata,
        "sample": {"dataset": item["dataset"], "source_name": item["source_name"], "source_relative": item["source_relative"], "actual": int(item["label"]), "mc3_probability": item["mc3_probability"], "window_start_seconds": item["window_start_seconds"], "window_end_seconds": item["window_end_seconds"]},
    })
    expected_tasks = ["binary_nonthinking"] if RUN_BINARY_ONLY else (["binary_nonthinking", "scene_think_off"] + (["scene_think_on"] if RUN_THINK_ON else []))
    emit(f"[preflight] Starting {len(expected_tasks)} requests on sample 1; these responses count as the first measured sample if successful.", log_path)
    run_task(item, frames, "binary_nonthinking", False, BINARY_PROMPT, existing, log_path)
    if not RUN_BINARY_ONLY:
        run_task(item, frames, "scene_think_off", False, SCENE_USER, existing, log_path)
        if RUN_THINK_ON:
            run_task(item, frames, "scene_think_on", True, SCENE_USER, existing, log_path)
    saved = load_existing_rows()
    preflight_rows = [saved.get((task, 1)) for task in expected_tasks]
    failures = [row for row in preflight_rows if row is None or row.get("error") or row.get("response_valid") is not True]
    if failures:
        raise RuntimeError("Preflight API call failed; stopping before remaining 28 clips. See progress.log/results.jsonl.")
    emit(f"[preflight] All {len(expected_tasks)} required API path(s) responded; continuing full 29-sample run.", log_path)


def main() -> int:
    global OUTPUT, BINARY_PROMPT, MODEL, FRAME_SOURCE, SCENE_NUM_PREDICT, BINARY_NUM_PREDICT, RUN_THINK_ON, RUN_BINARY_ONLY
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--model", default=MODEL, help="Installed Ollama model tag to evaluate.")
    parser.add_argument("--frames-from", type=Path, default=None, help="Reuse and verify prepared input frames from another completed evaluation.")
    parser.add_argument("--scene-num-predict", type=int, default=SCENE_NUM_PREDICT)
    parser.add_argument("--binary-num-predict", type=int, default=BINARY_NUM_PREDICT)
    parser.add_argument("--skip-scene-thinking-on", action="store_true", help="Run the valid binary and scene think-off paths only after a separate thinking-on preflight fails the output contract.")
    parser.add_argument("--binary-only", action="store_true", help="Evaluate only the non-thinking Borderline Review binary task on the 29 validation clips.")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--summarize-only", action="store_true")
    parser.add_argument("--sanitize-thinking-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    MODEL = args.model
    FRAME_SOURCE = args.frames_from.resolve() if args.frames_from else None
    RUN_THINK_ON = not args.skip_scene_thinking_on
    RUN_BINARY_ONLY = args.binary_only
    if args.scene_num_predict < 1 or args.binary_num_predict < 1:
        parser.error("token budgets must be positive integers")
    SCENE_NUM_PREDICT = args.scene_num_predict
    BINARY_NUM_PREDICT = args.binary_num_predict
    model_slug = MODEL.replace(":", "_").replace("/", "_").replace("-", "_")
    OUTPUT = (args.output or ROOT / "model_runs" / (model_slug + "_val29_scene_eval_" + datetime.now().strftime("%Y%m%d_%H%M%S"))).resolve()
    if args.sanitize_thinking_only:
        count = sanitize_saved_thinking()
        print(f"Removed stored internal thinking text while retaining per-sample nonempty flags; records={count}.", flush=True)
        return 0
    if args.self_test:
        assert binary_metrics([1, 1, 0, 0], [1, 0, 1, 0]) == {
            "n": 4, "tp": 1, "fp": 1, "fn": 1, "tn": 1,
            "accuracy": 0.5, "precision": 0.5, "recall": 0.5,
            "specificity": 0.5, "false_positive_rate": 0.5, "f1": 0.5,
        }
        assert validate_scene('{"summary":"People dance.","details":["A person moves.","Others clap."],"uncertainty":"low"}')[1] is None
        assert validate_scene('{"summary":"bad","summary":"duplicate","details":[],"uncertainty":"low"}')[1] is not None
        print("Self-tests passed: binary metrics, valid structured description, and duplicate-key rejection.", flush=True)
        return 0

    candidates, thresholds = select_candidates()
    candidates = [{"index": index, **item} for index, item in enumerate(candidates, start=1)]
    if len(candidates) != 29:
        raise RuntimeError(f"Expected 29 validation candidates, found {len(candidates)}")
    if args.summarize_only:
        metadata = json.loads((OUTPUT / "run_config.json").read_text(encoding="utf-8"))
        report = update_progress_summary(candidates, metadata)
        report["timing_interpretation"] = (
            "The initial run alternated binary and scene calls using different num_ctx values, "
            "which caused Ollama to reload the model. Raw wall times include variable load time. "
            "Warm request latency is estimated as wall_seconds minus Ollama load_duration; "
            "model load time is reported separately."
        )
        write_json(OUTPUT / "summary.json", report)
        print(f"Summary rebuilt from saved per-sample results: {OUTPUT / 'summary.json'}", flush=True)
        return 0
    BINARY_PROMPT = str(json.loads((SOURCE_MINICPM_RUN / "run_config.json").read_text(encoding="utf-8"))["prompt"])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    log_path = OUTPUT / "progress.log"
    emit(f"[start] model={MODEL}; candidates={len(candidates)}; output={OUTPUT}", log_path)
    emit(f"[threshold band] sensitive={thresholds['sensitive']:.8f}; certain={thresholds['certain']:.8f}", log_path)

    tags = api("/api/tags")
    tag = next((model for model in tags.get("models", []) if model.get("name") == MODEL), None)
    if tag is None:
        raise RuntimeError(f"Required local model is not installed: {MODEL}")
    show = api("/api/show", {"model": MODEL, "verbose": False}, timeout=30)
    ps = api("/api/ps", timeout=20)
    capabilities = set(show.get("capabilities", []))
    if "vision" not in capabilities:
        raise RuntimeError(f"Model does not report vision capability: {sorted(capabilities)}")
    if "thinking" not in capabilities:
        raise RuntimeError(f"Model does not report thinking capability: {sorted(capabilities)}")
    metadata = create_metadata(OUTPUT, candidates, tags, show, ps)
    if FRAME_SOURCE is not None:
        metadata["input"]["reused_prepared_frames_from"] = str(FRAME_SOURCE)
        write_json(OUTPUT / "run_config.json", metadata)
    write_json(OUTPUT / "selection_manifest.json", {
        "sensitive_threshold": thresholds["sensitive"],
        "certain_threshold": thresholds["certain"],
        "samples": [{key: item[key] for key in ("index", "dataset", "class_name", "label", "source_name", "source_relative", "source_path", "mc3_probability", "window_start_seconds", "window_end_seconds")} for item in candidates],
    })

    ffmpeg = find_ffmpeg()
    existing = load_existing_rows()
    expected_preflight_tasks = ["binary_nonthinking"] if RUN_BINARY_ONLY else (["binary_nonthinking", "scene_think_off"] + (["scene_think_on"] if RUN_THINK_ON else []))
    if not existing:
        run_preflight(candidates, ffmpeg, existing, log_path)
    elif not all((task, 1) in existing for task in expected_preflight_tasks):
        run_preflight(candidates, ffmpeg, existing, log_path)
    # The preflight request is itself the first scored sample. Reload its
    # persisted result so the main loop cannot append a duplicate row.
    existing = load_existing_rows()
    if args.preflight_only:
        update_progress_summary(candidates, metadata)
        metadata["status"] = "preflight_only"
        metadata["completed_rows"] = len(existing)
        metadata["expected_rows"] = len(candidates) if RUN_BINARY_ONLY else len(candidates) * (2 + int(RUN_THINK_ON))
        write_json(OUTPUT / "run_config.json", metadata)
        emit("[preflight-only] Stopped by request after sample 1; no remaining samples were run.", log_path)
        return 0

    for item in candidates:
        index = int(item["index"])
        frames, frame_metadata = prepare_sample_frames(ffmpeg, item)
        manifest_path = OUTPUT / "input_frames" / f"sample_{index:02d}" / "frame_manifest.json"
        if not manifest_path.exists():
            write_json(manifest_path, {
                **frame_metadata,
                "sample": {"dataset": item["dataset"], "source_name": item["source_name"], "source_relative": item["source_relative"], "actual": int(item["label"]), "mc3_probability": item["mc3_probability"], "window_start_seconds": item["window_start_seconds"], "window_end_seconds": item["window_end_seconds"]},
            })
        run_task(item, frames, "binary_nonthinking", False, BINARY_PROMPT, existing, log_path)
        if not RUN_BINARY_ONLY:
            scene_order = [("scene_think_off", False)]
            if RUN_THINK_ON:
                off_first = index % 2 == 1
                scene_order = [("scene_think_off", False), ("scene_think_on", True)] if off_first else [("scene_think_on", True), ("scene_think_off", False)]
            for task, think in scene_order:
                run_task(item, frames, task, think, SCENE_USER, existing, log_path)
        existing = load_existing_rows()
        report = update_progress_summary(candidates, metadata)
        progress_message = f"[progress] sample={index}/29; binary={report['binary_nonthinking']['valid_predictions']}/29"
        if not RUN_BINARY_ONLY:
            progress_message += f"; scene off={report['scene_description']['scene_descriptions_generated']['scene_think_off']}/29"
        if RUN_THINK_ON and not RUN_BINARY_ONLY:
            progress_message += f"; scene on={report['scene_description']['scene_descriptions_generated']['scene_think_on']}/29"
        emit(progress_message, log_path)
    make_contact_sheets(candidates)
    report = update_progress_summary(candidates, metadata)
    required_scene_tasks = [] if RUN_BINARY_ONLY else (["scene_think_off"] + (["scene_think_on"] if RUN_THINK_ON else []))
    report["status"] = "completed" if report["binary_nonthinking"]["valid_predictions"] == 29 and all(report["scene_description"]["scene_descriptions_generated"].get(task) == 29 and report["scene_description"]["schema_valid"].get(task) == 29 for task in required_scene_tasks) else "completed_with_failures"
    report["scene_description"]["content_accuracy_scoring"] = "Pending blinded visual review; JSON validity is not treated as semantic correctness."
    write_json(OUTPUT / "summary.json", report)
    emit(f"[complete] status={report['status']}; summary={OUTPUT / 'summary.json'}", log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
