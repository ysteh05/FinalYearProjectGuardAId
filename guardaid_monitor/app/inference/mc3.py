from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Sequence

from ..model_metadata import VIOLENCE_MODEL_VERSION


class Mc3ViolenceRunner:
    """MC3-18 inference using the exact validation preprocessing protocol."""

    MODEL_VERSION = VIOLENCE_MODEL_VERSION
    MEAN = (0.43216, 0.394666, 0.37645)
    STD = (0.22803, 0.22145, 0.216989)

    def __init__(self, checkpoint: Path) -> None:
        self.checkpoint = checkpoint.resolve()
        self._lock = Lock()
        self._model = None
        self._torch = None
        self._device = None

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
            model = model.to(device).eval()
            if device.type == "cuda":
                model = model.half()
            self._torch = torch
            self._device = device
            self._model = model

    @staticmethod
    def _resize_short_side(frame, short_side: int = 128):
        import cv2

        height, width = frame.shape[:2]
        if height <= width:
            new_height, new_width = short_side, int(round(width * short_side / height))
        else:
            new_width, new_height = short_side, int(round(height * short_side / width))
        return cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_LINEAR)

    def _preprocess(self, bgr_frames: Sequence):
        import cv2
        import numpy as np

        if not bgr_frames:
            raise ValueError("MC3-18 received an empty frame sequence")
        rgb = [cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) for frame in bgr_frames]
        resized = [self._resize_short_side(frame) for frame in rgb]
        height, width = resized[0].shape[:2]
        top, left = (height - 112) // 2, (width - 112) // 2
        clip = np.stack([frame[top : top + 112, left : left + 112] for frame in resized])
        clip = clip.astype(np.float32) / 255.0
        clip = (clip - np.asarray(self.MEAN, dtype=np.float32)) / np.asarray(
            self.STD, dtype=np.float32
        )
        clip = np.transpose(clip, (3, 0, 1, 2))
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
