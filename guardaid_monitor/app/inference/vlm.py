from __future__ import annotations

import gc
import importlib
import json
import os
from pathlib import Path
import re
from threading import Lock
from typing import Any


BINARY_SYSTEM_PROMPT = """You are the visual review component of GuardAId, a human-in-the-loop school safety prototype. Use only observable visual evidence in the supplied CCTV-like video. Do not infer identity, motive, relationship, guilt, injury severity, or events outside the clip. Do not use audio. Return exactly one JSON object and no markdown."""

BINARY_USER_PROMPT = """Review the entire video in chronological order. Return raise_alert=1 only when the visible sequence supports credible aggressive physical contact or an apparent physical attack, such as punching, kicking, striking, forceful pushing, choking, aggressive grappling, dragging, or repeated assaultive contact. Return 0 for ordinary movement, sports, play without credible aggression, accidental contact, falls without attack, verbal disagreement without physical aggression, or evidence too poor to support the judgement. Judge the temporal sequence, describe only visible movements, and do not assume intent.
Required JSON: {"raise_alert":0,"decision_label":"Do Not Raise Alert","visible_evidence":["short observation"],"brief_reason":"one concise sentence","uncertainty":"low"}. decision_label must be Raise Alert for 1 and Do Not Raise Alert for 0; uncertainty is low, medium, or high."""

SCENE_SYSTEM_PROMPT = """You create a concise incident description for an authorised human reviewer of a school-safety prototype. Describe only observable visual facts from the supplied CCTV-like video. Do not identify people, infer motive, guilt, relationship, injury, age, ethnicity, intent, severity, or events outside the clip. Ignore audio. Return exactly one JSON object and no markdown."""

SCENE_USER_PROMPT = """Review the clip in chronological order. Return exactly one JSON object with no Markdown or explanation outside JSON. The description must be one cautious observed action or scene statement, approximately 12 English words or fewer and no more than 120 characters. Include zero to two short factual visible-evidence items, each about 10 English words or fewer. Use uncertainty only as low, medium, or high. Do not state identity, motive, intent, guilt, injury, legal classification, severity, or anything not visible in the supplied clip.
Required JSON: {"description":"Possible physical contact between two people","visible_evidence":["Two people are visible.","One person moves an arm toward the other."],"uncertainty":"medium"}."""


def _available_ram_gib() -> float | None:
    """Return immediately usable physical RAM without allocating test buffers."""

    try:
        import psutil

        return psutil.virtual_memory().available / (1024**3)
    except (ImportError, OSError):
        return None


def _json_value(text: str) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("VLM output did not contain a JSON object")
        return json.loads(cleaned[start : end + 1])


def _json_object(text: str) -> dict[str, Any]:
    value = _json_value(text)
    if not isinstance(value, dict):
        raise ValueError("VLM output must be a JSON object")
    return value


def _clean_model_text(text: str, *, limit: int = 1200) -> str:
    """Convert a non-JSON generation to bounded display text without guessing."""

    cleaned = re.sub(r"<think>.*?</think>", " ", text, flags=re.I | re.S)
    cleaned = re.sub(r"```(?:json|text)?|```", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"^\s*(?:description|answer|response)\s*:\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit].rstrip()


def _scene_description_from_output(text: str) -> str:
    """Extract a useful description from strict or near-JSON model output."""

    try:
        value = _json_object(text)
        description = str(value.get("description", "")).strip()
        if description:
            return description
    except (ValueError, json.JSONDecodeError):
        pass
    # Qwen can emit a valid quoted description followed by truncated evidence,
    # and occasionally uses \\' instead of JSON's apostrophe form. Retain the
    # observable description rather than presenting raw model formatting.
    match = re.search(
        r'["\']description["\']\s*:\s*("(?:\\.|[^"\\])*")', text, flags=re.I
    )
    if match:
        try:
            description = json.loads(match.group(1).replace("\\'", "'"))
            if isinstance(description, str) and description.strip():
                return description.strip()
        except json.JSONDecodeError:
            pass
    incomplete = re.search(r'["\']description["\']\s*:\s*["\'](.*)$', text, flags=re.I | re.S)
    if incomplete:
        fragment = incomplete.group(1).strip()
        last_stop = max(fragment.rfind("."), fragment.rfind("!"), fragment.rfind("?"))
        if last_stop >= 40:
            return fragment[: last_stop + 1]
        if fragment:
            return fragment[:800].rsplit(" ", 1)[0].rstrip() + "…"
    return _clean_model_text(text)


def _scene_summary_from_output(text: str) -> dict[str, Any] | None:
    """Validate and bound the concise scene-summary contract."""

    try:
        value: Any = _json_value(text)
        for _ in range(2):
            if isinstance(value, str):
                value = _json_value(value)
            elif isinstance(value, dict) and isinstance(value.get("scene_summary"), dict):
                value = value["scene_summary"]
            elif isinstance(value, dict) and isinstance(value.get("result"), dict) and not value.get("description"):
                value = value["result"]
            else:
                break
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    description = str(value.get("description", "")).strip()
    if not description:
        return None
    description = re.sub(r"\s+", " ", description)
    words = description.split()
    if len(words) > 12:
        description = " ".join(words[:12]).rstrip(" ,;:") + "…"
    description = description[:120].rstrip()
    evidence_value = value.get("visible_evidence", value.get("visibleEvidence", []))
    evidence: list[str] = []
    if isinstance(evidence_value, list):
        for item in evidence_value:
            text_value = re.sub(r"\s+", " ", str(item).strip())
            if not text_value:
                continue
            item_words = text_value.split()
            if len(item_words) > 10:
                text_value = " ".join(item_words[:10]).rstrip(" ,;:") + "…"
            evidence.append(text_value[:120].rstrip())
            if len(evidence) == 2:
                break
    uncertainty = str(value.get("uncertainty", "high")).casefold()
    if uncertainty not in {"low", "medium", "high"}:
        uncertainty = "high"
    return {
        "description": description,
        "visible_evidence": evidence,
        "uncertainty": uncertainty,
    }


def _borderline_object(text: str) -> dict[str, Any]:
    """Parse strict JSON first, then only explicit text decisions."""

    try:
        return _json_object(text)
    except (ValueError, json.JSONDecodeError):
        cleaned = _clean_model_text(text)
        lower = cleaned.casefold()
        match = re.search(r"raise[_\s-]*alert\s*[:=]\s*[\"']?([01])", cleaned, flags=re.I)
        if match:
            decision = int(match.group(1))
        elif re.search(r"\b(?:do not|don't|not to) raise (?:an )?alert\b", lower):
            decision = 0
        elif re.search(r"\braise (?:an )?alert\b", lower):
            decision = 1
        else:
            raise ValueError("VLM output did not contain a usable raise_alert decision")
        uncertainty_match = re.search(
            r"uncertainty\s*[:=]\s*[\"']?(low|medium|high)", cleaned, flags=re.I
        )
        return {
            "raise_alert": decision,
            "decision_label": "Raise Alert" if decision else "Do Not Raise Alert",
            "brief_reason": cleaned,
            "visible_evidence": [],
            "uncertainty": uncertainty_match.group(1).lower() if uncertainty_match else "high",
        }


class QwenNativeVideoRunner:
    MODEL_VERSION = "Qwen/Qwen3-VL-2B-Instruct"

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir.resolve()
        self._lock = Lock()
        self._model = None
        self._processor = None
        self._torch = None
        self._loaded_model_id: str | None = None
        self._dll_handles: list[Any] = []

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def _snapshot(self, model_id: str) -> Path | None:
        repo = self.cache_dir / f"models--{model_id.replace('/', '--')}"
        ref = repo / "refs" / "main"
        if not ref.is_file():
            return None
        snapshot = repo / "snapshots" / ref.read_text(encoding="utf-8").strip()
        return snapshot if (snapshot / "config.json").is_file() else None

    def unload(self) -> None:
        torch = self._torch
        self._model = self._processor = self._torch = None
        self._loaded_model_id = None
        gc.collect()
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()

    def load(self, model_id: str, video_token_budget: int) -> None:
        if self.is_loaded and self._loaded_model_id == model_id:
            self._set_video_budget(video_token_budget)
            return
        with self._lock:
            if self.is_loaded and self._loaded_model_id != model_id:
                self.unload()
            if self.is_loaded:
                self._set_video_budget(video_token_budget)
                return
            os.environ.setdefault("FORCE_QWENVL_VIDEO_READER", "torchcodec")
            os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
            os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
            os.environ.setdefault("HF_ENABLE_PARALLEL_LOADING", "false")
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            os.environ.setdefault("OMP_NUM_THREADS", "2")
            os.environ.setdefault("MKL_NUM_THREADS", "2")
            minimum_ram_gib = float(
                os.environ.get("GUARDAID_VLM_MIN_AVAILABLE_RAM_GB", "3.0")
            )
            available_ram_gib = _available_ram_gib()
            if available_ram_gib is not None and available_ram_gib < minimum_ram_gib:
                raise RuntimeError(
                    f"Qwen3-VL load was stopped safely: only {available_ram_gib:.1f} GiB "
                    f"physical RAM is available; the configured safety threshold is "
                    f"{minimum_ram_gib:.1f} GiB. The model may need more RAM while loading; "
                    "lowering this threshold does not reduce actual memory use. Close "
                    "Android Studio, PyCharm and unnecessary browser tabs, then retry. "
                    "The backend was kept alive."
                )
            self._register_shared_ffmpeg()
            failures: list[str] = []
            for dependency in (
                "torch",
                "transformers",
                "accelerate",
                "bitsandbytes",
                "torchcodec",
            ):
                try:
                    importlib.import_module(dependency)
                except Exception as exc:  # DLL/runtime failures are not ImportError.
                    detail = str(exc).strip().splitlines()[0] if str(exc).strip() else "unknown error"
                    failures.append(f"{dependency}: {type(exc).__name__}: {detail}")
            if failures:
                raise RuntimeError(
                    "Qwen3-VL runtime dependency check failed:\n- "
                    + "\n- ".join(failures)
                    + "\nRun guardaid_monitor\\setup.ps1, then start the server with "
                    "guardaid_monitor\\run_demo.ps1."
                )
            import torch
            try:
                from transformers import (
                    AutoModelForImageTextToText,
                    AutoProcessor,
                    BitsAndBytesConfig,
                )
            except ImportError as exc:
                raise RuntimeError(
                    "The installed Transformers build does not expose the Qwen3-VL APIs. "
                    "Run guardaid_monitor\\setup.ps1 to install the project-pinned build."
                ) from exc
            if not torch.cuda.is_available():
                raise RuntimeError("Qwen3-VL NF4 runtime requires a CUDA GPU.")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            snapshot = self._snapshot(model_id)
            source = str(snapshot) if snapshot else model_id
            local_only = snapshot is not None
            try:
                processor = AutoProcessor.from_pretrained(
                    source, cache_dir=str(self.cache_dir), local_files_only=local_only
                )
                quantization = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_compute_dtype=torch.float16,
                )
                model = AutoModelForImageTextToText.from_pretrained(
                    source,
                    cache_dir=str(self.cache_dir),
                    local_files_only=local_only,
                    quantization_config=quantization,
                    device_map="auto",
                    dtype=torch.float16,
                    low_cpu_mem_usage=True,
                ).eval()
            except Exception:
                # A failed optional model load must release transient CUDA
                # allocations before MC3/YOLO are initialised by the caller.
                processor = None
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                raise
            self._torch, self._processor, self._model = torch, processor, model
            self._loaded_model_id = model_id
            self._set_video_budget(video_token_budget)

    def _register_shared_ffmpeg(self) -> None:
        """Register the known WinGet shared-FFmpeg DLL folder for TorchCodec."""

        if os.name != "nt" or not hasattr(os, "add_dll_directory"):
            return
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            return
        packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        try:
            packages_available = packages.is_dir()
        except OSError:
            packages_available = False
        if not packages_available:
            return
        try:
            for package in packages.glob("Gyan.FFmpeg.Shared*"):
                dll = next(package.rglob("avcodec-*.dll"), None)
                if dll is not None:
                    dll_dir = str(dll.parent.resolve())
                    os.environ["PATH"] = f"{dll_dir}{os.pathsep}{os.environ.get('PATH', '')}"
                    self._dll_handles.append(os.add_dll_directory(dll_dir))
                    return
        except OSError:
            return

    def _set_video_budget(self, budget: int) -> None:
        if self._processor is not None and hasattr(self._processor, "video_processor"):
            self._processor.video_processor.size = {
                "shortest_edge": 256 * 32 * 32 * 2,
                "longest_edge": int(budget) * 32 * 32 * 2,
            }

    def _uniform_scene_frames(
        self, video_path: Path, requested_frames: int
    ) -> tuple[Any, Any, float]:
        """Decode fixed scene frames plus source metadata for native video processing."""

        import cv2
        import numpy as np
        try:
            from transformers.video_utils import VideoMetadata
        except ModuleNotFoundError:
            # Keep frame sampling testable in the lightweight backend environment.
            # The native Qwen path still requires Transformers when it is loaded;
            # this fallback only carries the metadata fields consumed by the
            # sampling and test code.
            from types import SimpleNamespace

            VideoMetadata = SimpleNamespace  # type: ignore[misc,assignment]

        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise RuntimeError("OpenCV could not open the scene-description clip")
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        source_fps = float(capture.get(cv2.CAP_PROP_FPS))
        if total_frames <= 0 or not source_fps > 0:
            capture.release()
            raise RuntimeError("The scene-description clip has invalid timing metadata")

        duration = total_frames / source_fps
        # Midpoint sampling gives every part of the clip equal representation:
        # 5 s -> 4 FPS, 20 s -> 1 FPS and 40 s -> 0.5 FPS at the default 20 frames.
        target_indices = [
            min(total_frames - 1, int((index + 0.5) * total_frames / requested_frames))
            for index in range(requested_frames)
        ]
        sampled_frames = []
        last_frame = None
        try:
            for target_index in target_indices:
                capture.set(cv2.CAP_PROP_POS_FRAMES, target_index)
                ok, frame = capture.read()
                if ok:
                    last_frame = frame
                elif last_frame is not None:
                    frame = last_frame
                else:
                    raise RuntimeError("A uniformly selected scene frame could not be decoded")
                sampled_frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            metadata = VideoMetadata(
                total_num_frames=total_frames,
                fps=source_fps,
                width=sampled_frames[0].shape[1],
                height=sampled_frames[0].shape[0],
                duration=duration,
                frames_indices=target_indices,
            )
            return np.stack(sampled_frames), metadata, requested_frames / duration
        finally:
            capture.release()

    def _generate(
        self,
        video_input: Any,
        *,
        model_id: str,
        fps: float,
        video_token_budget: int,
        max_output_tokens: int,
        system_prompt: str,
        user_prompt: str,
        video_metadata: Any = None,
        pre_sampled: bool = False,
    ) -> str:
        self.load(model_id, video_token_budget)
        if isinstance(video_input, Path):
            if not video_input.is_file():
                raise FileNotFoundError(video_input)
            video_value = str(video_input.resolve())
        else:
            video_value = video_input
        with self._lock:
            messages = [
                {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
                {
                    "role": "user",
                    "content": [
                        {"type": "video", "video": video_value},
                        {"type": "text", "text": user_prompt},
                    ],
                },
            ]
            processor_kwargs = (
                {"do_sample_frames": False, "video_metadata": video_metadata}
                if pre_sampled
                else {"fps": fps}
            )
            inputs = self._processor.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
                return_dict=True,
                return_tensors="pt",
                processor_kwargs=processor_kwargs,
            )
            inputs = inputs.to(next(self._model.parameters()).device)
            with self._torch.inference_mode():
                generated = self._model.generate(
                    **inputs,
                    max_new_tokens=max_output_tokens,
                    do_sample=False,
                    use_cache=True,
                )
            generated = generated[:, inputs.input_ids.shape[1] :]
            return self._processor.batch_decode(
                generated, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )[0].strip()

    def review_borderline(self, video_path: Path, settings) -> dict[str, Any]:
        raw = self._generate(
            video_path,
            model_id=settings.model_id,
            fps=settings.input_fps,
            video_token_budget=settings.video_token_budget,
            max_output_tokens=settings.max_output_tokens,
            system_prompt=BINARY_SYSTEM_PROMPT,
            user_prompt=BINARY_USER_PROMPT,
        )
        value = _borderline_object(raw)
        decision = value.get("raise_alert")
        if isinstance(decision, str) and decision.strip() in {"0", "1"}:
            decision = int(decision)
        if isinstance(decision, bool):
            decision = int(decision)
        if decision not in {0, 1}:
            raise ValueError("VLM raise_alert must be 0 or 1")
        expected = "Raise Alert" if decision else "Do Not Raise Alert"
        if value.get("decision_label", expected) != expected:
            raise ValueError("VLM decision_label does not match raise_alert")
        uncertainty = str(value.get("uncertainty", "high")).lower()
        if uncertainty not in {"low", "medium", "high"}:
            uncertainty = "high"
        return {
            "raise_alert": bool(decision),
            "uncertainty": uncertainty,
            "brief_reason": str(value.get("brief_reason", "")).strip(),
            "visible_evidence": value.get("visible_evidence", []),
            "raw_output": raw,
        }

    def describe_scene(self, video_path: Path, settings) -> dict[str, Any]:
        # Load first so the small scene-frame batch does not add to first-load RAM peak.
        self.load(settings.model_id, settings.video_token_budget)
        sampled_frames, metadata, sampled_fps = self._uniform_scene_frames(
            video_path, settings.scene_description_frames
        )
        raw = self._generate(
            sampled_frames,
            model_id=settings.model_id,
            fps=sampled_fps,
            video_token_budget=settings.video_token_budget,
            max_output_tokens=settings.max_output_tokens,
            system_prompt=SCENE_SYSTEM_PROMPT,
            user_prompt=SCENE_USER_PROMPT,
            video_metadata=metadata,
            pre_sampled=True,
        )
        summary = _scene_summary_from_output(raw)
        if summary is None:
            raise ValueError("VLM scene summary did not contain a usable JSON object")
        return summary
