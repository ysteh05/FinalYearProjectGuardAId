from __future__ import annotations

import argparse
import base64
import configparser
import csv
import json
import math
import re
import shutil
import statistics
import subprocess
import tempfile
import time
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
TRAIN_RUN = (
    ROOT
    / "model_runs"
    / "mc3_temporal_shift_letterbox_4dataset_4s16f2_k400"
    / "run_temporal_shift_letterbox_ui_20261015_194612_288156"
)
PREVIOUS_QWEN_RUN = ROOT / "model_runs" / "qwen3vl_borderline_epoch08_val29_20261017"
MODEL = "openbmb/minicpm-v4.7:1b-q4_K_M"
QWEN_OLLAMA_MODEL = "qwen3-vl:2b-instruct-q4_K_M"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OUTPUT = ROOT / "model_runs" / "minicpmv47_1b_epoch08_val29_ctx16k_20261017"
MINICPM_RUN = DEFAULT_OUTPUT
SAMPLE_COUNT = 29
WINDOW_SECONDS = 4.0
NUM_CTX = 24576
PROMPT_2FPS = (
    "Classify the visible actions in this clip. Return 1 if the sequence shows credible "
    "aggressive physical contact or an apparent physical attack, such as punching, kicking, "
    "striking, forceful pushing, choking, aggressive grappling, dragging, or repeated "
    "assaultive contact. Return 0 for ordinary movement, sports, non-aggressive play, "
    "dancing, crowding, accidental contact, a fall without an attack, or when the frames "
    "do not visibly support physical violence. Use only the images, not audio. These 8 "
    "images are chronological samples at 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, and 3.5 "
    "seconds from the same 4-second window. Reply with exactly one character: 0 or 1. "
    "Do not include any other text."
)


def build_prompt(fps: int, frame_count: int) -> str:
    if fps == 2:
        # Preserve the exact prompt used by the existing native-resolution and 360p/2FPS runs.
        return PROMPT_2FPS
    offsets = ", ".join(f"{index / fps:.2f}" for index in range(frame_count))
    return (
        "Classify the visible actions in this clip. Return 1 if the sequence shows credible "
        "aggressive physical contact or an apparent physical attack, such as punching, kicking, "
        "striking, forceful pushing, choking, aggressive grappling, dragging, or repeated "
        "assaultive contact. Return 0 for ordinary movement, sports, non-aggressive play, "
        "dancing, crowding, accidental contact, a fall without an attack, or when the frames "
        "do not visibly support physical violence. Use only the images, not audio. These "
        f"{frame_count} images are chronological samples at {fps} FPS, at offsets {offsets} "
        "seconds from the same 4-second window. Reply with exactly one character: 0 or 1. "
        "Do not include any other text."
    )


def emit(message: str, log_path: Path) -> None:
    print(message, flush=True)
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(message + "\n")


def safe_divide(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def binary_metrics(labels: list[int], predictions: list[int]) -> dict[str, int | float | None]:
    if len(labels) != len(predictions):
        raise ValueError("Metric labels and predictions must have equal lengths.")
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
        "false_positive_rate": safe_divide(fp, fp + tn),
        "f1": f1,
    }


def find_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    winget_packages = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    for package in winget_packages.glob("Gyan.FFmpeg*"):
        matches = sorted(package.glob("ffmpeg-*-full_build*/bin/ffmpeg.exe"))
        if matches:
            return str(matches[0])
    raise FileNotFoundError("FFmpeg executable was not found.")


def select_candidates() -> tuple[list[dict[str, Any]], dict[str, float]]:
    thresholds = json.loads((TRAIN_RUN / "thresholds.json").read_text(encoding="utf-8"))
    report = json.loads((TRAIN_RUN / "final_report.json").read_text(encoding="utf-8"))
    if int(report.get("best_epoch", -1)) != 8:
        raise RuntimeError(f"Expected the selected Epoch 8 run; report says {report.get('best_epoch')}")
    sensitive = float(thresholds["selection"]["sensitive"]["threshold"])
    certain = float(thresholds["selection"]["certain"]["threshold"])
    if not 0 <= sensitive < certain <= 1:
        raise ValueError(f"Invalid saved threshold interval: {sensitive}..{certain}")

    with (TRAIN_RUN / "validation_predictions.csv").open(encoding="utf-8-sig", newline="") as stream:
        validation_rows = list(csv.DictReader(stream))
    with (TRAIN_RUN / "validation_window_predictions.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        window_rows = list(csv.DictReader(stream))

    borderline = [
        row
        for row in validation_rows
        if sensitive <= float(row["probability_fight"]) < certain
    ]
    if len(borderline) != SAMPLE_COUNT:
        raise RuntimeError(f"Expected {SAMPLE_COUNT} borderline bags, found {len(borderline)}")

    selected: list[dict[str, Any]] = []
    for row in borderline:
        matching_windows = [item for item in window_rows if item["path"] == row["path"]]
        if not matching_windows:
            raise RuntimeError(f"No window scores for {row['path']}")
        top_window = max(matching_windows, key=lambda item: float(item["probability_fight"]))
        bag_probability = float(row["probability_fight"])
        window_probability = float(top_window["probability_fight"])
        if not math.isclose(bag_probability, window_probability, rel_tol=1e-5, abs_tol=1e-6):
            raise RuntimeError(
                f"Bag score is not the maximum window score for {Path(row['path']).name}: "
                f"bag={bag_probability}, max_window={window_probability}"
            )
        source = Path(row["path"]).resolve()
        if not source.is_file() and not (source.is_dir() and (source / "seqinfo.ini").is_file()):
            raise FileNotFoundError(source)
        selected.append(
            {
                "dataset": row["dataset"],
                "class_name": row["class_name"],
                "label": int(row["label"]),
                "source_name": source.name,
                "source_relative": str(
                    source.relative_to(ROOT / "GuardAId Video Classification Model Dataset")
                ),
                "source_path": str(source),
                "mc3_probability": bag_probability,
                "window_start_seconds": float(top_window["start_seconds"]),
                "window_end_seconds": float(top_window["end_seconds"]),
                "window_probability": window_probability,
            }
        )
    if len({item["source_path"] for item in selected}) != SAMPLE_COUNT:
        raise RuntimeError("The borderline set contains duplicate source paths.")
    return selected, {"sensitive": sensitive, "certain": certain}


def extract_sequence_frames(
    source: Path, start: float, fps: int, frame_count: int
) -> list[bytes]:
    metadata = configparser.ConfigParser()
    metadata.read(source / "seqinfo.ini", encoding="utf-8-sig")
    if not metadata.has_section("Sequence"):
        raise ValueError(f"DanceTrack sequence has no [Sequence] metadata: {source}")
    sequence = metadata["Sequence"]
    rate = float(sequence["frameRate"])
    image_dir = source / sequence.get("imDir", "img1")
    extension = sequence.get("imExt", ".jpg")
    width = int(sequence.get("imWidth", "0"))
    height = int(sequence.get("imHeight", "0"))
    del width, height  # Dimensions are recorded in seqinfo but not needed for image loading.
    frames: list[bytes] = []
    for index in range(frame_count):
        sample_time = start + index / fps
        frame_number = math.floor(sample_time * rate + 1e-8) + 1
        frame_path = image_dir / f"{frame_number:08d}{extension}"
        if not frame_path.is_file():
            raise FileNotFoundError(f"DanceTrack sampled frame is missing: {frame_path}")
        frames.append(frame_path.read_bytes())
    return frames


def extract_video_frames(
    ffmpeg: str,
    source: Path,
    start: float,
    destination: Path,
    fps: int,
    frame_count: int,
) -> list[bytes]:
    destination.mkdir(parents=True, exist_ok=True)
    pattern = destination / "frame_%02d.jpg"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.6f}",
        "-i",
        str(source),
        "-t",
        f"{WINDOW_SECONDS:.3f}",
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        f"fps={fps}:round=near",
        "-frames:v",
        str(frame_count),
        "-q:v",
        "3",
        str(pattern),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    frames = sorted(destination.glob("frame_*.jpg"))
    if completed.returncode != 0 or len(frames) != frame_count:
        detail = completed.stderr.strip()[-800:]
        raise RuntimeError(
            f"Expected {frame_count} sampled frames from {source.name}, got {len(frames)}. "
            f"FFmpeg: {detail}"
        )
    return [frame.read_bytes() for frame in frames]


def extract_frames(
    ffmpeg: str,
    item: dict[str, Any],
    destination: Path,
    fps: int,
    frame_count: int,
) -> list[bytes]:
    source = Path(item["source_path"])
    start = float(item["window_start_seconds"])
    if source.is_dir():
        return extract_sequence_frames(source, start, fps, frame_count)
    return extract_video_frames(ffmpeg, source, start, destination, fps, frame_count)


def resize_frames_to_short_side(
    frame_bytes: list[bytes], short_side: int
) -> tuple[list[bytes], list[list[int]], list[list[int]]]:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError(
            "Resizing frames requires Pillow; run this evaluation with the bundled Python runtime."
        ) from exc

    resized_frames: list[bytes] = []
    source_dimensions: list[list[int]] = []
    model_dimensions: list[list[int]] = []
    for frame in frame_bytes:
        with Image.open(BytesIO(frame)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            width, height = image.size
            if width <= 0 or height <= 0:
                raise ValueError(f"Invalid sampled-frame dimensions: {width}x{height}")
            source_dimensions.append([width, height])
            if width <= height:
                target_width = short_side
                target_height = max(1, round(height * short_side / width))
            else:
                target_height = short_side
                target_width = max(1, round(width * short_side / height))
            if image.size != (target_width, target_height):
                image = image.resize(
                    (target_width, target_height), Image.Resampling.LANCZOS
                )
            output = BytesIO()
            image.save(output, format="JPEG", quality=95, subsampling=0)
            resized_frames.append(output.getvalue())
            model_dimensions.append([target_width, target_height])
    return resized_frames, source_dimensions, model_dimensions


def check_ollama(model_id: str) -> tuple[list[str], dict[str, Any]]:
    request = Request(f"{OLLAMA_BASE_URL}/api/tags", method="GET")
    with urlopen(request, timeout=10) as response:
        tags = json.loads(response.read().decode("utf-8"))
    names = [item.get("name", "") for item in tags.get("models", [])]
    if model_id not in names:
        raise RuntimeError(f"Required Ollama model is not listed by /api/tags: {model_id}")
    request = Request(
        f"{OLLAMA_BASE_URL}/api/show",
        data=json.dumps({"model": model_id, "verbose": False}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        details = json.loads(response.read().decode("utf-8"))
    return names, details


def call_ollama(
    frame_bytes: list[bytes], model_id: str, prompt: str, frame_count: int
) -> dict[str, Any]:
    if len(frame_bytes) != frame_count:
        raise ValueError(f"Expected {frame_count} images, got {len(frame_bytes)}")
    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [base64.b64encode(frame).decode("ascii") for frame in frame_bytes],
            }
        ],
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0,
            "seed": 20261017,
            "num_predict": 12,
            "num_ctx": NUM_CTX,
        },
    }
    request = Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=600) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"Ollama HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Ollama at {OLLAMA_BASE_URL}: {exc.reason}") from exc


def parse_decision(raw_text: str) -> int:
    match = re.fullmatch(r"\s*([01])\s*", raw_text)
    if not match:
        raise ValueError(f"Expected exactly 0 or 1; model returned {raw_text!r}")
    return int(match.group(1))


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in results if row.get("prediction") in (0, 1)]
    return {
        "sample_count": len(results),
        "valid_decision_count": len(valid),
        "failed_or_invalid_count": len(results) - len(valid),
        "metrics": binary_metrics(
            [int(row["actual"]) for row in valid], [int(row["prediction"]) for row in valid]
        ),
        "timing": {
            "total_inference_seconds": sum(float(row["inference_seconds"]) for row in valid),
            "mean_inference_seconds_per_clip": (
                statistics.mean(float(row["inference_seconds"]) for row in valid) if valid else None
            ),
            "median_inference_seconds_per_clip": (
                statistics.median(float(row["inference_seconds"]) for row in valid) if valid else None
            ),
            "mean_frame_preparation_seconds_per_clip": (
                statistics.mean(
                    float(row["frame_preparation_seconds"])
                    for row in results
                    if row.get("frame_preparation_seconds") is not None
                )
                if any(row.get("frame_preparation_seconds") is not None for row in results)
                else None
            ),
            "wall_seconds": sum(
                float(row.get("frame_preparation_seconds") or 0)
                + float(row.get("inference_seconds") or 0)
                for row in results
            ),
        },
    }


def compare_other_model(results: list[dict[str, Any]], current_model: str) -> dict[str, Any] | None:
    if current_model == MODEL:
        other_run = PREVIOUS_QWEN_RUN
        other_model = "Qwen/Qwen3-VL-2B-Instruct (previous Transformers run)"
        prediction_field = "qwen_prediction"
    elif current_model == QWEN_OLLAMA_MODEL:
        other_run = MINICPM_RUN
        other_model = MODEL
        prediction_field = "prediction"
    else:
        return None
    path = other_run / "per_sample_results.csv"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8-sig", newline="") as stream:
        other_rows = {int(row["index"]): row for row in csv.DictReader(stream)}

    other_valid: list[tuple[dict[str, str], int]] = []
    for row in other_rows.values():
        try:
            actual = int(row["actual"])
            prediction = int(row[prediction_field])
        except (KeyError, TypeError, ValueError):
            continue
        if actual in (0, 1) and prediction in (0, 1):
            other_valid.append((row, prediction))

    paired: list[tuple[dict[str, Any], int]] = []
    for row in results:
        other_row = other_rows.get(int(row["index"]))
        if row.get("prediction") not in (0, 1) or other_row is None:
            continue
        try:
            other_prediction = int(other_row[prediction_field])
            if int(row["actual"]) != int(other_row["actual"]):
                raise ValueError(f"Label mismatch for paired sample index {row['index']}")
        except (KeyError, TypeError, ValueError):
            continue
        if other_prediction in (0, 1):
            paired.append((row, other_prediction))

    agreements = sum(int(row["prediction"]) == prediction for row, prediction in paired)
    current_metrics = binary_metrics(
        [int(row["actual"]) for row, _ in paired],
        [int(row["prediction"]) for row, _ in paired],
    )
    other_metrics = binary_metrics(
        [int(row["actual"]) for row, _ in paired],
        [prediction for _, prediction in paired],
    )
    return {
        "other_model": other_model,
        "other_run": str(other_run),
        "other_valid_decision_count": len(other_valid),
        "other_metrics_on_its_valid_decisions": binary_metrics(
            [int(row["actual"]) for row, _ in other_valid],
            [prediction for _, prediction in other_valid],
        ),
        "paired_valid_decision_count": len(paired),
        "paired_current_model_metrics": current_metrics,
        "paired_other_model_metrics": other_metrics,
        "paired_agreement_count": agreements,
        "paired_agreement_rate": safe_divide(agreements, len(paired)),
        "paired_disagreement_count": len(paired) - agreements,
    }


def save_results(output: Path, results: list[dict[str, Any]], config: dict[str, Any], status: str) -> None:
    fields = [
        "index",
        "dataset",
        "source_name",
        "source_relative",
        "actual",
        "actual_class",
        "mc3_probability",
        "window_start_seconds",
        "window_end_seconds",
        "prediction",
        "raw_output",
        "thinking_text",
        "think_parameter",
        "frame_count",
        "source_frame_dimensions",
        "model_input_dimensions",
        "frame_preparation_seconds",
        "inference_seconds",
        "ollama_load_seconds",
        "prompt_evaluation_seconds",
        "generation_seconds",
        "status",
        "error",
    ]
    temp_csv = output / "per_sample_results.csv.tmp"
    with temp_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow({field: result.get(field) for field in fields})
    temp_csv.replace(output / "per_sample_results.csv")
    report = {
        **config,
        "status": status,
        "results_written": len(results),
        **summarize(results),
        "comparison_with_other_model": compare_other_model(results, str(config["model"])),
        "per_sample_results": results,
    }
    temp_json = output / "summary.json.tmp"
    temp_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp_json.replace(output / "summary.json")


def run(
    output: Path,
    preflight_only: bool,
    model_id: str,
    preflight_candidate: int,
    resize_short_side: int | None,
    fps: int,
) -> int:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Refusing to overwrite existing results: {output}")
    output.mkdir(parents=True, exist_ok=True)
    log_path = output / "progress.log"
    frame_count = int(fps * WINDOW_SECONDS)
    prompt = build_prompt(fps, frame_count)
    selected, thresholds = select_candidates()
    if not 1 <= preflight_candidate <= len(selected):
        raise ValueError(
            f"Preflight candidate must be in 1..{len(selected)}, got {preflight_candidate}"
        )
    names, model_details = check_ollama(model_id)
    ffmpeg = find_ffmpeg()
    model_info = model_details.get("details", {})
    capabilities = model_info.get("families", []) or model_details.get("capabilities", [])
    label_counts = {
        "violent": sum(int(item["label"]) == 1 for item in selected),
        "nonviolent": sum(int(item["label"]) == 0 for item in selected),
    }
    config = {
        "experiment": f"{model_id} Ollama binary review of Epoch 8 MC3 borderline validation clips",
        "source_training_run": str(TRAIN_RUN),
        "source_epoch": 8,
        "candidate_count": len(selected),
        "candidate_label_distribution": label_counts,
        "threshold_interval": {
            "sensitive": thresholds["sensitive"],
            "certain": thresholds["certain"],
            "rule": "sensitive <= bag_probability < certain",
        },
        "model": model_id,
        "ollama_tags_seen": names,
        "model_capabilities": capabilities,
        "input_protocol": {
            "window_selection": "highest MC3-18 violence-probability 4-second window per clip",
            "fps": fps,
            "frame_count": frame_count,
            "sample_offsets_seconds": [index / fps for index in range(frame_count)],
        },
        "image_resize": (
            {
                "method": "preserve aspect ratio; set shorter side to the requested pixels",
                "short_side_pixels": resize_short_side,
                "resampling": "Pillow LANCZOS",
                "jpeg_quality": 95,
            }
            if resize_short_side is not None
            else None
        ),
        "prompt": prompt,
        "generation": {
            "think": False,
            "temperature": 0,
            "seed": 20261017,
            "num_predict": 12,
            "num_ctx": NUM_CTX,
        },
        "comparison_run": str(
            PREVIOUS_QWEN_RUN
            if model_id == MODEL
            else MINICPM_RUN
            if model_id == QWEN_OLLAMA_MODEL
            else ""
        ),
        "preflight_only": preflight_only,
        "preflight_candidate_index": preflight_candidate if preflight_only else None,
    }
    (output / "run_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    (output / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    emit(
        f"[preflight] candidates={len(selected)} labels={label_counts}; "
        f"window=top-scoring 4s; sampling={frame_count} frames at {fps} FPS; "
        f"resize_short_side={resize_short_side or 'original'}; "
        f"threshold=[{thresholds['sensitive']:.9f}, {thresholds['certain']:.9f}); "
        f"model={model_id}; think=false; output={output}",
        log_path,
    )
    emit("[preflight] Ollama model is installed; inference remains local to 127.0.0.1.", log_path)

    run_candidates = [selected[preflight_candidate - 1]] if preflight_only else selected
    results: list[dict[str, Any]] = []
    started_all = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="minicpm_frames_", dir=str(output)) as temp_dir:
        temp_root = Path(temp_dir)
        for index, item in enumerate(run_candidates, start=1):
            emit(
                f"[frames {index:02d}/{len(run_candidates)}] {item['dataset']} / "
                f"{item['source_name']} window={item['window_start_seconds']:.3f}.."
                f"{item['window_end_seconds']:.3f}s",
                log_path,
            )
            result: dict[str, Any] = {
                "index": index,
                "dataset": item["dataset"],
                "source_name": item["source_name"],
                "source_relative": item["source_relative"],
                "actual": item["label"],
                "actual_class": item["class_name"],
                "mc3_probability": item["mc3_probability"],
                "window_start_seconds": item["window_start_seconds"],
                "window_end_seconds": item["window_end_seconds"],
                "prediction": None,
                "raw_output": None,
                "thinking_text": None,
                "think_parameter": False,
                "frame_count": 0,
                "source_frame_dimensions": None,
                "model_input_dimensions": None,
                "frame_preparation_seconds": None,
                "inference_seconds": None,
                "ollama_load_seconds": None,
                "prompt_evaluation_seconds": None,
                "generation_seconds": None,
                "status": "pending",
                "error": None,
            }
            try:
                sample_temp = temp_root / f"sample_{index:02d}"
                prep_started = time.perf_counter()
                frames = extract_frames(ffmpeg, item, sample_temp, fps, frame_count)
                if resize_short_side is not None:
                    frames, source_dimensions, model_dimensions = resize_frames_to_short_side(
                        frames, resize_short_side
                    )
                    result["source_frame_dimensions"] = source_dimensions
                    result["model_input_dimensions"] = model_dimensions
                result["frame_preparation_seconds"] = time.perf_counter() - prep_started
                result["frame_count"] = len(frames)
                inference_started = time.perf_counter()
                response = call_ollama(frames, model_id, prompt, frame_count)
                result["inference_seconds"] = time.perf_counter() - inference_started
                message = response.get("message", {})
                raw_text = str(message.get("content", ""))
                thinking_text = str(message.get("thinking", "") or "")
                result["raw_output"] = raw_text
                result["thinking_text"] = thinking_text
                result["ollama_load_seconds"] = response.get("load_duration", 0) / 1e9
                result["prompt_evaluation_seconds"] = response.get("prompt_eval_duration", 0) / 1e9
                result["generation_seconds"] = response.get("eval_duration", 0) / 1e9
                result["prediction"] = parse_decision(raw_text)
                result["status"] = "completed"
                if thinking_text.strip():
                    result["prediction"] = None
                    result["status"] = "thinking_mode_violation"
                    result["error"] = "Ollama returned non-empty thinking content despite think=false."
                results.append(result)
                save_results(
                    output,
                    results,
                    config,
                    "preflight_failed" if result["status"] != "completed" else "in_progress",
                )
                emit(
                    f"[review {index:02d}/{len(run_candidates)}] {item['dataset']} / "
                    f"{item['source_name']} prediction={result['prediction']} "
                    f"actual={item['class_name']} prep={result['frame_preparation_seconds']:.2f}s "
                    f"infer={result['inference_seconds']:.2f}s think_text="
                    f"{'present' if thinking_text.strip() else 'empty'}",
                    log_path,
                )
                if result["status"] != "completed":
                    if preflight_only:
                        return 2
            except Exception as exc:
                result["status"] = "failed"
                result["error"] = f"{type(exc).__name__}: {exc}"
                results.append(result)
                save_results(output, results, config, "preflight_failed" if preflight_only else "in_progress")
                emit(
                    f"[review {index:02d}/{len(run_candidates)}] FAILED: {result['error']}",
                    log_path,
                )
                if preflight_only:
                    return 2

    wall_seconds = time.perf_counter() - started_all
    config["measured_wall_seconds"] = wall_seconds
    if preflight_only:
        status = "preflight_passed" if results and results[0]["status"] == "completed" else "preflight_failed"
        save_results(output, results, config, status)
        emit(
            f"[preflight complete] status={status}; output={output}",
            log_path,
        )
        return 0 if status == "preflight_passed" else 2

    status = "completed" if len(results) == SAMPLE_COUNT and all(
        item["status"] == "completed" for item in results
    ) else "completed_with_failures"
    save_results(output, results, config, status)
    emit(
        f"[complete] status={status}; completed={sum(item['status'] == 'completed' for item in results)}"
        f"/{SAMPLE_COUNT}; wall={wall_seconds:.2f}s; output={output}",
        log_path,
    )
    return 0 if status == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default=MODEL, help="Exact installed Ollama model tag to evaluate.")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Run the exact extraction + Ollama request path on the first clip only.",
    )
    parser.add_argument(
        "--preflight-candidate",
        type=int,
        default=1,
        help="1-based position in the fixed 29-clip list to use for preflight.",
    )
    parser.add_argument(
        "--resize-short-side",
        type=int,
        default=None,
        help="Resize each sampled frame, preserving aspect ratio, so its shorter side has this many pixels.",
    )
    parser.add_argument(
        "--fps",
        type=int,
        choices=(2, 4),
        default=2,
        help="Frames per second sampled from each selected 4-second window (2 or 4).",
    )
    args = parser.parse_args()
    if args.resize_short_side is not None and args.resize_short_side < 1:
        parser.error("--resize-short-side must be a positive integer")
    return run(
        args.output_dir.resolve(),
        args.preflight_only,
        args.model,
        args.preflight_candidate,
        args.resize_short_side,
        args.fps,
    )


if __name__ == "__main__":
    raise SystemExit(main())
