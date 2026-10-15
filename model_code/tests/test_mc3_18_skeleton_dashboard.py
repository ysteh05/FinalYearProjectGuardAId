import json
import sys
import tempfile
import unittest
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mc3_18_dashboard as dashboard  # noqa: E402
import mc3_18_mil_pipeline as core  # noqa: E402
import mc3_18_skeleton_four_second_pipeline as skeleton  # noqa: E402

try:
    import torch  # noqa: E402
except ImportError:  # pragma: no cover - minimal test environments
    torch = None


class SkeletonDashboardConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output_root = self.root / "skeleton-runs"

    def make_args(self, payload):
        with mock.patch.object(dashboard, "DEFAULT_SKELETON_OUTPUT_ROOT", self.output_root):
            return dashboard._skeleton_training_arguments(payload, self.root / "pause.flag")

    @staticmethod
    def option(args, name):
        return args[args.index(name) + 1]

    def test_defaults_match_latest_skeleton_limits_and_half_4s16f2_learning_rates(self):
        args, run_dir = self.make_args({})

        self.assertEqual(run_dir.parent, self.output_root)
        self.assertEqual(self.option(args, "--epochs"), "15")
        self.assertEqual(self.option(args, "--patience"), "4")
        self.assertEqual(self.option(args, "--backbone-lr"), "1e-05")
        self.assertEqual(self.option(args, "--head-lr"), "5e-05")
        self.assertEqual(self.option(args, "--lr-reduce-after"), "2")
        self.assertEqual(self.option(args, "--lr-factor"), "0.5")
        self.assertEqual(self.option(args, "--weight-decay"), "0.0001")
        self.assertEqual(self.option(args, "--grad-accum"), "8")
        self.assertEqual(self.option(args, "--window-batch-size"), "2")
        self.assertEqual(self.option(args, "--seed"), "2026")

    def test_user_hyperparameter_overrides_are_passed_to_training_process(self):
        payload = {
            "epochs": 7, "patience": 3, "min_delta": 0.002,
            "backbone_lr": 0.000012, "head_lr": 0.00006, "weight_decay": 0.0002,
            "lr_reduce_after": 4, "lr_factor": 0.25, "min_lr": 0.0000005,
            "grad_accum": 5, "window_batch_size": 3, "seed": 1234,
        }
        args, _ = self.make_args(payload)

        expected = {
            "--epochs": "7", "--patience": "3", "--min-delta": "0.002",
            "--backbone-lr": "1.2e-05", "--head-lr": "6e-05", "--weight-decay": "0.0002",
            "--lr-reduce-after": "4", "--lr-factor": "0.25", "--min-lr": "5e-07",
            "--grad-accum": "5", "--window-batch-size": "3", "--seed": "1234",
        }
        for key, value in expected.items():
            with self.subTest(option=key):
                self.assertEqual(self.option(args, key), value)

    def test_invalid_integer_range_and_scheduler_values_are_rejected(self):
        for payload in (
            {"epochs": 3.5},
            {"window_batch_size": 9},
            {"lr_factor": 1.0},
            {"min_lr": 0.00002},
            {"head_lr": float("inf")},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.make_args(payload)

    def test_resume_uses_saved_config_instead_of_form_values(self):
        run_dir = self.output_root / "run_skeleton_ui_resume"
        run_dir.mkdir(parents=True)
        checkpoint = run_dir / "last.pt"
        checkpoint.touch()
        saved_config = {
            "epochs": 9, "patience": 4, "min_delta": 0.001,
            "backbone_lr": 1e-5, "head_lr": 5e-5, "weight_decay": 1e-4,
            "grad_accum": 8, "window_batch_size": 2, "seed": 2026,
            "lr_scheduler_factor": 0.5, "lr_scheduler_no_improvement_epochs": 2,
            "min_lr": 1e-6, "amp": True, "device": "auto",
        }
        (run_dir / "config.json").write_text(json.dumps(saved_config), encoding="utf-8")

        args, returned_run = self.make_args({
            "resume_checkpoint": str(checkpoint), "epochs": 99, "patience": 99,
            "backbone_lr": 0.5, "head_lr": 0.5, "device": "cpu", "amp": False,
        })

        self.assertEqual(returned_run, run_dir)
        self.assertEqual(self.option(args, "--epochs"), "9")
        self.assertEqual(self.option(args, "--patience"), "4")
        self.assertEqual(self.option(args, "--backbone-lr"), "1e-05")
        self.assertEqual(args[args.index("--device") + 1], "auto")
        self.assertNotIn("--allow-cpu", args)
        self.assertNotIn("--no-amp", args)

    def test_failed_run_error_and_epoch_count_are_returned_to_dashboard(self):
        run_dir = self.output_root / "run_skeleton_ui_failed"
        run_dir.mkdir(parents=True)
        (run_dir / "config.json").write_text(json.dumps({"epochs": 15}), encoding="utf-8")
        (run_dir / "live_status.json").write_text(json.dumps({
            "status": "failed", "completed_epochs": 0, "error": "manifest mismatch",
        }), encoding="utf-8")
        state = dashboard.SkeletonTrainingState()

        with (mock.patch.object(dashboard, "DEFAULT_SKELETON_OUTPUT_ROOT", self.output_root),
              mock.patch.object(dashboard, "SKELETON_STATE", state)):
            runs = dashboard._skeleton_runs()
            status = dashboard._skeleton_status_payload(str(run_dir))

        self.assertEqual(runs[0]["status"], "failed")
        self.assertEqual(runs[0]["epochs"], 15)
        self.assertEqual(runs[0]["error"], "manifest mismatch")
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["error"], "manifest mismatch")

    def test_default_numeric_form_values_pass_html_constraints(self):
        class NumberInputParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.inputs = {}

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if tag == "input" and values.get("type") == "number" and values.get("id"):
                    self.inputs[values["id"]] = values

        parser = NumberInputParser()
        parser.feed(dashboard.SKELETON_HTML_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(parser.inputs), 12)
        for field_id, attrs in parser.inputs.items():
            with self.subTest(field=field_id):
                value = Decimal(attrs["value"])
                minimum = Decimal(attrs["min"]) if attrs.get("min") is not None else value
                maximum = Decimal(attrs["max"]) if attrs.get("max") is not None else value
                self.assertGreaterEqual(value, minimum)
                self.assertLessEqual(value, maximum)
                step = attrs.get("step", "1")
                if step != "any":
                    self.assertEqual((value - minimum) % Decimal(step), Decimal(0))


class SkeletonManifestSchemaTests(unittest.TestCase):
    def test_airtlab_windows_serialize_through_shared_manifest_schema(self):
        raw_window = {
            "window_index": "0", "window_start_seconds": "0.0", "window_end_seconds": "4.0",
            "source_frame_indices_list": list(range(16)),
            "frame_paths_list": [f"frames/{index:04d}.png" for index in range(16)],
        }
        normalized = skeleton._normalise_air_windows([raw_window])
        record = core.VideoRecord(
            dataset="AIRTLab", split="train", class_name="Fight", label=1,
            path=Path("sample.mp4"), fps=30.0, frame_count=120, duration=4.0,
            size_bytes=1, mtime_ns=0,
        )
        sample = skeleton.SkeletonSample(
            record=record, source_relative="sample.mp4", group_id="sample", camera="cam1",
            source_sha256="abc", window_rows=normalized, frame_paths_by_index={},
        )

        rows = skeleton._window_manifest([sample])

        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["source_frame_indices"]), list(range(16)))
        self.assertEqual(json.loads(rows[0]["skeleton_frame_paths"]), raw_window["frame_paths_list"])
        self.assertEqual(rows[0]["window_start_seconds"], "0.0")
        self.assertEqual(rows[0]["window_end_seconds"], "4.0")

    def test_airtlab_windows_with_missing_frames_fail_preflight(self):
        with self.assertRaisesRegex(ValueError, "exactly 16"):
            skeleton._normalise_air_windows([{
                "source_frame_indices_list": list(range(15)),
                "frame_paths_list": [f"frames/{index:04d}.png" for index in range(15)],
            }])


class SkeletonLiveStatusJsonTests(unittest.TestCase):
    def test_initial_epoch_progress_writes_non_finite_auc_as_json_null(self):
        with tempfile.TemporaryDirectory(dir=skeleton.PROJECT_ROOT) as temp_dir:
            status_path = Path(temp_dir) / "live_status.json"
            initial_state = {
                "status": "training",
                "completed_epochs": 0,
                "best_val_roc_auc": -float("inf"),
                "nested": {"nan_metric": float("nan"), "positive_inf": float("inf")},
                "metric_history": [float("-inf"), float("nan"), 0.5],
                "finite_metric": 0.75,
            }

            skeleton._write_live(status_path, initial_state)

            written = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertIsNone(written["best_val_roc_auc"])
            self.assertIsNone(written["nested"]["nan_metric"])
            self.assertIsNone(written["nested"]["positive_inf"])
            self.assertEqual(written["metric_history"], [None, None, 0.5])
            self.assertEqual(written["finite_metric"], 0.75)
            self.assertEqual(initial_state["best_val_roc_auc"], -float("inf"))

    def test_atomic_json_retries_transient_windows_replace_lock(self):
        with tempfile.TemporaryDirectory(dir=skeleton.PROJECT_ROOT) as temp_dir:
            status_path = Path(temp_dir) / "live_status.json"
            real_replace = skeleton.os.replace
            attempts = 0

            def fail_once(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError("transient file lock")
                return real_replace(source, destination)

            with (mock.patch.object(skeleton.os, "replace", side_effect=fail_once),
                  mock.patch.object(skeleton.time, "sleep") as sleep):
                skeleton._atomic_json(status_path, {"status": "running", "epoch": 6})

            self.assertEqual(attempts, 2)
            sleep.assert_called_once()
            self.assertEqual(json.loads(status_path.read_text(encoding="utf-8")),
                             {"status": "running", "epoch": 6})
            self.assertEqual(list(Path(temp_dir).glob("*.tmp")), [])

    def test_persistently_locked_live_status_does_not_abort_training_writer(self):
        with tempfile.TemporaryDirectory(dir=skeleton.PROJECT_ROOT) as temp_dir:
            status_path = Path(temp_dir) / "live_status.json"
            status_path.write_text('{"status":"last-good"}', encoding="utf-8")

            with (mock.patch.object(skeleton.os, "replace", side_effect=PermissionError("locked")) as replace,
                  mock.patch.object(skeleton.time, "sleep"),
                  mock.patch.object(skeleton.logging.getLogger("mc3_skeleton_four_source"), "warning") as warning):
                skeleton._write_live(status_path, {"status": "running", "epoch": 6})

            self.assertEqual(replace.call_count, 8)
            warning.assert_called_once()
            self.assertEqual(json.loads(status_path.read_text(encoding="utf-8")), {"status": "last-good"})
            self.assertEqual(list(Path(temp_dir).glob("*.tmp")), [])


class SkeletonCheckpointArtifactTests(unittest.TestCase):
    @unittest.skipIf(torch is None, "PyTorch is required to verify checkpoint serialization")
    def test_epoch_checkpoint_serializes_with_raw_dataset_roots(self):
        with tempfile.TemporaryDirectory(dir=skeleton.PROJECT_ROOT) as temp_dir:
            root = Path(temp_dir)
            pose_root = root / "pose-data"
            with (
                mock.patch.object(skeleton.four, "LEGACY_DATASET_ROOT", pose_root),
                mock.patch.object(skeleton.four, "AIRTLAB_ROOT", root / "pose-airtlab"),
                mock.patch.object(skeleton.four, "SUPPLEMENT_CACHE_ROOT", root / "cache"),
                mock.patch.object(skeleton.four, "_install_protocol") as install_protocol,
                mock.patch.object(
                    skeleton.four,
                    "_selected_legacy_roots",
                    return_value=[("RWF-2000", root / "RWF-2000")],
                ) as selected_legacy_roots,
            ):
                protocol = skeleton._install_skeleton_runtime_protocol(root / "cache")
                install_protocol.assert_called_once_with()
                self.assertEqual(skeleton.four.LEGACY_DATASET_ROOT, skeleton.DATA_ROOT)
                self.assertEqual(skeleton.four.AIRTLAB_ROOT, skeleton.air.RAW_VIDEO_ROOT)
                self.assertEqual(protocol["source_roots"]["legacy"], str(skeleton.DATA_ROOT.resolve()))
                self.assertEqual(protocol["source_roots"]["airtlab"], str(skeleton.air.RAW_VIDEO_ROOT.resolve()))

                validation_predictions = [
                    {"label": 0, "probability_fight": 0.1},
                    {"label": 1, "probability_fight": 0.9},
                ]
                checkpoint = skeleton._epoch_artifact(
                    root / "run",
                    epoch=1,
                    model=torch.nn.Linear(1, 1),
                    torch=torch,
                    thresholds={"moderate": {"threshold": 0.5}},
                    val_predictions=validation_predictions,
                    val_auc=0.9,
                )
                payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
                self.assertEqual(payload["protocol"]["experiment_protocol"], skeleton.PROTOCOL_ID)
                self.assertEqual(payload["protocol"]["best_epoch"], 1)
                self.assertEqual(
                    payload["protocol"]["source_roots"]["legacy"],
                    str(skeleton.DATA_ROOT.resolve()),
                )
                self.assertEqual(protocol["legacy_datasets"],
                                 ["RWF-2000", "Real Life Violence Dataset", "ShanghaiTech"])
                self.assertEqual(payload["protocol"]["legacy_datasets"], protocol["legacy_datasets"])
                self.assertEqual(selected_legacy_roots.call_count, 0)


if __name__ == "__main__":
    unittest.main()
