"""Pure reference input functions retained from the original evaluator.
No dataset paths, media, predictions or experimental records are included.
"""
from __future__ import annotations
from pathlib import Path
from io import BytesIO
import shutil, subprocess

WINDOW_SECONDS = 4.0

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
