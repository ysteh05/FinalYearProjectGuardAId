from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = APP_ROOT.parent


def _resolve_path(raw: str | None, default: Path) -> Path:
    if not raw:
        return default.resolve()
    path = Path(raw).expanduser()
    return (APP_ROOT / path).resolve() if not path.is_absolute() else path.resolve()


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    persistence_mode: str
    data_dir: Path
    database_path: Path
    max_upload_mb: int
    auth_mode: str
    notification_mode: str
    firebase_project_id: str | None
    firebase_credentials: Path | None
    violence_checkpoint: Path
    weapon_checkpoint: Path | None
    hf_cache: Path

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        mode = os.getenv("GUARDAID_PERSISTENCE_MODE", "sqlite").strip().lower()
        if mode not in {"demo", "sqlite"}:
            raise ValueError("GUARDAID_PERSISTENCE_MODE must be 'demo' or 'sqlite'.")
        auth_mode = os.getenv("GUARDAID_AUTH_MODE", "disabled").strip().lower()
        notification_mode = os.getenv("GUARDAID_NOTIFICATION_MODE", "console").strip().lower()
        if auth_mode not in {"disabled", "firebase"}:
            raise ValueError("GUARDAID_AUTH_MODE must be 'disabled' or 'firebase'.")
        if notification_mode not in {"console", "firebase"}:
            raise ValueError("GUARDAID_NOTIFICATION_MODE must be 'console' or 'firebase'.")

        credentials_raw = os.getenv("GUARDAID_FIREBASE_CREDENTIALS")
        weapon_raw = os.getenv("GUARDAID_WEAPON_CHECKPOINT")
        data_dir = _resolve_path(os.getenv("GUARDAID_DATA_DIR"), APP_ROOT / "runtime_data")
        return cls(
            persistence_mode=mode,
            data_dir=data_dir,
            database_path=_resolve_path(
                os.getenv("GUARDAID_DATABASE_PATH"), data_dir / "database" / "guardaid.db"
            ),
            max_upload_mb=int(os.getenv("GUARDAID_MAX_UPLOAD_MB", "500")),
            auth_mode=auth_mode,
            notification_mode=notification_mode,
            firebase_project_id=os.getenv("GUARDAID_FIREBASE_PROJECT_ID"),
            firebase_credentials=_resolve_path(credentials_raw, APP_ROOT) if credentials_raw else None,
            violence_checkpoint=_resolve_path(
                os.getenv("GUARDAID_VIOLENCE_CHECKPOINT"),
                WORKSPACE_ROOT
                / "model_runs"
                / "mc3_4s16f2"
                / "run_four_ui_20261007_234526_491833"
                / "best_model.pt",
            ),
            weapon_checkpoint=_resolve_path(
                weapon_raw,
                WORKSPACE_ROOT
                / "model_runs"
                / "yolov9_selected_sources_50epoch"
                / "runs"
                / "sel50_20261011_193754_83560d"
                / "best_inference.pt",
            ),
            hf_cache=_resolve_path(os.getenv("GUARDAID_HF_CACHE"), Path("C:/tmp/guardaid-hf")),
        )

    @property
    def yolov9_root(self) -> Path:
        return _resolve_path(
            os.getenv("GUARDAID_YOLOV9_ROOT"),
            WORKSPACE_ROOT / "results (4)" / "yolov9",
        )

    @property
    def weapon_thresholds_path(self) -> Path:
        return _resolve_path(
            os.getenv("GUARDAID_WEAPON_THRESHOLDS"),
            WORKSPACE_ROOT
            / "model_runs"
            / "yolov9_selected_sources_50epoch"
            / "runs"
            / "sel50_20261011_193754_83560d"
            / "sensitivity_thresholds.json",
        )

    @property
    def max_active_feeds(self) -> int:
        return max(1, min(16, int(os.getenv("GUARDAID_MAX_ACTIVE_FEEDS", "4"))))

    def validate_firebase_admin(self) -> None:
        missing = []
        if not self.firebase_project_id:
            missing.append("GUARDAID_FIREBASE_PROJECT_ID")
        if not self.firebase_credentials:
            missing.append("GUARDAID_FIREBASE_CREDENTIALS")
        elif not self.firebase_credentials.is_file():
            missing.append(f"credential file not found: {self.firebase_credentials}")
        else:
            try:
                credential_data = json.loads(
                    self.firebase_credentials.read_text(encoding="utf-8")
                )
                credential_project_id = credential_data.get("project_id")
                if credential_project_id != self.firebase_project_id:
                    missing.append(
                        "Firebase project mismatch: GUARDAID_FIREBASE_PROJECT_ID is "
                        f"'{self.firebase_project_id}' but the service-account JSON belongs "
                        f"to '{credential_project_id}'"
                    )
            except (OSError, json.JSONDecodeError) as exc:
                missing.append(
                    f"unable to read Firebase credential JSON: {type(exc).__name__}"
                )
        if missing:
            raise RuntimeError("Firebase Auth/FCM configuration is incomplete: " + ", ".join(missing))
