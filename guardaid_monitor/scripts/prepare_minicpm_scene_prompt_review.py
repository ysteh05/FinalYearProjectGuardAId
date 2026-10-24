"""Prepare exact-case visual review sheets for MiniCPM scene prompt tuning.

This utility reads the already-curated cases and writes derived contact sheets
under guardaid_monitor/test_artifacts. It does not edit source media or the
production prompt/configuration.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import av
import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
CASES_ROOT = ROOT / "VLM Fine-tune"
OUTPUT = Path(__file__).resolve().parents[1] / "test_artifacts" / "minicpm_scene_prompt_review"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def media_files(case_dir: Path) -> list[Path]:
    folder = case_dir / "media"
    return sorted(path for path in folder.iterdir() if path.is_file())


def video_frames(path: Path, short_side: int = 360) -> list[tuple[float, np.ndarray]]:
    """Mirror the production center-of-interval PTS sampler and resize rule."""
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError(f"No video stream: {path}")
        stream = container.streams.video[0]
        duration = (
            float(stream.duration * stream.time_base)
            if stream.duration is not None
            else float(container.duration or 0) / av.time_base
        )
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"Invalid video duration: {path}")
        fps = 2.0 if duration <= 8.0 + 1e-6 else 1.0
        included = min(duration, 60.0)
        count = max(1, math.ceil(included * fps - 1e-7))
        targets = [
            min((index + 0.5) / fps, max(0.0, included - 1e-6))
            for index in range(count)
        ]
        decoded: list[tuple[float, np.ndarray]] = []
        origin = None
        for frame in container.decode(stream):
            if frame.pts is None:
                raise ValueError(f"Missing frame PTS: {path}")
            absolute = float(frame.pts * frame.time_base)
            if origin is None:
                origin = absolute
            timestamp = absolute - origin
            image = frame.to_ndarray(format="bgr24")
            decoded.append((timestamp, image))
        if not decoded:
            raise ValueError(f"No decodable frames: {path}")

        selected = []
        index = 0
        for target in targets:
            while index + 1 < len(decoded) and decoded[index + 1][0] <= target + 1e-7:
                index += 1
            timestamp, image = decoded[index]
            height, width = image.shape[:2]
            factor = short_side / min(height, width)
            resized = cv2.resize(
                image,
                (round(width * factor), round(height * factor)),
                interpolation=cv2.INTER_AREA if factor <= 1 else cv2.INTER_LINEAR,
            )
            selected.append((target, resized))
        return selected


def tile(label: str, image: np.ndarray, width: int, height: int) -> np.ndarray:
    h, w = image.shape[:2]
    factor = min((width - 16) / w, (height - 34) / h)
    shown = cv2.resize(image, (max(1, round(w * factor)), max(1, round(h * factor))))
    canvas = np.full((height, width, 3), 248, dtype=np.uint8)
    x = (width - shown.shape[1]) // 2
    y = 28 + (height - 28 - shown.shape[0]) // 2
    canvas[y : y + shown.shape[0], x : x + shown.shape[1]] = shown
    cv2.putText(canvas, label[:68], (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (20, 20, 20), 1, cv2.LINE_AA)
    return canvas


def save_sheet(case_id: str, items: list[tuple[str, np.ndarray]], *, cols: int, tile_w: int, tile_h: int) -> Path:
    rows = math.ceil(len(items) / cols)
    board = np.full((rows * tile_h, cols * tile_w, 3), 255, dtype=np.uint8)
    for position, (label, image) in enumerate(items):
        cell = tile(label, image, tile_w, tile_h)
        y = (position // cols) * tile_h
        x = (position % cols) * tile_w
        board[y : y + tile_h, x : x + tile_w] = cell
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / f"{case_id}.jpg"
    if not cv2.imwrite(str(target), board, [cv2.IMWRITE_JPEG_QUALITY, 92]):
        raise RuntimeError(f"Could not write contact sheet: {target}")
    return target


def main() -> None:
    inventory = []
    case_jsons = sorted(CASES_ROOT.glob("*/*/Case */case.json"))
    if len(case_jsons) != 40:
        raise SystemExit(f"Expected 40 curated cases; found {len(case_jsons)}")

    for metadata_path in case_jsons:
        case_dir = metadata_path.parent
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        files = media_files(case_dir)
        if not files:
            raise RuntimeError(f"No copied media for {metadata['case_id']}")
        for index, path in enumerate(files):
            expected = metadata["copied_media_sha256"][index]
            actual = sha256(path)
            if actual != expected:
                raise RuntimeError(f"Copied-media SHA-256 mismatch: {metadata['case_id']} {path.name}")

        items: list[tuple[str, np.ndarray]] = []
        media_records = []
        is_video_case = any(path.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".webm"} for path in files)
        for path in files:
            if path.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
                sampled = video_frames(path)
                for target_time, frame in sampled:
                    items.append((f"{metadata['case_id']}  target={target_time:.2f}s", frame))
                media_records.append({
                    "name": path.name,
                    "sha256": sha256(path),
                    "kind": "video",
                    "sample_count": len(sampled),
                    "target_times": [round(t, 6) for t, _ in sampled],
                })
            else:
                image = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if image is None:
                    raise RuntimeError(f"Unreadable copied image: {path}")
                items.append((f"{metadata['case_id']}  photo={path.name}", image))
                media_records.append({"name": path.name, "sha256": sha256(path), "kind": "image"})

        sheet_path = save_sheet(
            metadata["case_id"], items,
            cols=5 if is_video_case else 2,
            tile_w=320 if is_video_case else 620,
            tile_h=220 if is_video_case else 430,
        )
        inventory.append({
            "case_id": metadata["case_id"],
            "split": metadata["split"],
            "category": metadata["category"],
            "source_dataset": metadata["source_dataset"],
            "ground_truth_status": metadata["ground_truth_status"],
            "sheet": str(sheet_path.relative_to(ROOT)),
            "media": media_records,
        })
        print(f"[review-sheet] {metadata['case_id']} | {metadata['category']} | {len(items)} frames/photos", flush=True)

    inventory_path = OUTPUT / "inventory.json"
    inventory_path.write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[complete] {len(inventory)} cases; sheets at {OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
