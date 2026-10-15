import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mc3_18_dashboard as dashboard  # noqa: E402
import mc3_18_native_finetune_pipeline as native  # noqa: E402
import mc3_18_mil_pipeline as core  # noqa: E402

try:
    import torch  # noqa: E402
except ImportError:  # pragma: no cover
    torch = None


class NativeFineTuneDashboardConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output_root = self.root / "native-runs"

    def make_args(self, payload):
        with mock.patch.object(dashboard, "DEFAULT_NATIVE_FINETUNE_OUTPUT_ROOT", self.output_root):
            return dashboard._native_finetune_training_arguments(payload, self.root / "pause.flag")

    @staticmethod
    def option(args, name):
        return args[args.index(name) + 1]

    def test_defaults_lock_requested_schedule_and_reuse_base_hyperparameters(self):
        args, run_dir = self.make_args({})
        self.assertEqual(run_dir.parent, self.output_root)
        self.assertEqual(self.option(args, "--epochs"), "15")
        self.assertEqual(self.option(args, "--patience"), "4")
        self.assertEqual(self.option(args, "--lr-reduce-after"), "2")
        self.assertEqual(self.option(args, "--lr-factor"), "0.5")
        self.assertEqual(self.option(args, "--backbone-lr"), "2e-05")
        self.assertEqual(self.option(args, "--head-lr"), "0.0001")
        self.assertEqual(self.option(args, "--weight-decay"), "0.0001")
        self.assertEqual(self.option(args, "--grad-accum"), "8")
        self.assertEqual(self.option(args, "--window-batch-size"), "2")
        self.assertEqual(self.option(args, "--seed"), "2026")
        self.assertNotIn("--no-amp", args)

    def test_user_can_adjust_other_supported_hyperparameters(self):
        args, _ = self.make_args({"min_delta": 0.002, "backbone_lr": 1e-5, "head_lr": 5e-5,
                                  "weight_decay": 0.0002, "lr_factor": 0.25, "min_lr": 5e-7,
                                  "grad_accum": 4, "window_batch_size": 3, "seed": 7, "amp": False})
        expected = {"--epochs": "15", "--patience": "4", "--lr-reduce-after": "2",
                    "--min-delta": "0.002", "--backbone-lr": "1e-05", "--head-lr": "5e-05",
                    "--weight-decay": "0.0002", "--lr-factor": "0.25", "--min-lr": "5e-07",
                    "--grad-accum": "4", "--window-batch-size": "3", "--seed": "7"}
        for name, value in expected.items():
            with self.subTest(option=name):
                self.assertEqual(self.option(args, name), value)
        self.assertIn("--no-amp", args)

    def test_invalid_ranges_are_rejected_before_process_start(self):
        for payload in ({"window_batch_size": 9}, {"grad_accum": 0}, {"backbone_lr": float("inf")},
                        {"lr_factor": 1}, {"min_lr": 0.001}, {"seed": -1}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.make_args(payload)

    def test_resume_uses_immutable_saved_settings_and_rejects_wrong_schedule(self):
        run = self.output_root / "run_native_finetune_ui_resume"
        run.mkdir(parents=True)
        checkpoint = run / "last.pt"
        checkpoint.touch()
        config = {"epochs": 15, "patience": 4, "min_delta": 0.001, "backbone_lr": 2e-5,
                  "head_lr": 1e-4, "weight_decay": 1e-4, "grad_accum": 8,
                  "window_batch_size": 2, "seed": 2026, "lr_scheduler_factor": 0.5,
                  "lr_scheduler_no_improvement_epochs": 2, "min_lr": 1e-6,
                  "amp": True, "device": "cuda:0"}
        (run / "config.json").write_text(json.dumps(config), encoding="utf-8")
        args, resumed = self.make_args({"resume_checkpoint": str(checkpoint), "head_lr": 0.9,
                                        "seed": 99, "amp": False, "device": "cpu"})
        self.assertEqual(resumed, run)
        self.assertEqual(self.option(args, "--head-lr"), "0.0001")
        self.assertEqual(self.option(args, "--device"), "cuda")
        self.assertNotIn("--no-amp", args)
        config["patience"] = 6
        (run / "config.json").write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "15 epochs／4 patience／2 epochs"):
            self.make_args({"resume_checkpoint": str(checkpoint)})

    def test_status_surfaces_live_metrics_and_failed_run_error(self):
        run = self.output_root / "run_native_finetune_ui_failed"
        run.mkdir(parents=True)
        (run / "last.pt").touch()
        (run / "config.json").write_text(json.dumps({"epochs": 15}), encoding="utf-8")
        (run / "live_status.json").write_text(json.dumps({"status": "failed", "completed_epochs": 2,
                                                           "val_metrics_at_0_5": {"f1": 0.75},
                                                           "error": "sample decode failure"}), encoding="utf-8")
        state = dashboard.NativeFinetuneState()
        with (mock.patch.object(dashboard, "DEFAULT_NATIVE_FINETUNE_OUTPUT_ROOT", self.output_root),
              mock.patch.object(dashboard, "NATIVE_FINETUNE_STATE", state)):
            runs = dashboard._native_finetune_runs()
            status = dashboard._native_finetune_status_payload(str(run))
        self.assertEqual(runs[0]["status"], "failed")
        self.assertTrue(runs[0]["last_checkpoint"].endswith("last.pt"))
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["val_metrics_at_0_5"]["f1"], 0.75)
        self.assertEqual(status["error"], "sample decode failure")

    def test_epoch_checkpoint_is_listed_for_shared_video_inference(self):
        run = self.output_root / "run_native_finetune_ui_done"
        epoch = run / "epoch_03"
        epoch.mkdir(parents=True)
        (epoch / "best_model.pt").touch()
        selection = {name: {"threshold": threshold, "objective": name}
                     for name, threshold in (("sensitive", 0.2), ("moderate", 0.5), ("certain", 0.8))}
        (epoch / "thresholds.json").write_text(json.dumps({
            "protocol": "mc3-4s-2s-stride-16f-128short-112crop-short-tail-v1",
            "selection": selection,
        }), encoding="utf-8")
        (epoch / "final_report.json").write_text(json.dumps({
            "experiment_protocol": dashboard.NATIVE_FINETUNE_EXPERIMENT_PROTOCOL,
            "input_representation": "native_video", "best_epoch": 3,
        }), encoding="utf-8")
        (run / "config.json").write_text(json.dumps({"experiment_protocol": dashboard.NATIVE_FINETUNE_EXPERIMENT_PROTOCOL,
                                                       "preprocessing": {"window_seconds": 4, "stride_seconds": 2,
                                                                         "frames_per_window": 16}}), encoding="utf-8")

        models = dashboard._list_runs(self.output_root, include_nested=True)

        self.assertEqual(len(models), 1)
        self.assertTrue(models[0]["can_infer"])
        self.assertEqual(models[0]["threshold_modes"], ["sensitive", "moderate", "certain"])
        self.assertIn("Epoch 03", models[0]["name"])

    @unittest.skipIf(torch is None, "PyTorch is required to check deployment checkpoint inference dispatch")
    def test_native_finetune_checkpoint_uses_existing_4s16f2_inference_path(self):
        # Regression traceback before protocol metadata was decoupled from disk:
        #   File "model_code/mc3_18_native_finetune_pipeline.py", line 617
        #     protocol = dict(four._protocol())
        #   File "model_code/mc3_18_four_second_pipeline.py", line 149
        #     raise FileNotFoundError("Missing selected legacy datasets: ...")
        # FileNotFoundError: Missing selected legacy datasets:
        #   C:\\...\\GuardAId Video Classification Model Dataset\\ShanghaiTech
        class Model:
            def state_dict(self):
                return {"test_weight": torch.tensor([1.0])}

        checkpoint = self.root / "epoch_best.pt"
        thresholds = {key: {"threshold": value} for key, value in
                      (("sensitive", 0.2), ("moderate", 0.5), ("certain", 0.8))}
        with mock.patch.object(native.four, "LEGACY_DATASET_ROOT", self.root / "unmounted-legacy-data"):
            native._save_deployment(Model(), checkpoint, torch, 3, 0.91, thresholds)
        expected = {"prediction": "Fight"}
        fake_dependencies = (None, None, torch, torch.nn, (None, None))
        with (mock.patch.object(core, "_import_runtime_dependencies", return_value=fake_dependencies),
              mock.patch.object(dashboard, "_predict_video_four_second_checkpoint", return_value=expected) as infer):
            result = dashboard._predict_video_checkpoint(
                self.root / "video.mp4", "video.mp4", checkpoint, "moderate", 0.5, lambda **_values: None)
        self.assertEqual(result, expected)
        self.assertEqual(infer.call_count, 1)

    def test_page_has_valid_default_numeric_inputs_and_training_controls(self):
        class Parser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.inputs = {}
                self.ids = set()

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if values.get("id"):
                    self.ids.add(values["id"])
                if tag == "input" and values.get("type") == "number" and values.get("id"):
                    self.inputs[values["id"]] = values

        parser = Parser()
        html = dashboard.NATIVE_FINETUNE_HTML_PATH.read_text(encoding="utf-8")
        parser.feed(html)
        self.assertIn("startButton", parser.ids)
        self.assertIn("pauseButton", parser.ids)
        self.assertIn("resumeButton", parser.ids)
        self.assertIn("inferButton", parser.ids)
        for field_id, attrs in parser.inputs.items():
            with self.subTest(field=field_id):
                value = Decimal(attrs["value"])
                if attrs.get("min") is not None:
                    self.assertGreaterEqual(value, Decimal(attrs["min"]))
                if attrs.get("max") is not None:
                    self.assertLessEqual(value, Decimal(attrs["max"]))

    def test_live_dashboard_serves_page_and_read_only_dataset_summary(self):
        # Regression traceback when the read-only inventory request encountered
        # an unavailable dataset prerequisite:
        #   File "model_code/tests/test_mc3_18_native_finetune_dashboard.py", line 199
        #     urllib.request.urlopen(address + "/api/native-finetune/summary", timeout=10)
        # urllib.error.HTTPError: HTTP Error 400: Bad Request
        server = dashboard._make_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        address = f"http://127.0.0.1:{server.server_address[1]}"

        with urllib.request.urlopen(address + "/native-finetune", timeout=10) as response:
            html = response.read().decode("utf-8")
        self.assertIn("四数据集原生视频后训练", html)
        with urllib.request.urlopen(address + "/api/native-finetune/summary", timeout=10) as response:
            self.assertEqual(response.status, 200)
            summary = json.loads(response.read())
        if summary.get("summary"):
            self.assertEqual(summary["summary"]["total_bags"], 813)
            self.assertEqual(summary["summary"]["overall"]["train"]["bags"], 611)
            self.assertEqual(summary["summary"]["overall"]["val"]["bags"], 97)
            self.assertEqual(summary["summary"]["overall"]["test"]["bags"], 105)
        else:
            self.assertFalse(summary["ready"])
            self.assertIn("Dance Genre Estimation", summary["error"])
            self.assertTrue(native.DEFAULT_DANCETRACK_MANIFEST.is_file())


if __name__ == "__main__":
    unittest.main()
