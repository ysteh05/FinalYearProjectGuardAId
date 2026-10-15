"""Run the GuardAId YOLOv9-T weapon detector on images or videos.

The preprocessing and postprocessing deliberately match
``guardaid_monitor/app/inference/weapon.py``:

1. letterbox the BGR frame to a 640-pixel YOLO input;
2. convert BGR -> RGB, HWC -> CHW and scale pixels to [0, 1];
3. run the YOLOv9 DualDDetect inference branch;
4. apply class-aware NMS;
5. rescale boxes to the original frame and apply a per-class policy threshold.

Example (single image)::

    python model_code/yolov9_weapon_infer.py `
        --checkpoint best.zip `
        --yolov9-root "results (1)/yolov9" `
        --source "Dangerous Items/images/test/example.jpg" `
        --threshold-file "results (1)/weapon_detection_final_comparison/reports/validation_15_per_class_thresholds.json" `
        --threshold-mode moderate `
        --output output/weapon_example.jpg
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


CLASS_NAMES = (
    "parang_machete",
    "knife",
    "baseball_bat",
    "long_gun",
    "gun",
)

DEFAULT_THRESHOLDS = {
    "sensitive": {
        "parang_machete": 0.24,
        "knife": 0.24,
        "baseball_bat": 0.34,
        "long_gun": 0.21,
        "gun": 0.24,
    },
    "moderate": {
        "parang_machete": 0.42,
        "knife": 0.42,
        "baseball_bat": 0.50,
        "long_gun": 0.54,
        "gun": 0.42,
    },
    "certain": {
        "parang_machete": 0.42,
        "knife": 0.63,
        "baseball_bat": 0.65,
        "long_gun": 0.66,
        "gun": 0.57,
    },
}


@dataclass(frozen=True, slots=True)
class Detection:
    class_id: int
    class_name: str
    confidence: float
    xyxy: tuple[int, int, int, int]
    threshold: float


def load_thresholds(
    path: Path | None, mode: str, class_names: tuple[str, ...] | None = None
) -> dict[str, float]:
    """Load validation-calibrated thresholds, or use project defaults."""

    if path is None:
        selected: Any = DEFAULT_THRESHOLDS[mode]
    else:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload.get("profiles"), list):
            profile = next(
                (item for item in payload["profiles"] if str(item.get("profile", "")).casefold() == mode.casefold()),
                None,
            )
            if profile is None:
                raise KeyError(f"Threshold profile '{mode}' not found")
            selected = profile.get("thresholds", {})
        elif isinstance(payload.get("thresholds"), dict):
            selected = payload["thresholds"]
        elif mode in payload:
            selected = payload[mode]
        else:
            raise KeyError(f"Threshold mode '{mode}' not found. Available modes: {list(payload)}")
    active_names = class_names or tuple(selected.keys()) or CLASS_NAMES
    missing = [name for name in active_names if name not in selected]
    if missing:
        raise ValueError(f"Threshold file is missing classes: {missing}")
    return {name: float(selected.get(name, 0.25)) for name in active_names}


class Yolov9WeaponRunner:
    """Small standalone wrapper around the official YOLOv9 runtime."""

    def __init__(self, checkpoint: Path, yolov9_root: Path) -> None:
        self.checkpoint = checkpoint.resolve()
        self.yolov9_root = yolov9_root.resolve()
        self.model = None
        self.class_names: tuple[str, ...] = ()
        self.torch = None
        self.device = None
        self.nms = None
        self.letterbox = None
        self.scale_boxes = None

    def load(self) -> None:
        if self.model is not None:
            return
        if not self.checkpoint.is_file():
            raise FileNotFoundError(f"YOLOv9 checkpoint not found: {self.checkpoint}")
        if not (self.yolov9_root / "models" / "experimental.py").is_file():
            raise FileNotFoundError(f"Official YOLOv9 runtime not found: {self.yolov9_root}")
        if str(self.yolov9_root) not in sys.path:
            sys.path.insert(0, str(self.yolov9_root))

        try:
            import torch
            from models.experimental import attempt_load
            from utils.augmentations import letterbox
            from utils.general import non_max_suppression, scale_boxes
        except ImportError as exc:
            raise RuntimeError(
                "YOLOv9 inference requires torch, torchvision, NumPy, OpenCV "
                "and the bundled YOLOv9 repository."
            ) from exc

        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        model = attempt_load(str(self.checkpoint), device=device, inplace=True, fuse=True).eval()
        if device.type == "cuda":
            model = model.half()
        names_value = model.module.names if hasattr(model, "module") else model.names
        names = tuple(names_value[index] for index in range(len(names_value)))
        if not names or len(names) != len(set(names)):
            raise RuntimeError(f"Checkpoint has an invalid class mapping: {names}")

        self.torch = torch
        self.device = device
        self.model = model
        self.class_names = names
        self.nms = non_max_suppression
        self.letterbox = letterbox
        self.scale_boxes = scale_boxes

    def infer(
        self,
        bgr_frame,
        thresholds: dict[str, float],
        *,
        candidate_floor: float = 0.001,
        iou_threshold: float = 0.45,
    ) -> list[Detection]:
        """Return detections that pass their own class threshold."""

        import numpy as np

        self.load()
        image = self.letterbox(
            bgr_frame, new_shape=(640, 640), stride=32, auto=True
        )[0]
        image = image[:, :, ::-1].transpose(2, 0, 1)
        image = np.ascontiguousarray(image)
        tensor = self.torch.from_numpy(image).to(self.device)
        tensor = tensor.half() if self.device.type == "cuda" else tensor.float()
        tensor = tensor.unsqueeze(0) / 255.0

        with self.torch.inference_mode():
            raw = self.model(tensor)

        # YOLOv9-T uses DualDDetect.  The official detect_dual.py inference
        # contract places the decoded prediction tensor at output[0][1].
        if (
            isinstance(raw, (list, tuple))
            and raw
            and isinstance(raw[0], (list, tuple))
            and len(raw[0]) > 1
        ):
            raw = raw[0][1]

        floor = max(0.0001, min(candidate_floor, min(thresholds.values())))
        detections = self.nms(
            raw,
            conf_thres=floor,
            iou_thres=iou_threshold,
            max_det=300,
        )[0]
        if not len(detections):
            return []

        detections[:, :4] = self.scale_boxes(
            tensor.shape[2:], detections[:, :4], bgr_frame.shape
        ).round()
        output: list[Detection] = []
        for x1, y1, x2, y2, confidence, class_id in detections.detach().cpu().tolist():
            class_id = int(class_id)
            if class_id not in range(len(self.class_names)):
                continue
            class_name = self.class_names[class_id]
            threshold = float(thresholds[class_name])
            if confidence >= threshold:
                output.append(
                    Detection(
                        class_id=class_id,
                        class_name=class_name,
                        confidence=float(confidence),
                        xyxy=(int(x1), int(y1), int(x2), int(y2)),
                        threshold=threshold,
                    )
                )
        return output

    @staticmethod
    def annotate(bgr_frame, detections: list[Detection]):
        import cv2

        image = bgr_frame.copy()
        for detection in detections:
            x1, y1, x2, y2 = detection.xyxy
            cv2.rectangle(image, (x1, y1), (x2, y2), (49, 82, 255), 2)
            label = f"{detection.class_name} {detection.confidence:.2f}"
            cv2.putText(
                image,
                label,
                (x1, max(18, y1 - 7)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (49, 82, 255),
                2,
                cv2.LINE_AA,
            )
        return image


def image_extensions() -> set[str]:
    return {".jpg", ".jpeg", ".png", ".bmp", ".JPG", ".JPEG", ".PNG", ".BMP"}


def process_image(runner: Yolov9WeaponRunner, source: Path, output: Path, thresholds: dict[str, float], args) -> None:
    import cv2

    frame = cv2.imread(str(source))
    if frame is None:
        raise RuntimeError(f"Could not read image: {source}")
    detections = runner.infer(
        frame,
        thresholds,
        candidate_floor=args.candidate_floor,
        iou_threshold=args.nms_iou,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), runner.annotate(frame, detections)):
        raise RuntimeError(f"Could not write output image: {output}")
    print(json.dumps({"source": str(source), "detections": [asdict(item) for item in detections]}, indent=2))


def process_video(runner: Yolov9WeaponRunner, source: Path, output: Path, thresholds: dict[str, float], args) -> None:
    import cv2

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {source}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(output),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError(f"Could not create output video: {output}")

    frame_index = 0
    detections_count = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            detections = runner.infer(
                frame,
                thresholds,
                candidate_floor=args.candidate_floor,
                iou_threshold=args.nms_iou,
            )
            detections_count += bool(detections)
            writer.write(runner.annotate(frame, detections))
            frame_index += 1
            if args.max_frames and frame_index >= args.max_frames:
                break
    finally:
        capture.release()
        writer.release()
    print(json.dumps({"source": str(source), "frames": frame_index, "frames_with_detections": detections_count, "output": str(output)}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--yolov9-root", required=True)
    parser.add_argument("--source", required=True, help="Image, video or directory of images.")
    parser.add_argument("--output", required=True, help="Output image, video or directory.")
    parser.add_argument("--threshold-file", help="Validation-calibrated threshold JSON.")
    parser.add_argument("--threshold-mode", choices=("sensitive", "moderate", "certain"), default="moderate")
    parser.add_argument("--candidate-floor", type=float, default=0.001)
    parser.add_argument("--nms-iou", type=float, default=0.45)
    parser.add_argument("--max-frames", type=int, default=0, help="0 means process the entire video.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    import cv2  # Import here so --help works without OpenCV installed.

    source = Path(args.source).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    threshold_file = Path(args.threshold_file).expanduser() if args.threshold_file else None
    runner = Yolov9WeaponRunner(Path(args.checkpoint), Path(args.yolov9_root))
    runner.load()
    thresholds = load_thresholds(
        threshold_file.resolve() if threshold_file else None,
        args.threshold_mode,
        runner.class_names,
    )

    if source.is_dir():
        output.mkdir(parents=True, exist_ok=True)
        for image_path in sorted(source.iterdir()):
            if image_path.suffix in image_extensions():
                process_image(runner, image_path, output / image_path.name, thresholds, args)
        return

    if source.suffix.lower() in image_extensions():
        process_image(runner, source, output, thresholds, args)
    else:
        process_video(runner, source, output, thresholds, args)


if __name__ == "__main__":
    main()
