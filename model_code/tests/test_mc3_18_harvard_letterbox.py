import contextlib
import csv
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
import mc3_18_harvard_letterbox_pipeline as profile  # noqa: E402
import mc3_18_mil_pipeline as core  # noqa: E402
import mc3_18_native_finetune_pipeline as native  # noqa: E402


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


class HarvardLetterboxPreprocessingTests(unittest.TestCase):
    def test_landscape_portrait_and_square_use_rgb_mean_padding_and_fixed_tensor(self):
        mean_rgb = np.asarray(core.MEAN, dtype=np.float32)
        mean_pixel = np.rint(mean_rgb * 255).astype(np.uint8)
        landscape = np.broadcast_to(mean_pixel, (90, 160, 3)).copy()
        portrait = np.broadcast_to(mean_pixel, (160, 90, 3)).copy()
        square = np.broadcast_to(mean_pixel, (160, 160, 3)).copy()

        landscape_clip = profile.frames_to_clip([landscape] * 16, np=np, torch=torch, cv2=cv2)
        portrait_clip = profile.frames_to_clip([portrait] * 16, np=np, torch=torch, cv2=cv2)
        square_clip = profile.frames_to_clip([square] * 16, np=np, torch=torch, cv2=cv2)

        self.assertEqual(tuple(landscape_clip.shape), (3, 16, 160, 160))
        self.assertEqual(tuple(portrait_clip.shape), (3, 16, 160, 160))
        self.assertEqual(tuple(square_clip.shape), (3, 16, 160, 160))
        self.assertTrue(torch.equal(landscape_clip[:, :, :35, :], torch.zeros_like(landscape_clip[:, :, :35, :])))
        self.assertTrue(torch.equal(landscape_clip[:, :, 125:, :], torch.zeros_like(landscape_clip[:, :, 125:, :])))
        self.assertTrue(torch.equal(portrait_clip[:, :, :, :35], torch.zeros_like(portrait_clip[:, :, :, :35])))
        self.assertTrue(torch.equal(portrait_clip[:, :, :, 125:], torch.zeros_like(portrait_clip[:, :, :, 125:])))

    def test_letterbox_rejects_non_rgb_and_wrong_sample_count(self):
        with self.assertRaisesRegex(ValueError, "Expected 16 sampled frames"):
            profile.frames_to_clip([np.zeros((20, 20, 3), dtype=np.uint8)] * 15,
                                   np=np, torch=torch, cv2=cv2)
        with self.assertRaisesRegex(ValueError, "uint8 RGB"):
            profile.frames_to_clip([np.zeros((20, 20, 4), dtype=np.uint8)] * 16,
                                   np=np, torch=torch, cv2=cv2)

    def test_harvard_class_stratification_is_repeatable_and_exact(self):
        fight_paths = [Path(f"fight_{index:04}.mp4") for index in range(616)]
        negative_paths = [Path(f"nonfight_{index:04}.mp4") for index in range(1510)]
        for paths, offset, expected in (
            (fight_paths, 1, {"train": 493, "val": 62, "test": 61}),
            (negative_paths, 0, {"train": 1208, "val": 151, "test": 151}),
        ):
            first = profile.stratified_random_splits(paths, 2026 + offset)
            second = profile.stratified_random_splits(paths, 2026 + offset)
            self.assertEqual(first, second)
            self.assertEqual({split: sum(value == split for value in first.values())
                              for split in profile.SPLITS}, expected)

    def test_live_status_keeps_initial_non_finite_metrics_json_safe(self):
        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            path = Path(directory) / "live_status.json"
            native._write_live(path, {"best_val_roc_auc": float("-inf"),
                                      "metrics": {"pending": float("nan"), "infinite": float("inf")}})
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsNone(saved["best_val_roc_auc"])
        self.assertEqual(saved["metrics"], {"pending": None, "infinite": None})


class HarvardLetterboxDashboardTests(unittest.TestCase):
    def test_arguments_keep_requested_defaults_and_allow_overrides_without_dancetrack(self):
        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            output_root = Path(directory) / "runs"
            with mock.patch.object(dashboard, "DEFAULT_HARVARD_LETTERBOX_OUTPUT_ROOT", output_root):
                args, run_dir = dashboard._harvard_letterbox_training_arguments(
                    {"epochs": 9, "patience": 3, "lr_reduce_after": 1, "head_lr": 0.0002},
                    Path(directory) / "pause.flag")
            parsed = native.build_parser().parse_args(args)
            self.assertEqual(parsed.epochs, 9)
            self.assertEqual(parsed.patience, 3)
            self.assertEqual(parsed.lr_reduce_after, 1)
            self.assertEqual(parsed.head_lr, 0.0002)
            self.assertEqual(parsed.dancetrack_manifest, "")
            self.assertEqual(parsed.initialization_mode, "kinetics400")
            self.assertNotIn("--base-checkpoint", args)
            self.assertTrue(run_dir.name.startswith("run_harvard_letterbox_ui_"))
            self.assertEqual(dashboard.HARVARD_LETTERBOX_DEFAULT_CONFIG["epochs"], 15)
            self.assertEqual(dashboard.HARVARD_LETTERBOX_DEFAULT_CONFIG["patience"], 4)
            self.assertEqual(dashboard.HARVARD_LETTERBOX_DEFAULT_CONFIG["lr_reduce_after"], 2)

    def test_resume_rejects_checkpoint_outside_page_output_root_or_wrong_protocol(self):
        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            root = Path(directory) / "runs"
            run = root / "run_harvard_letterbox_ui_resume"
            run.mkdir(parents=True)
            last = run / "last.pt"
            last.touch()
            bad_config = {
                "experiment_protocol": "wrong",
                "input_representation": "wrong",
                "epochs": 15,
                "patience": 4,
                "lr_scheduler_no_improvement_epochs": 2,
                "lr_scheduler_factor": 0.5,
                "min_lr": 1e-6,
                "min_delta": 0.001,
                "backbone_lr": 2e-5,
                "head_lr": 1e-4,
                "weight_decay": 1e-4,
                "grad_accum": 8,
                "window_batch_size": 2,
                "seed": 2026,
                "amp": False,
                "device": "cpu",
                "protocol": {},
                "initialization_mode": "kinetics400",
                "initialization": {"weights": "wrong"},
                "dataset_fingerprint": "fixture",
            }
            (run / "config.json").write_text(json.dumps(bad_config), encoding="utf-8")
            with mock.patch.object(dashboard, "DEFAULT_HARVARD_LETTERBOX_OUTPUT_ROOT", root):
                with self.assertRaisesRegex(ValueError, "不属于 Harvard/RWF/AIRT"):
                    dashboard._harvard_letterbox_training_arguments(
                        {"resume_checkpoint": str(last)}, Path(directory) / "pause")
                with self.assertRaisesRegex(ValueError, "inside the chosen output folder"):
                    dashboard._harvard_letterbox_training_arguments(
                        {"resume_checkpoint": str(Path(directory) / "outside" / "last.pt")},
                        Path(directory) / "pause")

    def test_harvard_inventory_matches_data_and_does_not_require_old_training_checkpoint(self):
        data = profile.quick_inventory()
        self.assertTrue(data["ready"])
        self.assertEqual(data["summary"]["total_bags"], 4449)
        self.assertEqual(data["summary"]["overall"]["train"]["bags"], 3358)
        self.assertEqual(data["summary"]["overall"]["val"]["bags"], 445)
        self.assertEqual(data["summary"]["overall"]["test"]["bags"], 646)
        self.assertEqual(data["missing_source_count"], 0)
        self.assertEqual(data["initialization"]["weights"], "MC3_18_Weights.KINETICS400_V1")
        self.assertNotIn("base_checkpoint", data)
        self.assertEqual(data["protocol"], profile.EXPERIMENT_PROTOCOL)

    def test_record_loader_preserves_audited_splits_and_airtlab_camera_pairs(self):
        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            root = Path(directory)
            dataset_root = root / "dataset"
            harvard_root = dataset_root / "Harvard Dataverse"
            manifest = root / "audited-manifest.csv"
            base_rows = []
            for split in profile.SPLITS:
                for class_name, label in (("Fight", 1), ("NonFight", 0)):
                    rwf_path = dataset_root / "RWF-2000" / split / f"{class_name}.mp4"
                    rwf_path.parent.mkdir(parents=True, exist_ok=True)
                    rwf_path.touch()
                    base_rows.append({"dataset": "RWF-2000", "split": split, "class_name": class_name,
                                      "label": label, "path": str(rwf_path), "source_group": f"rwf|{split}|{label}",
                                      "fps": 4, "frame_count": 16, "duration_seconds": 4,
                                      "eligible": 1})
                    for camera in ("Cam1", "Cam2"):
                        airt_path = dataset_root / "AIRTLab" / camera / split / f"{class_name}.mp4"
                        airt_path.parent.mkdir(parents=True, exist_ok=True)
                        airt_path.touch()
                        base_rows.append({"dataset": "AIRTLab", "split": split, "class_name": class_name,
                                          "label": label, "path": str(airt_path),
                                          "source_group": f"airt|{split}|{label}", "fps": 4,
                                          "frame_count": 16, "duration_seconds": 4, "eligible": 1})
            with manifest.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(base_rows[0]))
                writer.writeheader()
                writer.writerows(base_rows)
            for folder, class_name in (("Violence", "Fight"), ("NonViolence", "NonFight")):
                class_root = harvard_root / folder
                class_root.mkdir(parents=True)
                for index in range(10):
                    (class_root / f"{class_name}_{index:02}.mp4").touch()

            expected = {"Harvard Dataverse": {"Fight": 10, "NonFight": 10},
                        "RWF-2000": {"Fight": 3, "NonFight": 3},
                        "AIRTLab": {"Fight": 6, "NonFight": 6}}

            def fake_probe(path, dataset, split, class_name, _cv2, *, source_group=""):
                record = native._new_record(dataset, split, class_name, path, 4.0, 16, 4.0,
                                            source_group=source_group)
                row = {"dataset": dataset, "split": split, "class_name": class_name,
                       "label": native.SOURCE_LABELS[class_name], "path": str(path.resolve()),
                       "source_group": source_group, "fps": "4", "frame_count": 16,
                       "duration_seconds": "4", "eligible": 1}
                return record, row

            with (mock.patch.object(profile, "EXPECTED_COUNTS", expected),
                  mock.patch.object(native, "_probe_video", side_effect=fake_probe)):
                records, rows, metadata = profile.load_records(dataset_root=dataset_root,
                                                               base_manifest=manifest, seed=2026, cv2=cv2)

        self.assertEqual(len(records), 38)
        self.assertEqual(metadata["harvard_split_seed"], 2026)
        self.assertEqual({split: sum(r.dataset == "Harvard Dataverse" and r.split == split for r in records)
                          for split in profile.SPLITS}, {"train": 16, "val": 2, "test": 2})
        airt_groups = {}
        for record in records:
            if record.dataset == "AIRTLab":
                airt_groups.setdefault(record.source_group, []).append(record)
        self.assertTrue(all(len(group) == 2 and len({r.split for r in group}) == 1
                            and len({r.label for r in group}) == 1 for group in airt_groups.values()))
        self.assertEqual(len(rows), 38)

    def test_threshold_registry_and_model_catalog_accept_only_this_protocol(self):
        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            root = Path(directory)
            run = root / "run_letterbox"
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
                "best_epoch": 1, "status": "epoch_checkpoint",
            }), encoding="utf-8")
            (run / "config.json").write_text(json.dumps({
                "experiment_protocol": profile.EXPERIMENT_PROTOCOL,
                "preprocessing": {"window_seconds": 4, "stride_seconds": 2, "frames_per_window": 16},
            }), encoding="utf-8")
            thresholds = dashboard._inference_threshold_payload(checkpoint)
            with mock.patch.object(dashboard, "DEFAULT_HARVARD_LETTERBOX_OUTPUT_ROOT", root):
                models = dashboard._list_runs(root, include_nested=True)
        self.assertEqual(thresholds["protocol"], profile.EXPERIMENT_PROTOCOL)
        self.assertEqual(thresholds["threshold_modes"], ["sensitive", "moderate", "certain"])
        self.assertEqual(len(models), 1)
        self.assertTrue(models[0]["can_infer"])
        self.assertEqual(models[0]["input_representation"], profile.INPUT_REPRESENTATION)

    def test_status_reads_latest_epoch_threshold_metrics_and_saved_checkpoint(self):
        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            root = Path(directory)
            run = root / "run_harvard_letterbox_ui_status"
            epoch = run / "epoch_01"
            epoch.mkdir(parents=True)
            (epoch / "best_model.pt").touch()
            (run / "last.pt").touch()
            (run / "config.json").write_text(json.dumps({"epochs": 15, "patience": 4}), encoding="utf-8")
            (run / "live_status.json").write_text(json.dumps({"status": "paused", "epoch": 1,
                                                                "completed_epochs": 1,
                                                                "best_val_roc_auc": 0.73}), encoding="utf-8")
            with (run / "training_history.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["epoch", "val_roc_auc"])
                writer.writeheader()
                writer.writerow({"epoch": 1, "val_roc_auc": 0.73})
            expected_selection = {name: {"threshold": value} for name, value in
                                  (("sensitive", 0.2), ("moderate", 0.5), ("certain", 0.8))}
            expected_metrics = {name: {"f1": 0.7} for name in expected_selection}
            (epoch / "thresholds.json").write_text(json.dumps({"selection": expected_selection,
                                                                 "validation_metrics": expected_metrics}),
                                                     encoding="utf-8")
            state = dashboard.HarvardLetterboxTrainingState()
            with (mock.patch.object(dashboard, "DEFAULT_HARVARD_LETTERBOX_OUTPUT_ROOT", root),
                  mock.patch.object(dashboard, "HARVARD_LETTERBOX_STATE", state)):
                status = dashboard._harvard_letterbox_status_payload(str(run))
        self.assertEqual(status["status"], "resumable")
        self.assertEqual(status["epoch_validation_thresholds"], expected_selection)
        self.assertEqual(status["epoch_validation_metrics"], expected_metrics)
        self.assertEqual(status["completed_epochs"], 1)
        self.assertEqual(status["epoch_checkpoints"][0]["epoch"], 1)

    def test_page_markup_has_every_form_control_and_is_served(self):
        class IDs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.values = set()

            def handle_starttag(self, _tag, attrs):
                value = dict(attrs).get("id")
                if value:
                    self.values.add(value)

        parser = IDs()
        parser.feed(dashboard.HARVARD_LETTERBOX_HTML_PATH.read_text(encoding="utf-8"))
        expected_ids = {"epochs", "patience", "lr_reduce_after", "seed", "backbone_lr", "head_lr",
                        "weight_decay", "min_delta", "lr_factor", "min_lr", "grad_accum",
                        "window_batch_size", "startButton", "resumeButton", "pauseButton", "stopButton",
                        "inferButton", "datasetTable", "historyBody", "validationMetrics", "testMetrics"}
        self.assertTrue(expected_ids.issubset(parser.values))

        # Exercise the actual route dispatcher in-process; the managed Windows
        # sandbox prohibits loopback sockets, so do not weaken the test by
        # treating a failed HTTP connection as a page/API result.
        handler = object.__new__(dashboard.DashboardHandler)
        handler.path = "/harvard-letterbox-training"
        with mock.patch.object(dashboard.DashboardHandler, "_send_bytes", autospec=True) as send_bytes:
            dashboard.DashboardHandler.do_GET(handler)
        self.assertEqual(send_bytes.call_args.args[1],
                         dashboard.HARVARD_LETTERBOX_HTML_PATH.read_bytes())
        html = send_bytes.call_args.args[1].decode("utf-8")
        self.assertIn("Kinetics-400", html)
        self.assertIn("不加载任何旧实验的 Best checkpoint", html)

        handler.path = "/api/harvard-letterbox/summary"
        with mock.patch.object(dashboard.DashboardHandler, "_send_json", autospec=True) as send_json:
            dashboard.DashboardHandler.do_GET(handler)
        summary = send_json.call_args.args[1]
        self.assertTrue(summary["ready"])
        self.assertEqual(summary["summary"]["total_bags"], 4449)
        handler.path = "/api/harvard-letterbox/runs"
        with mock.patch.object(dashboard.DashboardHandler, "_send_json", autospec=True) as send_json:
            dashboard.DashboardHandler.do_GET(handler)
        self.assertIn("runs", send_json.call_args.args[1])
        handler.path = "/api/harvard-letterbox/status"
        with mock.patch.object(dashboard.DashboardHandler, "_send_json", autospec=True) as send_json:
            dashboard.DashboardHandler.do_GET(handler)
        self.assertIn("status", send_json.call_args.args[1])


class HarvardLetterboxTrainingEndToEndTests(unittest.TestCase):
    def _tiny_records(self, root: Path):
        records, rows = [], []
        cache_root = root / "reused-complete-cache"
        cache_root.mkdir()
        for split in profile.SPLITS:
            for dataset in profile.DATASET_NAMES:
                for label in (0, 1):
                    class_name = "Fight" if label else "NonFight"
                    path = root / "sources" / dataset.replace(" ", "_") / split / f"{class_name}.mp4"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(f"{dataset}-{split}-{label}".encode("utf-8"))
                    record = native._new_record(dataset, split, class_name, path, 4.0, 16, 4.0,
                                                source_group=f"{dataset}|{split}|{label}")
                    records.append(record)
                    rows.append({"dataset": dataset, "split": split, "class_name": class_name,
                                 "label": label, "path": str(path), "source_group": record.source_group,
                                 "fps": 4.0, "frame_count": 16, "duration_seconds": 4.0,
                                 "eligible": 1, "window_count": 1})
        # A complete ready cache exists before training starts. The smoke path
        # consumes it read-only and exercises the real letterbox bag builder.
        for index, record in enumerate(records):
            indices = tuple(sorted({int(value) for window in four._windows_for_record(record, training=False)
                                    for value in window.frame_indices}))
            rgb = np.full((len(indices), 96, 176, 3), 220 if record.label else 28, dtype=np.uint8)
            np.savez_compressed(cache_root / f"{index:03d}.npz",
                                frame_indices=np.asarray(indices, dtype=np.int32), frames=rgb)
        return records, rows, cache_root

    def _runtime_context(self, live_path: Path | None, cache_root: Path | None):
        stack = contextlib.ExitStack()
        native_names = ("PROTOCOL_ID", "INPUT_REPRESENTATION", "INPUT_PROTOCOL_ID", "DATASET_NAMES",
                        "load_records", "_experiment_protocol", "_install_runtime_protocol")
        for name in native_names:
            stack.enter_context(mock.patch.object(native, name, getattr(native, name)))
        core_names = ("SHORT_SIDE", "CROP_SIZE", "_record_bag_tensor", "_load_cache", "WINDOW_SECONDS",
                      "STRIDE_SECONDS", "NUM_FRAMES", "CACHE_PROTOCOL", "_preprocessing_protocol",
                      "_windows_for_record")
        for name in core_names:
            stack.enter_context(mock.patch.object(core, name, getattr(core, name)))
        stack.enter_context(mock.patch.object(four, "SUPPLEMENT_CACHE_ROOT", four.SUPPLEMENT_CACHE_ROOT))
        return stack

    def test_reused_cache_first_batch_validation_checkpoint_pause_resume_and_final_metrics(self):
        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            root = Path(directory)
            records, rows, cache_root = self._tiny_records(root)
            manifest = root / "base-manifest.csv"
            manifest.write_text("fixture manifest\n", encoding="utf-8")
            run_dir = root / "run_harvard_letterbox_ui_smoke"
            cache_dir = root / "profile-cache"
            pause_file = root / "pause-after-epoch-1.flag"
            pause_file.touch()
            live_path = run_dir / "live_status.json"
            metadata = {"base_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                        "split_policy": "smoke fixture: deterministic fixed splits"}
            first_batch_status = {"checked": False}

            def verify_prevalidation_status():
                saved = json.loads(live_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["status"], "running")
                self.assertEqual(saved["epoch"], 1)
                self.assertIsNone(saved["best_val_roc_auc"])
                first_batch_status["checked"] = True

            kinetics_weights = mock.Mock(url="https://weights.invalid/mc3_18-k400-fixture.pth")
            weights_enum = type("Weights", (), {"KINETICS400_V1": kinetics_weights})
            model_builder_calls = []

            def model_builder(weights=None):
                model_builder_calls.append(weights)
                callback = verify_prevalidation_status if not first_batch_status["checked"] else None
                return TinyClassifier(callback)

            def prepare_reused_cache(records_arg, _cache, _cv2, _np, logger, update_status,
                                     *, cache_budget_bytes):
                del cache_budget_bytes
                for index, record in enumerate(records_arg):
                    indices = tuple(sorted({int(value) for window in four._windows_for_record(record, training=False)
                                            for value in window.frame_indices}))
                    record.required_frame_indices = indices
                    record.legacy_cache_path = None
                    record.prior_supplement_cache_paths = ()
                    record.supplement_cache_path = cache_root / f"{index:03d}.npz"
                    record.cache_path = record.supplement_cache_path
                    self.assertTrue(record.cache_path.is_file(), "must enter training through a completed cache")
                logger.info("Regression fixture: reused %d/%d complete source frame caches", len(records_arg), len(records_arg))
                update_status(stage="复用已完成视频帧缓存", step=len(records_arg),
                              steps_total=len(records_arg), stage_progress=1.0)

            def load_reused_cache(record, np_module):
                with np_module.load(record.supplement_cache_path, allow_pickle=False) as saved:
                    frame_ids = saved["frame_indices"].astype(np_module.int64)
                    frame_values = saved["frames"].copy()
                return frame_values, {int(value): i for i, value in enumerate(frame_ids.tolist())}

            argv = ["--dataset-root", str(root), "--base-manifest", str(manifest),
                    "--dancetrack-manifest", "", "--initialization-mode", "kinetics400",
                    "--base-checkpoint", str(root / "must-not-be-loaded.pt"),
                    "--output-root", str(root / "output"), "--run-dir", str(run_dir),
                    "--cache-dir", str(cache_dir), "--pause-file", str(pause_file),
                    "--device", "cpu", "--allow-cpu", "--epochs", "2", "--patience", "4",
                    "--lr-reduce-after", "2", "--backbone-lr", "0.00002", "--head-lr", "0.0001",
                    "--weight-decay", "0.0001", "--grad-accum", "1", "--window-batch-size", "2",
                    "--seed", "2026", "--log-every", "1"]
            with self._runtime_context(live_path, cache_root), \
                    mock.patch.object(profile, "load_records", return_value=(records, rows, metadata)), \
                    mock.patch.object(native, "_prepare_video_cache", side_effect=prepare_reused_cache), \
                    mock.patch.object(native, "_load_native_cache", side_effect=load_reused_cache), \
                    mock.patch.object(native.shutil, "disk_usage", return_value=mock.Mock(free=8 * 1024**3)), \
                    mock.patch.object(core, "_import_runtime_dependencies",
                                      return_value=(cv2, np, torch, nn, (weights_enum, model_builder))):
                self.assertEqual(profile.main(argv), 0)
                self.assertTrue(first_batch_status["checked"], "first model forward must see JSON-safe epoch-1 status")
                self.assertEqual(model_builder_calls[0], kinetics_weights,
                                 "fresh run must initialize with the official Kinetics-400 MC3-18 weights")
                paused = json.loads(live_path.read_text(encoding="utf-8"))
                self.assertEqual(paused["status"], "paused")
                self.assertTrue((run_dir / "epoch_01" / "best_model.pt").is_file())
                self.assertTrue((run_dir / "last.pt").is_file())
                log = (run_dir / "run.log").read_text(encoding="utf-8")
                self.assertIn("reused 18/18 complete source frame caches", log)
                self.assertIn("EPOCH 1/2 COMPLETE", log)
                self.assertIn("SAFE PAUSE after epoch 1", log)
                self.assertFalse((run_dir / "final_report.json").exists())
                for handler in list(logging.getLogger("mc3_18_mil").handlers):
                    if isinstance(handler, logging.FileHandler):
                        logging.getLogger("mc3_18_mil").removeHandler(handler)
                        handler.close()

                resume_argv = list(argv)
                pause_index = resume_argv.index("--pause-file")
                resume_argv[pause_index + 1] = str(root / "pause-after-resume.flag")
                resume_argv.extend(["--resume", str(run_dir / "last.pt")])
                self.assertEqual(profile.main(resume_argv), 0)
                self.assertIsNone(model_builder_calls[1],
                                  "resume must restore the run checkpoint, not reinitialize Kinetics or an old Best")
                self.assertTrue((run_dir / "epoch_02" / "best_model.pt").is_file())
                report = json.loads((run_dir / "final_report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["status"], "completed")
                self.assertEqual(report["protocol"]["experiment_protocol"], profile.EXPERIMENT_PROTOCOL)
                self.assertEqual(report["dataset_summary"]["total_bags"], 18)
                self.assertEqual(report["initialization_mode"], "kinetics400")
                self.assertEqual(report["initialization"]["weights"], "MC3_18_Weights.KINETICS400_V1")
                self.assertNotIn("base_checkpoint", report)
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


class HarvardLetterboxInferenceTests(unittest.TestCase):
    def test_uploaded_video_uses_exact_4s16f2_letterbox_predictor(self):
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
                value = np.full((90, 160, 3), 50 + self.position, dtype=np.uint8)
                return True, value

            def release(self):
                self.released = True

        with tempfile.TemporaryDirectory(dir=profile.PROJECT_ROOT) as directory:
            root = Path(directory)
            video = root / "sample.mp4"
            video.touch()
            checkpoint = root / "best_model.pt"
            selection = {name: {"threshold": threshold} for name, threshold in
                         (("sensitive", 0.2), ("moderate", 0.5), ("certain", 0.8))}
            saved_protocol = {**profile.experiment_protocol(),
                              "experiment_protocol": profile.EXPERIMENT_PROTOCOL,
                              "input_modality": "native",
                              "input_representation": profile.INPUT_REPRESENTATION}
            torch.save({"deployment_checkpoint_version": 1, "model_name": "mc3_18",
                        "class_to_idx": core.CLASS_TO_LABEL, "state_dict": TinyClassifier().state_dict(),
                        "protocol": saved_protocol}, checkpoint)
            capture = Capture()
            progress = []
            fake_deps = (cv2, np, torch, nn, (None, lambda weights=None: TinyClassifier()))
            with (mock.patch.object(core, "_import_runtime_dependencies", return_value=fake_deps),
                  mock.patch.object(cv2, "VideoCapture", return_value=capture),
                  mock.patch.object(torch.cuda, "is_available", return_value=False)):
                result = dashboard._predict_video_checkpoint(
                    video, "sample.mp4", checkpoint, "moderate", 0.5,
                    lambda **values: progress.append(values))
        self.assertEqual(result["input_representation"], profile.INPUT_REPRESENTATION)
        self.assertEqual(result["input_size"], [160, 160])
        self.assertTrue(result["letterbox"])
        self.assertEqual(result["window_count"], 1)
        self.assertEqual(len(result["windows"]), 1)
        self.assertTrue(capture.released)
        self.assertEqual(progress[-1]["processed_windows"], 1)


if __name__ == "__main__":
    unittest.main()
