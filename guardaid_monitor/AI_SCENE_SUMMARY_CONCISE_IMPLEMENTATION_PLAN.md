# GuardAId Concise AI Scene Summary — Implementation Plan

## 1. Purpose

The current AI Scene Summary can appear as a long paragraph or raw/near-raw model output. This makes it slow for an authorised user to understand an alert, especially on a mobile phone.

The goal of this change is to present the most important information in a short, structured, point-form layout. The user should be able to understand the main observation and the supporting visual evidence within a few seconds, while the original video evidence remains available for verification.

This document records the required changes. It is an implementation plan only; the feature should be changed step by step and verified against the completion criteria below.

## 2. Design principles

- Keep the VLM output simple enough for Qwen3-VL 2B.
- Prefer observable visual facts over conclusions or interpretations.
- Do not ask the VLM to infer identity, intention, motive, injury, legal classification or severity.
- Do not show raw JSON, Markdown fences, escaped strings or parser errors to the user.
- Keep alert type, camera, timestamp and detector information separate from the VLM summary.
- Preserve the video clip and image evidence as the authoritative material for human review.
- Keep backward compatibility with existing stored summaries.
- Do not make the alert depend on the VLM summary being available; urgent alerts must remain visible while the summary is pending.

## 3. Minimal VLM schema

Keep the existing field names where possible to minimise model and migration risk. New scene-description responses should follow this shape:

```json
{
  "description": "Possible physical contact between two people",
  "visible_evidence": [
    "Two people are visible.",
    "One person moves an arm toward the other."
  ],
  "uncertainty": "medium"
}
```

### 3.1 Field rules

#### `description`

- One short observed action or scene statement.
- Target maximum: 12 English words.
- Hard display limit: approximately 120 characters.
- Use cautious wording such as `appears to`, `possible` or `visible` when the action is not clear.
- Do not state guilt, intent, motive, injury or certainty.

#### `visible_evidence`

- Zero to two short factual observations.
- Each item should target a maximum of 10 English words.
- Evidence must describe what is visible, not what the model assumes happened.
- Do not include identities, demographic attributes or unsupported object classifications.

#### `uncertainty`

- Only accept `low`, `medium` or `high`.
- Do not request a numerical confidence percentage from Qwen3-VL 2B.
- Invalid or missing values must be normalised to `high`.

### 3.2 Output validation

The backend must:

1. Remove Markdown code fences if the model adds them.
2. Parse the first valid JSON object.
3. Validate the three expected fields.
4. Trim excess text from `description` and evidence items.
5. Limit evidence to two items.
6. Normalise invalid uncertainty to `high`.
7. Fall back safely if the response is malformed.
8. Never expose raw malformed output in the mobile UI.

If no usable summary can be recovered, display:

```text
AI scene summary is unavailable. Review the video evidence.
```

The alert itself must remain available.

## 4. Backend and storage changes

### 4.1 Preserve structured content

The current scene-description flow requests JSON but may reduce it to the `description` string. Refine the flow so that the validated structured result is retained for API and UI use.

Preferred representation:

- Keep the existing `sceneDescription` compatibility field for older clients.
- Add or expose a typed `sceneSummary` object containing:
  - `description`
  - `visibleEvidence`
  - `uncertainty`
- Keep `descriptionStatus` (`pending`, `ready`, `failed` or the project’s existing equivalent).
- Do not require an immediate destructive database migration if the existing schema can safely store the validated JSON.

### 4.2 Historical records

- Do not delete existing scene summaries.
- Parse legacy JSON, double-encoded JSON and plain text on the read path.
- For an old long paragraph, use the first complete sentence or a safe display truncation for the concise card.
- Do not re-run VLM inference over all historical alerts automatically.
- Preserve the original evidence clip and existing raw/legacy value where the current data model requires it.

### 4.3 Source of truth separation

The following values must come from alert/system metadata, not from VLM free text:

- Alert type: weapon, violence or physical-contact bullying.
- Camera name.
- Detection timestamp.
- Detection source: YOLO, MC3-18, rule-based detector or VLM-assisted review.
- Evidence availability.
- Alert status.

The VLM summary is an explanation aid and must not replace the detector decision or the video evidence.

## 5. Mobile UI changes

### 5.1 Shared summary component

Create one reusable mobile widget/component for the concise AI Scene Summary so Home and Alert Detail use the same parsing and visual rules.

Recommended layout:

```text
AI Scene Summary

Observed
<one short description>

Key evidence
• <evidence item 1>
• <evidence item 2>

Uncertainty
<Low / Medium / High badge>
```

Rules:

- Hide empty sections instead of showing empty JSON values.
- Never render braces, JSON keys, escaped quotes or Markdown fences.
- Use readable labels: `Observed`, `Key evidence` and `Uncertainty`.
- Use a compact card on Home.
- Keep enough padding and line spacing for readability on small screens.
- Support long text and 1.3x/2.0x font scale without overflow.
- Keep the human-review disclaimer visible but visually secondary:
  `AI-generated summary. Verify against the video evidence.`

### 5.2 Home alert card

Home should prioritise fast scanning. Display:

```text
Observed: <short description>
Uncertainty: <badge>
```

The evidence bullets may be omitted from the compact card or shown only when there is enough space. The user can open Alert Detail for the complete concise summary.

### 5.3 Alert Detail page

Alert Detail should display:

1. System alert information.
2. The concise AI Scene Summary card.
3. The related video clip or weapon screenshot.
4. The AI-generated disclaimer.
5. Existing review/action controls.

The summary must not block video playback or alert actions while the VLM request is pending.

## 6. Prompt change

Update only the scene-description prompt. Do not change the established borderline binary decision prompt as part of this task.

The scene prompt should explicitly require:

- Exactly one JSON object.
- No Markdown or explanation outside JSON.
- A short `description` of no more than approximately 12 words.
- No more than two `visible_evidence` items.
- Short factual evidence items.
- `uncertainty` limited to `low`, `medium` or `high`.
- No identity, motive, intent, injury, severity or legal conclusion.
- No claims beyond the supplied clip.
- Ignore audio unless the project later explicitly adds audio support.

The implementation should retain a prompt version such as `scene_description_prompt_v2` so evaluation and debugging can distinguish old and new outputs.

## 7. Recommended examples

### Physical-contact alert

```json
{
  "description": "Possible physical contact between two people",
  "visible_evidence": [
    "Two people are visible.",
    "One person moves an arm toward the other."
  ],
  "uncertainty": "medium"
}
```

### Weapon alert

```json
{
  "description": "An object appears near a person’s hand",
  "visible_evidence": [
    "A small object is visible near the right hand.",
    "Part of the object is obscured."
  ],
  "uncertainty": "medium"
}
```

These are format examples only. The model must describe the supplied clip and must not copy the example content.

## 8. Implementation order

1. Add/update backend schema validation and structured scene-summary parsing.
2. Add legacy plain-text and malformed-JSON compatibility tests.
3. Update the scene-description prompt and prompt version.
4. Update API serialization to expose typed `sceneSummary` while retaining the old compatibility field.
5. Implement the shared mobile summary widget.
6. Replace the Home paragraph display with the compact form.
7. Replace the Alert Detail paragraph/raw JSON display with the structured form.
8. Add overflow, empty-state and pending-state handling.
9. Verify that alert creation and video/evidence playback are unaffected when VLM output is pending or invalid.
10. Update the mobile run/test documentation after implementation is verified.

## 9. Tests and acceptance criteria

### 9.1 Backend tests

- Valid three-field JSON is parsed and stored correctly.
- Markdown-fenced JSON is accepted and cleaned.
- Double-encoded JSON is accepted if safely recoverable.
- Missing `description` produces a safe unavailable state.
- More than two evidence items are limited to two.
- Long description/evidence values are bounded safely.
- Invalid uncertainty becomes `high`.
- Plain-text legacy summaries remain readable.
- Existing alerts, timestamps, evidence paths and statuses are unchanged.
- VLM failure does not prevent alert creation or notification.

### 9.2 Mobile tests

- Home shows a short observed line, not a paragraph or raw JSON.
- Alert Detail shows `Observed`, `Key evidence` and `Uncertainty`.
- Empty evidence is hidden gracefully.
- Pending VLM state is clear and does not look like a failure of the alert itself.
- Malformed and legacy summaries do not crash the app.
- No `{}`, JSON keys, escaped quotes or Markdown fences appear in the UI.
- Text does not overflow at the supported phone size and 1.3x/2.0x font scale.
- Video, screenshot, alert status and review controls remain usable.
- Demo and Live Mode use the same summary rendering rules.

### 9.3 Manual acceptance example

For a valid alert, an authorised user should be able to identify the observed action, read at most two evidence points and see the uncertainty level without scrolling through a long paragraph. The user must still be able to open the video evidence for verification.

## 10. Out of scope

- Changing the YOLO, MC3-18 or borderline alert decision logic.
- Adding complex VLM fields such as identity, age, gender, intent, motive, severity or injury classification.
- Asking the VLM to produce a numerical confidence score.
- Re-running all historical clips through Qwen3-VL.
- Making the VLM summary the source of the alert decision.
- Changing Firebase notification behaviour.
