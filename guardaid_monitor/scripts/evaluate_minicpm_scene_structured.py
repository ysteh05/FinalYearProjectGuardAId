"""Isolated MiniCPM scene-description evaluation; does not change the live app."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import time
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
MODEL = "openbmb/minicpm-v4.7:1b-q4_K_M"
API = "http://127.0.0.1:11434"
VIDEOS = ["VID-20220816-WA0000.mp4", "VID20261014170218.mp4"]
TIMES = [15.25 + i * .5 for i in range(8)]
OPTIONS = {"temperature": 0, "seed": 20261017, "num_predict": 320, "num_ctx": 32768}
SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "details": {
            "type": "array",
            "minItems": 3,
            "items": {
                "type": "string",
                "maxLength": 80,
                "pattern": "^[^,;.!?]+[.!?]$",
            },
        },
        "uncertainty": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["summary", "details", "uncertainty"],
    "additionalProperties": False,
}
SYSTEM = """You describe visible events in short video clips for a human reviewer. Inspect all supplied frames in time order and report only actions that the images support. Your response must be exactly one valid JSON object with the required keys specified by the user. Do not add Markdown, explanations, comments, or text before or after the JSON. Do not infer identity, age, relationship, motive, intent, guilt, injury, or events outside the clip; ignore audio."""
USER = """Describe what happens in this video segment so a reviewer can understand its main event. Be concise but substantive.

Use this exact JSON shape and key names:
{"summary":"...","details":["..."],"uncertainty":"low"}

REQUIRED OUTPUT RULES:
1. Always include all three keys: summary, details, uncertainty. Never omit a key. If you cannot decide how certain the description is, set uncertainty to "medium" rather than leaving it out.
2. summary must be one non-empty sentence naming the main visible event or activity, no more than 25 words.
3. details must contain at least three separate short point-form sentences for these clips; there is no maximum number. Each item must state exactly one distinct visible fact or action, ideally 4–8 English words and never more than 12 words or 80 characters. Use one complete sentence per item, ending with a period. Do not use commas or semicolons to combine facts. Split different people and separate actions into separate items. For example, use ["A person raises both arms.", "Another person turns toward the door.", "A third person steps back."] as separate observations, not one combined sentence. This is only a formatting example; describe only what is actually visible. Keep every point independently readable, avoid repeating the summary or another point, and omit irrelevant static background. Prefer the direct visible action. Do not join separate actions with "while"; shorten them or make separate points (for example, write "A man checks his phone."). Before answering, silently count the words in every detail and rewrite any item over 12 words. Never stop an item mid-sentence or mid-word.
4. If physical violence is clearly visible, name the specific visible act (such as punching, kicking, striking, or forceful pushing) and its visible sequence. If the clip is nonviolent, say what people are actually doing, such as dancing, exercising, playing, or ordinary interaction. Do not infer violence from energetic movement or proximity alone. If an action is unclear, state what is visible and what remains unclear.
5. uncertainty must be exactly one of "low", "medium", or "high" and must be present even when it is "low". It describes confidence in the visual account, not whether the clip is violent.
6. Output strict JSON only: double-quoted strings, valid commas and brackets, no trailing comma, no extra keys, and no text after the final closing brace.

Before answering, silently check: the JSON parses; all three required keys exist exactly once; summary is a non-empty string; details is an array containing only strings; uncertainty is one allowed value. If any check fails, correct the JSON before returning it."""


def api(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(API + path, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=600 if path == "/api/chat" else 20) as response:
        return json.loads(response.read().decode())


def parse_and_validate(raw):
    """Validate this simple schema explicitly, including duplicate-key rejection."""
    def pairs(items):
        result = {}
        for key, val in items:
            if key in result:
                raise ValueError(f"Duplicate field: {key}")
            result[key] = val
        return result

    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict) or set(value) != set(SCHEMA["required"]):
        raise ValueError("Expected exactly summary, details and uncertainty")
    if not isinstance(value["summary"], str):
        raise ValueError("summary must be a string")
    if not isinstance(value["details"], list) or len(value["details"]) < 3:
        raise ValueError("details must contain at least three separate points")
    for point in value["details"]:
        if not isinstance(point, str) or len(point) > 80:
            raise ValueError("each detail must be a string no longer than 80 characters")
        if not point.endswith((".", "!", "?")) or any(mark in point for mark in (",", ";")):
            raise ValueError("each detail must be a complete sentence without comma/semicolon chains")
        if len(point.split()) > 12:
            raise ValueError("each detail must contain no more than 12 words")
    if value["uncertainty"] not in ["low", "medium", "high"]:
        raise ValueError("Invalid uncertainty value")
    return value


def self_test():
    # Exact syntax failures seen in the previous two MiniCPM responses.
    invalid = [
        '{"summary":"Three schoolgirls in uniforms perform a synchronized dance in a classroom setting.", "details":["They wear face masks and move their arms and legs in coordinated patterns"],"uncertainty:low}',
        '{"summary":"A group of people engage in a high-five and clapping activity in a room with lockers and a Global Impact wall.","details":["Woman in brown top and dark jeans waves and high-fives others," "Woman in black shirt and pants gestures while high-fiving," "Man in olive shirt looks at his phone while observing," "Others move around and interact in the background"],"uncertainty":"low"}',
        '{"summary":"test","details":[],"uncertainty":"low"}',
        '{"summary":"test","details":["One complete point.","Two complete points."],"uncertainty":"low"}',
        '{"summary":"test","details":["One complete point.","Two complete points.","A third point"],"uncertainty":"low"}',
        '{"summary":"test","details":["One complete point.","Two complete points.","A third, combined point."],"uncertainty":"low"}',
        '{"summary":"test","details":["One complete point.","Two complete points.","A third point with a semicolon; chain."],"uncertainty":"low"}',
        '{"summary":"test","details":["One complete point.","Two complete points.","A third point."],"uncertainty":"certain"}',
        '{"summary":"test","details":[1],"uncertainty":"low"}',
        '{"summary":"test","details":[],"uncertainty":"low","extra":1}',
        '{"summary":"test","summary":"duplicate","details":[],"uncertainty":"low"}',
    ]
    invalid.append(json.dumps({"summary": "test", "details": ["One complete point.", "Two complete points.", "x" * 81], "uncertainty": "low"}))
    invalid.append(json.dumps({"summary": "test", "details": ["One complete point.", "Two complete points.", "A person is standing in the room while everyone else is moving around."], "uncertainty": "low"}))
    for raw in invalid:
        try:
            parse_and_validate(raw)
        except (ValueError, TypeError):
            continue
        raise AssertionError(f"Incorrectly accepted: {raw}")
    valid_points = ["A person raises both arms.", "Another person turns toward the camera.", "A third person steps back."]
    for uncertainty in ("low", "medium", "high"):
        parse_and_validate(json.dumps({"summary": "People are dancing.", "details": valid_points, "uncertainty": uncertainty}))
    assert all(len(point.split()) <= 12 and len(point) <= 80 for point in valid_points)
    assert len(TIMES) == 8 and TIMES[0] == 15.25 and TIMES[-1] == 18.75
    print(f"Validator regression checks passed: {len(invalid)} invalid and 3 valid examples.", flush=True)


def frames_for(path):
    started = time.perf_counter()
    images, dimensions, digests = [], [], []
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg is unavailable")
    for timestamp in TIMES:
        completed = subprocess.run(
            [executable, "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp:.6f}",
             "-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
            capture_output=True, timeout=45, check=True,
        )
        with Image.open(io.BytesIO(completed.stdout)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            width, height = image.size
            target = (360, round(height * 360 / width)) if width <= height else (round(width * 360 / height), 360)
            if image.size != target:
                image = image.resize(target, Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=95, subsampling=0)
            data = buffer.getvalue()
            images.append(base64.b64encode(data).decode("ascii"))
            dimensions.append(list(target))
            digests.append(hashlib.sha256(data).hexdigest())
    return images, {"dimensions": dimensions, "jpeg_sha256": digests,
                    "preparation_seconds": time.perf_counter() - started}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    self_test()
    if args.self_test:
        return
    if args.repeats < 1:
        raise ValueError("repeats must be positive")
    version = api("/api/version")
    loaded = api("/api/ps")
    tags = api("/api/tags")
    tag = next((x for x in tags["models"] if x["name"] == MODEL), None)
    if tag is None:
        raise RuntimeError(f"Model is not installed: {MODEL}")
    other = [x["name"] for x in loaded.get("models", []) if x["name"] != MODEL]
    if other:
        raise RuntimeError(f"Other models are resident; review before timing: {other}")
    output = ROOT / "model_runs" / ("minicpm_scene_structured_360p2fps_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    output.mkdir(parents=True, exist_ok=False)
    report = {"model": MODEL, "model_digest": tag.get("digest"), "ollama": version,
              "initial_loaded_models": loaded, "system_prompt": SYSTEM, "user_prompt": USER,
              "prompt_version": "scene_details_complete_short_points_v8", "format": SCHEMA,
              "detail_point_guidance": {"ideal_word_range": [4, 8], "maximum_words": 12, "maximum_characters": 80, "minimum_item_count": 3, "fixed_item_count": None, "one_complete_sentence_per_item": True},
              "options": OPTIONS, "think": False, "sample_times": TIMES,
              "short_side": 360, "repeats": args.repeats, "frames": {}, "results": []}

    def save():
        (output / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")

    cached = {}
    for name in VIDEOS:
        cached[name], metadata = frames_for(ROOT / "Video Test" / "GuardAId" / name)
        report["frames"][name] = metadata
        print(f"[frames ready] {name}: 8 frames, {metadata['dimensions'][0]}", flush=True)
    save()
    print(f"[report] {output / 'summary.json'}", flush=True)
    for repeat in range(1, args.repeats + 1):
        for name in VIDEOS:
            print(f"[start] round {repeat}/{args.repeats}: {name}", flush=True)
            payload = {"model": MODEL, "messages": [{"role": "system", "content": SYSTEM},
                       {"role": "user", "content": USER, "images": cached[name]}],
                       "stream": False, "think": False, "options": OPTIONS, "format": SCHEMA}
            started = time.perf_counter()
            row = {"round": repeat, "video": name}
            try:
                response = api("/api/chat", payload)
                row["wall_seconds"] = time.perf_counter() - started
                row["response"] = response
                raw = response.get("message", {}).get("content", "")
                row["raw_response"] = raw
                row["thinking_nonempty"] = bool(response.get("message", {}).get("thinking", "").strip())
                for key in ["total_duration", "load_duration", "prompt_eval_duration", "eval_duration"]:
                    row[key.replace("_duration", "_seconds")] = response.get(key, 0) / 1e9
                try:
                    row["parsed"] = parse_and_validate(raw)
                    row["schema_valid"] = True
                    row["summary_nonempty"] = bool(row["parsed"]["summary"].strip())
                    row["summary_word_count"] = len(row["parsed"]["summary"].split())
                    row["detail_point_count"] = len(row["parsed"]["details"])
                    row["detail_point_word_counts"] = [len(point.split()) for point in row["parsed"]["details"]]
                    row["all_detail_points_at_most_12_words"] = all(n <= 12 for n in row["detail_point_word_counts"])
                    row["all_detail_points_at_most_80_characters"] = all(len(point) <= 80 for point in row["parsed"]["details"])
                    row["all_detail_points_are_complete_standalone_sentences"] = all(
                        point.endswith((".", "!", "?")) and not any(mark in point for mark in (",", ";"))
                        for point in row["parsed"]["details"]
                    )
                except (ValueError, TypeError) as error:
                    row["schema_valid"] = False
                    row["validation_error"] = str(error)
            except Exception as error:
                row["wall_seconds"] = time.perf_counter() - started
                row["error"] = repr(error)
                row["schema_valid"] = False
            report["results"].append(row)
            save()
            print(json.dumps({k: v for k, v in row.items() if k != "response"}, ensure_ascii=False), flush=True)
    print(f"[complete] schema-valid {sum(r['schema_valid'] for r in report['results'])}/{len(report['results'])}", flush=True)


if __name__ == "__main__":
    main()
