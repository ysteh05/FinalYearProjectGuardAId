"""Offline Qwen3-VL 4B Scene Description evaluation on the frozen 24-case Adjustment Set.

Uses the same Y0/M0 evidence preparation and production v9 prompts as the
MiniCPM evaluation. This script is isolated from production configuration.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MONITOR = ROOT / "guardaid_monitor"
sys.path.insert(0, str(MONITOR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.vlm_contract import SCENE_SCHEMA  # noqa: E402
import minicpm_scene_prompt_optimization as evaluator  # noqa: E402


DEFAULT_MODEL = "qwen3-vl:4b"
OLLAMA_URL = evaluator.OLLAMA_URL


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_model(model: str) -> dict:
    version = evaluator.api("/api/version", timeout=8)
    tags = evaluator.api("/api/tags", timeout=12)
    ps = evaluator.api("/api/ps", timeout=8)
    found = next((item for item in tags.get("models", []) if item.get("name") == model), None)
    if found is None:
        raise RuntimeError(f"Required model is not installed: {model}")
    return {"ollama_version": version.get("version"), "model": found.get("name"),
            "digest": found.get("digest"), "size": found.get("size"), "loaded_models": ps.get("models", [])}


def validate_reply(raw: str, done_reason: str | None) -> tuple[dict | None, str | None]:
    def reject_duplicate_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    parsed = None
    try:
        candidate = json.loads(raw, object_pairs_hook=reject_duplicate_pairs)
        if isinstance(candidate, dict) and set(candidate) == {"summary", "details", "uncertainty"}:
            parsed = candidate
    except Exception as exc:
        if done_reason != "length":
            return None, f"{type(exc).__name__}: {exc}"
    if done_reason == "length":
        return parsed, "generation reached num_predict limit"
    try:
        return evaluator.validate_scene_output(raw), None
    except Exception as exc:
        # Keep a parseable exact-shape object for blinded semantic review even
        # when the stricter production formatting rules reject it.
        return parsed, f"{type(exc).__name__}: {exc}"


def request_payload(model: str, prepared: dict, think: bool, num_predict: int,
                    repair_text: str = "") -> tuple[dict, dict]:
    system, user, prompt_meta = evaluator.prompts_for("baseline", prepared["alert_kind"], prepared["detected_classes"])
    user += repair_text
    images = prepared.get("_runtime_images")
    if images is None:
        images = [(ROOT / path).read_bytes() for path in prepared["prepared"]["prepared_paths"]]

    # Ollama's installed bare qwen3-vl-thinking template ignores think=false.
    # This documented raw/no-think prefix is only used for the think-off arm.
    if not think:
        messages = [
            {"role": "user", "content": f"{system}\n\n{user.rstrip()}\n/no_think",
             "images": [base64.b64encode(image).decode("ascii") for image in images]},
            {"role": "assistant", "content": "<think>\n\n</think>\n\n"},
        ]
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user,
             "images": [base64.b64encode(image).decode("ascii") for image in images]},
        ]
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": think,
        "keep_alive": "10m",
        "format": SCENE_SCHEMA,
        "options": {"temperature": 0, "seed": 20261017, "num_ctx": 32768, "num_predict": num_predict},
    }
    if not think:
        payload["raw"] = True
    return payload, {"prompt_id": "production-v9", **prompt_meta,
                     "system_sha256": digest(system.encode()), "schema_sha256": digest(evaluator.canonical_json(SCENE_SCHEMA).encode()),
                     "input_hashes": prepared["prepared"]["input_hashes"], "image_count": len(images)}


def run_one(model: str, prepared: dict, think: bool, num_predict: int) -> dict:
    prep_seconds = evaluator.refresh_runtime_images(prepared)
    attempts = []
    total_started = time.perf_counter()
    repair_text = ""
    for attempt_number in (1, 2):
        payload, metadata = request_payload(model, prepared, think, num_predict, repair_text)
        started = time.perf_counter()
        response = {}
        error = None
        try:
            response = evaluator.api("/api/chat", payload, timeout=600)
        except Exception as exc:  # Preserve per-request failures; do not silently retry transport errors.
            error = f"{type(exc).__name__}: {exc}"
        wall = time.perf_counter() - started
        message = response.get("message", {}) if isinstance(response, dict) else {}
        raw = str(message.get("content", ""))
        parsed, validation_error = validate_reply(raw, response.get("done_reason")) if response else (None, error or "no response")
        attempts.append({
            "attempt": attempt_number, "raw_response": raw, "parsed": parsed,
            "valid": validation_error is None, "validation_error": validation_error,
            "error": error, "done_reason": response.get("done_reason") if response else None,
            "thinking_nonempty": bool(str(message.get("thinking", "")).strip()),
            "wall_seconds": evaluator.safe_number(wall),
            "ollama_total_seconds": evaluator.safe_number((response.get("total_duration") or 0) / 1e9) if response else None,
            "load_seconds": evaluator.safe_number((response.get("load_duration") or 0) / 1e9) if response else None,
            "prompt_eval_seconds": evaluator.safe_number((response.get("prompt_eval_duration") or 0) / 1e9) if response else None,
            "generation_seconds": evaluator.safe_number((response.get("eval_duration") or 0) / 1e9) if response else None,
            "eval_count": response.get("eval_count") if response else None,
        })
        if validation_error is None or error or attempt_number == 2:
            break
        repair_text = "\n" + evaluator.REPAIR_TEMPLATE.format(validation_error=validation_error)
    final = attempts[-1]
    total_wall = evaluator.safe_number(time.perf_counter() - total_started)
    return {
        "case_id": prepared["case_id"], "category": prepared["category"],
        "alert_kind": prepared["alert_kind"], "variant": prepared["variant"],
        "controlled_fixture": prepared["controlled_fixture"], "model": model,
        "think_requested": think,
        "thinking_nonempty": any(item["thinking_nonempty"] for item in attempts),
        "raw_response": final["raw_response"], "parsed": final["parsed"], "valid": final["valid"],
        "first_pass_valid": attempts[0]["valid"], "validation_error": final["validation_error"],
        "error": final["error"], "done_reason": final["done_reason"], "attempts": attempts,
        "attempt_count": len(attempts), "wall_seconds": total_wall,
        "ollama_total_seconds": evaluator.safe_number(sum(item["ollama_total_seconds"] or 0 for item in attempts)),
        "load_seconds": evaluator.safe_number(sum(item["load_seconds"] or 0 for item in attempts)),
        "prompt_eval_seconds": evaluator.safe_number(sum(item["prompt_eval_seconds"] or 0 for item in attempts)),
        "generation_seconds": evaluator.safe_number(sum(item["generation_seconds"] or 0 for item in attempts)),
        "eval_count": sum(item["eval_count"] or 0 for item in attempts),
        "num_predict": num_predict, "input_prep_seconds": evaluator.safe_number(prep_seconds),
        "input_metadata": prepared["input_metadata"], "prompt_metadata": metadata,
    }


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--think-modes", choices=("off", "on", "both"), default="both")
    parser.add_argument("--num-predict", type=int, default=512)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.num_predict < 1:
        parser.error("--num-predict must be positive")

    state = check_model(args.model)
    selected_ids = args.case_id or None
    rows, prepared_meta = evaluator.prepare_all("adjustment", "Y0", "M0", selected_ids)
    # The three nonweapon-confounder cases have paired gun/knife controlled
    # variants. Select the predeclared gun variant to keep one row per source case.
    by_case: dict[str, dict] = {}
    for row in rows:
        if row["category"] == "Nonweapon Confounders" and row["variant"] != "controlled_false_gun_hint":
            continue
        if row["case_id"] in by_case:
            raise RuntimeError(f"More than one selected input for source case {row['case_id']}")
        by_case[row["case_id"]] = row
    prepared_rows = list(by_case.values())
    expected_count = len(selected_ids) if selected_ids else 24
    if len(prepared_rows) != expected_count:
        raise RuntimeError(f"Expected {expected_count} unique source cases, prepared {len(prepared_rows)}")

    modes = [False, True] if args.think_modes == "both" else [args.think_modes == "on"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = (args.output or (ROOT / "VLM Fine-tune" / "Optimization Runs" / f"{stamp}_Qwen3VL4B_scene_adjustment24_{args.think_modes}")).resolve()
    if not output.is_relative_to((ROOT / "VLM Fine-tune" / "Optimization Runs").resolve()):
        raise RuntimeError("Output must remain under VLM Fine-tune/Optimization Runs")
    output.mkdir(parents=True, exist_ok=False)
    config = {
        "status": "running", "model": state, "split": "Adjustment Set", "source_case_count": len(prepared_rows),
        "case_ids": [row["case_id"] for row in prepared_rows], "selected_profiles": {"weapon": "Y0", "violence": "M0"},
        "prepared": prepared_meta, "selected_variant_policy": "one row per source case; use controlled_false_gun_hint for each nonweapon confounder",
        "scene_prompt_version": "minicpm_alert_context_v9", "modes": ["think_on" if mode else "think_off" for mode in modes],
        "options": {"temperature": 0, "seed": 20261017, "num_ctx": 32768, "num_predict": args.num_predict},
        "think_off_workaround": "Ollama raw=true, /no_think suffix, and empty closed think prefix; installed qwen3-vl:4b template otherwise ignores think=false",
        "production_modified": False,
    }
    atomic_json(output / "run_config.json", config)
    (output / "system_prompt.txt").write_text(evaluator.SCENE_SYSTEM_PROMPT + "\n", encoding="utf-8")
    results_path = output / "results.jsonl"
    log_path = output / "progress.log"
    expected = len(prepared_rows) * len(modes)
    completed = 0
    try:
        with log_path.open("a", encoding="utf-8") as log:
            for index, prepared in enumerate(prepared_rows, start=1):
                for think in modes:
                    mode = "on" if think else "off"
                    print(f"[scene {completed + 1}/{expected}] case={prepared['case_id']} kind={prepared['alert_kind']} variant={prepared['variant']} think={mode}", flush=True)
                    row = run_one(args.model, prepared, think, args.num_predict)
                    row["case_index"] = index
                    with results_path.open("a", encoding="utf-8", newline="\n") as target:
                        target.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                    message = (f"[result {completed + 1}/{expected}] case={row['case_id']} think={mode} valid={row['valid']} "
                               f"wall={row['wall_seconds']:.2f}s reason={row['done_reason']} error={row['validation_error']}")
                    print(message, flush=True)
                    log.write(message + "\n")
                    log.flush()
                    completed += 1
        config["status"] = "complete" if completed == expected else "partial"
        config["completed_requests"] = completed
        config["expected_requests"] = expected
        atomic_json(output / "run_config.json", config)
    except BaseException as exc:
        config["status"] = "failed"
        config["completed_requests"] = completed
        config["expected_requests"] = expected
        config["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        atomic_json(output / "run_config.json", config)
        raise
    print(f"[complete] requests={completed}/{expected}; results={results_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
