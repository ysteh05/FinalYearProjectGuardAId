from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from threading import Lock

from ..model_metadata import WEAPON_MODEL_CLASS_NAMES, WEAPON_MODEL_VERSION


@dataclass(frozen=True, slots=True)
class WeaponDetection:
    class_id: int
    class_name: str
    confidence: float
    xyxy: tuple[int, int, int, int]
    threshold: float


class Yolov9WeaponRunner:
    MODEL_VERSION = WEAPON_MODEL_VERSION
    EXPECTED_CLASSES = WEAPON_MODEL_CLASS_NAMES

    def __init__(self, checkpoint: Path, yolov9_root: Path) -> None:
        self.checkpoint = checkpoint.resolve()
        self.yolov9_root = yolov9_root.resolve()
        self._lock = Lock()
        self._model = None
        self._torch = None
        self._device = None
        self._nms = None
        self._letterbox = None
        self._scale_boxes = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @staticmethod
    def _candidate_confidence_floor(
        candidate_floor: float, thresholds: dict[str, float]
    ) -> float:
        if not thresholds:
            raise ValueError("At least one weapon class threshold is required.")
        return max(0.0, min(candidate_floor, min(thresholds.values())))

    def load(self) -> None:
        if self.is_loaded:
            return
        with self._lock:
            if self.is_loaded:
                return
            if not self.checkpoint.is_file():
                raise FileNotFoundError(f"YOLOv9-T checkpoint not found: {self.checkpoint}")
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
                    "YOLOv9-T requires torch, torchvision, OpenCV, NumPy and the bundled YOLOv9 runtime."
                ) from exc

            device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
            # This is a trusted checkpoint produced by this project. The bundled
            # loader explicitly requests weights_only=False because the archive
            # contains the YOLO module object and EMA state.
            model = attempt_load(str(self.checkpoint), device=device, inplace=True, fuse=True)
            model = model.eval()
            if device.type == "cuda":
                model = model.half()
            names_value = model.module.names if hasattr(model, "module") else model.names
            names = tuple(names_value[index] for index in range(len(names_value)))
            if names != self.EXPECTED_CLASSES:
                raise RuntimeError(
                    f"Unexpected weapon class mapping {names}; expected {self.EXPECTED_CLASSES}."
                )
            self._torch = torch
            self._device = device
            self._model = model
            self._nms = non_max_suppression
            self._letterbox = letterbox
            self._scale_boxes = scale_boxes

    def infer(
        self,
        bgr_frame,
        thresholds: dict[str, float],
        *,
        candidate_floor: float = 0.001,
        iou_threshold: float = 0.45,
        include_below_threshold: bool = False,
    ) -> list[WeaponDetection]:
        import numpy as np

        self.load()
        with self._lock:
            image = self._letterbox(bgr_frame, new_shape=(640, 640), stride=32, auto=True)[0]
            image = image[:, :, ::-1].transpose(2, 0, 1)
            image = np.ascontiguousarray(image)
            tensor = self._torch.from_numpy(image).to(self._device)
            tensor = tensor.half() if self._device.type == "cuda" else tensor.float()
            tensor = tensor.unsqueeze(0) / 255.0
            with self._torch.inference_mode():
                raw = self._model(tensor)
            # YOLOv9-T uses DualDDetect. Its inference branch follows the
            # official detect_dual.py contract: output[0][1].
            if (
                isinstance(raw, (list, tuple))
                and raw
                and isinstance(raw[0], (list, tuple))
                and len(raw[0]) > 1
            ):
                raw = raw[0][1]
            # The very low floor only prevents unbounded candidate growth. Actual
            # deployment decisions are applied per class after class-aware NMS.
            detections = self._nms(
                raw,
                conf_thres=self._candidate_confidence_floor(candidate_floor, thresholds),
                iou_thres=iou_threshold,
                max_det=300,
            )[0]
            if not len(detections):
                return []
            detections[:, :4] = self._scale_boxes(
                tensor.shape[2:], detections[:, :4], bgr_frame.shape
            ).round()
            output: list[WeaponDetection] = []
            for x1, y1, x2, y2, confidence, class_id in detections.detach().cpu().tolist():
                class_id = int(class_id)
                class_name = self.EXPECTED_CLASSES[class_id]
                threshold = float(thresholds[class_name])
                if include_below_threshold or confidence >= threshold:
                    output.append(
                        WeaponDetection(
                            class_id=class_id,
                            class_name=class_name,
                            confidence=float(confidence),
                            xyxy=(int(x1), int(y1), int(x2), int(y2)),
                            threshold=threshold,
                        )
                    )
            return output

    @staticmethod
    def annotate(bgr_frame, detections: list[WeaponDetection]):
        import cv2

        image = bgr_frame.copy()
        for detection in detections:
            x1, y1, x2, y2 = detection.xyxy
            cv2.rectangle(image, (x1, y1), (x2, y2), (49, 82, 255), 2)
            label = f"{detection.class_name} {detection.confidence:.2f}"
            cv2.putText(
                image, label, (x1, max(18, y1 - 7)), cv2.FONT_HERSHEY_SIMPLEX,
                0.52, (49, 82, 255), 2, cv2.LINE_AA,
            )
        return image
