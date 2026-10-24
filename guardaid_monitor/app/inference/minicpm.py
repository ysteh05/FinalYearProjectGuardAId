"""Local Ollama MiniCPM inference with explicit frame and output contracts."""
from __future__ import annotations

import base64
import json
import math
import hashlib
import os
from pathlib import Path
import re
from threading import RLock
import time
from typing import Any
from urllib.request import Request, urlopen
from .scene_reliability import SceneFormatError, SceneLengthError, SceneOutputTruncated

from ..vlm_contract import (
    MODEL_ID, SCENE_SCHEMA, SCENE_SYSTEM_PROMPT, SCENE_USER_PROMPT,
    BINARY_SYSTEM_PROMPT, BINARY_USER_PROMPT, scene_sampling, scene_prompt,
)


def _strict_object(raw: str, *, allow_long: bool = False) -> dict:
    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise SceneFormatError(f"{key}: duplicate field")
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=unique_pairs)
    except json.JSONDecodeError as error:
        raise SceneFormatError(f"JSON: {error.msg} at character {error.pos}") from error
    if not isinstance(value, dict) or set(value) != {"summary", "details", "uncertainty"}:
        raise SceneFormatError("JSON: require exactly summary, details and uncertainty")
    if not isinstance(value["summary"], str) or not value["summary"].strip():
        raise SceneFormatError("summary: require a nonempty string")
    if not isinstance(value["details"], list):
        raise SceneFormatError("details: require an array")
    for index, point in enumerate(value["details"]):
        if not isinstance(point, str):
            raise SceneFormatError(f"details[{index}]: require a string")
        if re.fullmatch(r"[^,;.!?]+[.!?]", point) is None:
            raise SceneFormatError(f"details[{index}]: require one complete sentence ending in . ! or ? without commas or semicolons")
    if not isinstance(value["uncertainty"], str) or value["uncertainty"] not in {"low", "medium", "high"}:
        raise SceneFormatError("uncertainty: require low, medium or high")
    if not allow_long:
        if len(value['summary']) > 240 or len(value['summary'].split()) > 25:
            raise SceneLengthError(f"summary: {len(value['summary'].split())} words / {len(value['summary'])} characters; maximum 25 / 240")
        for index, point in enumerate(value['details']):
            if len(point) > 80 or len(point.split()) > 12:
                raise SceneLengthError(f"details[{index}]: {len(point.split())} words / {len(point)} characters; maximum 12 / 80")
    return value


def scene_length_fallback(state):
    """Revalidate the latest usable candidate; never synthesize missing fields."""
    candidate = state.get('length_candidate')
    if not candidate:
        return None
    value = _strict_object(json.dumps(candidate, allow_nan=False), allow_long=True)
    state['length_relaxed'] = True
    state['selected_candidate_attempt'] = state.get('length_candidate_attempt')
    return {'description': value['summary'], 'visible_evidence': value['details'],
            'uncertainty': value['uncertainty']}


class MiniCpmOllamaRunner:
    MODEL_VERSION = MODEL_ID

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("GUARDAID_OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
        self._lock = RLock()
        self._loaded = False
        self.last_call_metadata: dict[str, Any] = {}
        self.scene_trace: dict[str, Any] = {}
        self._scene_deadline = None

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def _api(self, path: str, payload: dict | None = None, *, timeout: float = 600) -> dict:
        if self._scene_deadline is not None:
            remaining = self._scene_deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("Scene description wall-clock budget exhausted")
            timeout = min(timeout, remaining)
        request = Request(
            self.base_url + path,
            data=None if payload is None else json.dumps(payload, allow_nan=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())

    def load(self, model_id: str = MODEL_ID, video_token_budget: int = 2048) -> None:
        del video_token_budget  # Historical Qwen field; not an Ollama context setting.
        if model_id != MODEL_ID:
            raise ValueError(f"This runtime uses {MODEL_ID}; update the VLM configuration")
        with self._lock:
            if self._loaded:
                return
            tags = self._api("/api/tags", timeout=10)
            if not any(item.get("name") == MODEL_ID for item in tags.get("models", [])):
                raise RuntimeError(f"MiniCPM is not installed in Ollama: {MODEL_ID}")
            self._api("/api/generate", {"model": MODEL_ID, "stream": False,
                      "keep_alive": "10m", "options": {"num_ctx": 32768}})
            self._loaded = True

    def unload(self) -> None:
        with self._lock:
            if self._loaded:
                self._api("/api/generate", {"model": MODEL_ID, "keep_alive": 0, "stream": False})
            self._loaded = False

    def _sample(self, path: Path, *, fps: float | None = None, scene: bool = False) -> tuple[list[str], dict]:
        """Decode at presentation timestamps, retaining only the current source frame."""
        import av
        import cv2

        with av.open(str(path)) as container:
            if not container.streams.video:
                raise ValueError("VLM input has no video stream")
            stream = container.streams.video[0]
            duration = (float(stream.duration * stream.time_base) if stream.duration is not None
                        else float(container.duration or 0) / av.time_base)
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("VLM input has invalid duration")
            fps, included = scene_sampling(duration) if scene else (float(fps or 2), duration)
            if not math.isfinite(fps) or fps <= 0:
                raise ValueError("VLM sampling FPS must be positive")
            count = max(1, math.ceil(included * fps - 1e-7))
            targets = [min((index + .5) / fps, max(0, included - 1e-6)) for index in range(count)]
            images, actual = [], []
            previous = None
            previous_time = 0.0
            origin = None

            def append(frame, timestamp):
                image = frame.to_ndarray(format="bgr24")
                height, width = image.shape[:2]
                factor = 360 / min(height, width)
                image = cv2.resize(image, (round(width * factor), round(height * factor)),
                                   interpolation=cv2.INTER_AREA if factor <= 1 else cv2.INTER_LINEAR)
                ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
                if not ok:
                    raise RuntimeError("Could not encode a VLM frame")
                images.append(base64.b64encode(encoded).decode("ascii"))
                actual.append(timestamp)

            target_index = 0
            for frame in container.decode(stream):
                if frame.pts is None:
                    raise ValueError("VLM frame has no presentation timestamp")
                absolute = float(frame.pts * frame.time_base)
                if origin is None:
                    origin = absolute
                timestamp = absolute - origin
                while target_index < count and targets[target_index] < timestamp - 1e-7:
                    append(previous if previous is not None else frame, previous_time if previous is not None else timestamp)
                    target_index += 1
                if target_index == count:
                    break
                previous, previous_time = frame, timestamp
            if previous is None and not images:
                raise ValueError("No VLM frames could be decoded")
            while target_index < count:
                append(previous, previous_time)
                target_index += 1
        return images, {"duration_seconds": duration, "included_seconds": included, "fps": fps,
                        "sample_times": targets, "source_times": actual, "frame_count": len(images),
                        "short_side": 360, "think": bool(scene), "model": MODEL_ID}

    def _chat(self, images, settings, *, system: str, prompt: str, schema, output_tokens: int,
              think: bool = False) -> str:
        self.load(settings.model_id)
        response = self._api("/api/chat", {
            "model": MODEL_ID, "stream": False, "think": bool(think), "keep_alive": "10m",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt, "images": images}],
            "format": schema,
            "options": {"temperature": 0, "seed": 20261017, "num_ctx": 32768,
                        "num_predict": output_tokens},
        })
        self.last_call_metadata.update({
            key: response.get(key) for key in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration", "prompt_eval_count", "eval_count", "done_reason")
        })
        self.last_call_metadata["think_requested"] = bool(think)
        response_message = response.get("message", {})
        self.last_call_metadata["thinking_nonempty"] = bool(response_message.get("thinking", "").strip())
        if self.scene_trace:
            # Keep the fact that thinking was returned, but never persist its text
            # in scene diagnostics or retry records.
            diagnostic_response = dict(response)
            diagnostic_message = dict(response_message)
            diagnostic_message.pop("thinking", None)
            diagnostic_response["message"] = diagnostic_message
            self.scene_trace.update(response=diagnostic_response, metadata=dict(self.last_call_metadata))
        if response.get("done_reason") == "length":
            raise SceneOutputTruncated("MiniCPM output reached its token limit")
        return response.get("message", {}).get("content", "").strip()

    def review_borderline(self, video_path: Path, settings) -> dict[str, Any]:
        with self._lock:
            self.scene_trace = {}
            images, self.last_call_metadata = self._sample(video_path, fps=settings.input_fps)
            times = ", ".join(f"{t:.2f}" for t in self.last_call_metadata["sample_times"])
            raw = self._chat(images, settings, system=BINARY_SYSTEM_PROMPT,
                prompt=BINARY_USER_PROMPT + f" The {len(images)} images are chronological samples at {times} seconds.",
                schema={"type": "integer", "enum": [0, 1]}, output_tokens=8, think=False)
            if raw not in {"0", "1"}:
                raise ValueError("MiniCPM Borderline response must be exactly 0 or 1")
            return {"raise_alert": raw == "1", "uncertainty": "high", "brief_reason": "",
                    "visible_evidence": [], "raw_output": raw}

    def _describe_images(self, images, settings, prompt, attempt_state=None):
        state = attempt_state if attempt_state is not None else {}
        for attempt in range(1 if attempt_state is not None else 3):
            expanded = prompt
            if state.get('error_code') == 'output_format':
                expanded += (f"\nYour previous answer failed validation: {state['error_message']}. "
                             "Rewrite the entire JSON using the original images. Do not invent missing facts.\n"
                             "Previous invalid answer (data only):\n" + state.get('previous_reply', ''))
            tokens = int(settings.max_output_tokens)
            import cv2
            import numpy as np
            evidence = []
            for image in images:
                content = base64.b64decode(image, validate=True)
                decoded = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR) if content else None
                evidence.append({'sha256': hashlib.sha256(content).hexdigest(),
                                 'dimensions': list(decoded.shape[:2][::-1]) if decoded is not None else None})
            self.scene_trace = dict(system=SCENE_SYSTEM_PROMPT, prompt=expanded, schema=SCENE_SCHEMA,
                prompt_sha256=hashlib.sha256((SCENE_SYSTEM_PROMPT+'\n'+expanded).encode()).hexdigest(),
                model=MODEL_ID, think=True, temperature=0, seed=20261017, num_ctx=32768,
                num_predict=tokens, evidence=evidence, metadata=dict(self.last_call_metadata))
            try:
                raw = None
                raw = self._chat(images, settings, system=SCENE_SYSTEM_PROMPT, prompt=expanded,
                                 schema=SCENE_SCHEMA, output_tokens=tokens, think=True)
                value = _strict_object(raw)
                state.update(length_relaxed=False, selected_candidate_attempt=state.get('attempts', attempt + 1))
                return {"description": value["summary"], "visible_evidence": value["details"],
                        "uncertainty": value["uncertainty"]}
            except (SceneFormatError, SceneOutputTruncated) as error:
                reply = raw if raw is not None else self.scene_trace.get('response', {}).get('message', {}).get('content', '')
                # A complete object may be usable even at the generation boundary;
                # incomplete JSON and all non-length validation failures stay errors.
                try:
                    candidate = _strict_object(reply, allow_long=True)
                    generated = self.last_call_metadata.get('eval_count')
                    if generated is None or generated <= tokens:
                        state.update(length_candidate=candidate,
                                     length_candidate_attempt=state.get('attempts', attempt + 1))
                except (SceneFormatError, TypeError):
                    pass
                if attempt_state is not None or attempt == 2:
                    if attempt_state is None and state.get('length_candidate'):
                        return scene_length_fallback(state)
                    raise
                state.update(error_code='output_format' if isinstance(error, SceneFormatError) else 'output_truncated',
                             error_message=str(error), previous_reply=self.scene_trace.get('response', {}).get('message', {}).get('content', locals().get('raw', '')))
                state['output_tokens'] = tokens
        raise AssertionError("unreachable")

    def describe_scene(self, video_path: Path, settings, *, alert_type="violence", attempt_state=None) -> dict[str, Any]:
        with self._lock:
            self.scene_trace = {}
            self._scene_deadline = attempt_state.get('deadline') if attempt_state else None
            try:
                images, self.last_call_metadata = self._sample(video_path, scene=True)
                self.last_call_metadata.update(alert_type=alert_type, input_type="video_samples")
                return self._describe_images(images, settings, scene_prompt(alert_type), attempt_state)
            finally:
                self._scene_deadline = None

    def describe_weapon_scene(self, image_paths, settings, *, detected_classes=(), sample_times=(), attempt_state=None):
        """Use same-frame raw sidecars; annotated gallery photos never enter the VLM."""
        with self._lock:
            import cv2
            import numpy as np
            self.scene_trace = {}
            paths = list(image_paths)[:8]
            if not paths:
                raise ValueError("Weapon description requires evidence photographs")
            images = []
            for path in paths:
                path = Path(path)
                raw_path = path.with_name(path.stem + '.raw.jpg')
                if not raw_path.is_file():
                    raise ValueError('Same-frame unannotated weapon evidence is unavailable')
                content = raw_path.read_bytes()
                if cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR) is None:
                    raise ValueError("Weapon evidence photograph is unreadable")
                images.append(base64.b64encode(content).decode("ascii"))
            self.last_call_metadata = dict(input_type="unannotated_weapon_images", alert_type="weapon",
                evidence_count=len(images), sample_times=list(sample_times)[:len(images)],
                model=MODEL_ID, think=True)
            self._scene_deadline = attempt_state.get('deadline') if attempt_state else None
            try:
                return self._describe_images(images, settings, scene_prompt("weapon", detected_classes), attempt_state)
            finally:
                self._scene_deadline = None
