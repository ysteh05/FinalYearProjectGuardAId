import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yolov9_weapon_pipeline as pipeline  # noqa: E402


class FullDatasetV1ConfigTests(unittest.TestCase):
    def _validate(self, **overrides):
        config = pipeline.default_training_config()
        config.update({"dataset_profile": "full_dataset_v1", "class_names": ["knife", "gun"]})
        config.update(overrides)
        return pipeline._validated_config(config)

    def test_gpu_cpu_and_numeric_gpu_index_are_valid_devices(self):
        for device, expected in (("0", "0"), (0, "0"), ("01", "1"), ("cpu", "cpu"), (" CPU ", "cpu")):
            with self.subTest(device=device):
                self.assertEqual(self._validate(device=device)["device"], expected)

    def test_invalid_devices_fail_with_clear_value_error(self):
        for device in ("gpu", "cuda:0", "-1", "", None, True, 0.5, []):
            with self.subTest(device=device):
                with self.assertRaisesRegex(ValueError, "device must be"):
                    self._validate(device=device)

    def test_invalid_numeric_settings_fail_cleanly(self):
        cases = (
            ({"batch_size": "sixteen"}, "batch_size must be an integer"),
            ({"imgsz": 640.5}, "imgsz must be an integer"),
            ({"lr0": float("nan")}, "lr0 must be between"),
            ({"optimizer": ["AdamW"]}, "optimizer must be"),
        )
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, message):
                    self._validate(**overrides)

    def test_eight_gb_gpu_attempts_the_requested_batch_before_fallback(self):
        candidates = pipeline._batch_size_candidates(16, 8 * 1024**3, 7 * 1024**3)
        self.assertEqual(candidates, [16, 8, 4, 2, 1])

    def test_low_free_vram_reduces_the_probe_batch(self):
        candidates = pipeline._batch_size_candidates(16, 8 * 1024**3, 5 * 1024**3)
        self.assertEqual(candidates, [4, 2, 1])

    def test_cuda_out_of_memory_detection_covers_pytorch_runtime_error(self):
        self.assertTrue(pipeline._is_cuda_oom_text("torch.AcceleratorError: CUDA error: out of memory"))
        self.assertTrue(pipeline._is_cuda_oom_text("CUDNN_STATUS_ALLOC_FAILED"))
        self.assertFalse(pipeline._is_cuda_oom_text("invalid device ordinal"))

    def test_dataloader_host_memory_failure_reduces_workers_before_batch(self):
        failure = (
            "cv2.error: Caught error in DataLoader worker process 0. "
            "OpenCV error: Insufficient memory; Failed to allocate 7378560 bytes"
        )
        self.assertTrue(pipeline._is_dataloader_memory_error(failure))
        self.assertEqual(pipeline._next_dataloader_worker_retry(4, failure, "", 0), 2)
        self.assertEqual(pipeline._next_dataloader_worker_retry(2, failure, "", 1), 1)
        self.assertEqual(pipeline._next_dataloader_worker_retry(1, failure, "", 2), 0)
        self.assertIsNone(pipeline._next_dataloader_worker_retry(0, failure, "", 3))
        self.assertIsNone(pipeline._next_dataloader_worker_retry(4, failure, "stop", 0))
        self.assertIsNone(pipeline._next_dataloader_worker_retry(4, "CUDA out of memory", "", 0))

    def test_oom_retry_halves_batch_until_one_and_respects_stop(self):
        self.assertEqual(pipeline._next_oom_retry_batch(8, "CUDA out of memory", "", 0), 4)
        self.assertEqual(pipeline._next_oom_retry_batch(4, "CUDA out of memory", "", 1), 2)
        self.assertIsNone(pipeline._next_oom_retry_batch(1, "CUDA out of memory", "", 2))
        self.assertIsNone(pipeline._next_oom_retry_batch(8, "CUDA out of memory", "stop", 0))
        self.assertIsNone(pipeline._next_oom_retry_batch(8, "CUDA error", "", 0))

    def test_retry_checkpoint_changes_only_the_retry_copy_loader_settings(self):
        import tempfile
        from argparse import Namespace
        import torch

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "last.pt"
            retry_dict = Path(temporary) / "retry_dict.pt"
            retry_namespace = Path(temporary) / "retry_namespace.pt"
            torch.save({"opt": {"batch_size": 16, "workers": 4, "epochs": 5, "patience": 0}, "epoch": 2}, source)
            pipeline._checkpoint_for_batch_retry(source, retry_dict, 8, workers=2, epochs=5, patience=0)
            torch.save({"opt": Namespace(batch_size=16, workers=4, epochs=5, patience=0), "epoch": 2}, source)
            pipeline._checkpoint_for_batch_retry(source, retry_namespace, 8, workers=2, epochs=5, patience=0)
            original = torch.load(source, map_location="cpu", weights_only=False)
            adjusted_dict = torch.load(retry_dict, map_location="cpu", weights_only=False)
            adjusted_namespace = torch.load(retry_namespace, map_location="cpu", weights_only=False)
            self.assertEqual(original["opt"].batch_size, 16)
            self.assertEqual(original["opt"].workers, 4)
            self.assertEqual(adjusted_dict["opt"]["batch_size"], 8)
            self.assertEqual(adjusted_dict["opt"]["workers"], 2)
            self.assertEqual(adjusted_dict["opt"]["epochs"], 5)
            self.assertEqual(adjusted_dict["opt"]["patience"], 0)
            self.assertEqual(adjusted_namespace["opt"].batch_size, 8)
            self.assertEqual(adjusted_namespace["opt"].workers, 2)

    def test_final_evaluation_captures_predictions_without_long_path_txt_exports(self):
        with tempfile.TemporaryDirectory(dir=pipeline.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            yolo_root = root / "yolo"
            yolo_root.mkdir()
            (yolo_root / "val_dual.py").write_text("# fixture", encoding="utf-8")
            run_dir = root / "run"
            run_dir.mkdir()
            state_path = run_dir / "progress.json"
            events_path = run_dir / "events.jsonl"
            control_path = run_dir / "control.json"
            config_path = run_dir / "runtime_config.json"
            config_path.write_text(json.dumps({"yolov9_root": str(yolo_root), "batch_size": 8}), encoding="utf-8")
            expected_image = str(root / "validation_image.jpg")
            command_seen = []

            def fake_run_child(command, _cwd, _environment, _log_path, _state_path, _events_path):
                command_seen.extend(command)
                (run_dir / "validation_predictions.jsonl").write_text(
                    json.dumps({"image": expected_image, "predictions": [], "targets": []}) + "\n",
                    encoding="utf-8",
                )
                return 0

            with patch.object(pipeline, "_run_child", side_effect=fake_run_child):
                pipeline._evaluation_command(
                    {
                        "yolov9_root": str(yolo_root),
                        "batch_size": 8,
                        "imgsz": 640,
                        "device": "cpu",
                        "workers": 0,
                        "iou_threshold": 0.7,
                        "class_names": ["knife", "gun"],
                    },
                    {"data_yaml": str(root / "data.yaml"), "images": {"val": 1}},
                    run_dir,
                    root / "best.pt",
                    state_path,
                    events_path,
                    control_path,
                    config_path,
                    split="val",
                    capture=True,
                )

            self.assertNotIn("--save-txt", command_seen)
            with (run_dir / "validation_predictions.jsonl").open(encoding="utf-8") as stream:
                self.assertEqual(json.loads(stream.readline())["image"], expected_image)


if __name__ == "__main__":
    unittest.main()
