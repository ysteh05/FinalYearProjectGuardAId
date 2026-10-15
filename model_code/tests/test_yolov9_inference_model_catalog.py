import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yolov9_weapon_dashboard as dashboard  # noqa: E402


class InferenceModelCatalogTests(unittest.TestCase):
    def test_imported_brev_model_is_listed_without_becoming_a_training_run(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact_dir = Path(directory)
            (artifact_dir / "best_inference.pt").write_bytes(b"checkpoint")
            (artifact_dir / "sensitivity_thresholds.json").write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(dashboard, "BREV_INFERENCE_ARTIFACT_DIR", artifact_dir),
                mock.patch.object(dashboard, "DEPLOYED_SELECTED_SOURCES_ARTIFACT_DIR", artifact_dir / "no-deployed-model"),
                mock.patch.object(dashboard, "_full_v1_runs", return_value=[]),
            ):
                catalog = dashboard._full_v1_inference_models()
                resolved = dashboard._resolve_full_v1_inference_model(dashboard.BREV_INFERENCE_MODEL_ID)

        self.assertEqual(catalog["default_model_id"], dashboard.BREV_INFERENCE_MODEL_ID)
        self.assertEqual(catalog["models"][0]["source"], "imported")
        self.assertEqual(resolved[0].name, "best_inference.pt")
        self.assertEqual(resolved[1].name, "sensitivity_thresholds.json")
        self.assertEqual(resolved[3], "imported")

    def test_imported_model_is_hidden_if_its_calibration_report_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact_dir = Path(directory)
            (artifact_dir / "best_inference.pt").write_bytes(b"checkpoint")
            with (
                mock.patch.object(dashboard, "BREV_INFERENCE_ARTIFACT_DIR", artifact_dir),
                mock.patch.object(dashboard, "DEPLOYED_SELECTED_SOURCES_ARTIFACT_DIR", artifact_dir / "no-deployed-model"),
                mock.patch.object(dashboard, "_full_v1_runs", return_value=[]),
            ):
                catalog = dashboard._full_v1_inference_models()
                with self.assertRaises(FileNotFoundError):
                    dashboard._resolve_full_v1_inference_model(dashboard.BREV_INFERENCE_MODEL_ID)

        self.assertEqual(catalog["models"], [])
        self.assertIsNone(catalog["default_model_id"])

    def test_model_id_cannot_supply_an_arbitrary_checkpoint_path(self):
        with self.assertRaises(ValueError):
            dashboard._resolve_full_v1_inference_model("../../private/model.pt")

    def test_deployed_selected_sources_model_is_default_in_weapon_inference_catalogs(self):
        threshold_report = {
            "class_names": ["knife", "gun"],
            "profiles": [
                {"profile": "Sensitive", "thresholds": {"knife": 0.28, "gun": 0.13}},
                {"profile": "Moderate", "thresholds": {"knife": 0.52, "gun": 0.36}},
                {"profile": "Certain", "thresholds": {"knife": 0.66, "gun": 0.64}},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deployed = root / "deployed"
            deployed.mkdir()
            (deployed / "best_inference.pt").write_bytes(b"checkpoint")
            (deployed / "sensitivity_thresholds.json").write_text(json.dumps(threshold_report), encoding="utf-8")
            with (
                mock.patch.object(dashboard, "BREV_INFERENCE_ARTIFACT_DIR", root / "no-brev-model"),
                mock.patch.object(dashboard, "DEPLOYED_SELECTED_SOURCES_ARTIFACT_DIR", deployed),
                mock.patch.object(dashboard, "_full_v1_runs", return_value=[]),
                mock.patch.object(dashboard, "_mall_finetune_runs", return_value=[]),
            ):
                full_catalog = dashboard._full_v1_inference_models()
                mall_catalog = dashboard._mall_inference_model_catalog()
                full_resolved = dashboard._resolve_full_v1_inference_model(dashboard.DEPLOYED_SELECTED_SOURCES_MODEL_ID)
                mall_resolved = dashboard._resolve_mall_inference_model(dashboard.DEPLOYED_SELECTED_SOURCES_MODEL_ID)

        self.assertEqual(full_catalog["default_model_id"], dashboard.DEPLOYED_SELECTED_SOURCES_MODEL_ID)
        self.assertEqual(mall_catalog["default_model_id"], dashboard.DEPLOYED_SELECTED_SOURCES_MODEL_ID)
        self.assertEqual(full_resolved[0], deployed / "best_inference.pt")
        self.assertEqual(mall_resolved[0], deployed / "best_inference.pt")
        self.assertEqual(mall_resolved[1]["Moderate"], {"knife": 0.52, "gun": 0.36})

    def test_brev_threshold_report_contains_all_three_two_class_profiles(self):
        report_path = dashboard.BREV_INFERENCE_ARTIFACT_DIR / "sensitivity_thresholds.json"
        if not report_path.is_file():
            self.skipTest("BREV artifact is not present in this checkout")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        profiles = {item["profile"]: item["thresholds"] for item in report["profiles"]}

        self.assertEqual(set(profiles), {"Sensitive", "Moderate", "Certain"})
        for thresholds in profiles.values():
            self.assertEqual(set(thresholds), {"knife", "gun"})
            self.assertTrue(all(0.0 <= float(value) <= 1.0 for value in thresholds.values()))


if __name__ == "__main__":
    unittest.main()
