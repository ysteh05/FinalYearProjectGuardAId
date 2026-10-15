import sys
import tempfile
import unittest
import random
import logging
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mc3_18_mil_pipeline import (  # noqa: E402
    VideoRecord,
    _record_bag_tensor,
    _augment_params,
    _count_split_samples,
    _scan_datasets,
    _preprocessing_protocol,
    _validate_checkpoint_config,
    CHECKPOINT_VERSION,
    binary_metrics,
    choose_thresholds,
    discover_dataset_roots,
    make_windows,
    roc_auc,
    stable_seed,
)
import mc3_18_mil_pipeline as mil_pipeline  # noqa: E402

_MIL_DEFAULT_CACHE_LOADER = mil_pipeline._load_cache

try:
    import numpy as np  # noqa: E402
except ImportError:
    np = None


class WindowProtocolTests(unittest.TestCase):
    def test_exact_and_short_boundaries(self):
        self.assertEqual(len(make_windows(0.99, 30, 30)), 0)
        self.assertEqual(len(make_windows(0.9999995, 30, 30)), 0)
        self.assertEqual(len(make_windows(1.0, 30, 30)), 1)
        self.assertEqual(len(make_windows(1.06, 30, 32)), 1)

    def test_training_only_tail_is_appended_once(self):
        self.assertEqual(len(make_windows(1.06, 30, 32, include_training_tail=False)), 1)
        train = make_windows(1.06, 30, 32, include_training_tail=True)
        self.assertEqual(len(train), 2)
        self.assertAlmostEqual(train[-1].start, 0.06)
        self.assertAlmostEqual(train[-1].end, 1.06)

    def test_16_and_40_second_examples(self):
        self.assertEqual(len(make_windows(1.6, 30, 48)), 2)
        windows = make_windows(40.1, 30, 1203)
        self.assertEqual(len(windows), 79)
        self.assertAlmostEqual(windows[-1].start, 39.0)
        self.assertAlmostEqual(windows[-1].end, 40.0)

    def test_four_frame_sampling_uses_bin_centres(self):
        windows = make_windows(1.0, 4.0, 4)
        self.assertEqual(windows[0].frame_indices, (0, 1, 2, 3))


class LoggingLifecycleTests(unittest.TestCase):
    def test_replacing_run_logger_closes_previous_file_handler(self):
        logger = logging.getLogger("mc3_18_mil")
        with tempfile.TemporaryDirectory() as directory:
            try:
                mil_pipeline._log_setup(Path(directory) / "first")
                previous_file_handler = next(
                    handler for handler in logger.handlers if isinstance(handler, logging.FileHandler)
                )
                mil_pipeline._log_setup(Path(directory) / "second")
                self.assertIsNone(previous_file_handler.stream)
                self.assertNotIn(previous_file_handler, logger.handlers)
                self.assertTrue((Path(directory) / "first" / "run.log").is_file())
            finally:
                for handler in tuple(logger.handlers):
                    logger.removeHandler(handler)
                    handler.close()


class MetricAndCalibrationTests(unittest.TestCase):
    def test_auc_ties_are_half_credit(self):
        self.assertEqual(roc_auc([1, 0], [0.5, 0.5]), 0.5)

    def test_video_level_binary_metrics(self):
        result = binary_metrics([1, 1, 0, 0], [0.9, 0.4, 0.6, 0.1], 0.5)
        self.assertEqual((result["tp"], result["tn"], result["fp"], result["fn"]), (1, 1, 1, 1))
        self.assertAlmostEqual(result["accuracy"], 0.5)

    def test_calibration_returns_ordered_modes(self):
        thresholds = choose_thresholds(
            [0, 0, 0, 1, 1, 1],
            [0.05, 0.20, 0.35, 0.30, 0.60, 0.95],
        )
        sensitive = thresholds["sensitive"]["threshold"]
        moderate = thresholds["moderate"]["threshold"]
        certain = thresholds["certain"]["threshold"]
        self.assertLessEqual(sensitive, moderate)
        self.assertLessEqual(moderate, certain)


class ResumeValidationTests(unittest.TestCase):
    def test_resume_requires_same_data_and_training_protocol(self):
        config = {
            "seed": 42,
            "epochs": 30,
            "backbone_lr": 2e-5,
            "head_lr": 1e-4,
            "weight_decay": 1e-4,
            "grad_accum": 8,
            "window_batch_size": 8,
            "patience": 6,
            "min_delta": 0.001,
            "amp": True,
            "device": "cuda:0",
            "protocol": "mc3-1s-4f-128short-112crop-v1",
            "preprocessing": _preprocessing_protocol(),
        }
        checkpoint = {
            "checkpoint_version": CHECKPOINT_VERSION,
            "dataset_fingerprint": "dataset-hash",
            "config": config.copy(),
        }
        _validate_checkpoint_config(checkpoint, "dataset-hash", config)
        with self.assertRaisesRegex(RuntimeError, "Dataset files changed"):
            _validate_checkpoint_config(checkpoint, "other-dataset-hash", config)
        changed_config = config.copy()
        changed_config["min_delta"] = 0.01
        with self.assertRaisesRegex(RuntimeError, "min_delta"):
            _validate_checkpoint_config(checkpoint, "dataset-hash", changed_config)


class AugmentationTests(unittest.TestCase):
    def test_seeded_photometric_parameters_repeat_per_epoch_and_change_between_epochs(self):
        identity = "dataset|train|Fight|video.mp4"
        first_seed = stable_seed(42, 1, identity)
        repeat_seed = stable_seed(42, 1, identity)
        next_epoch_seed = stable_seed(42, 2, identity)
        self.assertEqual(first_seed, repeat_seed)
        self.assertNotEqual(first_seed, next_epoch_seed)
        first_params = _augment_params(random.Random(first_seed))
        repeat_params = _augment_params(random.Random(repeat_seed))
        next_params = _augment_params(random.Random(next_epoch_seed))
        self.assertEqual(first_params, repeat_params)
        self.assertNotEqual(first_params, next_params)

    @unittest.skipIf(np is None, "numpy is required for image augmentation checks")
    def test_train_view_changes_by_epoch_but_eval_view_is_fixed(self):
        class NumpyTensorOps:
            @staticmethod
            def from_numpy(array):
                return array

            @staticmethod
            def stack(items, dim=0):
                return np.stack(items, axis=dim)

        class NoOpCV2:
            @staticmethod
            def GaussianBlur(image, _kernel, sigmaX=0):
                return image

        with tempfile.TemporaryDirectory() as temporary:
            cache_path = Path(temporary) / "frames.npz"
            rng = np.random.default_rng(2026)
            frame_indices = np.asarray([3, 11, 18, 26], dtype=np.int32)
            frames = rng.integers(0, 256, size=(4, 128, 160, 3), dtype=np.uint8)
            np.savez_compressed(cache_path, frame_indices=frame_indices, frames=frames)
            record = VideoRecord(
                dataset="unit",
                split="train",
                class_name="Fight",
                label=1,
                path=Path(temporary) / "video.mp4",
                fps=30.0,
                frame_count=30,
                duration=1.0,
                size_bytes=1,
                mtime_ns=1,
                cache_path=cache_path,
            )
            # Other MC3 profile tests install their cache reader on the shared
            # module. Keep this legacy augmentation test bound to the MIL cache
            # contract it is intended to exercise.
            with mock.patch.object(mil_pipeline, "_load_cache", _MIL_DEFAULT_CACHE_LOADER):
                train_epoch_one, _ = _record_bag_tensor(
                    record, training=True, epoch=1, seed=42, np=np, cv2=NoOpCV2, torch=NumpyTensorOps
                )
                train_epoch_one_repeat, _ = _record_bag_tensor(
                    record, training=True, epoch=1, seed=42, np=np, cv2=NoOpCV2, torch=NumpyTensorOps
                )
                train_epoch_two, _ = _record_bag_tensor(
                    record, training=True, epoch=2, seed=42, np=np, cv2=NoOpCV2, torch=NumpyTensorOps
                )
                eval_epoch_one, _ = _record_bag_tensor(
                    record, training=False, epoch=1, seed=42, np=np, cv2=NoOpCV2, torch=NumpyTensorOps
                )
                eval_epoch_two, _ = _record_bag_tensor(
                    record, training=False, epoch=2, seed=42, np=np, cv2=NoOpCV2, torch=NumpyTensorOps
                )
        np.testing.assert_array_equal(train_epoch_one, train_epoch_one_repeat)
        self.assertFalse(np.array_equal(train_epoch_one, train_epoch_two))
        np.testing.assert_array_equal(eval_epoch_one, eval_epoch_two)


class DatasetDiscoveryTests(unittest.TestCase):
    def test_only_canonical_real_life_tree_is_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = [
                root / "RWF-2000",
                root / "archive" / "Real Life Violence Dataset",
                root / "bus-violence",
                root / "seymanurakti-fight-detection-surv-dataset-ff83b7c",
                root / "ShanghaiTech",
                root / "archive" / "real life violence situations" / "Real Life Violence Dataset",
            ]
            for path in expected:
                path.mkdir(parents=True)
            roots = discover_dataset_roots(root)
            self.assertEqual(len(roots), 5)
            self.assertEqual(roots[1][1], expected[1])
            self.assertNotIn(expected[5], [path for _, path in roots])

    def _create_complete_layout(self, root):
        dataset_paths = [
            root / "RWF-2000",
            root / "archive" / "Real Life Violence Dataset",
            root / "bus-violence",
            root / "seymanurakti-fight-detection-surv-dataset-ff83b7c",
            root / "ShanghaiTech",
        ]
        for dataset_path in dataset_paths:
            for split in ("train", "val", "test"):
                for class_name in ("Fight", "NonFight"):
                    (dataset_path / split / class_name).mkdir(parents=True, exist_ok=True)
        return dataset_paths

    def test_avi_mp4_scanning_and_subsecond_counts(self):
        class Capture:
            def __init__(self, path):
                self.path = Path(path)

            def isOpened(self):
                return True

            def get(self, property_id):
                if property_id == FakeCV2.CAP_PROP_FPS:
                    return 30.0
                return {"full.avi": 30, "full.mp4": 32, "short.mp4": 15}[self.path.name]

            def release(self):
                pass

        class FakeCV2:
            CAP_PROP_FPS = 1
            CAP_PROP_FRAME_COUNT = 2

            @staticmethod
            def VideoCapture(path):
                return Capture(path)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset_paths = self._create_complete_layout(root)
            fight_dir = dataset_paths[0] / "train" / "Fight"
            for name in ("full.avi", "full.mp4", "short.mp4", "ignored.mov"):
                (fight_dir / name).touch()
            roots = discover_dataset_roots(root)
            records, manifest = _scan_datasets(root, FakeCV2, logging.getLogger("test-mc3-scan"))
            eligible, excluded = _count_split_samples(roots, records, manifest)
        self.assertEqual(len(records), 2)
        self.assertEqual({record.path.suffix.lower() for record in records}, {".avi", ".mp4"})
        self.assertEqual(eligible["RWF-2000/train/Fight"], 2)
        self.assertEqual(excluded["RWF-2000/train/Fight"], 1)
        self.assertEqual(eligible["RWF-2000/train/NonFight"], 0)

    def test_unreadable_video_is_reported_by_path(self):
        class Capture:
            def isOpened(self):
                return False

            def release(self):
                pass

        class FakeCV2:
            @staticmethod
            def VideoCapture(_path):
                return Capture()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset_paths = self._create_complete_layout(root)
            broken = dataset_paths[2] / "test" / "NonFight" / "broken_video.avi"
            broken.touch()
            with self.assertRaisesRegex(RuntimeError, "broken_video\\.avi"):
                _scan_datasets(root, FakeCV2, logging.getLogger("test-mc3-scan-fail"))


if __name__ == "__main__":
    unittest.main()
