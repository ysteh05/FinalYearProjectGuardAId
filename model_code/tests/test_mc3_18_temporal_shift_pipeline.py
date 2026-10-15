import contextlib
import hashlib
import json
import logging
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn as nn  # noqa: E402

import mc3_18_dashboard as dashboard  # noqa: E402
import mc3_18_four_second_pipeline as four  # noqa: E402
import mc3_18_mil_pipeline as core  # noqa: E402
import mc3_18_native_finetune_pipeline as native  # noqa: E402
import mc3_18_temporal_shift_letterbox_pipeline as profile  # noqa: E402


# Exact traceback captured from the first CUDA launch on 2026-10-15. Keep this
# as the regression record for the Windows cache-commit failure.
OBSERVED_WINDOWS_CACHE_WINERROR3_TRACEBACK = r"""Traceback (most recent call last):
  File "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\model_code\mc3_18_native_finetune_pipeline.py", line 1242, in main
    run_dir = _run_training(args)
              ^^^^^^^^^^^^^^^^^^^
  File "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\model_code\mc3_18_native_finetune_pipeline.py", line 907, in _run_training
    _prepare_video_cache(records, cache_root, cv2, np, logger,
  File "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\model_code\mc3_18_temporal_shift_letterbox_pipeline.py", line 404, in prepare_temporal_cache
    os.replace(temporary, cache_path)
FileNotFoundError: [WinError 3] 系统找不到指定的路径。: 'C:\\Users\\YSTeh\\Documents\\INTI\\2026_2027_Y3\\Python\\6000CEM\\model_runs\\mc3_temporal_shift_letterbox_4dataset_4s16f2_k400\\_frame_cache\\mc3-4s16f2-letterbox160-rgbmean-five-frame-offsets-v1\\temporal_shift_frames\\.cache-write-25276-1792037243946328800.tmp' -> 'C:\\Users\\YSTeh\\Documents\\INTI\\2026_2027_Y3\\Python\\6000CEM\\model_runs\\mc3_temporal_shift_letterbox_4dataset_4s16f2_k400\\_frame_cache\\mc3-4s16f2-letterbox160-rgbmean-five-frame-offsets-v1\\temporal_shift_frames\\3522ed3be979fea7b4909b5093b6dd43b4856a406ce1ba782974588207c4badb.npz'
"""

# Exact traceback captured from the fresh CUDA launch on 2026-10-15. The native
# parser's unrelated default DanceTrack manifest was forwarded to this profile's
# directory-based loader, whose signature did not accept that keyword.
OBSERVED_TEMPORAL_DANCETRACK_DEFAULT_TRACEBACK = r"""Traceback (most recent call last):
  File "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\model_code\mc3_18_native_finetune_pipeline.py", line 1248, in main
    run_dir = _run_training(args)
              ^^^^^^^^^^^^^^^^^^^
  File "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\model_code\mc3_18_native_finetune_pipeline.py", line 833, in _run_training
    records, manifest_rows, data_meta = load_records(**record_load_args)
                                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: load_records() got an unexpected keyword argument 'dancetrack_manifest'
Traceback (most recent call last):
  File "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\model_code\mc3_18_temporal_shift_letterbox_pipeline.py", line 535, in <module>
    raise SystemExit(main())
                     ^^^^^^
  File "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\model_code\mc3_18_temporal_shift_letterbox_pipeline.py", line 531, in main
    return native.main(argv)
           ^^^^^^^^^^^^^^^^^
  File "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\model_code\mc3_18_native_finetune_pipeline.py", line 1248, in main
    run_dir = _run_training(args)
              ^^^^^^^^^^^^^^^^^^^
  File "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\model_code\mc3_18_native_finetune_pipeline.py", line 833, in _run_training
    records, manifest_rows, data_meta = load_records(**record_load_args)
                                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: load_records() got an unexpected keyword argument 'dancetrack_manifest'
"""


class TinyClassifier(nn.Module):
    def __init__(self, on_first_forward=None):
        super().__init__()
        self.fc = nn.Linear(3, 2)
        self.on_first_forward = on_first_forward
        self.did_callback = False

    def forward(self, values):
        if self.on_first_forward and not self.did_callback:
            self.did_callback = True
            self.on_first_forward()
        return self.fc(values.mean(dim=(2, 3, 4)))


class TemporalShiftTransformTests(unittest.TestCase):
    def test_expanded_cache_budget_counts_reusable_files_and_preserves_three_gib(self):
        gib = 1024**3
        self.assertEqual(profile.TEMPORAL_CACHE_ADDITIONAL_TARGET_GIB, 14.82)
        target = int(31.79 * gib)
        self.assertEqual(profile.TEMPORAL_CACHE_TARGET_BYTES, target)
        self.assertGreater(target, native.VIDEO_CACHE_CAP_BYTES)
        self.assertEqual(profile.temporal_cache_budget_bytes(20 * gib, 20 * gib), target)
        self.assertEqual(profile.temporal_cache_budget_bytes(5 * gib, 4 * gib), 6 * gib)
        self.assertEqual(profile.temporal_cache_budget_bytes(2 * gib, 4 * gib), 4 * gib)
        free = int(11.82 * gib)
        existing = int(16.97 * gib)
        budget = profile.temporal_cache_budget_bytes(free, existing)
        self.assertEqual(budget, min(target, existing + free - 3 * gib))
        self.assertLessEqual(budget - existing, free - 3 * gib)

    def test_regression_recorded_windows_cache_winerror3_path_is_now_short(self):
        self.assertIn("os.replace(temporary, cache_path)", OBSERVED_WINDOWS_CACHE_WINERROR3_TRACEBACK)
        self.assertIn("FileNotFoundError: [WinError 3]", OBSERVED_WINDOWS_CACHE_WINERROR3_TRACEBACK)
        self.assertIn("3522ed3be979fea7b4909b5093b6dd43b4856a406ce1ba782974588207c4badb.npz",
                      OBSERVED_WINDOWS_CACHE_WINERROR3_TRACEBACK)
        previous_destination = (
            r"C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\model_runs"
            r"\mc3_temporal_shift_letterbox_4dataset_4s16f2_k400\_frame_cache"
            r"\mc3-4s16f2-letterbox160-rgbmean-five-frame-offsets-v1\temporal_shift_frames"
            r"\3522ed3be979fea7b4909b5093b6dd43b4856a406ce1ba782974588207c4badb.npz"
        )
        self.assertEqual(len(previous_destination), 276)
        self.assertGreater(len(previous_destination), 259)

        production_cache_root = (
            dashboard.DEFAULT_TEMPORAL_SHIFT_OUTPUT_ROOT / "_frame_cache"
            / profile.CACHE_PROTOCOL / "temporal_shift_frames"
        )
        full_digest_target = production_cache_root / (("a" * 64) + ".npz")
        temporary_target = four._cache_write_temp_path(full_digest_target)
        self.assertLess(len(str(full_digest_target)), 260)
        self.assertLess(len(str(temporary_target)), 260)

    def test_regression_recorded_dancetrack_default_trace_uses_profile_loader_without_keyword(self):
        self.assertIn("line 833, in _run_training", OBSERVED_TEMPORAL_DANCETRACK_DEFAULT_TRACEBACK)
        self.assertIn("TypeError: load_records() got an unexpected keyword argument 'dancetrack_manifest'",
                      OBSERVED_TEMPORAL_DANCETRACK_DEFAULT_TRACEBACK)
        parser = profile._build_temporal_profile_parser(native.build_parser)
        parsed = parser.parse_args(["--run-dir", "fixture-run", "--cache-dir", "fixture-cache"])
        self.assertEqual(parsed.dancetrack_manifest, "")
        self.assertEqual((parsed.epochs, parsed.patience, parsed.lr_reduce_after), (20, 4, 2))

    def test_five_variants_apply_one_offset_to_all_sixteen_frame_indices(self):
        window = core.Window(0.0, 4.0, tuple(range(10, 26)))
        variants = profile.window_variants(window, frame_count=100, training=True)
        self.assertEqual([offset for offset, _ in variants], [-2, -1, 0, 1, 2])
        for offset, indices in variants:
            self.assertEqual(len(indices), 16)
            self.assertEqual(indices, tuple(value + offset for value in window.frame_indices))
        self.assertEqual(profile.window_variants(window, frame_count=100, training=False),
                         [(0, window.frame_indices)])

    def test_boundary_policy_clamps_without_changing_window_length(self):
        self.assertEqual(profile.shifted_indices((0, 1, 2, 3), -2, 10), (0, 0, 0, 1))
        self.assertEqual(profile.shifted_indices((6, 7, 8, 9), 2, 10), (8, 9, 9, 9))
        with self.assertRaisesRegex(ValueError, "frame_count must be positive"):
            profile.shifted_indices((0,), -1, 0)

    def test_record_builder_expands_training_only_and_returns_full_frame_letterbox(self):
        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            root = Path(directory)
            records = []
            for split in ("train", "val"):
                video = root / f"{split}.mp4"
                video.write_bytes(split.encode("ascii"))
                record = native._new_record("Harvard Dataverse", split, "NonFight", video,
                                            4.0, 16, 4.0)
                indices = profile.required_frame_indices(record)
                record.temporal_frame_indices = indices
                record.temporal_cache_path = root / f"{split}.npz"
                frames = np.stack([np.full((80, 120, 3), 30 + index * 8, dtype=np.uint8)
                                   for index in indices])
                np.savez_compressed(record.temporal_cache_path,
                                    frame_indices=np.asarray(indices, dtype=np.int32), frames=frames)
                records.append(record)

            train_clips, train_windows = profile.record_bag_tensor(
                records[0], training=True, epoch=1, seed=11, np=np, cv2=cv2, torch=torch)
            val_clips, val_windows = profile.record_bag_tensor(
                records[1], training=False, epoch=1, seed=11, np=np, cv2=cv2, torch=torch)

        self.assertEqual(len(train_windows), 1)
        self.assertEqual(tuple(train_clips.shape), (5, 3, 16, 160, 160))
        self.assertEqual(len(val_windows), 1)
        self.assertEqual(tuple(val_clips.shape), (1, 3, 16, 160, 160))

    def test_temporal_cache_is_atomically_built_then_reused_without_redecoding(self):
        class Capture:
            def __init__(self):
                self.position = 0
                self.read_count = 0

            def isOpened(self):
                return True

            def read(self):
                frame = np.full((20, 32, 3), self.position % 240, dtype=np.uint8)
                self.position += 1
                self.read_count += 1
                return True, frame

            def release(self):
                return None

        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            root = Path(directory)
            production_cache_root = (
                dashboard.DEFAULT_TEMPORAL_SHIFT_OUTPUT_ROOT / "_frame_cache"
                / profile.CACHE_PROTOCOL / "temporal_shift_frames"
            )
            cache_component_length = (
                len(str(production_cache_root)) - len(str(root)) - len("temporal_shift_frames") - 2
            )
            self.assertGreater(cache_component_length, 0)
            cache_dir = root / ("c" * cache_component_length)
            self.assertEqual(len(str(cache_dir / "temporal_shift_frames")),
                             len(str(production_cache_root)))
            video = root / "fixture.mp4"
            video.write_bytes(b"fixture source file")
            record = native._new_record("Harvard Dataverse", "train", "NonFight", video,
                                        30.0, 120, 4.0)
            records = [record]
            logger = logging.getLogger(f"temporal_cache_test_{id(record)}")
            updates = []
            capture = Capture()
            with mock.patch.object(cv2, "VideoCapture", return_value=capture):
                profile.prepare_temporal_cache(records, cache_dir, cv2, np, logger,
                                               lambda **values: updates.append(values),
                                               cache_budget_bytes=8 * 1024 * 1024)
            cache_path = record.temporal_cache_path
            self.assertTrue(cache_path.is_file())
            self.assertEqual(tuple(record.temporal_frame_indices), profile.required_frame_indices(record))
            with np.load(cache_path, allow_pickle=False) as saved:
                self.assertEqual(saved["frame_indices"].astype(np.int64).tolist(),
                                 list(profile.required_frame_indices(record)))
            built_clips, _ = profile.record_bag_tensor(
                record, training=True, epoch=1, seed=5, np=np, cv2=cv2, torch=torch)
            self.assertEqual(tuple(built_clips.shape), (5, 3, 16, 160, 160))
            reused_updates = []
            with mock.patch.object(cv2, "VideoCapture", side_effect=AssertionError("complete cache must be reused")):
                profile.prepare_temporal_cache(records, cache_dir, cv2, np, logger,
                                               lambda **values: reused_updates.append(values),
                                               cache_budget_bytes=8 * 1024 * 1024)
        self.assertEqual(capture.read_count, max(profile.required_frame_indices(record)) + 1)
        self.assertTrue(any(item.get("cache_reused") == 1 and item.get("cache_created") == 0
                            for item in reused_updates))

    def test_quick_inventory_adds_official_dancetrack_splits_to_three_source_totals(self):
        base_datasets = {
            "Harvard Dataverse": {
                "train": {"Fight": 1, "NonFight": 1, "bags": 2, "windows": 2},
                "val": {"Fight": 1, "NonFight": 1, "bags": 2, "windows": 2},
                "test": {"Fight": 1, "NonFight": 1, "bags": 2, "windows": 2},
                "total_bags": 6,
            },
            "RWF-2000": {}, "AIRTLab": {},
        }
        base_overall = {split: {"Fight": 1, "NonFight": 1, "bags": 2, "windows": 2}
                        for split in profile.SPLITS}
        base_summary = {"datasets": base_datasets, "overall": base_overall, "total_bags": 6}
        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            root = Path(directory) / "DanceTrack"
            for shard, expected in profile.EXPECTED_DANCETRACK_SHARDS.items():
                for number in range(expected):
                    seq = root / shard / f"seq_{number:02d}"
                    (seq / "img1").mkdir(parents=True)
                    (seq / "seqinfo.ini").write_text("[Sequence]\nframeRate=4\nseqLength=16\n",
                                                      encoding="utf-8")
                    record = native._new_record("DanceTrack", profile.DANCETRACK_SHARDS[shard],
                                                "NonFight", seq, 4.0, 16, 4.0)
                    for frame_index in profile.required_frame_indices(record):
                        (seq / "img1" / f"{frame_index + 1:08d}.jpg").touch()
            mock_inventory = {"ready": True, "summary": base_summary, "seed": 2026,
                              "missing_source_count": 0, "missing_source_count_sampled": 0,
                              "first_missing_sources": []}
            with mock.patch.object(profile.letterbox, "quick_inventory", return_value=mock_inventory):
                summary = profile.quick_inventory(dataset_root=Path(directory), base_manifest=Path(directory) / "base.csv")
        self.assertTrue(summary["ready"])
        self.assertEqual(summary["summary"]["total_bags"], 106)
        self.assertEqual(summary["summary"]["datasets"]["DanceTrack"]["train"]["bags"], 40)
        self.assertEqual(summary["summary"]["datasets"]["DanceTrack"]["val"]["bags"], 25)
        self.assertEqual(summary["summary"]["datasets"]["DanceTrack"]["test"]["bags"], 35)


class TemporalShiftDashboardTests(unittest.TestCase):
    def test_defaults_are_twenty_four_two_and_epochs_are_hard_capped_at_twenty(self):
        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            output_root = Path(directory) / "runs"
            with mock.patch.object(dashboard, "DEFAULT_TEMPORAL_SHIFT_OUTPUT_ROOT", output_root):
                args, run_dir = dashboard._temporal_shift_training_arguments({}, Path(directory) / "pause.flag")
                parsed = native.build_parser().parse_args(args)
                self.assertEqual((parsed.epochs, parsed.patience, parsed.lr_reduce_after), (20, 4, 2))
                self.assertEqual(parsed.initialization_mode, "kinetics400")
                self.assertEqual(parsed.dancetrack_manifest, "")
                self.assertNotIn("--base-checkpoint", args)
                self.assertTrue(run_dir.name.startswith("run_temporal_shift_letterbox_ui_"))
                with self.assertRaisesRegex(ValueError, "上限为 20"):
                    dashboard._temporal_shift_training_arguments({"epochs": 21}, Path(directory) / "pause2.flag")
        self.assertEqual(dashboard.TEMPORAL_SHIFT_DEFAULT_CONFIG["epochs"], 20)
        self.assertEqual(dashboard.TEMPORAL_SHIFT_DEFAULT_CONFIG["patience"], 4)
        self.assertEqual(dashboard.TEMPORAL_SHIFT_DEFAULT_CONFIG["lr_reduce_after"], 2)

    def test_resume_rejects_another_profile_checkpoint(self):
        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            root = Path(directory) / "runs"
            run = root / "run_temporal_shift_letterbox_ui_other"
            run.mkdir(parents=True)
            last = run / "last.pt"
            last.touch()
            config = {"experiment_protocol": "different", "input_representation": profile.INPUT_REPRESENTATION,
                      "epochs": 20, "patience": 4, "lr_scheduler_no_improvement_epochs": 2,
                      "lr_scheduler_factor": 0.5, "min_lr": 1e-6, "min_delta": 0.001,
                      "backbone_lr": 2e-5, "head_lr": 1e-4, "weight_decay": 1e-4,
                      "grad_accum": 8, "window_batch_size": 2, "seed": 2026, "amp": False,
                      "device": "cpu", "protocol": {}, "initialization_mode": "kinetics400",
                      "initialization": {"weights": "MC3_18_Weights.KINETICS400_V1"}, "dataset_fingerprint": "fixture"}
            (run / "config.json").write_text(json.dumps(config), encoding="utf-8")
            with mock.patch.object(dashboard, "DEFAULT_TEMPORAL_SHIFT_OUTPUT_ROOT", root):
                with self.assertRaisesRegex(ValueError, "不属于四数据集"):
                    dashboard._temporal_shift_training_arguments({"resume_checkpoint": str(last)},
                                                                 Path(directory) / "pause.flag")

    def test_page_exposes_controls_routes_and_inference(self):
        class IDs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.values = set()

            def handle_starttag(self, _tag, attrs):
                value = dict(attrs).get("id")
                if value:
                    self.values.add(value)

        parser = IDs()
        parser.feed(dashboard.TEMPORAL_SHIFT_HTML_PATH.read_text(encoding="utf-8"))
        self.assertTrue({"epochs", "patience", "lr_reduce_after", "startButton", "resumeButton",
                         "pauseButton", "historyBody", "validationMetrics", "testMetrics",
                         "inferButton", "datasetTable", "console"}.issubset(parser.values))
        page_handler = object.__new__(dashboard.DashboardHandler)
        page_handler.path = "/temporal-shift-training"
        with mock.patch.object(dashboard.DashboardHandler, "_send_bytes", autospec=True) as send_bytes:
            dashboard.DashboardHandler.do_GET(page_handler)
        self.assertEqual(send_bytes.call_args.args[1], dashboard.TEMPORAL_SHIFT_HTML_PATH.read_bytes())
        summary_handler = object.__new__(dashboard.DashboardHandler)
        summary_handler.path = "/api/temporal-shift/summary"
        with mock.patch.object(dashboard, "_temporal_shift_dataset_summary",
                               return_value={"ready": True, "summary": {"total_bags": 4549}}), \
                mock.patch.object(dashboard.DashboardHandler, "_send_json", autospec=True) as send_json:
            dashboard.DashboardHandler.do_GET(summary_handler)
        self.assertEqual(send_json.call_args.args[1]["summary"]["total_bags"], 4549)

    def test_temporal_shift_checkpoint_is_listed_as_inferable_with_its_own_thresholds(self):
        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            root = Path(directory)
            run = root / "run_temporal_shift_letterbox_ui_fixture"
            epoch = run / "epoch_01"
            epoch.mkdir(parents=True)
            checkpoint = epoch / "best_model.pt"
            checkpoint.touch()
            selection = {name: {"threshold": value} for name, value in
                         (("sensitive", 0.2), ("moderate", 0.5), ("certain", 0.8))}
            (epoch / "thresholds.json").write_text(json.dumps({
                "protocol": profile.EXPERIMENT_PROTOCOL, "selection": selection,
            }), encoding="utf-8")
            (epoch / "final_report.json").write_text(json.dumps({
                "experiment_protocol": profile.EXPERIMENT_PROTOCOL,
                "input_representation": profile.INPUT_REPRESENTATION,
                "best_epoch": 1,
            }), encoding="utf-8")
            models = dashboard._list_runs(root, include_nested=True)
        self.assertEqual(len(models), 1)
        self.assertTrue(models[0]["can_infer"])
        self.assertEqual(models[0]["input_representation"], profile.INPUT_REPRESENTATION)
        self.assertEqual(models[0]["threshold_modes"], ["sensitive", "moderate", "certain"])

    def test_uploaded_video_inference_uses_unshifted_letterbox_protocol(self):
        class Capture:
            def __init__(self):
                self.position = 0
                self.released = False

            def isOpened(self):
                return True

            def get(self, key):
                return 4.0 if key == cv2.CAP_PROP_FPS else 16

            def set(self, _key, value):
                self.position = int(value)
                return True

            def read(self):
                frame = np.full((90, 160, 3), 50 + self.position, dtype=np.uint8)
                return True, frame

            def release(self):
                self.released = True

        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            root = Path(directory)
            video = root / "sample.mp4"
            video.touch()
            checkpoint = root / "best_model.pt"
            saved_protocol = {**profile.experiment_protocol(),
                              "experiment_protocol": profile.EXPERIMENT_PROTOCOL,
                              "input_modality": "native",
                              "input_representation": profile.INPUT_REPRESENTATION}
            torch.save({"deployment_checkpoint_version": 1, "model_name": "mc3_18",
                        "class_to_idx": core.CLASS_TO_LABEL,
                        "state_dict": TinyClassifier().state_dict(),
                        "protocol": saved_protocol}, checkpoint)
            capture = Capture()
            progress = []
            fake_deps = (cv2, np, torch, nn, (None, lambda weights=None: TinyClassifier()))
            with mock.patch.object(core, "_import_runtime_dependencies", return_value=fake_deps), \
                    mock.patch.object(cv2, "VideoCapture", return_value=capture), \
                    mock.patch.object(torch.cuda, "is_available", return_value=False):
                result = dashboard._predict_video_checkpoint(
                    video, "sample.mp4", checkpoint, "moderate", 0.5,
                    lambda **values: progress.append(values))
        self.assertEqual(result["protocol"], profile.EXPERIMENT_PROTOCOL)
        self.assertEqual(result["input_representation"], profile.INPUT_REPRESENTATION)
        self.assertEqual(result["input_size"], [160, 160])
        self.assertFalse(result["temporal_augmentation_applied"])
        self.assertEqual(result["inference_source_frame_offsets"], [0])
        self.assertEqual(result["window_count"], 1)
        self.assertTrue(capture.released)
        self.assertGreaterEqual(len(progress), 2)


class TemporalShiftTrainingEndToEndTests(unittest.TestCase):
    def _fixture_records(self, root: Path):
        records, rows = [], []
        cache_root = root / "complete-shift-cache"
        cache_root.mkdir()
        for split in profile.SPLITS:
            for label in (0, 1):
                class_name = "Fight" if label else "NonFight"
                path = root / "videos" / split / f"{class_name}.mp4"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"{split}-{label}".encode("ascii"))
                record = native._new_record("Harvard Dataverse", split, class_name, path, 4.0, 16, 4.0,
                                            source_group=f"fixture|{split}|{label}")
                indices = profile.required_frame_indices(record)
                record.temporal_frame_indices = indices
                cache_path = cache_root / f"{split}_{label}.npz"
                pixels = 220 if label else 28
                frames = np.full((len(indices), 80, 120, 3), pixels, dtype=np.uint8)
                np.savez_compressed(cache_path, frame_indices=np.asarray(indices, dtype=np.int32), frames=frames)
                records.append(record)
                rows.append({"dataset": record.dataset, "split": record.split, "class_name": class_name,
                             "label": label, "path": str(path), "source_group": record.source_group,
                             "fps": 4.0, "frame_count": 16, "duration_seconds": 4.0,
                             "eligible": 1, "window_count": 1})
        return records, rows, cache_root

    def test_reused_cache_first_batch_validation_checkpoint_pause_and_resume(self):
        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            root = Path(directory)
            records, rows, _fixture_cache_root = self._fixture_records(root)
            manifest = root / "fixture-base-manifest.csv"
            manifest.write_text("fixture base split\n", encoding="utf-8")
            run_dir = root / "run_temporal_shift_letterbox_ui_e2e"
            production_cache_root = (
                dashboard.DEFAULT_TEMPORAL_SHIFT_OUTPUT_ROOT / "_frame_cache"
                / profile.CACHE_PROTOCOL / "temporal_shift_frames"
            )
            cache_component_length = (
                len(str(production_cache_root)) - len(str(root)) - len("temporal_shift_frames") - 2
            )
            self.assertGreater(cache_component_length, 0)
            cache_dir = root / ("c" * cache_component_length)
            self.assertEqual(len(str(cache_dir / "temporal_shift_frames")),
                             len(str(production_cache_root)))
            pause_file = root / "pause-after-first-epoch.flag"
            pause_file.touch()
            live_path = run_dir / "live_status.json"
            metadata = {"base_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                        "split_policy": "fixture fixed split", "summary": profile._summarize_records(records)}
            first_batch = {"seen": False}
            kinetics_weights = mock.Mock(url="https://weights.invalid/mc3_18-k400-fixture.pth")
            weights_enum = type("Weights", (), {"KINETICS400_V1": kinetics_weights})
            model_calls = []

            def inspect_initial_live_state():
                payload = json.loads(live_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "running")
                self.assertEqual(payload["epoch"], 1)
                self.assertIsNone(payload["best_val_roc_auc"])
                self.assertNotIn("NaN", live_path.read_text(encoding="utf-8"))
                first_batch["seen"] = True

            def model_builder(weights=None):
                model_calls.append(weights)
                return TinyClassifier(inspect_initial_live_state if not first_batch["seen"] else None)

            cache_prepare_calls = 0
            real_prepare_temporal_cache = profile.prepare_temporal_cache

            class SyntheticVideoCapture:
                def __init__(self, pixel_value):
                    self.pixel_value = pixel_value
                    self.position = 0

                def isOpened(self):
                    return True

                def read(self):
                    frame = np.full((80, 120, 3), self.pixel_value, dtype=np.uint8)
                    self.position += 1
                    return True, frame

                def release(self):
                    return None

            def prepare_completed_cache(records_arg, cache, cv2_arg, np_arg, logger, update_status,
                                        *, cache_budget_bytes):
                nonlocal cache_prepare_calls
                cache_prepare_calls += 1
                if cache_prepare_calls == 1:
                    def open_synthetic_video(video_path):
                        pixel_value = 220 if Path(video_path).stem == "Fight" else 28
                        return SyntheticVideoCapture(pixel_value)

                    with mock.patch.object(cv2_arg, "VideoCapture", side_effect=open_synthetic_video):
                        real_prepare_temporal_cache(
                            records_arg, Path(cache), cv2_arg, np_arg, logger, update_status,
                            cache_budget_bytes=cache_budget_bytes)
                else:
                    with mock.patch.object(
                            cv2_arg, "VideoCapture",
                            side_effect=AssertionError("a complete temporal cache must be reused")):
                        real_prepare_temporal_cache(
                            records_arg, Path(cache), cv2_arg, np_arg, logger, update_status,
                            cache_budget_bytes=cache_budget_bytes)
                for record in records_arg:
                    self.assertTrue(record.temporal_cache_path.is_file())
                logger.info("Regression fixture: reused %d/%d complete temporal frame caches",
                            len(records_arg), len(records_arg))

            native_attrs = ("PROTOCOL_ID", "INPUT_REPRESENTATION", "INPUT_PROTOCOL_ID", "DATASET_NAMES",
                            "load_records", "_experiment_protocol", "_install_runtime_protocol",
                            "_prepare_video_cache", "build_parser", "CACHE_REUSE_METADATA")
            core_attrs = ("SHORT_SIDE", "CROP_SIZE", "_record_bag_tensor", "_load_cache", "WINDOW_SECONDS",
                          "STRIDE_SECONDS", "NUM_FRAMES", "CACHE_PROTOCOL", "_preprocessing_protocol",
                          "_windows_for_record")
            with contextlib.ExitStack() as stack:
                for name in native_attrs:
                    stack.enter_context(mock.patch.object(native, name, getattr(native, name)))
                for name in core_attrs:
                    stack.enter_context(mock.patch.object(core, name, getattr(core, name)))
                stack.enter_context(mock.patch.object(profile, "load_records",
                                                       return_value=(records, rows, metadata)))
                stack.enter_context(mock.patch.object(profile, "prepare_temporal_cache",
                                                       side_effect=prepare_completed_cache))
                stack.enter_context(mock.patch.object(native.shutil, "disk_usage",
                                                       return_value=mock.Mock(free=8 * 1024**3)))
                stack.enter_context(mock.patch.object(core, "_import_runtime_dependencies",
                                                       return_value=(cv2, np, torch, nn,
                                                                     (weights_enum, model_builder))))
                argv = ["--dataset-root", str(root), "--base-manifest", str(manifest),
                        "--initialization-mode", "kinetics400",
                        "--output-root", str(root / "out"), "--run-dir", str(run_dir),
                        "--cache-dir", str(cache_dir), "--pause-file", str(pause_file),
                        "--device", "cpu", "--allow-cpu", "--epochs", "2", "--patience", "4",
                        "--lr-reduce-after", "2", "--backbone-lr", "0.00002", "--head-lr", "0.0001",
                        "--weight-decay", "0.0001", "--grad-accum", "1", "--window-batch-size", "2",
                        "--seed", "2026", "--log-every", "1"]
                self.assertEqual(profile.main(argv), 0)
                self.assertTrue(first_batch["seen"], "the first training batch must see JSON-safe initial status")
                self.assertIs(model_calls[0], kinetics_weights)
                paused = json.loads(live_path.read_text(encoding="utf-8"))
                self.assertEqual(paused["status"], "paused")
                self.assertTrue((run_dir / "epoch_01" / "best_model.pt").is_file())
                self.assertTrue((run_dir / "last.pt").is_file())
                log = (run_dir / "run.log").read_text(encoding="utf-8")
                self.assertIn("reused 6/6 complete temporal frame caches", log)
                self.assertIn("EPOCH 1/2 COMPLETE", log)
                self.assertIn("SAFE PAUSE after epoch 1", log)
                self.assertFalse((run_dir / "final_report.json").exists())

                resumed = list(argv)
                resumed[resumed.index("--pause-file") + 1] = str(root / "resume-pause.flag")
                resumed.extend(["--resume", str(run_dir / "last.pt")])
                self.assertEqual(profile.main(resumed), 0)
                self.assertIsNone(model_calls[1], "resume must restore the saved model instead of reinitializing Kinetics")
                self.assertTrue((run_dir / "epoch_02" / "best_model.pt").is_file())
                report = json.loads((run_dir / "final_report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["status"], "completed")
                self.assertEqual(report["protocol"]["experiment_protocol"], profile.EXPERIMENT_PROTOCOL)
                self.assertEqual(report["dataset_summary"]["total_bags"], 6)
                self.assertEqual(report["initialization_mode"], "kinetics400")
                self.assertEqual(report["initialization"]["weights"], "MC3_18_Weights.KINETICS400_V1")
                self.assertEqual(set(report["validation_metrics"]), {"sensitive", "moderate", "certain"})
                self.assertEqual(set(report["test_metrics"]), {"sensitive", "moderate", "certain"})
                self.assertEqual(len(list(run_dir.glob("epoch_[0-9][0-9]/best_model.pt"))), 2)
                live = json.loads(live_path.read_text(encoding="utf-8"))
                self.assertEqual(live["status"], "completed")
                self.assertNotIn("NaN", live_path.read_text(encoding="utf-8"))
                for handler in list(logging.getLogger("mc3_18_mil").handlers):
                    if isinstance(handler, logging.FileHandler):
                        logging.getLogger("mc3_18_mil").removeHandler(handler)
                        handler.close()


if __name__ == "__main__":
    unittest.main()
