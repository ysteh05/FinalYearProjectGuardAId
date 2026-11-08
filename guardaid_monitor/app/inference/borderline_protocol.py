"""Source-window input contract selected by the archived Borderline evaluation.

Scene Description intentionally does not use this module.
"""
from __future__ import annotations

import base64
import configparser
import hashlib
from io import BytesIO
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

PROTOCOL_VERSION = "minicpm_binary_source_window_v2"
WINDOW_SECONDS = 4.0


def build_prompt(fps: float, frame_count: int) -> str:
    prefix = (
        "Classify the visible actions in this clip. Return 1 if the sequence shows credible "
        "aggressive physical contact or an apparent physical attack, such as punching, kicking, "
        "striking, forceful pushing, choking, aggressive grappling, dragging, or repeated "
        "assaultive contact. Return 0 for ordinary movement, sports, non-aggressive play, "
        "dancing, crowding, accidental contact, a fall without an attack, or when the frames "
        "do not visibly support physical violence. Use only the images, not audio. These "
    )
    if fps == 2:
        samples = "8 images are chronological samples at 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, and 3.5 "
    else:
        offsets = ", ".join(f"{i / fps:.2f}" for i in range(frame_count))
        samples = f"{frame_count} images are chronological samples at {fps:g} FPS, at offsets {offsets} "
    return prefix + samples + (
        "seconds from the same 4-second window. Reply with exactly one character: 0 or 1. "
        "Do not include any other text."
    )


def find_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    packages = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for package in sorted(packages.glob("Gyan.FFmpeg*")):
        matches = sorted(package.glob("ffmpeg-*-full_build*/bin/ffmpeg.exe"))
        if matches:
            return str(matches[0])
    raise FileNotFoundError("FFmpeg is required for Borderline source-window sampling")


def prepare_images(source: Path, *, start: float = 0, fps: float = 4) -> tuple[list[str], dict]:
    from PIL import Image, ImageOps

    source = Path(source)
    if not math.isfinite(start) or start < 0:
        raise ValueError("Borderline source-window start must be finite and nonnegative")
    if not math.isfinite(fps) or not .25 <= fps <= 12:
        raise ValueError("Borderline sampling FPS must be between 0.25 and 12")
    count = math.ceil(fps * 4 - 1e-7)
    if source.is_dir():
        metadata = configparser.ConfigParser()
        metadata.read(source / "seqinfo.ini", encoding="utf-8-sig")
        seq = metadata["Sequence"]
        rate = float(seq["frameRate"])
        frames = [(source / seq.get("imDir", "img1") /
                   f"{math.floor((start + i / fps) * rate + 1e-8) + 1:08d}{seq.get('imExt', '.jpg')}").read_bytes()
                  for i in range(count)]
    else:
        if not source.is_file():
            raise FileNotFoundError(source)
        with tempfile.TemporaryDirectory(prefix="guardaid-borderline-") as tmp:
            destination = Path(tmp)
            command = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                       "-ss", f"{start:.6f}", "-i", str(source), "-t", "4.000",
                       "-map", "0:v:0", "-an", "-vf", f"fps={fps:g}:round=near",
                       "-frames:v", str(count), "-q:v", "3", str(destination / "frame_%02d.jpg")]
            result = subprocess.run(command, capture_output=True, timeout=120)
            paths = sorted(destination.glob("frame_*.jpg"))
            if result.returncode or len(paths) != count:
                raise ValueError(f"Borderline window requires {count} frames; decoded {len(paths)}")
            frames = [p.read_bytes() for p in paths]
    images, hashes, dimensions = [], [], []
    for frame in frames:
        with Image.open(BytesIO(frame)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            width, height = image.size
            target = (360, max(1, round(height * 360 / width))) if width <= height else (
                max(1, round(width * 360 / height)), 360)
            if image.size != target:
                image = image.resize(target, Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="JPEG", quality=95, subsampling=0)
            encoded = output.getvalue()
            images.append(base64.b64encode(encoded).decode("ascii"))
            hashes.append(hashlib.sha256(encoded).hexdigest())
            dimensions.append(list(target))
    return images, {"protocol_version": PROTOCOL_VERSION, "source_path": str(source),
                    "source_start_seconds": start, "included_seconds": WINDOW_SECONDS,
                    "fps": fps, "sample_times": [i / fps for i in range(count)],
                    "frame_count": count, "short_side": 360, "image_sha256": hashes,
                    "image_dimensions": dimensions, "think": False}


def request_payload(model: str, images: list[str], fps: int) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": build_prompt(fps, len(images)),
                                           "images": images}], "stream": False, "think": False,
            "options": {"temperature": 0, "seed": 20261017, "num_predict": 12, "num_ctx": 24576}}


def parse_decision(response: dict) -> str:
    raw = response.get("message", {}).get("content", "")
    if response.get("done_reason") == "length" or not re.fullmatch(r"\s*[01]\s*", raw):
        raise ValueError("MiniCPM Borderline response must be a complete 0 or 1")
    return raw.strip()
