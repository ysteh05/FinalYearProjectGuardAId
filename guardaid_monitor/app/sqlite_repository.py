from __future__ import annotations

import sqlite3
import json
import hashlib
import re
from contextlib import contextmanager
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from collections.abc import Iterator
from typing import Any

from .config import RuntimeConfig
from .schema_v15 import insert, utc
from .schema_v20 import initialize, validate
from .workflow_conflicts import WorkflowConflictError, stale_workflow_error
from .read_models_v17 import read_sql
from .repository import ConfigurationConflictError
from .vlm_contract import MODEL_ID, BINARY_PROMPT_VERSION, SCENE_PROMPT_VERSION
from .domain import (
    Alert,
    Camera,
    DetectionEventSnapshot,
    EvidenceRecord,
    INITIAL_ALERT_DELIVERY_MAX_AGE_SECONDS,
    SceneSummary,
    SystemSettings,
    WeaponClassThresholds,
    new_id,
    utc_now,
)
from .model_metadata import (
    PREVIOUS_BREV_WEAPON_CLASS_THRESHOLDS,
    PREVIOUS_HARD_NEGATIVE_WEAPON_CLASS_THRESHOLDS,
    PREVIOUS_MALL_NEGATIVE_WEAPON_CLASS_THRESHOLDS,
    PREVIOUS_MC3_E05_THRESHOLDS,
    PREVIOUS_SELECTED_NINE_SOURCE_E06_WEAPON_CLASS_THRESHOLDS,
    PREVIOUS_WEAPON_CLASS_THRESHOLDS,
    WEAPON_CLASS_THRESHOLDS,
    WEAPON_MODEL_VERSION,
    VIOLENCE_MODEL_VERSION,
    VIOLENCE_CERTAIN_THRESHOLD,
    VIOLENCE_INPUT_FRAMES,
    VIOLENCE_MODERATE_THRESHOLD,
    VIOLENCE_SENSITIVE_THRESHOLD,
    VIOLENCE_STRIDE_SECONDS,
    VIOLENCE_WINDOW_SECONDS,
)


def _iso(value: datetime | None = None) -> str:
    return utc(value or utc_now())


def _normalise_location(value: str) -> str:
    return " ".join(value.casefold().split())


def _load_weapon_thresholds(path: Path) -> WeaponClassThresholds:
    """Load either a deployment profile or the full calibration report."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    profiles = payload.get("profiles") if isinstance(payload, dict) else None
    if profiles is not None:
        if payload.get("status") != "complete" or not isinstance(profiles, list):
            raise ValueError("Weapon threshold calibration report is incomplete.")
        by_name = {
            str(profile.get("profile", "")).casefold(): profile.get("thresholds")
            for profile in profiles
            if isinstance(profile, dict)
        }
        required = {"sensitive", "moderate", "certain"}
        if not required.issubset(by_name) or any(
            not isinstance(by_name[name], dict) for name in required
        ):
            raise ValueError("Weapon threshold report is missing one or more sensitivity profiles.")
        payload = {name: by_name[name] for name in required}
    return WeaponClassThresholds.model_validate(payload)


_SCENE_SUMMARY_KEYS = (
    "description", "scenedescription", "scenesummary", "summary",
    "briefreason", "briefdescription", "narrative", "caption",
    "whatishappening", "actiondescription", "text", "content", "output", "result",
)
_SCENE_OBSERVATION_KEYS = (
    "visibleevidence", "visibleobservations", "observations", "evidence", "events", "actions",
)
_SCENE_METADATA_KEYS = {
    "uncertainty", "confidence", "severity", "raisealert", "decisionlabel",
    "status", "incidenttype", "type", "category", "model", "version", "score",
    "timestamp", "id",
}

_UNAVAILABLE_SCENE_SUMMARY = "AI scene summary is unavailable. Review the video evidence."
_LEGACY_FORMAT_ERROR = "Scene description could not be formatted."


def _scene_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _decode_scene_json(value: str) -> object | None:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        decoder = json.JSONDecoder()
        for opening, closing in (("{", "}"), ("[", "]")):
            start = value.find(opening)
            end = value.rfind(closing)
            if start < 0 or end <= start:
                continue
            try:
                return decoder.raw_decode(value[start:])[0]
            except json.JSONDecodeError:
                continue
    return None


def _scene_text_from_value(value: object, depth: int = 0) -> str | None:
    if depth > 5:
        return None
    if isinstance(value, str):
        text = value.strip().replace("```json", "").replace("```", "").strip()
        if not text:
            return None
        decoded = _decode_scene_json(text)
        if decoded is not None:
            return _scene_text_from_value(decoded, depth + 1)
        if text.startswith(("{", "[")):
            return None
        return text
    if isinstance(value, list):
        items = [
            text for item in value
            if (text := _scene_text_from_value(item, depth + 1))
        ]
        return ". ".join(dict.fromkeys(items)) or None
    if isinstance(value, dict):
        keyed = {_scene_key(key): item for key, item in value.items()}
        for key in (*_SCENE_SUMMARY_KEYS, *_SCENE_OBSERVATION_KEYS):
            if key in keyed:
                text = _scene_text_from_value(keyed[key], depth + 1)
                if text:
                    return text
        items = [
            text for key, item in value.items()
            if _scene_key(key) not in _SCENE_METADATA_KEYS
            and (text := _scene_text_from_value(item, depth + 1))
        ]
        return ". ".join(dict.fromkeys(items)) or None
    return None


def _readable_scene_text(value: str | None) -> str | None:
    """Return a safe human-readable summary, including older nested JSON outputs."""
    if not value:
        return value
    if value.strip() == _LEGACY_FORMAT_ERROR:
        return _UNAVAILABLE_SCENE_SUMMARY
    text = _scene_text_from_value(value)
    if text:
        return text.rstrip()
    candidate = value.strip()
    # VLMs occasionally truncate their JSON after already emitting the useful
    # description field. Recover that string rather than hiding it behind a
    # generic formatting error.
    for key in (*_SCENE_SUMMARY_KEYS, *_SCENE_OBSERVATION_KEYS):
        match = re.search(
            rf'["\']{re.escape(key)}["\']\s*:\s*("(?:\\.|[^"\\])*")',
            candidate,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        try:
            # Some VLM responses use JavaScript/Python-style \\' for an
            # apostrophe. It is not valid JSON, but the text remains safe and
            # useful once normalised before decoding the quoted string.
            recovered = json.loads(match.group(1).replace("\\'", "'"))
        except json.JSONDecodeError:
            continue
        if isinstance(recovered, str) and recovered.strip():
            return recovered.strip()[:800].rstrip()
    incomplete = re.search(r'["\']description["\']\s*:\s*["\'](.*)$', candidate, flags=re.I | re.S)
    if incomplete:
        # The generated response may hit its output limit before it closes the
        # description quote. Return only complete prose (or a clearly marked
        # shortened fragment), never the raw JSON prefix.
        fragment = incomplete.group(1).replace("\\n", " ").replace("\\'", "'").strip()
        next_field = re.search(r'",\s*["\'](?:visible_|uncertainty|confidence|summary)', fragment, flags=re.I)
        if next_field:
            fragment = fragment[:next_field.start()].rstrip()
        last_stop = max(fragment.rfind("."), fragment.rfind("!"), fragment.rfind("?"))
        if last_stop >= 40:
            return fragment[: last_stop + 1][:800].rstrip()
        if fragment:
            shortened = fragment[:800].rstrip()
            boundary = shortened.rfind(" ")
            return (shortened[:boundary] if boundary >= 40 else shortened).rstrip(" ,;:") + "…"
    if candidate.startswith(("{", "[")) or "```json" in candidate.casefold():
        return _UNAVAILABLE_SCENE_SUMMARY
    return candidate.replace("```", "").strip()


def _readable_scene_record(display_text: str | None, structured_json: str | None) -> str | None:
    """Prefer saved prose, but recover from structured data if legacy prose is a placeholder."""
    display = _readable_scene_text(display_text)
    if display and display != _UNAVAILABLE_SCENE_SUMMARY:
        return display
    structured = _readable_scene_text(structured_json)
    if structured and structured != _UNAVAILABLE_SCENE_SUMMARY:
        return structured
    return display or structured


def _scene_summary_value(value: object, depth: int = 0) -> SceneSummary | None:
    """Recover a typed summary from current, nested, or double-encoded data."""

    if depth > 4:
        return None
    if isinstance(value, str):
        decoded = _decode_scene_json(value.strip().replace("```json", "").replace("```", "").strip())
        return _scene_summary_value(decoded, depth + 1) if decoded is not None else None
    if not isinstance(value, dict):
        return None
    try:
        return SceneSummary.model_validate(value)
    except Exception:
        pass
    for key in ("sceneSummary", "scene_summary", "result"):
        nested = value.get(key)
        if isinstance(nested, (dict, str)):
            summary = _scene_summary_value(nested, depth + 1)
            if summary is not None:
                return summary
    return None


def _write_mobile_preferences(connection, enabled, selected_json, actor, now):
    connection.execute("UPDATE roles SET compat_borderline_selected=CASE WHEN id IN (SELECT value FROM json_each(?)) THEN 1 ELSE 0 END",(selected_json,))
    connection.execute("UPDATE mobile_settings SET borderline_alert_enabled=?,updated_by_user_id=?,updated_at=? WHERE id='global'",(enabled,actor,now))


def _output_format(display, structured):
    if structured is not None:
        value=json.loads(structured)
        return 'summary' if isinstance(value,dict) and set(('description','visible_evidence','uncertainty')).issubset(value) else 'raw_json'
    return 'text' if display is not None else 'none'


def _scene_storage_values(value: Any) -> tuple[str | None, str | None]:
    """Return concise display text and validated structured JSON when available."""

    if isinstance(value, SceneSummary):
        parsed: object = value.model_dump(mode="json")
        return value.description, json.dumps(parsed, ensure_ascii=False)
    if isinstance(value, dict):
        parsed = value
        summary = _scene_summary_value(parsed)
        if summary is not None:
            return summary.description, json.dumps(
                summary.model_dump(mode="json"), ensure_ascii=False
            )
        display = _readable_scene_text(json.dumps(parsed, ensure_ascii=False))
        return display, json.dumps(parsed, ensure_ascii=False)
    display = _readable_scene_text(value)
    if not value:
        return display, None
    parsed = _decode_scene_json(str(value).strip().replace("```json", "").replace("```", "").strip())
    if isinstance(parsed, dict):
        summary = _scene_summary_value(parsed)
        if summary is not None:
            return summary.description, json.dumps(
                summary.model_dump(mode="json"), ensure_ascii=False
            )
    return display, json.dumps(parsed, ensure_ascii=False) if isinstance(parsed, (dict, list)) else None


def _scene_summary_from_storage(
    display_text: str | None, structured_json: str | None
) -> SceneSummary | None:
    """Recover the typed summary without breaking legacy display values."""

    if structured_json:
        summary = _scene_summary_value(structured_json)
        if summary is not None:
            return summary
    if display_text:
        readable = _readable_scene_text(display_text) or display_text
        if readable in {_UNAVAILABLE_SCENE_SUMMARY, "Scene description is not available yet."}:
            return None
        try:
            return SceneSummary(description=readable)
        except Exception:
            return None
    return None


class SQLiteRepository:
    """Normalized, transactional SQLite implementation of the operational repository."""

    def __init__(self, config: RuntimeConfig) -> None:
        self._database_path = config.database_path.resolve()
        if self._database_path.is_file() and self._database_path.stat().st_size:
            with closing(sqlite3.connect(self._database_path.as_uri()+"?mode=ro", uri=True)) as existing:
                version = validate(existing)
                if version < 20:
                    backup = self._database_path.with_name(self._database_path.stem + '.before-scene-v20.db')
                    if not backup.exists():
                        with closing(sqlite3.connect(backup)) as destination:
                            existing.backup(destination)
                if version in {15, 16, 17, 18}:
                    backup = self._database_path.with_name(self._database_path.stem + '.before-scene-v19.db')
                    if not backup.exists():
                        with closing(sqlite3.connect(backup)) as destination:
                            existing.backup(destination)
                if version in {15, 16}:
                    backup = self._database_path.with_name(self._database_path.stem + ".before-workflow-v17.db")
                    if not backup.exists():
                        with closing(sqlite3.connect(backup)) as destination:
                            existing.backup(destination)
                if version in {15, 16, 17}:
                    backup = self._database_path.with_name(self._database_path.stem + '.before-media-v18.db')
                    if not backup.exists():
                        with closing(sqlite3.connect(backup)) as destination:
                            existing.backup(destination)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._migration_path = Path(__file__).resolve().parents[1] / "migrations" / "001_initial.sql"
        self._lock = RLock()
        self._migrate()
        with self._connect() as connection:
            has_config = connection.execute(read_sql("SELECT 1 FROM config_versions LIMIT 1")).fetchone()
        if has_config and config.maintenance_mode:
            return
        if not has_config:
            settings = SystemSettings(updated_by="sqlite-bootstrap")
            if config.weapon_thresholds_path.is_file():
                calibrated = _load_weapon_thresholds(config.weapon_thresholds_path)
                settings = settings.model_copy(
                    update={
                        "weapon": settings.weapon.model_copy(
                            update={"class_thresholds": calibrated}
                        )
                    }
                )
            self.save_settings(settings)
        else:
            current = self.get_settings()
            if (
                current.config_version == 1
                and current.violence.segment_seconds == 5.0
                and current.violence.stride_seconds == 2.5
                and current.violence.detector_input_frames == 16
                and current.violence.sensitive_threshold == 0.009688
                and current.violence.moderate_threshold == 0.056
                and current.violence.certain_threshold == 0.710192
            ):
                self.save_settings(
                    current.model_copy(
                        update={
                            "violence": current.violence.model_copy(
                                update={"detector_input_frames": 20}
                            ),
                            "config_version": 2,
                            "updated_by": "migration-mc3-training-protocol-20f",
                            "updated_at": utc_now(),
                        }
                    )
                )
            current = self.get_settings()
            legacy_violence_defaults = (
                current.violence.segment_seconds == 5.0
                and current.violence.stride_seconds == 2.5
                and current.violence.detector_input_frames == 20
                and current.violence.sensitive_threshold == 0.009688
                and current.violence.moderate_threshold == 0.056
                and current.violence.certain_threshold == 0.710192
            )
            if legacy_violence_defaults:
                self.save_settings(
                    current.model_copy(
                        update={
                            "violence": current.violence.model_copy(
                                update={
                                    "segment_seconds": 1.0,
                                    "stride_seconds": 0.5,
                                    "detector_input_frames": 4,
                                    "sensitive_threshold": 0.016434572637081146,
                                    "moderate_threshold": 0.4920050799846649,
                                    "certain_threshold": 0.9982624650001526,
                                }
                            ),
                            "config_version": current.config_version + 1,
                            "updated_by": "migration-mc3-1s4f-20260930-e19",
                            "updated_at": utc_now(),
                        }
                    )
                )
            current = self.get_settings()
            # Upgrade only fields still carrying the previous deployed model's
            # defaults. Any setting changed by an operator remains untouched.
            previous_protocol_defaults = {
                "segment_seconds": 1.0,
                "stride_seconds": 0.5,
                "detector_input_frames": 4,
            }
            next_protocol_defaults = {
                "segment_seconds": VIOLENCE_WINDOW_SECONDS,
                "stride_seconds": VIOLENCE_STRIDE_SECONDS,
                "detector_input_frames": VIOLENCE_INPUT_FRAMES,
            }
            previous_threshold_defaults = {
                "sensitive_threshold": 0.016434572637081146,
                "moderate_threshold": 0.4920050799846649,
                "certain_threshold": 0.9982624650001526,
            }
            next_threshold_defaults = {
                "sensitive_threshold": VIOLENCE_SENSITIVE_THRESHOLD,
                "moderate_threshold": VIOLENCE_MODERATE_THRESHOLD,
                "certain_threshold": VIOLENCE_CERTAIN_THRESHOLD,
            }
            violence_updates = {
                field: next_protocol_defaults[field]
                for field, old_value in previous_protocol_defaults.items()
                if getattr(current.violence, field) == old_value
            }
            if all(
                getattr(current.violence, field) == old_value
                for field, old_value in previous_threshold_defaults.items()
            ):
                violence_updates.update(next_threshold_defaults)
            if violence_updates:
                self.save_settings(
                    current.model_copy(
                        update={
                            "violence": current.violence.model_copy(update=violence_updates),
                            "config_version": current.config_version + 1,
                            "updated_by": "migration-mc3-4s16f2-20261007-e05",
                            "updated_at": utc_now(),
                        }
                    )
                )
            current = self.get_settings()
            # Upgrade only the untouched previous MC3 Epoch 5 threshold triplet.
            if all(
                getattr(current.violence, field) == old_value
                for field, old_value in PREVIOUS_MC3_E05_THRESHOLDS.items()
            ):
                self.save_settings(
                    current.model_copy(
                        update={
                            "violence": current.violence.model_copy(
                                update={
                                    "sensitive_threshold": VIOLENCE_SENSITIVE_THRESHOLD,
                                    "moderate_threshold": VIOLENCE_MODERATE_THRESHOLD,
                                    "certain_threshold": VIOLENCE_CERTAIN_THRESHOLD,
                                }
                            ),
                            "config_version": current.config_version + 1,
                            "updated_by": f"migration-{VIOLENCE_MODEL_VERSION}",
                            "updated_at": utc_now(),
                        }
                    )
                )
            current = self.get_settings()
            # Move untouched historical defaults directly to the currently
            # deployed model. Keep operator-adjusted class values unchanged.
            threshold_profiles = {}
            for mode, previous_values in PREVIOUS_WEAPON_CLASS_THRESHOLDS.items():
                profile = getattr(current.weapon.class_thresholds, mode)
                next_values = WEAPON_CLASS_THRESHOLDS[mode]
                updates = {
                    class_name: next_values[class_name]
                    for class_name, old_value in previous_values.items()
                    if round(getattr(profile, class_name), 4) == round(old_value, 4)
                }
                threshold_profiles[mode] = (
                    profile.model_copy(update=updates) if updates else profile
                )
            if any(
                getattr(current.weapon.class_thresholds, mode) != threshold_profiles[mode]
                for mode in threshold_profiles
            ):
                self.save_settings(
                    current.model_copy(
                        update={
                            "weapon": current.weapon.model_copy(
                                update={
                                    "class_thresholds": (
                                        current.weapon.class_thresholds.model_copy(
                                            update=threshold_profiles
                                        )
                                    ),
                                }
                            ),
                            "config_version": current.config_version + 1,
                            "updated_by": f"migration-{WEAPON_MODEL_VERSION}",
                            "updated_at": utc_now(),
                        }
                    )
                )
            current = self.get_settings()
            # Existing installations may already have the Brev checkpoint's
            # defaults. Upgrade only values that still match those exact
            # defaults, preserving any per-class operator customization.
            threshold_profiles = {}
            for mode, previous_values in PREVIOUS_BREV_WEAPON_CLASS_THRESHOLDS.items():
                profile = getattr(current.weapon.class_thresholds, mode)
                next_values = WEAPON_CLASS_THRESHOLDS[mode]
                updates = {
                    class_name: next_values[class_name]
                    for class_name, old_value in previous_values.items()
                    if round(getattr(profile, class_name), 4) == round(old_value, 4)
                }
                threshold_profiles[mode] = (
                    profile.model_copy(update=updates) if updates else profile
                )
            if any(
                getattr(current.weapon.class_thresholds, mode) != threshold_profiles[mode]
                for mode in threshold_profiles
            ):
                self.save_settings(
                    current.model_copy(
                        update={
                            "weapon": current.weapon.model_copy(
                                update={
                                    "class_thresholds": (
                                        current.weapon.class_thresholds.model_copy(
                                            update=threshold_profiles
                                        )
                                    ),
                                }
                            ),
                            "config_version": current.config_version + 1,
                            "updated_by": f"migration-{WEAPON_MODEL_VERSION}",
                            "updated_at": utc_now(),
                        }
                    )
                )
            current = self.get_settings()
            # Migrate the last deployed Mall-negative profile to the current
            # BREV hard-negative best checkpoint, preserving operator edits.
            threshold_profiles = {}
            for mode, previous_values in PREVIOUS_MALL_NEGATIVE_WEAPON_CLASS_THRESHOLDS.items():
                profile = getattr(current.weapon.class_thresholds, mode)
                next_values = WEAPON_CLASS_THRESHOLDS[mode]
                updates = {
                    class_name: next_values[class_name]
                    for class_name, old_value in previous_values.items()
                    if round(getattr(profile, class_name), 4) == round(old_value, 4)
                }
                threshold_profiles[mode] = (
                    profile.model_copy(update=updates) if updates else profile
                )
            if any(
                getattr(current.weapon.class_thresholds, mode) != threshold_profiles[mode]
                for mode in threshold_profiles
            ):
                self.save_settings(
                    current.model_copy(
                        update={
                            "weapon": current.weapon.model_copy(
                                update={
                                    "class_thresholds": current.weapon.class_thresholds.model_copy(
                                        update=threshold_profiles
                                    ),
                                }
                            ),
                            "config_version": current.config_version + 1,
                            "updated_by": f"migration-{WEAPON_MODEL_VERSION}",
                            "updated_at": utc_now(),
                        }
                    )
                )
            current = self.get_settings()
            if current.vlm.model_id != MODEL_ID:
                self.save_settings(
                    current.model_copy(
                        update={
                            "vlm": current.vlm.model_copy(
                                update={
                                    "model_id": MODEL_ID,
                                    "quantization": "Q4_K_M (Ollama)",
                                    "input_fps": 2.0,
                                    "max_output_tokens": 1024,
                                    "binary_prompt_version": BINARY_PROMPT_VERSION,
                                    "scene_prompt_version": SCENE_PROMPT_VERSION,
                                }
                            ),
                            "config_version": current.config_version + 1,
                            "updated_by": "migration-minicpm-v47-v1",
                            "updated_at": utc_now(),
                        }
                    )
                )
            current = self.get_settings()
            if current.vlm.scene_prompt_version in {'minicpm_scene_short_points_v8', 'minicpm_alert_context_v9'}:
                self.save_settings(current.model_copy(update={
                    'vlm':current.vlm.model_copy(update={'scene_prompt_version':SCENE_PROMPT_VERSION, 'max_output_tokens':1024}),
                    'config_version':current.config_version+1,
                    'updated_by':'migration-minicpm-scene-v10','updated_at':utc_now(),
                }))
                current=self.get_settings()
            # Upgrade exact defaults from the previously deployed BREV
            # hard-negative Epoch 5 model; retain per-class operator edits.
            threshold_profiles = {}
            for mode, previous_values in PREVIOUS_HARD_NEGATIVE_WEAPON_CLASS_THRESHOLDS.items():
                profile = getattr(current.weapon.class_thresholds, mode)
                next_values = WEAPON_CLASS_THRESHOLDS[mode]
                updates = {
                    class_name: next_values[class_name]
                    for class_name, old_value in previous_values.items()
                    if round(getattr(profile, class_name), 4) == round(old_value, 4)
                }
                threshold_profiles[mode] = (
                    profile.model_copy(update=updates) if updates else profile
                )
            if any(
                getattr(current.weapon.class_thresholds, mode) != threshold_profiles[mode]
                for mode in threshold_profiles
            ):
                self.save_settings(
                    current.model_copy(
                        update={
                            "weapon": current.weapon.model_copy(
                                update={
                                    "class_thresholds": current.weapon.class_thresholds.model_copy(
                                        update=threshold_profiles
                                    ),
                                }
                            ),
                            "config_version": current.config_version + 1,
                            "updated_by": f"migration-{WEAPON_MODEL_VERSION}",
                            "updated_at": utc_now(),
                        }
                    )
                )
            current = self.get_settings()
            # Move untouched nine-source Epoch 6 defaults to the ten-source
            # Epoch 10 validation-calibrated thresholds; preserve operator edits.
            threshold_profiles = {}
            for mode, previous_values in PREVIOUS_SELECTED_NINE_SOURCE_E06_WEAPON_CLASS_THRESHOLDS.items():
                profile = getattr(current.weapon.class_thresholds, mode)
                next_values = WEAPON_CLASS_THRESHOLDS[mode]
                updates = {
                    class_name: next_values[class_name]
                    for class_name, old_value in previous_values.items()
                    if round(getattr(profile, class_name), 4) == round(old_value, 4)
                }
                threshold_profiles[mode] = (
                    profile.model_copy(update=updates) if updates else profile
                )
            if any(
                getattr(current.weapon.class_thresholds, mode) != threshold_profiles[mode]
                for mode in threshold_profiles
            ):
                self.save_settings(
                    current.model_copy(
                        update={
                            "weapon": current.weapon.model_copy(
                                update={
                                    "class_thresholds": current.weapon.class_thresholds.model_copy(
                                        update=threshold_profiles
                                    ),
                                }
                            ),
                            "config_version": current.config_version + 1,
                            "updated_by": f"migration-{WEAPON_MODEL_VERSION}",
                            "updated_at": utc_now(),
                        }
                    )
                )

        current = self.get_settings()
        with self._connect() as connection:
            threshold_row = connection.execute(
                read_sql("""
                SELECT wc.class_thresholds_json
                FROM weapon_policy wc
                JOIN config_versions cv ON cv.id=wc.config_version_id
                ORDER BY cv.version_number DESC LIMIT 1
                """)
            ).fetchone()
        if threshold_row:
            stored_thresholds = json.loads(threshold_row["class_thresholds_json"])
            normalised_thresholds = WeaponClassThresholds.model_validate(
                stored_thresholds
            ).model_dump(mode="json")
            if stored_thresholds != normalised_thresholds:
                self.save_settings(
                    current.model_copy(
                        update={
                            "config_version": current.config_version + 1,
                            "updated_by": "migration-weapon-threshold-4dp",
                            "updated_at": utc_now(),
                        }
                    )
                )

    @property
    def database_path(self) -> Path:
        return self._database_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute(read_sql("PRAGMA foreign_keys = ON"))
        connection.execute(read_sql("PRAGMA busy_timeout = 5000"))
        connection.execute(read_sql("PRAGMA journal_mode = WAL"))
        try:
            yield connection
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._lock, self._connect() as connection:
            initialize(connection)

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
        return connection.execute(
            read_sql("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?"), (name,)
        ).fetchone() is not None

    def schema_version(self) -> int:
        with self._connect() as connection:
            row = connection.execute(read_sql("SELECT MAX(version) AS version FROM schema_migrations")).fetchone()
            return int(row["version"] or 0)

    def _camera_query(self, where: str = "", parameters: tuple = ()) -> list[Camera]:
        sql = """
            SELECT c.id, c.name, l.name AS location, c.enabled, c.status,
                   cs.source_type, cs.duration_seconds,
                   mf.relative_path AS storage_path, mf.original_filename,
                   mf.content_type, mf.size_bytes, c.created_at, c.updated_at
            FROM cameras c
            JOIN locations l ON l.id = c.location_id
            JOIN camera_sources cs ON cs.camera_id = c.id AND cs.is_active = 1
            LEFT JOIN media_files mf ON mf.id = cs.media_file_id
        """ + where + " ORDER BY c.created_at"
        with self._connect() as connection:
            rows = connection.execute(read_sql(sql), parameters).fetchall()
        return [
            Camera.model_validate(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "location": row["location"],
                    "enabled": bool(row["enabled"]),
                    "status": row["status"],
                    "source_type": row["source_type"],
                    "storage_path": row["storage_path"] or "",
                    "original_filename": row["original_filename"] or "RTSP source",
                    "content_type": row["content_type"] or "application/x-rtsp",
                    "size_bytes": row["size_bytes"] or 0,
                    "duration_seconds": row["duration_seconds"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
            for row in rows
        ]

    def list_cameras(self) -> list[Camera]:
        return self._camera_query()

    def get_camera(self, camera_id: str) -> Camera | None:
        rows = self._camera_query(" WHERE c.id = ?", (camera_id,))
        return rows[0] if rows else None

    def save_camera(self, camera: Camera) -> Camera:
        with self._lock, self._connect() as connection:
            location_key = _normalise_location(camera.location)
            old_camera = connection.execute(
                read_sql("SELECT location_id FROM cameras WHERE id = ?"), (camera.id,)
            ).fetchone()
            location = connection.execute(
                read_sql("SELECT id FROM locations WHERE normalized_name = ?"), (location_key,)
            ).fetchone()
            location_id = location["id"] if location else new_id("loc")
            if not location:
                connection.execute(
                    read_sql("INSERT INTO locations(id,name,normalized_name,created_at) VALUES (?,?,?,?)"),
                    (location_id, camera.location, location_key, _iso()),
                )

            connection.execute(
                read_sql("""
                INSERT INTO cameras(id,location_id,name,enabled,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    location_id=excluded.location_id,
                    name=excluded.name,
                    enabled=excluded.enabled,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """),
                (
                    camera.id,
                    location_id,
                    camera.name,
                    int(camera.enabled),
                    str(camera.status),
                    _iso(camera.created_at),
                    _iso(camera.updated_at),
                ),
            )

            active = connection.execute(
                read_sql("""
                SELECT cs.id, cs.media_file_id, mf.relative_path
                FROM camera_sources cs
                LEFT JOIN media_files mf ON mf.id = cs.media_file_id
                WHERE cs.camera_id = ? AND cs.is_active = 1
                """),
                (camera.id,),
            ).fetchone()
            if not active or active["relative_path"] != camera.storage_path:
                if active:
                    connection.execute(
                        read_sql("UPDATE camera_sources SET is_active = 0 WHERE id = ?"), (active["id"],)
                    )
                media_id = new_id("media")
                connection.execute(
                    read_sql("""
                    INSERT INTO media_files(
                        id,relative_path,original_filename,media_kind,content_type,size_bytes,created_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """),
                    (
                        media_id,
                        camera.storage_path,
                        camera.original_filename,
                        "camera_source",
                        camera.content_type,
                        camera.size_bytes,
                        _iso(),
                    ),
                )
                connection.execute(
                    read_sql("""
                    INSERT INTO camera_sources(
                        id,camera_id,media_file_id,source_type,duration_seconds,is_active,created_at
                    ) VALUES (?,?,?,?,?,1,?)
                    """),
                    (
                        new_id("source"),
                        camera.id,
                        media_id,
                        camera.source_type,
                        camera.duration_seconds,
                        _iso(),
                    ),
                )
            if old_camera and old_camera["location_id"] != location_id:
                connection.execute(
                    read_sql("DELETE FROM locations WHERE id=? AND NOT EXISTS (SELECT 1 FROM cameras WHERE location_id=?)"),
                    (old_camera["location_id"], old_camera["location_id"]),
                )
            connection.commit()
        return camera

    def delete_camera(self, camera_id: str) -> Camera | None:
        camera = self.get_camera(camera_id)
        if not camera:
            return None
        with self._lock, self._connect() as connection:
            if connection.execute(
                read_sql('SELECT 1 FROM incident_state WHERE camera_id=? LIMIT 1'), (camera_id,)
            ).fetchone():
                raise ValueError(
                    "Camera has alert history and cannot be deleted; disable or archive it instead."
                )
            rows = connection.execute(
                read_sql("SELECT media_file_id FROM camera_sources WHERE camera_id = ?"), (camera_id,)
            ).fetchall()
            location_row = connection.execute(
                read_sql("SELECT location_id FROM cameras WHERE id = ?"), (camera_id,)
            ).fetchone()
            connection.execute(read_sql("DELETE FROM cameras WHERE id = ?"), (camera_id,))
            for row in rows:
                if row["media_file_id"]:
                    connection.execute(read_sql("DELETE FROM media_files WHERE id = ?"), (row["media_file_id"],))
            if location_row:
                connection.execute(
                    read_sql("DELETE FROM locations WHERE id = ? AND NOT EXISTS (SELECT 1 FROM cameras WHERE location_id = ?)"),
                    (location_row["location_id"], location_row["location_id"]),
                )
            connection.commit()
        return camera

    def remove_inactive_camera_sources(self, camera_id: str) -> list[str]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT cs.id,cs.media_file_id,mf.relative_path
                FROM camera_sources cs
                JOIN media_files mf ON mf.id=cs.media_file_id
                WHERE cs.camera_id=? AND cs.is_active=0
                """),
                (camera_id,),
            ).fetchall()
            for row in rows:
                connection.execute(read_sql("DELETE FROM camera_sources WHERE id=?"), (row["id"],))
                connection.execute(read_sql("DELETE FROM media_files WHERE id=?"), (row["media_file_id"],))
            connection.commit()
        return [row["relative_path"] for row in rows]

    def get_settings(self) -> SystemSettings:
        with self._connect() as connection:
            row = connection.execute(
                read_sql("""
                SELECT cv.id,cv.version_number,cv.default_sensitivity,cv.created_by,cv.created_at,
                       cv.detection_debug_mode_enabled,
                       vc.segment_seconds,vc.stride_seconds,vc.detector_input_frames,
                       vc.sensitive_threshold,vc.moderate_threshold,vc.certain_threshold,
                       vc.incident_merge_gap_seconds AS violence_merge_gap_seconds,
                       vc.cooldown_seconds AS violence_cooldown_seconds,
                       wc.sample_fps,wc.confidence_threshold,wc.nms_candidate_floor,
                       wc.nms_iou_threshold,wc.class_thresholds_json,wc.n_required,wc.m_window,
                       wc.incident_merge_gap_seconds AS weapon_merge_gap_seconds,
                       wc.cooldown_seconds AS weapon_cooldown_seconds,
                       q.model_id,q.quantization,q.input_fps,q.video_token_budget,q.max_output_tokens,
                       q.scene_description_frames,
                       q.borderline_review_enabled,q.scene_description_enabled,
                       q.binary_prompt_version,q.scene_prompt_version,
                       ae.same_audience_repeat_after_seconds,
                       ae.all_users_escalation_after_seconds
                FROM config_versions cv
                JOIN violence_policy vc ON vc.config_version_id = cv.id
                JOIN weapon_policy wc ON wc.config_version_id = cv.id
                JOIN vlm_policy q ON q.config_version_id = cv.id
                JOIN reminder_policy ae ON ae.config_version_id = cv.id
                ORDER BY cv.version_number DESC LIMIT 1
                """)
            ).fetchone()
        if not row:
            raise RuntimeError("No system configuration is available")
        return SystemSettings.model_validate(
            {
                "id": "global",
                "default_sensitivity": row["default_sensitivity"],
                "detection_debug_mode_enabled": bool(row["detection_debug_mode_enabled"]),
                "config_version": row["version_number"],
                "updated_by": row["created_by"],
                "updated_at": row["created_at"],
                "violence": {
                    "segment_seconds": row["segment_seconds"],
                    "stride_seconds": row["stride_seconds"],
                    "detector_input_frames": row["detector_input_frames"],
                    "sensitive_threshold": row["sensitive_threshold"],
                    "moderate_threshold": row["moderate_threshold"],
                    "certain_threshold": row["certain_threshold"],
                    "incident_merge_gap_seconds": row["violence_merge_gap_seconds"],
                    "cooldown_seconds": row["violence_cooldown_seconds"],
                },
                "weapon": {
                    "sample_fps": row["sample_fps"],
                    "confidence_threshold": row["confidence_threshold"],
                    "nms_candidate_floor": row["nms_candidate_floor"],
                    "nms_iou_threshold": row["nms_iou_threshold"],
                    "class_thresholds": json.loads(row["class_thresholds_json"]),
                    "n_required": row["n_required"],
                    "m_window": row["m_window"],
                    "incident_merge_gap_seconds": row["weapon_merge_gap_seconds"],
                    "cooldown_seconds": row["weapon_cooldown_seconds"],
                },
                "vlm": {
                    "model_id": row["model_id"],
                    "quantization": row["quantization"],
                    "input_fps": row["input_fps"],
                    "video_token_budget": row["video_token_budget"],
                    "max_output_tokens": row["max_output_tokens"],
                    "scene_description_frames": row["scene_description_frames"],
                    "borderline_review_enabled": bool(row["borderline_review_enabled"]),
                    "scene_description_enabled": bool(row["scene_description_enabled"]),
                    "binary_prompt_version": row["binary_prompt_version"],
                    "scene_prompt_version": row["scene_prompt_version"],
                },
                "alert_notification_escalation": {
                    "same_audience_repeat_after_seconds": row[
                        "same_audience_repeat_after_seconds"
                    ],
                    "all_users_escalation_after_seconds": row[
                        "all_users_escalation_after_seconds"
                    ],
                },
            }
        )

    def save_settings(self, settings: SystemSettings, *, expected_config_version: int | None = None) -> SystemSettings:
        settings = SystemSettings.model_validate(settings.model_dump(mode="python"))
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            if expected_config_version is not None:
                current_version = int(connection.execute(
                    "SELECT COALESCE(MAX(version_number), 0) FROM config_versions"
                ).fetchone()[0])
                if current_version != expected_config_version:
                    raise ConfigurationConflictError(current_version)
                settings = settings.model_copy(update={"config_version": current_version + 1})
            v, w, q = settings.violence, settings.weapon, settings.vlm
            values = dict(id=f"cfg_{settings.config_version:06d}",version_number=settings.config_version,
                default_sensitivity=str(settings.default_sensitivity),created_by=settings.updated_by,
                created_at=_iso(settings.updated_at),detection_debug_mode_enabled=int(settings.detection_debug_mode_enabled),validation_profile='runtime')
            for prefix, value in [('violence_',v),('weapon_',w),('vlm_',q)]:
                for key,item in value.model_dump(mode='json').items():
                    if key in ('binary_prompt_version','scene_prompt_version'):
                        values[key+'_id']=self._ensure_prompt(connection,'borderline_binary' if key.startswith('binary') else 'scene_description',item)
                    elif key=='class_thresholds':values['weapon_class_thresholds_json']=json.dumps(item,separators=(',',':'),allow_nan=False)
                    else:values[prefix+('merge_gap_seconds' if key=='incident_merge_gap_seconds' else key)]=int(item) if isinstance(item,bool) else item
            values.update(settings.alert_notification_escalation.model_dump(mode='json'))
            insert(connection,'config_versions',values)
            if not q.borderline_review_enabled:
                connection.execute(read_sql("UPDATE borderline_reviews SET supplemental_notification_status='suppressed_disabled' WHERE record_origin='task' AND status='positive' AND supplemental_notification_status IN ('eligible','handled')"))
            connection.commit()
        return settings

    def _ensure_model(self, connection: sqlite3.Connection, task: str, version: str) -> str:
        row = connection.execute(
            read_sql("SELECT id FROM model_versions WHERE task=? AND model_name=? AND version=?"),
            (task, version, version),
        ).fetchone()
        if row:
            return row["id"]
        model_id = new_id("model")
        connection.execute(
            read_sql("INSERT INTO model_versions(id,task,model_name,version,created_at) VALUES (?,?,?,?,?)"),
            (model_id, task, version, version, _iso()),
        )
        return model_id

    def _ensure_prompt(self, connection: sqlite3.Connection, task: str, version: str | None) -> str | None:
        if not version:
            return None
        row = connection.execute(read_sql("SELECT id FROM prompt_versions WHERE version=?"), (version,)).fetchone()
        if row:
            return row["id"]
        prompt_id = new_id("prompt")
        connection.execute(
            read_sql("INSERT INTO prompt_versions(id,task,version,created_at,capture_state) VALUES (?,?,?,?,'metadata_only')"),
            (prompt_id, task, version, _iso()),
        )
        return prompt_id

    def save_alert(
        self,
        alert: Alert,
        detection_event: DetectionEventSnapshot | None = None,
    ) -> Alert:
        with self._lock, self._connect() as connection:
            existing = connection.execute(read_sql('SELECT id FROM incident_state WHERE id=?'), (alert.id,)).fetchone()
            model_id = self._ensure_model(connection, str(alert.type), alert.model_version)
            prompt_id = self._ensure_prompt(connection, "borderline_binary", alert.prompt_version)
            config_row = connection.execute(
                read_sql("SELECT id FROM config_versions WHERE version_number=?"), (alert.config_version,)
            ).fetchone()
            if not config_row:
                raise ValueError(f"Unknown config version {alert.config_version}")
            config_id = config_row["id"]

            if not existing:
                connection.execute(
                    read_sql("""
                    INSERT INTO alerts(
                        id,camera_id,incident_type,workflow_status,sensitivity_mode,is_borderline_promoted,
                        workflow_updated_at,borderline_review_status,evidence_status,
                        opening_config_version_id,opening_model_version_id,
                        binary_prompt_version_id,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """),
                    (
                        alert.id,
                        alert.camera_id,
                        str(alert.type),
                        {'open':'awaiting_confirmation','in_review':'legacy_in_review','escalated':'legacy_escalated','resolved':'resolved','dismissed':'dismissed'}[str(alert.status)],
                        str(alert.sensitivity_mode),
                        int(alert.is_borderline_promoted),
                        _iso(alert.first_detected_at),
                        alert.borderline_review_status,
                        alert.evidence_status,
                        config_id,
                        model_id,
                        prompt_id,
                        _iso(alert.first_detected_at),
                    ),
                )
                vlm_model_id = self._ensure_model(
                    connection, "scene_description", self.get_settings().vlm.model_id
                )
                scene_prompt_id = self._ensure_prompt(
                    connection, "scene_description", self.get_settings().vlm.scene_prompt_version
                )
                scene_value = (
                    alert.scene_summary
                    if alert.scene_summary is not None
                    else alert.scene_description
                )
                scene_display, scene_structured = _scene_storage_values(scene_value)
                connection.execute(
                    read_sql("""
                    INSERT INTO vlm_descriptions(
                        id,alert_id,model_version_id,prompt_version_id,status,structured_json,display_text,created_at,config_version_id,output_format
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """),
                    (
                        new_id("vlmdesc"),
                        alert.id,
                        vlm_model_id,
                        scene_prompt_id,
                        str(alert.description_status),
                        scene_structured,
                        scene_display,
                        _iso(),config_id,_output_format(scene_display,scene_structured),
                    ),
                )
                current_count = 0
            else:
                connection.execute(
                    read_sql("UPDATE alerts SET is_borderline_promoted=MAX(is_borderline_promoted,?), "
                    "borderline_review_status=CASE WHEN is_borderline_promoted=1 "
                    "THEN 'positive' ELSE ? END WHERE id=?"),
                    (
                        int(alert.is_borderline_promoted), alert.borderline_review_status, alert.id,
                    ),
                )
                current_count = connection.execute(
                    read_sql("SELECT COUNT(*) AS count FROM detection_events WHERE alert_id=?"), (alert.id,)
                ).fetchone()["count"]

            decision_source = (
                "weapon_n_of_m"
                if str(alert.type) == "weapon"
                else "vlm_promoted"
                if alert.is_borderline_promoted and current_count == 0
                else "threshold"
            )
            events_to_add = max(0, alert.detection_count - current_count)
            if detection_event is not None:
                event_model_id = self._ensure_model(
                    connection, str(alert.type), detection_event.model_version
                )
                event_config_row = connection.execute(
                    read_sql("SELECT id FROM config_versions WHERE version_number=?"),
                    (detection_event.config_version,),
                ).fetchone()
                if not event_config_row:
                    raise ValueError(
                        f"Unknown detection config version {detection_event.config_version}"
                    )
                event_config_id = event_config_row["id"]
            else:
                event_model_id = model_id
                event_config_id = config_id
            for event_index in range(events_to_add):
                # Review tasks use the latest detection event as a durable
                # foreign-key anchor, including when the alert is merged.
                event_id = (
                    alert.detection_event_key
                    if event_index == events_to_add - 1
                    else new_id("detect")
                )
                connection.execute(
                    read_sql("""
                    INSERT INTO detection_events(
                        id,alert_id,model_version_id,config_version_id,confidence,decision_source,
                        source_start_seconds,source_end_seconds,detected_at,
                        qualifications_json,weapon_class_scores_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """),
                    (
                        event_id,
                        alert.id,
                        event_model_id,
                        event_config_id,
                        detection_event.confidence if detection_event else alert.max_confidence,
                        decision_source,
                        detection_event.source_start_seconds if detection_event else alert.source_start_seconds,
                        detection_event.source_end_seconds if detection_event else alert.source_end_seconds,
                        _iso(detection_event.detected_at if detection_event else alert.last_detected_at),
                        json.dumps(
                            detection_event.qualifications if detection_event else alert.detection_qualifications,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            detection_event.weapon_class_scores if detection_event else alert.weapon_class_scores,
                            separators=(",", ":"),
                        ),
                    ),
                )
            connection.commit()
        return alert

    def get_mobile_alert_workflow(self, alert_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                read_sql("""
                SELECT w.alert_id,w.workflow_status,w.handler_user_id,
                       COALESCE(NULLIF(trim(u.display_name),''),'') AS handler_name,
                       w.active_phase_id,w.broadcast_role_ids_json,w.revision,w.updated_at
                FROM workflow_state w
                LEFT JOIN users u ON u.id=w.handler_user_id
                WHERE w.alert_id=?
                """),
                (alert_id,),
            ).fetchone()
            if row is None:
                return None
            events = connection.execute(
                read_sql("""
                SELECT e.id,e.event_type AS action,e.comment AS note,e.created_at,
                       e.broadcast_role_ids_json,
                       COALESCE(NULLIF(trim(u.display_name),''),'An authorised user') AS performed_by,
                       u.email_cache AS performed_by_email
                FROM workflow_history e
                JOIN users u ON u.id=e.actor_user_id
                WHERE e.alert_id=?
                ORDER BY e.created_at DESC,e.rowid DESC
                """),
                (alert_id,),
            ).fetchall()
        value = dict(row)
        value["broadcast_role_ids"] = json.loads(value.pop("broadcast_role_ids_json") or "[]")
        value["events"] = []
        for event in events:
            item = dict(event)
            item["broadcast_role_ids"] = json.loads(
                item.pop("broadcast_role_ids_json") or "[]"
            )
            value["events"].append(item)
        return value

    @staticmethod
    def _active_user_ids_for_roles(
        connection: sqlite3.Connection,
        role_ids: list[str],
        *,
        require_enabled_device: bool = False,
        exclude_user_id: str | None = None,
    ) -> list[str]:
        unique_role_ids = list(dict.fromkeys(role_id for role_id in role_ids if role_id))
        if not unique_role_ids:
            return []
        device_join = "JOIN devices d ON d.user_id=u.id AND d.enabled=1" if require_enabled_device else ""
        placeholders = ",".join("?" for _ in unique_role_ids)
        parameters: list[str] = unique_role_ids
        sql = f"""
            SELECT DISTINCT u.id
            FROM users u
            JOIN user_roles ur ON ur.user_id=u.id
            {device_join}
            WHERE u.status='active' AND ur.role_id IN ({placeholders})
        """
        if exclude_user_id:
            sql += " AND u.id<>?"
            parameters.append(exclude_user_id)
        sql += " ORDER BY u.id"
        return [row["id"] for row in connection.execute(read_sql(sql), parameters).fetchall()]

    @staticmethod
    def _queue_mobile_notification(connection, *, alert_id, title, body, action, source,
        channel_id, recipients, event_key, extra_data=None, created_at=None):
        user_ids=list(dict.fromkeys(user_id for user_id in recipients if user_id))
        if not user_ids:return None
        existing=connection.execute(read_sql("SELECT o.id FROM notification_outbox o JOIN notifications n ON n.id=o.notification_id WHERE n.event_key=?"),(event_key,)).fetchone()
        if existing:return existing['id']
        outbox_id,notification_id=new_id('dispatch'),new_id('notification')
        now=utc(created_at) if created_at else _iso()
        data=dict(alertId=alert_id,action=action,source=source,channelId=channel_id,
            dispatchId=outbox_id,notificationEventId=event_key,
            notificationStage='initial_broadcast' if source=='alert_broadcast' else 'decision_update' if source=='alert_decision' else '')
        data.update(extra_data or {})
        kind={'alert_broadcast':'broadcast','alert_decision':'decision_update','alert_created':'initial'}.get(source,'legacy')
        phase_id,stage=None,None
        if source=='alert_reminder':
            phase_id=data['reminderPhaseId'];stage=int(data['reminderStage'])
            kind='late_repeat' if data.get('lateAudience')=='true' else 'phase_reminder'
        insert(connection,'notifications',dict(id=notification_id,alert_id=alert_id,title=title,body=body,
            data_payload_json=json.dumps(data,separators=(',',':'),allow_nan=False),created_at=now,
            event_key=event_key,message_kind=kind,phase_id=phase_id,reminder_stage=stage,visibility_policy='restricted'))
        insert(connection,'notification_outbox',dict(id=outbox_id,notification_id=notification_id,channel_id=channel_id,
            status='pending',next_attempt_at=now,created_at=now,updated_at=now))
        connection.executemany(read_sql("INSERT INTO notification_recipients(notification_id,user_id,feed_visible,queued_target) VALUES (?,?,1,1)"),[(notification_id,u) for u in user_ids])
        return outbox_id

    def mobile_alert_broadcast_preview(
        self, alert_id: str, actor_user_id: str
    ) -> dict | None:
        with self._connect() as connection:
            alert = connection.execute(
                read_sql('SELECT sensitivity_mode FROM incident_state WHERE id=?'), (alert_id,)
            ).fetchone()
            if not alert:
                return None
            detections = connection.execute(
                read_sql("SELECT qualifications_json FROM detection_events WHERE alert_id=?"),
                (alert_id,),
            ).fetchall()
            roles = connection.execute(
                read_sql("""
                SELECT r.id,r.display_name,r.name,r.sensitivity_mode,
                       COUNT(DISTINCT CASE WHEN u.status='active' THEN u.id END) AS active_user_count
                FROM roles r
                LEFT JOIN user_roles ur ON ur.role_id=r.id
                LEFT JOIN users u ON u.id=ur.user_id
                GROUP BY r.id
                ORDER BY COALESCE(NULLIF(r.display_name,''),r.name)
                """)
            ).fetchall()
            qualified_modes: set[str] = set()
            for detection in detections:
                try:
                    qualifications = json.loads(
                        detection["qualifications_json"] or "{}"
                    )
                except (TypeError, json.JSONDecodeError):
                    continue
                qualified_modes.update(
                    str(mode) for mode in qualifications.get("violence", [])
                )
                weapon_qualifications = qualifications.get("weapon", {})
                if isinstance(weapon_qualifications, dict):
                    qualified_modes.update(
                        mode for mode, classes in weapon_qualifications.items() if classes
                    )
            values = []
            for role in roles:
                eligible = self._active_user_ids_for_roles(
                    connection,
                    [role["id"]],
                    require_enabled_device=True,
                    exclude_user_id=actor_user_id,
                )
                values.append(
                    {
                        "id": role["id"],
                        "name": role["display_name"] or role["name"],
                        "sensitivityMode": role["sensitivity_mode"],
                        "activeUserCount": len(
                            self._active_user_ids_for_roles(connection, [role["id"]])
                        ),
                        "eligibleDeviceUserCount": len(eligible),
                        "defaultSelected": role["sensitivity_mode"] in qualified_modes,
                    }
                )
        return {"roles": values, "sensitivityMode": alert["sensitivity_mode"]}

    @staticmethod
    def _start_mobile_alert_reminder_phase(
        connection: sqlite3.Connection,
        *,
        alert_id: str,
        phase_kind: str,
        started_at: datetime,
        first_after_seconds: int,
        all_after_seconds: int,
        initiated_by_user_id: str | None = None,
        broadcast_role_ids: list[str] | None = None,
        source_notification_id: str | None = None,
    ) -> str:
        if (
            phase_kind not in {"awaiting_decision", "awaiting_handler"}
            or first_after_seconds < 15
            or all_after_seconds <= first_after_seconds
        ):
            raise ValueError("Reminder times or workflow phase are invalid.")
        existing = connection.execute(
            read_sql("""
            SELECT id FROM reminder_timers
            WHERE alert_id=? AND phase_kind=? AND phase_status='active'
            ORDER BY started_at DESC LIMIT 1
            """),
            (alert_id, phase_kind),
        ).fetchone()
        if existing:
            return existing["id"]
        SQLiteRepository._cancel_mobile_alert_reminder_phases(
            connection,
            alert_id,
            reason="superseded",
            completed_at=_iso(started_at),
        )
        phase_id = new_id("phase")
        connection.execute(
            read_sql("""
            INSERT INTO notification_phases(
                id,alert_id,phase_kind,phase_status,started_at,first_after_seconds,
                all_after_seconds,first_due_at,all_due_at,broadcast_role_snapshot_json,
                initiated_by_user_id,source_notification_id
            ) VALUES (?,?,?,'active',?,?,?,?,?,?,?,?)
            """),
            (
                phase_id,
                alert_id,
                phase_kind,
                _iso(started_at),
                first_after_seconds,
                all_after_seconds,
                _iso(started_at + timedelta(seconds=first_after_seconds)),
                _iso(started_at + timedelta(seconds=all_after_seconds)),
                json.dumps(list(dict.fromkeys(broadcast_role_ids or [])), separators=(",", ":")),
                initiated_by_user_id,
                source_notification_id,
            ),
        )
        connection.executemany(
            "INSERT INTO notification_phase_roles(phase_id,role_id) VALUES (?,?)",
            [(phase_id,role_id) for role_id in dict.fromkeys(broadcast_role_ids or [])],
        )
        return phase_id

    @staticmethod
    def _cancel_mobile_alert_reminder_phases(
        connection: sqlite3.Connection,
        alert_id: str,
        *,
        reason: str,
        completed_at: str,
        phase_status: str = "cancelled",
    ) -> None:
        """Stop active phases and mark queued, not-yet-delivering reminders stale."""
        if phase_status not in {"cancelled", "completed"}:
            raise ValueError("Invalid reminder phase terminal status.")
        phase_ids = [
            row["id"]
            for row in connection.execute(
                read_sql("SELECT id FROM reminder_timers WHERE alert_id=? AND phase_status='active'"),
                (alert_id,),
            ).fetchall()
        ]
        if not phase_ids:
            return
        placeholders = ",".join("?" for _ in phase_ids)
        connection.execute(
            read_sql("UPDATE notification_phases "
            f"SET phase_status=?,cancel_reason=?,completed_at=? WHERE id IN ({placeholders})"),
            [phase_status, reason, completed_at, *phase_ids],
        )
        stopped_dispatch_status = (
            "skipped_responded" if phase_status == "completed" else "skipped_stopped"
        )
        connection.execute(
            read_sql("UPDATE notification_phase_dispatches "
            f"SET status=?,error_code='WorkflowChanged',completed_at=? "
            f"WHERE phase_id IN ({placeholders}) AND status='queued'"),
            [stopped_dispatch_status, completed_at, *phase_ids],
        )

    def start_mobile_alert_reminder_phase(
        self,
        alert_id: str,
        *,
        phase_kind: str,
        started_at: datetime,
        first_after_seconds: int,
        all_after_seconds: int,
        source_notification_id: str | None = None,
    ) -> str | None:
        with self._lock, self._connect() as connection:
            exists = connection.execute(
                read_sql('SELECT 1 FROM incident_state WHERE id=?'), (alert_id,)
            ).fetchone()
            if not exists:
                return None
            phase_id = self._start_mobile_alert_reminder_phase(
                connection,
                alert_id=alert_id,
                phase_kind=phase_kind,
                started_at=started_at,
                first_after_seconds=first_after_seconds,
                all_after_seconds=all_after_seconds,
                source_notification_id=source_notification_id,
            )
            connection.execute(
                read_sql('UPDATE alerts SET active_phase_id=?,revision=revision+1,workflow_updated_at=? WHERE id=?'),
                (phase_id, _iso(started_at), alert_id),
            )
            connection.commit()
        return phase_id

    def queue_initial_mobile_alert_notification(
        self,
        alert: Alert,
        *,
        recipients: list[dict],
        title: str,
        body: str,
        channel_id: str,
        first_after_seconds: int,
        all_after_seconds: int,
        started_at: datetime | None = None,
        borderline_review_task_id: str | None = None,
    ) -> dict:
        now = started_at or utc_now()
        proposed_user_ids = list(dict.fromkeys(
            item.get("user_id") for item in recipients if item.get("user_id")
        ))
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            if not connection.execute(
                read_sql('SELECT 1 FROM incident_state WHERE id=?'), (alert.id,)
            ).fetchone():
                connection.rollback()
                raise ValueError("Alert not found.")
            workflow = connection.execute(
                read_sql('SELECT workflow_status,active_phase_id,revision FROM workflow_state WHERE alert_id=?'),
                (alert.id,),
            ).fetchone()
            if not workflow:
                connection.rollback()
                raise ValueError("Alert workflow is not available.")
            if workflow["workflow_status"] not in {
                "awaiting_confirmation", "confirmed_awaiting_handler"
            }:
                if borderline_review_task_id:
                    connection.execute(
                        read_sql("UPDATE borderline_reviews SET supplemental_notification_status='handled' WHERE id=? AND alert_id=? AND status='positive' AND supplemental_notification_status='eligible'"),
                        (borderline_review_task_id, alert.id),
                    )
                connection.commit()
                return {"outboxId": None, "phaseId": None, "recipientCount": 0}
            audience_rows = connection.execute(
                read_sql("""
                SELECT a.user_id,a.first_sent_at,o.status AS outbox_status
                FROM alert_notification_audience a
                LEFT JOIN queue_messages o
                  ON o.notification_id=a.first_notification_id
                WHERE a.alert_id=?
                """),
                (alert.id,),
            ).fetchall()
            existing_audience = {row["user_id"]: row for row in audience_rows}
            user_ids = []
            for user_id in proposed_user_ids:
                prior = existing_audience.get(user_id)
                if prior is None or prior["first_sent_at"] is None and prior["outbox_status"] not in {
                    "pending", "dispatching"
                }:
                    user_ids.append(user_id)
            outbox_id = None
            notification_id = None
            if user_ids:
                cohort = hashlib.sha256(
                    "\n".join(sorted(user_ids)).encode("utf-8")
                ).hexdigest()[:16]
                attempt = connection.execute(
                    read_sql("""
                    SELECT COUNT(*) AS count FROM queue_messages o
                    JOIN notifications n ON n.id=o.notification_id
                    WHERE n.alert_id=?
                      AND json_extract(o.data_payload_json,'$.source')='alert_created'
                    """),
                    (alert.id,),
                ).fetchone()["count"] + 1
                outbox_id = self._queue_mobile_notification(
                    connection,
                    alert_id=alert.id,
                    title=title,
                    body=body,
                    action="new_alert",
                    source="alert_created",
                    channel_id=channel_id,
                    recipients=user_ids,
                    event_key=f"alert-initial:{alert.id}:{cohort}:{attempt}",
                    extra_data={
                        "type": str(alert.type),
                        "notificationStage": "initial_alert",
                        "alertDetectedAt": alert.first_detected_at.isoformat(),
                    },
                )
                if outbox_id:
                    notification = connection.execute(
                        read_sql('SELECT notification_id FROM queue_messages WHERE id=?'),
                        (outbox_id,),
                    ).fetchone()
                    notification_id = notification["notification_id"] if notification else None
                    if notification_id:
                        proposed_by_user: dict[str, set[str]] = {}
                        for item in recipients:
                            user_id = item.get("user_id")
                            if user_id:
                                proposed_by_user.setdefault(user_id, set()).update(
                                    item.get("recipient_reasons", ["legacy"])
                                )
                        for user_id in user_ids:
                            prior = existing_audience.get(user_id)
                            prior_reasons = set()
                            if prior:
                                prior_row = connection.execute(
                                    read_sql("SELECT qualification_reasons_json FROM alert_notification_audience "
                                    "WHERE alert_id=? AND user_id=?"),
                                    (alert.id, user_id),
                                ).fetchone()
                                try:
                                    prior_reasons = set(
                                        json.loads(prior_row["qualification_reasons_json"] or "[]")
                                    ) if prior_row else set()
                                except (TypeError, json.JSONDecodeError):
                                    prior_reasons = set()
                            reasons = prior_reasons | proposed_by_user.get(user_id, {"legacy"})
                            connection.execute(
                                read_sql("""
                                INSERT INTO alert_notification_audience(
                                    alert_id,user_id,qualification_reasons_json,
                                    first_notification_id,first_queued_at
                                ) VALUES (?,?,?,?,?)
                                ON CONFLICT(alert_id,user_id) DO UPDATE SET
                                    qualification_reasons_json=excluded.qualification_reasons_json,
                                    first_notification_id=excluded.first_notification_id,
                                    first_queued_at=excluded.first_queued_at
                                """),
                                (
                                    alert.id,user_id,json.dumps(sorted(reasons)),
                                    notification_id,_iso(now),
                                ),
                            )
            if workflow["workflow_status"] == "awaiting_confirmation":
                phase_id = self._start_mobile_alert_reminder_phase(
                    connection,
                    alert_id=alert.id,
                    phase_kind="awaiting_decision",
                    started_at=now,
                    first_after_seconds=first_after_seconds,
                    all_after_seconds=all_after_seconds,
                    source_notification_id=notification_id,
                )
                connection.execute(
                    read_sql('UPDATE alerts SET active_phase_id=?,revision=revision+1,workflow_updated_at=? WHERE id=?'),
                    (phase_id, _iso(now), alert.id),
                )
            else:
                phase_id = workflow["active_phase_id"]
            if outbox_id:
                current_workflow = connection.execute(
                    read_sql('SELECT workflow_status,revision FROM workflow_state WHERE alert_id=?'),
                    (alert.id,),
                ).fetchone()
                dispatch = connection.execute(
                    read_sql('SELECT notification_id,data_payload_json FROM queue_messages WHERE id=?'),
                    (outbox_id,),
                ).fetchone()
                if current_workflow and dispatch:
                    try:
                        payload = json.loads(dispatch["data_payload_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        payload = {}
                    payload["workflowStatus"] = current_workflow["workflow_status"]
                    payload["workflowRevision"] = int(current_workflow["revision"])
                    payload_json = json.dumps(payload, separators=(",", ":"))
                    connection.execute(
                        read_sql("UPDATE notifications SET data_payload_json=? WHERE id=?"),
                        (payload_json, dispatch["notification_id"]),
                    )
            if borderline_review_task_id:
                connection.execute(
                    read_sql("UPDATE borderline_reviews SET supplemental_notification_status='handled' WHERE id=? AND alert_id=? AND status='positive' AND supplemental_notification_status='eligible'"),
                    (borderline_review_task_id, alert.id),
                )
            connection.commit()
        return {
            "outboxId": outbox_id,
            "phaseId": phase_id,
            "recipientCount": len(user_ids),
        }

    def record_mobile_workflow_action(
        self,
        alert_id: str,
        actor_user_id: str,
        action: str,
        *,
        comment: str | None,
        recipient_role_ids: list[str],
        allow_empty_audience: bool,
        idempotency_key: str,
        expected_workflow_revision: int | None = None,
        first_after_seconds: int,
        all_after_seconds: int,
    ) -> dict | None:
        allowed_actions = {
            "dismiss_false_alarm", "confirm_only", "confirm_and_handle",
            "confirm_resolved", "take_ownership", "resolve",
            "simulate_emergency_call",
        }
        if action not in allowed_actions:
            raise ValueError("Unsupported alert workflow action.")
        if not idempotency_key.strip():
            raise ValueError("An idempotency key is required.")
        clean_comment = (comment or "").strip() or None
        role_ids = list(dict.fromkeys(role_id.strip() for role_id in recipient_role_ids if role_id.strip()))
        if action == "confirm_only" and not role_ids:
            raise ValueError("Select at least one Broadcast Role.")
        if action != "confirm_only" and role_ids:
            raise ValueError("Broadcast Roles are only accepted for Confirm only.")
        now = utc_now()
        now_iso = _iso(now)
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            existing = connection.execute(
                read_sql("""
                SELECT actor_user_id,event_type,comment,broadcast_role_ids_json FROM workflow_history
                WHERE alert_id=? AND idempotency_key=?
                """),
                (alert_id, idempotency_key),
            ).fetchone()
            workflow = connection.execute(
                read_sql('SELECT * FROM workflow_state WHERE alert_id=?'), (alert_id,)
            ).fetchone()
            alert = connection.execute(
                read_sql("""
                SELECT a.status,a.incident_type,a.sensitivity_mode,c.name AS camera_name
                FROM incident_state a JOIN cameras c ON c.id=a.camera_id WHERE a.id=?
                """),
                (alert_id,),
            ).fetchone()
            if not alert:
                connection.rollback()
                return None
            if not workflow:
                connection.rollback()
                raise ValueError("Alert workflow is not available for this record.")
            if existing:
                expected_action = 'simulated_emergency_call' if action == 'simulate_emergency_call' else action
                if (
                    existing['actor_user_id'] != actor_user_id
                    or existing['event_type'] != expected_action
                    or existing['comment'] != clean_comment
                    or set(json.loads(existing['broadcast_role_ids_json'] or '[]')) != set(role_ids)
                ):
                    connection.rollback()
                    raise WorkflowConflictError(
                        'This request number was already used for a different response. Review the incident before trying again.',
                        'idempotency_payload_mismatch', workflow,
                    )
                connection.commit()
                return {
                    "idempotent": True,
                    "workflow": self.get_mobile_alert_workflow(alert_id),
                    "queuedOutboxIds": [],
                    "broadcastOutboxIds": [],
                    "decisionUpdateOutboxIds": [],
                    "broadcastEligibleUsers": 0,
                }

            if (
                expected_workflow_revision is not None
                and int(workflow["revision"]) != expected_workflow_revision
            ):
                connection.rollback()
                raise stale_workflow_error(workflow)

            current_state = workflow["workflow_status"]
            transitions = {
                "dismiss_false_alarm": {"awaiting_confirmation"},
                "confirm_only": {"awaiting_confirmation"},
                "confirm_and_handle": {"awaiting_confirmation"},
                "confirm_resolved": {"awaiting_confirmation"},
                "take_ownership": {"confirmed_awaiting_handler"},
                "resolve": {"in_progress"},
                "simulate_emergency_call": {
                    "awaiting_confirmation", "confirmed_awaiting_handler", "in_progress",
                    # The Confirm dialog can record a simulation after the
                    # user confirms that an incident is already resolved.
                    # This is an audit-only event and never reopens the alert.
                    "resolved",
                },
            }
            if current_state not in transitions[action]:
                connection.rollback()
                raise stale_workflow_error(workflow)
            if action == "resolve" and workflow["handler_user_id"] != actor_user_id:
                connection.rollback()
                raise PermissionError("Only the assigned handler can resolve this incident.")

            valid_role_ids: set[str] = set()
            eligible_broadcast_users: list[str] = []
            if action == "confirm_only":
                placeholders = ",".join("?" for _ in role_ids)
                valid_role_ids = {
                    row["id"]
                    for row in connection.execute(
                        read_sql(f"SELECT id FROM roles WHERE id IN ({placeholders})"),
                        role_ids,
                    ).fetchall()
                }
                if valid_role_ids != set(role_ids):
                    connection.rollback()
                    raise ValueError("One or more selected Broadcast Roles are no longer available.")
                eligible_broadcast_users = self._active_user_ids_for_roles(
                    connection,
                    role_ids,
                    require_enabled_device=True,
                    exclude_user_id=actor_user_id,
                )
                # `allow_empty_audience` remains accepted for older clients,
                # but recipient/device presence is not a prerequisite for
                # recording Confirm only. The selected Role and the Phase B
                # reminder schedule are persisted; dispatch resolves current
                # eligible devices when each notification is sent.

            event_id = new_id("workflow_event")
            event_type = (
                "simulated_emergency_call"
                if action == "simulate_emergency_call"
                else action
            )
            outbox_ids: list[str] = []
            broadcast_outbox_ids: list[str] = []
            decision_update_outbox_ids: list[str] = []
            if action == "dismiss_false_alarm":
                next_state = "dismissed"
                next_alert_status = "dismissed"
                connection.execute(
                    read_sql("UPDATE alerts SET resolved_at=? WHERE id=?"),
                    (now_iso, alert_id),
                )
                self._cancel_mobile_alert_reminder_phases(
                    connection,
                    alert_id,
                    reason="dismissed",
                    completed_at=now_iso,
                )
                connection.execute(
                    read_sql("""
                    UPDATE alerts
                    SET workflow_status='dismissed',handler_user_id=NULL,
                        active_phase_id=NULL,revision=revision+1,workflow_updated_at=?
                    WHERE id=?
                    """),
                    (now_iso, alert_id),
                )
            elif action == "confirm_only":
                next_state = "confirmed_awaiting_handler"
                next_alert_status = "in_review"
                connection.execute(
                    read_sql("UPDATE alerts SET resolved_at=NULL WHERE id=?"),
                    (alert_id,),
                )
                self._cancel_mobile_alert_reminder_phases(
                    connection,
                    alert_id,
                    reason="confirmed",
                    completed_at=now_iso,
                )
                connection.execute(
                    read_sql("""
                    UPDATE alerts
                    SET workflow_status=?,handler_user_id=NULL,broadcast_role_snapshot_json=?,
                        revision=revision+1,workflow_updated_at=? WHERE id=?
                    """),
                    (next_state, json.dumps(role_ids), now_iso, alert_id),
                )
                phase_id = self._start_mobile_alert_reminder_phase(
                    connection,
                    alert_id=alert_id,
                    phase_kind="awaiting_handler",
                    started_at=now,
                    first_after_seconds=first_after_seconds,
                    all_after_seconds=all_after_seconds,
                    initiated_by_user_id=actor_user_id,
                    broadcast_role_ids=role_ids,
                )
                connection.execute(
                    read_sql('UPDATE alerts SET active_phase_id=? WHERE id=?'),
                    (phase_id, alert_id),
                )
            elif action in {"confirm_and_handle", "take_ownership"}:
                next_state = "in_progress"
                next_alert_status = "in_review"
                connection.execute(
                    read_sql("UPDATE alerts SET resolved_at=NULL WHERE id=?"),
                    (alert_id,),
                )
                self._cancel_mobile_alert_reminder_phases(
                    connection,
                    alert_id,
                    reason="handler_assigned",
                    completed_at=now_iso,
                )
                connection.execute(
                    read_sql("""
                    UPDATE alerts
                    SET workflow_status=?,handler_user_id=?,active_phase_id=NULL,
                        revision=revision+1,workflow_updated_at=? WHERE id=?
                    """),
                    (next_state, actor_user_id, now_iso, alert_id),
                )
            elif action in {"confirm_resolved", "resolve"}:
                next_state = "resolved"
                next_alert_status = "resolved"
                connection.execute(
                    read_sql("UPDATE alerts SET resolved_at=? WHERE id=?"),
                    (now_iso, alert_id),
                )
                self._cancel_mobile_alert_reminder_phases(
                    connection,
                    alert_id,
                    reason="resolved",
                    completed_at=now_iso,
                    phase_status="completed",
                )
                connection.execute(
                    read_sql("""
                    UPDATE alerts
                    SET workflow_status='resolved',active_phase_id=NULL,
                        revision=revision+1,workflow_updated_at=? WHERE id=?
                    """),
                    (now_iso, alert_id),
                )
            else:
                next_state = current_state
                next_alert_status = alert["status"]
                # Keep this legacy activity row for consumers that still read
                # alert_actions directly. The workflow-event read model below
                # removes the duplicate display entry for this same action.

            connection.execute(
                read_sql("""
                INSERT INTO alert_actions(
                    id,alert_id,user_id,action,note,broadcast_role_snapshot_json,
                    idempotency_key,created_at,record_origin
                ) VALUES (?,?,?,?,?,?,?,?,'workflow')
                """),
                (
                    event_id,
                    alert_id,
                    actor_user_id,
                    event_type,
                    clean_comment,
                    json.dumps(role_ids, separators=(",", ":")),
                    idempotency_key,
                    now_iso,
                ),
            )

            if action == "confirm_only" and eligible_broadcast_users:
                actor = connection.execute(
                    read_sql("SELECT display_name FROM users WHERE id=?"), (actor_user_id,)
                ).fetchone()
                actor_name = (actor["display_name"] if actor else None) or "An authorised user"
                outbox_id = self._queue_mobile_notification(
                    connection,
                    alert_id=alert_id,
                    title="GuardAId incident needs attention",
                    body=(
                        f"{actor_name} confirmed the {alert['incident_type'].replace('_', ' ').title()} "
                        f"incident at {alert['camera_name']} and requested help."
                    ),
                    action=action,
                    source="alert_broadcast",
                    channel_id="guardaid_urgent_alerts_v8",
                    recipients=eligible_broadcast_users,
                    event_key=f"{idempotency_key}:broadcast",
                    extra_data={
                        "type": alert["incident_type"],
                        "notificationStage": "initial_broadcast",
                    },
                )
                if outbox_id:
                    outbox_ids.append(outbox_id)
                    broadcast_outbox_ids.append(outbox_id)
                    notification_row = connection.execute(
                        read_sql('SELECT notification_id FROM queue_messages WHERE id=?'),
                        (outbox_id,),
                    ).fetchone()
                    if notification_row:
                        connection.execute(
                            read_sql('UPDATE notification_phases SET source_notification_id=? WHERE id=?'),
                            (notification_row["notification_id"], phase_id),
                        )

            if action != "simulate_emergency_call":
                actor = connection.execute(
                    read_sql("SELECT display_name FROM users WHERE id=?"), (actor_user_id,)
                ).fetchone()
                actor_name = (actor["display_name"] if actor else None) or "An authorised user"
                previous_recipients = self._interested_users_for_alert(
                    connection, alert_id, exclude_user_id=actor_user_id
                )
                broadcast_set = set(eligible_broadcast_users)
                update_recipients = [
                    user_id for user_id in previous_recipients
                    if user_id not in broadcast_set
                ]
                if update_recipients:
                    verb = {
                        "dismiss_false_alarm": "dismissed this alert as a false alarm",
                        "confirm_only": "confirmed this incident and requested help",
                        "confirm_and_handle": "confirmed and is handling this incident",
                        "confirm_resolved": "confirmed and resolved this incident",
                        "take_ownership": "is handling this incident",
                        "resolve": "resolved this incident",
                    }[action]
                    outbox_id = self._queue_mobile_notification(
                        connection,
                        alert_id=alert_id,
                        title="Alert update",
                        body=(
                            f"{actor_name} {verb}: {alert['incident_type'].replace('_', ' ').title()} "
                            f"at {alert['camera_name']}."
                        ),
                        action=action,
                        source="alert_decision",
                        channel_id="guardaid_updates_v1",
                        recipients=update_recipients,
                        event_key=f"{idempotency_key}:update",
                    )
                    if outbox_id:
                        outbox_ids.append(outbox_id)
                        decision_update_outbox_ids.append(outbox_id)

            connection.execute(
                read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                (
                    new_id("audit"), actor_user_id, f"alert_workflow_{action}",
                    "alert", alert_id,
                    json.dumps(
                        {
                            "previousWorkflowStatus": current_state,
                            "workflowStatus": next_state,
                            "alertStatus": next_alert_status,
                            "broadcastRoleIds": role_ids,
                            "commentPresent": bool(clean_comment),
                        },
                        separators=(",", ":"),
                    ),
                    now_iso,
                ),
            )
            connection.commit()
        return {
            "idempotent": False,
            "workflow": self.get_mobile_alert_workflow(alert_id),
            "queuedOutboxIds": outbox_ids,
            "broadcastOutboxIds": broadcast_outbox_ids,
            "decisionUpdateOutboxIds": decision_update_outbox_ids,
            "broadcastEligibleUsers": len(eligible_broadcast_users),
        }

    @staticmethod
    def _interested_users_for_alert(
        connection: sqlite3.Connection,
        alert_id: str,
        *,
        exclude_user_id: str,
    ) -> list[str]:
        rows = connection.execute(
            read_sql("""
            SELECT DISTINCT u.id
            FROM notifications n
            JOIN notification_recipients nr ON nr.notification_id=n.id
            JOIN users u ON u.id=nr.user_id AND u.status='active'
            WHERE n.alert_id=? AND nr.user_id IS NOT NULL AND u.id<>?
              AND EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id=u.id)
            UNION
            SELECT DISTINCT u.id
            FROM notifications n
            JOIN notification_recipients nr ON nr.notification_id=n.id
            JOIN user_roles ur ON ur.role_id=nr.role_id
            JOIN users u ON u.id=ur.user_id AND u.status='active'
            WHERE n.alert_id=? AND nr.role_id IS NOT NULL AND u.id<>?
            ORDER BY 1
            """),
            (alert_id, exclude_user_id, alert_id, exclude_user_id),
        ).fetchall()
        return [row["id"] for row in rows]

    def get_alert(self, alert_id: str) -> Alert | None:
        alerts = self.list_alerts(alert_id=alert_id)
        return alerts[0] if alerts else None

    def list_active_alerts(self, camera_id: str) -> list[Alert]:
        return self.list_alerts(camera_id=camera_id, active_only=True, include_evidence=False)

    def list_alerts(self, *, alert_id=None, camera_id=None, active_only=False, include_evidence=True) -> list[Alert]:
        conditions, parameters = [], []
        if alert_id is not None:
            conditions.append('a.id=?')
            parameters.append(alert_id)
        if camera_id is not None:
            conditions.append('a.camera_id=?')
            parameters.append(camera_id)
        if active_only:
            conditions.append("a.status IN ('open','in_review')")
        where = 'WHERE ' + ' AND '.join(conditions) if conditions else ''
        with self._connect() as connection:
            rows = connection.execute(
                read_sql(f"""
                SELECT a.id,a.camera_id,c.name AS camera_name,a.incident_type,a.status,
                       a.sensitivity_mode,a.is_borderline_promoted,
                       a.borderline_review_status,a.evidence_status,cv.version_number,
                       mv.version AS model_version,pv.version AS prompt_version,
                       MIN(d.detected_at) AS first_detected_at,
                       MAX(d.detected_at) AS last_detected_at,
                       MAX(d.confidence) AS max_confidence,
                       COUNT(d.id) AS detection_count,
                       MIN(d.source_start_seconds) AS source_start_seconds,
                       MAX(d.source_end_seconds) AS source_end_seconds
                FROM incident_state a
                JOIN cameras c ON c.id=a.camera_id
                JOIN config_versions cv ON cv.id=a.opening_config_version_id
                JOIN model_versions mv ON mv.id=a.opening_model_version_id
                LEFT JOIN prompt_versions pv ON pv.id=a.binary_prompt_version_id
                JOIN detection_events d ON d.alert_id=a.id
                {where}
                GROUP BY a.id
                ORDER BY last_detected_at DESC
                """), parameters
            ).fetchall()
            decision_rows = []
            ids = [row['id'] for row in rows]
            for offset in range(0, len(ids), 900):
                chunk = ids[offset:offset+900]
                decision_rows.extend(connection.execute(
                    'SELECT alert_id,qualifications_json,weapon_class_scores_json FROM detection_events '
                    'WHERE alert_id IN (' + ','.join('?' for _ in chunk) + ') ORDER BY rowid', chunk).fetchall())
            decisions_by_alert: dict[str, dict] = {}
            for decision in decision_rows:
                summary = decisions_by_alert.setdefault(
                    decision["alert_id"],
                    {"violence": set(), "weapon": {}, "scores": {}},
                )
                try:
                    qualifications = json.loads(decision["qualifications_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    qualifications = {}
                violence_modes = qualifications.get("violence", [])
                if isinstance(violence_modes, list):
                    summary["violence"].update(str(mode) for mode in violence_modes)
                weapon_modes = qualifications.get("weapon", {})
                if isinstance(weapon_modes, dict):
                    for mode, classes in weapon_modes.items():
                        if isinstance(classes, list):
                            summary["weapon"].setdefault(str(mode), set()).update(
                                str(class_name) for class_name in classes
                            )
                try:
                    scores = json.loads(decision["weapon_class_scores_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    scores = {}
                if isinstance(scores, dict):
                    for class_name, score in scores.items():
                        if isinstance(score, (int, float)):
                            summary["scores"][str(class_name)] = max(
                                float(score), summary["scores"].get(str(class_name), 0.0)
                            )
            alerts: list[Alert] = []
            for row in rows:
                description = connection.execute(
                    read_sql("SELECT status,display_text,structured_json FROM vlm_descriptions WHERE alert_id=? ORDER BY created_at DESC LIMIT 1"),
                    (row["id"],),
                ).fetchone()
                evidence_rows = connection.execute(
                    read_sql("""
                    SELECT e.id,e.alert_id,e.evidence_type,m.relative_path,m.content_type,m.sha256,
                           e.source_start_seconds,e.source_end_seconds,e.captured_at
                    FROM evidence_records e JOIN media_files m ON m.id=e.media_file_id
                    WHERE e.alert_id=? ORDER BY e.source_start_seconds IS NULL,
                        e.source_start_seconds,e.captured_at,e.id
                    """),
                    (row["id"],),
                ).fetchall() if include_evidence else []
                evidence = [
                    EvidenceRecord(
                        id=item["id"],
                        alert_id=item["alert_id"],
                        media_type=item["evidence_type"],
                        storage_path=item["relative_path"],
                        content_type=item["content_type"],
                        sha256=item["sha256"],
                        start_seconds=item["source_start_seconds"],
                        end_seconds=item["source_end_seconds"],
                        created_at=item["captured_at"],
                    )
                    for item in evidence_rows
                ]
                decision_summary = decisions_by_alert.get(
                    row["id"], {"violence": set(), "weapon": {}, "scores": {}}
                )
                qualifications = {}
                if decision_summary["violence"]:
                    mode_order = {"sensitive": 0, "moderate": 1, "certain": 2}
                    qualifications["violence"] = sorted(
                        decision_summary["violence"],
                        key=lambda mode: mode_order.get(mode, 99),
                    )
                if decision_summary["weapon"]:
                    qualifications["weapon"] = {
                        mode: sorted(classes)
                        for mode, classes in decision_summary["weapon"].items()
                    }
                alerts.append(
                    Alert.model_validate(
                        {
                            "id": row["id"],
                            "camera_id": row["camera_id"],
                            "camera_name": row["camera_name"],
                            "type": row["incident_type"],
                            "status": row["status"],
                            "confidence": row["max_confidence"],
                            "max_confidence": row["max_confidence"],
                            "detection_count": row["detection_count"],
                            "sensitivity_mode": row["sensitivity_mode"],
                            "is_borderline_promoted": bool(row["is_borderline_promoted"]),
                            "borderline_review_status": row["borderline_review_status"],
                            "evidence_status": row["evidence_status"],
                            "detection_qualifications": qualifications,
                            "weapon_class_scores": decision_summary["scores"],
                            "source_start_seconds": row["source_start_seconds"],
                            "source_end_seconds": row["source_end_seconds"],
                            "first_detected_at": row["first_detected_at"],
                            "last_detected_at": row["last_detected_at"],
                            "description_status": description["status"] if description else "disabled",
                            "scene_description": _readable_scene_record(
                                description["display_text"], description["structured_json"]
                            ) if description else None,
                            "scene_summary": _scene_summary_from_storage(
                                description["display_text"], description["structured_json"]
                            ) if description else None,
                            "evidence": evidence,
                            "model_version": row["model_version"],
                            "prompt_version": row["prompt_version"],
                            "config_version": row["version_number"],
                        }
                    )
                )
        return alerts

    def save_evidence(self, evidence: EvidenceRecord, size_bytes: int) -> EvidenceRecord:
        media_id = new_id("media")
        evidence_type = evidence.media_type
        media_kind = "evidence_image" if evidence_type == "weapon_screenshot" else "evidence_clip"
        with self._lock, self._connect() as connection:
            connection.execute(
                read_sql("""
                INSERT INTO media_files(
                    id,relative_path,original_filename,media_kind,content_type,size_bytes,sha256,created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """),
                (
                    media_id,
                    evidence.storage_path,
                    Path(evidence.storage_path).name,
                    media_kind,
                    evidence.content_type,
                    size_bytes,
                    evidence.sha256,
                    _iso(evidence.created_at),
                ),
            )
            connection.execute(
                read_sql("""
                INSERT INTO evidence_records(
                    id,alert_id,media_file_id,evidence_type,source_start_seconds,
                    source_end_seconds,captured_at
                ) VALUES (?,?,?,?,?,?,?)
                """),
                (
                    evidence.id,
                    evidence.alert_id,
                    media_id,
                    evidence_type,
                    evidence.start_seconds,
                    evidence.end_seconds,
                    _iso(evidence.created_at),
                ),
            )
            connection.execute(
                read_sql("UPDATE alerts SET evidence_status='available' WHERE id=?"),
                (evidence.alert_id,),
            )
            connection.commit()
        return evidence

    def set_alert_evidence_status(self, alert_id: str, status: str) -> None:
        if status not in {"preparing", "available", "failed", "legacy_unknown"}:
            raise ValueError("Invalid alert evidence status.")
        with self._lock, self._connect() as connection:
            connection.execute(
                read_sql("UPDATE alerts SET evidence_status=? WHERE id=?"),
                (status, alert_id),
            )
            connection.commit()

    def delete_evidence(self, alert_id: str, media_type: str, *, keep_id: str | None = None) -> list[str]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT e.id,e.media_file_id,m.relative_path
                FROM evidence_records e JOIN media_files m ON m.id=e.media_file_id
                    WHERE e.alert_id=? AND e.evidence_type=? AND (? IS NULL OR e.id<>?)
                """),
                (alert_id, media_type, keep_id, keep_id),
            ).fetchall()
            if not rows:
                return []
            connection.executemany(
                read_sql("DELETE FROM evidence_records WHERE id=?"), [(row["id"],) for row in rows]
            )
            connection.executemany(
                read_sql("DELETE FROM media_files WHERE id=?"), [(row["media_file_id"],) for row in rows]
            )
            connection.commit()
        return [row["relative_path"] for row in rows]

    def update_description(
        self, alert_id: str, status: str, text: Any, error_code: str | None = None
    ) -> None:
        display, structured = _scene_storage_values(text)
        with self._lock, self._connect() as connection:
            connection.execute(
                read_sql("""
                UPDATE vlm_descriptions
                SET status=?, structured_json=?, display_text=?, error_code=?, completed_at=?,output_format=?
                WHERE id=(
                    SELECT id FROM vlm_descriptions WHERE alert_id=?
                    ORDER BY created_at DESC LIMIT 1
                )
                """),
                (status, structured, display, error_code, _iso(), _output_format(display,structured), alert_id),
            )
            connection.commit()

    def get_scene_task(self, alert_id):
        with self._connect() as connection:
            row = connection.execute('SELECT state_json FROM scene_generation_tasks WHERE alert_id=?', (alert_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_scene_task(self, alert_id, state, diagnostic):
        from .inference.scene_reliability import json_safe
        with self._lock, self._connect() as connection:
            connection.execute('INSERT INTO scene_generation_tasks VALUES (?,?,?) ON CONFLICT(alert_id) DO UPDATE SET state_json=excluded.state_json,diagnostic_json=excluded.diagnostic_json',
                (alert_id, json.dumps(json_safe(state), allow_nan=False), json.dumps(json_safe(diagnostic), allow_nan=False)))
            connection.commit()

    def scene_diagnostics(self):
        with self._connect() as connection:
            return {row[0]: json.loads(row[1]) for row in connection.execute('SELECT alert_id,diagnostic_json FROM scene_generation_tasks')}

    def set_description_policy(self, alert_id: str, settings: SystemSettings) -> None:
        """Attribute generation to the actual worker, including recovered old jobs."""
        with self._lock, self._connect() as connection:
            model = self._ensure_model(connection, "scene_description", settings.vlm.model_id)
            prompt = self._ensure_prompt(connection, "scene_description", settings.vlm.scene_prompt_version)
            config = connection.execute("SELECT id FROM config_versions WHERE version_number=?", (settings.config_version,)).fetchone()
            connection.execute(
                "UPDATE vlm_descriptions SET model_version_id=?,prompt_version_id=?,config_version_id=? "
                "WHERE id=(SELECT id FROM vlm_descriptions WHERE alert_id=? ORDER BY created_at DESC LIMIT 1)",
                (model, prompt, config["id"], alert_id),
            )
            connection.commit()

    def save_borderline_review(
        self,
        *,
        camera_id: str,
        alert_id: str | None,
        detector_confidence: float,
        raise_alert: bool,
        uncertainty: str,
        brief_reason: str,
        source_start_seconds: float,
        source_end_seconds: float,
        model_version: str,
        prompt_version: str,
        config_version: int,
    ) -> None:
        with self._lock, self._connect() as connection:
            model_id = self._ensure_model(connection, "borderline_binary", model_version)
            prompt_id = self._ensure_prompt(connection, "borderline_binary", prompt_version)
            config_id = connection.execute(
                read_sql("SELECT id FROM config_versions WHERE version_number=?"), (config_version,)
            ).fetchone()["id"]
            connection.execute(read_sql("""
                INSERT INTO borderline_reviews(
                    id,camera_id,alert_id,model_version_id,prompt_version_id,config_version_id,
                    detector_confidence,raise_alert,uncertainty,brief_reason,
                    source_start_seconds,source_end_seconds,reviewed_at,record_origin,status
                ) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,'audit_only',CASE ?8 WHEN 1 THEN 'positive' ELSE 'negative' END)
                """),(
                    new_id("review"), camera_id, alert_id, model_id, prompt_id, config_id,
                    detector_confidence, int(raise_alert), uncertainty, brief_reason,
                    source_start_seconds, source_end_seconds, _iso(),
                ))
            connection.commit()

    def list_enabled_devices(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("SELECT id,user_id,fcm_registration_value FROM devices WHERE enabled=1")
            ).fetchall()
        return [dict(row) for row in rows]

    def list_enabled_devices_for_all_active_users(self) -> list[dict]:
        """Return devices belonging to active users with an assigned role."""
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT DISTINCT d.id,d.user_id,d.fcm_registration_value
                FROM devices d
                JOIN users u ON u.id=d.user_id AND u.status='active'
                WHERE d.enabled=1
                  AND EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id=u.id)
                ORDER BY d.user_id,d.id
                """)
            ).fetchall()
        return [dict(row) for row in rows]

    def list_alert_notification_audience_devices(self, alert_id: str) -> list[dict]:
        """Return devices for accounts that have actually received an initial push."""
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT DISTINCT d.id,d.user_id,d.fcm_registration_value,
                       a.qualification_reasons_json AS recipient_reasons
                FROM alert_notification_audience a
                JOIN users u ON u.id=a.user_id AND u.status='active'
                JOIN user_roles ur ON ur.user_id=u.id
                JOIN devices d ON d.user_id=u.id AND d.enabled=1
                WHERE a.alert_id=? AND a.first_sent_at IS NOT NULL
                ORDER BY d.user_id,d.id
                """),
                (alert_id,),
            ).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["recipient_reasons"] = json.loads(value["recipient_reasons"] or "[]")
            values.append(value)
        return values

    def prepare_mobile_alert_initial_delivery(
        self, outbox_id: str, now: datetime | None = None
    ) -> set[str]:
        """Recheck eligibility and avoid replaying an old initial alert push."""
        with self._connect() as connection:
            row = connection.execute(
                read_sql('SELECT data_payload_json,created_at FROM queue_messages WHERE id=?'),
                (outbox_id,),
            ).fetchone()
            recipients = connection.execute(
                read_sql('SELECT user_id FROM queued_users WHERE outbox_id=?'),
                (outbox_id,),
            ).fetchall()
        if row is None:
            return set()
        try:
            queued_at = datetime.fromisoformat(row["created_at"])
        except (TypeError, ValueError):
            return set()
        if queued_at.tzinfo is None:
            queued_at = queued_at.replace(tzinfo=timezone.utc)
        elapsed = ((now or utc_now()) - queued_at).total_seconds()
        if elapsed > INITIAL_ALERT_DELIVERY_MAX_AGE_SECONDS:
            return set()
        try:
            payload = json.loads(row["data_payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            return set()
        alert_id = payload.get("alertId")
        if not alert_id:
            return set()
        alert = next((item for item in self.list_alerts() if item.id == alert_id), None)
        if alert is None:
            return set()
        alert_age = ((now or utc_now()) - alert.first_detected_at).total_seconds()
        if alert_age > INITIAL_ALERT_DELIVERY_MAX_AGE_SECONDS:
            return set()
        workflow = self.get_mobile_alert_workflow(alert_id)
        if not workflow or workflow["workflow_status"] not in {
            "awaiting_confirmation", "confirmed_awaiting_handler"
        }:
            return set()
        expected_status = payload.get("workflowStatus")
        expected_revision = payload.get("workflowRevision")
        if expected_status is not None and expected_status != workflow["workflow_status"]:
            return set()
        if expected_revision is not None:
            try:
                expected_revision = int(expected_revision)
            except (TypeError, ValueError):
                return set()
            if expected_revision != int(workflow["revision"]):
                return set()
        current = {
            item["user_id"]
            for item in self.list_enabled_devices_for_alert(alert)
        }
        return current & {item["user_id"] for item in recipients}

    def create_notification(
        self, alert_id: str, title: str, body: str, payload_json: str
    ) -> str:
        notification_id = new_id("notification")
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("INSERT INTO notifications(id,alert_id,title,body,data_payload_json,created_at,message_kind,visibility_policy) VALUES (?,?,?,?,?,?,'legacy','broadcast')"),(notification_id, alert_id, title, body, payload_json, _iso()))
            connection.commit()
        return notification_id

    def notification_content(self, notification_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                read_sql("SELECT title,body FROM notifications WHERE id=?"),
                (notification_id,),
            ).fetchone()
        return dict(row) if row else None

    def record_notification_delivery(
        self,
        notification_id: str,
        device_id: str,
        status: str,
        provider_message_id: str | None,
        error_code: str | None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("""
                INSERT INTO notification_deliveries(
                    id,notification_id,device_id,status,provider_message_id,
                    attempt_count,last_attempt_at,error_code,updated_at,recipient_user_id
                ) VALUES (?1,?2,?3,?4,?5,1,?6,?7,?6,(SELECT user_id FROM devices WHERE id=?3))
                ON CONFLICT(notification_id,device_id) DO UPDATE SET
                    status=excluded.status,
                    provider_message_id=excluded.provider_message_id,
                    attempt_count=notification_deliveries.attempt_count+1,
                    last_attempt_at=excluded.last_attempt_at,
                    error_code=excluded.error_code,updated_at=excluded.updated_at
                """),(
                    new_id("delivery"), notification_id, device_id, status,
                    provider_message_id, _iso(), error_code,
                ))
            if status == "sent":
                recipient = connection.execute(
                    read_sql("""
                    SELECT n.alert_id,n.data_payload_json,d.user_id
                    FROM notifications n JOIN devices d ON d.id=?
                    WHERE n.id=?
                    """),
                    (device_id, notification_id),
                ).fetchone()
                if recipient:
                    try:
                        payload = json.loads(recipient["data_payload_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        payload = {}
                    if payload.get("source") == "alert_created":
                        connection.execute(
                            read_sql("""
                            UPDATE alert_notification_audience
                            SET first_sent_at=COALESCE(first_sent_at,?)
                            WHERE alert_id=? AND user_id=? AND first_notification_id=?
                            """),
                            (
                                _iso(), recipient["alert_id"], recipient["user_id"],
                                notification_id,
                            ),
                        )
            connection.commit()

    def claim_mobile_notification_outbox(self, now: datetime | None = None) -> dict | None:
        now_value = now or utc_now()
        now_iso = _iso(now_value)
        stale_before = _iso(now_value - timedelta(seconds=60))
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            row = connection.execute(
                read_sql("""
                SELECT id,notification_id,channel_id,data_payload_json,attempt_count
                FROM queue_messages
                WHERE (status='pending' AND next_attempt_at<=?)
                   OR (status='dispatching' AND claimed_at<=?)
                ORDER BY created_at,id LIMIT 1
                """),
                (now_iso, stale_before),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            attempt = int(row["attempt_count"]) + 1
            connection.execute(
                read_sql("""
                UPDATE notification_outbox
                SET status='dispatching',attempt_count=?,claimed_at=?,updated_at=?
                WHERE id=?
                """),
                (attempt, now_iso, now_iso, row["id"]),
            )
            recipients = connection.execute(
                read_sql("""
                SELECT user_id FROM queued_users
                WHERE outbox_id=? ORDER BY user_id
                """),
                (row["id"],),
            ).fetchall()
            connection.commit()
        return {
            **dict(row),
            "attempt_count": attempt,
            "recipient_user_ids": [item["user_id"] for item in recipients],
        }

    def list_mobile_outbox_devices(self, outbox_id: str, user_ids: list[str]) -> list[dict]:
        unique_ids = list(dict.fromkeys(user_id for user_id in user_ids if user_id))
        if not unique_ids:
            return []
        placeholders = ",".join("?" for _ in unique_ids)
        with self._connect() as connection:
            rows = connection.execute(
                read_sql(f"""
                SELECT DISTINCT d.id,d.user_id,d.fcm_registration_value,
                       prior.status AS prior_status
                FROM devices d
                JOIN users u ON u.id=d.user_id AND u.status='active'
                JOIN user_roles ur ON ur.user_id=u.id
                LEFT JOIN device_results prior
                  ON prior.outbox_id=? AND prior.device_id=d.id
                WHERE d.enabled=1 AND u.id IN ({placeholders})
                  AND (prior.recipient_user_id IS NULL OR prior.recipient_user_id=d.user_id)
                ORDER BY d.user_id,d.id
                """),
                (outbox_id, *unique_ids),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_mobile_outbox_delivery(
        self,
        outbox_id: str,
        notification_id: str,
        device_id: str,
        status: str,
        provider_message_id: str | None,
        error_code: str | None,
        *, recipient_user_id: str | None = None,
    ) -> None:
        if status not in {"sent", "failed"}:
            raise ValueError("Unsupported notification delivery status.")
        now = _iso()
        with self._lock, self._connect() as connection:
            if recipient_user_id is None:
                recipient_user_id=connection.execute("SELECT user_id FROM devices WHERE id=?",(device_id,)).fetchone()[0]
            connection.execute(read_sql("""
                INSERT INTO notification_deliveries(
                    id,notification_id,device_id,status,provider_message_id,
                    attempt_count,last_attempt_at,error_code,updated_at,recipient_user_id
                ) VALUES (?1,?2,?3,?4,?5,1,?6,?7,?6,?8)
                ON CONFLICT(notification_id,device_id) DO UPDATE SET
                    status=excluded.status,
                    provider_message_id=excluded.provider_message_id,
                    attempt_count=notification_deliveries.attempt_count+1,
                    last_attempt_at=excluded.last_attempt_at,
                    error_code=excluded.error_code,updated_at=excluded.updated_at
                """),(
                    new_id("delivery"), notification_id, device_id, status,
                    provider_message_id, now, error_code,recipient_user_id,
                ))
            if status == "sent":
                outbox = connection.execute(
                    read_sql('SELECT notification_id,data_payload_json FROM queue_messages WHERE id=?'),
                    (outbox_id,),
                ).fetchone()
                if outbox:
                    try:
                        payload = json.loads(outbox["data_payload_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        payload = {}
                    if payload.get("source") == "alert_created":
                        user = connection.execute(
                            read_sql("SELECT ? AS user_id"), (recipient_user_id,)
                        ).fetchone()
                        if user:
                            connection.execute(
                                read_sql("""
                                UPDATE alert_notification_audience SET first_sent_at=COALESCE(first_sent_at,?)
                                WHERE alert_id=? AND user_id=? AND first_notification_id=?
                                """),
                                (now, payload.get("alertId"), user["user_id"], outbox["notification_id"]),
                            )
            outbox = connection.execute(
                read_sql('SELECT data_payload_json FROM queue_messages WHERE id=?'),
                (outbox_id,),
            ).fetchone()
            if outbox:
                try:
                    payload = json.loads(outbox["data_payload_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    payload = {}
                if payload.get("source") == "alert_reminder":
                    user = connection.execute(
                        read_sql("SELECT ? AS user_id"), (recipient_user_id,)
                    ).fetchone()
                    if user:
                        connection.execute(read_sql("""
                            UPDATE notification_phase_audience
                            SET status=CASE WHEN status='sent' THEN 'sent' ELSE ? END,
                                updated_at=?
                            WHERE (SELECT alert_id FROM notification_phases p WHERE p.id=notification_phase_audience.phase_id)=? AND phase_id=? AND user_id=? AND stage=?
                              AND notification_id=(SELECT notification_id FROM notification_outbox WHERE id=?)
                            """),(
                                status, now, payload.get("alertId"),
                                payload.get("reminderPhaseId"), user["user_id"],
                                int(payload.get("reminderStage", 0)), outbox_id,
                            ))
            connection.commit()

    def complete_mobile_notification_outbox(
        self,
        outbox_id: str,
        *,
        no_recipients: bool = False,
        error_code: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        now_value = now or utc_now()
        now_iso = _iso(now_value)
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            outbox = connection.execute(
                read_sql('SELECT attempt_count FROM queue_messages WHERE id=?'),
                (outbox_id,),
            ).fetchone()
            if outbox is None:
                connection.rollback()
                raise ValueError("Notification dispatch was not found.")
            stats = connection.execute(
                read_sql("""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) AS sent,
                       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
                FROM device_results WHERE outbox_id=?
                """),
                (outbox_id,),
            ).fetchone()
            total = int(stats["total"] or 0)
            sent = int(stats["sent"] or 0)
            failed = int(stats["failed"] or 0)
            attempts = int(outbox["attempt_count"])
            if no_recipients or total == 0:
                status = "no_recipients"
                next_attempt = now_iso
            elif failed and attempts < 3:
                status = "pending"
                next_attempt = _iso(now_value + timedelta(seconds=30 * attempts))
            elif failed and sent:
                status = "partial"
                next_attempt = now_iso
            elif failed:
                status = "failed"
                next_attempt = now_iso
            else:
                status = "sent"
                next_attempt = now_iso
            connection.execute(
                read_sql("""
                UPDATE notification_outbox
                SET status=?,next_attempt_at=?,claimed_at=NULL,last_error_code=?,updated_at=?
                WHERE id=?
                """),
                (status, next_attempt, error_code, now_iso, outbox_id),
            )
            reminder_status = "queued" if status == "pending" else status
            connection.execute(read_sql("""
                UPDATE notification_phase_dispatches
                SET status=?,recipient_count=?,recipient_count_unit='device',sent_count=?,failed_count=?,
                    error_code=?,completed_at=?
                WHERE notification_id=?
                  AND status NOT IN ('skipped_stopped','skipped_superseded','skipped_responded')
                """),(
                    reminder_status,
                    total,
                    sent,
                    failed,
                    error_code,
                    None if status == "pending" else now_iso,
                    connection.execute(
                        read_sql('SELECT notification_id FROM queue_messages WHERE id=?'),
                        (outbox_id,),
                    ).fetchone()["notification_id"],
                ))
            connection.commit()
        return {"status": status, "recipientCount": total, "sentCount": sent, "failedCount": failed}

    def upsert_firebase_user(
        self, firebase_uid: str, email: str | None, display_name: str | None
    ) -> dict:
        now = _iso()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                read_sql("SELECT id,status FROM users WHERE firebase_uid=?"), (firebase_uid,)
            ).fetchone()
            user_id = row["id"] if row else new_id("user")
            connection.execute(
                read_sql("""
                INSERT INTO users(id,firebase_uid,email_cache,display_name,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(firebase_uid) DO UPDATE SET
                    email_cache=excluded.email_cache,
                    display_name=CASE
                        WHEN excluded.display_name IS NOT NULL AND trim(excluded.display_name) <> ''
                        THEN excluded.display_name
                        ELSE users.display_name
                    END,
                    updated_at=excluded.updated_at
                """),
                (user_id, firebase_uid, email, display_name, "pending", now, now),
            )
            connection.commit()
            saved = connection.execute(
                read_sql("SELECT id,firebase_uid,email_cache,display_name,status FROM users WHERE firebase_uid=?"),
                (firebase_uid,),
            ).fetchone()
        return dict(saved)

    def register_device(
        self,
        user_id: str,
        registration_value: str,
        platform: str,
        device_label: str | None,
        installation_id: str | None,
    ) -> str:
        now = _iso()
        device_id = new_id("device")
        with self._lock, self._connect() as connection:
            connection.execute(
                read_sql("""
                INSERT INTO devices(
                    id,user_id,installation_id,fcm_registration_value,platform,device_label,
                    enabled,last_seen_at,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,1,?,?,?)
                ON CONFLICT(fcm_registration_value) DO UPDATE SET
                    user_id=excluded.user_id,
                    installation_id=excluded.installation_id,
                    platform=excluded.platform,
                    device_label=excluded.device_label,
                    enabled=1,
                    last_seen_at=excluded.last_seen_at,
                    updated_at=excluded.updated_at
                """),
                (
                    device_id,
                    user_id,
                    installation_id,
                    registration_value,
                    platform,
                    device_label,
                    now,
                    now,
                    now,
                ),
            )
            connection.commit()
            row = connection.execute(
                read_sql("SELECT id FROM devices WHERE fcm_registration_value=?"), (registration_value,)
            ).fetchone()
        return row["id"]

    def mobile_user_profile(self, user_id: str) -> dict | None:
        with self._connect() as connection:
            user = connection.execute(
                read_sql("""
                SELECT id,firebase_uid,email_cache,display_name,status
                FROM users WHERE id=?
                """),
                (user_id,),
            ).fetchone()
            if not user:
                return None
            roles = connection.execute(
                read_sql("""
                SELECT r.id,r.name,r.display_name,r.description,r.sensitivity_mode,
                       r.can_manage_roles,r.can_manage_users,r.can_manage_borderline_settings
                FROM roles r
                JOIN user_roles ur ON ur.role_id=r.id
                WHERE ur.user_id=?
                ORDER BY r.name
                """),
                (user_id,),
            ).fetchall()
        values = [dict(role) for role in roles]
        role_names = {role["name"] for role in values}
        can_manage = any(
            role.get("can_manage_roles") or role.get("can_manage_users") or role.get("can_manage_borderline_settings")
            for role in values
        )
        return {
            **dict(user),
            "roles": values,
            "is_admin": "school_administrator" in role_names or "admin" in role_names or can_manage,
            "sensitivity_mode": values[0]["sensitivity_mode"] if values else None,
        }

    def list_mobile_roles(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT r.id,r.name,COALESCE(NULLIF(r.display_name,''),r.name) AS display_name,
                       r.description,r.sensitivity_mode,r.is_system,
                       r.can_manage_roles,r.can_manage_users,r.can_manage_borderline_settings,
                       r.updated_at,
                       COUNT(ur.user_id) AS user_count
                FROM roles r
                LEFT JOIN user_roles ur ON ur.role_id=r.id
                GROUP BY r.id
                ORDER BY r.is_system DESC, COALESCE(NULLIF(r.display_name,''),r.name)
                """)
            ).fetchall()
        return [dict(row) for row in rows]

    def user_has_permission(self, user_id: str, permission: str) -> bool:
        if permission not in {
            "can_manage_roles", "can_manage_users", "can_manage_borderline_settings"
        }:
            return False
        with self._connect() as connection:
            row = connection.execute(
                read_sql(f"""
                SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id
                JOIN users u ON u.id=ur.user_id
                WHERE ur.user_id=? AND u.status='active' AND r.{permission}=1 LIMIT 1
                """),
                (user_id,),
            ).fetchone()
        return row is not None

    @staticmethod
    def _assert_role_grant_allowed(
        connection: sqlite3.Connection, actor_user_id: str, role: sqlite3.Row
    ) -> None:
        actor_roles = connection.execute(
            read_sql("""
            SELECT r.name,r.can_manage_roles,r.can_manage_users,
                   r.can_manage_borderline_settings
            FROM user_roles ur
            JOIN roles r ON r.id=ur.role_id
            JOIN users u ON u.id=ur.user_id
            WHERE ur.user_id=? AND u.status='active'
            """),
            (actor_user_id,),
        ).fetchall()
        actor_permissions = {
            "can_manage_roles": any(bool(row["can_manage_roles"]) for row in actor_roles),
            "can_manage_users": any(bool(row["can_manage_users"]) for row in actor_roles),
            "can_manage_borderline_settings": any(
                bool(row["can_manage_borderline_settings"]) for row in actor_roles
            ),
        }
        if not actor_permissions["can_manage_users"]:
            raise PermissionError("User-management permission is required.")
        requested = {
            "can_manage_roles": bool(role["can_manage_roles"]),
            "can_manage_users": bool(role["can_manage_users"]),
            "can_manage_borderline_settings": bool(
                role["can_manage_borderline_settings"]
            ),
        }
        if any(value and not actor_permissions[key] for key, value in requested.items()):
            raise PermissionError("You cannot grant a role with permissions you do not possess.")
        if role["name"] in {"school_administrator", "admin"} and not any(
            row["name"] in {"school_administrator", "admin"} for row in actor_roles
        ):
            raise PermissionError("Only a School Administrator can assign this role.")

    @staticmethod
    def _assert_not_last_active_administrator(
        connection: sqlite3.Connection,
        user_id: str,
        requested_status: str,
        requested_role_name: str | None,
    ) -> None:
        current = connection.execute(
            read_sql("""
            SELECT u.status,r.name AS role_name
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id=u.id
            LEFT JOIN roles r ON r.id=ur.role_id
            WHERE u.id=?
            """),
            (user_id,),
        ).fetchall()
        was_active_admin = any(
            row["status"] == "active"
            and row["role_name"] in {"school_administrator", "admin"}
            for row in current
        )
        remains_active_admin = (
            requested_status == "active"
            and requested_role_name in {"school_administrator", "admin"}
        )
        if not was_active_admin or remains_active_admin:
            return
        remaining = connection.execute(
            read_sql("""
            SELECT COUNT(*) AS count
            FROM users u
            JOIN user_roles ur ON ur.user_id=u.id
            JOIN roles r ON r.id=ur.role_id
            WHERE u.status='active'
              AND r.name IN ('school_administrator','admin')
              AND u.id<>?
            """),
            (user_id,),
        ).fetchone()["count"]
        if remaining < 1:
            raise ValueError("The last active School Administrator cannot be changed.")

    def assert_mobile_user_role_assignable(
        self, actor_user_id: str, role_id: str
    ) -> None:
        with self._connect() as connection:
            role = connection.execute(
                read_sql("SELECT * FROM roles WHERE id=?"), (role_id,)
            ).fetchone()
            if not role:
                raise KeyError("Role not found.")
            self._assert_role_grant_allowed(connection, actor_user_id, role)

    def mobile_user_by_email(self, email: str) -> dict | None:
        normalized = email.strip().casefold()
        with self._connect() as connection:
            row = connection.execute(
                read_sql("SELECT id,email_cache FROM users WHERE lower(trim(email_cache))=? LIMIT 1"),
                (normalized,),
            ).fetchone()
        return dict(row) if row else None

    def create_mobile_user(
        self,
        firebase_uid: str,
        email: str,
        display_name: str,
        role_id: str,
        actor_user_id: str,
        identity_action: str,
    ) -> dict:
        now = _iso()
        clean_email = email.strip().casefold()
        clean_name = " ".join(display_name.split())
        if identity_action not in {"created", "linked"}:
            raise ValueError("Unsupported Firebase identity action.")
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            try:
                role = connection.execute(
                    read_sql("SELECT * FROM roles WHERE id=?"), (role_id,)
                ).fetchone()
                if not role:
                    raise KeyError("Role not found.")
                self._assert_role_grant_allowed(connection, actor_user_id, role)
                if connection.execute(
                    read_sql("SELECT 1 FROM users WHERE lower(trim(email_cache))=? LIMIT 1"),
                    (clean_email,),
                ).fetchone():
                    raise ValueError("A GuardAId user with this email already exists.")
                if connection.execute(
                    read_sql("SELECT 1 FROM users WHERE firebase_uid=? LIMIT 1"), (firebase_uid,)
                ).fetchone():
                    raise ValueError("This Firebase identity is already linked to GuardAId.")
                user_id = new_id("user")
                connection.execute(
                    read_sql("""
                    INSERT INTO users(
                        id,firebase_uid,email_cache,display_name,status,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """),
                    (user_id, firebase_uid, clean_email, clean_name, "active", now, now),
                )
                connection.execute(
                    read_sql("""
                    INSERT INTO user_roles(user_id,role_id,assigned_by_user_id,assigned_at)
                    VALUES (?,?,?,?)
                    """),
                    (user_id, role_id, actor_user_id, now),
                )
                role_setting = connection.execute(
                    read_sql('SELECT default_enabled FROM role_preferences WHERE role_id=?'),
                    (role_id,),
                ).fetchone()
                connection.execute(read_sql("UPDATE users SET borderline_enabled=?2,borderline_source='role_default',borderline_updated_by_user_id=?3,borderline_updated_at=?4 WHERE id=?1"),(user_id, int(bool(role_setting and role_setting["default_enabled"])), actor_user_id, now))
                connection.execute(
                    read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                    (
                        new_id("audit"), actor_user_id,
                        "create_mobile_user" if identity_action == "created" else "link_mobile_user",
                        "user", user_id,
                        json.dumps({"roleId": role_id, "status": "active"}), now,
                    ),
                )
                saved = {
                    "id": user_id,
                    "firebase_uid": firebase_uid,
                    "email_cache": clean_email,
                    "display_name": clean_name,
                    "status": "active",
                    "role_id": role_id,
                    "role_name": role["name"],
                    "role_display_name": role["display_name"] or role["name"],
                    "sensitivity_mode": role["sensitivity_mode"],
                    "can_manage_roles": role["can_manage_roles"],
                    "can_manage_users": role["can_manage_users"],
                    "can_manage_borderline_settings": role[
                        "can_manage_borderline_settings"
                    ],
                }
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return saved

    def create_mobile_role(
        self,
        display_name: str,
        description: str | None,
        sensitivity_mode: str,
        permissions: dict[str, bool],
        actor_user_id: str,
    ) -> dict | None:
        clean_name = " ".join(display_name.split()).strip()
        if not clean_name:
            return None
        now = _iso()
        role_id = new_id("role")
        with self._lock, self._connect() as connection:
            duplicate = connection.execute(
                read_sql("SELECT 1 FROM roles WHERE lower(COALESCE(display_name,name))=lower(?)"),
                (clean_name,),
            ).fetchone()
            if duplicate:
                raise ValueError("A role with this name already exists.")
            connection.execute(
                read_sql("""
                INSERT INTO roles(
                    id,name,display_name,description,sensitivity_mode,is_system,
                    can_manage_roles,can_manage_users,can_manage_borderline_settings,created_at,updated_at
                ) VALUES (?,?,?,?,?,0,?,?,?,?,?)
                """),
                (
                    role_id,
                    f"custom_{role_id.removeprefix('role_')}",
                    clean_name,
                    (description or "").strip() or None,
                    sensitivity_mode,
                    int(permissions.get("can_manage_roles", False)),
                    int(permissions.get("can_manage_users", False)),
                    int(permissions.get("can_manage_borderline_settings", False)),
                    now,
                    now,
                ),
            )
            connection.execute(read_sql('UPDATE roles SET borderline_default_enabled=0,borderline_updated_at=?2 WHERE id=?1'),(role_id, now))
            connection.execute(
                read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                (
                    new_id("audit"), actor_user_id, "create_role", "role", role_id,
                    json.dumps({"displayName": clean_name, "permissions": permissions}), now,
                ),
            )
            connection.commit()
        return next(item for item in self.list_mobile_roles() if item["id"] == role_id)

    def update_mobile_role(
        self,
        role_id: str,
        actor_user_id: str,
        sensitivity_mode: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        permissions: dict[str, bool] | None = None,
    ) -> bool:
        now = _iso()
        with self._lock, self._connect() as connection:
            role = connection.execute(read_sql("SELECT * FROM roles WHERE id=?"), (role_id,)).fetchone()
            if not role:
                return False
            changes: dict[str, object] = {}
            if sensitivity_mode is not None:
                changes["sensitivity_mode"] = sensitivity_mode
            if display_name is not None and not role["is_system"]:
                clean_name = " ".join(display_name.split()).strip()
                duplicate = connection.execute(
                    read_sql("SELECT 1 FROM roles WHERE id<>? AND lower(COALESCE(display_name,name))=lower(?)"),
                    (role_id, clean_name),
                ).fetchone()
                if duplicate:
                    raise ValueError("A role with this name already exists.")
                changes["display_name"] = clean_name
            if description is not None and not role["is_system"]:
                changes["description"] = description.strip() or None
            if permissions and not role["is_system"]:
                for key in ("can_manage_roles", "can_manage_users", "can_manage_borderline_settings"):
                    if key in permissions:
                        changes[key] = int(bool(permissions[key]))
            if not changes:
                return True
            changes["updated_at"] = now
            assignments = ",".join(f"{key}=?" for key in changes)
            connection.execute(
                read_sql(f"UPDATE roles SET {assignments} WHERE id=?"),
                (*changes.values(), role_id),
            )
            connection.execute(
                read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                (
                    new_id("audit"), actor_user_id, "update_role", "role", role_id,
                    json.dumps({"changes": changes}, default=str), now,
                ),
            )
            connection.commit()
        return True

    def update_role_sensitivity(
        self, role_id: str, sensitivity_mode: str, actor_user_id: str
    ) -> bool:
        return self.update_mobile_role(
            role_id, actor_user_id, sensitivity_mode=sensitivity_mode
        )

    def delete_mobile_role(self, role_id: str, actor_user_id: str) -> dict | None:
        """Delete a custom role and safely revoke its assignments in one transaction."""
        now = _iso()
        with self._lock, self._connect() as connection:
            role = connection.execute(
                read_sql("SELECT id,name,display_name,is_system FROM roles WHERE id=?"),
                (role_id,),
            ).fetchone()
            if not role:
                return None
            if role["is_system"]:
                raise ValueError("System roles cannot be deleted.")

            affected = connection.execute(
                read_sql("""
                SELECT u.id,u.status,COUNT(other.role_id) AS other_role_count
                FROM users u
                JOIN user_roles assigned ON assigned.user_id=u.id
                LEFT JOIN user_roles other
                  ON other.user_id=u.id AND other.role_id<>?
                WHERE assigned.role_id=?
                GROUP BY u.id,u.status
                """),
                (role_id, role_id),
            ).fetchall()

            remaining_managers = connection.execute(
                read_sql("""
                SELECT COUNT(DISTINCT u.id) AS count
                FROM users u
                JOIN user_roles ur ON ur.user_id=u.id
                JOIN roles r ON r.id=ur.role_id
                WHERE u.status='active' AND ur.role_id<>?
                  AND (r.name IN ('school_administrator','admin')
                    OR r.can_manage_roles=1 OR r.can_manage_users=1
                    OR r.can_manage_borderline_settings=1)
                """),
                (role_id,),
            ).fetchone()["count"]
            if remaining_managers < 1:
                raise ValueError(
                    "This role cannot be deleted because it would remove the last active administrator."
                )

            settings = connection.execute(
                read_sql("SELECT borderline_alert_enabled,borderline_recipient_roles_json FROM mobile_preferences WHERE id='global'")
            ).fetchone()
            recipient_ids = json.loads(settings["borderline_recipient_roles_json"])
            updated_recipient_ids = [item for item in recipient_ids if item != role_id]
            removed_from_recipients = len(updated_recipient_ids) != len(recipient_ids)
            borderline_enabled = bool(settings["borderline_alert_enabled"])
            if removed_from_recipients:
                if borderline_enabled and not updated_recipient_ids:
                    borderline_enabled = False
                _write_mobile_preferences(connection,*(
                        int(borderline_enabled),
                        json.dumps(updated_recipient_ids),
                        actor_user_id,
                        now,
                    ))

            connection.execute(read_sql("DELETE FROM user_roles WHERE role_id=?"), (role_id,))
            pending_users = 0
            disabled_devices = 0
            for user in affected:
                if user["other_role_count"] > 0:
                    new_status = user["status"]
                else:
                    new_status = "pending"
                    pending_users += 1
                    connection.execute(
                        read_sql("UPDATE users SET status='pending',updated_at=? WHERE id=?"),
                        (now, user["id"]),
                    )
                    disabled_devices += connection.execute(
                        read_sql("UPDATE devices SET enabled=0,updated_at=? "
                        "WHERE user_id=? AND enabled=1"),
                        (now, user["id"]),
                    ).rowcount
                connection.execute(
                    read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                    (
                        new_id("audit"),
                        actor_user_id,
                        "role_deleted_user_access_updated",
                        "user",
                        user["id"],
                        json.dumps({"roleId": role_id, "status": new_status}),
                        now,
                    ),
                )

            connection.execute(read_sql("DELETE FROM roles WHERE id=?"), (role_id,))
            connection.execute(
                read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                (
                    new_id("audit"),
                    actor_user_id,
                    "delete_role",
                    "role",
                    role_id,
                    json.dumps(
                        {
                            "name": role["display_name"] or role["name"],
                            "assignedUsers": len(affected),
                            "pendingUsers": pending_users,
                            "disabledDevices": disabled_devices,
                            "removedFromBorderlineRecipients": removed_from_recipients,
                            "borderlineAlertsDisabled": not borderline_enabled
                            and bool(settings["borderline_alert_enabled"]),
                        }
                    ),
                    now,
                ),
            )
            connection.commit()
        return {
            "deleted": True,
            "roleId": role_id,
            "roleName": role["display_name"] or role["name"],
            "assignedUsers": len(affected),
            "pendingUsers": pending_users,
            "disabledDevices": disabled_devices,
        }

    def list_mobile_users(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT u.id,u.firebase_uid,u.email_cache,u.display_name,u.status,
                       r.id AS role_id,r.name AS role_name,
                       COALESCE(NULLIF(r.display_name,''),r.name) AS role_display_name,
                       r.description AS role_description,r.sensitivity_mode,
                       r.can_manage_roles,r.can_manage_users,r.can_manage_borderline_settings
                FROM users u
                LEFT JOIN user_roles ur ON ur.user_id=u.id
                LEFT JOIN roles r ON r.id=ur.role_id
                ORDER BY CASE u.status WHEN 'pending' THEN 0 ELSE 1 END,
                         COALESCE(NULLIF(u.display_name,''),u.email_cache,u.id)
                """)
            ).fetchall()
        return [dict(row) for row in rows]

    def assign_mobile_user(
        self,
        user_id: str,
        role_id: str,
        status: str,
        actor_user_id: str,
    ) -> bool:
        now = _iso()
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            user = connection.execute(read_sql("SELECT id FROM users WHERE id=?"), (user_id,)).fetchone()
            role = connection.execute(read_sql("SELECT * FROM roles WHERE id=?"), (role_id,)).fetchone()
            if not user or not role:
                connection.rollback()
                return False
            try:
                if user_id == actor_user_id:
                    raise ValueError("You cannot change your own role or access status.")
                if status not in {"pending", "active", "disabled"}:
                    raise ValueError("Invalid user status.")
                if status == "active" and role_id is None:
                    raise ValueError("An active user must have a role.")
                self._assert_role_grant_allowed(connection, actor_user_id, role)
                self._assert_not_last_active_administrator(
                    connection, user_id, status, role["name"]
                )
                previous = connection.execute(
                    read_sql("""
                    SELECT r.id FROM user_roles ur JOIN roles r ON r.id=ur.role_id
                    WHERE ur.user_id=?
                    """),
                    (user_id,),
                ).fetchone()
                connection.execute(read_sql("DELETE FROM user_roles WHERE user_id=?"), (user_id,))
                connection.execute(
                    read_sql("""
                    INSERT INTO user_roles(user_id,role_id,assigned_by_user_id,assigned_at)
                    VALUES (?,?,?,?)
                    """),
                    (user_id, role_id, actor_user_id, now),
                )
                role_setting = connection.execute(
                    read_sql('SELECT default_enabled FROM role_preferences WHERE role_id=?'),
                    (role_id,),
                ).fetchone()
                connection.execute(read_sql("UPDATE users SET borderline_enabled=?2,borderline_source='role_default',borderline_updated_by_user_id=?3,borderline_updated_at=?4 WHERE id=?1"),(user_id, int(bool(role_setting and role_setting["default_enabled"])), actor_user_id, now))
                connection.execute(
                    read_sql("UPDATE users SET status=?,updated_at=? WHERE id=?"),
                    (status, now, user_id),
                )
                devices_disabled = 0
                if status != "active":
                    devices_disabled = connection.execute(
                        read_sql("UPDATE devices SET enabled=0,updated_at=? WHERE user_id=? AND enabled=1"),
                        (now, user_id),
                    ).rowcount
                connection.execute(
                    read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                    (
                        new_id("audit"), actor_user_id, "assign_user_role", "user", user_id,
                        json.dumps({
                            "previousRoleId": previous["id"] if previous else None,
                            "roleId": role_id,
                            "status": status,
                            "devicesDisabled": devices_disabled,
                        }), now,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return True

    def deactivate_mobile_user(self, user_id: str, actor_user_id: str) -> bool:
        now = _iso()
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            user = connection.execute(
                read_sql("SELECT id,status FROM users WHERE id=?"), (user_id,)
            ).fetchone()
            if not user:
                connection.rollback()
                return False
            try:
                if user_id == actor_user_id:
                    raise ValueError("You cannot deactivate your own account.")
                self._assert_not_last_active_administrator(
                    connection, user_id, "disabled", None
                )
                role = connection.execute(
                    read_sql("SELECT role_id FROM user_roles WHERE user_id=?"), (user_id,)
                ).fetchone()
                connection.execute(
                    read_sql("UPDATE users SET status='disabled',updated_at=? WHERE id=?"),
                    (now, user_id),
                )
                devices_disabled = connection.execute(
                    read_sql("UPDATE devices SET enabled=0,updated_at=? WHERE user_id=? AND enabled=1"),
                    (now, user_id),
                ).rowcount
                if user["status"] != "disabled":
                    connection.execute(
                        read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                        (
                            new_id("audit"), actor_user_id, "deactivate_user", "user", user_id,
                            json.dumps({
                                "previousStatus": user["status"],
                                "roleId": role["role_id"] if role else None,
                                "status": "disabled",
                                "devicesDisabled": devices_disabled,
                            }), now,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return True

    def revoke_mobile_user(self, user_id: str, actor_user_id: str) -> bool:
        now = _iso()
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            if user_id == actor_user_id:
                connection.rollback()
                raise ValueError("You cannot revoke your own role.")
            user = connection.execute(
                read_sql("SELECT id,status FROM users WHERE id=?"), (user_id,)
            ).fetchone()
            if not user:
                connection.rollback()
                return False
            try:
                self._assert_not_last_active_administrator(
                    connection, user_id, "pending", None
                )
                connection.execute(read_sql("DELETE FROM user_roles WHERE user_id=?"), (user_id,))
                connection.execute(
                    read_sql("UPDATE users SET status='pending',updated_at=? WHERE id=?"), (now, user_id)
                )
                devices_disabled = connection.execute(
                    read_sql("UPDATE devices SET enabled=0,updated_at=? WHERE user_id=? AND enabled=1"),
                    (now, user_id),
                ).rowcount
                connection.execute(
                    read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                    (
                        new_id("audit"), actor_user_id, "revoke_user_role", "user", user_id,
                        json.dumps({"status": "pending", "devicesDisabled": devices_disabled}), now,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return True

    def update_mobile_profile(self, user_id: str, display_name: str) -> dict | None:
        clean_name = " ".join(display_name.split()).strip()
        if not clean_name:
            raise ValueError("Display name cannot be empty.")
        now = _iso()
        with self._lock, self._connect() as connection:
            if not connection.execute(read_sql("SELECT 1 FROM users WHERE id=?"), (user_id,)).fetchone():
                return None
            connection.execute(
                read_sql("UPDATE users SET display_name=?,updated_at=? WHERE id=?"),
                (clean_name, now, user_id),
            )
            connection.execute(
                read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                (new_id("audit"), user_id, "update_profile", "user", user_id,
                 json.dumps({"displayName": clean_name}), now),
            )
            connection.commit()
        return self.mobile_user_profile(user_id)

    def bootstrap_first_administrator(
        self, firebase_uid: str, email: str, display_name: str
    ) -> dict:
        now = _iso()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                read_sql("""
                SELECT 1 FROM users u
                JOIN user_roles ur ON ur.user_id=u.id
                JOIN roles r ON r.id=ur.role_id
                WHERE u.status='active' AND r.name IN ('school_administrator','admin')
                LIMIT 1
                """)
            ).fetchone()
            if existing:
                raise ValueError("An active School Administrator already exists.")
            user = connection.execute(
                read_sql("SELECT id FROM users WHERE firebase_uid=?"), (firebase_uid,)
            ).fetchone()
            user_id = user["id"] if user else new_id("user")
            connection.execute(
                read_sql("""
                INSERT INTO users(id,firebase_uid,email_cache,display_name,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(firebase_uid) DO UPDATE SET
                    email_cache=excluded.email_cache,
                    display_name=excluded.display_name,
                    status='active',
                    updated_at=excluded.updated_at
                """),
                (user_id, firebase_uid, email, display_name, "active", now, now),
            )
            connection.execute(read_sql("DELETE FROM user_roles WHERE user_id=?"), (user_id,))
            connection.execute(
                read_sql("""
                INSERT INTO user_roles(user_id,role_id,assigned_by_user_id,assigned_at)
                VALUES (?,'role_school_administrator',NULL,?)
                """),
                (user_id, now),
            )
            connection.execute(
                read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                (
                    new_id("audit"), user_id, "bootstrap_first_administrator", "user",
                    user_id, json.dumps({"firebaseUid": firebase_uid, "email": email}), now,
                ),
            )
            connection.commit()
        profile = self.mobile_user_profile(user_id)
        assert profile is not None
        return profile

    def record_mobile_alert_action(
        self,
        alert_id: str,
        user_id: str,
        action: str,
        note: str,
    ) -> bool:
        status_map = {
            "dismiss": "dismissed",
            "resolve": "resolved",
            "escalate": "escalated",
        }
        if action not in status_map:
            raise ValueError("Unsupported alert action.")
        now = _iso()
        with self._lock, self._connect() as connection:
            alert = connection.execute(
                read_sql('SELECT status FROM incident_state WHERE id=?'), (alert_id,)
            ).fetchone()
            if not alert:
                return False
            if alert["status"] not in {"open", "in_review"}:
                raise ValueError("This alert has already received a final response action.")
            target_status = status_map[action]
            connection.execute(
                read_sql("""
                UPDATE alerts SET resolved_at=? WHERE id=?
                """),
                (now if target_status in {"resolved", "dismissed"} else None, alert_id),
            )
            workflow_status = {
                "dismiss": "dismissed",
                "resolve": "resolved",
                "escalate": "legacy_escalated",
            }[action]
            connection.execute(
                read_sql("""
                UPDATE alerts
                SET workflow_status=?,active_phase_id=NULL,revision=revision+1,workflow_updated_at=?
                WHERE id=?
                """),
                (workflow_status, now, alert_id),
            )
            self._cancel_mobile_alert_reminder_phases(
                connection,
                alert_id,
                reason="legacy_response",
                completed_at=now,
            )
            connection.execute(
                read_sql("INSERT INTO alert_actions(id,alert_id,user_id,action,note,created_at,record_origin) VALUES (?,?,?,?,?,?,'legacy')"),
                (new_id("action"), alert_id, user_id, action, note, now),
            )
            connection.execute(
                read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                (
                    new_id("audit"), user_id, f"alert_{action}", "alert", alert_id,
                    json.dumps({"previousStatus": alert["status"], "newStatus": target_status}),
                    now,
                ),
            )
            connection.commit()
        return True

    def record_simulated_emergency_call(self, alert_id: str, user_id: str) -> bool:
        now = _iso()
        with self._lock, self._connect() as connection:
            if not connection.execute(read_sql('SELECT 1 FROM incident_state WHERE id=?'), (alert_id,)).fetchone():
                return False
            connection.execute(
                read_sql("INSERT INTO alert_actions(id,alert_id,user_id,action,note,created_at,record_origin) VALUES (?,?,?,?,?,?,'legacy')"),
                (
                    new_id("action"), alert_id, user_id, "simulated_emergency_call",
                    "Prototype simulation only. No dialler opened and no real call was made.", now,
                ),
            )
            connection.execute(
                read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                (
                    new_id("audit"), user_id, "simulated_emergency_call", "alert", alert_id,
                    json.dumps({"realCallMade": False, "diallerOpened": False}), now,
                ),
            )
            connection.commit()
        return True

    def list_alert_actions(self, alert_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT aa.id,aa.action,aa.note,aa.created_at,
                       COALESCE(NULLIF(trim(u.display_name),''),'An authorised user') AS performed_by,
                       u.email_cache AS performed_by_email
                FROM alert_actions aa
                JOIN users u ON u.id=aa.user_id
                WHERE aa.alert_id=? AND aa.record_origin='legacy'
                ORDER BY aa.created_at DESC
                """),
                (alert_id,),
            ).fetchall()
        actions = [{**dict(row), "is_legacy": True} for row in rows]
        workflow = self.get_mobile_alert_workflow(alert_id)
        if workflow:
            legacy_simulation_times = {
                item["created_at"]
                for item in actions
                if item["action"] == "simulated_emergency_call"
            }
            legacy_aliases = {
                "confirm_only": "confirm_only",
                "confirm_and_handle": "confirm_and_handle",
                "confirm_resolved": "confirm_resolved",
                "take_ownership": "take_ownership",
                "resolve": "resolve",
                "simulated_emergency_call": "simulated_emergency_call",
            }
            actions.extend(
                {
                    "id": item["id"],
                    "action": legacy_aliases.get(item["action"], item["action"]),
                    "note": item["note"],
                    "created_at": item["created_at"],
                    "performed_by": item["performed_by"],
                    "performed_by_email": item.get("performed_by_email"),
                    "broadcast_role_ids": item.get("broadcast_role_ids", []),
                    "is_legacy": False,
                }
                for item in workflow["events"]
                if not (
                    item["action"] == "simulated_emergency_call"
                    and item["created_at"] in legacy_simulation_times
                )
            )
        actions.sort(key=lambda item: (item.get("created_at") or "", item.get("id") or ""), reverse=True)
        return actions

    def set_notification_recipients(self, notification_id: str, role_ids: list[str]) -> None:
        with self._lock, self._connect() as policy_connection:
            policy_connection.execute("UPDATE notifications SET visibility_policy='restricted' WHERE id=?",(notification_id,))
            policy_connection.commit()
        if not role_ids:
            return
        with self._lock, self._connect() as connection:
            valid = {
                row["id"]
                for row in connection.execute(
                    read_sql(f"SELECT id FROM roles WHERE id IN ({','.join('?' for _ in role_ids)})"),
                    tuple(role_ids),
                ).fetchall()
            }
            connection.executemany(
                read_sql("INSERT OR IGNORE INTO notification_recipients(notification_id,role_id,user_id) VALUES (?,?,NULL)"),
                [(notification_id, role_id) for role_id in valid],
            )
            connection.commit()

    def set_notification_user_recipients(
        self, notification_id: str, user_ids: list[str]
    ) -> None:
        """Snapshot the exact users who were eligible for a notification.

        Role assignments can change after an alert is delivered. User-level
        recipients therefore preserve the original audience for later
        decision-update notifications.
        """
        unique_ids = list(dict.fromkeys(item for item in user_ids if item))
        with self._lock, self._connect() as policy_connection:
            policy_connection.execute("UPDATE notifications SET visibility_policy='restricted' WHERE id=?",(notification_id,))
            policy_connection.commit()
        if not unique_ids:
            return
        with self._lock, self._connect() as connection:
            valid = {
                row["id"]
                for row in connection.execute(
                    read_sql(f"SELECT id FROM users WHERE id IN ({','.join('?' for _ in unique_ids)})"),
                    tuple(unique_ids),
                ).fetchall()
            }
            connection.executemany(
                read_sql("INSERT OR IGNORE INTO notification_recipients(notification_id,role_id,user_id) VALUES (?,NULL,?)"),
                [(notification_id, user_id) for user_id in valid],
            )
            connection.commit()

    def schedule_alert_notification_escalation(
        self,
        alert_id: str,
        initial_notification_id: str,
        same_audience_repeat_after_seconds: int,
        all_users_escalation_after_seconds: int,
    ) -> bool:
        """Persist one timing snapshot for the unanswered-alert reminders."""
        if (
            same_audience_repeat_after_seconds < 15
            or all_users_escalation_after_seconds <= same_audience_repeat_after_seconds
        ):
            raise ValueError("Invalid alert notification escalation intervals.")
        with self._lock, self._connect() as connection:
            initial = connection.execute(
                read_sql("SELECT created_at FROM notifications WHERE id=? AND alert_id=?"),
                (initial_notification_id, alert_id),
            ).fetchone()
            if not initial:
                return False
            notified_at = datetime.fromisoformat(initial["created_at"])
            repeat_due_at = notified_at + timedelta(
                seconds=same_audience_repeat_after_seconds
            )
            all_users_due_at = notified_at + timedelta(
                seconds=all_users_escalation_after_seconds
            )
            phase_id=self._start_mobile_alert_reminder_phase(connection,
                alert_id=alert_id,phase_kind='awaiting_decision',started_at=notified_at,
                first_after_seconds=same_audience_repeat_after_seconds,
                all_after_seconds=all_users_escalation_after_seconds,
                source_notification_id=initial_notification_id)
            connection.execute("UPDATE alerts SET active_phase_id=?,revision=revision+1,workflow_updated_at=? WHERE id=?",(phase_id,_iso(),alert_id))
            connection.execute(
                read_sql("""
                INSERT OR IGNORE INTO alert_notification_audience(
                    alert_id,user_id,qualification_reasons_json,first_notification_id,
                    first_queued_at,first_sent_at
                )
                SELECT n.alert_id,nr.user_id,'["legacy"]',n.id,n.created_at,
                       CASE WHEN EXISTS (
                         SELECT 1 FROM notification_deliveries nd
                         JOIN devices d ON d.id=nd.device_id
                         WHERE nd.notification_id=n.id AND nd.status='sent'
                           AND d.user_id=nr.user_id
                       ) THEN n.created_at ELSE NULL END
                FROM notifications n
                JOIN notification_recipients nr ON nr.notification_id=n.id
                WHERE n.id=? AND nr.user_id IS NOT NULL
                """),
                (initial_notification_id,),
            )
            connection.commit()
            created = bool(phase_id)
        return created

    def list_due_mobile_alert_reminder_phases(
        self, now: datetime | None = None
    ) -> list[dict]:
        now_iso = _iso(now)
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT p.id AS phase_id,p.alert_id,p.phase_kind,p.started_at,
                       p.first_due_at,p.all_due_at,p.broadcast_role_ids_json,
                       p.initiated_by_user_id,
                       CASE
                         WHEN p.all_due_at<=:now AND NOT EXISTS (
                           SELECT 1 FROM stage_claims d
                           WHERE d.phase_id=p.id AND d.stage=2
                             AND (d.status<>'dispatching' OR d.claimed_at>:stale_before)
                         ) THEN 2
                         WHEN p.first_due_at<=:now AND NOT EXISTS (
                           SELECT 1 FROM stage_claims d
                           WHERE d.phase_id=p.id AND d.stage=1
                             AND (d.status<>'dispatching' OR d.claimed_at>:stale_before)
                         ) THEN 1
                       END AS stage
                FROM reminder_timers p
                JOIN workflow_state w ON w.alert_id=p.alert_id
                WHERE p.phase_status='active'
                  AND ((p.phase_kind='awaiting_decision' AND w.workflow_status='awaiting_confirmation')
                    OR (p.phase_kind='awaiting_handler' AND w.workflow_status='confirmed_awaiting_handler'))
                  AND (
                    (p.all_due_at<=:now AND NOT EXISTS (
                      SELECT 1 FROM stage_claims d
                      WHERE d.phase_id=p.id AND d.stage=2
                        AND (d.status<>'dispatching' OR d.claimed_at>:stale_before)
                    )) OR
                    (p.first_due_at<=:now AND NOT EXISTS (
                      SELECT 1 FROM stage_claims d
                      WHERE d.phase_id=p.id AND d.stage=1
                        AND (d.status<>'dispatching' OR d.claimed_at>:stale_before)
                    ))
                  )
                ORDER BY stage DESC,p.first_due_at,p.id
                """),
                {
                    "now": now_iso,
                    "stale_before": _iso((now or utc_now()) - timedelta(seconds=60)),
                },
            ).fetchall()
        return [dict(row) for row in rows if row["stage"] is not None]

    def list_late_mobile_alert_repeat_candidates(
        self, now: datetime | None = None
    ) -> list[dict]:
        now_iso = _iso(now)
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT p.id AS phase_id,p.alert_id,p.phase_kind,p.broadcast_role_ids_json,
                       p.initiated_by_user_id,a.user_id,a.qualification_reasons_json
                FROM reminder_timers p
                JOIN workflow_state w ON w.alert_id=p.alert_id
                JOIN stage_claims d ON d.phase_id=p.id AND d.stage=1
                JOIN alert_notification_audience a ON a.alert_id=p.alert_id
                WHERE p.phase_status='active' AND p.first_due_at<=:now AND p.all_due_at>:now
                  AND d.status IN ('sent','partial','failed','no_recipients')
                  AND a.first_sent_at>p.first_due_at
                  AND ((p.phase_kind='awaiting_decision' AND w.workflow_status='awaiting_confirmation')
                    OR (p.phase_kind='awaiting_handler' AND w.workflow_status='confirmed_awaiting_handler'))
                  AND NOT EXISTS (
                    SELECT 1 FROM user_reminders prior
                    WHERE prior.alert_id=p.alert_id AND prior.phase_id=p.id
                      AND prior.user_id=a.user_id AND prior.stage=1
                  )
                ORDER BY a.first_sent_at,p.id,a.user_id
                """),
                {"now": now_iso},
            ).fetchall()
        return [dict(row) for row in rows]

    def queue_late_mobile_alert_repeat_notification(
        self,
        phase_id: str,
        user_id: str,
        *,
        recipients: list[dict],
        title: str,
        body: str,
        channel_id: str,
        data: dict[str, str],
        event_key: str,
        now: datetime | None = None,
    ) -> dict:
        now_iso = _iso(now)
        now_value = now or utc_now()
        borderline_available = self.get_settings().vlm.borderline_review_enabled
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            phase = connection.execute(
                read_sql("""
                SELECT p.*,w.workflow_status,d.status AS first_repeat_status
                FROM reminder_timers p
                JOIN workflow_state w ON w.alert_id=p.alert_id
                JOIN stage_claims d ON d.phase_id=p.id AND d.stage=1
                WHERE p.id=?
                """),
                (phase_id,),
            ).fetchone()
            if not phase:
                connection.rollback()
                return {"status": "missing", "outboxId": None}
            expected = (
                "awaiting_confirmation"
                if phase["phase_kind"] == "awaiting_decision"
                else "confirmed_awaiting_handler"
            )
            audience = connection.execute(
                read_sql("SELECT first_sent_at,qualification_reasons_json FROM alert_notification_audience "
                "WHERE alert_id=? AND user_id=?"),
                (phase["alert_id"], user_id),
            ).fetchone()
            existing = connection.execute(
                read_sql('SELECT status FROM user_reminders WHERE alert_id=? AND phase_id=? AND user_id=? AND stage=1'),
                (phase["alert_id"], phase_id, user_id),
            ).fetchone()
            if (
                phase["phase_status"] != "active"
                or phase["workflow_status"] != expected
                or phase["first_repeat_status"] not in {"sent", "partial", "failed", "no_recipients"}
                or phase["all_due_at"] <= _iso(now_value)
                or not audience
                or audience["first_sent_at"] <= phase["first_due_at"]
                or existing
            ):
                connection.rollback()
                return {"status": "not_eligible", "outboxId": None}
            try:
                reasons = json.loads(audience["qualification_reasons_json"] or "[]")
            except (TypeError, json.JSONDecodeError):
                reasons = []
            if "ordinary_threshold" not in reasons and "legacy" not in reasons and not (
                borderline_available and "borderline_review" in reasons
            ):
                connection.rollback()
                return {"status": "feature_disabled", "outboxId": None}
            valid_device_user = connection.execute(
                read_sql("SELECT 1 FROM users u JOIN user_roles ur ON ur.user_id=u.id "
                "JOIN devices d ON d.user_id=u.id AND d.enabled=1 "
                "WHERE u.id=? AND u.status='active' LIMIT 1"),
                (user_id,),
            ).fetchone()
            recipients = [item for item in recipients if item.get("user_id") == user_id]
            if not valid_device_user or not recipients:
                connection.rollback()
                return {"status": "no_device", "outboxId": None}
            late_data = {
                **data,
                "lateAudience": "true",
                "alertId": phase["alert_id"],
                "source": "alert_reminder",
                "reminderPhaseId": phase_id,
                "reminderStage": "1",
            }
            outbox_id = self._queue_mobile_notification(
                connection,
                alert_id=phase["alert_id"],
                title=title,
                body=body,
                action="reminder_stage_1",
                source="alert_reminder",
                channel_id=channel_id,
                recipients=[user_id],
                event_key=event_key,
                extra_data=late_data,
                created_at=now_iso,
            )
            if not outbox_id:
                connection.rollback()
                return {"status": "already_queued", "outboxId": None}
            connection.execute(read_sql("INSERT INTO notification_phase_audience(phase_id,user_id,stage,status,notification_id,updated_at) SELECT ?2,?3,1,'queued',(SELECT notification_id FROM notification_outbox WHERE id=?4),?5 WHERE EXISTS(SELECT 1 FROM notification_phases WHERE id=?2 AND alert_id=?1)"),(phase["alert_id"], phase_id, user_id, outbox_id, now_iso))
            connection.commit()
        return {"status": "queued", "outboxId": outbox_id}

    def claim_mobile_alert_reminder_dispatch(
        self, phase_id: str, stage: int, now: datetime | None = None
    ) -> dict | None:
        if stage not in {1, 2}:
            raise ValueError("Reminder stage must be 1 or 2.")
        now_iso = _iso(now)
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            phase = connection.execute(
                read_sql("""
                SELECT p.*,w.workflow_status
                FROM reminder_timers p
                JOIN workflow_state w ON w.alert_id=p.alert_id
                WHERE p.id=?
                """),
                (phase_id,),
            ).fetchone()
            if not phase:
                connection.rollback()
                return None
            expected = (
                "awaiting_confirmation"
                if phase["phase_kind"] == "awaiting_decision"
                else "confirmed_awaiting_handler"
            )
            if phase["phase_status"] != "active" or phase["workflow_status"] != expected:
                self._cancel_mobile_alert_reminder_phases(
                    connection,
                    phase["alert_id"],
                    reason="workflow_changed",
                    completed_at=now_iso,
                )
                connection.commit()
                return None
            existing_dispatch = connection.execute(
                read_sql('SELECT status,claimed_at FROM stage_claims WHERE phase_id=? AND stage=?'),
                (phase_id, stage),
            ).fetchone()
            stale_before = _iso((now or utc_now()) - timedelta(seconds=60))
            if existing_dispatch:
                if (
                    existing_dispatch["status"] != "dispatching"
                    or existing_dispatch["claimed_at"] > stale_before
                ):
                    connection.rollback()
                    return None
                connection.execute(read_sql("UPDATE notification_phase_dispatches SET claimed_at=?,completed_at=NULL WHERE phase_id=? AND stage=? AND status='dispatching'"),(now_iso, phase_id, stage))
                connection.commit()
                return dict(phase)
            due_column = "first_due_at" if stage == 1 else "all_due_at"
            if phase[due_column] > now_iso:
                connection.rollback()
                return None
            if stage == 2:
                connection.execute(
                    read_sql("""
                    INSERT OR IGNORE INTO notification_phase_dispatches(
                        phase_id,stage,status,claimed_at,completed_at
                    ) VALUES (?,1,'skipped_superseded',?,?)
                    """),
                    (phase_id, now_iso, now_iso),
                )
                connection.execute(read_sql("UPDATE notification_phase_dispatches SET status='skipped_superseded',completed_at=? WHERE phase_id=? AND stage=1 AND status='dispatching'"),(now_iso, phase_id))
            connection.execute(
                read_sql("""
                INSERT INTO notification_phase_dispatches(
                    phase_id,stage,status,claimed_at
                ) VALUES (?,?,'dispatching',?)
                """),
                (phase_id, stage, now_iso),
            )
            connection.commit()
        return dict(phase)

    def queue_mobile_alert_reminder_notification(
        self,
        phase_id: str,
        stage: int,
        *,
        recipients: list[dict],
        title: str,
        body: str,
        channel_id: str,
        data: dict[str, str],
        event_key: str,
        now: datetime | None = None,
    ) -> dict:
        now_iso = _iso(now)
        user_ids = list(dict.fromkeys(
            item.get("user_id") for item in recipients if item.get("user_id")
        ))
        borderline_available = (
            self.get_settings().vlm.borderline_review_enabled if stage == 1 else False
        )
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            phase = connection.execute(
                read_sql("""
                SELECT p.alert_id,p.phase_kind,p.phase_status,p.broadcast_role_ids_json,
                       w.workflow_status
                FROM reminder_timers p
                JOIN workflow_state w ON w.alert_id=p.alert_id
                WHERE p.id=?
                """),
                (phase_id,),
            ).fetchone()
            dispatch = connection.execute(
                read_sql('SELECT status FROM stage_claims WHERE phase_id=? AND stage=?'),
                (phase_id, stage),
            ).fetchone()
            if not phase or not dispatch or dispatch["status"] != "dispatching":
                connection.rollback()
                return {"status": "skipped_stopped", "notificationId": None}
            if phase["phase_status"] != "active":
                connection.execute(read_sql("""
                    UPDATE notification_phase_dispatches
                    SET status='skipped_stopped',error_code='WorkflowChanged',completed_at=?
                    WHERE phase_id=? AND stage=? AND status='dispatching'
                    """),(now_iso, phase_id, stage))
                connection.commit()
                return {"status": "skipped_stopped", "notificationId": None}
            expected = "awaiting_confirmation" if phase["phase_kind"] == "awaiting_decision" else "confirmed_awaiting_handler"
            if phase["workflow_status"] != expected:
                self._cancel_mobile_alert_reminder_phases(
                    connection,
                    phase["alert_id"],
                    reason="workflow_changed",
                    completed_at=now_iso,
                )
                connection.execute(read_sql("""
                    UPDATE notification_phase_dispatches
                    SET status='skipped_stopped',error_code='WorkflowChanged',completed_at=?
                    WHERE phase_id=? AND stage=? AND status='dispatching'
                    """),(now_iso, phase_id, stage))
                connection.commit()
                return {"status": "skipped_stopped", "notificationId": None}
            if stage == 1:
                audience_rows = connection.execute(
                    read_sql("SELECT user_id,qualification_reasons_json FROM alert_notification_audience "
                    "WHERE alert_id=? AND first_sent_at IS NOT NULL"),
                    (phase["alert_id"],),
                ).fetchall()
                initial_users = set()
                for audience in audience_rows:
                    try:
                        reasons = json.loads(audience["qualification_reasons_json"] or "[]")
                    except (TypeError, json.JSONDecodeError):
                        reasons = []
                    if "ordinary_threshold" in reasons or "legacy" in reasons or (
                        borderline_available and "borderline_review" in reasons
                    ):
                        initial_users.add(audience["user_id"])
                broadcast_users: set[str] = set()
                if phase["phase_kind"] == "awaiting_handler":
                    role_ids = json.loads(phase["broadcast_role_ids_json"] or "[]")
                    if role_ids and user_ids:
                        placeholders = ",".join("?" for _ in user_ids)
                        role_placeholders = ",".join("?" for _ in role_ids)
                        broadcast_users = {
                            row["user_id"] for row in connection.execute(
                                read_sql(f"""
                                SELECT DISTINCT u.id AS user_id FROM users u
                                JOIN user_roles ur ON ur.user_id=u.id
                                JOIN devices d ON d.user_id=u.id AND d.enabled=1
                                WHERE u.status='active' AND u.id IN ({placeholders})
                                  AND ur.role_id IN ({role_placeholders})
                                """),
                                [*user_ids, *role_ids],
                            ).fetchall()
                        }
                allowed_users = initial_users | broadcast_users
                user_ids = [user_id for user_id in user_ids if user_id in allowed_users]
            if not user_ids:
                connection.execute(read_sql("UPDATE notification_phase_dispatches SET status='no_recipients',recipient_count=0,completed_at=? WHERE phase_id=? AND stage=?"),(now_iso, phase_id, stage))
                connection.commit()
                return {"status": "no_recipients", "notificationId": None}
            outbox_id = self._queue_mobile_notification(
                connection,
                alert_id=phase["alert_id"],
                title=title,
                body=body,
                action=f"reminder_stage_{stage}",
                source="alert_reminder",
                channel_id=channel_id,
                recipients=user_ids,
                event_key=event_key,
                extra_data=data,
                created_at=now_iso,
            )
            if outbox_id is None:
                connection.execute(read_sql("UPDATE notification_phase_dispatches SET status='no_recipients',completed_at=? WHERE phase_id=? AND stage=?"),(now_iso, phase_id, stage))
                connection.commit()
                return {"status": "no_recipients", "notificationId": None}
            outbox = connection.execute(
                read_sql('SELECT notification_id FROM queue_messages WHERE id=?'),
                (outbox_id,),
            ).fetchone()
            connection.execute(read_sql("""
                UPDATE notification_phase_dispatches
                SET status='queued',notification_id=?,recipient_count=?,recipient_count_unit='user',completed_at=?
                WHERE phase_id=? AND stage=?
                """),(outbox["notification_id"], len(user_ids), now_iso, phase_id, stage))
            connection.executemany(read_sql("INSERT OR IGNORE INTO notification_phase_audience(phase_id,user_id,stage,status,notification_id,updated_at) SELECT ?2,?3,?4,'queued',(SELECT notification_id FROM notification_outbox WHERE id=?5),?6 WHERE EXISTS(SELECT 1 FROM notification_phases WHERE id=?2 AND alert_id=?1)"),[
                    (phase["alert_id"], phase_id, user_id, stage, outbox_id, now_iso)
                    for user_id in user_ids
                ])
            connection.commit()
        return {
            "status": "queued",
            "notificationId": outbox["notification_id"],
            "outboxId": outbox_id,
            "recipientCount": len(user_ids),
        }

    def prepare_mobile_alert_reminder_delivery(
        self,
        outbox_id: str,
        phase_id: str,
        stage: int,
        *,
        now: datetime | None = None,
    ) -> set[str] | None:
        """Revalidate a reminder immediately before handing it to the FCM sender."""
        if stage not in {1, 2}:
            return None
        now_iso = _iso(now)
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            row = connection.execute(
                read_sql("""
                SELECT p.alert_id,p.phase_kind,p.phase_status,p.broadcast_role_ids_json,
                       p.first_due_at,p.all_due_at,w.workflow_status,
                       d.status AS dispatch_status,o.status AS outbox_status,
                       o.data_payload_json
                FROM reminder_timers p
                JOIN workflow_state w ON w.alert_id=p.alert_id
                JOIN stage_claims d
                  ON d.phase_id=p.id AND d.stage=?
                JOIN queue_messages o ON o.id=?
                WHERE p.id=?
                """),
                (stage, outbox_id, phase_id),
            ).fetchone()
            expected = (
                "awaiting_confirmation"
                if row and row["phase_kind"] == "awaiting_decision"
                else "confirmed_awaiting_handler"
            )
            try:
                payload = json.loads(row["data_payload_json"] or "{}") if row else {}
            except (TypeError, json.JSONDecodeError):
                payload = {}
            late_audience = payload.get("lateAudience") == "true"
            if (
                row
                and row["phase_status"] == "active"
                and row["workflow_status"] == expected
                and row["outbox_status"] == "dispatching"
                and (
                    (
                        late_audience and stage == 1
                        and row["dispatch_status"] in {"sent", "partial", "failed", "no_recipients"}
                        and row["all_due_at"] > now_iso
                    )
                    or (
                        not late_audience
                        and row["dispatch_status"] in {"queued", "dispatching"}
                    )
                )
            ):
                if not late_audience:
                    connection.execute(read_sql("""
                        UPDATE notification_phase_dispatches
                        SET status='dispatching',claimed_at=?,completed_at=NULL
                        WHERE phase_id=? AND stage=? AND status='queued'
                        """),(now_iso, phase_id, stage))
                recipient_ids = {
                    row["user_id"] for row in connection.execute(
                        read_sql("SELECT user_id FROM user_reminders WHERE outbox_id=? AND status='queued'"),
                        (outbox_id,),
                    ).fetchall()
                }
                if stage == 1 and recipient_ids:
                    settings_row = connection.execute(
                        read_sql("""
                        SELECT v.borderline_review_enabled
                        FROM config_versions c JOIN vlm_policy v ON v.config_version_id=c.id
                        ORDER BY c.version_number DESC LIMIT 1
                        """)
                    ).fetchone()
                    review_enabled = bool(settings_row and settings_row["borderline_review_enabled"])
                    placeholders = ",".join("?" for _ in recipient_ids)
                    audience_rows = connection.execute(
                        read_sql(f"""
                        SELECT user_id,qualification_reasons_json,first_sent_at
                        FROM alert_notification_audience
                        WHERE alert_id=? AND first_sent_at IS NOT NULL
                          AND user_id IN ({placeholders})
                        """),
                        [row["alert_id"], *recipient_ids],
                    ).fetchall()
                    eligible_initial = set()
                    for audience in audience_rows:
                        try:
                            reasons = json.loads(audience["qualification_reasons_json"] or "[]")
                        except (TypeError, json.JSONDecodeError):
                            reasons = []
                        if "ordinary_threshold" in reasons or "legacy" in reasons or (
                            review_enabled and "borderline_review" in reasons
                        ):
                            if not late_audience or audience["first_sent_at"] > row["first_due_at"]:
                                eligible_initial.add(audience["user_id"])
                    if row["phase_kind"] == "awaiting_handler":
                        role_ids = json.loads(row["broadcast_role_ids_json"] or "[]")
                        if role_ids:
                            user_placeholders = ",".join("?" for _ in recipient_ids)
                            role_placeholders = ",".join("?" for _ in role_ids)
                            eligible_initial.update(
                                user["user_id"] for user in connection.execute(
                                    read_sql(f"""
                                    SELECT DISTINCT u.id AS user_id FROM users u
                                    JOIN user_roles ur ON ur.user_id=u.id
                                    WHERE u.status='active' AND u.id IN ({user_placeholders})
                                      AND ur.role_id IN ({role_placeholders})
                                    """),
                                    [*recipient_ids, *role_ids],
                                ).fetchall()
                            )
                    recipient_ids.intersection_update(eligible_initial)
                skipped_ids = {
                    row["user_id"] for row in connection.execute(
                        read_sql("SELECT user_id FROM user_reminders WHERE outbox_id=? AND status='queued'"),
                        (outbox_id,),
                    ).fetchall()
                } - recipient_ids
                if skipped_ids:
                    connection.executemany(read_sql("UPDATE notification_phase_audience SET status='skipped',updated_at=? WHERE notification_id=(SELECT notification_id FROM notification_outbox WHERE id=?) AND user_id=? AND status='queued'"),[(now_iso, outbox_id, user_id) for user_id in skipped_ids])
                if late_audience and not recipient_ids:
                    connection.execute(read_sql("UPDATE notification_phase_audience SET status='skipped',updated_at=? WHERE notification_id=(SELECT notification_id FROM notification_outbox WHERE id=?) AND status='queued'"),(now_iso, outbox_id))
                connection.commit()
                return recipient_ids

            if row:
                if row["phase_status"] == "active" and row["workflow_status"] != expected:
                    self._cancel_mobile_alert_reminder_phases(
                        connection,
                        row["alert_id"],
                        reason="workflow_changed",
                        completed_at=now_iso,
                    )
                if row["dispatch_status"] in {"queued", "dispatching"}:
                    connection.execute(read_sql("""
                        UPDATE notification_phase_dispatches
                        SET status='skipped_stopped',error_code='WorkflowChanged',completed_at=?
                        WHERE phase_id=? AND stage=? AND status IN ('queued','dispatching')
                        """),(now_iso, phase_id, stage))
                connection.execute(
                    read_sql("""
                    UPDATE notification_outbox
                    SET status='no_recipients',last_error_code='WorkflowChanged',
                        claimed_at=NULL,next_attempt_at=?,updated_at=?
                    WHERE id=? AND status IN ('pending','dispatching')
                    """),
                    (now_iso, now_iso, outbox_id),
                )
                connection.execute(read_sql("UPDATE notification_phase_audience SET status='skipped',updated_at=? WHERE notification_id=(SELECT notification_id FROM notification_outbox WHERE id=?) AND status='queued'"),(now_iso, outbox_id))
            connection.commit()
        return None

    def list_due_alert_notification_escalations(self, now=None):
        return [dict(alert_id=p['alert_id'],stage=p['stage']) for p in self.list_due_mobile_alert_reminder_phases(now)]

    def claim_alert_notification_escalation(self, alert_id, stage, now=None):
        with self._connect() as connection:
            row=connection.execute("SELECT active_phase_id FROM alerts WHERE id=?",(alert_id,)).fetchone()
        return bool(row and row['active_phase_id'] and self.claim_mobile_alert_reminder_dispatch(row['active_phase_id'],stage,now))

    def complete_alert_notification_escalation(self, alert_id, stage, *, status, notification_id=None,
        recipient_count=0,sent_count=0,failed_count=0,error_code=None,completed_at=None):
        with self._lock,self._connect() as connection:
            phase=connection.execute("SELECT id FROM notification_phases WHERE alert_id=? ORDER BY started_at DESC,id LIMIT 1",(alert_id,)).fetchone()
            if phase is None:return
            connection.execute("UPDATE notification_phase_dispatches SET status=?,notification_id=?,recipient_count=?,recipient_count_unit='device',sent_count=?,failed_count=?,error_code=?,completed_at=? WHERE phase_id=? AND stage=?",
                (status,notification_id,recipient_count,sent_count,failed_count,error_code,_iso(completed_at),phase['id'],stage))
            connection.commit()

    def list_original_alert_recipient_user_ids(self, alert_id: str) -> list[str]:
        """Return the user snapshot from the original alert notification only."""
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT DISTINCT nr.user_id,n.data_payload_json
                FROM notifications n
                JOIN notification_recipients nr ON nr.notification_id=n.id
                WHERE n.alert_id=? AND nr.user_id IS NOT NULL
                """),
                (alert_id,),
            ).fetchall()
        recipients: list[str] = []
        for row in rows:
            try:
                payload = json.loads(row["data_payload_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            if payload.get("source") == "alert_created" and row["user_id"]:
                recipients.append(str(row["user_id"]))
        return list(dict.fromkeys(recipients))

    def list_all_user_escalated_alert_ids(self, user_id: str) -> set[str]:
        """Return this user's stage-two notification audience history, not access grants."""
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT n.alert_id,n.data_payload_json
                FROM notifications n
                JOIN notification_recipients nr ON nr.notification_id=n.id
                WHERE nr.user_id=? AND n.alert_id IS NOT NULL
                """),
                (user_id,),
            ).fetchall()
        alert_ids: set[str] = set()
        for row in rows:
            try:
                payload = json.loads(row["data_payload_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if (
                payload.get("source") == "alert_reminder"
                and payload.get("notificationStage") == "all_users_escalation"
            ):
                alert_ids.add(str(row["alert_id"]))
        return alert_ids

    def list_enabled_devices_for_users(
        self, user_ids: list[str], *, exclude_user_id: str | None = None
    ) -> list[dict]:
        """List enabled devices for a fixed recipient snapshot."""
        unique_ids = list(
            dict.fromkeys(
                item for item in user_ids if item and item != exclude_user_id
            )
        )
        if not unique_ids:
            return []
        placeholders = ",".join("?" for _ in unique_ids)
        with self._connect() as connection:
            rows = connection.execute(
                read_sql(f"""
                SELECT d.id,d.user_id,d.fcm_registration_value
                FROM devices d
                JOIN users u ON u.id=d.user_id AND u.status='active'
                WHERE d.enabled=1 AND d.user_id IN ({placeholders})
                """),
                tuple(unique_ids),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_enabled_devices_for_roles(self, role_ids: list[str]) -> list[dict]:
        if not role_ids:
            return []
        placeholders = ",".join("?" for _ in role_ids)
        with self._connect() as connection:
            rows = connection.execute(
                read_sql(f"""
                SELECT DISTINCT d.id,d.user_id,d.fcm_registration_value
                FROM devices d
                JOIN users u ON u.id=d.user_id AND u.status='active'
                JOIN user_roles ur ON ur.user_id=u.id
                WHERE d.enabled=1 AND ur.role_id IN ({placeholders})
                """),
                tuple(role_ids),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_enabled_devices_for_alert(self, alert: Alert) -> list[dict]:
        settings = self.get_settings()
        with self._connect() as connection:
            detections = connection.execute(
                read_sql("""
                SELECT qualifications_json,weapon_class_scores_json
                FROM detection_events WHERE alert_id=? ORDER BY detected_at,rowid
                """),
                (alert.id,),
            ).fetchall()
            rows = connection.execute(
                read_sql("""
                SELECT DISTINCT d.id,d.user_id,d.fcm_registration_value,r.id AS role_id,
                                r.sensitivity_mode,COALESCE(bus.enabled,0) AS borderline_enabled
                FROM devices d
                JOIN users u ON u.id=d.user_id AND u.status='active'
                JOIN user_roles ur ON ur.user_id=u.id
                JOIN roles r ON r.id=ur.role_id
                LEFT JOIN personal_preferences bus ON bus.user_id=u.id
                WHERE d.enabled=1
                """)
            ).fetchall()
            borderline_notification_active = bool(
                connection.execute(
                    read_sql("""
                    SELECT 1 FROM review_tasks
                    WHERE alert_id=? AND status='positive'
                      AND supplemental_notification_status IN ('eligible','handled')
                    LIMIT 1
                    """),
                    (alert.id,),
                ).fetchone()
            )
        qualified_modes: set[str] = set()
        for detection in detections:
            try:
                qualification = json.loads(detection["qualifications_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                qualification = {}
            for kind, values in qualification.items():
                if kind == "violence":
                    qualified_modes.update(str(value) for value in values)
                elif kind == "weapon" and isinstance(values, dict):
                    qualified_modes.update(
                        mode for mode, classes in values.items() if classes
                    )
        borderline_available = settings.vlm.borderline_review_enabled
        selected: dict[str, dict] = {}
        for row in rows:
            reasons = []
            if row["sensitivity_mode"] in qualified_modes:
                reasons.append("ordinary_threshold")
            if (
                borderline_available
                and alert.is_borderline_promoted
                and borderline_notification_active
                and bool(row["borderline_enabled"])
            ):
                reasons.append("borderline_review")
            if not reasons:
                continue
            item = selected.setdefault(
                row["id"],
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "fcm_registration_value": row["fcm_registration_value"],
                    "recipient_reasons": set(),
                },
            )
            item["recipient_reasons"].update(reasons)
        return [
            {**item, "recipient_reasons": sorted(item["recipient_reasons"])}
            for item in selected.values()
        ]

    def list_borderline_role_settings(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT r.id,r.name,COALESCE(NULLIF(r.display_name,''),r.name) AS display_name,
                       r.sensitivity_mode,brs.default_enabled,
                       u.id AS user_id,u.display_name AS user_display_name,
                       u.email_cache,u.status,COALESCE(bus.enabled,brs.default_enabled) AS enabled,
                       COALESCE(bus.setting_source,'role_default') AS setting_source
                FROM roles r
                LEFT JOIN role_preferences brs ON brs.role_id=r.id
                LEFT JOIN user_roles ur ON ur.role_id=r.id
                LEFT JOIN users u ON u.id=ur.user_id
                LEFT JOIN personal_preferences bus ON bus.user_id=u.id
                ORDER BY r.is_system DESC,display_name,
                         COALESCE(NULLIF(u.display_name,''),u.email_cache,u.id)
                """)
            ).fetchall()
        grouped: dict[str, dict] = {}
        for row in rows:
            role = grouped.setdefault(
                row["id"],
                {
                    "id": row["id"], "name": row["name"],
                    "displayName": row["display_name"],
                    "sensitivityMode": row["sensitivity_mode"],
                    "defaultEnabled": bool(row["default_enabled"]),
                    "users": [],
                },
            )
            if row["user_id"]:
                role["users"].append({
                    "id": row["user_id"],
                    "displayName": row["user_display_name"] or row["email_cache"] or row["user_id"],
                    "status": row["status"],
                    "enabled": bool(row["enabled"]),
                    "settingSource": row["setting_source"],
                })
        for role in grouped.values():
            enabled = sum(user["enabled"] for user in role["users"])
            role["enabledUserCount"] = enabled
            role["userCount"] = len(role["users"])
            role["selectionState"] = (
                "off" if enabled == 0 else "on" if enabled == len(role["users"]) else "mixed"
            )
        return list(grouped.values())

    def set_borderline_role_enabled(
        self, role_id: str, enabled: bool, actor_user_id: str,
        expected_revision: int | None = None,
    ) -> dict | None:
        now = _iso()
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            revision = connection.execute(
                read_sql("SELECT revision FROM preference_version WHERE id='global'")
            ).fetchone()["revision"]
            if expected_revision is not None and revision != expected_revision:
                connection.rollback()
                raise ValueError("revision_conflict")
            role = connection.execute(read_sql("SELECT id FROM roles WHERE id=?"), (role_id,)).fetchone()
            if role is None:
                connection.rollback()
                return None
            before = connection.execute(
                read_sql('SELECT u.id,COALESCE(bus.enabled,brs.default_enabled,0) AS enabled FROM user_roles ur JOIN users u ON u.id=ur.user_id LEFT JOIN personal_preferences bus ON bus.user_id=u.id LEFT JOIN role_preferences brs ON brs.role_id=ur.role_id WHERE ur.role_id=?'), (role_id,),
            ).fetchall()
            connection.execute(read_sql('UPDATE roles SET borderline_default_enabled=?2,borderline_updated_by_user_id=?3,borderline_updated_at=?4 WHERE id=?1'),(role_id, int(enabled), actor_user_id, now))
            connection.execute(read_sql("UPDATE users SET borderline_enabled=?1,borderline_source='role_bulk',borderline_updated_by_user_id=?2,borderline_updated_at=?3 WHERE id IN (SELECT user_id FROM user_roles WHERE role_id=?4)"),(int(enabled), actor_user_id, now, role_id))
            affected = connection.execute(
                read_sql("SELECT COUNT(*) AS count FROM user_roles WHERE role_id=?"), (role_id,)
            ).fetchone()["count"]
            # The batch operation intentionally overwrites individual settings.
            connection.execute(
                read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                (new_id("audit"), actor_user_id, "borderline_role_bulk_update", "role",
                 role_id, json.dumps({
                     "before": {row["id"]: bool(row["enabled"]) for row in before},
                     "after": {row["id"]: enabled for row in before},
                     "affectedUsers": affected,
                 }), now),
            )
            connection.execute(read_sql("UPDATE mobile_settings SET borderline_revision=borderline_revision+1 WHERE id='global'"))
            connection.commit()
        return next((item for item in self.list_borderline_role_settings() if item["id"] == role_id), None)

    def set_borderline_user_enabled(
        self, user_id: str, enabled: bool, actor_user_id: str,
        expected_revision: int | None = None,
    ) -> dict | None:
        now = _iso()
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            revision = connection.execute(
                read_sql("SELECT revision FROM preference_version WHERE id='global'")
            ).fetchone()["revision"]
            if expected_revision is not None and revision != expected_revision:
                connection.rollback()
                raise ValueError("revision_conflict")
            user = connection.execute(
                read_sql("SELECT id FROM users WHERE id=? AND EXISTS "
                "(SELECT 1 FROM user_roles WHERE user_id=users.id)"), (user_id,)
            ).fetchone()
            if user is None:
                connection.rollback()
                return None
            before_row = connection.execute(
                read_sql('SELECT enabled FROM personal_preferences WHERE user_id=?'), (user_id,)
            ).fetchone()
            before = bool(before_row["enabled"]) if before_row else False
            connection.execute(read_sql("UPDATE users SET borderline_enabled=?2,borderline_source='individual',borderline_updated_by_user_id=?3,borderline_updated_at=?4 WHERE id=?1"),(user_id, int(enabled), actor_user_id, now))
            connection.execute(
                read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                (new_id("audit"), actor_user_id, "borderline_user_update", "user",
                 user_id, json.dumps({"before": before, "after": enabled}), now),
            )
            connection.execute(read_sql("UPDATE mobile_settings SET borderline_revision=borderline_revision+1 WHERE id='global'"))
            connection.commit()
        return {"userId": user_id, "enabled": enabled}

    def set_borderline_roles_enabled(
        self,
        recipient_role_ids: list[str],
        actor_user_id: str,
        expected_revision: int | None = None,
    ) -> dict:
        """Atomically adapt the legacy role-list update to role and user settings."""
        selected = sorted(set(recipient_role_ids))
        now = _iso()
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            revision = connection.execute(
                read_sql("SELECT revision FROM preference_version WHERE id='global'")
            ).fetchone()["revision"]
            if expected_revision is not None and revision != expected_revision:
                connection.rollback()
                raise ValueError("revision_conflict")
            role_ids = {
                row["id"] for row in connection.execute(read_sql("SELECT id FROM roles")).fetchall()
            }
            if not set(selected).issubset(role_ids):
                connection.rollback()
                raise ValueError("invalid_role")
            before = {
                row["id"]: bool(row["enabled"])
                for row in connection.execute(
                    read_sql('SELECT user_id AS id,enabled FROM personal_preferences')
                ).fetchall()
            }
            connection.executemany(read_sql('UPDATE roles SET borderline_default_enabled=?2,borderline_updated_by_user_id=?3,borderline_updated_at=?4 WHERE id=?1'),[(role_id, int(role_id in selected), actor_user_id, now) for role_id in role_ids])
            users = connection.execute(
                read_sql("""
                SELECT DISTINCT u.id,
                       EXISTS(
                           SELECT 1 FROM user_roles ur
                           WHERE ur.user_id=u.id AND ur.role_id IN (
                               SELECT value FROM json_each(?)
                           )
                       ) AS enabled
                FROM users u JOIN user_roles member ON member.user_id=u.id
                """),
                (json.dumps(selected),),
            ).fetchall()
            connection.executemany(read_sql("UPDATE users SET borderline_enabled=?2,borderline_source='role_bulk',borderline_updated_by_user_id=?3,borderline_updated_at=?4 WHERE id=?1"),[(row["id"], int(row["enabled"]), actor_user_id, now) for row in users])
            after = {row["id"]: bool(row["enabled"]) for row in users}
            _write_mobile_preferences(connection,*(int(bool(selected)), json.dumps(selected), actor_user_id, now))
            connection.execute(
                read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                (
                    new_id("audit"), actor_user_id, "borderline_legacy_bulk_update",
                    "role_set", "global",
                    json.dumps({
                        "before": before,
                        "after": after,
                        "recipientRoleIds": selected,
                        "affectedUsers": len(users),
                    }),
                    now,
                ),
            )
            connection.execute(read_sql("UPDATE mobile_settings SET borderline_revision=borderline_revision+1 WHERE id='global'"))
            connection.commit()
        return {
            "enabled": bool(selected),
            "recipientRoleIds": selected,
            "revision": self.get_borderline_settings_revision(),
        }

    def get_borderline_settings_revision(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                read_sql("SELECT revision FROM preference_version WHERE id='global'")
            ).fetchone()
        return int(row["revision"]) if row else 1

    def has_individual_borderline_settings(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                read_sql("SELECT 1 FROM personal_preferences WHERE setting_source='individual' LIMIT 1")
            ).fetchone()
        return row is not None

    def borderline_user_enabled(self, user_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                read_sql('SELECT enabled FROM personal_preferences WHERE user_id=?'),
                (user_id,),
            ).fetchone()
        return bool(row["enabled"]) if row else False

    @staticmethod
    def _review_obsolete_reason(connection, alert_id):
        incident = connection.execute(
            'SELECT is_borderline_promoted,workflow_status FROM alerts WHERE id=?', (alert_id,)
        ).fetchone()
        if incident is None or incident['workflow_status'] in {'dismissed', 'resolved'}:
            return 'alert_closed'
        if incident['is_borderline_promoted']:
            return 'alert_already_confirmed'
        rows = connection.execute(read_sql(
            'SELECT qualifications_json FROM detection_events WHERE alert_id=?'), (alert_id,)).fetchall()
        if any('certain' in json.loads(row['qualifications_json'] or '{}').get('violence', []) for row in rows):
            return 'certain_threshold_reached'
        return None

    def borderline_review_needed(self, alert_id):
        with self._connect() as connection:
            return self._review_obsolete_reason(connection, alert_id) is None

    def cancel_obsolete_borderline_reviews(self, alert_id):
        with self._lock, self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            reason = self._review_obsolete_reason(connection, alert_id)
            if reason is None:
                connection.commit()
                return []
            paths = [row['context_path'] for row in connection.execute(read_sql(
                "SELECT context_path FROM review_tasks WHERE alert_id=? AND status='queued'"), (alert_id,))]
            connection.execute("UPDATE borderline_reviews SET status='cancelled',error_code=?,completed_at=? "
                               "WHERE alert_id=? AND status='queued' AND record_origin='task'", (reason, _iso(), alert_id))
            if paths:
                self._refresh_borderline_alert_status(connection, alert_id)
            connection.commit()
            return paths

    def create_borderline_review_task(
        self,
        *,
        alert_id: str,
        camera_id: str,
        detection_event_key: str,
        detector_confidence: float,
        source_start_seconds: float,
        source_end_seconds: float,
        context_path: str,
        model_version: str,
        prompt_version: str,
        config_version: int,
        vlm_settings: dict,
    ) -> str | None:
        task_id = new_id("reviewtask")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            incident = connection.execute(
                "SELECT is_borderline_promoted,workflow_status FROM alerts WHERE id=?", (alert_id,)
            ).fetchone()
            if self._review_obsolete_reason(connection, alert_id) is not None:
                connection.commit()
                return None
            connection.execute(read_sql("""
                INSERT OR IGNORE INTO borderline_reviews(
                    id,alert_id,camera_id,detection_event_id,detector_confidence,
                    source_start_seconds,source_end_seconds,context_path,status,
                    model_version_id,prompt_version_id,config_version_id,vlm_settings_json,created_at,record_origin,attempt_count
                ) VALUES (?,?,?,?,?,?,?,?,'queued',?,?,?,?,?,'task',0)
                """),(
                    task_id,alert_id,camera_id,detection_event_key,detector_confidence,
                    source_start_seconds,source_end_seconds,context_path,self._ensure_model(connection,"borderline_binary",model_version),
                    self._ensure_prompt(connection,"borderline_binary",prompt_version),connection.execute("SELECT id FROM config_versions WHERE version_number=?",(config_version,)).fetchone()["id"],json.dumps(vlm_settings,separators=(",",":")),_iso(),
                ))
            row = connection.execute(
                read_sql('SELECT id FROM review_tasks WHERE detection_event_key=?'),
                (detection_event_key,),
            ).fetchone()
            connection.execute(
                read_sql("UPDATE alerts SET borderline_review_status='queued' WHERE id=? "
                "AND borderline_review_status NOT IN ('positive','legacy_unknown')"),
                (alert_id,),
            )
            connection.commit()
        return row["id"]

    def list_queued_borderline_review_tasks(self) -> list[dict]:
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            connection.execute(
                read_sql("""
                UPDATE borderline_reviews
                SET status='queued',started_at=NULL,error_code=NULL
                WHERE status='running' AND attempt_count<3
                """)
            )
            exhausted = connection.execute(
                read_sql("""
                SELECT DISTINCT alert_id FROM review_tasks
                WHERE status='running' AND attempt_count>=3
                """)
            ).fetchall()
            connection.execute(
                read_sql("""
                UPDATE borderline_reviews
                SET status='failed',error_code='worker_interrupted_after_final_attempt',
                    completed_at=?
                WHERE status='running' AND attempt_count>=3
                """),
                (_iso(),),
            )
            for item in exhausted:
                self._refresh_borderline_alert_status(connection, item["alert_id"])
            rows = connection.execute(
                read_sql("SELECT * FROM review_tasks WHERE status='queued' ORDER BY created_at,id")
            ).fetchall()
            connection.commit()
        return [dict(row) for row in rows]

    @staticmethod
    def _refresh_borderline_alert_status(
        connection: sqlite3.Connection, alert_id: str
    ) -> None:
        states = {
            row["status"]
            for row in connection.execute(
                read_sql('SELECT status FROM review_tasks WHERE alert_id=?'),
                (alert_id,),
            ).fetchall()
        }
        alert_status = (
            "positive" if "positive" in states else
            "queued" if "queued" in states else
            "running" if "running" in states else
            "failed" if "failed" in states else
            "cancelled" if states == {"cancelled"} else "negative"
        )
        connection.execute(
            read_sql("UPDATE alerts SET borderline_review_status=?,is_borderline_promoted=? WHERE id=?"),
            (alert_status,int(alert_status == "positive"),alert_id),
        )

    def get_borderline_review_task(self, task_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(read_sql("SELECT * FROM review_tasks WHERE id=?"), (task_id,)).fetchone()
        return dict(row) if row else None

    def claim_borderline_review_task(self, task_id: str) -> dict | None:
        now = _iso()
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            row = connection.execute(
                read_sql("SELECT * FROM review_tasks WHERE id=? AND status='queued'"),
                (task_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            incident = connection.execute(
                "SELECT is_borderline_promoted,workflow_status FROM alerts WHERE id=?", (row["alert_id"],)
            ).fetchone()
            reason = self._review_obsolete_reason(connection, row['alert_id'])
            if reason is not None:
                connection.execute(
                    "UPDATE borderline_reviews SET status='cancelled',error_code=?,completed_at=? WHERE id=?",
                    (reason, now, task_id),
                )
                self._refresh_borderline_alert_status(connection, row['alert_id'])
                connection.commit()
                return None
            connection.execute(
                read_sql("UPDATE borderline_reviews SET status='running',attempt_count=attempt_count+1,started_at=? WHERE id=?"),
                (now, task_id),
            )
            connection.execute(
                read_sql("UPDATE alerts SET borderline_review_status='running' WHERE id=? "
                "AND borderline_review_status NOT IN ('positive','legacy_unknown')"),
                (row["alert_id"],),
            )
            connection.commit()
        return {**dict(row), "attempt_count": int(row["attempt_count"]) + 1}

    def finish_borderline_review_task(
        self,
        task_id: str,
        *,
        status: str,
        raise_alert: bool | None = None,
        uncertainty: str | None = None,
        brief_reason: str | None = None,
        error_code: str | None = None,
    ) -> dict | None:
        if status not in {"positive", "negative", "failed", "cancelled", "queued"}:
            raise ValueError("Invalid borderline review task status.")
        now = _iso()
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            task = connection.execute(
                read_sql('SELECT * FROM review_tasks WHERE id=?'), (task_id,)
            ).fetchone()
            if task is None:
                connection.rollback()
                return None
            master_row = connection.execute(
                read_sql("""
                SELECT q.borderline_review_enabled
                FROM vlm_policy q
                JOIN config_versions cv ON cv.id=q.config_version_id
                ORDER BY cv.version_number DESC LIMIT 1
                """)
            ).fetchone()
            obsolete_reason = self._review_obsolete_reason(connection, task['alert_id'])
            supplemental_status = (
                'not_applicable' if obsolete_reason is not None else
                "eligible"
                if status == "positive" and master_row and master_row["borderline_review_enabled"]
                else "suppressed_disabled"
                if status == "positive"
                else "not_applicable"
            )
            connection.execute(
                read_sql("""
                UPDATE borderline_reviews SET status=?,raise_alert=?,uncertainty=?,
                    brief_reason=?,error_code=?,completed_at=?,
                    supplemental_notification_status=? WHERE id=?
                """),
                (
                    status,int(raise_alert) if raise_alert is not None else None,
                    uncertainty,brief_reason,error_code,
                    None if status == "queued" else now,supplemental_status,task_id,
                ),
            )
            summaries = connection.execute(
                read_sql('SELECT status FROM review_tasks WHERE alert_id=?'),
                (task["alert_id"],),
            ).fetchall()
            states = {row["status"] for row in summaries}
            alert_status = (
                "positive" if "positive" in states else
                "queued" if "queued" in states else
                "running" if "running" in states else
                "failed" if "failed" in states else
                "cancelled" if states == {"cancelled"} else "negative"
            )
            connection.execute(
                read_sql("UPDATE alerts SET borderline_review_status=?,is_borderline_promoted=? WHERE id=?"),
                (alert_status,int(alert_status == "positive"),task["alert_id"]),
            )
            cancelled_contexts = []
            if status == "positive":
                cancelled_contexts = [item["context_path"] for item in connection.execute(
                    read_sql("SELECT context_path FROM review_tasks WHERE alert_id=? AND status='queued'"),
                    (task["alert_id"],),
                ).fetchall()]
                connection.execute(
                    "UPDATE borderline_reviews SET status='cancelled',error_code='alert_already_confirmed',completed_at=? "
                    "WHERE alert_id=? AND status='queued' AND record_origin='task'", (now, task["alert_id"]),
                )
            connection.commit()
        return {**dict(task), "status": status, "cancelled_contexts": cancelled_contexts,
                'review_redundant': obsolete_reason is not None}

    def list_pending_borderline_notification_tasks(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT id,alert_id FROM review_tasks
                WHERE status='positive' AND supplemental_notification_status='eligible'
                ORDER BY completed_at,id
                """)
            ).fetchall()
        return [dict(row) for row in rows]

    def list_interrupted_final_borderline_review_contexts(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT context_path FROM review_tasks
                WHERE status='failed' AND error_code='worker_interrupted_after_final_attempt'
                """)
            ).fetchall()
        return [row["context_path"] for row in rows]

    def list_borderline_review_tasks(self, alert_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT id,status,detector_confidence,source_start_seconds,source_end_seconds,
                       raise_alert,uncertainty,brief_reason,error_code,model_version,
                       prompt_version,created_at,completed_at
                FROM review_tasks WHERE alert_id=? ORDER BY created_at,id
                """),
                (alert_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_borderline_review_summaries(self) -> dict[str, dict]:
        """Aggregate review outcomes for the Web alert list without loading clips."""
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT alert_id,COUNT(*) AS total,
                       SUM(status='positive') AS positive,
                       SUM(status='negative') AS negative,
                       SUM(status='failed') AS failed,
                       SUM(status='cancelled') AS cancelled,
                       SUM(status IN ('queued','running')) AS pending,
                       (SELECT status FROM review_tasks latest
                        WHERE latest.alert_id=tasks.alert_id
                        ORDER BY latest.created_at DESC,latest.rowid DESC LIMIT 1) AS latest_status,
                       (SELECT brief_reason FROM review_tasks latest
                        WHERE latest.alert_id=tasks.alert_id
                        ORDER BY latest.created_at DESC,latest.rowid DESC LIMIT 1) AS latest_reason,
                       (SELECT uncertainty FROM review_tasks latest
                        WHERE latest.alert_id=tasks.alert_id
                        ORDER BY latest.created_at DESC,latest.rowid DESC LIMIT 1) AS latest_uncertainty,
                       (SELECT error_code FROM review_tasks latest
                        WHERE latest.alert_id=tasks.alert_id
                        ORDER BY latest.created_at DESC,latest.rowid DESC LIMIT 1) AS latest_error_code
                FROM review_tasks tasks GROUP BY alert_id
                """)
            ).fetchall()
        return {
            row["alert_id"]: {
                key: (int(row[key] or 0) if key in {
                    "total", "positive", "negative", "failed", "cancelled", "pending"
                } else row[key])
                for key in (
                    "total", "positive", "negative", "failed", "cancelled", "pending",
                    "latest_status", "latest_reason", "latest_uncertainty", "latest_error_code",
                )
            }
            for row in rows
        }

    def list_alert_detection_events(self, alert_id: str) -> list[dict]:
        """Return auditable detector decisions without exposing media or credentials."""
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT d.id,d.confidence,d.decision_source,d.source_start_seconds,
                       d.source_end_seconds,d.detected_at,d.qualifications_json,
                       d.weapon_class_scores_json,cv.version_number AS config_version,
                       mv.version AS model_version
                FROM detection_events d
                JOIN config_versions cv ON cv.id=d.config_version_id
                JOIN model_versions mv ON mv.id=d.model_version_id
                WHERE d.alert_id=? ORDER BY d.rowid
                """),
                (alert_id,),
            ).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            for column, output in (
                ("qualifications_json", "qualifications"),
                ("weapon_class_scores_json", "weapon_class_scores"),
            ):
                try:
                    value[output] = json.loads(value.pop(column) or "{}")
                except (TypeError, json.JSONDecodeError):
                    value[output] = {}
            values.append(value)
        return values

    def get_alert_notification_audience(
        self, alert_id: str, user_id: str
    ) -> dict | None:
        """Return only the requesting account's persisted first-notice record."""
        with self._connect() as connection:
            row = connection.execute(
                read_sql("""
                SELECT a.qualification_reasons_json,a.first_notification_id,
                       a.first_queued_at,a.first_sent_at,o.status AS outbox_status
                FROM alert_notification_audience a
                LEFT JOIN queue_messages o
                  ON o.notification_id=a.first_notification_id
                WHERE a.alert_id=? AND a.user_id=?
                """),
                (alert_id, user_id),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        try:
            value["qualification_reasons"] = json.loads(
                value.pop("qualification_reasons_json") or "[]"
            )
        except (TypeError, json.JSONDecodeError):
            value["qualification_reasons"] = []
        return value

    def cancel_queued_borderline_review_tasks(
        self, *, reason: str = "global_disabled"
    ) -> list[str]:
        """Cancel pending reviews atomically and return their private context paths."""
        now = _iso()
        with self._lock, self._connect() as connection:
            connection.execute(read_sql("BEGIN IMMEDIATE"))
            rows = connection.execute(
                read_sql("SELECT id,alert_id,context_path FROM review_tasks WHERE status='queued'")
            ).fetchall()
            if not rows:
                connection.commit()
                return []
            task_ids = [row["id"] for row in rows]
            alert_ids = sorted({row["alert_id"] for row in rows})
            placeholders = ",".join("?" for _ in task_ids)
            connection.execute(
                read_sql(f"""UPDATE borderline_reviews
                    SET status='cancelled',error_code=?,completed_at=?
                    WHERE id IN ({placeholders}) AND status='queued'"""),
                [reason, now, *task_ids],
            )
            for alert_id in alert_ids:
                states = {
                    row["status"] for row in connection.execute(
                        read_sql('SELECT status FROM review_tasks WHERE alert_id=?'),
                        (alert_id,),
                    ).fetchall()
                }
                status = (
                    "positive" if "positive" in states else
                    "queued" if "queued" in states else
                    "running" if "running" in states else
                    "failed" if "failed" in states else
                    "cancelled" if states == {"cancelled"} else "negative"
                )
                connection.execute(
                    read_sql("UPDATE alerts SET borderline_review_status=?,is_borderline_promoted=? WHERE id=?"),
                    (status, int(status == "positive"), alert_id),
                )
            connection.commit()
        return [row["context_path"] for row in rows]

    def list_mobile_notifications(self, user_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                read_sql("""
                SELECT DISTINCT n.id,n.alert_id,n.title,n.body,n.data_payload_json,n.created_at,
                       CASE WHEN nr.read_at IS NULL THEN 0 ELSE 1 END AS is_read
                FROM notifications n
                LEFT JOIN notification_recipients target ON target.notification_id=n.id AND target.feed_visible=1
                LEFT JOIN user_roles ur ON ur.user_id=? AND ur.role_id=target.role_id
                LEFT JOIN notification_reads nr ON nr.notification_id=n.id AND nr.user_id=?
                WHERE n.visibility_policy='broadcast' OR target.user_id=? OR ur.user_id IS NOT NULL
                ORDER BY n.created_at DESC
                """),
                (user_id, user_id, user_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_read_alert_ids(self, user_id: str) -> set[str]:
        with self._connect() as connection:
            return {
                row["alert_id"] for row in connection.execute(
                    read_sql("SELECT alert_id FROM alert_reads WHERE user_id=?"), (user_id,)
                ).fetchall()
            }

    def mark_mobile_alert_read(self, user_id: str, alert_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                read_sql("INSERT OR IGNORE INTO alert_reads(user_id,alert_id,read_at) VALUES (?,?,?)"),
                (user_id, alert_id, _iso()),
            )
            connection.commit()

    def mark_mobile_notifications_read(self, user_id: str) -> int:
        now = _iso()
        visible = self.list_mobile_notifications(user_id)
        unread = [item["id"] for item in visible if not item["is_read"]]
        if not unread:
            return 0
        with self._lock, self._connect() as connection:
            connection.executemany(
                read_sql("INSERT OR IGNORE INTO notification_reads(notification_id,user_id,read_at) VALUES (?,?,?)"),
                [(notification_id, user_id, now) for notification_id in unread],
            )
            connection.commit()
        return len(unread)

    def get_mobile_settings(self) -> dict:
        with self._connect() as connection:
            row = connection.execute(read_sql("SELECT * FROM mobile_preferences WHERE id='global'")).fetchone()
        assert row is not None
        value = dict(row)
        value["borderline_recipient_role_ids"] = json.loads(
            value.pop("borderline_recipient_roles_json")
        )
        return value

    def update_mobile_settings(
        self,
        enabled: bool,
        recipient_role_ids: list[str],
        actor_user_id: str,
    ) -> dict:
        valid_ids = {role["id"] for role in self.list_mobile_roles()}
        selected = sorted(set(recipient_role_ids))
        if any(role_id not in valid_ids for role_id in selected):
            raise ValueError("One or more recipient roles are invalid.")
        now = _iso()
        with self._lock, self._connect() as connection:
            _write_mobile_preferences(connection,*(int(enabled), json.dumps(selected), actor_user_id, now))
            connection.execute(
                read_sql("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)"),
                (
                    new_id("audit"), actor_user_id, "update_borderline_settings",
                    "mobile_settings", "global",
                    json.dumps({"enabled": enabled, "recipientRoleIds": selected}), now,
                ),
            )
            connection.commit()
        return self.get_mobile_settings()
