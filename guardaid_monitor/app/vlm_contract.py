"""Shared MiniCPM policy, independent of the serving or model runtime."""
MODEL_ID = "openbmb/minicpm-v4.7:1b-q4_K_M"
BINARY_PROMPT_VERSION = "minicpm_binary_360p_v1"
SCENE_PROMPT_VERSION = "minicpm_unannotated_scene_v10"

SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "minLength": 1},
        "details": {"type": "array", "items": {
            "type": "string", "minLength": 1, "pattern": "^[^,;.!?]+[.!?]$",
        }},
        "uncertainty": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["summary", "details", "uncertainty"],
    "additionalProperties": False,
}
SCENE_SYSTEM_PROMPT = (
    "Write concise English visual scene descriptions for a human reviewing a safety alert. Inspect the "
    "images in time order. Alert labels are fallible model hypotheses, not verified facts. "
    "Independently inspect their visual evidence. Use only observable visual facts; ignore audio. Do not "
    "infer identity, age, relationship, motive, intent, guilt, injury or events outside "
    "the clip. Ignore watermarks, logos, detector text and confidence scores; never report them as scene facts. "
    "Return exactly one valid JSON object, without Markdown, commentary or thinking."
)
SCENE_USER_PROMPT = """Describe what happens so a reviewer understands the main visible event.
Return exactly the keys summary, details and uncertainty.
Summary: one complete English sentence, at most 25 words and 240 characters.
Describe the scene, not an answer such as 'Yes', 'YOLO is correct' or 'A gun was detected'.
Details: separate short English point-form sentences. Each point states one distinct
visible fact or action, ideally 4-8 words and never more than 12 words or 80 characters.
Use a complete sentence ending in a period, exclamation mark or question mark. Do not combine facts with commas,
semicolons or while. Split different people and separate actions into separate items.
Avoid repeating facts or the summary. Do not copy any example's objects or actions.
Include three or more points when useful distinct observations support them. There is
no maximum point count. Use fewer points or [] when evidence is insufficient; never
invent details to meet a count. Never truncate a sentence or word.
For visible violence name the specific act and sequence. Otherwise describe the actual
activity such as dancing or ordinary interaction. Energetic movement alone is not violence.
Uncertainty must be low, medium or high, describing confidence in the visual account.
Check that every key is present, JSON parses, and each point meets the length rules."""


def scene_prompt(alert_type="violence", detected_classes=()):
    if alert_type == "weapon":
        labels = sorted(set(detected_classes) & {"gun", "knife"})
        context = (
            "Suspected alert type: weapon detection. Suspected classes: " + (", ".join(labels) or "weapon") + ". "
            "YOLO proposed these classes; this is not proof that a weapon is visible. "
            "These are the first up to eight unannotated evidence photos in chronological order. "
            "Independently inspect the relevant objects and surroundings. Describe the main object and its position "
            "relative to visible people. State whether it is held or stationary only when visible. "
            "When a gun or knife is visible, describe that weapon without a false-positive warning. "
            "When a clearly identifiable nonweapon is visible instead, name that object and add one short detail "
            "stating that the weapon alert may be a false positive. Do not call a visible gun or knife an ordinary object. "
            "When the object is too small, blurred or occluded, say the suspected weapon cannot be confirmed. "
            "Missing detail alone does not prove a false alarm. Do not invent a bag, phone or weapon. "
            "Do not infer motion from one photo or assume that dancing excludes a visible weapon. "
            "Do not infer that a gun is loaded, functional or genuine. "
            "Write the visual account itself, not these instructions or a discussion of the detection model."
        )
    elif alert_type in {"violence", "physical_contact_bullying"}:
        context = (
            "Suspected alert type: violence / physical-contact bullying. This label is an upstream model hypothesis, "
            "not confirmation. Inspect the chronological video samples independently. "
            "The summary must address the suspected aggressive contact or its non-aggressive alternative. "
            "Describe specific visible aggressive contact and its sequence when supported. If ordinary interaction, dancing or another "
            "non-aggressive activity better explains the scene, describe it and say the violence alert may be a "
            "false positive in one short detail point; do not omit that assessment when describing only ordinary "
            "activity without visible attack evidence. If ambiguous, state the uncertainty instead of inventing violence or declaring innocence. "
            "Do not infer bullying history, intent or relationships from a short clip."
        )
    else:
        raise ValueError("Unsupported scene-description alert type")
    return SCENE_USER_PROMPT + "\n" + context

BINARY_SYSTEM_PROMPT = "Classify visual actions. Ignore audio. Reply only 0 or 1. Do not think."
BINARY_USER_PROMPT = (
    "Return 1 if the sequence shows credible aggressive physical contact or an apparent "
    "physical attack, such as punching, kicking, striking, forceful pushing, choking, "
    "aggressive grappling, dragging or repeated assaultive contact. Return 0 for ordinary "
    "movement, sports, non-aggressive play, dancing, crowding, accidental contact, a fall "
    "without attack, or frames that do not visibly support violence. Use only the images, "
    "not audio. Reply with exactly one character: 0 or 1."
)


def scene_sampling(duration: float) -> tuple[float, float]:
    """Return FPS and the included prefix duration, in incident-relative time."""
    import math
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("The VLM input must have a positive finite duration")
    return (2.0 if duration <= 8.0 + 1e-6 else 1.0), min(duration, 60.0)
