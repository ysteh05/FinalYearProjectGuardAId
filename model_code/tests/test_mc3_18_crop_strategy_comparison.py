from __future__ import annotations

import importlib.util
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "mc3_18_crop_strategy_comparison.py"
SPEC = importlib.util.spec_from_file_location("mc3_crop_strategy_comparison", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class CropPositionTests(unittest.TestCase):
    def test_center_crop_is_exact_center(self) -> None:
        self.assertEqual(MODULE.crop_positions(128, 228, "center"), [(8, 58)])

    def test_three_position_spans_landscape_long_axis(self) -> None:
        self.assertEqual(
            MODULE.crop_positions(128, 228, "three_position"),
            [(8, 0), (8, 58), (8, 116)],
        )

    def test_three_position_spans_portrait_long_axis(self) -> None:
        self.assertEqual(
            MODULE.crop_positions(228, 128, "three_position"),
            [(0, 8), (58, 8), (116, 8)],
        )

    def test_square_three_position_uses_vertical_convention(self) -> None:
        self.assertEqual(
            MODULE.crop_positions(128, 128, "three_position"),
            [(0, 8), (8, 8), (16, 8)],
        )

    def test_five_crop_contains_corners_and_center(self) -> None:
        self.assertEqual(
            MODULE.crop_positions(128, 228, "five_crop"),
            [(0, 0), (0, 116), (16, 0), (16, 116), (8, 58)],
        )

    def test_rejects_frame_smaller_than_crop(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.crop_positions(111, 128, "center")

    def test_saved_baseline_csv_is_consumed_while_file_is_open(self) -> None:
        # Regression for the observed traceback: ValueError:
        # "I/O operation on closed file" from iterating csv.DictReader later.
        with tempfile.TemporaryDirectory() as temporary_directory:
            predictions = Path(temporary_directory) / "baseline.csv"
            sample_path = Path(temporary_directory) / "AIRTLab" / "cam1" / "video.mp4"
            with predictions.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["dataset", "path", "probability_fight"])
                writer.writeheader()
                writer.writerow({
                    "dataset": "AIRTLab",
                    "path": str(sample_path),
                    "probability_fight": "0.75",
                })
            with patch.object(MODULE, "BASELINE_VALIDATION", predictions):
                result = MODULE._load_baseline("val")
            self.assertEqual(result, {str(sample_path.resolve()): 0.75})

    def test_summary_includes_airtlab_event_group_metrics(self) -> None:
        rows = []
        dataset_rows = [
            ("Real Life Violence Dataset", 0, 0.1, "", "rlvd.mp4"),
            ("RWF-2000", 1, 0.9, "", "rwf.mp4"),
            ("AIRTLab", 1, 0.8, "event-a", "event-a-cam1.mp4"),
            ("AIRTLab", 1, 0.7, "event-a", "event-a-cam2.mp4"),
            ("AIRTLab", 0, 0.2, "event-b", "event-b-cam1.mp4"),
        ]
        for strategy in MODULE.STRATEGIES:
            for dataset, label, score, event_group, filename in dataset_rows:
                for split in ("val", "test"):
                    rows.append({
                        "strategy": strategy,
                        "dataset": dataset,
                        "split": split,
                        "label": label,
                        "probability_fight": score,
                        "airtlab_event_group": event_group,
                        "path": filename,
                    })
        thresholds = {
            strategy: {
                mode: {"threshold": 0.5}
                for mode in MODULE.MODE_OBJECTIVES
            }
            for strategy in MODULE.STRATEGIES
        }
        report = MODULE._summarize(
            [row for row in rows if row["split"] == "val"],
            [row for row in rows if row["split"] == "test"],
            thresholds,
        )
        self.assertEqual(
            report["validation"]["center"]["airtlab_event_group_average"]["n_groups"], 2
        )


if __name__ == "__main__":
    unittest.main()
