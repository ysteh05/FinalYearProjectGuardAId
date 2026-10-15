from __future__ import annotations

import sys
from pathlib import Path


MODEL_CODE = Path(__file__).resolve().parents[1]
if str(MODEL_CODE) not in sys.path:
    sys.path.insert(0, str(MODEL_CODE))

import mc3_18_airtlab_modalities_experiment as experiment


def test_airtlab_group_split_is_deterministic_stratified_and_camera_safe() -> None:
    violent = [f"violent/{index}" for index in range(115)]
    nonviolent = [f"non-violent/{index}" for index in range(60)]
    groups = experiment.assign_group_splits({0: nonviolent, 1: violent}, seed=2026)
    assert groups == experiment.assign_group_splits({0: nonviolent, 1: violent}, seed=2026)
    assert len(groups) == 175
    counts = {split: sum(value == split for value in groups.values()) for split in ("train", "val", "test")}
    assert counts == {"train": 105, "val": 35, "test": 35}
    for label, expected in ((1, {"train": 69, "val": 23, "test": 23}), (0, {"train": 36, "val": 12, "test": 12})):
        label_groups = violent if label else nonviolent
        assert {split: sum(groups[group] == split for group in label_groups) for split in expected} == expected
    # Both camera rows inherit the assignment of their shared event-group key.
    rows = [(group, camera, groups[group]) for group in groups for camera in ("cam1", "cam2")]
    for group in groups:
        assert {split for row_group, _, split in rows if row_group == group} == {groups[group]}


def test_balanced_counts_use_exact_60_20_20_for_airtlab_classes() -> None:
    assert experiment._balanced_counts(115, experiment.SPLIT_RATIOS) == {"train": 69, "val": 23, "test": 23}
    assert experiment._balanced_counts(60, experiment.SPLIT_RATIOS) == {"train": 36, "val": 12, "test": 12}
