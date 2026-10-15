"""Run the GuardAId MC3-18 violence classifier on a video.

The model receives a short temporal clip rather than one image.  By default
this script follows the deployed workflow: a five-second sliding window,
2.5-second stride, 20 uniformly sampled frames, 128-pixel short side and a
centre 112x112 crop.  The output is the probability of class index 1,
``Fight``.

Example::

    python model_code/mc3_18_infer.py `
        --checkpoint checkpoints_rwf2000/mc3_18_best.pt `
        --video "sample.mp4" `
        --threshold-mode moderate `
        --output-json output/mc3_predictions.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
from torchvision.models.video import mc3_18


NUM_FRAMES = 20
SHORT_SIDE = 128
CROP_SIZE = 112
MEAN = np.array([0.43216, 0.394666, 0.37645], dtype=np.float32)
STD = np.array([0.22803, 0.22145, 0.216989], dtype=np.float32)

# These are the validation-calibrated operating points used by GuardAId.
# They express different operational preferences, not different models.
THRESHOLDS = {
    "sensitive": 0.009688,
    "moderate": 0.056,
    "certain": 0.710192,
}


@dataclass(frozen=True, slots=True)
class WindowPrediction:
    start_seconds: float
    end_seconds: float
    probability_fight: float
    threshold: float
    alert: bool


def resize_short_side(frame: np.ndarray, short_side: int = SHORT_SIDE) -> np.ndarray:
    height, width = frame.shape[:2]
    if height <= width:
        new_height = short_side
        new_width = int(round(width * short_side / height))
    else:
        new_width = short_side
        new_height = int(round(height * short_side / width))
    return cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_LINEAR)


def preprocess_frames(bgr_frames: list[np.ndarray]) -> torch.Tensor:
    """Convert BGR frames into a normalised tensor shaped (1,C,T,H,W)."""

    if not bgr_frames:
        raise ValueError("MC3-18 received an empty frame sequence")
    if len(bgr_frames) != NUM_FRAMES:
        raise ValueError(f"MC3-18 expects exactly {NUM_FRAMES} frames, got {len(bgr_frames)}")
    rgb = [cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) for frame in bgr_frames]
    resized = [resize_short_side(frame) for frame in rgb]
    height, width = resized[0].shape[:2]
    if height < CROP_SIZE or width < CROP_SIZE:
        raise ValueError(f"Input frame is too small after resize: {(height, width)}")
    top, left = (height - CROP_SIZE) // 2, (width - CROP_SIZE) // 2
    clip = np.stack([frame[top : top + CROP_SIZE, left : left + CROP_SIZE] for frame in resized])
    clip = clip.astype(np.float32) / 255.0
    clip = (clip - MEAN) / STD
    clip = np.transpose(clip, (3, 0, 1, 2))
    return torch.from_numpy(clip).unsqueeze(0)


class Mc3ViolenceRunner:
    def __init__(self, checkpoint: Path, *, force_cpu: bool = False) -> None:
        self.checkpoint = checkpoint.resolve()
        self.device = torch.device(
            "cuda" if torch.cuda.is_available() and not force_cpu else "cpu"
        )
        self.model = self._load_model()

    def _load_model(self) -> nn.Module:
        if not self.checkpoint.is_file():
            raise FileNotFoundError(f"MC3-18 checkpoint not found: {self.checkpoint}")
        model = mc3_18(weights=None)
        model.fc = nn.Linear(model.fc.in_features, 2)
        try:
            payload = torch.load(str(self.checkpoint), map_location="cpu", weights_only=True)
        except TypeError:  # Compatibility with older PyTorch versions.
            payload = torch.load(str(self.checkpoint), map_location="cpu")
        state_dict = payload.get("state_dict", payload)
        model.load_state_dict(state_dict, strict=True)
        model = model.to(self.device).eval()
        if self.device.type == "cuda":
            model = model.half()
        return model

    def infer_frames(self, bgr_frames: list[np.ndarray]) -> float:
        tensor = preprocess_frames(bgr_frames).to(self.device)
        if self.device.type == "cuda":
            tensor = tensor.half()
        with torch.inference_mode():
            logits = self.model(tensor)
            probability = torch.softmax(logits.float(), dim=1)[0, 1].item()
        return float(probability)


def video_info(video_path: Path) -> tuple[float, int, float]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Unable to open video: {video_path}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps if frame_count > 0 else 0.0
    capture.release()
    return float(fps), frame_count, float(duration)


def read_frames_at_times(video_path: Path, times: np.ndarray, fps: float, frame_count: int) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Unable to open video: {video_path}")
    frames: list[np.ndarray] = []
    last_frame: np.ndarray | None = None
    try:
        for timestamp in times:
            index = int(np.clip(round(float(timestamp) * fps), 0, max(frame_count - 1, 0)))
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok:
                if last_frame is None:
                    raise RuntimeError(f"Unable to read frame {index} from {video_path}")
                frame = last_frame.copy()
            frames.append(frame)
            last_frame = frame
    finally:
        capture.release()
    return frames


def sliding_windows(duration: float, segment: float, stride: float) -> list[tuple[float, float]]:
    if segment <= 0 or stride <= 0 or stride > segment:
        raise ValueError("Require segment > 0 and 0 < stride <= segment")
    if duration <= 0:
        return []
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start + segment <= duration + 1e-9:
        windows.append((round(start, 3), round(min(start + segment, duration), 3)))
        start += stride
    tail_start = max(0.0, duration - segment)
    if not windows or tail_start > windows[-1][0] + 1e-9:
        windows.append((round(tail_start, 3), round(duration, 3)))
    return windows


def predict_video(
    runner: Mc3ViolenceRunner,
    video_path: Path,
    *,
    segment_seconds: float,
    stride_seconds: float,
    num_frames: int,
    threshold: float,
) -> list[WindowPrediction]:
    if num_frames != NUM_FRAMES:
        raise ValueError(f"The locked MC3-18 protocol uses exactly {NUM_FRAMES} frames")
    fps, frame_count, duration = video_info(video_path)
    predictions: list[WindowPrediction] = []
    for start, end in sliding_windows(duration, segment_seconds, stride_seconds):
        sample_times = np.linspace(start, end, num=num_frames)
        frames = read_frames_at_times(video_path, sample_times, fps, frame_count)
        probability = runner.infer_frames(frames)
        predictions.append(
            WindowPrediction(
                start_seconds=start,
                end_seconds=end,
                probability_fight=probability,
                threshold=threshold,
                alert=probability >= threshold,
            )
        )
    return predictions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--threshold-mode", choices=tuple(THRESHOLDS), default="moderate")
    parser.add_argument("--threshold", type=float, help="Override the selected operating threshold.")
    parser.add_argument("--window-seconds", type=float, default=5.0)
    parser.add_argument("--stride-seconds", type=float, default=2.5)
    parser.add_argument("--frames", type=int, default=NUM_FRAMES, help="Must remain 20 to match training.")
    parser.add_argument("--output-json", help="Optional JSON output path.")
    parser.add_argument("--cpu", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    video = Path(args.video).expanduser().resolve()
    mode_threshold = THRESHOLDS[args.threshold_mode]
    threshold = float(args.threshold if args.threshold is not None else mode_threshold)
    runner = Mc3ViolenceRunner(Path(args.checkpoint), force_cpu=args.cpu)
    predictions = predict_video(
        runner,
        video,
        segment_seconds=args.window_seconds,
        stride_seconds=args.stride_seconds,
        num_frames=args.frames,
        threshold=threshold,
    )
    payload = {
        "video": str(video),
        "device": str(runner.device),
        "threshold_mode": args.threshold_mode,
        "predictions": [asdict(item) for item in predictions],
    }
    print(json.dumps(payload, indent=2))
    if args.output_json:
        output = Path(args.output_json).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
