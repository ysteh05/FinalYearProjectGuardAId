import base64
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yolov9_weapon_dashboard as dashboard  # noqa: E402


def _threshold_report() -> dict:
    return {
        "class_names": ["knife", "gun"],
        "profiles": [
            {"profile": "Sensitive", "thresholds": {"knife": 0.2, "gun": 0.18}},
            {"profile": "Moderate", "thresholds": {"knife": 0.4, "gun": 0.42}},
            {"profile": "Certain", "thresholds": {"knife": 0.7, "gun": 0.62}},
        ],
    }


class MallInferenceCatalogTests(unittest.TestCase):
    def test_catalog_lists_baseline_and_completed_finetune_with_model_specific_profiles(self):
        with tempfile.TemporaryDirectory(dir=dashboard.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            artifact = root / "baseline"
            artifact.mkdir()
            (artifact / "best_inference.pt").write_bytes(b"checkpoint")
            (artifact / "sensitivity_thresholds.json").write_text(json.dumps(_threshold_report()), encoding="utf-8")
            output = root / "mall-output"
            run_id = "mallft_20261010_120000_a1b2c3"
            run_dir = output / "runs" / run_id
            run_dir.mkdir(parents=True)
            (run_dir / "progress.json").write_text("{}", encoding="utf-8")
            (run_dir / "resolved_training_config.json").write_text(json.dumps({"class_names": ["knife", "gun"]}), encoding="utf-8")
            (run_dir / "best_inference.pt").write_bytes(b"checkpoint")
            (run_dir / "sensitivity_thresholds.json").write_text(json.dumps(_threshold_report()), encoding="utf-8")
            with (
                mock.patch.object(dashboard, "BREV_INFERENCE_ARTIFACT_DIR", artifact),
                mock.patch.object(dashboard, "DEPLOYED_SELECTED_SOURCES_ARTIFACT_DIR", artifact / "no-deployed-model"),
                mock.patch.object(dashboard, "MALL_FINETUNE_OUTPUT_ROOT", output),
                mock.patch.object(dashboard, "_full_v1_runs", return_value=[]),
                mock.patch.object(dashboard, "_mall_finetune_runs", return_value=[{"run_id": run_id, "status": "completed"}]),
            ):
                catalog = dashboard._mall_inference_model_catalog()
                checkpoint, profiles, selected = dashboard._resolve_mall_inference_model(run_id)

        self.assertEqual([item["model_id"] for item in catalog["models"]], [dashboard.BREV_INFERENCE_MODEL_ID, run_id])
        self.assertEqual(catalog["default_model_id"], run_id)
        self.assertEqual(checkpoint.name, "best_inference.pt")
        self.assertEqual(set(profiles), {"Sensitive", "Moderate", "Certain"})
        self.assertEqual(profiles["Moderate"], {"knife": 0.4, "gun": 0.42})
        self.assertEqual(selected["source"], "mall_finetune")

    def test_catalog_excludes_runs_without_complete_threshold_profiles(self):
        with tempfile.TemporaryDirectory(dir=dashboard.PROJECT_ROOT) as temporary:
            artifact = Path(temporary)
            (artifact / "best_inference.pt").write_bytes(b"checkpoint")
            invalid = {"class_names": ["knife", "gun"], "profiles": [{"profile": "Moderate", "thresholds": {"knife": 0.4, "gun": 0.4}}]}
            (artifact / "sensitivity_thresholds.json").write_text(json.dumps(invalid), encoding="utf-8")
            with (
                mock.patch.object(dashboard, "BREV_INFERENCE_ARTIFACT_DIR", artifact),
                mock.patch.object(dashboard, "DEPLOYED_SELECTED_SOURCES_ARTIFACT_DIR", artifact / "no-deployed-model"),
                mock.patch.object(dashboard, "_full_v1_runs", return_value=[]),
                mock.patch.object(dashboard, "_mall_finetune_runs", return_value=[]),
            ):
                catalog = dashboard._mall_inference_model_catalog()
        self.assertEqual(catalog["models"], [])
        self.assertIsNone(catalog["default_model_id"])

    def test_resolver_rejects_arbitrary_paths(self):
        with self.assertRaises(ValueError):
            dashboard._resolve_mall_inference_model("../../private/model.pt")

    def test_custom_thresholds_must_cover_both_classes_and_stay_in_range(self):
        profiles = {"Sensitive": {"knife": 0.2, "gun": 0.18}, "Moderate": {"knife": 0.4, "gun": 0.42}, "Certain": {"knife": 0.7, "gun": 0.62}}
        with (
            mock.patch.object(dashboard, "_current_status", return_value={"process_active": False}),
            mock.patch.object(dashboard, "_resolve_mall_inference_model", return_value=(Path("unused.pt"), profiles, {"label": "Fixture", "source": "test"})),
        ):
            with self.assertRaisesRegex(ValueError, "Knife 和 Gun"):
                dashboard._run_mall_finetune_inference({"model_id": "fixture", "threshold_mode": "Moderate", "thresholds": {"knife": 0.4}, "image_base64": ""})
            with self.assertRaisesRegex(ValueError, "0.001 到 1"):
                dashboard._run_mall_finetune_inference({"model_id": "fixture", "threshold_mode": "Moderate", "thresholds": {"knife": 1.1, "gun": 0.4}, "image_base64": ""})
        self.assertFalse(dashboard._INFERENCE_LOCK.locked())

    def test_invalid_upload_encoding_is_rejected_without_leaking_inference_lock(self):
        profiles = {"Sensitive": {"knife": 0.2, "gun": 0.18}, "Moderate": {"knife": 0.4, "gun": 0.42}, "Certain": {"knife": 0.7, "gun": 0.62}}
        with (
            mock.patch.object(dashboard, "_current_status", return_value={"process_active": False}),
            mock.patch.object(dashboard, "_resolve_mall_inference_model", return_value=(Path("unused.pt"), profiles, {"label": "Fixture", "source": "test"})),
        ):
            with self.assertRaisesRegex(ValueError, "编码无效"):
                dashboard._run_mall_finetune_inference({"model_id": "fixture", "threshold_mode": "Moderate", "image_base64": "%%%"})
        self.assertFalse(dashboard._INFERENCE_LOCK.locked())

    def test_real_inference_path_uses_calibration_nms_and_returns_image_dimensions(self):
        import io

        from PIL import Image

        output = io.BytesIO()
        Image.new("RGB", (80, 48), color=(20, 30, 40)).save(output, format="PNG")
        image_base64 = base64.b64encode(output.getvalue()).decode("ascii")
        profiles = {
            "Sensitive": {"knife": 0.2, "gun": 0.18},
            "Moderate": {"knife": 0.4, "gun": 0.42},
            "Certain": {"knife": 0.7, "gun": 0.62},
        }
        calls = {}

        class FakeRunner:
            class_names = ("knife", "gun")

            def __init__(self, checkpoint, yolov9_root):
                calls["checkpoint"] = checkpoint

            def load(self):
                calls["loaded"] = True

            def infer(self, frame, thresholds, *, candidate_floor, iou_threshold):
                calls["infer"] = {
                    "thresholds": thresholds,
                    "candidate_floor": candidate_floor,
                    "iou_threshold": iou_threshold,
                }
                calls["shape"] = frame.shape
                return []

            def annotate(self, frame, detections):
                return frame

        with (
            mock.patch.object(dashboard, "_current_status", return_value={"process_active": False}),
            mock.patch.object(dashboard, "_resolve_mall_inference_model", return_value=(Path("fixture.pt"), profiles, {"label": "Fixture", "source": "test"})),
            mock.patch.object(dashboard, "default_training_config", return_value={"yolov9_root": "fixture"}),
            mock.patch.dict(sys.modules, {"yolov9_weapon_infer": types.SimpleNamespace(Yolov9WeaponRunner=FakeRunner)}),
        ):
            result = dashboard._run_mall_finetune_inference({
                "model_id": "fixture",
                "threshold_mode": "Moderate",
                "thresholds": profiles["Moderate"],
                "image_base64": image_base64,
            })

        self.assertTrue(calls["loaded"])
        self.assertEqual(calls["shape"][:2], (48, 80))
        self.assertEqual(calls["infer"]["candidate_floor"], 0.001)
        self.assertEqual(calls["infer"]["iou_threshold"], dashboard.MALL_INFERENCE_NMS_IOU)
        self.assertEqual(result["nms_iou_threshold"], dashboard.MALL_INFERENCE_NMS_IOU)
        self.assertEqual((result["image_width"], result["image_height"]), (80, 48))


if __name__ == "__main__":
    unittest.main()
