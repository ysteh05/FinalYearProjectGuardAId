import json
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mc3_18_dashboard as dashboard  # noqa: E402


class AirtlabInferenceTests(unittest.TestCase):
    def test_nested_model_discovery_exposes_both_modalities_and_thresholds(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            experiment_run = project_root / "run_20261013_0015"
            experiment_run.mkdir()
            (experiment_run / "config.json").write_text(json.dumps({
                "experiment_protocol": dashboard.AIRTLAB_MODALITIES_PROTOCOL,
                "window_seconds": 4,
                "stride_seconds": 2,
                "frames_per_window": 16,
            }), encoding="utf-8")
            for folder, representation, epoch in (
                ("native_video", "native_video", 18),
                ("black_background_skeleton", "black_background_skeleton", 9),
            ):
                arm = experiment_run / folder
                arm.mkdir()
                (arm / "best_model.pt").touch()
                (arm / "final_report.json").write_text(json.dumps({
                    "modality": "native" if representation == "native_video" else "skeleton",
                    "input_representation": representation,
                    "best_epoch": epoch,
                    "validation_best_f1_threshold": {"threshold": 0.61, "validation_f1": 0.9},
                }), encoding="utf-8")

            direct = dashboard._list_runs(project_root)
            nested = dashboard._list_runs(project_root, include_nested=True)

        self.assertEqual(direct, [])
        self.assertEqual(len(nested), 2)
        self.assertEqual({item["input_representation"] for item in nested}, {
            "native_video", "black_background_skeleton",
        })
        self.assertTrue(all(item["can_infer"] for item in nested))
        self.assertTrue(all(item["threshold_modes"] == ["fixed_0_5", "validation_best_f1"] for item in nested))

    def test_skeleton_inference_extracts_protocol_frames_before_mc3(self):
        try:
            import cv2
            import numpy as np
            import torch
        except ImportError as exc:  # pragma: no cover - exercised on minimal test installs
            self.skipTest(f"video inference dependencies are unavailable: {exc}")

        import mc3_18_mil_pipeline as core
        from mc3_18_four_second_pipeline import make_four_second_windows

        class Capture:
            def __init__(self):
                self.index = 0
                self.frame_count = 30

            def isOpened(self):
                return True

            def get(self, property_id):
                if property_id == cv2.CAP_PROP_FPS:
                    return 25.0
                if property_id == cv2.CAP_PROP_FRAME_COUNT:
                    return self.frame_count
                return 0.0

            def read(self):
                if self.index >= self.frame_count:
                    return False, None
                self.index += 1
                return True, np.zeros((128, 128, 3), dtype=np.uint8)

            def release(self):
                pass

        class PoseEngine:
            provider = "CPUExecutionProvider"

            def detect(self, _frame):
                return np.asarray([[5, 5, 120, 120]], dtype=np.float32), np.asarray([0.9], dtype=np.float32)

            def estimate(self, _frame, _boxes):
                return np.zeros((1, 17, 2), dtype=np.float32), np.ones((1, 17), dtype=np.float32)

        class DummyModel:
            def load_state_dict(self, _state):
                return None

            def eval(self):
                return self

            def __call__(self, batch):
                return torch.stack((torch.zeros(batch.shape[0]), torch.ones(batch.shape[0])), dim=1)

        protocol = {
            "experiment_protocol": dashboard.AIRTLAB_MODALITIES_PROTOCOL,
            "input_modality": "skeleton",
            "window_seconds": 4.0,
            "stride_seconds": 2.0,
            "frames_per_window": 16,
            "short_side": 128,
            "crop_size": 112,
            "color_space": "RGB",
            "normalization_mean": [0.43216, 0.394666, 0.37645],
            "normalization_std": [0.22803, 0.22145, 0.216989],
        }
        capture = Capture()
        progress = []
        fake_pose_pipeline = SimpleNamespace(
            draw_skeleton_frame=lambda width, height, _points, _scores: np.zeros((height, width, 3), dtype=np.uint8)
        )

        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / "short.mp4"
            video_path.write_bytes(b"synthetic video")
            with (
                mock.patch.object(cv2, "VideoCapture", return_value=capture),
                mock.patch.object(dashboard, "_get_pose_engine", return_value=(fake_pose_pipeline, PoseEngine())),
                mock.patch.object(core, "_build_model", return_value=DummyModel()),
                mock.patch.object(core, "_amp_context", side_effect=lambda *_args: nullcontext()),
                mock.patch.object(torch.cuda, "is_available", return_value=False),
            ):
                result = dashboard._predict_video_airtlab_skeleton_checkpoint(
                    video_path,
                    video_path.name,
                    video_path,
                    "validation_best_f1",
                    0.61,
                    lambda **values: progress.append(values),
                    {"protocol": protocol, "state_dict": {}},
                    cv2,
                    np,
                    torch,
                    torch.nn,
                    (None, None),
                )

        expected_unique_frames = len(set(
            index
            for window in make_four_second_windows(1.2, 25.0, 30)
            for index in window.frame_indices
        ))
        self.assertEqual(result["input_representation"], "black_background_skeleton")
        self.assertEqual(result["window_count"], 1)
        self.assertEqual(result["skeleton_frames_extracted"], expected_unique_frames)
        self.assertEqual(result["skeleton_frames_with_people"], expected_unique_frames)
        self.assertEqual(result["prediction"], "Fight")
        self.assertTrue(any(item.get("stage") == "提取黑底骨架" for item in progress))


if __name__ == "__main__":
    unittest.main()
