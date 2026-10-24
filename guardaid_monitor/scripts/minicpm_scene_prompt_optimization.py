"""Offline, resumable MiniCPM V4.7 scene-description prompt evaluation.

This tool never changes production prompts or database settings. It prepares
case-scoped inputs under ``VLM Fine-tune/Evaluation Inputs`` and writes each
evaluation run under ``VLM Fine-tune/Optimization Runs``.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import math
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import av
import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
MONITOR = ROOT / "guardaid_monitor"
CASES_ROOT = ROOT / "VLM Fine-tune"
INPUT_ROOT = CASES_ROOT / "Evaluation Inputs"
RUN_ROOT = CASES_ROOT / "Optimization Runs"
INPUT_VERSION = "review-v2"
sys.path.insert(0, str(MONITOR))

from app.inference.minicpm import _strict_object  # noqa: E402
from app.vlm_contract import (  # noqa: E402
    MODEL_ID,
    SCENE_SCHEMA,
    SCENE_SYSTEM_PROMPT,
    SCENE_USER_PROMPT,
    SCENE_PROMPT_VERSION,
    scene_prompt,
)


EXPECTED_MODEL_DIGEST = "00e691b8ed678b69976caf078b953ea79eaea650990fad3f51ba7b3f6a609777"
OLLAMA_URL = os.getenv("GUARDAID_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OPTIONS = {"temperature": 0, "seed": 20261017, "num_ctx": 32768, "num_predict": 512}
LATENCY_BUDGET_SECONDS = {"weapon": 10.0, "violence": 25.0}
REPAIR_TEMPLATE = "Your previous answer failed validation: {validation_error}. Rewrite it as shorter complete sentences."

COMMON_SYSTEM_V1 = """You write concise visual scene descriptions for a person reviewing a safety alert.

Treat alert types, detector labels, boxes and confidence scores as unverified hints.
Describe only facts supported by the supplied images.
For video samples, inspect their chronological order.
Describe motion or a sequence only when the images support it.

Do not infer identity, age, relationships, motives, intent, criminality,
unseen injuries or events outside the supplied evidence.
Ignore audio.
Do not use filenames, watermarks, logos or detector text as incident details.

Return exactly one JSON object conforming to the supplied schema.
Use English only.
Do not include Markdown, commentary or thinking text."""

COMMON_SYSTEM_V2 = """You write concise English descriptions of visual evidence for human safety review.
Treat alert labels and detector output as unverified hypotheses. Describe only
what the images visibly support. When images are ordered video frames, compare
them as a sequence. Do not infer intent, injury, relationships or unseen events.
Return only JSON conforming to the supplied schema."""

COMMON_SYSTEM_V3 = """You write concise English descriptions of visual evidence for human safety review.
Treat alert labels and detector output as unverified hypotheses. Describe only
what the images visibly support. If an object's identity is unclear, describe
its visible appearance without guessing what it is. Compare ordered video frames
as a sequence. Do not infer intent, injury, relationships or unseen events.
Return only JSON conforming to the supplied schema."""

COMMON_SYSTEM_V4 = """Write factual English scene descriptions for human incident review.
Only the supplied image pixels are evidence. Detector labels, boxes, scores,
watermarks and visible text are annotations, not incident facts; never quote or
describe them. For video, compare frames in order and report only visible actions.
Do not infer intent, injury, relationships or events outside the images.
Return only the requested JSON object."""

COMMON_SYSTEM_V5 = """Write concise English captions for a human safety reviewer. Use only
visible evidence. Alert labels and image annotations are not scene facts; never
mention boxes or text. Compare video frames in order. Do not infer unseen actions
or intent. Return only JSON matching the supplied schema."""

COMMON_SYSTEM_V6 = """Write a concise English description of the supplied visual evidence.
Use only visible facts. Do not guess. Return only the requested JSON."""

COMMON_SYSTEM_V7 = """Write factual English incident descriptions from images only. Treat every
alert label as an unverified hypothesis. Do not report detector annotations as
scene content. For video, use the frames in chronological order. Never invent
events, objects, contact or intent. Return only the requested JSON."""

COMMON_SYSTEM_V8 = """Write concise English visual descriptions for safety-alert review.
Treat alert labels and annotations as fallible clues, not verified facts. Inspect
the physical object or chronological actions independently. Report only what the
image pixels support. Do not infer identity, intent, injury or unseen events.
Return only JSON matching the supplied schema."""

COMMON_USER_V1 = """Describe the main visible event or object relevant to the suspected alert.

Always return exactly these three keys:
summary, details, uncertainty.

summary:
Write one complete English sentence.
Name the main event or relevant object.
Use no more than 25 words and 240 characters.

details:
Write separate short English sentences.
Each item must add one distinct useful visual fact or action.
Prefer 4–8 words per item.
Never exceed 12 words or 80 characters per item.
End each item with a period.
Do not combine observations using commas or semicolons.
Avoid repeating the summary or another item.
Include multiple points when useful observations support them.
Use fewer points when little is visible.
Do not invent details to reach a point count.

uncertainty:
Choose exactly low, medium or high.
Assess uncertainty about the main suspected object or action.
Use low when it is clearly supported.
Use medium when important details remain partly unclear.
Use high when the main suspected object or action cannot be confirmed.

Check that every required key is present.
Keep all statements supported by the images."""

WEAPON_CONTEXT_V1 = """Task: Review a suspected weapon alert.
Suspected classes: {suspected_classes}.

The supplied images are evidence photographs in chronological order.
They may contain detector boxes, class names and confidence scores.

Independently inspect the highlighted objects and their surroundings.
A detector label does not establish that a weapon is visible.

Focus the summary on the suspected object and the visible situation.
Describe weapon-like physical features only when they are visible.
State whether an object is held, used or stationary only when supported.
A single photograph does not establish a movement sequence.

If a clearly supported ordinary object better explains the highlighted region,
describe that object and include one short point saying that the weapon alert
may be a false positive.

If the object is too small, blurred or occluded to identify,
state that the suspected weapon cannot be confirmed.
Do not invent an ordinary object to explain unclear evidence.
Do not treat missing detail as proof of a false alarm.

Do not infer that a weapon is loaded, functional or genuine from appearance alone.
Ordinary activity elsewhere in the scene does not rule out a visible weapon."""

VIOLENCE_CONTEXT_V1 = """Task: Review a suspected violence or physical-contact bullying alert.

The alert is an upstream model hypothesis.
Inspect the chronological video samples independently.

Focus the summary on the main interaction or activity.
When the samples support aggressive contact,
name the specific visible action and its observable sequence.

Do not infer an attack from energetic movement, proximity or a raised arm alone.
When ordinary interaction or another non-aggressive activity better explains
the samples, describe that activity.
Include one short point saying that the violence alert may be a false positive.

When contact or its nature cannot be determined,
state what is visible and what remains unclear.
Do not invent violence or confidently dismiss ambiguous contact.

Do not infer a history of bullying, intent or relationships from the clip."""

COMMON_USER_V2 = """Describe the main visible event or object relevant to the suspected alert.

Always return exactly these three keys: summary, details, uncertainty.

summary:
Write one complete English sentence naming the main visible event or object.
Aim for 12-18 words when possible; never exceed 25 words or 240 characters.

details:
Write separate short English sentences, one distinct useful fact per item.
Prefer 4-8 words per item; never exceed 12 words or 80 characters.
End each item with a period. Do not use commas or semicolons.
Do not repeat the summary. Include multiple points only when each adds a fact.
Do not invent details to reach a point count.

uncertainty:
Return exactly low, medium or high for uncertainty about the main suspected
object or action. Use low when clearly supported, medium when partly unclear,
and high when it cannot be confirmed.

Check all three keys and keep every statement supported by the images."""

WEAPON_CONTEXT_V2 = """Task: Review a suspected weapon alert.
Suspected classes: {suspected_classes}.

The supplied evidence photos are in chronological order and may show detector
boxes, class names and confidence scores. These are unverified hypotheses.
Inspect each highlighted object and its surrounding context independently.

Name the visible object in the summary. State a weapon-like feature only if it
is actually visible. If a clearly visible ordinary object explains the box,
name that object and state that the weapon alert may be a false positive.
If the target is too small, blurred or occluded, say it cannot be confirmed;
do not guess an alternative. Do not claim a single photo shows movement.
Do not infer that a weapon is genuine, loaded or functional."""

VIOLENCE_CONTEXT_V2 = """Task: Review a suspected violence or physical-contact bullying alert.
The alert is an unverified upstream hypothesis. Inspect the supplied frames in
chronological order before writing.

Actively check every frame and adjacent-frame transition for direct person-to-
person contact, including a slap, punch, kick, strike, forceful push or grapple.
If a specific aggressive contact is visibly supported in even one frame, name
that action directly in the summary and describe its visible sequence briefly.
Do not reduce a visible strike to talking, gesturing or generic physical contact.

Do not call raised arms, fast movement or proximity an attack without visible
contact or other supporting evidence. If no attack is visibly supported and an
ordinary activity better explains the clip, describe that activity and include
one short detail saying the violence alert may be a false positive. If the
contact remains ambiguous, state what is visible and what is unclear.

Do not infer bullying history, intent, injury or relationships."""

WEAPON_CONTEXT_V3 = """Task: Review a suspected weapon alert.
Suspected classes: {suspected_classes}.

Inspect each highlighted object independently; detector boxes, labels and scores
are unverified hints. The summary must identify the visible highlighted object
and, when the evidence allows, whether it supports the suspected weapon alert.
If it is clearly an ordinary item, name that item and say the alert may be a
false positive. Do not call a clearly identified ordinary item unconfirmed.
If the target itself cannot be identified because it is small, blurred or
occluded, say that the suspected weapon cannot be confirmed and do not guess.
Use details for visible object features or its relation to a person, not general
room background. Do not infer that a weapon is genuine, loaded or functional."""

VIOLENCE_CONTEXT_V3 = """Task: Review a suspected violence or physical-contact bullying alert.
The alert is an unverified hypothesis. Inspect every supplied frame in
chronological order and focus on what one person visibly does to another.

First determine whether there is direct contact or impact, such as a hand
striking a face or body, a kick, a forceful push or a grapple. Compare the
contact moment with the following frame for a visible reaction or separation.
When a specific strike is visible, name it in the summary; do not describe it
only as conversation, gesturing or generic physical contact. Do not let clothing
or room details replace the main interaction.

Distinguish an actual visible impact from a raised arm or gesture without
contact. If the action is ambiguous, describe exactly what can be seen (for
example, a hand reaching toward another person's face) and state that the
contact or impact is unclear; do not claim a completed strike. If no attack is
visibly supported and ordinary activity better explains the clip, describe that
activity and say the violence alert may be a false positive.

Keep detail points focused on separate phases of the interaction. Do not infer
bullying history, intent, injury or relationships."""

COMMON_USER_V4 = """Describe the main visible event or object relevant to the suspected alert.

Return exactly these three keys: summary, details, uncertainty.

summary: one concise, complete English sentence about the main event or object.
Aim for 8-16 words; maximum 25 words and 240 characters.

details: separate short, complete English sentences. Each gives one distinct
visual fact that helps explain the alert. Prefer 4-8 words; maximum 12 words
and 80 characters per item. End with a period. No commas or semicolons.
Do not use clothing or room details unless they help explain the alert.
Do not repeat the summary or add points just to reach a count.

uncertainty: exactly low, medium or high. Rate uncertainty about the main
suspected object or action, not about unrelated background details.

Use only the images. Include every key and obey the word and character limits."""

VIOLENCE_CONTEXT_V4 = """Task: Review a suspected violence or physical-contact bullying alert.
The alert label is only a hypothesis. Inspect the frames in time order.

Focus on the interaction between people, not their clothes or the room.
Look for a hand, foot or object contacting another person's body, and any
immediate reaction. If a slap, punch, kick, push or grapple is visibly shown,
name that specific action in the summary. Do not call a reach or gesture a
completed strike unless contact is visible. If only a reach is clear, describe
the reach and say the impact is unclear.

If no attack is visible and ordinary activity better explains the clip, name
that activity and say the violence alert may be a false positive. Do not infer
bullying history, intent, injury or relationships."""

VIOLENCE_CONTEXT_V5 = """Task: Describe a suspected violence or physical-contact bullying alert.
Treat the alert as a question, not a conclusion. Inspect the frames in order.

When people interact, describe their actions toward each other: who approaches,
what a hand, foot or object does, where it reaches or touches, and what happens
next. Put this interaction in the summary. Do not make clothing or the room the
main description.

If a hand or foot is near another person's body, describe that visible relation
(for example, a hand reaches toward a face). Call it a slap, punch, kick or push
only when the images support actual contact or impact. Do not say there was no
contact unless the sampled interaction clearly shows none. If contact is
uncertain, describe the movement and mark the uncertainty instead of guessing.

If there is no aggressive interaction and a benign activity explains the clip,
name that activity and state the alert may be a false positive. Do not infer
intent, injury, bullying history or relationships."""

COMMON_USER_V6 = """Describe the main visible event or object relevant to the suspected alert.

Return exactly three keys: summary, details, uncertainty.

summary: one complete English sentence about the most important visible action
or object. For a video, describe what changes over time, not just who is present
or where they stand. Maximum 25 words and 240 characters.

details: use separate complete English sentences for other useful visual facts.
Each point adds one distinct fact, starts with a capital letter and ends with a
period. Maximum 12 words and 80 characters per point. Do not repeat the summary.
Do not add clothing or background unless it explains the alert. Do not invent
points to fill space.

uncertainty: exactly low, medium or high. Rate uncertainty about the main
suspected action or object.

Use only the supplied images. Preserve the main visible action when contact or
intent is uncertain. Return only the JSON object."""

VIOLENCE_CONTEXT_V6 = """Task: Review a suspected violence or physical-contact bullying alert.
The alert is a question, not a conclusion. Compare all frames in chronological
order.

Identify the most important change in the interaction. Describe each person's
visible action toward the other in sequence: approach, arm or leg movement,
contact if visible, the other person's response, and what follows. Make that
interaction the summary; do not substitute clothing, room details, talking or
generic gestures when the frames show a more specific movement.

Distinguish reaching from confirmed contact. If a hand or foot moves toward a
person but contact is unclear, describe that movement and mark the contact as
uncertain. Name a strike, kick, push or grapple only when visible evidence
supports it. Do not declare that no contact occurred when a reach or interaction
is visible.

If no aggressive interaction is visible and a benign activity explains the
frames, describe that activity and state that the alert may be a false positive.
Do not infer intent, injury, bullying history or relationships."""

COMMON_USER_V7 = """Describe the main visible event or object relevant to the suspected alert.

Return exactly three keys: summary, details, uncertainty.

summary: one complete English sentence, maximum 25 words and 240 characters.
For a video interaction, name the moving person, the direct action and its target;
include the other person's visible response when supported. Do not summarize the
room, clothing or a generic conversation instead of the main action. For a still
image, name the visible object and what it is doing or how it is held.

details: separate complete English sentences, one useful fact per point. Start
each with a capital letter and end it with one period. Maximum 12 words and 80
characters per point. Do not repeat the summary or add irrelevant background.

uncertainty: exactly low, medium or high, based only on the main action or object.
Use only the supplied images. Do not fill gaps with guesses. Return only JSON."""

WEAPON_CONTEXT_V4 = """Task: Independently review a suspected weapon alert.
Suspected classes: {suspected_classes}.

Inspect the physical object inside each highlighted region. Detector class names,
red boxes and numeric scores are not scene content: never copy or describe them.
Do not repeat the suspected class as proof. Identify visible shape and features,
then state whether the object's appearance supports that suspicion.

If the visible object is clearly an ordinary item, name it and say the weapon
alert may be a false positive. If it is too small, blurred or blocked to identify,
say the suspected weapon cannot be confirmed; do not invent an alternative.
Describe whether a person holds or uses it only when visible. A single photo
cannot establish motion. Do not infer that a weapon is genuine, loaded or usable."""

VIOLENCE_CONTEXT_V7 = """Task: Review a suspected violence or physical-contact bullying alert.
Treat the label as a question. Compare the frames in time order.

The summary must state the main visible action, not just who is present. When
people interact, identify who moves, the direct movement and its target. Include
the other person's visible response if shown. Choose the most specific supported
action verb. Do not replace a visible approach, reach, touch, impact, fall or
withdrawal with only “talking,” “gesturing” or “conversation.”

Distinguish a reach from confirmed contact. If contact is unclear, report the
movement and say contact is uncertain. Name a strike, kick, push or grapple only
when visible. If no aggressive interaction is shown and a benign activity clearly
explains the clip, describe it and say the alert may be a false positive.
Do not infer intent, injury, bullying history or relationships."""

COMMON_USER_V8 = """Describe what the supplied images visibly show about the suspected alert.

Return exactly three keys: summary, details, uncertainty.

summary: one complete English sentence, maximum 25 words and 240 characters.
Lead with the main visible action or object, not the room or a list of people.

details: separate short, complete English sentences. Each adds one distinct
visual fact. Start with a capital letter and end with one period. Maximum 12
words and 80 characters each. Avoid irrelevant clothing or background.

uncertainty: exactly low, medium or high. Rate only the main suspected action
or object. Never invent facts. Return only the JSON object."""

WEAPON_CONTEXT_V5 = """Review the suspected weapon alert. Suspected classes: {suspected_classes}.

Look at the physical object inside the highlighted region. Detector labels,
colored boxes and confidence numbers are not evidence about object identity.
Never include those markings or numbers in the description.

Name the visible object. If it is clearly an ordinary item, explicitly say the
weapon alert may be a false positive. If you cannot identify it, say the suspected
weapon cannot be confirmed. Do not guess an alternative or claim a visible gun
or knife just because the detector label says so. Describe a person's handling
only when visible. Do not infer that a weapon is real, loaded or usable."""

VIOLENCE_CONTEXT_V8 = """Review a suspected violence or physical-contact bullying alert.
The alert is only a hypothesis. Compare the frames in chronological order.

Describe the main interaction in this order: who moves, the specific movement,
its target, and the other person's visible response. Use this summary pattern:
“[Person] [visible action] toward/at [target], while [visible response].” Fill
each part only with evidence from the frames. Do not use a generic conversation
or gesture description when a specific movement is visible.

Check hands, arms, legs and body position across neighboring frames. Distinguish
a reach from confirmed contact. If contact is unclear, describe the reach and
say contact is uncertain; do not claim a strike or say no contact occurred.
If no aggressive interaction is visible and a benign activity explains the clip,
name that activity and state the alert may be a false positive.
Do not infer intent, injury, bullying history or relationships."""

COMMON_USER_V9 = """Describe only the main visible event or object related to the alert.

Return exactly three JSON keys: summary, details, uncertainty.

summary: one complete English sentence, at most 25 words and 240 characters.
Name the main action or object, not the room or a list of people.

details: short, separate, complete English sentences. Each adds one useful fact.
Start each with a capital letter; end with one period. At most 12 words and 80
characters per item. No repetition or irrelevant clothing/background.

uncertainty: exactly low, medium or high. Use only the supplied images. Do not
guess. Return only JSON."""

WEAPON_CONTEXT_V6 = """Review the suspected weapon alert. Suspected classes: {suspected_classes}.

Look at the object itself, not the detector label. Never mention the colored box,
class text or confidence number. Name the visible object and its appearance.
If it is clearly an ordinary item, say the weapon alert may be a false positive.
If the object cannot be identified, say the suspected weapon cannot be confirmed.
Do not guess, repeat the detector label, or infer whether a weapon works or is real."""

WEAPON_CONTEXT_V7 = """Review a suspected weapon alert. Suspected classes: {suspected_classes}.
Colored outlines, boxes, class words and scores are detector annotations, not
objects in the scene. Ignore them when identifying the target and never describe
or quote them. Inspect the visible object and nearby context independently.
Name a gun, knife or ordinary item only when its visible shape supports that
identity. If the item is unclear, state that the suspected weapon cannot be
confirmed. If it is clearly an ordinary item, name it and say the alert may be a
false positive. Describe only visible appearance and position; do not infer that
a weapon is genuine, loaded or functional."""

WEAPON_CONTEXT_V8 = """Review the suspected weapon alert. Suspected class: {suspected_classes}.
Treat that class as a question, not a fact. Inspect the visible object inside the
highlighted area and identify it from its shape. Ignore the outline and all
overlay words or numbers. If the visible object is clearly ordinary, name it and
say the alert may be a false positive. If it cannot be identified, say the
weapon cannot be confirmed. Do not guess or describe the overlay."""

WEAPON_CONTEXT_V9 = """Suspected class: {suspected_classes}. This is only a hypothesis.
Identify the highlighted object from its visible shape. Do not quote labels,
numbers or describe the box. If it is clearly ordinary, name it and say the alert
may be false. If unclear, say the weapon cannot be confirmed. Do not infer that
it is genuine, loaded or functional."""

WEAPON_CONTEXT_V10 = """Task: Visually check the suspected weapon alert. Proposed class: {suspected_classes}.
The class may be wrong. Identify the marked physical object from the image itself.
Ignore the box and all text or numbers; do not mention them. If it is clearly an
ordinary item, name it and say the alert may be false. If unclear, say it cannot
be confirmed. Do not infer whether a weapon is real or functional."""

WEAPON_CONTEXT_V11 = """Visually review the suspected {suspected_classes} alert. Independently inspect
the marked physical object; the label may be wrong. Do not mention the box, label
or score. If the object is clearly an ordinary item, name it and say the weapon
alert may be a false positive. If its identity is unclear, say the weapon cannot
be confirmed. Do not infer that a weapon is real, loaded or functional."""

VIOLENCE_CONTEXT_V9 = """Review a suspected violence or physical-contact bullying alert.
Compare the frames in time order. Describe the main physical interaction, not a
generic conversation.

State who moves, the specific visible movement, its target, and the other person's
visible response. If a hand or foot reaches toward a body but contact is unclear,
describe the reach and say contact is unclear. Do not say no contact occurred
when an interaction is visible. Do not call it a strike unless impact is shown.
If the frames clearly show benign activity instead, name it and say the alert may
be a false positive. Do not infer intent, injury, bullying history or relationships."""

VIOLENCE_CONTEXT_V10 = """Review a suspected violence or physical-contact bullying alert.
The supplied image is a numbered storyboard. Read panels left to right, then top
to bottom; the printed timestamps are chronological.

Describe the main visible interaction across panels: who moves, the specific
movement, its target and the other person's visible response. Do not replace a
specific physical movement with a generic conversation or gesture. Distinguish
a reach from confirmed contact. If impact is unclear, describe the reach and say
contact is unclear. Do not infer intent, injury, bullying history or relationships.
If no aggressive interaction is visible and a benign activity explains the
storyboard, name it and say the alert may be a false positive."""

COMMON_USER_V10 = """Describe the main visible event or object relevant to the alert.

Return only a JSON object with exactly these keys: summary, details, uncertainty.

summary: one grammatical sentence naming the main visible action or object. For
video, state who does what to whom or what when the images show it. A room,
group, conversation, or generic gesture is not a substitute for a visible
directed action. Keep all uncertainty wording in the uncertainty field only.

details: zero or more short, complete English sentences. Each gives one distinct
useful visual fact. Each is at most 12 words and 80 characters. Do not repeat
the summary or add labels, explanations, or unsupported details.

uncertainty: exactly low, medium or high, based only on the supplied evidence."""

VIOLENCE_CONTEXT_V11 = """Review a suspected violence or physical-contact bullying alert.
Compare the sampled video frames in chronological order. Describe the clearest
directed physical action: identify the person moving, the visible movement and
its target. Include another person's visible response when clear. Prefer the
specific movement over a generic statement that people talk, gesture or interact.
Distinguish an approach or reach from confirmed contact; do not infer impact,
force, intent, injury or relationships. If the sequence instead shows ordinary
activity, name that activity and state that the alert may be a false positive."""

COMMON_USER_V11 = """Describe the main visible event or object related to the alert.

Return exactly one JSON object with summary, details and uncertainty.

summary: one complete English sentence, no more than 25 words. Name the main
visible action or object, not the setting or a list of people. For video, name
the person moving and the specific movement toward its visible target when clear.
Do not add a second event or an uncertainty label.

details: include only useful facts not already stated. Each is one short complete
English sentence. Prefer 4–8 words and never exceed 12 words or 80 characters.
Do not add background filler or repeat the summary.

uncertainty: exactly low, medium or high. Put uncertainty only in this field."""

VIOLENCE_CONTEXT_V12 = """The images are chronological samples from a suspected violence or
physical-contact bullying alert. Compare the people and their positions across
early, middle and late frames. First check for a directed movement toward another
person. If visible, state who moves, which body part moves, the target and the
visible response. If a hand or foot approaches but contact is not clear, describe
the approach without claiming contact. If no directed aggressive movement is
visible, describe the ordinary activity instead. The alert label alone is not
evidence of violence. Do not infer force, intent, injury or relationships."""

COMMON_USER_V12 = """Describe the main observable event related to the alert.

summary: one complete English sentence, at most 25 words. State the main visible
action or object, not the setting or a list of people. For video, use the clearest
frame-to-frame change. When supported, name the person, specific action and its
target or the other person's visible response. Do not add an uncertainty label.

details: separate short, complete English sentences with distinct useful facts.
Prefer 4–8 words; never exceed 12 words or 80 characters. Do not repeat the
summary or add background filler.

uncertainty: exactly low, medium or high. Keep uncertainty only in this field."""

COMMON_USER_V13 = """Describe the main observable event related to the alert.

Return exactly one JSON object with summary, details and uncertainty.

summary: one complete English sentence, at most 25 words. Start with a capital
letter. State the main visible action or object, not the setting or a list of
people. For video, use the clearest frame-to-frame change. When supported, name
the person, specific action and target or visible response. Do not add an
uncertainty label.

details: separate short, complete English sentences with distinct useful facts.
Start every detail with a capital letter. Prefer 4–8 words; never exceed 12 words
or 80 characters. Do not repeat the summary or add background filler.

uncertainty: exactly low, medium or high. Keep uncertainty only in this field."""

COMMON_USER_V14 = """Describe the single most important visible object or action.

Return exactly one JSON object with these keys: summary, details, uncertainty.

summary: one complete English sentence, at most 25 words and 240 characters.
Start with a capital letter and end with one period. Name the main object or
action. Do not mention labels, boxes or scores. Never append an uncertainty label
or a second sentence.

details: zero or more distinct, useful visual facts. Each is one short, complete
English sentence, at most 12 words and 80 characters. Start with a capital
letter and end with one period. Include only facts that clarify the main event;
omit irrelevant background and clothing. Do not repeat the summary.

uncertainty: exactly low, medium or high. Put uncertainty only in this field."""

COMMON_USER_V15 = """Describe the main visible event or object.

Return only a JSON object with exactly summary, details and uncertainty.

summary: one complete English sentence, at most 25 words. Name the main visible
action or object. Start with a capital letter and end with one period. Never put
an uncertainty label here.

details: zero or more short, complete English sentences, each at most 12 words
and 80 characters. Each must add a distinct useful fact not already stated.
Capitalize the first word and end with one period. Never repeat a fact. Stop when
no distinct useful fact remains. Omit irrelevant clothing and background.

uncertainty: exactly low, medium or high, based only on the images."""

COMMON_USER_V16 = """Return exactly one JSON object with summary, details and uncertainty.

summary: one complete English sentence of no more than 25 words. Start with a
capital letter and end with one period. State the main visible action or object.

details: zero or more separate, short, complete English sentences. Each is at
most 12 words and 80 characters, starts with a capital letter and ends with one
period. Add distinct useful facts only. Never repeat a fact. Omit irrelevant
background. Do not put uncertainty in summary or details.

uncertainty: exactly low, medium or high, based only on the images. Return only
the JSON object."""

COMMON_USER_V17 = """Return one JSON object with exactly summary, details and uncertainty.

summary: one complete English sentence, at most 25 words. Start with a capital
letter and end with one period. Describe the main visible event or object.

details: zero or more distinct, useful facts in separate short complete English
sentences. Each is at most 12 words and 80 characters. Start with a capital
letter and end with one period. Do not repeat any fact or add background filler.
In details, use no commas, semicolons, question marks or exclamation marks; use
only one final period.

uncertainty: exactly low, medium or high. Use only the supplied images. Keep all
uncertainty wording out of summary and details. Return only JSON."""

COMMON_USER_V18 = """Return exactly one JSON object with summary, details and uncertainty.

summary: one complete English sentence of at most 25 words. State the main visible
object or action, not merely the setting. End with one period.

details: zero or more separate, short English sentences. Each must state one
distinct visible fact, use at most 12 words, and end with one period. Omit clothing,
background filler and repeated facts.

uncertainty: exactly low, medium or high, based only on the visible evidence."""

VIOLENCE_CONTEXT_V13 = """These are chronological frames from a suspected violence or
physical-contact alert. Focus on the visible body movement or contact, not the
alert label. If a person reaches toward, touches, pushes or kicks toward another
person, describe who does it and the visible target only when shown. Distinguish
a reach from confirmed contact. Do not infer force, intent or injury. If the
sequence clearly shows an ordinary activity instead, describe it and note that
the alert may be a false positive."""

VIOLENCE_CONTEXT_V14 = """These are chronological frames from a suspected violence or
physical-contact alert. Describe the most informative visible movement and
compare how the people change position. When a person moves a limb toward another,
state who moves, which limb, and the visible target. Say a person reaches toward
another only when the images show that movement; claim contact only when touch
is visible. Do not guess an unclear object's identity or invent a specific action
to explain it. Describe ordinary activity when that is what the frames show."""

VIOLENCE_CONTEXT_V15 = """These are chronological frames from a suspected violence or
physical-contact alert. Describe the clearest visible action, not a generic
interaction. When supported, name who moves, the body part, the target and the
other person's response. Distinguish a reach from visible contact. Do not call a
gesture an attack. If no directed aggressive action is visible, describe the
ordinary activity. Do not invent an object, strike, intent or injury."""

VIOLENCE_CONTEXT_V16 = """These are chronological video frames. Describe the clearest visible
physical action. Name who moves, the body part, the target and the other person's
response only when clear. Do not replace a visible directed action with a generic
conversation. Distinguish a reach from confirmed contact. If no aggressive action
is visible, name the ordinary activity and say the alert may be a false positive.
Never invent an object, action, intent or injury."""

VIOLENCE_CONTEXT_V17 = """Task: Explain the main action across these chronological frames. Name the
visible movement and response when clear. Describe a reach as a reach unless
contact is visible. Do not infer an attack from a gesture. If the activity is
clearly ordinary, name it and say the alert may be false. Do not guess."""

VIOLENCE_CONTEXT_V18 = """Review these chronological video frames without assuming the alert is correct.
If an aggressive action is visible, describe who moves, the action, its target
and the other person's response. Distinguish an approach from visible contact.
If ordinary activity is visible, describe it and say the alert may be a false
positive. If the action is unclear, describe only what can be seen."""

WEAPON_CONTEXT_V12 = """Proposed detector class: {suspected_classes}. The marked region only locates
the candidate object; it does not verify the class. Judge the object's visible
shape independently. If it is clearly an ordinary item, name it and say the alert
may be a false positive. If it is unclear, say the suspected weapon cannot be
confirmed. Do not infer that a weapon is real, loaded or functional."""

WEAPON_CONTEXT_V13 = """Suspected class: {suspected_classes}. The box only points to an object;
the class label and score are not visual evidence. Name the physical object that
the pixels support. If it is clearly ordinary, say it appears to be that item,
not the suspected weapon, and that the alert may be false. If you cannot identify
it, say the suspected weapon cannot be confirmed. Do not describe overlay text or
infer that a weapon is real, loaded or functional."""

VIOLENCE_CONTEXT_V19 = """These frames are in chronological order. Describe the clearest visible change
in the interaction: who moves, what they do, and the visible target or response.
Report contact only when it is shown. Do not replace a specific visible action
with a generic conversation or standing description. If the activity is clearly
ordinary, name it and say the alert may be a false positive. If unclear, describe
only what is visible; do not infer an attack, intent or injury."""

VIOLENCE_CONTEXT_V20 = """Compare these chronological frames and describe the clearest action between
people. State the visible movement and target; include the other person's response
only if shown. Describe contact only when visible. Do not summarize the clip as
merely talking, standing or interacting when a clearer movement appears in the
frames. If no aggressive action is visible, name the ordinary activity and say
the alert may be false. Do not invent props, contact, intent or injury."""

WEAPON_CONTEXT_V14 = """Suspected class: {suspected_classes}. The marked area only points to an object;
its label and score do not identify it. Name the visible object from its shape.
Do not describe the box, label, score or other overlay. If it is an ordinary item,
name it and say the weapon alert may be false. If its shape is unclear, say the
suspected weapon cannot be confirmed. Do not guess from a dark or distant shape."""

VIOLENCE_CONTEXT_V21 = """Review these frames in time order. Describe the clearest visible action and response.
Say who moves and toward whom or what only when the frames show it. Distinguish a
reach from visible contact. Describe a specific object only when its shape is
clear; otherwise use a neutral description such as 'a long light-colored object'.
Do not guess that an object is a bat or weapon. Do not infer a strike, intent,
force or injury from a gesture or from holding an object. If the frames show
ordinary conversation or gestures, describe those actions plainly."""

WEAPON_CONTEXT_V15 = """Review a suspected weapon alert. Proposed class: {suspected_classes}. Treat it as
a question, not proof. Identify the marked physical object from its visible shape.
Do not mention detector boxes, labels or scores. If it is clearly an ordinary item,
name it and say the alert may be false. If its identity is unclear, say the weapon
cannot be confirmed. Do not infer that a weapon is genuine, loaded or functional."""

VIOLENCE_CONTEXT_V22 = """Review these chronological frames from a suspected violence or physical-contact alert.
Describe the clearest directed movement: who moves, what they do, and the visible
target or response. Do not replace a clear reach, leg extension, push or fall with
generic conversation. Use a neutral action word when the exact action is unclear.
Name an object's type only when its shape is unmistakable; otherwise describe its
visible color or shape. Report contact or impact only when shown. Do not infer an
attack from gestures or from holding an object. Do not invent a strike, object,
intent, force or injury. If ordinary conversation or gestures are shown, describe
them plainly."""

COMMON_USER_V19 = """Return only the JSON object required by the schema. Use English.

summary: one complete sentence about the main visible action or object. Use at
most 25 words and 240 characters. Start with a capital letter and end with one
period.

details: zero or more distinct, useful visual facts. Each is one short, complete
sentence of at most 12 words and 80 characters. Start with a capital letter and
end with one period. Do not repeat a fact or add irrelevant background.

uncertainty: exactly low, medium or high, based on the visible evidence."""

WEAPON_CONTEXT_V16 = """Suspected weapon class: {suspected_classes}. This is only a hypothesis. Inspect the
marked physical object itself and name what its visible shape supports. Do not
mention detector markings. If it is clearly an ordinary item, name it and say the
alert may be false. If unclear, say the suspected weapon cannot be confirmed."""

VIOLENCE_CONTEXT_V23 = """Review the suspected violence alert in chronological frames. State the clearest
visible action, its target and the other person's response when shown. Distinguish
a reach from confirmed contact. If no aggressive action is visible, describe the
ordinary activity instead; gestures alone do not establish an attack. Do not
invent an object, contact, intent or injury."""

WEAPON_CONTEXT_V17 = """Suspected class: {suspected_classes}. Identify the physical object from the image,
not from the class hint. Describe the object and its visible state or use. Omit
detector annotations: do not write about a box, label, score or highlighting. If
the object is clearly ordinary, name it and say the alert may be false. If unclear,
say the suspected weapon cannot be confirmed."""

VIOLENCE_CONTEXT_V24 = """Review the chronological frames for the suspected violence alert. Name the most
specific visible movement and its target; include the other person's response
when clear. Call a reach a reach, and claim contact only when visible. If the clip
shows only ordinary gestures or activity, describe that activity rather than an
attack and say the alert may be false. Do not invent an object, strike, intent or
injury."""

WEAPON_CONTEXT_V18 = """Inspect the physical object inside the marked region. Proposed class: {suspected_classes}.
The class is a question, not evidence. Name the object supported by its visible
shape; omit boxes, labels and scores. If it is clearly ordinary, name it and say
the suspected weapon is not confirmed and the alert may be false. If unclear, say
the suspected weapon cannot be confirmed."""

VIOLENCE_CONTEXT_V25 = """Review these frames in chronological order. Describe an action only when the change
is visible across the frames. State who moves, the target and the response when
clear. Do not turn a reach into a push or impact. Say someone falls only if the
frames show them going down. Name a strike or kick only when that action is
visibly supported. If there is no clear aggression, describe the ordinary action
and say the alert may be false. Do not infer an object, intent or injury."""

WEAPON_CONTEXT_V19 = """Review a suspected weapon alert. Suspected class: {suspected_classes}. The class
and any words or numbers printed over the image are unverified annotations. Do not
repeat, quote or describe those annotations, boxes or highlights. Inspect the
physical object itself. Name a weapon only when its visible shape supports that
identification. If a clearly ordinary object explains the marked region, name it
and say the alert may be false. If the object cannot be identified, say the
suspected weapon cannot be confirmed. Do not infer that it is real, loaded or
functional."""

VIOLENCE_CONTEXT_V26 = """Review these frames in chronological order. Describe the clearest visible body
movement and its target; include another person's response only when shown. Use a
neutral movement description when the exact action is unclear. A limb moving near
someone does not by itself prove contact. Say contact, impact or a fall only when
the frames visibly show it. Do not assign an activity or object category from
color, shape or one isolated pose; use a neutral description when uncertain. Do
not invent props, strikes, intent, force, injury or events between sampled frames.
If the frames clearly show ordinary activity, describe it and say the alert may
be false."""

CANDIDATE_REPAIR_V4 = """Your prior response failed validation: {validation_error}.
Return only valid JSON with all three required keys. Keep summary to one sentence
of 15 words or fewer. Keep every detail sentence to 12 words or fewer. Remove
extra background and preserve the main visible action or object. Capitalize the
first word of summary and each detail. End summary and each detail with one
period only."""

CANDIDATE_REPAIR_V5 = """The previous JSON did not meet the required output format.
Rewrite it with exactly the keys summary, details and uncertainty. The summary
must contain only the main event or object as one grammatical sentence. Keep
uncertainty wording only in the uncertainty field; do not add labels or a second
sentence to the summary. Each detail must be one short, complete visual fact.
Keep all facts unchanged and supported by the images. Return only the JSON object."""

CANDIDATE_REPAIR_V6 = """The previous JSON did not meet the required output format.
Rewrite it with exactly the keys summary, details and uncertainty. Start summary
and every detail with a capital letter. Summary must be one complete sentence
about the main event or object. Each detail must be one short, complete visual
fact. Keep uncertainty only in the uncertainty field. Keep facts supported by
the images. Return only the JSON object."""

CANDIDATE_REPAIR_V7 = """Rewrite the same answer as valid JSON with exactly summary, details and
uncertainty. Keep the facts unchanged. Summary is one complete sentence ending
with one period. Each detail is one short complete sentence, starts with a
capital letter and ends with one period. Never put an uncertainty label in the
summary or details. Return only JSON."""

CANDIDATE_REPAIR_V8 = """Fix only the JSON format. Return exactly summary, details and uncertainty.
Keep the same facts. Use one complete summary sentence and short complete detail
sentences. Capitalize each sentence and end it with one period. Put uncertainty
only in its field. Do not repeat details. Return only JSON."""

CANDIDATE_REPAIR_V9 = """Rewrite the same facts as JSON with exactly summary, details and uncertainty.
Use one complete summary sentence and short complete detail sentences. Put
uncertainty only in its field. Return only JSON."""

CANDIDATE_REPAIR_V10 = """Return valid JSON with exactly summary, details and uncertainty. Keep the facts
unchanged. Use one complete summary sentence and separate short complete details.
Put uncertainty only in its field. Return only JSON."""

PROMPT_CANDIDATES = {
    "baseline": {
        "version": SCENE_PROMPT_VERSION,
        "system": SCENE_SYSTEM_PROMPT,
        "user_mode": "production-v9",
    },
    "candidate-v1": {
        "version": "minicpm_scene_prompt_candidate_v1",
        "system": COMMON_SYSTEM_V1,
        "user_mode": "candidate-v1",
    },
    "candidate-v2": {
        "version": "minicpm_scene_prompt_candidate_v2",
        "system": COMMON_SYSTEM_V1,
        "user_mode": "candidate-v2",
    },
    "candidate-v3": {
        "version": "minicpm_scene_prompt_candidate_v3",
        "system": COMMON_SYSTEM_V1,
        "user_mode": "candidate-v3",
    },
    "candidate-v4": {
        "version": "minicpm_scene_prompt_candidate_v4",
        "system": COMMON_SYSTEM_V1,
        "user_mode": "candidate-v4",
        "repair_template": CANDIDATE_REPAIR_V4,
    },
    "candidate-v5": {
        "version": "minicpm_scene_prompt_candidate_v5",
        "system": COMMON_SYSTEM_V1,
        "user_mode": "candidate-v5",
        "repair_template": CANDIDATE_REPAIR_V4,
    },
    "candidate-v6": {
        "version": "minicpm_scene_prompt_candidate_v6",
        "system": COMMON_SYSTEM_V1,
        "user_mode": "candidate-v6",
        "repair_template": CANDIDATE_REPAIR_V4,
    },
    "candidate-v7": {
        "version": "minicpm_scene_prompt_candidate_v7",
        "system": COMMON_SYSTEM_V1,
        "user_mode": "candidate-v7",
        "repair_template": CANDIDATE_REPAIR_V4,
    },
    "candidate-v8": {
        "version": "minicpm_scene_prompt_candidate_v8",
        "system": COMMON_SYSTEM_V1,
        "user_mode": "candidate-v8",
        "repair_template": CANDIDATE_REPAIR_V4,
    },
    "candidate-v9": {
        "version": "minicpm_scene_prompt_candidate_v9",
        "system": COMMON_SYSTEM_V1,
        "user_mode": "candidate-v9",
        "repair_template": CANDIDATE_REPAIR_V4,
    },
    "candidate-v10": {
        "version": "minicpm_scene_prompt_candidate_v10",
        "system": COMMON_SYSTEM_V1,
        "user_mode": "candidate-v10",
        "repair_template": CANDIDATE_REPAIR_V4,
    },
    "candidate-v11": {
        "version": "minicpm_scene_prompt_candidate_v11",
        "system": COMMON_SYSTEM_V1,
        "user_mode": "candidate-v11",
        "repair_template": CANDIDATE_REPAIR_V4,
    },
    "candidate-v12": {
        "version": "minicpm_scene_prompt_candidate_v12",
        "system": COMMON_SYSTEM_V1,
        "user_mode": "candidate-v12",
        "repair_template": CANDIDATE_REPAIR_V5,
    },
    "candidate-v13": {
        "version": "minicpm_scene_prompt_candidate_v13",
        "system": COMMON_SYSTEM_V1,
        "user_mode": "candidate-v13",
        "repair_template": CANDIDATE_REPAIR_V5,
    },
    "candidate-v14": {
        "version": "minicpm_scene_prompt_candidate_v14",
        "system": COMMON_SYSTEM_V2,
        "user_mode": "candidate-v14",
        "repair_template": CANDIDATE_REPAIR_V5,
    },
    "candidate-v15": {
        "version": "minicpm_scene_prompt_candidate_v15",
        "system": COMMON_SYSTEM_V3,
        "user_mode": "candidate-v15",
        "repair_template": CANDIDATE_REPAIR_V5,
    },
    "candidate-v16": {
        "version": "minicpm_scene_prompt_candidate_v16",
        "system": COMMON_SYSTEM_V3,
        "user_mode": "candidate-v16",
        "repair_template": CANDIDATE_REPAIR_V5,
    },
    "candidate-v17": {
        "version": "minicpm_scene_prompt_candidate_v17",
        "system": COMMON_SYSTEM_V3,
        "user_mode": "candidate-v17",
        "repair_template": CANDIDATE_REPAIR_V6,
    },
    "candidate-v18": {
        "version": "minicpm_scene_prompt_candidate_v18",
        "system": COMMON_SYSTEM_V4,
        "user_mode": "candidate-v18",
        "repair_template": CANDIDATE_REPAIR_V7,
    },
    "candidate-v19": {
        "version": "minicpm_scene_prompt_candidate_v19",
        "system": COMMON_SYSTEM_V5,
        "user_mode": "candidate-v19",
        "repair_template": CANDIDATE_REPAIR_V8,
    },
    "candidate-v20": {
        "version": "minicpm_scene_prompt_candidate_v20",
        "system": COMMON_SYSTEM_V6,
        "user_mode": "candidate-v20",
        "repair_template": CANDIDATE_REPAIR_V9,
    },
    "candidate-v21": {
        "version": "minicpm_scene_prompt_candidate_v21",
        "system": COMMON_SYSTEM_V7,
        "user_mode": "candidate-v21",
        "repair_template": CANDIDATE_REPAIR_V10,
    },
    "candidate-v22": {
        "version": "minicpm_scene_prompt_candidate_v22",
        "system": COMMON_SYSTEM_V8,
        "user_mode": "candidate-v22",
    },
    "candidate-v23": {
        "version": "minicpm_scene_prompt_candidate_v23",
        "system": COMMON_SYSTEM_V5,
        "user_mode": "candidate-v23",
        "repair_template": CANDIDATE_REPAIR_V8,
    },
    "candidate-v24": {
        "version": "minicpm_scene_prompt_candidate_v24",
        "system": COMMON_SYSTEM_V5,
        "user_mode": "candidate-v24",
        "repair_template": CANDIDATE_REPAIR_V8,
    },
    "candidate-v25": {
        "version": "minicpm_scene_prompt_candidate_v25",
        "system": COMMON_SYSTEM_V2,
        "user_mode": "candidate-v25",
        "repair_template": CANDIDATE_REPAIR_V5,
    },
    "candidate-v26": {
        "version": "minicpm_scene_prompt_candidate_v26",
        "system": COMMON_SYSTEM_V3,
        "user_mode": "candidate-v26",
        "repair_template": CANDIDATE_REPAIR_V8,
    },
    "candidate-v27": {
        "version": "minicpm_scene_prompt_candidate_v27",
        "system": COMMON_SYSTEM_V5,
        "user_mode": "candidate-v27",
        "repair_template": CANDIDATE_REPAIR_V8,
    },
    "candidate-v27-thinking": {
        "version": "minicpm_scene_prompt_candidate_v27_thinking",
        "system": COMMON_SYSTEM_V5,
        "user_mode": "candidate-v27",
        "repair_template": CANDIDATE_REPAIR_V8,
        "think": True,
    },
    "candidate-v28": {
        "version": "minicpm_scene_prompt_candidate_v28",
        "system": COMMON_SYSTEM_V5,
        "user_mode": "candidate-v28",
        "repair_template": CANDIDATE_REPAIR_V8,
    },
    "candidate-v28-thinking": {
        "version": "minicpm_scene_prompt_candidate_v28_thinking",
        "system": COMMON_SYSTEM_V5,
        "user_mode": "candidate-v28",
        "repair_template": CANDIDATE_REPAIR_V8,
        "think": True,
    },
    "candidate-v29": {
        "version": "minicpm_scene_prompt_candidate_v29",
        "system": COMMON_SYSTEM_V5,
        "user_mode": "candidate-v29",
        "repair_template": CANDIDATE_REPAIR_V8,
    },
    "candidate-v29-thinking": {
        "version": "minicpm_scene_prompt_candidate_v29_thinking",
        "system": COMMON_SYSTEM_V5,
        "user_mode": "candidate-v29",
        "repair_template": CANDIDATE_REPAIR_V8,
        "think": True,
    },
    "candidate-v25-thinking": {
        "version": "minicpm_scene_prompt_candidate_v25_thinking",
        "system": COMMON_SYSTEM_V2,
        "user_mode": "candidate-v25",
        "repair_template": CANDIDATE_REPAIR_V5,
        "think": True,
    },
}

# Normalized XYXY boxes around the visible ordinary object. These are controlled
# red-box fixtures, not predictions produced by YOLO. ACC-NC cases use the exact
# source annotation XML instead of a manually placed box.
NEGATIVE_ROIS = {
    "ADJ-NC-01": [0.14, 0.00, 0.92, 0.84],  # mop head and handle
    "ADJ-NC-02": [0.16, 0.41, 0.60, 0.69],  # blue bifold wallet in hand
    "ADJ-NC-03": [0.14, 0.43, 0.50, 0.77],  # wallet in hand
}
SOURCE_CLASS_NAMES = {"1": "knife", "4": "gun"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
MAX_WEAPON_IMAGES = 8


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return value if math.isfinite(value) else None


def exception_record(exc: Exception, traceback_text: str | None = None) -> dict:
    record = {"error": repr(exc), "exception_type": type(exc).__name__}
    if isinstance(exc, HTTPError):
        body = exc.read(8192)
        record.update({
            "http_status": exc.code,
            "http_reason": str(exc.reason),
            "http_response_body": body.decode("utf-8", errors="replace"),
            "http_response_body_truncated": len(body) >= 8192,
        })
    if traceback_text:
        record["traceback"] = traceback_text
    return record


def validate_scene_output(raw: str) -> dict:
    value = _strict_object(raw)
    summary = value["summary"].strip()
    if not summary or not summary[0].isupper():
        raise ValueError("MiniCPM summary must start with an uppercase letter")
    if not summary.endswith(".") or sum(summary.count(mark) for mark in ".!?") != 1:
        raise ValueError("MiniCPM summary must be exactly one complete sentence ending with a period")
    value["summary"] = summary
    for index, point in enumerate(value["details"]):
        normalized = point.strip()
        if not normalized or not normalized[0].isupper():
            raise ValueError(f"MiniCPM detail {index + 1} must start with an uppercase letter")
        if not normalized.endswith(".") or sum(normalized.count(mark) for mark in ".!?") != 1:
            raise ValueError(f"MiniCPM detail {index + 1} must be one complete sentence ending with a period")
        value["details"][index] = normalized
    return value


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def api(path: str, payload: dict | None = None, timeout: float = 600) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    request = Request(OLLAMA_URL + path, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def check_model() -> dict:
    version = api("/api/version", timeout=8)
    tags = api("/api/tags", timeout=12)
    processes = api("/api/ps", timeout=8)
    found = next((item for item in tags.get("models", []) if item.get("name") == MODEL_ID), None)
    if found is None:
        raise RuntimeError(f"Required model is not installed: {MODEL_ID}")
    if found.get("digest") != EXPECTED_MODEL_DIGEST:
        raise RuntimeError(f"Model digest changed: expected {EXPECTED_MODEL_DIGEST}, got {found.get('digest')}")
    other_models = [item.get("name") for item in processes.get("models", []) if item.get("name") != MODEL_ID]
    if other_models:
        raise RuntimeError(f"Other Ollama models are resident; refusing timed run without stopping them: {other_models}")
    return {"ollama_version": version.get("version"), "model": found.get("name"),
            "model_digest": found.get("digest"), "loaded_models_at_start": processes.get("models", [])}


def load_cases(split: str, case_ids: list[str] | None = None) -> list[dict]:
    manifest = CASES_ROOT / "case_manifest.csv"
    with manifest.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    if len(rows) != 40:
        raise RuntimeError(f"Expected exactly 40 cases in the frozen curation manifest, found {len(rows)}")
    wanted = {"adjustment": "Adjustment Set", "acceptance": "Acceptance Set", "all": None}[split]
    cases = []
    for row in rows:
        if wanted is not None and row["split"] != wanted:
            continue
        case_dir = CASES_ROOT / row["case_folder"]
        metadata_path = case_dir / "case.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("case_id") != row["case_id"]:
            raise RuntimeError(f"Case/manifest ID mismatch: {row['case_id']}")
        media_paths = [case_dir / "media" / Path(p).name for p in metadata.get("copied_media", [])]
        if not media_paths or any(not path.is_file() for path in media_paths):
            raise RuntimeError(f"Missing copied media for {row['case_id']}")
        expected_hashes = metadata.get("copied_media_sha256", [])
        actual_hashes = [sha256_file(path) for path in media_paths]
        if actual_hashes != expected_hashes:
            raise RuntimeError(f"Curated media hash mismatch for {row['case_id']}")
        row["metadata"] = metadata
        row["case_dir"] = case_dir
        row["media_paths"] = media_paths
        cases.append(row)
    if case_ids:
        requested = set(case_ids)
        available = {case["case_id"] for case in cases}
        missing = requested - available
        if missing:
            raise ValueError(f"Requested case IDs are not in split {split}: {sorted(missing)}")
        cases = [case for case in cases if case["case_id"] in requested]
        if len(cases) != len(requested):
            raise ValueError("Duplicate --case-id arguments are not allowed")
    return cases


def alert_kind(case: dict) -> str:
    return "weapon" if case["category"] in {
        "Real Gun", "Real Knives", "Nonweapon Confounders", "Visually Ambiguous Objects"
    } else "violence"


def weapon_classes_from_db(alert_id: str) -> list[str]:
    db_path = MONITOR / "runtime_data" / "database" / "guardaid.db"
    if not db_path.is_file():
        raise FileNotFoundError(f"Saved alert DB not found for detector-label metadata: {db_path}")
    import sqlite3

    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT weapon_class_scores_json FROM detection_events WHERE alert_id = ? ORDER BY detected_at, id",
            (alert_id,),
        ).fetchall()
    finally:
        connection.close()
    names = set()
    for (raw,) in rows:
        scores = json.loads(raw or "{}")
        names.update(name for name in scores if name in {"gun", "knife"})
    return sorted(names)


def normalized_to_xyxy(values: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    x1n, y1n, x2n, y2n = values
    x1 = max(0, min(width - 1, round(x1n * width)))
    y1 = max(0, min(height - 1, round(y1n * height)))
    x2 = max(x1 + 1, min(width - 1, round(x2n * width)))
    y2 = max(y1 + 1, min(height - 1, round(y2n * height)))
    return x1, y1, x2, y2


def read_yolo_boxes(path: Path, width: int, height: int, wanted_class: str | None = None) -> list[tuple[str, tuple[int, int, int, int]]]:
    return parse_yolo_box_lines(path.read_text(encoding="utf-8").splitlines(), width, height, wanted_class)


def parse_yolo_box_lines(lines: list[str], width: int, height: int, wanted_class: str | None = None) -> list[tuple[str, tuple[int, int, int, int]]]:
    output = []
    for line in lines:
        parts = line.split()
        if len(parts) != 5:
            continue
        name = SOURCE_CLASS_NAMES.get(parts[0])
        if name is None or (wanted_class and name != wanted_class):
            continue
        cx, cy, bw, bh = map(float, parts[1:])
        output.append((name, normalized_to_xyxy([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], width, height)))
    return output


def read_xml_boxes(path: Path, width: int, height: int, wanted_class: str | None = None) -> list[tuple[str, tuple[int, int, int, int]]]:
    import xml.etree.ElementTree as ET

    root = ET.parse(path).getroot()
    output = []
    for obj in root.findall("object"):
        name = (obj.findtext("name") or "").strip().lower()
        if wanted_class and name != wanted_class:
            continue
        box = obj.find("bndbox")
        if box is None:
            continue
        x1, y1, x2, y2 = [int(float(box.findtext(key) or 0)) for key in ("xmin", "ymin", "xmax", "ymax")]
        x1, y1 = max(0, min(width - 1, x1)), max(0, min(height - 1, y1))
        x2, y2 = max(x1 + 1, min(width - 1, x2)), max(y1 + 1, min(height - 1, y2))
        output.append((name, (x1, y1, x2, y2)))
    return output


def draw_detection(image: np.ndarray, class_name: str, box: tuple[int, int, int, int], confidence: float = 0.90) -> np.ndarray:
    result = image.copy()
    x1, y1, x2, y2 = box
    color = (49, 82, 255)
    cv2.rectangle(result, (x1, y1), (x2, y2), color, 2)
    cv2.putText(result, f"{class_name} {confidence:.2f}", (x1, max(18, y1 - 7)), cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 2, cv2.LINE_AA)
    return result


def encode_jpeg(image: np.ndarray, quality: int = 95) -> bytes:
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("Could not encode evaluation image")
    return encoded.tobytes()


def sampling_targets(duration: float, fps: float, included_seconds: float | None = None) -> tuple[float, list[float]]:
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(fps) or fps <= 0:
        raise ValueError("Video duration and sampling FPS must be positive finite numbers")
    included = min(duration, 60.0) if included_seconds is None else min(duration, included_seconds)
    if not math.isfinite(included) or included <= 0:
        raise ValueError("Included video duration must be positive and finite")
    count = max(1, math.ceil(included * fps - 1e-7))
    targets = [min((idx + 0.5) / fps, max(0.0, included - 1e-6)) for idx in range(count)]
    return included, targets


def controlled_weapon_variants(case: dict) -> list[dict]:
    case_id = case["case_id"]
    category = case["category"]
    media_path = case["media_paths"][0]
    image = cv2.imread(str(media_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"Unreadable weapon image: {media_path}")
    height, width = image.shape[:2]
    annotation_paths = sorted((case["case_dir"] / "source_annotations").glob("*"))
    result = []

    if category in {"Real Gun", "Real Knives"}:
        target_class = "gun" if category == "Real Gun" else "knife"
        boxes = []
        for annotation in annotation_paths:
            if annotation.suffix.lower() == ".txt":
                boxes.extend(read_yolo_boxes(annotation, width, height, target_class))
            elif annotation.suffix.lower() == ".xml":
                boxes.extend(read_xml_boxes(annotation, width, height, target_class))
        if not boxes:
            raise RuntimeError(f"No source {target_class} box for {case_id}; refuses to fabricate a positive ROI")
        annotated = image.copy()
        for name, box in boxes:
            annotated = draw_detection(annotated, name, box)
        result.append({"variant": "controlled_true_class", "detected_classes": [target_class],
                       "images": [encode_jpeg(annotated)], "controlled_fixture": True,
                       "fixture_note": "Source ground-truth box rendered in production style at fixed confidence 0.90; the original image and annotation remain unchanged."})
        return result

    if category == "Nonweapon Confounders":
        if case_id in NEGATIVE_ROIS:
            roi = normalized_to_xyxy(NEGATIVE_ROIS[case_id], width, height)
        else:
            roi = None
            for annotation in annotation_paths:
                if annotation.suffix.lower() == ".xml":
                    boxes = read_xml_boxes(annotation, width, height)
                    if boxes:
                        roi = boxes[0][1]
                        break
            if roi is None:
                raise RuntimeError(f"No manually or source-annotated ordinary-object ROI for {case_id}")
        for suspect in ("gun", "knife"):
            annotated = draw_detection(image, suspect, roi)
            result.append({"variant": f"controlled_false_{suspect}_hint", "detected_classes": [suspect],
                           "images": [encode_jpeg(annotated)], "controlled_fixture": True,
                           "fixture_note": "A human-selected ordinary-object ROI is shown with a controlled detector label and fixed confidence 0.90. It is not a YOLO prediction."})
        return result

    if category == "Visually Ambiguous Objects":
        classes = weapon_classes_from_db(case["alert_id"])
        if not classes:
            raise RuntimeError(f"No saved detector class metadata for unadjudicated alert {case_id}")
        paths = case["media_paths"][:MAX_WEAPON_IMAGES]
        images = [path.read_bytes() for path in paths]
        return [{"variant": "actual_saved_detector_evidence", "detected_classes": classes,
                 "images": images, "source_times": [], "controlled_fixture": False,
                 "fixture_note": "Exact saved JPEG evidence and saved predicted class names; predictions are not adjudicated truth."}]
    raise ValueError(f"Not a weapon category: {category}")


def video_samples(path: Path, fps: float, short_side: int | None, included_seconds: float | None = None) -> tuple[list[bytes], dict]:
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError(f"Video has no video stream: {path}")
        stream = container.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else float(container.duration or 0) / av.time_base
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"Video duration is invalid: {path}")
        included, targets = sampling_targets(duration, fps, included_seconds)
        count = len(targets)
        selected: list[tuple[float, float, np.ndarray]] = []
        previous = None
        previous_time = 0.0
        origin = None
        target_index = 0
        for frame in container.decode(stream):
            if frame.pts is None:
                raise ValueError(f"Frame has no presentation timestamp: {path}")
            absolute = float(frame.pts * frame.time_base)
            if origin is None:
                origin = absolute
            timestamp = absolute - origin
            while target_index < count and targets[target_index] < timestamp - 1e-7:
                chosen = previous if previous is not None else frame
                chosen_time = previous_time if previous is not None else timestamp
                selected.append((targets[target_index], previous_time if previous is not None else timestamp,
                                 chosen.to_ndarray(format="bgr24")))
                target_index += 1
            if target_index >= count:
                break
            previous, previous_time = frame, timestamp
        if previous is None and not selected:
            raise RuntimeError(f"No frames decoded from {path}")
        while target_index < count:
            selected.append((targets[target_index], previous_time, previous.to_ndarray(format="bgr24")))
            target_index += 1

    encoded = []
    sizes = []
    source_sizes = []
    for _, _, image in selected:
        height, width = image.shape[:2]
        source_sizes.append([int(width), int(height)])
        if short_side is None:
            resized = image
        else:
            factor = short_side / min(height, width)
            resized = cv2.resize(image, (round(width * factor), round(height * factor)), interpolation=cv2.INTER_AREA if factor <= 1 else cv2.INTER_LINEAR)
        encoded.append(encode_jpeg(resized, 95))
        sizes.append([int(resized.shape[1]), int(resized.shape[0])])
    return encoded, {"source_duration_seconds": duration, "included_seconds": included, "fps": fps,
                     "target_times_seconds": targets, "source_times_seconds": [round(item[1], 6) for item in selected],
                     "frame_count": len(encoded), "short_side": short_side,
                     "resolution_mode": "source_native" if short_side is None else "short_side_resize",
                     "source_image_sizes": source_sizes, "image_sizes": sizes}


def video_storyboard(images: list[bytes], target_times: list[float], columns: int = 2) -> tuple[bytes, dict]:
    """Pack unchanged sampled frames into a numbered row-major storyboard."""
    if not images or len(images) != len(target_times) or columns < 1:
        raise ValueError("Storyboard needs matching non-empty frame and timestamp lists")
    decoded = [cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) for data in images]
    if any(frame is None for frame in decoded):
        raise ValueError("Storyboard contains an unreadable sampled frame")
    heights = {frame.shape[0] for frame in decoded}
    widths = {frame.shape[1] for frame in decoded}
    if len(heights) != 1 or len(widths) != 1:
        raise ValueError("Storyboard frames must have a uniform size")
    tile_h, tile_w = decoded[0].shape[:2]
    label_h, gap = 30, 8
    rows = math.ceil(len(decoded) / columns)
    board_h = rows * (tile_h + label_h) + (rows + 1) * gap
    board_w = columns * tile_w + (columns + 1) * gap
    board = np.zeros((board_h, board_w, 3), dtype=np.uint8)
    labels = []
    for index, (frame, seconds) in enumerate(zip(decoded, target_times)):
        row, col = divmod(index, columns)
        x, y = gap + col * tile_w, gap + row * (tile_h + label_h)
        label = f"Frame {index + 1:02d} | {float(seconds):.3f}s"
        cv2.putText(board, label, (x + 4, y + 21), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (255, 255, 255), 1, cv2.LINE_AA)
        board[y + label_h:y + label_h + tile_h, x:x + tile_w] = frame
        labels.append({"index": index + 1, "time_seconds": float(seconds), "row": row + 1, "column": col + 1})
    ok, encoded = cv2.imencode(".jpg", board, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise RuntimeError("Failed to encode chronological storyboard")
    return encoded.tobytes(), {
        "representation": "numbered_row_major_storyboard",
        "source_frame_count": len(images),
        "model_image_count": 1,
        "columns": columns,
        "rows": rows,
        "tile_size": [tile_w, tile_h],
        "panel_order": labels,
        "encoded_size": [int(board_w), int(board_h)],
    }


def video_profile(duration: float, profile: str) -> tuple[float, int | None]:
    if profile not in {"M0", "M1", "M2", "M3", "M4", "M5", "M6"}:
        raise ValueError(f"Unsupported MC3 profile: {profile}")
    short_side = None if profile == "M6" else 360 if profile in {"M0", "M2", "M5"} else 720 if profile == "M4" else 480
    fps = (2.0 if duration <= 8.0 + 1e-6 else 1.0) if profile in {"M0", "M1", "M4", "M5", "M6"} else (4.0 if duration <= 8.0 + 1e-6 else 2.0)
    return fps, short_side


def image_profile_bytes(data: bytes, profile: str) -> tuple[bytes, dict]:
    if profile not in {"Y0", "Y1", "Y2"}:
        raise ValueError(f"Unsupported YOLO profile: {profile}")
    if profile == "Y0":
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Unable to decode weapon fixture JPEG")
        return data, {"profile": profile, "size_before": [int(image.shape[1]), int(image.shape[0])], "size_after": [int(image.shape[1]), int(image.shape[0])], "jpeg_sha256": sha256_bytes(data)}
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Unable to decode weapon fixture JPEG")
    height, width = image.shape[:2]
    maximum = 1920 if profile == "Y1" else 1280
    factor = min(1.0, maximum / max(width, height))
    if factor < 1:
        image = cv2.resize(image, (round(width * factor), round(height * factor)), interpolation=cv2.INTER_AREA)
    output = encode_jpeg(image, 95)
    return output, {"profile": profile, "size_before": [width, height], "size_after": [int(image.shape[1]), int(image.shape[0])], "jpeg_sha256": sha256_bytes(output)}


def prompts_for(prompt_id: str, kind: str, classes: list[str]) -> tuple[str, str, dict]:
    spec = PROMPT_CANDIDATES[prompt_id]
    if spec["user_mode"] == "production-v9":
        user = scene_prompt("weapon" if kind == "weapon" else "violence", classes)
        return spec["system"], user, {"version": spec["version"], "system_sha256": sha256_bytes(spec["system"].encode()), "user_sha256": sha256_bytes(user.encode())}
    if spec["user_mode"] == "candidate-v1":
        common_user = COMMON_USER_V1
        context = WEAPON_CONTEXT_V1.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V1
    elif spec["user_mode"] == "candidate-v2":
        common_user = COMMON_USER_V2
        context = WEAPON_CONTEXT_V2.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V2
    elif spec["user_mode"] == "candidate-v3":
        common_user = COMMON_USER_V2
        context = WEAPON_CONTEXT_V3.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V3
    elif spec["user_mode"] == "candidate-v4":
        common_user = COMMON_USER_V4
        context = WEAPON_CONTEXT_V1.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V4
    elif spec["user_mode"] == "candidate-v5":
        common_user = COMMON_USER_V4
        context = WEAPON_CONTEXT_V1.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V5
    elif spec["user_mode"] == "candidate-v6":
        common_user = COMMON_USER_V6
        context = WEAPON_CONTEXT_V3.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V6
    elif spec["user_mode"] == "candidate-v7":
        common_user = COMMON_USER_V7
        context = WEAPON_CONTEXT_V4.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V7
    elif spec["user_mode"] == "candidate-v8":
        common_user = COMMON_USER_V8
        context = WEAPON_CONTEXT_V5.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V8
    elif spec["user_mode"] == "candidate-v9":
        common_user = COMMON_USER_V9
        context = WEAPON_CONTEXT_V6.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V9
    elif spec["user_mode"] == "candidate-v10":
        common_user = COMMON_USER_V9
        context = WEAPON_CONTEXT_V6.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V9
    elif spec["user_mode"] == "candidate-v11":
        common_user = COMMON_USER_V9
        context = WEAPON_CONTEXT_V6.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V10
    elif spec["user_mode"] == "candidate-v12":
        common_user = COMMON_USER_V10
        context = WEAPON_CONTEXT_V6.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V11
    elif spec["user_mode"] == "candidate-v13":
        common_user = COMMON_USER_V11
        context = WEAPON_CONTEXT_V6.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V12
    elif spec["user_mode"] == "candidate-v14":
        common_user = COMMON_USER_V12
        context = WEAPON_CONTEXT_V6.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V13
    elif spec["user_mode"] == "candidate-v15":
        common_user = COMMON_USER_V12
        context = WEAPON_CONTEXT_V6.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V14
    elif spec["user_mode"] == "candidate-v16":
        common_user = COMMON_USER_V12
        context = WEAPON_CONTEXT_V7.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V14
    elif spec["user_mode"] == "candidate-v17":
        common_user = COMMON_USER_V13
        context = WEAPON_CONTEXT_V7.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V14
    elif spec["user_mode"] == "candidate-v18":
        common_user = COMMON_USER_V14
        context = WEAPON_CONTEXT_V8.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V15
    elif spec["user_mode"] == "candidate-v19":
        common_user = COMMON_USER_V15
        context = WEAPON_CONTEXT_V9.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V16
    else:
        common_user = COMMON_USER_V16
        context = WEAPON_CONTEXT_V10.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V17
    if spec["user_mode"] == "candidate-v20":
        user = context + "\n\n" + common_user
    elif spec["user_mode"] == "candidate-v21":
        common_user = COMMON_USER_V17
        context = WEAPON_CONTEXT_V11.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V18
        user = context + "\n\n" + common_user
    elif spec["user_mode"] == "candidate-v22":
        common_user = COMMON_USER_V18
        context = WEAPON_CONTEXT_V12.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V19
        user = context + "\n\n" + common_user
    elif spec["user_mode"] == "candidate-v23":
        common_user = COMMON_USER_V15
        context = WEAPON_CONTEXT_V13.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V20
        user = common_user + "\n\n" + context
    elif spec["user_mode"] == "candidate-v24":
        common_user = COMMON_USER_V15
        context = WEAPON_CONTEXT_V14.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V21
        user = common_user + "\n\n" + context
    elif spec["user_mode"] == "candidate-v25":
        common_user = COMMON_USER_V12
        context = WEAPON_CONTEXT_V15.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V22
        user = common_user + "\n\n" + context
    elif spec["user_mode"] == "candidate-v26":
        common_user = COMMON_USER_V19
        context = WEAPON_CONTEXT_V16.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V23
        user = context + "\n\n" + common_user
    elif spec["user_mode"] == "candidate-v27":
        common_user = COMMON_USER_V15
        context = WEAPON_CONTEXT_V17.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V24
        user = common_user + "\n\n" + context
    elif spec["user_mode"] == "candidate-v28":
        common_user = COMMON_USER_V15
        context = WEAPON_CONTEXT_V18.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V25
        user = common_user + "\n\n" + context
    elif spec["user_mode"] == "candidate-v29":
        common_user = COMMON_USER_V15
        context = WEAPON_CONTEXT_V19.format(suspected_classes=", ".join(classes) or "weapon") if kind == "weapon" else VIOLENCE_CONTEXT_V26
        user = common_user + "\n\n" + context
    else:
        user = common_user + "\n\n" + context
    return spec["system"], user, {"version": spec["version"], "system_sha256": sha256_bytes(spec["system"].encode()), "user_sha256": sha256_bytes(user.encode())}


def prepare_case_inputs(case: dict, profile: str) -> list[dict]:
    kind = alert_kind(case)
    case_id = case["case_id"]
    if kind == "weapon":
        variants = controlled_weapon_variants(case)
        prepared = []
        for variant in variants:
            profile_meta = []
            image_bytes = []
            for raw in variant["images"]:
                data, meta = image_profile_bytes(raw, profile)
                image_bytes.append(data)
                profile_meta.append(meta)
            prepared.append({**variant, "images": image_bytes, "input_metadata": profile_meta})
        return prepared

    video_path = next((path for path in case["media_paths"] if path.suffix.lower() in VIDEO_SUFFIXES), None)
    if video_path is None:
        raise RuntimeError(f"Video sample missing for MC3 case {case_id}")
    # Use the same duration-dependent policy as production; derive duration
    # from the actual video rather than the source label or manifest.
    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else float(container.duration or 0) / av.time_base
    fps, short_side = video_profile(duration, profile)
    images, metadata = video_samples(video_path, fps, short_side)
    if profile == "M5":
        storyboard, storyboard_metadata = video_storyboard(images, metadata["target_times_seconds"])
        metadata.update(storyboard_metadata)
        images = [storyboard]
    return [{"variant": "video_samples", "detected_classes": [], "images": images,
             "controlled_fixture": False, "fixture_note": "Video frames sampled by the selected uniform scene profile and presentation layout.",
             "input_metadata": metadata, "video_path": str(video_path)}]


def save_prepared_input(case_id: str, variant: dict, profile: str) -> dict:
    folder = INPUT_ROOT / INPUT_VERSION / profile / case_id / variant["variant"]
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, data in enumerate(variant["images"], start=1):
        path = folder / f"frame-{index:03d}.jpg"
        if path.exists() and sha256_file(path) != sha256_bytes(data):
            raise RuntimeError(f"Prepared-input cache differs; refusing overwrite: {path}")
        if not path.exists():
            path.write_bytes(data)
        paths.append(str(path.relative_to(ROOT)))
    result = {key: value for key, value in variant.items() if key != "images"}
    result["prepared_paths"] = paths
    result["input_hashes"] = [sha256_bytes(data) for data in variant["images"]]
    result["profile"] = profile
    return result


def profile_for_case(case: dict, yolo_profile: str, mc3_profile: str) -> str:
    return yolo_profile if alert_kind(case) == "weapon" else mc3_profile


def prepare_all(split: str, yolo_profile: str, mc3_profile: str, case_ids: list[str] | None = None) -> tuple[list[dict], dict]:
    cases = load_cases(split, case_ids)
    prepared_rows = []
    for index, case in enumerate(cases, start=1):
        profile = profile_for_case(case, yolo_profile, mc3_profile)
        variants = prepare_case_inputs(case, profile)
        for variant in variants:
            row = {
                "case_id": case["case_id"], "split": case["split"], "category": case["category"],
                "source_dataset": case["source_dataset"], "ground_truth_status": case["ground_truth_status"],
                "case_key": f"{case['case_id']}:{variant['variant']}", "alert_kind": alert_kind(case),
                "variant": variant["variant"],
                "detected_classes": variant["detected_classes"], "controlled_fixture": variant["controlled_fixture"],
                "fixture_note": variant["fixture_note"], "input_metadata": variant["input_metadata"],
                "prepared": save_prepared_input(case["case_id"], variant, profile),
                "_source_case": case,
            }
            prepared_rows.append(row)
        print(f"[prepared] {index}/{len(cases)} {case['case_id']} | {len(variants)} input variant(s)", flush=True)
    return prepared_rows, {"split": split, "input_version": INPUT_VERSION,
                          "profiles": {"weapon": yolo_profile, "violence": mc3_profile},
                          "case_count": len(cases), "case_ids": [case["case_id"] for case in cases],
                          "variant_count": len(prepared_rows)}


def refresh_runtime_images(prepared: dict) -> float:
    """Rebuild the exact input for each timed repeat so preprocessing is timed."""
    started = time.perf_counter()
    source_case = prepared.get("_source_case")
    if source_case is None:
        raise RuntimeError("Timed case is missing its frozen source metadata")
    profile = prepared["prepared"]["profile"]
    if prepared["alert_kind"] == "weapon":
        variants = controlled_weapon_variants(source_case)
        variant = next((item for item in variants if item["variant"] == prepared["case_key"].split(":", 1)[1]), None)
        if variant is None:
            raise RuntimeError(f"Controlled input variant disappeared: {prepared['case_key']}")
        images, metadata = [], []
        for raw in variant["images"]:
            encoded, info = image_profile_bytes(raw, profile)
            images.append(encoded)
            metadata.append(info)
    else:
        video_path = next((path for path in source_case["media_paths"] if path.suffix.lower() in VIDEO_SUFFIXES), None)
        if video_path is None:
            raise RuntimeError(f"Source video disappeared: {prepared['case_id']}")
        with av.open(str(video_path)) as container:
            stream = container.streams.video[0]
            duration = float(stream.duration * stream.time_base) if stream.duration is not None else float(container.duration or 0) / av.time_base
        fps, short_side = video_profile(duration, profile)
        images, metadata = video_samples(video_path, fps, short_side)
        if profile == "M5":
            storyboard, storyboard_metadata = video_storyboard(images, metadata["target_times_seconds"])
            metadata.update(storyboard_metadata)
            images = [storyboard]
    hashes = [sha256_bytes(data) for data in images]
    if hashes != prepared["prepared"]["input_hashes"]:
        raise RuntimeError(f"Rebuilt input differs from frozen cache: {prepared['case_key']}")
    prepared["_runtime_images"] = images
    prepared["_runtime_hashes"] = hashes
    prepared["_runtime_input_metadata"] = metadata
    return safe_number(time.perf_counter() - started)


def prompt_payload(prompt_id: str, prepared: dict) -> tuple[dict, dict]:
    system, user, prompt_meta = prompts_for(prompt_id, prepared["alert_kind"], prepared["detected_classes"])
    if prompt_id == "candidate-v11" and prepared["alert_kind"] != "weapon":
        if prepared["prepared"].get("profile") != "M5":
            raise ValueError("candidate-v11 requires the numbered M5 storyboard input profile")
    if prompt_id == "candidate-v10" and prepared["alert_kind"] != "weapon":
        input_meta = prepared["prepared"].get("input_metadata", {})
        target_times = input_meta.get("target_times_seconds", [])
        image_count = len(prepared.get("_runtime_images", prepared["prepared"].get("input_hashes", [])))
        if len(target_times) != image_count:
            raise ValueError("Frame timestamp map must match the chronological video image count")
        timeline = "; ".join(f"frame {index + 1}={float(seconds):.3f}s" for index, seconds in enumerate(target_times))
        user += "\n\nThe attached video images follow this timeline in order: " + timeline + "."
        prompt_meta["user_sha256"] = sha256_bytes(user.encode())
        prompt_meta["timestamp_map_sha256"] = sha256_bytes(timeline.encode())
    runtime_images = prepared.get("_runtime_images")
    if runtime_images is None:
        runtime_images = [(ROOT / path).read_bytes() for path in prepared["prepared"]["prepared_paths"]]
    images = [base64.b64encode(data).decode("ascii") for data in runtime_images]
    payload = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user, "images": images},
        ],
        "stream": False,
        "think": bool(PROMPT_CANDIDATES[prompt_id].get("think", False)),
        "keep_alive": "10m",
        "format": SCENE_SCHEMA,
        "options": OPTIONS,
    }
    meta = {"prompt_id": prompt_id, "think_requested": payload["think"], **prompt_meta,
            "schema_sha256": sha256_bytes(canonical_json(SCENE_SCHEMA).encode()),
            "image_count": len(images), "input_hashes": prepared.get("_runtime_hashes", prepared["prepared"]["input_hashes"])}
    return payload, meta


def run_one(prompt_id: str, prepared: dict) -> dict:
    pipeline_start = time.perf_counter()
    prep_seconds = refresh_runtime_images(prepared)
    payload, prompt_meta = prompt_payload(prompt_id, prepared)
    attempts = []
    repair_template = PROMPT_CANDIDATES[prompt_id].get("repair_template", REPAIR_TEMPLATE)
    user_prompt = payload["messages"][1]["content"]
    for attempt in range(1, 3):
        payload["messages"][1]["content"] = user_prompt
        started = time.perf_counter()
        try:
            response = api("/api/chat", payload, timeout=600)
            raw = response.get("message", {}).get("content", "")
            done_reason = response.get("done_reason")
            thinking = response.get("message", {}).get("thinking", "")
            row = {
                "attempt": attempt,
                "wall_seconds": safe_number(time.perf_counter() - started),
                "done_reason": done_reason,
                "raw_response": raw,
                "thinking_nonempty": bool(str(thinking).strip()),
                "total_seconds": safe_number((response.get("total_duration") or 0) / 1e9),
                "load_seconds": safe_number((response.get("load_duration") or 0) / 1e9),
                "prompt_eval_seconds": safe_number((response.get("prompt_eval_duration") or 0) / 1e9),
                "eval_seconds": safe_number((response.get("eval_duration") or 0) / 1e9),
                "prompt_eval_count": response.get("prompt_eval_count"),
                "eval_count": response.get("eval_count"),
            }
            try:
                if done_reason == "length":
                    raise ValueError("MiniCPM output reached num_predict limit")
                row["parsed"] = validate_scene_output(raw)
                row["schema_valid"] = True
                row["validation_error"] = None
            except (ValueError, TypeError) as exc:
                row["schema_valid"] = False
                row["validation_error"] = str(exc)
            attempts.append(row)
            if row["schema_valid"]:
                break
            if attempt == 1:
                user_prompt += "\n" + repair_template.format(validation_error=row["validation_error"])
        except Exception as exc:
            attempts.append({"attempt": attempt, "wall_seconds": safe_number(time.perf_counter() - started),
                             **exception_record(exc, traceback.format_exc()), "schema_valid": False})
            break

    total_wall = safe_number(time.perf_counter() - pipeline_start)
    successful = next((row for row in reversed(attempts) if row.get("schema_valid")), None)
    final_raw = successful.get("raw_response") if successful else (attempts[-1].get("raw_response") if attempts else None)
    return {
        "case_id": prepared["case_id"], "case_key": prepared["case_key"], "split": prepared["split"],
        "category": prepared["category"], "alert_kind": prepared["alert_kind"], "prompt_id": prompt_id,
        "think_requested": prompt_meta["think_requested"],
        "profile": prepared["prepared"]["profile"], "detected_classes": prepared["detected_classes"],
        "controlled_fixture": prepared["controlled_fixture"], "fixture_note": prepared["fixture_note"],
        "source_ground_truth_status": prepared["ground_truth_status"],
        "input_hashes": prepared.get("_runtime_hashes", prepared["prepared"]["input_hashes"]),
        "input_metadata": prepared.get("_runtime_input_metadata", prepared["input_metadata"]),
        "prompt_metadata": prompt_meta, "attempts": attempts, "first_pass_valid": bool(attempts and attempts[0].get("schema_valid")),
        "schema_valid": successful is not None, "result": successful.get("parsed") if successful else None,
        "raw_response": final_raw, "attempt_count": len(attempts), "total_wall_seconds": total_wall,
        "input_preparation_seconds": prep_seconds,
        "thinking_nonempty": any(bool(item.get("thinking_nonempty")) for item in attempts),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def warmup_vision_inputs(prepared_rows: list[dict], prompt_ids: list[str]) -> list[dict]:
    """Warm each selected prompt through one real image/video request per modality."""
    representatives = {}
    for row in prepared_rows:
        representatives.setdefault(row["alert_kind"], row)
    warmups = []
    for kind, prepared in representatives.items():
        for prompt_id in prompt_ids:
            payload, prompt_meta = prompt_payload(prompt_id, prepared)
            started = time.perf_counter()
            try:
                response = api("/api/chat", payload, timeout=600)
            except Exception as exc:
                failure = exception_record(exc, traceback.format_exc())
                failure.update({
                    "alert_kind": kind,
                    "case_id": prepared["case_id"],
                    "prompt_id": prompt_id,
                    "think_requested": prompt_meta["think_requested"],
                    "profile": prepared["prepared"]["profile"],
                    "image_count": prompt_meta["image_count"],
                    "stage": "vision_warmup",
                })
                print("[vision-warmup-failed] " + json.dumps(failure, ensure_ascii=False), flush=True)
                raise VisionWarmupError(f"Vision warm-up failed for {kind}/{prompt_id}", failure) from exc
            wall_seconds = safe_number(time.perf_counter() - started)
            warmup = {
                "alert_kind": kind,
                "case_id": prepared["case_id"],
                "prompt_id": prompt_id,
                "think_requested": prompt_meta["think_requested"],
                "profile": prepared["prepared"]["profile"],
                "image_count": prompt_meta["image_count"],
                "wall_seconds": wall_seconds,
                "done_reason": response.get("done_reason"),
                "load_seconds": safe_number((response.get("load_duration") or 0) / 1e9),
                "prompt_eval_seconds": safe_number((response.get("prompt_eval_duration") or 0) / 1e9),
                "eval_seconds": safe_number((response.get("eval_duration") or 0) / 1e9),
            }
            warmups.append(warmup)
            print(f"[vision-warmup] kind={kind} prompt={prompt_id} case={prepared['case_id']} "
                  f"profile={prepared['prepared']['profile']} images={prompt_meta['image_count']} "
                  f"think={prompt_meta['think_requested']} "
                  f"seconds={wall_seconds:.2f}; excluded from scoring/timing", flush=True)
    return warmups


class VisionWarmupError(RuntimeError):
    """A warm-up request failed before any scored evaluation rows were run."""

    def __init__(self, message: str, request_failure: dict):
        super().__init__(message)
        self.request_failure = request_failure


def run_eval(args) -> None:
    model_state = check_model()
    prepared_rows, prepared_meta = prepare_all(args.split, args.yolo_profile, args.mc3_profile, args.case_id)
    prompts = args.prompts.split(",")
    if any(prompt_id not in PROMPT_CANDIDATES for prompt_id in prompts):
        raise ValueError(f"Unknown prompt id; available: {', '.join(PROMPT_CANDIDATES)}")
    config = {"model": model_state, "prompts": {key: PROMPT_CANDIDATES[key] for key in prompts},
              "schema": SCENE_SCHEMA, "options": OPTIONS,
              "think": {key: bool(PROMPT_CANDIDATES[key].get("think", False)) for key in prompts},
              "latency_budget_seconds": LATENCY_BUDGET_SECONDS,
              "prepared": prepared_meta,
              "baseline_repair_template": REPAIR_TEMPLATE, "repeat_count": args.repeats,
              "evaluation_script_sha256": sha256_file(Path(__file__)),
              "all_input_hashes": {row["case_key"]: row["prepared"]["input_hashes"] for row in prepared_rows}}
    fingerprint = sha256_bytes(canonical_json(config).encode())
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    if args.run_dir:
        output = Path(args.run_dir).resolve()
        if not output.is_relative_to(RUN_ROOT.resolve()):
            raise RuntimeError(f"Run directory must remain under {RUN_ROOT}")
        if output.exists():
            metadata_path = output / "run.json"
            if not metadata_path.is_file():
                raise RuntimeError("Existing run directory has no run.json; refusing to reuse it")
            old = json.loads(metadata_path.read_text(encoding="utf-8"))
            if old.get("fingerprint") != fingerprint:
                raise RuntimeError("Run fingerprint differs; refusing to mix results")
        else:
            output.mkdir(parents=True, exist_ok=False)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = RUN_ROOT / f"{stamp}_{args.split}_{args.yolo_profile}_{args.mc3_profile}_{'+'.join(prompts)}"
        output.mkdir(parents=True, exist_ok=False)
    metadata_path = output / "run.json"
    if not metadata_path.exists():
        atomic_json(metadata_path, {"fingerprint": fingerprint, "config": config,
                                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                                    "status": "running", "production_config_modified": False})
    run_meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    run_meta["status"] = "running"
    run_meta.pop("failure", None)
    atomic_json(metadata_path, run_meta)

    jsonl = output / "results.jsonl"
    completed = set()
    if jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            old = json.loads(line)
            completed.add((old["case_key"], old["prompt_id"], old["repeat"]))

    total = len(prepared_rows) * len(prompts) * args.repeats
    done = len(completed)
    try:
        warmups = warmup_vision_inputs(prepared_rows, prompts)
        run_meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        run_meta["vision_warmups"] = warmups
        run_meta["timed_rows_exclude_model_warmup"] = True
        run_meta["warmup_policy"] = "one real multimodal request per selected prompt and alert kind; warm-up outputs are excluded"
        atomic_json(metadata_path, run_meta)
        print(f"[model-ready] {MODEL_ID} vision warm-up requests={len(warmups)}; warm timings begin now", flush=True)

        # Interleave paired prompt versions and alternate their order by case/repeat
        # to reduce systematic warmup or thermal-order bias.
        for repeat in range(1, args.repeats + 1):
            for case_index, prepared in enumerate(prepared_rows):
                order = list(prompts)
                if (case_index + repeat) % 2:
                    order.reverse()
                for prompt_id in order:
                    key = (prepared["case_key"], prompt_id, repeat)
                    if key in completed:
                        continue
                    print(f"[run] {done + 1}/{total} repeat={repeat}/{args.repeats} prompt={prompt_id} case={prepared['case_id']} variant={prepared['variant']}", flush=True)
                    row = run_one(prompt_id, prepared)
                    row["repeat"] = repeat
                    with jsonl.open("a", encoding="utf-8", newline="\n") as target:
                        target.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                    done += 1
                    print(json.dumps({"progress": f"{done}/{total}", "case_id": row["case_id"], "prompt": prompt_id,
                                      "variant": row["case_key"].split(":", 1)[1], "valid": row["schema_valid"],
                                      "first_pass_valid": row["first_pass_valid"], "seconds": row["total_wall_seconds"],
                                      "summary": row["result"]["summary"] if row["result"] else None,
                                      "details": row["result"]["details"] if row["result"] else None,
                                      "error": row["attempts"][-1].get("error") if row["attempts"] else None}, ensure_ascii=False), flush=True)
        run_meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        run_meta["status"] = "complete" if done >= total else "partial"
        run_meta["completed_rows"] = done
        run_meta["expected_rows"] = total
        atomic_json(metadata_path, run_meta)
        print(f"[complete] {done}/{total}; results={jsonl}", flush=True)
    except Exception as exc:
        run_meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        run_meta["status"] = "failed"
        run_meta["completed_rows"] = done
        run_meta["expected_rows"] = total
        run_meta["failure"] = {"exception_type": type(exc).__name__, "message": str(exc),
                                "traceback": traceback.format_exc()}
        if isinstance(exc, VisionWarmupError):
            run_meta["failure"]["request_failure"] = exc.request_failure
        atomic_json(metadata_path, run_meta)
        raise


def dry_run(args) -> None:
    cases = load_cases(args.split, args.case_id)
    count = 0
    for index, case in enumerate(cases, start=1):
        profile = profile_for_case(case, args.yolo_profile, args.mc3_profile)
        variants = prepare_case_inputs(case, profile)
        for variant in variants:
            prepared = {
                "case_id": case["case_id"], "case_key": f"{case['case_id']}:{variant['variant']}",
                "split": case["split"], "category": case["category"], "alert_kind": alert_kind(case),
                "detected_classes": variant["detected_classes"], "controlled_fixture": variant["controlled_fixture"],
                "fixture_note": variant["fixture_note"], "input_metadata": variant["input_metadata"],
                "prepared": save_prepared_input(case["case_id"], variant, profile),
                "ground_truth_status": case["ground_truth_status"], "variant": variant["variant"],
                "_source_case": case,
            }
            for prompt_id in args.prompts.split(","):
                if prompt_id not in PROMPT_CANDIDATES:
                    raise ValueError(f"Unknown prompt id: {prompt_id}")
                payload, meta = prompt_payload(prompt_id, prepared)
                if any(item.get("images") is None for item in payload["messages"] if item["role"] == "user"):
                    raise RuntimeError("User payload lacks images")
                count += 1
                print(f"[dry-run] {index}/{len(cases)} {case['case_id']} {variant['variant']} {prompt_id} | images={meta['image_count']} | schema={meta['schema_sha256'][:12]}", flush=True)
    print(f"[dry-run-complete] {count} prompt/input combinations; no Ollama call was made.", flush=True)


def reconcile_failed_run(args) -> None:
    output = Path(args.run_dir).resolve()
    if not output.is_relative_to(RUN_ROOT.resolve()):
        raise RuntimeError(f"Run directory must remain under {RUN_ROOT}")
    metadata_path = output / "run.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Missing run metadata: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("status") != "running":
        raise RuntimeError(f"Only a stale running run can be reconciled; current status is {metadata.get('status')!r}")
    traceback_text = Path(args.traceback_file).read_text(encoding="utf-8") if args.traceback_file else ""
    results_path = output / "results.jsonl"
    completed = sum(1 for line in results_path.read_text(encoding="utf-8").splitlines() if line.strip()) if results_path.exists() else 0
    config = metadata.get("config", {})
    prepared = config.get("prepared", {})
    prompts = config.get("prompts", {})
    repeats = int(config.get("repeat_count", 0))
    expected = int(prepared.get("variant_count", 0)) * len(prompts) * repeats
    metadata.update({
        "status": "failed",
        "completed_rows": completed,
        "expected_rows": expected,
        "failure": {
            "exception_type": args.failure_type,
            "message": args.failure_message,
            "traceback": traceback_text,
        },
    })
    atomic_json(metadata_path, metadata)
    print(f"[reconciled-failed] completed={completed}/{expected}; metadata={metadata_path}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("dry-run", "run", "reconcile-failed"))
    parser.add_argument("--split", choices=("adjustment", "acceptance", "all"), default="adjustment")
    parser.add_argument("--yolo-profile", choices=("Y0", "Y1", "Y2"), default="Y0")
    parser.add_argument("--mc3-profile", choices=("M0", "M1", "M2", "M3", "M4", "M5", "M6"), default="M0")
    parser.add_argument("--prompts", default="baseline,candidate-v1")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--case-id", action="append", default=[], help="Limit a smoke run to a frozen case ID; may be repeated")
    parser.add_argument("--run-dir", help="Existing run directory for exact-fingerprint resume")
    parser.add_argument("--failure-type", default="InterruptedRun")
    parser.add_argument("--failure-message", default="Run was left in running state after a process failure.")
    parser.add_argument("--traceback-file", help="Optional captured traceback text to preserve when reconciling a run")
    args = parser.parse_args()
    if args.repeats < 1:
        raise ValueError("--repeats must be positive")
    if args.mode == "reconcile-failed":
        if not args.run_dir:
            raise ValueError("reconcile-failed requires --run-dir")
        reconcile_failed_run(args)
    elif args.mode == "dry-run":
        dry_run(args)
    else:
        run_eval(args)


if __name__ == "__main__":
    main()
