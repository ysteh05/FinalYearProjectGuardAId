"""Deployment identities and calibrated defaults for the active models."""

VIOLENCE_MODEL_VERSION = "mc3-18-k400-4s16f2-20261007-e05"
VIOLENCE_MODEL_PROTOCOL = "mc3-4s-2s-stride-16f-128short-112crop-short-tail-v1"
VIOLENCE_WINDOW_SECONDS = 4.0
VIOLENCE_STRIDE_SECONDS = 2.0
VIOLENCE_INPUT_FRAMES = 16

# Validation-selected operating points for the deployed checkpoint/protocol.
VIOLENCE_SENSITIVE_THRESHOLD = 0.9208608865737915
VIOLENCE_MODERATE_THRESHOLD = 0.9954879879951477
VIOLENCE_CERTAIN_THRESHOLD = 0.9997246861457825

# Selected nine-source YOLOv9-T fine-tune. Epoch 6 was selected by the
# 0.5 * F0.5 + 0.5 * fitness benchmark; thresholds were calibrated on its
# validation split and locked before test evaluation.
WEAPON_MODEL_VERSION = "yolov9-t-selected-9source-f05-ft-20261011-193754-83560d-e06"
WEAPON_MODEL_CLASS_NAMES = ("knife", "gun")
WEAPON_CLASS_THRESHOLDS = {
    "sensitive": {"knife": 0.28156810998916626, "gun": 0.12619423866271973},
    "moderate": {"knife": 0.5161489248275757, "gun": 0.3640787899494171},
    "certain": {"knife": 0.6588038802146912, "gun": 0.6444171071052551},
}

# Thresholds for the previously deployed BREV hard-negative Epoch 5 model.
# Migrate exact untouched defaults, while leaving per-class operator edits alone.
PREVIOUS_HARD_NEGATIVE_WEAPON_CLASS_THRESHOLDS = {
    "sensitive": {"knife": 0.2170601636171341, "gun": 0.18463186919689178},
    "moderate": {"knife": 0.44626009464263916, "gun": 0.4098173975944519},
    "certain": {"knife": 0.6011608242988586, "gun": 0.4950620234012604},
}

# Previous deployed checkpoint defaults. Existing SQLite settings that still
# use these exact values move to the current validation-calibrated defaults;
# per-class operator edits are retained.
PREVIOUS_BREV_WEAPON_CLASS_THRESHOLDS = {
    "sensitive": {"knife": 0.15379296243190765, "gun": 0.22764818370342255},
    "moderate": {"knife": 0.5272320508956909, "gun": 0.41140204668045044},
    "certain": {"knife": 0.6442655324935913, "gun": 0.5589425563812256},
}

# Previous deployed Mall-negative checkpoint defaults. Upgrade only exact
# matches so individually calibrated operator settings remain untouched.
PREVIOUS_MALL_NEGATIVE_WEAPON_CLASS_THRESHOLDS = {
    "sensitive": {"knife": 0.3433084189891815, "gun": 0.19067250192165375},
    "moderate": {"knife": 0.4541761577129364, "gun": 0.41610413789749146},
    "certain": {"knife": 0.7339307069778442, "gun": 0.525465726852417},
}

# Previous deployed defaults. Used only to migrate untouched defaults; any
# operator-edited threshold is retained when the old long_gun key becomes gun.
PREVIOUS_WEAPON_CLASS_THRESHOLDS = {
    "sensitive": {"knife": 0.2014363706111908, "gun": 0.4412950575351715},
    "moderate": {"knife": 0.2014363706111908, "gun": 0.4412950575351715},
    "certain": {"knife": 0.30376800894737244, "gun": 0.5570604205131531},
}
