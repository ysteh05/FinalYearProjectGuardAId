import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mc3_18_dashboard as dashboard  # noqa: E402


class FourSecondDashboardTests(unittest.TestCase):
    def make_layout(self, root: Path) -> dict[str, Path]:
        legacy = root / "legacy"
        for dataset in (
            legacy / "RWF-2000",
            legacy / "archive" / "Real Life Violence Dataset",
            legacy / "ShanghaiTech",
        ):
            for split in ("train", "val", "test"):
                for class_name in ("Fight", "NonFight"):
                    class_dir = dataset / split / class_name
                    class_dir.mkdir(parents=True)
                    (class_dir / "sample.mp4").touch()

        ucf = root / "UCF"
        (ucf / "Violence" / "Assault").mkdir(parents=True)
        (ucf / "Non-Violence").mkdir(parents=True)
        (ucf / "Violence" / "Assault" / "fight.mp4").touch()
        (ucf / "Non-Violence" / "normal.mp4").touch()

        airtlab = root / "AIRTLab"
        for class_name in ("violent", "non-violent"):
            class_dir = airtlab / class_name
            class_dir.mkdir(parents=True)
            (class_dir / f"{class_name}.mp4").touch()

        guardaid = root / "GuardAId"
        guardaid.mkdir()
        (guardaid / "unlabeled.mp4").touch()
        old_cache = root / "old-two-second-cache"
        old_cache.mkdir()
        return {"legacy": legacy, "ucf": ucf, "airtlab": airtlab, "guardaid": guardaid, "old_cache": old_cache}

    def test_airtlab_split_preview_keeps_matching_camera_names_together(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for class_dir in (root / "violent", root / "non-violent"):
                for camera in ("cam1", "cam2"):
                    (class_dir / camera).mkdir(parents=True)
                    for index in range(20):
                        (class_dir / camera / f"event-{index:02d}.mp4").touch()
            preview = dashboard._four_second_airtlab_split_preview(root)

        self.assertEqual(preview["source_groups"], 40)
        self.assertEqual(preview["paired_filename_groups"], 40)
        self.assertEqual(preview["split_preview"]["train"]["Fight"], 32)
        self.assertEqual(preview["split_preview"]["val"]["Fight"], 4)
        self.assertEqual(preview["split_preview"]["test"]["Fight"], 4)
        self.assertEqual(preview["split_preview"]["train"]["NonFight"], 32)
        self.assertEqual(preview["split_preview"]["val"]["NonFight"], 4)
        self.assertEqual(preview["split_preview"]["test"]["NonFight"], 4)

    def test_training_arguments_use_separate_cache_and_conservative_batch_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.make_layout(root)
            output = root / "four-output"
            supplement = output / "_supplemental_cache"
            payload = {
                "legacy_dataset_root": str(paths["legacy"]),
                "ucf_root": str(paths["ucf"]),
                "airtlab_root": str(paths["airtlab"]),
                "guardaid_root": str(paths["guardaid"]),
                "output_root": str(output),
                "cache_dir": str(supplement),
            }
            with mock.patch.object(dashboard, "DEFAULT_TWO_SECOND_FRAME_CACHE", paths["old_cache"]):
                args, run_dir, roots = dashboard._four_second_training_arguments(payload, output, root / "pause")
            self.assertEqual(run_dir.parent, output)
            self.assertEqual(roots["legacy_cache_root"], str(paths["old_cache"]))
            self.assertEqual(roots["supplemental_cache_root"], str(supplement.resolve()))
            self.assertEqual(args[args.index("--window-batch-size") + 1], "2")
            self.assertEqual(args[args.index("--legacy-cache-dir") + 1], str(paths["old_cache"]))
            self.assertEqual(args[args.index("--cache-dir") + 1], str(supplement.resolve()))

    def test_training_arguments_reject_cache_path_inside_shared_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.make_layout(root)
            output = root / "four-output"
            payload = {
                "legacy_dataset_root": str(paths["legacy"]),
                "ucf_root": str(paths["ucf"]),
                "airtlab_root": str(paths["airtlab"]),
                "guardaid_root": str(paths["guardaid"]),
                "output_root": str(output),
                "cache_dir": str(paths["old_cache"] / "supplement"),
            }
            with mock.patch.object(dashboard, "DEFAULT_TWO_SECOND_FRAME_CACHE", paths["old_cache"]):
                with self.assertRaises(ValueError):
                    dashboard._four_second_training_arguments(payload, output, root / "pause")

    def test_video_inference_keeps_frames_for_off_grid_short_tail(self):
        try:
            import cv2
            import numpy as np
            import torch
        except ImportError as exc:  # pragma: no cover - exercised on minimal test installs
            self.skipTest(f"video inference dependencies are unavailable: {exc}")

        import mc3_18_mil_pipeline as core

        class Capture:
            def __init__(self):
                self.index = 0
                self.frame_count = 597  # 19.9 s at 30 fps; tail starts at 15.9 s.

            def isOpened(self):
                return True

            def get(self, property_id):
                if property_id == cv2.CAP_PROP_FPS:
                    return 30.0
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

        class DummyModel:
            def load_state_dict(self, _state):
                return None

            def eval(self):
                return self

            def __call__(self, batch):
                return torch.stack(
                    (torch.zeros(batch.shape[0]), torch.ones(batch.shape[0])), dim=1
                )

        protocol = {
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
        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / "off-grid-tail.mp4"
            video_path.write_bytes(b"synthetic video placeholder")
            with (
                mock.patch.object(cv2, "VideoCapture", return_value=capture),
                mock.patch.object(core, "_build_model", return_value=DummyModel()),
                mock.patch.object(core, "_amp_context", side_effect=lambda *_args: nullcontext()),
                mock.patch.object(torch.cuda, "is_available", return_value=False),
            ):
                result = dashboard._predict_video_four_second_checkpoint(
                    video_path,
                    video_path.name,
                    video_path,
                    "moderate",
                    0.5,
                    lambda **_values: None,
                    {"protocol": protocol, "state_dict": {}},
                    cv2,
                    np,
                    torch,
                    torch.nn,
                    (None, None),
                )

        self.assertEqual(result["window_count"], 9)
        self.assertEqual(len(result["windows"]), 9)
        self.assertGreaterEqual(capture.index, 594)


if __name__ == "__main__":
    unittest.main()
