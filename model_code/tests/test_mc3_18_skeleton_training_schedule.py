import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mc3_18_skeleton_four_second_pipeline as skeleton  # noqa: E402

try:
    import torch  # noqa: E402
except ImportError:  # pragma: no cover - minimal test environments
    torch = None


class SkeletonTrainingScheduleTests(unittest.TestCase):
    def test_requested_epoch_limits_and_scheduler_policy_are_locked(self):
        self.assertEqual(skeleton.EPOCHS, 15)
        self.assertEqual(skeleton.PATIENCE, 4)
        self.assertEqual(skeleton.LR_SCHEDULER_FACTOR, 0.5)
        self.assertEqual(skeleton.LR_SCHEDULER_NO_IMPROVEMENT_EPOCHS, 2)
        self.assertEqual(skeleton.LR_SCHEDULER_PATIENCE, 1)

    @unittest.skipIf(torch is None, "PyTorch is required to verify scheduler behavior")
    def test_lr_is_halved_after_exactly_two_bad_validation_epochs(self):
        parameter = torch.nn.Parameter(torch.tensor(1.0))
        optimizer = torch.optim.SGD([parameter], lr=0.1)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer,
            mode="max",
            factor=skeleton.LR_SCHEDULER_FACTOR,
            patience=skeleton.LR_SCHEDULER_PATIENCE,
            min_lr=1e-6,
        )

        scheduler.step(0.8)  # Initial best.
        scheduler.step(0.7)  # First bad epoch: retain LR.
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 0.1)
        scheduler.step(0.7)  # Second consecutive bad epoch: halve LR.
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 0.05)


if __name__ == "__main__":
    unittest.main()
