from __future__ import annotations

import json
import hashlib
import logging
import math
import secrets
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import RuntimeConfig
from .vlm_contract import MODEL_ID
from .domain import (
    Camera,
    CameraUpdate,
    DeviceRegistration,
    MobileAlertAction,
    MobileBorderlineSettings,
    BorderlineRecipientUpdate,
    MobileRoleUpdate,
    MobileRoleCreate,
    MobileProfileUpdate,
    MobileUserAssignment,
    MobileUserCreate,
    SystemSettings,
    new_id,
)
from .firebase_services import (
    FirebaseAccountAlreadyExists,
    TokenVerificationError,
    TokenVerifier,
    UserAccountAdmin,
    build_notification_sender,
    build_token_verifier,
    build_user_account_admin,
)
from .media import LocalMediaStore, MediaStore
from .inference import InferenceOrchestrator
from .notification_escalation import AlertNotificationEscalationWorker
from .notification_outbox import MobileNotificationOutboxWorker
from .repository import ConfigurationConflictError, DemoRepository, Repository
from .simulation import SimulationPlanner
from .sqlite_repository import SQLiteRepository
from .workflow_conflicts import WorkflowConflictError


STATIC_ROOT = Path(__file__).resolve().parent / "static"
LOGGER = logging.getLogger(__name__)


def _json(model):
    return model.model_dump(mode="json", by_alias=True)


def _camera_json(camera):
    media_url = f"/api/cameras/{camera.id}/media"
    if Path(camera.storage_path).suffix.lower() == ".avi":
        source_version = hashlib.sha256(camera.storage_path.encode("utf-8")).hexdigest()[:16]
        media_url += f"?playback=avi-mp4-v1&source={source_version}"
    return {**_json(camera), "mediaUrl": media_url}


def create_app(
    runtime: RuntimeConfig | None = None,
    repository: Repository | None = None,
    media_store: MediaStore | None = None,
    token_verifier_override: TokenVerifier | None = None,
    user_account_admin_override: UserAccountAdmin | None = None,
) -> FastAPI:
    runtime = runtime or RuntimeConfig.from_env()
    if repository is None:
        repository = SQLiteRepository(runtime) if runtime.persistence_mode == "sqlite" else DemoRepository()
    if media_store is None:
        media_store = LocalMediaStore(runtime)
    token_verifier = token_verifier_override or build_token_verifier(runtime)
    user_account_admin = (
        user_account_admin_override
        if user_account_admin_override is not None
        else build_user_account_admin(runtime)
    )
    notification_sender = build_notification_sender(runtime)
    orchestrator = InferenceOrchestrator(
        runtime, repository, media_store, notification_sender
    )
    notification_escalation_worker = AlertNotificationEscalationWorker(
        repository, notification_sender
    )
    outbox_worker = MobileNotificationOutboxWorker(repository, notification_sender)
    orchestrator.notifications._outbox = outbox_worker
    planner = SimulationPlanner(runtime, orchestrator.readiness)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if runtime.maintenance_mode:
            try:
                yield
            finally:
                orchestrator.shutdown()
            return
        try:
            orchestrator.reconcile_pending_borderline_notifications()
            orchestrator.recover_pending_scene_descriptions()
        except Exception as exc:
            LOGGER.error(
                "Borderline notification recovery failed (%s); the outbox remains available for normal work.",
                type(exc).__name__,
            )
        outbox_worker.start()
        notification_escalation_worker.start()
        try:
            yield
        finally:
            notification_escalation_worker.stop()
            outbox_worker.stop()
            orchestrator.shutdown()

    app = FastAPI(title="GuardAId Monitor", version="0.2.0", lifespan=lifespan)
    app.state.runtime = runtime
    app.state.repository = repository
    app.state.media_store = media_store
    app.state.planner = planner
    app.state.token_verifier = token_verifier
    app.state.user_account_admin = user_account_admin
    app.state.notification_sender = notification_sender
    app.state.notification_escalation_worker = notification_escalation_worker
    app.state.notification_outbox_worker = outbox_worker
    app.state.orchestrator = orchestrator

    @app.middleware("http")
    async def maintenance_gate(request, call_next):
        if runtime.maintenance_mode and request.url.path.startswith("/api/") and request.url.path not in {"/api/health", "/api/runtime"}:
            return JSONResponse(status_code=503, content={"detail": "Database maintenance is in progress. Please retry later."}, headers={"Retry-After": "60"})
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "maintenance" if runtime.maintenance_mode else "ok", "persistenceMode": runtime.persistence_mode,
            "maintenanceMode": runtime.maintenance_mode,
            "schemaVersion": repository.schema_version() if isinstance(repository, SQLiteRepository) else None}

    @app.get("/api/runtime")
    def runtime_status() -> dict:
        warnings: list[str] = []
        if runtime.maintenance_mode:
            warnings.append("Database maintenance is in progress. Operations are temporarily unavailable.")
        if runtime.persistence_mode == "demo":
            warnings.append(
                "Demo mode is non-persistent and uploads remain on this computer only."
            )
        if runtime.auth_mode == "disabled":
            warnings.append("Firebase Authentication is not configured; keep this server on localhost.")
        readiness = planner.readiness()
        if not readiness["weapon"]["checkpointFound"]:
            warnings.append("Weapon detector is unavailable: no validated checkpoint is configured.")
        return {
            "persistenceMode": runtime.persistence_mode,
            "maintenanceMode": runtime.maintenance_mode,
            "cloudConnected": False,
            "database": {
                "engine": "sqlite" if runtime.persistence_mode == "sqlite" else "memory",
                "file": runtime.database_path.name if runtime.persistence_mode == "sqlite" else None,
                "schemaVersion": repository.schema_version() if isinstance(repository, SQLiteRepository) else None,
            },
            "mediaStorage": "local_filesystem",
            "authMode": runtime.auth_mode,
            "notificationMode": runtime.notification_mode,
            "firebaseProjectId": runtime.firebase_project_id if runtime.auth_mode == "firebase" else None,
            "maxUploadMb": runtime.max_upload_mb,
            "maxActiveFeeds": runtime.max_active_feeds,
            "readiness": readiness,
            "warnings": warnings,
        }

    @app.get("/api/cameras")
    def list_cameras() -> list[dict]:
        return [
            _camera_json(camera)
            for camera in repository.list_cameras()
        ]

    @app.post("/api/cameras", status_code=201)
    def create_camera(
        video: UploadFile = File(...),
        name: str = Form(..., min_length=1, max_length=80),
        location: str = Form(default="Unspecified", max_length=120),
        duration_seconds: float = Form(default=0, alias="durationSeconds", ge=0),
    ) -> dict:
        camera_id = new_id("cam")
        try:
            stored = media_store.save_camera_video(
                camera_id,
                video.filename or "video.mp4",
                video.content_type or "application/octet-stream",
                video.file,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        camera = Camera(
            id=camera_id,
            name=name.strip(),
            location=location.strip() or "Unspecified",
            storage_path=stored.storage_path,
            original_filename=video.filename or "video.mp4",
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            duration_seconds=duration_seconds,
        )
        repository.save_camera(camera)
        return _camera_json(camera)

    @app.patch("/api/cameras/{camera_id}")
    def update_camera(camera_id: str, update: CameraUpdate) -> dict:
        camera = repository.get_camera(camera_id)
        if not camera:
            raise HTTPException(status_code=404, detail="Camera not found")
        changes = update.model_dump(exclude_none=True)
        changes["updated_at"] = datetime.now(timezone.utc)
        updated = camera.model_copy(update=changes)
        repository.save_camera(updated)
        return _camera_json(updated)

    @app.put("/api/cameras/{camera_id}/video")
    def replace_camera_video(
        camera_id: str,
        video: UploadFile = File(...),
        duration_seconds: float = Form(default=0, alias="durationSeconds", ge=0),
    ) -> dict:
        camera = repository.get_camera(camera_id)
        if not camera:
            raise HTTPException(status_code=404, detail="Camera not found")
        if orchestrator.has_pending_media(camera_id) or any(
            job.camera_id == camera_id and (str(job.status) in {"queued", "running"} or job.pending_vlm_jobs > 0 or job.pending_media_jobs > 0)
            for job in orchestrator.list_jobs()
        ):
            raise HTTPException(status_code=409, detail="Stop camera analysis and wait for pending evidence/VLM tasks before replacing its video")
        try:
            stored = media_store.save_camera_video(
                camera.id,
                video.filename or "video.mp4",
                video.content_type or "application/octet-stream",
                video.file,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        updated = camera.model_copy(
            update={
                "storage_path": stored.storage_path,
                "original_filename": video.filename or "video.mp4",
                "content_type": stored.content_type,
                "size_bytes": stored.size_bytes,
                "duration_seconds": duration_seconds,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        try:
            repository.save_camera(updated)
            stale_paths = (
                repository.remove_inactive_camera_sources(camera_id)
                if hasattr(repository, "remove_inactive_camera_sources")
                else [camera.storage_path]
            )
            for stale_path in stale_paths:
                media_store.delete(stale_path)
        except Exception:
            media_store.delete(stored.storage_path)
            raise
        return _camera_json(updated)

    @app.delete("/api/cameras/{camera_id}", status_code=204)
    def delete_camera(camera_id: str) -> None:
        if orchestrator.has_pending_media(camera_id) or any(
            job.camera_id == camera_id and (str(job.status) in {"queued", "running"} or job.pending_vlm_jobs > 0 or job.pending_media_jobs > 0)
            for job in orchestrator.list_jobs()
        ):
            raise HTTPException(status_code=409, detail="Stop camera analysis and wait for pending evidence/VLM tasks before deleting it")
        try:
            camera = repository.delete_camera(camera_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not camera:
            raise HTTPException(status_code=404, detail="Camera not found")
        media_store.delete(camera.storage_path)

    @app.get("/api/cameras/{camera_id}/media")
    def camera_media(camera_id: str):
        camera = repository.get_camera(camera_id)
        if not camera:
            raise HTTPException(status_code=404, detail="Camera not found")
        source_suffix = Path(camera.storage_path).suffix.lower()
        try:
            playback = getattr(media_store, "materialize_browser_playback", None)
            path = playback(camera.storage_path) if callable(playback) else media_store.materialize(camera.storage_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Camera media not found") from exc
        except RuntimeError as exc:
            LOGGER.warning("Could not prepare browser playback for camera %s (%s).", camera_id, type(exc).__name__)
            raise HTTPException(
                status_code=503,
                detail="The AVI playback preview could not be prepared. The original video is preserved for inference.",
            ) from exc
        is_avi_preview = source_suffix == ".avi" and path.suffix.lower() == ".mp4"
        filename = f"{Path(camera.original_filename).stem}.mp4" if is_avi_preview else camera.original_filename
        media_type = "video/mp4" if is_avi_preview else camera.content_type
        return FileResponse(
            path,
            media_type=media_type,
            filename=filename,
            content_disposition_type="inline",
        )

    @app.get("/api/cameras/{camera_id}/frame")
    def camera_frame(camera_id: str, seconds: float = Query(ge=0)) -> Response:
        """Return the decoded source frame at a detector sample timestamp."""
        if not math.isfinite(seconds):
            raise HTTPException(status_code=422, detail="Frame timestamp must be finite")
        camera = repository.get_camera(camera_id)
        if not camera:
            raise HTTPException(status_code=404, detail="Camera not found")
        try:
            path = media_store.materialize(camera.storage_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Camera media not found") from exc

        import cv2

        capture = cv2.VideoCapture(str(path))
        try:
            if not capture.isOpened():
                raise HTTPException(status_code=422, detail="OpenCV could not decode the source video")
            fps = capture.get(cv2.CAP_PROP_FPS)
            if not math.isfinite(fps) or fps <= 0:
                fps = 25.0
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            frame_index = max(0, int(round(seconds * fps)))
            if frame_count > 0 and frame_index >= frame_count:
                raise HTTPException(status_code=404, detail="No source frame exists at this timestamp")
            if not capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index):
                capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000.0)
            ok, frame = capture.read()
            if not ok:
                raise HTTPException(status_code=404, detail="No source frame exists at this timestamp")
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if not ok:
                raise HTTPException(status_code=500, detail="Could not encode the sampled source frame")
            return Response(
                content=encoded.tobytes(),
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )
        finally:
            capture.release()

    @app.get("/api/alerts")
    def list_alerts() -> list[dict]:
        output = []
        diagnostics = repository.scene_diagnostics() if hasattr(repository, 'scene_diagnostics') else {}
        review_summaries = (
            repository.list_borderline_review_summaries()
            if isinstance(repository, SQLiteRepository)
            else {}
        )
        for alert in repository.list_alerts():
            value = _json(alert)
            value['sceneDiagnostics'] = diagnostics.get(alert.id)
            summary = review_summaries.get(alert.id)
            if summary:
                value["borderlineReviewSummary"] = {
                    "total": summary["total"],
                    "positive": summary["positive"],
                    "negative": summary["negative"],
                    "failed": summary["failed"],
                    "cancelled": summary["cancelled"],
                    "pending": summary["pending"],
                    "latestStatus": summary["latest_status"],
                    "latestReason": summary["latest_reason"],
                    "latestUncertainty": summary["latest_uncertainty"],
                    "latestErrorCode": summary["latest_error_code"],
                }
            else:
                value["borderlineReviewSummary"] = {
                    "total": 0,
                    "positive": 0,
                    "negative": 0,
                    "failed": 0,
                    "cancelled": 0,
                    "pending": 0,
                    "latestStatus": None,
                    "latestReason": None,
                    "latestUncertainty": None,
                    "latestErrorCode": None,
                }
            for evidence in value["evidence"]:
                evidence["url"] = f"/api/evidence/{evidence['id']}/media"
            output.append(value)
        return output

    @app.get("/api/evidence/{evidence_id}/media")
    def evidence_media(evidence_id: str):
        evidence = next(
            (
                item
                for alert in repository.list_alerts()
                for item in alert.evidence
                if item.id == evidence_id
            ),
            None,
        )
        if not evidence:
            raise HTTPException(status_code=404, detail="Evidence not found")
        try:
            path = media_store.materialize(evidence.storage_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Evidence media not found") from exc
        return FileResponse(path, media_type=evidence.content_type)

    @app.get("/api/settings")
    def get_settings() -> dict:
        return _json(repository.get_settings())

    @app.put("/api/settings")
    def save_settings(incoming: SystemSettings) -> dict:
        if incoming.vlm.model_id != MODEL_ID:
            raise HTTPException(status_code=422, detail=f"The serving VLM is {MODEL_ID}. Reload configuration before saving.")
        current = repository.get_settings()
        updated = incoming.model_copy(
            update={
                "id": "global",
                "config_version": current.config_version + 1,
                "updated_at": datetime.now(timezone.utc),
                "updated_by": "local-web-admin",
                "vlm": incoming.vlm.model_copy(update={"quantization": "Q4_K_M (Ollama)"}),
            }
        )
        try:
            saved = repository.save_settings(updated, expected_config_version=incoming.config_version)
        except ConfigurationConflictError as exc:
            raise HTTPException(status_code=409, detail={
                "code": "config_version_conflict", "message": str(exc),
                "currentConfigVersion": exc.current_version,
            }) from exc
        if (
            isinstance(repository, SQLiteRepository)
            and current.vlm.borderline_review_enabled
            and not updated.vlm.borderline_review_enabled
        ):
            review_root = (runtime.data_dir / "tmp" / "borderline_reviews").resolve()
            for context_path in repository.cancel_queued_borderline_review_tasks():
                candidate = Path(context_path)
                try:
                    resolved = candidate.resolve()
                    resolved.relative_to(review_root)
                except (OSError, ValueError):
                    continue
                try:
                    if resolved.is_file():
                        resolved.unlink()
                except OSError as exc:
                    LOGGER.warning(
                        "Could not remove a cancelled review context (%s).",
                        type(exc).__name__,
                    )
        return _json(saved)

    @app.post("/api/simulations/{camera_id}/plan")
    def plan_simulation(camera_id: str) -> dict:
        camera = repository.get_camera(camera_id)
        if not camera:
            raise HTTPException(status_code=404, detail="Camera not found")
        return _json(planner.create_plan(camera, repository.get_settings()))

    @app.post("/api/analysis/{camera_id}", status_code=202)
    def start_analysis(camera_id: str, monitorPlayback: bool = Query(default=False)) -> dict:
        try:
            return _json(orchestrator.start(camera_id, monitor_playback=monitorPlayback))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            # Model-runtime failures are operational readiness errors, not an
            # unhandled API crash. Auth/mobile endpoints remain available.
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/analysis")
    def list_analysis_jobs() -> list[dict]:
        return [_json(job) for job in orchestrator.list_jobs()]

    @app.get("/api/monitor")
    def monitor_results(cursors: str = Query(default="{}", max_length=20000)) -> dict:
        try:
            values = json.loads(cursors)
            if not isinstance(values, dict) or any(
                not isinstance(k, str) or not isinstance(v, int) or isinstance(v, bool) or v < 0
                for k, v in values.items()
            ):
                raise ValueError("Invalid monitor cursors")
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="Monitor cursors must map job IDs to nonnegative integers") from exc
        return orchestrator.monitor_snapshot(values)

    @app.post("/api/monitor/playback")
    def monitor_playback(payload: dict) -> dict:
        cursors, players = payload.get("cursors", {}), payload.get("players", {})
        if not isinstance(cursors, dict) or not isinstance(players, dict) or len(cursors) > 100 or len(players) > 100:
            raise HTTPException(status_code=422, detail="Invalid monitor playback data")
        if any(not isinstance(k, str) or not isinstance(v, int) or isinstance(v, bool) or v < 0 for k, v in cursors.items()):
            raise HTTPException(status_code=422, detail="Invalid monitor cursors")
        if any(not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for k, v in players.items()):
            raise HTTPException(status_code=422, detail="Invalid playback positions")
        return orchestrator.monitor_snapshot(cursors, players)

    @app.get("/api/analysis/{job_id}/results")
    def monitor_history(job_id: str, after: int = Query(default=0, ge=0), limit: int = Query(default=1000, ge=1, le=1000)) -> dict:
        try:
            return orchestrator.monitor_history(job_id, after, limit)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Analysis results not found") from exc

    @app.get("/api/analysis/{job_id}")
    def get_analysis_job(job_id: str) -> dict:
        job = orchestrator.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Analysis job not found")
        return _json(job)

    @app.delete("/api/analysis/{job_id}", status_code=202)
    def cancel_analysis_job(job_id: str) -> dict:
        try:
            return _json(orchestrator.cancel(job_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def verified_local_user(authorization: str | None, require_active: bool = False) -> dict:
        if token_verifier is None:
            raise HTTPException(status_code=503, detail="Firebase Authentication is not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="A Firebase ID token is required")
        try:
            identity = token_verifier.verify(authorization.removeprefix("Bearer ").strip())
        except TokenVerificationError as exc:
            raise HTTPException(status_code=401, detail=exc.public_message) from exc
        except Exception as exc:
            raise HTTPException(status_code=401, detail="The Firebase ID token is invalid or expired") from exc
        if not isinstance(repository, SQLiteRepository):
            raise HTTPException(status_code=503, detail="Mobile identity requires SQLite mode")
        saved = repository.upsert_firebase_user(
            identity.firebase_uid, identity.email, identity.display_name
        )
        profile = repository.mobile_user_profile(saved["id"])
        assert profile is not None
        if require_active and profile["status"] != "active":
            raise HTTPException(
                status_code=403,
                detail="A School Administrator must activate this account before alerts can be accessed",
            )
        return profile

    def active_mobile_user(authorization: str | None) -> dict:
        user = verified_local_user(authorization, require_active=True)
        if not user["roles"]:
            raise HTTPException(
                status_code=403,
                detail="An active role assignment is required before alerts can be accessed",
            )
        return user

    def admin_mobile_user(authorization: str | None) -> dict:
        user = active_mobile_user(authorization)
        if not user["is_admin"]:
            raise HTTPException(status_code=403, detail="School Administrator access is required")
        return user

    def permission_mobile_user(authorization: str | None, permission: str) -> dict:
        user = active_mobile_user(authorization)
        if not isinstance(repository, SQLiteRepository) or not repository.user_has_permission(user["id"], permission):
            raise HTTPException(status_code=403, detail="This role does not have the required permission")
        return user

    def mobile_alert_value(
        alert,
        read_alert_ids: set[str] | None = None,
        *,
        user_id: str | None = None,
        include_detail: bool = False,
    ) -> dict:
        value = _json(alert)
        value["isRead"] = alert.id in (read_alert_ids or set())
        camera = repository.get_camera(alert.camera_id)
        value["location"] = camera.location if camera else "Unspecified"
        for evidence in value["evidence"]:
            evidence["url"] = f"/api/mobile/evidence/{evidence['id']}/media"
        if isinstance(repository, SQLiteRepository):
            workflow = repository.get_mobile_alert_workflow(alert.id)
            value["workflowStatus"] = (
                workflow["workflow_status"] if workflow else "awaiting_confirmation"
            )
            value["handlerUserId"] = workflow.get("handler_user_id") if workflow else None
            value["handlerName"] = workflow.get("handler_name") if workflow else None
            value["broadcastRoleIds"] = workflow.get("broadcast_role_ids", []) if workflow else []
            value["workflowRevision"] = workflow.get("revision", 0) if workflow else 0
            value["activeReminderPhaseId"] = workflow.get("active_phase_id") if workflow else None
            value["actions"] = [
                {
                    "id": item["id"],
                    "action": item["action"],
                    "note": item["note"],
                    "createdAt": item["created_at"],
                    "performedBy": item["performed_by"],
                    "performedByEmail": item.get("performed_by_email"),
                    "broadcastRoleIds": item.get("broadcast_role_ids", []),
                    "isLegacy": item.get("is_legacy", False),
                }
                for item in repository.list_alert_actions(alert.id)
            ]
            if include_detail:
                tasks = repository.list_borderline_review_tasks(alert.id)
                detection_events = repository.list_alert_detection_events(alert.id)
                value["borderlineReviewTasks"] = [
                    {
                        "id": item["id"],
                        "status": item["status"],
                        "detectorConfidence": item["detector_confidence"],
                        "sourceStartSeconds": item["source_start_seconds"],
                        "sourceEndSeconds": item["source_end_seconds"],
                        "raiseAlert": item["raise_alert"],
                        "uncertainty": item["uncertainty"],
                        "briefReason": item["brief_reason"],
                        "errorCode": item["error_code"],
                        "modelVersion": item["model_version"],
                        "promptVersion": item["prompt_version"],
                        "createdAt": item["created_at"],
                        "completedAt": item["completed_at"],
                    }
                    for item in tasks
                ]
                value["borderlineReviewSummary"] = {
                    "status": alert.borderline_review_status,
                    "total": len(tasks),
                    "positive": sum(item["status"] == "positive" for item in tasks),
                    "negative": sum(item["status"] == "negative" for item in tasks),
                    "failed": sum(item["status"] == "failed" for item in tasks),
                    "cancelled": sum(item["status"] == "cancelled" for item in tasks),
                    "pending": sum(item["status"] in {"queued", "running"} for item in tasks),
                }
                value["detectionEvents"] = [
                    {
                        "id": item["id"],
                        "confidence": item["confidence"],
                        "decisionSource": item["decision_source"],
                        "sourceStartSeconds": item["source_start_seconds"],
                        "sourceEndSeconds": item["source_end_seconds"],
                        "detectedAt": item["detected_at"],
                        "qualifications": item["qualifications"],
                        "weaponClassScores": item["weapon_class_scores"],
                        "configVersion": item["config_version"],
                        "modelVersion": item["model_version"],
                    }
                    for item in detection_events
                ]
                if user_id:
                    audience = repository.get_alert_notification_audience(alert.id, user_id)
                    if audience is None:
                        value["notificationEligibility"] = {
                            "status": "not_recorded",
                            "inFirstNotificationAudience": False,
                            "qualificationReasons": [],
                            "note": (
                                "No first-notification record exists for this account; "
                                "historical eligibility may not be reconstructable."
                            ),
                        }
                    else:
                        sent = audience.get("first_sent_at") is not None
                        queued = audience.get("first_queued_at") is not None
                        value["notificationEligibility"] = {
                            "status": (
                                "provider_accepted" if sent else
                                "pending" if audience.get("outbox_status") in {"pending", "dispatching"} else
                                "not_sent" if queued else "eligible"
                            ),
                            "inFirstNotificationAudience": True,
                            "qualificationReasons": audience["qualification_reasons"],
                            "firstNotificationProviderAcceptedAt": audience.get("first_sent_at"),
                            "note": (
                                "FCM accepted the send request; GuardAId cannot confirm that "
                                "the phone displayed it or played sound."
                                if sent else None
                            ),
                        }
                    review_enabled = repository.get_settings().vlm.borderline_review_enabled
                    user_borderline_enabled = repository.borderline_user_enabled(user_id)
                    value["borderlineReviewAvailability"] = {
                        "available": review_enabled,
                        "enabledForAccount": user_borderline_enabled,
                        "effective": review_enabled and user_borderline_enabled,
                        "unavailableReason": None if review_enabled else "web_master_disabled",
                    }
        else:
            value["actions"] = []
        return value

    def dispatch_mobile_update(
        alert_id: str,
        title: str,
        body: str,
        role_ids: list[str],
        action: str,
        *,
        exclude_user_ids: set[str] | None = None,
    ) -> int:
        assert isinstance(repository, SQLiteRepository)
        devices = [
            device
            for device in repository.list_enabled_devices_for_roles(role_ids)
            if not exclude_user_ids or device.get("user_id") not in exclude_user_ids
        ]
        # A selected role may currently have no active device. Avoid creating
        # an untargeted notification row in that case.
        if not devices:
            return 0
        data = {"alertId": alert_id, "action": action, "source": "mobile_response"}
        notification_id = repository.create_notification(
            alert_id, title, body, json.dumps(data, separators=(",", ":"))
        )
        repository.set_notification_recipients(notification_id, role_ids)
        sent = 0
        for device in devices:
            try:
                provider_id = notification_sender.send(
                    device["fcm_registration_value"], title, body, data
                )
                repository.record_notification_delivery(
                    notification_id, device["id"], "sent", provider_id, None
                )
                sent += 1
            except Exception as exc:
                repository.record_notification_delivery(
                    notification_id, device["id"], "failed", None, type(exc).__name__
                )
        return sent

    def dispatch_decision_update(
        alert_id: str,
        action: str,
        actor_user_id: str,
        actor_display_name: str | None,
    ) -> int:
        """Notify the original alert audience about a later decision.

        This deliberately uses the routine notification channel. The exact
        original user snapshot is used so later role changes cannot broaden
        the audience unexpectedly.
        """
        assert isinstance(repository, SQLiteRepository)
        original_recipients = repository.list_original_alert_recipient_user_ids(alert_id)
        recipients = [user_id for user_id in original_recipients if user_id != actor_user_id]
        display_name = (actor_display_name or "An authorised user").strip() or "An authorised user"
        action_labels = {
            "dismiss": "dismissed",
            "resolve": "resolved",
            "escalate": "escalated",
        }
        verb = action_labels.get(action, action)
        alert = next((item for item in repository.list_alerts() if item.id == alert_id), None)
        if alert is None or not recipients:
            return 0
        alert_label = str(alert.type).replace("_", " ").title()
        title = f"Alert {verb}"
        body = (
            f"{display_name} {verb} the {alert_label} alert "
            f"at {alert.camera_name}."
        )
        data = {
            "alertId": alert_id,
            "action": action,
            "source": "alert_decision",
        }
        notification_id = repository.create_notification(
            alert_id, title, body, json.dumps(data, separators=(",", ":"))
        )
        repository.set_notification_user_recipients(notification_id, recipients)
        sent = 0
        for device in repository.list_enabled_devices_for_users(
            recipients, exclude_user_id=actor_user_id
        ):
            try:
                provider_id = notification_sender.send(
                    device["fcm_registration_value"],
                    title,
                    body,
                    data,
                    "guardaid_updates_v1",
                )
                repository.record_notification_delivery(
                    notification_id, device["id"], "sent", provider_id, None
                )
                sent += 1
            except Exception as exc:
                repository.record_notification_delivery(
                    notification_id, device["id"], "failed", None, type(exc).__name__
                )
        return sent

    @app.get("/api/mobile/session")
    def mobile_session(authorization: str | None = Header(default=None)) -> dict:
        user = verified_local_user(authorization)
        borderline_master_enabled = (
            repository.get_settings().vlm.borderline_review_enabled
            if isinstance(repository, SQLiteRepository) else False
        )
        borderline_account_enabled = (
            repository.borderline_user_enabled(user["id"])
            if isinstance(repository, SQLiteRepository) else False
        )
        return {
            "userId": user["id"],
            "firebaseUid": user["firebase_uid"],
            "email": user["email_cache"],
            "displayName": user["display_name"] or "Name not provided",
            "status": user["status"],
            "roles": [
                {
                    "id": role["id"],
                    "name": role["name"],
                    "displayName": role.get("display_name") or role["name"],
                    "description": role["description"],
                    "sensitivityMode": role["sensitivity_mode"],
                    "permissions": {
                        "manageRoles": bool(role.get("can_manage_roles")),
                        "manageUsers": bool(role.get("can_manage_users")),
                        "manageBorderlineSettings": bool(role.get("can_manage_borderline_settings")),
                    },
                }
                for role in user["roles"]
            ],
            "sensitivityMode": user["sensitivity_mode"],
            "isAdmin": user["is_admin"],
            "borderlineReviewAvailable": borderline_master_enabled,
            "borderlineReviewEnabledForAccount": borderline_account_enabled,
            "borderlineReviewEffective": borderline_master_enabled and borderline_account_enabled,
            "borderlineReviewUnavailableReason": (
                None if borderline_master_enabled else "web_master_disabled"
            ),
        }

    @app.put("/api/mobile/device")
    def register_mobile_device(
        registration: DeviceRegistration,
        authorization: str | None = Header(default=None),
    ) -> dict:
        user = active_mobile_user(authorization)
        assert isinstance(repository, SQLiteRepository)
        device_id = repository.register_device(
            user["id"],
            registration.registration_value,
            registration.platform,
            registration.device_label,
            registration.installation_id,
        )
        return {"deviceId": device_id, "status": "registered"}

    @app.get("/api/mobile/monitoring-summary")
    def mobile_monitoring_summary(authorization: str | None = Header(default=None)) -> dict:
        active_mobile_user(authorization)
        return {
            "monitoredCameraCount": sum(camera.enabled for camera in repository.list_cameras()),
            "asOf": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/mobile/alerts")
    def list_mobile_alerts(
        authorization: str | None = Header(default=None),
        status: str | None = None,
        incident_type: str | None = None,
        q: str | None = None,
    ) -> list[dict]:
        output = []
        search = (q or "").strip().casefold()
        user = active_mobile_user(authorization)
        read_alert_ids = repository.list_read_alert_ids(user["id"])
        # Role sensitivity controls notification delivery, not incident visibility.
        for alert in repository.list_alerts():
            if status and str(alert.status) != status:
                continue
            if incident_type and str(alert.type) != incident_type:
                continue
            value = mobile_alert_value(alert, read_alert_ids)
            if search and not any(
                search in str(value.get(field) or "").casefold()
                for field in ("cameraName", "location", "type", "sceneDescription")
            ):
                continue
            output.append(value)
        return output

    @app.get("/api/mobile/alerts/{alert_id}")
    def get_mobile_alert(
        alert_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict:
        user = active_mobile_user(authorization)
        alert = next((item for item in repository.list_alerts() if item.id == alert_id), None)
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
        return mobile_alert_value(
            alert,
            repository.list_read_alert_ids(user["id"]),
            user_id=user["id"],
            include_detail=True,
        )

    @app.post("/api/mobile/alerts/{alert_id}/read")
    def mark_mobile_alert_read(
        alert_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict:
        user = active_mobile_user(authorization)
        get_mobile_alert(alert_id, authorization)
        repository.mark_mobile_alert_read(user["id"], alert_id)
        return {"alertId": alert_id, "isRead": True}

    @app.get("/api/mobile/alerts/{alert_id}/broadcast-preview")
    def preview_mobile_alert_broadcast(
        alert_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict:
        user = active_mobile_user(authorization)
        assert isinstance(repository, SQLiteRepository)
        if not any(item.id == alert_id for item in repository.list_alerts()):
            raise HTTPException(status_code=404, detail="Alert not found")
        preview = repository.mobile_alert_broadcast_preview(alert_id, user["id"])
        if preview is None:
            raise HTTPException(status_code=404, detail="Alert not found")
        return preview

    @app.post("/api/mobile/alerts/{alert_id}/actions")
    def submit_mobile_alert_action(
        alert_id: str,
        incoming: MobileAlertAction,
        authorization: str | None = Header(default=None),
    ) -> dict:
        user = active_mobile_user(authorization)
        assert isinstance(repository, SQLiteRepository)
        get_mobile_alert(alert_id, authorization)
        action = {
            "dismiss": "dismiss_false_alarm",
            "escalate": "escalate",
        }.get(incoming.action, incoming.action)
        if action == "escalate":
            raise HTTPException(
                status_code=410,
                detail="Emergency escalation was replaced by Confirm incident and Broadcast.",
            )
        settings = repository.get_settings().alert_notification_escalation
        try:
            result = repository.record_mobile_workflow_action(
                alert_id,
                user["id"],
                action,
                comment=incoming.reason,
                recipient_role_ids=incoming.recipient_role_ids,
                allow_empty_audience=incoming.allow_empty_audience,
                idempotency_key=incoming.idempotency_key or new_id("mobile_action"),
                expected_workflow_revision=incoming.expected_workflow_revision,
                first_after_seconds=settings.same_audience_repeat_after_seconds,
                all_after_seconds=settings.all_users_escalation_after_seconds,
            )
        except WorkflowConflictError as exc:
            return JSONResponse(status_code=409, content={
                'detail': str(exc), 'code': exc.code,
                'workflowStatus': exc.workflow_status,
                'workflowRevision': exc.workflow_revision,
            })
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not result:
            raise HTTPException(status_code=404, detail="Alert not found")
        try:
            outbox_worker.process_pending()
        except Exception:
            LOGGER.exception("Could not immediately process a queued mobile notification.")
        alert = next(item for item in repository.list_alerts() if item.id == alert_id)
        return {
            "alert": mobile_alert_value(alert, repository.list_read_alert_ids(user["id"])),
            "workflow": result["workflow"],
            "notificationsQueued": len(result["queuedOutboxIds"]),
            "broadcastDispatchesQueued": len(result["broadcastOutboxIds"]),
            "decisionUpdateDispatchesQueued": len(result["decisionUpdateOutboxIds"]),
            "broadcastEligibleUsers": result["broadcastEligibleUsers"],
            "idempotent": result["idempotent"],
        }

    @app.post("/api/mobile/alerts/{alert_id}/simulate-emergency-call")
    def simulate_mobile_emergency_call(
        alert_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict:
        user = active_mobile_user(authorization)
        assert isinstance(repository, SQLiteRepository)
        get_mobile_alert(alert_id, authorization)
        settings = repository.get_settings().alert_notification_escalation
        try:
            result = repository.record_mobile_workflow_action(
                alert_id,
                user["id"],
                "simulate_emergency_call",
                comment=None,
                recipient_role_ids=[],
                allow_empty_audience=True,
                idempotency_key=new_id("simulated_call"),
                first_after_seconds=settings.same_audience_repeat_after_seconds,
                all_after_seconds=settings.all_users_escalation_after_seconds,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not result:
            raise HTTPException(status_code=404, detail="Alert not found")
        return {
            "status": "simulated",
            "message": "Simulated emergency call has been placed. No real call was made.",
            "diallerOpened": False,
            "realCallMade": False,
        }

    @app.get("/api/mobile/evidence/{evidence_id}/media")
    def mobile_evidence_media(
        evidence_id: str,
        authorization: str | None = Header(default=None),
    ):
        active_mobile_user(authorization)
        match = next(
            (
                (alert, item)
                for alert in repository.list_alerts()
                for item in alert.evidence
                if item.id == evidence_id
            ),
            None,
        )
        if not match:
            raise HTTPException(status_code=404, detail="Evidence not found")
        alert, evidence = match
        get_mobile_alert(alert.id, authorization)
        try:
            path = media_store.materialize(evidence.storage_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Evidence media not found") from exc
        return FileResponse(path, media_type=evidence.content_type)

    @app.get("/api/mobile/notifications")
    def list_mobile_notifications(
        authorization: str | None = Header(default=None),
    ) -> list[dict]:
        user = active_mobile_user(authorization)
        assert isinstance(repository, SQLiteRepository)
        return [
            {
                "id": item["id"],
                "alertId": item["alert_id"],
                "title": item["title"],
                "message": item["body"],
                "createdAt": item["created_at"],
                "isRead": bool(item["is_read"]),
            }
            for item in repository.list_mobile_notifications(user["id"])
        ]

    @app.post("/api/mobile/notifications/mark-all-read")
    def mark_mobile_notifications_read(
        authorization: str | None = Header(default=None),
    ) -> dict:
        user = active_mobile_user(authorization)
        assert isinstance(repository, SQLiteRepository)
        return {"updated": repository.mark_mobile_notifications_read(user["id"])}

    @app.get("/api/mobile/admin/roles")
    def list_mobile_roles(
        authorization: str | None = Header(default=None),
    ) -> list[dict]:
        permission_mobile_user(authorization, "can_manage_roles")
        assert isinstance(repository, SQLiteRepository)
        return [
            {
                "id": role["id"],
                "name": role["name"],
                "displayName": role.get("display_name") or role["name"],
                "description": role["description"],
                "sensitivityMode": role["sensitivity_mode"],
                "userCount": role["user_count"],
                "isAdmin": role["name"] in {"school_administrator", "admin"},
                "isSystem": bool(role.get("is_system")),
                "permissions": {
                    "manageRoles": bool(role.get("can_manage_roles")),
                    "manageUsers": bool(role.get("can_manage_users")),
                    "manageBorderlineSettings": bool(role.get("can_manage_borderline_settings")),
                },
            }
            for role in repository.list_mobile_roles()
        ]

    @app.get("/api/mobile/roles")
    def list_mobile_recipient_roles(
        authorization: str | None = Header(default=None),
    ) -> list[dict]:
        active_mobile_user(authorization)
        assert isinstance(repository, SQLiteRepository)
        return [
            {
                "id": role["id"],
                "name": role["name"],
                "displayName": role.get("display_name") or role["name"],
                "description": role["description"],
                "sensitivityMode": role["sensitivity_mode"],
                "userCount": role["user_count"],
                "isAdmin": role["name"] in {"school_administrator", "admin"},
                "isSystem": bool(role.get("is_system")),
            }
            for role in repository.list_mobile_roles()
        ]

    @app.patch("/api/mobile/admin/roles/{role_id}")
    def update_mobile_role(
        role_id: str,
        incoming: MobileRoleUpdate,
        authorization: str | None = Header(default=None),
    ) -> dict:
        user = permission_mobile_user(authorization, "can_manage_roles")
        assert isinstance(repository, SQLiteRepository)
        requested_permissions = {
            key: value
            for key, value in {
                "can_manage_roles": incoming.can_manage_roles,
                "can_manage_users": incoming.can_manage_users,
                "can_manage_borderline_settings": incoming.can_manage_borderline_settings,
            }.items()
            if value is not None
        }
        if any(value and not repository.user_has_permission(user["id"], key) for key, value in requested_permissions.items()):
            raise HTTPException(status_code=403, detail="You cannot grant a permission you do not possess")
        try:
            if not repository.update_mobile_role(
                role_id,
                user["id"],
                sensitivity_mode=str(incoming.sensitivity_mode) if incoming.sensitivity_mode else None,
                display_name=incoming.display_name,
                description=incoming.description,
                permissions=requested_permissions,
            ):
                raise HTTPException(status_code=404, detail="Role not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return next(role for role in list_mobile_roles(authorization) if role["id"] == role_id)

    @app.delete("/api/mobile/admin/roles/{role_id}")
    def delete_mobile_role(
        role_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict:
        actor = permission_mobile_user(authorization, "can_manage_roles")
        assert isinstance(repository, SQLiteRepository)
        try:
            result = repository.delete_mobile_role(role_id, actor["id"])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Role not found")
        return result

    @app.post("/api/mobile/admin/roles", status_code=201)
    def create_mobile_role(
        incoming: MobileRoleCreate,
        authorization: str | None = Header(default=None),
    ) -> dict:
        actor = permission_mobile_user(authorization, "can_manage_roles")
        assert isinstance(repository, SQLiteRepository)
        requested = {
            "can_manage_roles": incoming.can_manage_roles,
            "can_manage_users": incoming.can_manage_users,
            "can_manage_borderline_settings": incoming.can_manage_borderline_settings,
        }
        if any(value and not repository.user_has_permission(actor["id"], key) for key, value in requested.items()):
            raise HTTPException(status_code=403, detail="You cannot grant a permission you do not possess")
        try:
            role = repository.create_mobile_role(
                incoming.name,
                incoming.description,
                str(incoming.sensitivity_mode),
                requested,
                actor["id"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if role is None:
            raise HTTPException(status_code=422, detail="Role name is required")
        return {
            "id": role["id"],
            "name": role["name"],
            "displayName": role["display_name"],
            "description": role["description"],
            "sensitivityMode": role["sensitivity_mode"],
            "userCount": role["user_count"],
            "isAdmin": False,
            "isSystem": False,
            "permissions": {
                "manageRoles": bool(role["can_manage_roles"]),
                "manageUsers": bool(role["can_manage_users"]),
                "manageBorderlineSettings": bool(role["can_manage_borderline_settings"]),
            },
        }

    @app.get("/api/mobile/admin/users")
    def list_mobile_users(
        authorization: str | None = Header(default=None),
    ) -> list[dict]:
        permission_mobile_user(authorization, "can_manage_users")
        assert isinstance(repository, SQLiteRepository)
        return [mobile_admin_user_value(item) for item in repository.list_mobile_users()]

    def mobile_admin_user_value(item: dict) -> dict:
        return {
            "id": item["id"],
            "firebaseUid": item["firebase_uid"],
            "email": item["email_cache"],
            "displayName": item["display_name"] or "Name not provided",
            "status": item["status"],
            "roleId": item["role_id"],
            "roleName": item["role_name"],
            "roleDisplayName": item.get("role_display_name") or item["role_name"],
            "roleKey": item["role_name"],
            "sensitivityMode": item["sensitivity_mode"],
            "isAdmin": item["role_name"] in {"school_administrator", "admin"},
        }

    @app.post("/api/mobile/admin/users", status_code=201, response_model=None)
    def create_mobile_user(
        incoming: MobileUserCreate,
        authorization: str | None = Header(default=None),
    ) -> dict | JSONResponse:
        actor = permission_mobile_user(authorization, "can_manage_users")
        if not isinstance(repository, SQLiteRepository):
            raise HTTPException(status_code=503, detail="User management requires SQLite mode")
        existing_user = repository.mobile_user_by_email(incoming.email)
        if existing_user:
            existing_record = next(
                item
                for item in repository.list_mobile_users()
                if item["id"] == existing_user["id"]
            )
            return JSONResponse(
                status_code=200,
                content={
                    **mobile_admin_user_value(existing_record),
                    "identityAction": "already_registered",
                },
            )
        if user_account_admin is None:
            raise HTTPException(status_code=503, detail="Firebase Authentication is not configured")
        try:
            repository.assert_mobile_user_role_assignable(actor["id"], incoming.role_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Role not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        created_identity = False
        try:
            firebase_user = user_account_admin.find_by_email(incoming.email)
            if firebase_user is not None:
                if not incoming.link_existing:
                    raise HTTPException(status_code=409, detail="firebase_identity_exists")
                if firebase_user.disabled:
                    raise HTTPException(status_code=409, detail="firebase_identity_disabled")
                identity_action = "linked"
            else:
                if incoming.link_existing:
                    raise HTTPException(status_code=404, detail="firebase_identity_not_found")
                # The recipient sets their own real password using the Firebase reset email.
                firebase_user = user_account_admin.create_user(
                    incoming.email,
                    incoming.display_name,
                    secrets.token_urlsafe(36),
                )
                created_identity = True
                identity_action = "created"
        except HTTPException:
            raise
        except FirebaseAccountAlreadyExists as exc:
            raise HTTPException(status_code=409, detail="firebase_identity_exists") from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Firebase Authentication could not create or link this account.",
            ) from exc

        def cleanup_created_firebase_identity() -> bool:
            if not created_identity:
                return True
            try:
                user_account_admin.delete_user(firebase_user.uid)
                return True
            except Exception:
                return False

        try:
            saved = repository.create_mobile_user(
                firebase_user.uid,
                incoming.email,
                incoming.display_name,
                incoming.role_id,
                actor["id"],
                identity_action,
            )
        except PermissionError as exc:
            if not cleanup_created_firebase_identity():
                raise HTTPException(
                    status_code=502,
                    detail="account_created_but_local_setup_failed",
                ) from exc
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            if not cleanup_created_firebase_identity():
                raise HTTPException(
                    status_code=502,
                    detail="account_created_but_local_setup_failed",
                ) from exc
            raise HTTPException(status_code=404, detail="Role not found") from exc
        except ValueError as exc:
            if not cleanup_created_firebase_identity():
                raise HTTPException(
                    status_code=502,
                    detail="account_created_but_local_setup_failed",
                ) from exc
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            rollback_succeeded = cleanup_created_firebase_identity()
            detail = (
                "account_created_but_local_setup_failed"
                if created_identity and not rollback_succeeded
                else "The GuardAId user record could not be created."
            )
            raise HTTPException(status_code=502, detail=detail) from exc

        return {
            **mobile_admin_user_value(saved),
            "identityAction": identity_action,
        }

    @app.patch("/api/mobile/admin/users/{user_id}")
    def update_mobile_user(
        user_id: str,
        incoming: MobileUserAssignment,
        authorization: str | None = Header(default=None),
    ) -> dict:
        actor = permission_mobile_user(authorization, "can_manage_users")
        assert isinstance(repository, SQLiteRepository)
        try:
            if incoming.role_id is None:
                if not repository.revoke_mobile_user(user_id, actor["id"]):
                    raise HTTPException(status_code=404, detail="User not found")
            else:
                if not repository.assign_mobile_user(user_id, incoming.role_id, incoming.status, actor["id"]):
                    raise HTTPException(status_code=404, detail="User or role not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return next(item for item in list_mobile_users(authorization) if item["id"] == user_id)

    @app.post("/api/mobile/admin/users/{user_id}/deactivate")
    def deactivate_mobile_user(
        user_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict:
        actor = permission_mobile_user(authorization, "can_manage_users")
        assert isinstance(repository, SQLiteRepository)
        try:
            if not repository.deactivate_mobile_user(user_id, actor["id"]):
                raise HTTPException(status_code=404, detail="User not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return next(item for item in list_mobile_users(authorization) if item["id"] == user_id)

    @app.patch("/api/mobile/profile")
    def update_mobile_profile(
        incoming: MobileProfileUpdate,
        authorization: str | None = Header(default=None),
    ) -> dict:
        user = active_mobile_user(authorization)
        assert isinstance(repository, SQLiteRepository)
        try:
            saved = repository.update_mobile_profile(user["id"], incoming.display_name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if saved is None:
            raise HTTPException(status_code=404, detail="User not found")
        return mobile_session(authorization)

    @app.get("/api/mobile/admin/borderline-settings")
    def get_mobile_borderline_settings(
        authorization: str | None = Header(default=None),
    ) -> dict:
        permission_mobile_user(authorization, "can_manage_borderline_settings")
        assert isinstance(repository, SQLiteRepository)
        value = repository.get_mobile_settings()
        master_enabled = repository.get_settings().vlm.borderline_review_enabled
        return {
            "enabled": bool(value["borderline_alert_enabled"]),
            "recipientRoleIds": value["borderline_recipient_role_ids"],
            "reviewEnabled": master_enabled,
            "available": master_enabled,
            "revision": repository.get_borderline_settings_revision(),
            "roles": repository.list_borderline_role_settings(),
            "emergencyCallSimulationOnly": True,
            "updatedAt": value["updated_at"],
        }

    @app.put("/api/mobile/admin/borderline-settings/roles/{role_id}")
    def update_mobile_borderline_role(
        role_id: str,
        incoming: BorderlineRecipientUpdate,
        authorization: str | None = Header(default=None),
    ) -> dict:
        actor = permission_mobile_user(authorization, "can_manage_borderline_settings")
        assert isinstance(repository, SQLiteRepository)
        try:
            saved = repository.set_borderline_role_enabled(
                role_id, incoming.enabled, actor["id"], incoming.revision
            )
        except ValueError as exc:
            if str(exc) == "revision_conflict":
                raise HTTPException(status_code=409, detail="Borderline settings changed. Reload and retry.") from exc
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if saved is None:
            raise HTTPException(status_code=404, detail="Role not found")
        return get_mobile_borderline_settings(authorization)

    @app.put("/api/mobile/admin/borderline-settings/users/{user_id}")
    def update_mobile_borderline_user(
        user_id: str,
        incoming: BorderlineRecipientUpdate,
        authorization: str | None = Header(default=None),
    ) -> dict:
        actor = permission_mobile_user(authorization, "can_manage_borderline_settings")
        assert isinstance(repository, SQLiteRepository)
        try:
            saved = repository.set_borderline_user_enabled(
                user_id, incoming.enabled, actor["id"], incoming.revision
            )
        except ValueError as exc:
            if str(exc) == "revision_conflict":
                raise HTTPException(status_code=409, detail="Borderline settings changed. Reload and retry.") from exc
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if saved is None:
            raise HTTPException(status_code=404, detail="User not found or has no role")
        return get_mobile_borderline_settings(authorization)

    @app.put("/api/mobile/admin/borderline-settings")
    def save_mobile_borderline_settings(
        incoming: MobileBorderlineSettings,
        authorization: str | None = Header(default=None),
    ) -> dict:
        actor = permission_mobile_user(authorization, "can_manage_borderline_settings")
        assert isinstance(repository, SQLiteRepository)
        if (
            incoming.revision is not None
            and incoming.revision != repository.get_borderline_settings_revision()
        ):
            raise HTTPException(
                status_code=409,
                detail="Borderline settings changed. Reload and retry.",
            )
        if repository.has_individual_borderline_settings():
            raise HTTPException(
                status_code=409,
                detail="This legacy endpoint cannot update role settings after individual exceptions exist.",
            )
        try:
            if incoming.enabled and not incoming.recipient_role_ids:
                raise ValueError("At least one recipient role is required when enabled.")
            selected = set(incoming.recipient_role_ids if incoming.enabled else [])
            repository.set_borderline_roles_enabled(
                list(selected), actor["id"], incoming.revision
            )
        except ValueError as exc:
            if str(exc) == "revision_conflict":
                raise HTTPException(
                    status_code=409,
                    detail="Borderline settings changed. Reload and retry.",
                ) from exc
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return get_mobile_borderline_settings(authorization)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_ROOT / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
    return app


app = create_app()
