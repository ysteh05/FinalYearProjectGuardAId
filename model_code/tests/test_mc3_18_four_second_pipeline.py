import hashlib
import importlib
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mc3_18_four_second_pipeline as four  # noqa: E402
from mc3_18_mil_pipeline import VideoRecord  # noqa: E402

try:
    import numpy as np  # noqa: E402
except ImportError:
    np = None


@unittest.skipIf(np is None, "numpy is required for MC3 frame-cache tests")
class FourSecondWindowTests(unittest.TestCase):
    def test_frame_derived_duration_reproduces_unrounded_extraction_indices(self):
        fps, frame_count = 24.0, 128
        recorded_duration = round(frame_count / fps, 9)
        duration = four.duration_from_frame_count(fps, frame_count, recorded_duration)
        windows = four.make_four_second_windows(duration, fps, frame_count)
        self.assertEqual(duration, frame_count / fps)
        self.assertEqual(windows[-1].frame_indices, (34, 40, 46, 52, 58, 65, 71, 77, 83, 89, 95, 101, 107, 113, 119, 125))

    def test_three_second_video_uses_one_natural_short_window(self):
        windows = four.make_four_second_windows(3.0, 30.0, 90)
        self.assertEqual(len(windows), 1)
        self.assertEqual((windows[0].start, windows[0].end), (0.0, 3.0))
        self.assertEqual(len(windows[0].frame_indices), 16)
        self.assertEqual(windows[0].frame_indices[0], 2)
        self.assertEqual(windows[0].frame_indices[-1], 87)
        self.assertEqual(tuple(sorted(windows[0].frame_indices)), windows[0].frame_indices)

    def test_twenty_one_second_video_adds_one_end_aligned_tail(self):
        windows = four.make_four_second_windows(21.0, 30.0, 630)
        self.assertEqual(len(windows), 10)
        self.assertEqual([window.start for window in windows], [0, 2, 4, 6, 8, 10, 12, 14, 16, 17])
        self.assertEqual((windows[-1].start, windows[-1].end), (17.0, 21.0))
        self.assertEqual(len(windows[-1].frame_indices), 16)
        self.assertEqual(windows[-1].frame_indices[0], 513)
        self.assertEqual(windows[-1].frame_indices[-1], 626)

    def test_exact_four_seconds_does_not_duplicate_the_tail(self):
        windows = four.make_four_second_windows(4.0, 30.0, 120)
        self.assertEqual(len(windows), 1)
        self.assertEqual((windows[0].start, windows[0].end), (0.0, 4.0))

    def test_invalid_video_metadata_returns_no_windows(self):
        self.assertEqual(four.make_four_second_windows(0, 30, 0), [])
        self.assertEqual(four.make_four_second_windows(float("nan"), 30, 30), [])
        self.assertEqual(four.make_four_second_windows(0.99, 30, 30), [])


class DatasetPolicyTests(unittest.TestCase):
    def make_record(self, dataset, duration, source_uri):
        record = VideoRecord(
            dataset=dataset,
            split="train",
            class_name="Fight",
            label=1,
            path=Path(source_uri),
            fps=30.0,
            frame_count=max(1, int(duration * 30)),
            duration=duration,
            size_bytes=10,
            mtime_ns=20,
        )
        record.source_uri = source_uri
        return record

    def test_rlvd_short_videos_drop_but_other_short_videos_use_tail_policy(self):
        rlvd = self.make_record("Real Life Violence Dataset", 3.9, "rlvd-short.mp4")
        ucf = self.make_record("UCF", 3.0, "ucf-short.mp4")
        manifest = [
            {"source_uri": "rlvd-short.mp4", "dataset": rlvd.dataset, "eligible": 1, "duration_seconds": "3.9"},
            {"source_uri": "ucf-short.mp4", "dataset": ucf.dataset, "eligible": 1, "duration_seconds": "3.0"},
        ]
        kept, short_count = four._apply_rlvd_duration_rule([rlvd, ucf], manifest)
        self.assertEqual([record.dataset for record in kept], ["UCF"])
        self.assertEqual(short_count, 1)
        self.assertEqual(manifest[0]["eligible"], 0)
        self.assertEqual(manifest[0]["exclusion_reason"], "real_life_violence_shorter_than_4_seconds")
        self.assertEqual(manifest[1]["short_tail_window_used"], 1)

    def test_only_three_requested_legacy_datasets_are_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (Path("RWF-2000"), Path("archive") / "Real Life Violence Dataset", Path("ShanghaiTech"), Path("bus-violence")):
                (root / relative).mkdir(parents=True)
            selected = four._selected_legacy_roots(root)
        self.assertEqual([name for name, _ in selected], ["RWF-2000", "Real Life Violence Dataset", "ShanghaiTech"])

    def test_protocol_metadata_does_not_require_source_directories_to_be_mounted(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_root = Path(directory) / "unmounted-legacy-datasets"
            with mock.patch.object(four, "LEGACY_DATASET_ROOT", missing_root):
                protocol = four._protocol()
        self.assertEqual(protocol["legacy_datasets"], ["RWF-2000", "Real Life Violence Dataset", "ShanghaiTech"])

    def test_airtlab_cam_views_with_matching_names_share_one_source_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ucf = root / "UCF"
            airtlab = root / "AIRTLab"
            guardaid = root / "GuardAId"
            for relative in (Path("Violence") / "Assault", Path("Non-Violence"),
                             Path("../AIRTLab/violent/cam1"), Path("../AIRTLab/violent/cam2"),
                             Path("../AIRTLab/non-violent/cam1"), Path("../AIRTLab/non-violent/cam2"),
                             Path("../GuardAId")):
                (ucf / relative).mkdir(parents=True, exist_ok=True)
            (ucf / "Violence" / "Assault" / "fight.mp4").write_bytes(b"fight")
            (ucf / "Non-Violence" / "normal.mp4").write_bytes(b"normal")
            for class_dir in ("violent", "non-violent"):
                for camera in ("cam1", "cam2"):
                    (airtlab / class_dir / camera / "event-01.mp4").write_bytes(f"{class_dir}-{camera}".encode())
            (guardaid / "unlabeled.mp4").write_bytes(b"external")
            core_attrs = (
                "WINDOW_SECONDS", "STRIDE_SECONDS", "NUM_FRAMES", "CACHE_PROTOCOL", "CACHE_ENGINE_VERSION",
                "default_dataset_root", "default_cache_root", "_preprocessing_protocol", "discover_dataset_roots",
                "_scan_datasets", "_dataset_fingerprint", "_windows_for_record", "_cache_video_worker",
                "_count_split_samples", "_aggregate_metrics", "_per_dataset_metrics", "_evaluate_split",
            )
            old_core_state = {name: getattr(four.core, name) for name in core_attrs}
            try:
                reverse = importlib.import_module("mc3_18_reverse_concat_pipeline")
                with mock.patch.multiple(
                    reverse,
                    UCF_ROOT=ucf,
                    AIRTLAB_ROOT=airtlab,
                    GUARDAID_ROOT=guardaid,
                    AIRTLAB_SPLIT_FOR_MODEL=True,
                    _ORIGINAL_DISCOVER_DATASET_ROOTS=lambda _root: [],
                ):
                    sources, _ = reverse._collect_sources(logging.getLogger("airtlab-group-test"))
                    air_sources = [source for source in sources if source.dataset == "AIRTLab"]
                    assignments = reverse._assign_airtlab_splits(air_sources, 2026)
            finally:
                for name, value in old_core_state.items():
                    setattr(four.core, name, value)

        for class_name in ("Fight", "NonFight"):
            same_event = [source for source in air_sources if source.class_name == class_name]
            self.assertEqual(len(same_event), 2)
            self.assertEqual(same_event[0].source_group, same_event[1].source_group)
            self.assertIn(assignments[same_event[0].source_group], {"train", "val", "test"})

    def test_full_scan_policy_marks_short_rlvd_and_keeps_other_short_manifest_rows(self):
        rlvd = self.make_record("Real Life Violence Dataset", 3.9, "rlvd-3.9.mp4")
        ucf = self.make_record("UCF", 3.0, "ucf-3.0.mp4")
        manifest = [
            {"source_uri": "rlvd-3.9.mp4", "dataset": rlvd.dataset, "eligible": 1, "duration_seconds": "3.9", "exclusion_reason": ""},
            {"source_uri": "ucf-3.0.mp4", "dataset": ucf.dataset, "eligible": 1, "duration_seconds": "3.0", "exclusion_reason": ""},
            {"source_uri": "rlvd-subsecond.mp4", "dataset": rlvd.dataset, "eligible": 0, "duration_seconds": "0.7", "exclusion_reason": "shorter_than_1_second"},
            {"source_uri": "rlvd-duplicate.mp4", "dataset": rlvd.dataset, "eligible": 0, "duration_seconds": "", "exclusion_reason": "exact_duplicate_deduplicated"},
        ]
        fake_reverse = mock.Mock()
        fake_reverse.WINDOW_SECONDS = 2.0
        fake_reverse.MIN_TRAIN_SECONDS = 1.0
        fake_reverse._scan_datasets.return_value = ([rlvd, ucf], manifest)
        with mock.patch.object(four, "_reverse", fake_reverse):
            kept, scanned_manifest = four._scan_four_second_datasets(Path("unused"), object(), logging.getLogger("four-policy-test"))
        self.assertEqual([record.dataset for record in kept], ["UCF"])
        self.assertEqual(scanned_manifest[0]["exclusion_reason"], "real_life_violence_shorter_than_4_seconds")
        self.assertEqual(scanned_manifest[1]["short_tail_window_used"], 1)
        self.assertEqual(scanned_manifest[2]["exclusion_reason"], "real_life_violence_shorter_than_4_seconds")
        self.assertEqual(scanned_manifest[3]["exclusion_reason"], "exact_duplicate_deduplicated")

    def test_guardaid_records_are_separated_from_the_regular_test_split(self):
        model_test = self.make_record("UCF", 6.0, "model-test.mp4")
        guardaid = self.make_record("GuardAId", 6.0, "guardaid-extra.mp4")
        guardaid.split = "test"
        fake_reverse = mock.Mock()
        fake_reverse.WINDOW_SECONDS = 4.0
        fake_reverse.MIN_TRAIN_SECONDS = 1.0
        fake_reverse._scan_datasets.return_value = ([model_test, guardaid], [])
        with mock.patch.object(four, "_reverse", fake_reverse):
            kept, _manifest = four._scan_four_second_datasets(Path("unused"), object(), logging.getLogger("four-external-test"))
        self.assertEqual(kept, [model_test])
        self.assertEqual(four._EXTERNAL_GUARDAID_RECORDS, [guardaid])
        self.assertEqual(guardaid.split, "external_test")


@unittest.skipIf(np is None, "numpy is required for MC3 frame-cache tests")
class HybridCacheTests(unittest.TestCase):
    def make_record(self, root):
        record = VideoRecord(
            dataset="unit",
            split="train",
            class_name="Fight",
            label=1,
            path=Path(root) / "sample.mp4",
            fps=30.0,
            frame_count=90,
            duration=3.0,
            size_bytes=100,
            mtime_ns=200,
        )
        return record

    def test_four_second_cache_key_uses_legacy_protocol_for_read_through(self):
        record = self.make_record("C:/datasets")
        material = f"{four.LEGACY_PROTOCOL}|{record.path}|{record.size_bytes}|{record.mtime_ns}|{record.fps:.6f}|{record.frame_count}"
        expected = hashlib.sha256(material.encode("utf-8")).hexdigest() + ".npz"
        self.assertEqual(four._legacy_cache_path(record).name, expected)

    def test_cache_roots_must_not_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / "legacy"
            supplement = root / "supplement"
            self.assertEqual(four._validate_cache_roots(shared, supplement), (shared.resolve(), supplement.resolve()))
            for left, right in ((shared, shared), (shared, shared / "nested"), (shared / "nested", shared)):
                with self.assertRaisesRegex(ValueError, "read-only"):
                    four._validate_cache_roots(left, right)

    def test_prepare_cache_reuses_old_frames_and_only_writes_missing_frames_to_supplement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_root, supplement_root = root / "legacy", root / "supplement"
            legacy_root.mkdir()
            record = self.make_record(root)
            required = sorted({index for window in four._windows_for_record(record, training=True) for index in window.frame_indices})
            legacy_path = legacy_root / four._legacy_cache_path(record).name
            old_frames = np.full((1, 128, 128, 3), 10, dtype=np.uint8)
            np.savez_compressed(legacy_path, frame_indices=np.asarray([required[0]], dtype=np.int32), frames=old_frames)
            legacy_before = legacy_path.read_bytes()
            missing_count = len(required) - 1
            decoded = np.full((missing_count, 128, 128, 3), 20, dtype=np.uint8)
            logger = logging.getLogger("four-second-cache-test")
            with mock.patch.object(four, "LEGACY_CACHE_ROOT", legacy_root), \
                    mock.patch.object(four, "MAX_SUPPLEMENT_CACHE_BYTES", 10_000_000), \
                    mock.patch.object(four, "_decode_record_frames", return_value=decoded) as decoder:
                four._prepare_hybrid_cache([record], supplement_root, object(), np, logger, rebuild=False)
            self.assertEqual(decoder.call_args.args[1], required[1:])
            self.assertEqual(legacy_path.read_bytes(), legacy_before)
            supplement_path = four._supplement_cache_path(record)
            self.assertTrue(supplement_path.is_file())
            with np.load(supplement_path, allow_pickle=False) as data:
                self.assertEqual(data["frame_indices"].tolist(), required[1:])
                self.assertEqual(data["frames"].shape[0], missing_count)

    def test_prepare_cache_reuses_previous_four_second_supplement_before_decoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_root, prior_root, current_root = root / "legacy", root / "prior-4s", root / "current"
            legacy_root.mkdir()
            prior_root.mkdir()
            record = self.make_record(root)
            required = sorted({index for window in four._windows_for_record(record, training=True)
                               for index in window.frame_indices})
            legacy_path = legacy_root / four._legacy_cache_path(record).name
            legacy_frames = np.full((1, 128, 128, 3), 10, dtype=np.uint8)
            np.savez_compressed(legacy_path, frame_indices=np.asarray([required[0]], dtype=np.int32),
                                frames=legacy_frames)
            with mock.patch.object(four, "SUPPLEMENT_CACHE_ROOT", prior_root):
                prior_path = four._supplement_cache_path(record)
            prior_frames = np.full((len(required) - 1, 128, 128, 3), 20, dtype=np.uint8)
            np.savez_compressed(prior_path, frame_indices=np.asarray(required[1:], dtype=np.int32),
                                frames=prior_frames)
            legacy_before, prior_before = legacy_path.read_bytes(), prior_path.read_bytes()
            logger = logging.getLogger("four-second-prior-cache-test")
            with (mock.patch.object(four, "LEGACY_CACHE_ROOT", legacy_root),
                  mock.patch.object(four, "SUPPLEMENT_CACHE_ROOT", current_root),
                  mock.patch.object(four, "READ_ONLY_SUPPLEMENT_CACHE_ROOTS", (prior_root,)),
                  mock.patch.object(four, "MAX_SUPPLEMENT_CACHE_BYTES", 10_000_000),
                  mock.patch.object(four, "_decode_record_frames") as decoder):
                four._prepare_hybrid_cache([record], current_root, object(), np, logger, rebuild=False)

            decoder.assert_not_called()
            self.assertEqual(tuple(record.prior_supplement_cache_paths), (prior_path,))
            self.assertFalse(Path(record.supplement_cache_path).exists())
            frames, index_map = four._load_hybrid_cache(record, np)
            self.assertEqual(set(index_map), set(required))
            self.assertEqual(frames[:, 0, 0, 0].tolist(), [10] + [20] * (len(required) - 1))
            self.assertEqual(legacy_path.read_bytes(), legacy_before)
            self.assertEqual(prior_path.read_bytes(), prior_before)

    def test_load_combines_old_and_supplemental_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = self.make_record(root)
            record.legacy_cache_path = root / "old.npz"
            record.supplement_cache_path = root / "supplement.npz"
            record.required_frame_indices = (2, 5, 9)
            old_frames = np.full((2, 128, 128, 3), 10, dtype=np.uint8)
            new_frame = np.full((1, 128, 128, 3), 20, dtype=np.uint8)
            np.savez_compressed(record.legacy_cache_path, frame_indices=np.asarray([2, 5], dtype=np.int32), frames=old_frames)
            np.savez_compressed(record.supplement_cache_path, frame_indices=np.asarray([9], dtype=np.int32), frames=new_frame)
            frames, index_map = four._load_hybrid_cache(record, np)
        self.assertEqual(index_map, {2: 0, 5: 1, 9: 2})
        self.assertEqual(frames.shape, (3, 128, 128, 3))
        self.assertEqual(frames[:, 0, 0, 0].tolist(), [10, 10, 20])

    def test_unstored_frame_is_decoded_on_demand(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = self.make_record(root)
            record.legacy_cache_path = root / "old.npz"
            record.supplement_cache_path = root / "supplement.npz"
            record.required_frame_indices = (2, 9)
            old = np.full((1, 128, 128, 3), 10, dtype=np.uint8)
            np.savez_compressed(record.legacy_cache_path, frame_indices=np.asarray([2], dtype=np.int32), frames=old)
            decoded = np.full((1, 128, 128, 3), 30, dtype=np.uint8)
            with mock.patch.object(four, "_decode_record_frames", return_value=decoded):
                frames, index_map = four._load_hybrid_cache(record, np)
        self.assertEqual(index_map, {2: 0, 9: 1})
        self.assertEqual(frames[:, 0, 0, 0].tolist(), [10, 30])

    def test_supplement_writer_never_crosses_configured_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.npz"
            frames = np.zeros((1, 128, 128, 3), dtype=np.uint8)
            written, used = four._atomic_write_cache(
                path, [1], frames, cap_bytes=1, current_bytes=0, prior_size=0
            )
            self.assertFalse(written)
            self.assertEqual(used, 0)
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_atomic_cache_write_uses_short_temp_name_under_windows_path_limit(self):
        # Regression for the captured 2026-10-14 training traceback:
        # _prepare_video_cache -> _prepare_hybrid_cache -> _atomic_write_cache
        # -> temporary.open("wb") raised FileNotFoundError [Errno 2] because
        # `<64-char-digest>.npz.<pid>.<time_ns>.tmp` made a 269-char path.
        # The destination itself fits; only the appended temporary basename did not.
        with tempfile.TemporaryDirectory(dir=four.PROJECT_ROOT) as directory:
            cache_dir = (Path(directory) / "model_runs" / "mc3_full_frame_shortside112_4s16f2"
                         / "_frame_cache" / "mc3-4s-2s-stride-16f-shortside112-fullframe-v1" / "videos")
            destination = cache_dir / ("a" * 64 + ".npz")
            old_style_temp = destination.with_name(
                f"{destination.name}.19328.1791959762960369000.tmp")
            short_temp = four._cache_write_temp_path(destination)
            self.assertLess(len(str(destination)), 260)
            self.assertGreater(len(str(old_style_temp)), 260)
            self.assertLess(len(str(short_temp)), 260)

            frames = np.zeros((1, 128, 128, 3), dtype=np.uint8)
            written, used = four._atomic_write_cache(destination, [7], frames)
            self.assertTrue(written)
            self.assertGreater(used, 0)
            self.assertTrue(destination.is_file())
            with np.load(destination, allow_pickle=False) as saved:
                self.assertEqual(saved["frame_indices"].tolist(), [7])
                self.assertEqual(saved["frames"].shape, (1, 128, 128, 3))
            self.assertEqual(list(cache_dir.glob(".cache-write-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
