import csv
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from PIL import Image
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yolov9_expanded_negative_finetune as recipe  # noqa: E402
import yolov9_weapon_hooks as hooks  # noqa: E402
import yolov9_weapon_dashboard as dashboard  # noqa: E402
import yolov9_weapon_pipeline as pipeline  # noqa: E402
from yolov9_mall_finetune import MANIFEST_FIELDS  # noqa: E402


class ExpandedNegativeDatasetTests(unittest.TestCase):
    def _image(self, path: Path, value: int) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (24, 16), (value % 255, (value * 3) % 255, (value * 7) % 255)).save(path)
        return path

    def _source(self, root: Path, source_id: str, counts: dict[str, int], *, nonempty_labels: bool) -> None:
        source_root = root / recipe.SOURCES[source_id]["folder"]
        split_dirs = recipe.SPLIT_DIRECTORIES[source_id]
        for split, count in counts.items():
            source_split = split_dirs[split]
            for index in range(count):
                image = self._image(source_root / source_split / "images" / f"{source_id}_{split}_{index:03}.jpg", index + 8)
                label = source_root / source_split / "labels" / f"{image.stem}.txt"
                label.parent.mkdir(parents=True, exist_ok=True)
                label.write_text("0 0.5 0.5 0.2 0.2\n" if nonempty_labels else "", encoding="utf-8")

    def _mall_report(self, root: Path, *, v11_empty_counts: dict[str, int] | None = None) -> dict:
        cache = root / "previous-mall-cache"
        rows = []
        for split in ("train", "val", "test"):
            base_image = self._image(cache / "images" / split / f"base_{split}.jpg", len(rows) + 1)
            base_label = cache / "labels" / split / f"base_{split}.txt"
            base_label.parent.mkdir(parents=True, exist_ok=True)
            base_label.write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
            rows.append({"source": "curated_fixture", "source_split": split, "split": split, "source_image": str(base_image), "source_label": str(base_label), "image": str(base_image), "label": str(base_label), "classes": "0", "box_count": "1", "negative": "False"})

            mall_image = self._image(cache / "images" / split / f"mall_frame_{split}.jpg", len(rows) + 1)
            mall_label = cache / "labels" / split / f"mall_frame_{split}.txt"
            mall_label.write_text("", encoding="utf-8")
            rows.append({"source": "mall_negative", "source_split": "sequence", "split": split, "source_image": str(mall_image), "source_label": "(empty)", "image": str(mall_image), "label": str(mall_label), "classes": "", "box_count": "0", "negative": "True"})
            if v11_empty_counts:
                for index in range(v11_empty_counts.get(split, 0)):
                    empty_image = self._image(cache / "images" / split / f"v11_empty_{split}_{index}.jpg", len(rows) + 12)
                    empty_label = cache / "labels" / split / f"v11_empty_{split}_{index}.txt"
                    empty_label.write_text("", encoding="utf-8")
                    rows.append({"source": "weapon_dataset_v11", "source_split": split, "split": split, "source_image": str(empty_image), "source_label": "(empty)", "image": str(empty_image), "label": str(empty_label), "classes": "", "box_count": "0", "negative": "True"})
                positive_image = self._image(cache / "images" / split / f"v11_weapon_{split}.jpg", len(rows) + 22)
                positive_label = cache / "labels" / split / f"v11_weapon_{split}.txt"
                positive_label.write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
                rows.append({"source": "weapon_dataset_v11", "source_split": split, "split": split, "source_image": str(positive_image), "source_label": str(positive_label), "image": str(positive_image), "label": str(positive_label), "classes": "0", "box_count": "1", "negative": "False"})
        manifest = cache / "manifest.csv"
        with manifest.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        excluded = cache / "excluded.csv"
        excluded.write_text("source_sample,reason\nprior,already reviewed\n", encoding="utf-8-sig")
        (cache / "audit_report.json").write_text("{}", encoding="utf-8")
        return {
            "profile": "mall_negative_finetune_v1",
            "fingerprint": "mall-fixture",
            "cache_root": str(cache),
            "manifest": str(manifest),
            "excluded_csv": str(excluded),
            "images": {split: sum(1 for row in rows if row["split"] == split) for split in ("train", "val", "test")},
            "total_images": len(rows),
            "mall_split": {"images": {split: 1 + (v11_empty_counts or {}).get(split, 0) for split in ("train", "val", "test")}},
            "selected_sources": ["curated_fixture", "mall_negative"],
            "excluded_samples": 1,
            "class_counts": {"knife": 3, "gun": 0},
        }

    def test_production_sources_have_exact_expected_counts_and_removed_knife(self):
        summary = recipe.inspect_negative_sources()
        self.assertTrue(summary["ready"])
        self.assertEqual(summary["negative_images"], 6286)
        self.assertEqual(summary["negative_split_counts"], {"train": 5496, "val": 416, "test": 374})
        self.assertEqual(summary["sources"]["nogun"]["split_counts"], {"train": 2828, "val": 88, "test": 30})
        self.assertEqual(summary["excluded_samples"][0]["file"], recipe.EXCLUDED_NOGUN_IMAGE)
        self.assertFalse((recipe.DATA_ROOT / "NoGUN.v5i.yolov9" / "train" / "images" / recipe.EXCLUDED_NOGUN_IMAGE).exists())
        self.assertFalse((recipe.DATA_ROOT / "NoGUN.v5i.yolov9" / "train" / "labels" / f"{Path(recipe.EXCLUDED_NOGUN_IMAGE).stem}.txt").exists())

    def test_expanded_cache_preserves_prior_splits_and_forces_empty_negative_labels(self):
        with tempfile.TemporaryDirectory(dir=recipe.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            data_root = root / "sources"
            mall_frames = root / "mall-frames"
            for index in range(40):
                self._image(mall_frames / f"frame_{index:04}.jpg", index + 40)
            self._source(data_root, "airt_lab_nonviolent", {"train": 2, "val": 1, "test": 1}, nonempty_labels=False)
            self._source(data_root, "bus_violence_nonfight", {"train": 1, "val": 1, "test": 1}, nonempty_labels=False)
            self._source(data_root, "nogun", {"train": 2, "val": 1, "test": 1}, nonempty_labels=True)
            # The explicitly excluded photo is ignored even if it is later restored to the source tree.
            excluded_image = self._image(data_root / "NoGUN.v5i.yolov9" / "train" / "images" / recipe.EXCLUDED_NOGUN_IMAGE, 241)
            excluded_label = data_root / "NoGUN.v5i.yolov9" / "train" / "labels" / f"{excluded_image.stem}.txt"
            excluded_label.write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
            original_no_gun_label = data_root / "NoGUN.v5i.yolov9" / "train" / "labels" / "nogun_train_000.txt"
            original_label_text = original_no_gun_label.read_text(encoding="utf-8")
            mall_report = self._mall_report(root)

            report = recipe.build_expanded_negative_dataset(
                output_root=root / "expanded-output",
                mall_output_root=root / "legacy-mall-output",
                data_root=data_root,
                mall_frames_root=mall_frames,
                expected_source_counts={},
                expected_mall_images=40,
                block_size=2,
                mall_report=mall_report,
            )

            self.assertEqual(report["profile"], recipe.PROFILE_ID)
            self.assertEqual(report["images"], {"train": 7, "val": 5, "test": 5})
            self.assertEqual(report["total_images"], 17)
            self.assertEqual(report["negative_source_total"], 14)
            self.assertEqual(report["negative_source_split_counts"], {"train": 6, "val": 4, "test": 4})
            self.assertEqual(report["added_negative_source_split_counts"]["nogun"], {"train": 2, "val": 1, "test": 1})
            self.assertEqual(original_no_gun_label.read_text(encoding="utf-8"), original_label_text)

            with Path(report["manifest"]).open("r", encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 17)
            self.assertFalse(any(Path(row["source_image"]).name == recipe.EXCLUDED_NOGUN_IMAGE for row in rows))
            for row in rows:
                if row["source"] in {"airt_lab_nonviolent", "bus_violence_nonfight", "nogun"}:
                    self.assertEqual(Path(row["label"]).read_text(encoding="utf-8"), "")
                    self.assertEqual(row["negative"], "True")
            with Path(report["excluded_csv"]).open("r", encoding="utf-8-sig", newline="") as stream:
                exclusions = list(csv.DictReader(stream))
            self.assertIn("Confirmed true knife", exclusions[-1]["reason"])
            self.assertEqual(json.loads(Path(report["source_inventory"]).read_text(encoding="utf-8"))["profile"], recipe.PROFILE_ID)

    def test_nonempty_labels_in_prepared_negative_sources_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=recipe.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            self._source(root, "airt_lab_nonviolent", {"train": 1, "val": 1, "test": 1}, nonempty_labels=True)
            for source_id in ("bus_violence_nonfight", "nogun"):
                self._source(root, source_id, {"train": 1, "val": 1, "test": 1}, nonempty_labels=False)
            frames = root / "mall"
            for index in range(20):
                self._image(frames / f"frame_{index:03}.jpg", index)
            with self.assertRaisesRegex(ValueError, "Expected empty negative labels"):
                recipe.inspect_negative_sources(data_root=root, mall_frames_root=frames, expected_source_counts={}, expected_mall_images=20, mall_block_size=2)

    def test_hard_negative_profile_drops_only_v11_empty_labels_and_preserves_splits(self):
        with tempfile.TemporaryDirectory(dir=recipe.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            data_root = root / "sources"
            mall_frames = root / "mall-frames"
            for index in range(40):
                self._image(mall_frames / f"frame_{index:04}.jpg", index + 40)
            self._source(data_root, "airt_lab_nonviolent", {"train": 2, "val": 1, "test": 1}, nonempty_labels=False)
            self._source(data_root, "bus_violence_nonfight", {"train": 1, "val": 1, "test": 1}, nonempty_labels=False)
            self._source(data_root, "nogun", {"train": 2, "val": 1, "test": 1}, nonempty_labels=True)
            mall_report = self._mall_report(root, v11_empty_counts={"train": 2, "val": 1, "test": 1})

            report = recipe.build_hard_negative_dataset(
                output_root=root / "hard-negative-output",
                mall_output_root=root / "legacy-mall-output",
                data_root=data_root,
                mall_frames_root=mall_frames,
                expected_source_counts={},
                expected_mall_images=40,
                block_size=2,
                mall_report=mall_report,
            )

            self.assertEqual(report["profile"], recipe.HARD_NEGATIVE_PROFILE_ID)
            self.assertEqual(report["v11_empty_label_excluded"]["by_split"], {"train": 2, "val": 1, "test": 1})
            self.assertEqual(report["v11_empty_label_excluded"]["total"], 4)
            self.assertEqual(report["images"], {"train": 8, "val": 6, "test": 6})
            self.assertEqual(report["total_images"], 20)
            self.assertEqual(report["negative_source_split_counts"], {"train": 6, "val": 4, "test": 4})
            with Path(report["manifest"]).open("r", encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertFalse(any(row["source"] == "weapon_dataset_v11" and row["box_count"] == "0" for row in rows))
            self.assertEqual(sum(row["source"] == "weapon_dataset_v11" for row in rows), 3)
            with Path(report["excluded_csv"]).open("r", encoding="utf-8-sig", newline="") as stream:
                exclusions = list(csv.DictReader(stream))
            self.assertEqual(sum("V11 empty-labelled" in row["reason"] for row in exclusions), 4)


class ExpandedNegativeConfigTests(unittest.TestCase):
    def test_process_liveness_probe_handles_live_and_missing_pids(self):
        self.assertTrue(dashboard._pid_exists(dashboard.os.getpid()))
        self.assertFalse(dashboard._pid_exists(4_294_967_295))

    def test_new_fine_tune_profile_keeps_five_epochs_and_uses_doubled_defaults(self):
        self.assertEqual(dashboard.MALL_FINETUNE_MODEL_DEFAULTS["epochs_cap"], 5)
        self.assertEqual(dashboard.MALL_FINETUNE_MODEL_DEFAULTS["patience"], 0)
        self.assertEqual(dashboard.MALL_FINETUNE_MODEL_DEFAULTS["batch_size"], 16)
        self.assertEqual(dashboard.MALL_FINETUNE_MODEL_DEFAULTS["workers"], 4)
        self.assertEqual(dashboard.MALL_FINETUNE_MODEL_DEFAULTS["lr0"], 0.0001)
        self.assertEqual(pipeline.EXPANDED_NEGATIVE_FINETUNE_PROFILE, recipe.PROFILE_ID)

    def test_hard_negative_profile_has_fixed_recipe_and_monitoring_policy(self):
        defaults = dashboard.HARD_NEGATIVE_MODEL_DEFAULTS
        self.assertEqual(defaults["epochs_cap"], 5)
        self.assertEqual(defaults["patience"], 0)
        self.assertEqual(defaults["batch_size"], 8)
        self.assertEqual(defaults["workers"], 2)
        self.assertEqual(defaults["hard_negative_loss_weight"], 1.0)
        self.assertEqual(defaults["hard_negative_loss_gamma"], 2.0)
        self.assertEqual(pipeline.HARD_NEGATIVE_FINETUNE_PROFILE, recipe.HARD_NEGATIVE_PROFILE_ID)


class HardNegativeMetricTests(unittest.TestCase):
    def test_epoch_metrics_csv_includes_validation_fpr_columns(self):
        with tempfile.TemporaryDirectory(dir=recipe.PROJECT_ROOT) as temporary:
            metrics_path = Path(temporary) / "epoch_metrics.csv"
            previous_config = hooks._RUN_CONFIG
            try:
                hooks._RUN_CONFIG = {"metrics_csv": str(metrics_path)}
                hooks._append_csv({
                    "epoch": 1,
                    "fitness": 0.5,
                    "best_fitness": 0.5,
                    "train_loss": {"box": 1.0, "cls": 1.0, "dfl": 1.0},
                    "precision": 0.8,
                    "recall": 0.7,
                    "map50": 0.75,
                    "map50_95": 0.4,
                    "learning_rate": [0.0001],
                    "epoch_seconds": 10.0,
                    "val_fpr_threshold": 0.25,
                    "val_negative_images": 10,
                    "val_negative_images_evaluated": 10,
                    "val_false_positive_images": 2,
                    "val_false_positive_rate": 0.2,
                    "val_false_positive_boxes": 3,
                })
            finally:
                hooks._RUN_CONFIG = previous_config

            with metrics_path.open("r", encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(float(row["val_false_positive_rate"]), 0.2)
            self.assertEqual(int(row["val_negative_images_evaluated"]), 10)

    def test_epoch_fpr_pipeline_recomputes_both_splits_for_baseline_and_every_epoch(self):
        with tempfile.TemporaryDirectory(dir=recipe.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            epoch_dir = run_dir / "checkpoints" / "epoch_models"
            epoch_dir.mkdir(parents=True)
            initial = root / "brev_best.pt"
            initial.write_bytes(b"baseline checkpoint fixture")
            for epoch in range(1, 6):
                (epoch_dir / f"epoch_{epoch:02d}.pt").write_bytes(f"checkpoint {epoch}".encode())
            metrics_path = run_dir / "epoch_metrics.csv"
            with metrics_path.open("w", encoding="utf-8-sig", newline="") as stream:
                fields = ("epoch", "fitness")
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for epoch in range(1, 6):
                    writer.writerow({"epoch": epoch, "fitness": epoch / 10})

            report = {
                "fingerprint": "fixture-fingerprint",
                "manifest": str(root / "manifest.csv"),
                "images": {"val": 4, "test": 5},
                "negative_source_split_counts": {"val": 2, "test": 3},
                "class_names": ["knife", "gun"],
            }
            config = {"initial_weights_path": str(initial), "epochs": 5, "epochs_cap": 5, "fpr_confidence_threshold": 0.25}
            calls = []

            def fake_evaluate(_config, _report, _run_dir, checkpoint, _state, _events, _control, _config_path, *, split, capture=False, run_label=None):
                calls.append((Path(checkpoint).name, split, capture, run_label))
                count = report["negative_source_split_counts"][split]
                return {"negative_fpr_record": {"split": split, "confidence_threshold": 0.25, "negative_images": count, "negative_images_evaluated": count, "false_positive_images": 1, "false_positive_rate": 1 / count, "false_positive_boxes": 1}}

            with mock.patch.object(pipeline, "_evaluation_command", side_effect=fake_evaluate):
                result = pipeline._complete_hard_negative_fpr_tracking(
                    config, report, run_dir, run_dir / "progress.json", run_dir / "events.jsonl", run_dir / "control.json", run_dir / "runtime_config.json", root
                )

            self.assertEqual(len(calls), 12)
            self.assertEqual(len(result["models"]), 6)
            baseline = next(row for row in result["models"] if row["epoch"] == 0)
            self.assertEqual(set(baseline["val"]), {"split", "confidence_threshold", "negative_images", "negative_images_evaluated", "false_positive_images", "false_positive_rate", "false_positive_boxes", "dataset_fingerprint"})
            for epoch in range(1, 6):
                row = next(item for item in result["models"] if item["epoch"] == epoch)
                self.assertEqual(row["val"]["negative_images_evaluated"], 2)
                self.assertEqual(row["test"]["negative_images_evaluated"], 3)

    def test_fpr_tracker_counts_unique_negative_images_at_fixed_threshold(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_one = root / "cache" / "images" / "val" / "negative_one.jpg"
            image_two = root / "cache" / "images" / "val" / "negative_two.jpg"
            source_one = root / "source" / "negative_one.jpg"
            manifest = root / "manifest.csv"
            with manifest.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("split", "negative", "image", "source_image"))
                writer.writeheader()
                writer.writerow({"split": "val", "negative": "True", "image": str(image_one), "source_image": str(source_one)})
                writer.writerow({"split": "val", "negative": "True", "image": str(image_two), "source_image": ""})
                writer.writerow({"split": "val", "negative": "False", "image": str(root / "positive.jpg"), "source_image": ""})

            previous_config = hooks._RUN_CONFIG
            try:
                hooks._NEGATIVE_IMAGE_CACHE.clear()
                hooks._RUN_CONFIG = {"fpr_manifest_path": str(manifest), "fpr_confidence_threshold": 0.25, "track_negative_fpr": True}
                aliases = hooks._negative_images_for_split("val")
                self.assertEqual(len(set(aliases.values())), 2)
                hooks._FPR_TRACKER = {
                    "negative_paths": aliases,
                    "seen": set(),
                    "images_with_false_positive": set(),
                    "false_positive_boxes": 0,
                }
                detections = [
                    torch.tensor([[0, 0, 2, 2, 0.8, 0]], dtype=torch.float32),
                    torch.tensor([[0, 0, 2, 2, 0.2, 1]], dtype=torch.float32),
                ]
                hooks._track_negative_false_positives((0, None, None, [str(image_one), str(image_two)], None, detections))
                self.assertEqual(len(hooks._FPR_TRACKER["seen"]), 2)
                self.assertEqual(len(hooks._FPR_TRACKER["images_with_false_positive"]), 1)
                self.assertEqual(hooks._FPR_TRACKER["false_positive_boxes"], 1)
            finally:
                hooks._RUN_CONFIG = previous_config
                hooks._NEGATIVE_IMAGE_CACHE.clear()

    def test_loss_multiplier_targets_only_zero_label_entries(self):
        yolo_root = recipe.PROJECT_ROOT / "results (4)" / "yolov9"
        if str(yolo_root) not in sys.path:
            sys.path.insert(0, str(yolo_root))
        from utils.loss_tal_dual import ComputeLoss

        criterion = ComputeLoss.__new__(ComputeLoss)
        criterion.hyp = {"hard_negative_loss_weight": 1.0, "hard_negative_loss_gamma": 2.0}
        criterion.BCEcls = torch.nn.BCEWithLogitsLoss(reduction="none")
        hard_negative = criterion._classification_loss(torch.tensor([2.0]), torch.tensor([0.0]))
        easy_negative = criterion._classification_loss(torch.tensor([-2.0]), torch.tensor([0.0]))
        positive = criterion._classification_loss(torch.tensor([2.0]), torch.tensor([1.0]))
        base_positive = torch.nn.functional.binary_cross_entropy_with_logits(torch.tensor([2.0]), torch.tensor([1.0]))
        self.assertGreater(float(hard_negative), float(easy_negative) * 10)
        self.assertAlmostEqual(float(positive), float(base_positive), places=6)

    def test_eight_gb_gpu_attempts_the_requested_batch_before_oom_fallback(self):
        self.assertEqual(pipeline._batch_size_candidates(16, 8 * 1024**3, 7 * 1024**3), [16, 8, 4, 2, 1])

    def test_temporary_cache_paths_stay_under_windows_max_path(self):
        cache_root = Path("C:/workspace/model_runs/yolov9_mall_negative_finetune/datasets") / ("f" * 24)
        staging = recipe._staging_path(cache_root)
        sample_name = "bus_violence_nonfight__train__train_nonfight_NONVIOLENCE_1027_mid_f000012.jpg"
        longest = staging / "images" / "train" / sample_name
        self.assertLess(len(str(longest)), 260)


if __name__ == "__main__":
    unittest.main()
