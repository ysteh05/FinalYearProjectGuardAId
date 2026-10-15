from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yolov9_weapon_hooks as hooks  # noqa: E402


class ConfiguredEpochStopTests(unittest.TestCase):
    def setUp(self) -> None:
        workspace = Path(__file__).resolve().parents[2]
        self.temp_dir = tempfile.TemporaryDirectory(dir=workspace)
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.paths = {
            "_STATE_PATH": hooks._STATE_PATH,
            "_EVENT_PATH": hooks._EVENT_PATH,
            "_CONTROL_PATH": hooks._CONTROL_PATH,
            "_RUN_CONFIG": hooks._RUN_CONFIG,
            "_LAST_SAVED_EPOCH": hooks._LAST_SAVED_EPOCH,
            "_CURRENT_EPOCH": hooks._CURRENT_EPOCH,
        }
        self.addCleanup(self._restore_hook_state)
        hooks._STATE_PATH = root / "progress.json"
        hooks._EVENT_PATH = root / "events.jsonl"
        hooks._CONTROL_PATH = root / "control.json"
        hooks._CONTROL_PATH.write_text('{"command":""}', encoding="utf-8")
        hooks._RUN_CONFIG = {"stop_after_epoch": 3}
        hooks._LAST_SAVED_EPOCH = -1
        hooks._CURRENT_EPOCH = 0

    def _restore_hook_state(self) -> None:
        for name, value in self.paths.items():
            setattr(hooks, name, value)

    def test_stops_only_after_configured_epoch_checkpoint_is_saved(self) -> None:
        callbacks = SimpleNamespace(stop_training=False)

        hooks._LAST_SAVED_EPOCH = 3
        hooks._check_control(callbacks, checkpoint_saved=False)
        self.assertFalse(callbacks.stop_training)

        hooks._LAST_SAVED_EPOCH = 2
        hooks._check_control(callbacks, checkpoint_saved=True)
        self.assertFalse(callbacks.stop_training)

        hooks._LAST_SAVED_EPOCH = 3
        hooks._check_control(callbacks, checkpoint_saved=True)
        self.assertTrue(callbacks.stop_training)
        state = json.loads(hooks._STATE_PATH.read_text(encoding="utf-8"))
        self.assertTrue(state["budget_finalization_requested"])
        self.assertEqual(state["budget_finalization_epoch"], 3)

    def test_exits_before_first_batch_after_configured_epoch_is_saved(self) -> None:
        hooks._LAST_SAVED_EPOCH = 3

        with self.assertRaises(SystemExit) as result:
            hooks._on_event("on_train_epoch_start", (), {}, SimpleNamespace())

        self.assertEqual(result.exception.code, 0)
        state = json.loads(hooks._STATE_PATH.read_text(encoding="utf-8"))
        self.assertTrue(state["budget_finalization_requested"])
        self.assertEqual(state["budget_finalization_epoch"], 3)

    def test_does_not_exit_at_epoch_start_until_configured_epoch_is_saved(self) -> None:
        hooks._LAST_SAVED_EPOCH = 2

        hooks._on_event("on_train_epoch_start", (), {}, SimpleNamespace())

        state = json.loads(hooks._STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(state["epoch"], 1)
        self.assertNotIn("budget_finalization_requested", state)


if __name__ == "__main__":
    unittest.main()
