import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yolov9_mall_finetune as recipe  # noqa: E402
import yolov9_weapon_dashboard as dashboard  # noqa: E402


class MallFineTuneDatasetTests(unittest.TestCase):
    def _make_image(self, path: Path, index: int) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 24), ((index * 11) % 255, (index * 17) % 255, (index * 23) % 255)).save(path)
        return path

    def _base_report(self, root: Path) -> dict:
        cache = root / "base-cache"
        rows = []
        for split, stem in (("train", "base-train"), ("val", "base-val"), ("test", "base-test")):
            image = self._make_image(cache / "images" / split / f"{stem}.jpg", len(rows) + 1)
            label = cache / "labels" / split / f"{stem}.txt"
            label.parent.mkdir(parents=True, exist_ok=True)
            label.write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
            rows.append({
                "source": "curated_fixture", "source_split": split, "split": split,
                "source_image": str(image), "source_label": str(label),
                "image": str(image), "label": str(label), "classes": "0", "box_count": "1", "negative": "False",
            })
        manifest = cache / "manifest.csv"
        with manifest.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=recipe.MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        (cache / "audit_report.json").write_text("{}", encoding="utf-8")
        excluded = cache / "excluded.csv"
        excluded.write_text("source_sample,reason\n", encoding="utf-8")
        return {
            "profile": "full_dataset_v1", "fingerprint": "base-fixture-v1", "cache_root": str(cache),
            "manifest": str(manifest), "excluded_csv": str(excluded),
            "images": {"train": 1, "val": 1, "test": 1}, "class_counts": {"knife": 3, "gun": 0},
            "selected_sources": ["curated_fixture"], "excluded_samples": 0,
        }

    def _make_frames(self, root: Path, count: int = 40) -> Path:
        frames = root / "frames"
        for index in range(count):
            self._make_image(frames / f"frame_{index:04d}.jpg", index + 50)
        return frames

    def test_temporal_block_split_is_exact_deterministic_and_keeps_each_block_together(self):
        with tempfile.TemporaryDirectory(dir=recipe.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            frames_root = self._make_frames(root, 40)
            frames = sorted(frames_root.glob("*.jpg"))
            first = recipe.assign_mall_splits(frames, expected_count=40, block_size=2, seed=2026)
            second = recipe.assign_mall_splits(frames, expected_count=40, block_size=2, seed=2026)
            self.assertEqual(first, second)
            self.assertEqual({split: list(first.values()).count(split) for split in ("train", "val", "test")}, {"train": 32, "val": 4, "test": 4})
            for index in range(0, len(frames), 2):
                self.assertEqual(first[frames[index]], first[frames[index + 1]])

    def test_merged_cache_copies_existing_splits_and_mall_empty_labels_without_mutating_sources(self):
        with tempfile.TemporaryDirectory(dir=recipe.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            base_report = self._base_report(root)
            frames_root = self._make_frames(root, 40)
            before = {path: path.read_bytes() for path in frames_root.glob("*.jpg")}
            report = recipe.build_finetune_dataset(
                output_root=root / "separate-output", frames_root=frames_root,
                expected_mall_images=40, block_size=2, seed=2026, base_report=base_report,
            )
            self.assertEqual(report["profile"], recipe.PROFILE_ID)
            self.assertEqual(report["mall_split"]["images"], {"train": 32, "val": 4, "test": 4})
            self.assertEqual(report["images"], {"train": 33, "val": 5, "test": 5})
            with Path(report["manifest"]).open("r", encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            mall_rows = [row for row in rows if row["source"] == "mall_negative"]
            self.assertEqual(len(mall_rows), 40)
            self.assertTrue(all(row["negative"] == "True" and row["box_count"] == "0" for row in mall_rows))
            self.assertTrue(all(Path(row["label"]).read_text(encoding="utf-8") == "" for row in mall_rows))
            self.assertEqual(before, {path: path.read_bytes() for path in frames_root.glob("*.jpg")})
            self.assertEqual(Path(base_report["manifest"]).read_bytes().count(b"curated_fixture"), 3)
            self.assertEqual(recipe.build_finetune_dataset(
                output_root=root / "separate-output", frames_root=frames_root,
                expected_mall_images=40, block_size=2, seed=2026, base_report=base_report,
            )["fingerprint"], report["fingerprint"])

    def test_requires_expected_image_count_and_ten_way_temporal_grouping(self):
        with tempfile.TemporaryDirectory(dir=recipe.PROJECT_ROOT) as temporary:
            frames_root = self._make_frames(Path(temporary), 18)
            with self.assertRaisesRegex(ValueError, "Expected exactly 2000"):
                recipe.inspect_mall_source(frames_root)
            with self.assertRaisesRegex(ValueError, "expected 20"):
                recipe.assign_mall_splits(sorted(frames_root.glob("*.jpg")), expected_count=20, block_size=2)


class MallFineTuneDashboardTests(unittest.TestCase):
    def test_run_ids_route_to_a_dedicated_output_root(self):
        path = dashboard._safe_run_dir("mallft_20261010_120000_ab12cd")
        self.assertTrue(path.is_relative_to(dashboard.MALL_FINETUNE_OUTPUT_ROOT / "runs"))
        self.assertFalse(path.is_relative_to(dashboard.OUTPUT_ROOT / "runs"))

    def test_model_resolver_rejects_arbitrary_checkpoint_paths_and_unknown_ids(self):
        with mock.patch.object(dashboard, "_mall_finetune_models", return_value={"models": [{"model_id": "trusted", "label": "Trusted"}]}):
            with self.assertRaisesRegex(ValueError, "不在本地可信"):
                dashboard._resolve_mall_finetune_model("C:\\temp\\model.pt")

    def test_recipe_locks_five_epochs_and_disables_early_stopping(self):
        self.assertEqual(dashboard.MALL_FINETUNE_MODEL_DEFAULTS["epochs_cap"], 5)
        self.assertEqual(dashboard.MALL_FINETUNE_MODEL_DEFAULTS["patience"], 0)
        self.assertEqual(dashboard.MALL_FINETUNE_MODEL_DEFAULTS["batch_size"], 16)
        self.assertEqual(dashboard.MALL_FINETUNE_MODEL_DEFAULTS["workers"], 4)
        self.assertEqual(dashboard.MALL_FINETUNE_MODEL_DEFAULTS["lr0"], 0.0001)


if __name__ == "__main__":
    unittest.main()
