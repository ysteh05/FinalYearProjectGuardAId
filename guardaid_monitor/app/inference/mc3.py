from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Sequence
from collections import OrderedDict
import weakref

from ..model_metadata import VIOLENCE_MODEL_VERSION


class Mc3ViolenceRunner:
    """MC3-18 inference using the exact validation preprocessing protocol."""

    MODEL_VERSION = VIOLENCE_MODEL_VERSION
    MEAN = (0.43216, 0.394666, 0.37645)
    STD = (0.22803, 0.22145, 0.216989)
    INPUT_SIZE = 160

    def __init__(self, checkpoint: Path) -> None:
        self.checkpoint = checkpoint.resolve()
        self._lock = Lock()
        self._model = None
        self._torch = None
        self._device = None
        self._prepared = OrderedDict()
        self._cache_lock = Lock()
        self._warmed = False

    def warmup(self):
        if self._warmed or not self.is_loaded:
            return
        import numpy as np
        self.infer([np.zeros((160, 160, 3), dtype=np.uint8)] * 16)
        if self._device.type == 'cuda':
            self._torch.cuda.synchronize(self._device)
        self._warmed = True

    def prepare_frame(self, bgr_frame):
        import cv2
        import numpy as np
        with self._cache_lock:
            key = id(bgr_frame)
            cached = self._prepared.get(key)
            if cached is not None and cached[0]() is bgr_frame:
                self._prepared.move_to_end(key)
                return cached[1], cached[2]
            rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
            resized, geometry = self._letterbox_rgb(rgb, np=np, cv2=cv2)
            image = resized.astype(np.float32) / 255.0
            image = (image - 0.5) * 1.0 + 0.5 + 0.0
            image = np.clip(image, 0.0, 1.0)
            mean = np.asarray(self.MEAN, np.float32).reshape(1, 1, 3)
            std = np.asarray(self.STD, np.float32).reshape(1, 1, 3)
            top, left, height, width = geometry
            canvas = np.broadcast_to(mean, (self.INPUT_SIZE, self.INPUT_SIZE, 3)).copy()
            canvas[top:top+height, left:left+width] = image
            normalized = np.ascontiguousarray((canvas-mean)/std, dtype=np.float32)
            self._prepared[key] = (weakref.ref(bgr_frame), normalized, geometry)
            while len(self._prepared) > 256:
                self._prepared.popitem(last=False)
            return normalized, geometry

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self.is_loaded:
            return
        with self._lock:
            if self.is_loaded:
                return
            if not self.checkpoint.is_file():
                raise FileNotFoundError(f"MC3-18 checkpoint not found: {self.checkpoint}")
            try:
                import torch
                from torchvision.models.video import mc3_18
            except ImportError as exc:
                raise RuntimeError("MC3-18 requires torch and torchvision.") from exc

            model = mc3_18(weights=None)
            model.fc = torch.nn.Linear(model.fc.in_features, 2)
            checkpoint = torch.load(self.checkpoint, map_location="cpu", weights_only=True)
            state_dict = checkpoint.get("state_dict", checkpoint)
            class_to_idx = checkpoint.get("class_to_idx") if isinstance(checkpoint, dict) else None
            if class_to_idx is not None and class_to_idx != {"NonFight": 0, "Fight": 1}:
                raise RuntimeError(
                    "MC3-18 checkpoint class mapping must be NonFight=0, Fight=1; "
                    f"received {class_to_idx!r}"
                )
            model.load_state_dict(state_dict, strict=True)
            device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
            if device.type == 'cuda':
                torch.set_num_threads(2)
            model = model.to(device).eval()
            if device.type == "cuda":
                model = model.half()
            self._torch = torch
            self._device = device
            self._model = model

    @classmethod
    def _letterbox_rgb(cls, frame, *, np, cv2):
        """Match Epoch 8 training: full-frame RGB resize and mean-filled square."""
        height, width = frame.shape[:2]
        if height <= 0 or width <= 0:
            raise ValueError("MC3-18 received a frame with an empty dimension")
        scale = min(cls.INPUT_SIZE / width, cls.INPUT_SIZE / height)
        resized_width = max(1, min(cls.INPUT_SIZE, int(round(width * scale))))
        resized_height = max(1, min(cls.INPUT_SIZE, int(round(height * scale))))
        interpolation = (
            cv2.INTER_AREA
            if resized_width < width or resized_height < height
            else cv2.INTER_LINEAR
        )
        resized = cv2.resize(frame, (resized_width, resized_height), interpolation=interpolation)
        left = (cls.INPUT_SIZE - resized_width) // 2
        top = (cls.INPUT_SIZE - resized_height) // 2
        return resized, (top, left, resized_height, resized_width)

    def _preprocess(self, bgr_frames: Sequence):
        import cv2
        import numpy as np

        if not bgr_frames:
            raise ValueError("MC3-18 received an empty frame sequence")
        mean = np.asarray(self.MEAN, dtype=np.float32).reshape(1, 1, 3)
        std = np.asarray(self.STD, dtype=np.float32).reshape(1, 1, 3)
        processed = []
        expected_geometry = None
        for bgr_frame in bgr_frames:
            normalized, geometry = self.prepare_frame(bgr_frame)
            if expected_geometry is None:
                expected_geometry = geometry
            elif geometry != expected_geometry:
                raise ValueError(
                    "Video frame dimensions changed within an MC3-18 window; "
                    "refusing inconsistent letterbox geometry."
                )
            processed.append(np.ascontiguousarray(normalized, dtype=np.float32))
        clip = np.stack(processed).transpose(3, 0, 1, 2)
        return self._torch.from_numpy(clip).unsqueeze(0)

    def infer(self, bgr_frames: Sequence) -> float:
        self.load()
        with self._lock:
            tensor = self._preprocess(bgr_frames).to(self._device)
            if self._device.type == "cuda":
                tensor = tensor.half()
            with self._torch.inference_mode():
                logits = self._model(tensor)
                probability = self._torch.softmax(logits.float(), dim=1)[0, 1].item()
            return float(probability)
