import json
import sys
import threading
import tempfile
import unittest
import urllib.request
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mc3_18_dashboard as dashboard  # noqa: E402
import mc3_18_full_frame_pipeline as full_frame  # noqa: E402


class FullFrameDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=full_frame.PROJECT_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output_root = self.root / "full-frame-runs"

    def _arguments(self, payload):
        with mock.patch.object(dashboard, "DEFAULT_FULL_FRAME_OUTPUT_ROOT", self.output_root):
            return dashboard._full_frame_training_arguments(payload, self.root / "pause.flag")

    @staticmethod
    def option(args, name):
        return args[args.index(name) + 1]

    def test_defaults_enforce_schedule_and_expose_adjustable_optimizer_values(self):
        args, run_dir = self._arguments({})
        self.assertEqual(run_dir.parent, self.output_root)
        self.assertEqual(self.option(args, "--epochs"), "15")
        self.assertEqual(self.option(args, "--patience"), "4")
        self.assertEqual(self.option(args, "--lr-reduce-after"), "2")
        self.assertEqual(self.option(args, "--lr-factor"), "0.5")
        self.assertEqual(self.option(args, "--backbone-lr"), "2e-05")
        self.assertEqual(self.option(args, "--head-lr"), "0.0001")
        self.assertEqual(self.option(args, "--weight-decay"), "0.0001")
        self.assertEqual(self.option(args, "--seed"), "2026")
        self.assertNotIn("--no-amp", args)

    def test_other_hyperparameters_are_editable_but_invalid_values_fail_before_spawn(self):
        args, _ = self._arguments({"backbone_lr": 1e-5, "head_lr": 5e-5, "weight_decay": 0.0002,
                                   "lr_factor": 0.25, "min_lr": 5e-7, "min_delta": 0.002,
                                   "grad_accum": 4, "window_batch_size": 3, "seed": 7, "amp": False})
        for key, expected in {"--backbone-lr": "1e-05", "--head-lr": "5e-05", "--weight-decay": "0.0002",
                              "--lr-factor": "0.25", "--min-lr": "5e-07", "--min-delta": "0.002",
                              "--grad-accum": "4", "--window-batch-size": "3", "--seed": "7"}.items():
            with self.subTest(option=key):
                self.assertEqual(self.option(args, key), expected)
        self.assertIn("--no-amp", args)
        for bad in ({"window_batch_size": 9}, {"grad_accum": 0}, {"backbone_lr": float("inf")},
                    {"lr_factor": 1}, {"min_lr": 0.001}, {"seed": -1}):
            with self.subTest(payload=bad), self.assertRaises(ValueError):
                self._arguments(bad)

    def test_resume_requires_profile_fingerprint_and_fixed_schedule(self):
        run_dir = self.output_root / "run_full_frame_ui_resume"
        run_dir.mkdir(parents=True)
        checkpoint = run_dir / "last.pt"
        checkpoint.touch()
        config = {"experiment_protocol": full_frame.EXPERIMENT_PROTOCOL,
                  "input_representation": full_frame.INPUT_REPRESENTATION,
                  "epochs": 15, "patience": 4, "lr_scheduler_no_improvement_epochs": 2,
                  "lr_scheduler_factor": 0.5, "min_lr": 1e-6, "min_delta": 0.001,
                  "backbone_lr": 2e-5, "head_lr": 1e-4, "weight_decay": 1e-4,
                  "grad_accum": 8, "window_batch_size": 2, "seed": 2026,
                  "amp": True, "device": "cuda:0", "protocol": full_frame.EXPERIMENT_PROTOCOL,
                  "initial_checkpoint_sha256": "base-hash", "dataset_fingerprint": "split-data-hash"}
        (run_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        with mock.patch.object(dashboard, "DEFAULT_FULL_FRAME_OUTPUT_ROOT", self.output_root):
            args, resumed = dashboard._full_frame_training_arguments(
                {"resume_checkpoint": str(checkpoint), "head_lr": 0.8, "seed": 3, "device": "cpu"},
                self.root / "pause.flag")
        self.assertEqual(resumed, run_dir)
        self.assertEqual(self.option(args, "--head-lr"), "0.0001")
        self.assertEqual(self.option(args, "--seed"), "2026")
        self.assertEqual(self.option(args, "--device"), "cuda")
        config["input_representation"] = "native_video"
        (run_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        with mock.patch.object(dashboard, "DEFAULT_FULL_FRAME_OUTPUT_ROOT", self.output_root):
            with self.assertRaisesRegex(ValueError, "15 epochs／早停4／学习率patience 2"):
                dashboard._full_frame_training_arguments({"resume_checkpoint": str(checkpoint)},
                                                         self.root / "pause.flag")

    def test_page_has_controls_and_numeric_defaults_within_declared_ranges(self):
        class PageParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = set()
                self.numeric_inputs = {}

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if values.get("id"):
                    self.ids.add(values["id"])
                if tag == "input" and values.get("type") == "number" and values.get("id"):
                    self.numeric_inputs[values["id"]] = values

        parser = PageParser()
        parser.feed(dashboard.FULL_FRAME_HTML_PATH.read_text(encoding="utf-8"))
        for required in ("startButton", "pauseButton", "resumeButton", "inferButton", "historyBody",
                         "validationMetrics", "testMetrics", "console", "datasetTable"):
            self.assertIn(required, parser.ids)
        for field, attrs in parser.numeric_inputs.items():
            with self.subTest(field=field):
                value = Decimal(attrs["value"])
                if attrs.get("min") is not None:
                    self.assertGreaterEqual(value, Decimal(attrs["min"]))
                if attrs.get("max") is not None:
                    self.assertLessEqual(value, Decimal(attrs["max"]))

    def test_full_frame_checkpoint_is_inference_catalog_with_calibrated_modes(self):
        run = self.output_root / "run_full_frame_ui_done"
        epoch = run / "epoch_01"
        epoch.mkdir(parents=True)
        (epoch / "best_model.pt").touch()
        modes = {name: {"threshold": threshold, "objective": name}
                 for name, threshold in (("sensitive", 0.2), ("moderate", 0.5), ("certain", 0.8))}
        (epoch / "thresholds.json").write_text(json.dumps({"protocol": full_frame.EXPERIMENT_PROTOCOL,
                                                            "selection": modes}), encoding="utf-8")
        (epoch / "final_report.json").write_text(json.dumps({
            "experiment_protocol": full_frame.EXPERIMENT_PROTOCOL,
            "input_representation": full_frame.INPUT_REPRESENTATION,
            "best_epoch": 1,
        }), encoding="utf-8")
        (run / "config.json").write_text(json.dumps({"experiment_protocol": full_frame.EXPERIMENT_PROTOCOL,
                                                        "protocol": {"window_seconds": 4,
                                                                     "stride_seconds": 2,
                                                                     "frames_per_window": 16}}),
                                           encoding="utf-8")
        with mock.patch.object(dashboard, "_inference_threshold_payload",
                               wraps=dashboard._inference_threshold_payload):
            catalog = dashboard._list_runs(self.output_root, include_nested=True)
        self.assertEqual(len(catalog), 1)
        self.assertTrue(catalog[0]["can_infer"])
        self.assertEqual(catalog[0]["input_representation"], full_frame.INPUT_REPRESENTATION)
        self.assertEqual(catalog[0]["threshold_modes"], ["sensitive", "moderate", "certain"])

    def test_full_frame_training_is_counted_as_shared_gpu_work(self):
        active = dashboard.FullFrameTrainingState()
        active.status = "running"
        active.process = mock.Mock(poll=mock.Mock(return_value=None))
        with mock.patch.object(dashboard, "FULL_FRAME_STATE", active):
            self.assertTrue(dashboard._mc3_training_active())
            self.assertFalse(dashboard._mc3_training_active(except_name="full_frame"))

    def test_dashboard_serves_the_new_page_and_live_inventory(self):
        # Regression traceback before the DanceTrack frame manifest was restored:
        #   File "model_code/tests/test_mc3_18_full_frame_dashboard.py", line 162
        #     urllib.request.urlopen(address + "/api/full-frame/summary", timeout=30)
        # urllib.error.HTTPError: HTTP Error 400: Bad Request
        server = dashboard._make_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        address = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(address + "/full-frame-training", timeout=10) as response:
            html = response.read().decode("utf-8")
        with urllib.request.urlopen(address + "/api/full-frame/summary", timeout=30) as response:
            inventory = json.loads(response.read())
        self.assertIn("全画面短边 112 训练", html)
        self.assertEqual(inventory["summary"]["total_bags"], 2905)
        self.assertEqual(inventory["summary"]["overall"]["train"]["bags"], 2083)
        self.assertEqual(inventory["summary"]["overall"]["val"]["bags"], 305)
        self.assertEqual(inventory["summary"]["overall"]["test"]["bags"], 517)
        self.assertTrue(inventory["base_checkpoint_exists"])
        self.assertIsInstance(inventory["ready"], bool)
        if not inventory["ready"]:
            self.assertGreater(inventory["missing_source_count_sampled"], 0)
            self.assertTrue(inventory.get("first_missing_sources"))


if __name__ == "__main__":
    unittest.main()
