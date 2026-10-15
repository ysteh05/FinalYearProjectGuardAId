from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yolov9_selected_sources_finetune as recipe  # noqa: E402
import yolov9_weapon_dashboard as dashboard  # noqa: E402
import yolov9_weapon_hooks as hooks  # noqa: E402
import yolov9_weapon_pipeline as pipeline  # noqa: E402
from yolov9_expanded_negative_finetune import HARD_NEGATIVE_PROFILE_ID, MANIFEST_FIELDS  # noqa: E402


class SelectedSourcesDatasetTests(unittest.TestCase):
    def _image(self, path: Path, value: int) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (24, 18), (value % 255, (value * 3) % 255, (value * 7) % 255)).save(path)
        return path

    def _fixture_report(self, root: Path, *, cross_split_duplicate: bool = False) -> dict:
        cache = root / "base-cache"
        rows = []
        serial = 0
        for source in recipe.SELECTED_SOURCES:
            for split in recipe.SPLITS:
                serial += 1
                image = self._image(cache / "images" / split / f"{source}_{split}.jpg", serial)
                label = cache / "labels" / split / f"{source}_{split}.txt"
                label.parent.mkdir(parents=True, exist_ok=True)
                has_box = source == "knife_dataset" and split != "test" or source == "long_gun_dataset" and split == "train"
                label.write_text("0 0.5 0.5 0.2 0.2\n" if has_box else "", encoding="utf-8")
                classes = "0" if source == "knife_dataset" and split != "test" else "1" if source == "long_gun_dataset" and split == "train" else ""
                negative = not bool(classes)
                rows.append({
                    "source": source,
                    "source_split": split,
                    "split": split,
                    "source_image": str(image),
                    "source_label": str(label),
                    "image": str(image),
                    "label": str(label),
                    "image_sha256": f"image-{serial}",
                    "pixel_sha256": "same-cross-split" if cross_split_duplicate and source == "airt_lab_nonviolent" and split in {"train", "val"} else f"pixel-{serial}",
                    "label_sha256": f"label-{serial}",
                    "image_width": 24,
                    "image_height": 18,
                    "exif_orientation": 1,
                    "link_mode": "fixture",
                    "classes": classes,
                    "box_count": int(bool(classes)),
                    "negative": negative,
                })
        excluded = self._image(cache / "images" / "train" / "v11.jpg", 240)
        excluded_label = cache / "labels" / "train" / "v11.txt"
        excluded_label.write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
        rows.append({
            "source": "weapon_dataset_v11", "source_split": "train", "split": "train",
            "source_image": str(excluded), "source_label": str(excluded_label),
            "image": str(excluded), "label": str(excluded_label), "image_sha256": "v11",
            "pixel_sha256": "v11", "label_sha256": "v11-label", "image_width": 24,
            "image_height": 18, "exif_orientation": 1, "link_mode": "fixture",
            "classes": "0", "box_count": 1, "negative": False,
        })
        manifest = cache / "manifest.csv"
        with manifest.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return {
            "profile": HARD_NEGATIVE_PROFILE_ID,
            "fingerprint": "base-fixture",
            "cache_root": str(cache),
            "manifest": str(manifest),
            "selected_sources": [*recipe.SELECTED_SOURCES, "weapon_dataset_v11", "od_knife_detection", "od_pistol_detection"],
            "negative_label_assumption": "Fixture negatives are empty-label samples.",
        }

    def test_selected_cache_contains_only_requested_sources_and_preserves_splits(self):
        with tempfile.TemporaryDirectory(dir=recipe.PROJECT_ROOT) as temp:
            root = Path(temp)
            base_report = self._fixture_report(root)
            report = recipe.build_selected_source_dataset(
                output_root=root / "selected-output",
                base_output_root=root / "unused-base",
                base_report=base_report,
            )
            self.assertEqual(report["profile"], recipe.PROFILE_ID)
            self.assertEqual(report["images"], {"train": 9, "val": 9, "test": 9})
            self.assertEqual(report["total_images"], 27)
            self.assertEqual(report["selected_sources"], list(recipe.SELECTED_SOURCES))
            self.assertEqual(report["excluded_sources"], ["od_knife_detection", "od_pistol_detection", "weapon_dataset_v11"])
            self.assertEqual(report["class_counts"], {"knife": 2, "gun": 1})
            self.assertEqual(report["negative_source_total"], 24)
            with Path(report["manifest"]).open("r", encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 27)
            self.assertEqual({row["source"] for row in rows}, set(recipe.SELECTED_SOURCES))
            for row in rows:
                self.assertEqual(row["source_split"], row["split"])
                self.assertTrue(Path(row["image"]).is_file())
                self.assertTrue(Path(row["label"]).is_file())
            self.assertIn("0: knife", Path(report["data_yaml"]).read_text(encoding="utf-8"))

    def test_cross_split_duplicate_pixels_fail_closed(self):
        with tempfile.TemporaryDirectory(dir=recipe.PROJECT_ROOT) as temp:
            root = Path(temp)
            base_report = self._fixture_report(root, cross_split_duplicate=True)
            with self.assertRaisesRegex(ValueError, "crosses dataset splits"):
                recipe.build_selected_source_dataset(
                    output_root=root / "selected-output",
                    base_output_root=root / "unused-base",
                    base_report=base_report,
                )

    def test_page_defaults_and_pipeline_profile_are_fixed(self):
        defaults = dashboard.SELECTED_SOURCES_MODEL_DEFAULTS
        self.assertEqual(defaults["epochs_cap"], 10)
        self.assertEqual(defaults["patience"], 3)
        self.assertEqual(defaults["early_stop_metric"], "f05_fitness_mean")
        self.assertEqual(defaults["close_mosaic_epochs"], 3)
        self.assertEqual(defaults["batch_size"], 8)
        self.assertEqual(defaults["workers"], 2)
        self.assertEqual(defaults["lr0"], 0.0001)
        self.assertEqual(pipeline.SELECTED_SOURCES_FINETUNE_PROFILE, recipe.PROFILE_ID)
        self.assertIn(recipe.PROFILE_ID, pipeline.FINETUNE_PROFILES)
        self.assertEqual(dashboard._safe_run_dir("sel50_20261011_123456_abcdef").parent, dashboard.SELECTED_SOURCES_FINETUNE_OUTPUT_ROOT / "runs")

    def test_existing_nine_source_manifest_totals(self):
        summary = recipe.summarize_existing_sources()
        self.assertTrue(summary["ready"])
        self.assertEqual(summary["images"], {"train": 10585, "val": 1487, "test": 2221})
        self.assertEqual(summary["total_images"], 14293)
        self.assertEqual(summary["class_counts"], {"knife": 2180, "gun": 3384})
        self.assertEqual(summary["negative_source_total"], 9081)

    def test_recommended_finetune_config_passes_pipeline_validation(self):
        initial_checkpoint = dashboard.BREV_INFERENCE_ARTIFACT_DIR / "best_training.pt"
        if not initial_checkpoint.is_file():
            self.skipTest("BREV Best checkpoint is not present in this checkout")
        config = pipeline.default_training_config()
        config.update(dashboard.SELECTED_SOURCES_MODEL_DEFAULTS)
        config.update({
            "dataset_profile": recipe.PROFILE_ID,
            "class_names": ["knife", "gun"],
            "initial_weights_path": str(initial_checkpoint),
            "output_root": str(dashboard.SELECTED_SOURCES_FINETUNE_OUTPUT_ROOT),
        })
        validated = pipeline._validated_config(config)
        self.assertEqual(validated["epochs_cap"], 10)
        self.assertEqual(validated["patience"], 3)
        self.assertEqual(validated["early_stop_metric"], "f05_fitness_mean")
        self.assertEqual(validated["initial_weights_path"], str(initial_checkpoint.resolve()))

    def test_f05_and_fitness_benchmark_math(self):
        summary = hooks.validation_score_summary([0.8, 0.5, 0.6, 0.7])
        self.assertAlmostEqual(summary["f0_5"], 0.7142857142857143)
        self.assertAlmostEqual(summary["fitness"], 0.69)
        self.assertAlmostEqual(summary["early_stop_benchmark"], 0.7021428571428572)
        self.assertEqual(hooks.f_beta_score(0.0, 0.0, 0.5), 0.0)


if __name__ == "__main__":
    unittest.main()
