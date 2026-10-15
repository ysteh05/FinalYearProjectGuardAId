import csv
import json
import logging
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn as nn  # noqa: E402

import mc3_18_dashboard as dashboard  # noqa: E402
import mc3_18_four_second_pipeline as four  # noqa: E402
import mc3_18_full_frame_pipeline as full_frame  # noqa: E402
import mc3_18_mil_pipeline as core  # noqa: E402
import mc3_18_native_finetune_pipeline as native  # noqa: E402


class TinyClassifier(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc = nn.Linear(3, 2)

    def forward(self, values):
        return self.fc(values.mean(dim=(2, 3, 4)))


class FullFramePreprocessingTests(unittest.TestCase):
    def test_short_side_resize_preserves_landscape_portrait_and_square_geometry(self):
        landscape = full_frame._resize_rgb_short_side(np.zeros((1080, 1920, 3), dtype=np.uint8), 112, cv2)
        portrait = full_frame._resize_rgb_short_side(np.zeros((1920, 1080, 3), dtype=np.uint8), 112, cv2)
        square = full_frame._resize_rgb_short_side(np.zeros((720, 720, 3), dtype=np.uint8), 112, cv2)

        self.assertEqual(landscape.shape[:2], (112, 199))
        self.assertEqual(portrait.shape[:2], (199, 112))
        self.assertEqual(square.shape[:2], (112, 112))

    def test_clip_keeps_edge_content_and_rejects_mid_clip_geometry_change(self):
        image = np.zeros((90, 160, 3), dtype=np.uint8)
        image[:, -24:, :] = 255
        clip = full_frame.frames_to_clip([image] * 16, np=np, torch=torch, cv2=cv2)

        self.assertEqual(tuple(clip.shape), (3, 16, 112, 199))
        self.assertGreater(float(clip[:, :, :, -4:].mean()), float(clip[:, :, :, :4].mean()))
        with self.assertRaisesRegex(ValueError, "aspect/size changed"):
            full_frame.frames_to_clip([image] * 15 + [image[:, :-1]], np=np, torch=torch, cv2=cv2)

    def test_shakefive2_split_is_stable_exact_80_10_10(self):
        paths = [Path(f"shake_{index:03}.mp4") for index in range(153)]
        first = full_frame._random_splits(paths, 2026)
        second = full_frame._random_splits(paths, 2026)
        self.assertEqual(first, second)
        self.assertEqual({split: sum(value == split for value in first.values())
                          for split in full_frame.SPLITS}, {"train": 123, "val": 15, "test": 15})

    def test_live_status_serializes_initial_unavailable_metrics_as_json_null(self):
        with tempfile.TemporaryDirectory(dir=full_frame.PROJECT_ROOT) as directory:
            status_path = Path(directory) / "live_status.json"
            native._write_live(status_path, {"best_val_roc_auc": -math.inf,
                                             "metrics": {"initial": math.nan, "positive_inf": math.inf}})
            saved = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertIsNone(saved["best_val_roc_auc"])
        self.assertIsNone(saved["metrics"]["initial"])
        self.assertIsNone(saved["metrics"]["positive_inf"])


class FullFrameTrainingSmokeTests(unittest.TestCase):
    def _fixture(self, root: Path):
        dataset_root = root / "datasets"
        source_cache = root / "readonly-base-4s-cache"
        source_cache.mkdir()
        records = []
        rows = []
        labels_by_split = {"train": (1, 0), "val": (0, 1), "test": (1, 0)}
        video_datasets = ("RWF-2000", "AIRTLab", "ShakeFive2", "ShanghaiTech")
        for split_index, split in enumerate(full_frame.SPLITS):
            for dataset_index, dataset in enumerate(video_datasets):
                label = labels_by_split[split][0] if dataset == "RWF-2000" else (
                    labels_by_split[split][1] if dataset == "AIRTLab" else 0)
                class_name = "Fight" if label else "NonFight"
                video = dataset_root / dataset / split / f"{dataset_index}_{split}.mp4"
                video.parent.mkdir(parents=True, exist_ok=True)
                video.touch()
                record = native._new_record(dataset, split, class_name, video, 4.0, 16, 4.0,
                                           source_group=f"{dataset}|{split}|{split_index}")
                windows = four.make_four_second_windows(record.duration, record.fps, record.frame_count)
                frame_indices = sorted({value for window in windows for value in window.frame_indices})
                if dataset_index % 3 == 0:
                    height, width = 128, 228
                elif dataset_index % 3 == 1:
                    height, width = 228, 128
                else:
                    height = width = 128
                rgb_value = np.uint8(215 if label else 35)
                frames = np.full((len(frame_indices), height, width, 3), rgb_value, dtype=np.uint8)
                np.savez_compressed(source_cache / four._supplement_cache_filename(record),
                                    frame_indices=np.asarray(frame_indices, dtype=np.int32), frames=frames)
                records.append(record)
                rows.append({"dataset": dataset, "split": split, "class_name": class_name,
                             "label": label, "path": str(video), "source_group": record.source_group,
                             "fps": record.fps, "frame_count": record.frame_count,
                             "duration_seconds": record.duration, "eligible": 1, "window_count": 1})

            sequence = dataset_root / "DanceTrack" / split / f"fixture-{split}"
            frame_dir = sequence / "img1"
            frame_dir.mkdir(parents=True)
            required = sorted({value for window in four.make_four_second_windows(4.0, 4.0, 16)
                               for value in window.frame_indices})
            for index in required:
                frame = np.full((160, 240, 3), 32 + split_index, dtype=np.uint8)
                self.assertTrue(cv2.imwrite(str(frame_dir / f"{index + 1:08d}.jpg"), frame))
            dance = native._new_record("DanceTrack", split, "NonFight", sequence, 4.0, 16, 4.0,
                                       source_group=f"DanceTrack|{split}")
            dance.required_frame_indices = tuple(required)
            records.append(dance)
            rows.append({"dataset": "DanceTrack", "split": split, "class_name": "NonFight", "label": 0,
                         "path": str(sequence), "source_group": dance.source_group, "fps": 4.0,
                         "frame_count": 16, "duration_seconds": 4.0, "eligible": 1, "window_count": 1})

        base_checkpoint = root / "base_best.pt"
        base_model = TinyClassifier()
        torch.save({"deployment_checkpoint_version": 1, "model_name": "mc3_18",
                    "class_to_idx": core.CLASS_TO_LABEL,
                    "protocol": {"protocol": four.PROTOCOL},
                    "state_dict": base_model.state_dict()}, base_checkpoint)
        metadata = {"base_manifest_sha256": "fixture-base-manifest",
                    "dancetrack_manifest_sha256": "fixture-dancetrack-manifest",
                    "split_policy": "fixture: grouped views; fixed source splits"}
        return dataset_root, source_cache, records, rows, metadata, base_checkpoint

    def test_reused_cache_first_batch_validation_checkpoint_pause_and_resume_end_to_end(self):
        with tempfile.TemporaryDirectory(dir=full_frame.PROJECT_ROOT) as directory:
            root = Path(directory)
            dataset_root, source_cache, records, rows, metadata, base_checkpoint = self._fixture(root)
            run_dir = root / "run_full_frame_smoke"
            cache_dir = root / "profile-cache" / full_frame.CACHE_PROTOCOL
            pause_file = root / "pause-after-epoch-1.flag"
            pause_file.touch()
            manifest = root / "fixture-manifest.csv"
            dance_manifest = root / "fixture-dance.json"
            manifest.write_text("fixture\n", encoding="utf-8")
            dance_manifest.write_text("fixture\n", encoding="utf-8")
            argv = ["--dataset-root", str(dataset_root), "--base-manifest", str(manifest),
                    "--dancetrack-manifest", str(dance_manifest), "--base-checkpoint", str(base_checkpoint),
                    "--output-root", str(root / "output"), "--run-dir", str(run_dir),
                    "--cache-dir", str(cache_dir), "--pause-file", str(pause_file),
                    "--device", "cpu", "--allow-cpu", "--epochs", "2", "--patience", "4",
                    "--lr-reduce-after", "2", "--backbone-lr", "0.00002", "--head-lr", "0.0001",
                    "--weight-decay", "0.0001", "--grad-accum", "1", "--window-batch-size", "2",
                    "--seed", "2026", "--log-every", "1"]
            old_runtime = {
                "native_install": native._install_runtime_protocol,
                "native_protocol": native.PROTOCOL_ID,
                "native_input": native.INPUT_REPRESENTATION,
                "native_input_protocol": native.INPUT_PROTOCOL_ID,
                "native_names": native.DATASET_NAMES,
                "native_loader": native.load_records,
                "native_dance_cache": native._prepare_dancetrack_cache,
                "native_experiment": native._experiment_protocol,
                "core_record": core._record_bag_tensor,
                "core_loader": core._load_cache,
                "core_windows": core._windows_for_record,
                "core_cache_protocol": core.CACHE_PROTOCOL,
                "core_window": core.WINDOW_SECONDS,
                "core_stride": core.STRIDE_SECONDS,
                "core_num_frames": core.NUM_FRAMES,
                "core_short": core.SHORT_SIDE,
                "core_crop": core.CROP_SIZE,
                "core_preprocess": core._preprocessing_protocol,
                "four_supplement_root": four.SUPPLEMENT_CACHE_ROOT,
                "four_roots": four.READ_ONLY_SUPPLEMENT_CACHE_ROOTS,
            }
            def close_smoke_log_handlers():
                smoke_logger = logging.getLogger("mc3_18_mil")
                for handler in list(smoke_logger.handlers):
                    if isinstance(handler, logging.FileHandler):
                        smoke_logger.removeHandler(handler)
                        handler.close()
            try:
                full_frame.configure_runtime()
                with (mock.patch.object(full_frame, "load_records", return_value=(records, rows, metadata)),
                      mock.patch.object(native, "_base_four_second_supplement_cache_root", return_value=source_cache),
                      mock.patch.object(native.shutil, "disk_usage", return_value=mock.Mock(free=8 * 1024**3)),
                      mock.patch.object(core, "_import_runtime_dependencies",
                                        return_value=(cv2, np, torch, nn, (None, lambda weights=None: TinyClassifier())))):
                    source_hashes = {path.name: __import__("hashlib").sha256(path.read_bytes()).hexdigest()
                                     for path in source_cache.glob("*.npz")}
                    self.assertEqual(full_frame.main(argv), 0)
                    first_status = json.loads((run_dir / "live_status.json").read_text(encoding="utf-8"))
                    self.assertEqual(first_status["status"], "paused")
                    self.assertTrue((run_dir / "epoch_01" / "best_model.pt").is_file())
                    self.assertTrue((run_dir / "last.pt").is_file())
                    self.assertIn("EPOCH 1/2 COMPLETE", (run_dir / "run.log").read_text(encoding="utf-8"))
                    self.assertTrue(any(cache_dir.joinpath("dancetrack").glob("*.npz")))
                    self.assertFalse(any(cache_dir.joinpath("videos").glob("*.npz")),
                                     "complete read-only source caches should not be copied or rewritten")
                    close_smoke_log_handlers()

                    resume_argv = [arg for arg in argv if arg != "--pause-file"]
                    pause_position = argv.index("--pause-file")
                    resume_argv = argv[:pause_position] + argv[pause_position + 2:]
                    resume_argv += ["--resume", str(run_dir / "last.pt")]
                    self.assertEqual(full_frame.main(resume_argv), 0)
                    self.assertTrue((run_dir / "epoch_02" / "best_model.pt").is_file())
                    report = json.loads((run_dir / "final_report.json").read_text(encoding="utf-8"))
                    self.assertEqual(report["status"], "completed")
                    self.assertEqual(report["protocol"]["input_representation"], full_frame.INPUT_REPRESENTATION)
                    self.assertEqual(set(report["dataset_summary"]["datasets"]), set(full_frame.DATASET_NAMES))
                    self.assertEqual(report["dataset_summary"]["total_bags"], len(records))
                    self.assertEqual(set(report["validation_metrics"]), {"sensitive", "moderate", "certain"})
                    self.assertEqual(set(report["test_metrics"]), {"sensitive", "moderate", "certain"})
                    self.assertEqual(len(list(run_dir.glob("epoch_[0-9][0-9]/best_model.pt"))), 2)
                    self.assertEqual(source_hashes,
                                     {path.name: __import__("hashlib").sha256(path.read_bytes()).hexdigest()
                                      for path in source_cache.glob("*.npz")})
            finally:
                close_smoke_log_handlers()
                native._install_runtime_protocol = old_runtime["native_install"]
                native.PROTOCOL_ID = old_runtime["native_protocol"]
                native.INPUT_REPRESENTATION = old_runtime["native_input"]
                native.INPUT_PROTOCOL_ID = old_runtime["native_input_protocol"]
                native.DATASET_NAMES = old_runtime["native_names"]
                native.load_records = old_runtime["native_loader"]
                native._prepare_dancetrack_cache = old_runtime["native_dance_cache"]
                native._experiment_protocol = old_runtime["native_experiment"]
                core._record_bag_tensor = old_runtime["core_record"]
                core._load_cache = old_runtime["core_loader"]
                core._windows_for_record = old_runtime["core_windows"]
                core.CACHE_PROTOCOL = old_runtime["core_cache_protocol"]
                core.WINDOW_SECONDS = old_runtime["core_window"]
                core.STRIDE_SECONDS = old_runtime["core_stride"]
                core.NUM_FRAMES = old_runtime["core_num_frames"]
                core.SHORT_SIDE = old_runtime["core_short"]
                core.CROP_SIZE = old_runtime["core_crop"]
                core._preprocessing_protocol = old_runtime["core_preprocess"]
                four.SUPPLEMENT_CACHE_ROOT = old_runtime["four_supplement_root"]
                four.READ_ONLY_SUPPLEMENT_CACHE_ROOTS = old_runtime["four_roots"]

    def test_uploaded_video_inference_uses_full_frame_4s16f2_transform(self):
        class FakeCapture:
            def __init__(self, _path):
                self.current = 0

            def isOpened(self):
                return True

            def get(self, prop):
                if prop == cv2.CAP_PROP_FPS:
                    return 4.0
                if prop == cv2.CAP_PROP_FRAME_COUNT:
                    return 16
                return 0

            def set(self, prop, value):
                if prop == cv2.CAP_PROP_POS_FRAMES:
                    self.current = int(value)

            def read(self):
                frame = np.zeros((90, 160, 3), dtype=np.uint8)
                frame[:, -20:, :] = 255
                return True, frame

            def release(self):
                return None

        model = TinyClassifier()
        payload = {"state_dict": model.state_dict(), "protocol": full_frame.experiment_protocol()}
        progress = []
        with mock.patch.object(torch.cuda, "is_available", return_value=False):
            result = dashboard._predict_video_full_frame_checkpoint(
                Path("fixture.mp4"), "fixture.mp4", Path("checkpoint.pt"), "moderate", 0.5,
                lambda **values: progress.append(values), payload,
                mock.Mock(VideoCapture=FakeCapture, CAP_PROP_FPS=cv2.CAP_PROP_FPS,
                          CAP_PROP_FRAME_COUNT=cv2.CAP_PROP_FRAME_COUNT,
                          CAP_PROP_POS_FRAMES=cv2.CAP_PROP_POS_FRAMES, COLOR_BGR2RGB=cv2.COLOR_BGR2RGB,
                          INTER_AREA=cv2.INTER_AREA, INTER_LINEAR=cv2.INTER_LINEAR,
                          cvtColor=cv2.cvtColor, resize=cv2.resize),
                np, torch, nn, (None, lambda weights=None: TinyClassifier()))
        self.assertEqual(result["window_count"], 1)
        self.assertEqual(result["frames_per_window"], 16)
        self.assertEqual(result["short_side"], 112)
        self.assertIsNone(result["crop_size"])
        self.assertEqual(result["input_representation"], full_frame.INPUT_REPRESENTATION)
        self.assertEqual(progress[-1]["processed_windows"], 1)

    def test_matching_prior_dancetrack_cache_is_read_only_and_not_copied(self):
        with tempfile.TemporaryDirectory(dir=full_frame.PROJECT_ROOT) as directory:
            root = Path(directory)
            sequence = root / "DanceTrack" / "train" / "sequence-001"
            frame_dir = sequence / "img1"
            frame_dir.mkdir(parents=True)
            required = tuple(range(16))
            for index in required:
                (frame_dir / f"{index + 1:08d}.jpg").write_bytes(b"retained-frame")
            record = native._new_record("DanceTrack", "train", "NonFight", sequence,
                                       4.0, 16, 4.0, source_group="DanceTrack|sequence-001")
            record.required_frame_indices = required
            signature = [f"{index}:{(frame_dir / f'{index + 1:08d}.jpg').stat().st_size}:"
                         f"{(frame_dir / f'{index + 1:08d}.jpg').stat().st_mtime_ns}"
                         for index in required]
            cache_key = __import__("hashlib").sha256(
                (full_frame.LEGACY_DANCETRACK_CACHE_PROTOCOL + "|" + record.identity + "|"
                 + "|".join(signature)).encode("utf-8")).hexdigest()
            prior_root = root / "old-cache" / "dancetrack"
            prior_root.mkdir(parents=True)
            prior_path = prior_root / f"{cache_key}.npz"
            old_frames = np.full((16, 128, 128, 3), 77, dtype=np.uint8)
            np.savez_compressed(prior_path, frame_indices=np.asarray(required, dtype=np.int32), frames=old_frames)
            target = root / "new-profile-cache"
            updates = []
            logger = logging.getLogger("full-frame-dancetrack-cache-test")
            with (mock.patch.object(full_frame, "LEGACY_DANCETRACK_CACHE_ROOT", prior_root),
                  mock.patch.object(full_frame, "_ORIGINAL_DANCETRACK_CACHE_PREPARER") as rebuild):
                full_frame.prepare_dancetrack_cache([record], target, cv2, np, logger,
                                                    lambda **values: updates.append(values),
                                                    cache_budget_bytes=1_000_000)
            self.assertEqual(record.prior_supplement_cache_paths, (prior_path,))
            self.assertFalse(any((target / "dancetrack").glob("*.npz")))
            rebuild.assert_not_called()
            cached, mapping = four._load_hybrid_cache(record, np)
            self.assertEqual(cached.shape, old_frames.shape)
            self.assertEqual(int(cached[0, 0, 0, 0]), 77)
            self.assertEqual(mapping, {index: index for index in required})
            self.assertEqual(updates[-1]["stage_progress"], 1.0)


if __name__ == "__main__":
    unittest.main()
