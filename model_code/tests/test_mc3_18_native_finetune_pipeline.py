import logging
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mc3_18_mil_pipeline as core  # noqa: E402
import mc3_18_native_finetune_pipeline as native  # noqa: E402

try:
    import numpy as np  # noqa: E402
except ImportError:  # pragma: no cover
    np = None


class NativeFineTuneSplitTests(unittest.TestCase):
    def test_video_cache_ceiling_is_20_gib_but_disk_reserve_still_limits_budget(self):
        gib = 1024**3
        self.assertEqual(native.VIDEO_CACHE_CAP_BYTES, 20 * gib)
        self.assertEqual(native._video_cache_budget_from_free_bytes(40 * gib), 20 * gib)
        self.assertEqual(native._video_cache_budget_from_free_bytes(22 * gib), int(13 * gib))
        self.assertEqual(native._video_cache_budget_from_free_bytes(3 * gib), int(0.65 * gib))
        self.assertEqual(native._video_cache_budget_from_free_bytes(2 * gib), 0)

    def test_live_status_encodes_unavailable_auc_as_json_null_then_updates_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live_status.json"
            native._write_live(path, {
                "best_val_roc_auc": float("-inf"),
                "nested": {"nan_metric": float("nan"), "positive_infinity": float("inf")},
                "epoch": 1,
            })

            initial = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNone(initial["best_val_roc_auc"])
            self.assertIsNone(initial["nested"]["nan_metric"])
            self.assertIsNone(initial["nested"]["positive_infinity"])
            self.assertEqual(initial["epoch"], 1)

            native._write_live(path, {"best_val_roc_auc": 0.8125})
            updated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(updated["best_val_roc_auc"], 0.8125)
            self.assertEqual(updated["epoch"], 1)

    def test_training_smoke_reaches_first_batch_and_checkpoint_from_prevalidation_state(self):
        try:
            import torch
            import torch.nn as nn
            import numpy as np
        except ImportError as exc:  # pragma: no cover - runtime dependency guard
            self.skipTest(f"training smoke test requires torch and numpy: {exc}")

        class TinyClassifier(nn.Module):
            def __init__(self):
                super().__init__()
                self.fc = nn.Linear(2, 2)

            def forward(self, values):
                return self.fc(values)

        def deployment_predictions(_records, split, **_kwargs):
            return ([
                {"dataset": "AIRTLab", "split": split, "class_name": "NonFight", "label": 0,
                 "path": "negative.mp4", "source_group": "negative", "duration_seconds": 4.0,
                 "window_count": 1, "probability_fight": 0.1},
                {"dataset": "AIRTLab", "split": split, "class_name": "Fight", "label": 1,
                 "path": "positive.mp4", "source_group": "positive", "duration_seconds": 4.0,
                 "window_count": 1, "probability_fight": 0.9},
            ], [])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run"
            checkpoint_path = root / "base_best.pt"
            records = [
                core.VideoRecord("AIRTLab", split, class_name, label, root / f"{split}_{label}.mp4",
                                 30.0, 120, 4.0, 1, 1)
                for split in ("train", "val", "test")
                for class_name, label in (("NonFight", 0), ("Fight", 1))
            ]
            manifest_rows = [{"dataset": record.dataset, "split": record.split,
                              "class_name": record.class_name, "label": record.label,
                              "path": str(record.path), "source_group": record.path.stem,
                              "eligible": 1} for record in records]
            data_meta = {"base_manifest_sha256": "base-manifest", "dancetrack_manifest_sha256": "dance-manifest",
                         "split_policy": "test-only smoke fixture"}
            model = TinyClassifier()
            torch.save({"deployment_checkpoint_version": 1, "model_name": "mc3_18",
                        "class_to_idx": core.CLASS_TO_LABEL,
                        "protocol": {"protocol": native.four.PROTOCOL}, "state_dict": model.state_dict()}, checkpoint_path)
            args = native.build_parser().parse_args([
                "--run-dir", str(run_dir), "--cache-dir", str(root / "cache"),
                "--base-checkpoint", str(checkpoint_path), "--base-manifest", str(root / "base.csv"),
                "--dancetrack-manifest", str(root / "dance.json"), "--dataset-root", str(root / "datasets"),
                "--device", "cpu", "--allow-cpu", "--epochs", "1", "--patience", "1",
                "--lr-reduce-after", "1", "--grad-accum", "1", "--window-batch-size", "1",
                "--log-every", "1",
            ])

            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(native.core, "_log_setup",
                    return_value=logging.getLogger("native-finetune-smoke-test")))
                stack.enter_context(mock.patch.object(native.core, "_import_runtime_dependencies",
                    return_value=(None, np, torch, nn, (None, lambda weights=None: TinyClassifier()))))
                stack.enter_context(mock.patch.object(native, "_install_runtime_protocol"))
                stack.enter_context(mock.patch.object(native, "load_records",
                    return_value=(records, manifest_rows, data_meta)))
                stack.enter_context(mock.patch.object(native, "summarize", return_value={"total_bags": len(records)}))
                stack.enter_context(mock.patch.object(native, "_experiment_protocol",
                    return_value={"protocol": native.four.PROTOCOL}))
                stack.enter_context(mock.patch.object(native, "_prepare_video_cache"))
                stack.enter_context(mock.patch.object(native, "_prepare_dancetrack_cache"))
                stack.enter_context(mock.patch.object(native.core, "_record_bag_tensor",
                    side_effect=lambda record, **_kwargs: (torch.tensor([[float(record.label), 1.0]]), [object()])))
                stack.enter_context(mock.patch.object(native, "_evaluate", side_effect=deployment_predictions))
                native._run_training(args)

            self.assertTrue((run_dir / "epoch_01" / "best_model.pt").is_file())
            self.assertTrue((run_dir / "last.pt").is_file())
            status = json.loads((run_dir / "live_status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "completed")
            self.assertEqual(status["completed_epochs"], 1)

    def test_dance_genre_split_is_deterministic_exact_and_stratified(self):
        paths = [Path(f"{genre}_video_{index:02d}.mp4")
                 for genre in ("ballet", "break", "hiphop", "jazz", "modern", "tap", "folk", "latin", "street", "swing")
                 for index in range(21)]
        first = native._stratified_dge_splits(paths, 2026)
        second = native._stratified_dge_splits(paths, 2026)

        self.assertEqual(first, second)
        self.assertEqual({split: sum(value == split for value in first.values()) for split in native.SPLITS},
                         {"train": 168, "val": 21, "test": 21})
        for genre in {path.stem.split("_")[0] for path in paths}:
            labels = {first[path] for path in paths if path.stem.startswith(genre + "_")}
            self.assertEqual(labels, set(native.SPLITS))

    def test_shakefive2_split_uses_largest_remainder_80_10_10(self):
        paths = [Path(f"clip_{index:03d}.mp4") for index in range(153)]
        first = native._random_splits(paths, 2026)
        second = native._random_splits(paths, 2026)

        self.assertEqual(first, second)
        self.assertEqual({split: sum(value == split for value in first.values()) for split in native.SPLITS},
                         {"train": 123, "val": 15, "test": 15})

    def test_summary_counts_bags_separately_from_sliding_windows(self):
        records = [
            core.VideoRecord("AIRTLab", "train", "Fight", 1, Path("positive.mp4"), 30, 300, 10, 1, 1),
            core.VideoRecord("DanceTrack", "train", "NonFight", 0, Path("negative"), 20, 1200, 60, 0, 1),
        ]
        rows = [{"dataset": "AIRTLab", "eligible": 1}, {"dataset": "DanceTrack", "eligible": 1}]

        summary = native.summarize(records, rows)

        self.assertEqual(summary["total_bags"], 2)
        self.assertEqual(summary["overall"]["train"]["bags"], 2)
        self.assertEqual(summary["datasets"]["AIRTLab"]["train"]["windows"], 4)
        self.assertEqual(summary["datasets"]["DanceTrack"]["train"]["windows"], 29)

    def test_base_supplement_cache_path_comes_from_selected_best_run_config(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            cache = Path(directory) / "saved-four-second-cache"
            run.mkdir()
            (run / "config.json").write_text(json.dumps({
                "cache_dir": "ignored-fallback",
                "preprocessing": {"cache_reuse": {"supplemental_cache": str(cache)}},
            }), encoding="utf-8")
            with mock.patch.object(native, "DEFAULT_BASE_RUN", run):
                self.assertEqual(native._base_four_second_supplement_cache_root(), cache.resolve())


@unittest.skipIf(np is None, "numpy is required for DanceTrack frame-cache tests")
class NativeDanceTrackCacheTests(unittest.TestCase):
    def test_dancetrack_cache_builds_once_then_reuses_without_decoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sequence = root / "DanceTrack" / "train" / "MOT17-01"
            frame_dir = sequence / "img1"
            frame_dir.mkdir(parents=True)
            (frame_dir / "00000001.jpg").touch()
            record = core.VideoRecord("DanceTrack", "train", "NonFight", 0, sequence,
                                      30.0, 300, 10.0, 0, 0)
            record.required_frame_indices = (0,)
            image = np.full((224, 224, 3), 37, dtype=np.uint8)
            fake_cv2 = SimpleNamespace(IMREAD_COLOR=1, COLOR_BGR2RGB=2,
                                       imread=mock.Mock(return_value=image),
                                       cvtColor=mock.Mock(side_effect=lambda frame, _code: frame))
            logger = logging.getLogger("native-dancetrack-cache-test")
            statuses = []
            with mock.patch.object(native.core, "_resize_short_side", side_effect=lambda frame, _cv2: frame):
                native._prepare_dancetrack_cache([record], root / "cache", fake_cv2, np, logger,
                                                  lambda **values: statuses.append(values),
                                                  cache_budget_bytes=10_000_000)
                first_path = record.supplement_cache_path
                self.assertTrue(first_path.is_file())
                fake_cv2.imread.reset_mock()
                native._prepare_dancetrack_cache([record], root / "cache", fake_cv2, np, logger,
                                                  lambda **values: statuses.append(values),
                                                  cache_budget_bytes=10_000_000)

            fake_cv2.imread.assert_not_called()
            with np.load(first_path, allow_pickle=False) as saved:
                self.assertEqual(saved["frame_indices"].tolist(), [0])
                self.assertEqual(saved["frames"].shape, (1, 224, 224, 3))
                self.assertEqual(int(saved["frames"][0, 0, 0, 0]), 37)
            self.assertTrue(any(status.get("stage_progress") == 1.0 for status in statuses))


if __name__ == "__main__":
    unittest.main()
