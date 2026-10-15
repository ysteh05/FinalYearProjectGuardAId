from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from fractions import Fraction
from itertools import count
import math
import json
import logging
from pathlib import Path
from queue import PriorityQueue
from threading import Event, RLock, Thread, Timer
import time
from typing import Any, Callable, Literal
from uuid import uuid4

from ..config import RuntimeConfig
from ..domain import (
    AnalysisDebugBox,
    AnalysisDebugEvent,
    AnalysisJob,
    AnalysisStatus,
    CameraStatus,
    EvidenceRecord,
    SensitivityMode,
    VlmSettings,
    new_id,
)
from ..media import MediaStore
from ..model_metadata import VIOLENCE_MODEL_PROTOCOL, VIOLENCE_MODEL_VERSION
from ..policy import (
    generate_segment_windows,
    violence_borderline_range,
    violence_qualified_modes,
    violence_recording_threshold,
)
from ..repository import Repository
from ..services import AlertService, NotificationService, WeaponFrameGate
from .mc3 import Mc3ViolenceRunner
from .vlm import QwenNativeVideoRunner
from .weapon import WeaponDetection, Yolov9WeaponRunner


LOGGER = logging.getLogger(__name__)


class _PriorityVlmExecutor:
    """One non-preemptive Qwen worker with borderline ahead of descriptions."""

    def __init__(self) -> None:
        self._queue: PriorityQueue = PriorityQueue()
        self._sequence = count()
        self._closed = Event()
        self._thread = Thread(target=self._work, name="guardaid-vlm", daemon=True)
        self._thread.start()

    def submit(self, priority: int, function: Callable, *args, **kwargs) -> Future:
        future = Future()
        if self._closed.is_set():
            future.cancel()
            return future
        self._queue.put((priority, next(self._sequence), future, function, args, kwargs))
        return future

    def _work(self) -> None:
        while True:
            priority, sequence, future, function, args, kwargs = self._queue.get()
            del priority, sequence
            try:
                if function is None:
                    return
                if future.set_running_or_notify_cancel():
                    try:
                        future.set_result(function(*args, **kwargs))
                    except BaseException as exc:
                        future.set_exception(exc)
            finally:
                self._queue.task_done()

    def shutdown(self) -> None:
        self._closed.set()
        self._queue.put((10_000, next(self._sequence), Future(), None, (), {}))


class InferenceOrchestrator:
    """Shared-model, decode-once pipeline for uploaded CCTV simulations."""

    def __init__(
        self,
        runtime: RuntimeConfig,
        repository: Repository,
        media_store: MediaStore,
        notification_sender,
    ) -> None:
        self.runtime = runtime
        self.repository = repository
        self.media_store = media_store
        self.alert_service = AlertService(repository)
        self.weapon_gate = WeaponFrameGate(self.alert_service, repository)
        self.notifications = NotificationService(repository, notification_sender)
        self.mc3 = Mc3ViolenceRunner(runtime.violence_checkpoint)
        self.weapon = Yolov9WeaponRunner(
            runtime.weapon_checkpoint or (runtime.data_dir / "missing-weapon-checkpoint.pt"),
            runtime.yolov9_root,
        )
        self.vlm = QwenNativeVideoRunner(runtime.hf_cache)
        self._jobs: dict[str, AnalysisJob] = {}
        self._weapon_evidence_candidates: dict[str, deque] = {}
        self._cancel: dict[str, Event] = {}
        self._evidence_timers: dict[str, Timer] = {}
        self._lock = RLock()
        self._startup_lock = RLock()
        self._feed_pool = ThreadPoolExecutor(
            max_workers=runtime.max_active_feeds, thread_name_prefix="guardaid-feed"
        )
        # Qwen is serialized on the 8 GB GPU, with borderline reviews ahead of
        # best-effort descriptions. It never occupies a detector worker.
        self._vlm_queue = _PriorityVlmExecutor()
        (runtime.data_dir / "tmp" / "vlm_context").mkdir(parents=True, exist_ok=True)
        (runtime.data_dir / "tmp" / "borderline_reviews").mkdir(parents=True, exist_ok=True)
        if hasattr(repository, "list_queued_borderline_review_tasks"):
            pending_reviews = repository.list_queued_borderline_review_tasks()
            if hasattr(repository, "list_interrupted_final_borderline_review_contexts"):
                for context_path in repository.list_interrupted_final_borderline_review_contexts():
                    self._remove_borderline_context(context_path)
            review_enabled = repository.get_settings().vlm.borderline_review_enabled
            for task in pending_reviews:
                if review_enabled:
                    self._vlm_queue.submit(1, self._run_persisted_borderline_review, task["id"], None)
                else:
                    repository.finish_borderline_review_task(task["id"], status="cancelled")
                    self._remove_borderline_context(task["context_path"])

    def reconcile_pending_borderline_notifications(self) -> int:
        """Persist eligible VLM notifications before the outbox worker starts."""
        if not hasattr(self.repository, "list_pending_borderline_notification_tasks"):
            return 0
        settings = self.repository.get_settings()
        if not settings.vlm.borderline_review_enabled:
            return 0
        alerts = {alert.id: alert for alert in self.repository.list_alerts()}
        queued = 0
        for task in self.repository.list_pending_borderline_notification_tasks():
            alert = alerts.get(task["alert_id"])
            if alert is None:
                continue
            try:
                queued += self.notifications.dispatch(
                    alert,
                    escalation_settings=settings.alert_notification_escalation,
                    borderline_review_task_id=task["id"],
                    process_pending=False,
                )
            except Exception as exc:
                # Leave the durable task eligible; the next process restart
                # can retry without changing the completed VLM conclusion.
                LOGGER.warning(
                    "Could not reconcile Borderline notification task %s (%s).",
                    task["id"],
                    type(exc).__name__,
                )
        return queued

    def readiness(self) -> dict[str, dict[str, object]]:
        threshold_found = self.runtime.weapon_thresholds_path.is_file()
        return {
            "violence": {
                "checkpointFound": self.runtime.violence_checkpoint.is_file(),
                "workerConnected": True,
                "loaded": self.mc3.is_loaded,
                "checkpoint": self.runtime.violence_checkpoint.name,
                "modelVersion": VIOLENCE_MODEL_VERSION,
                "protocol": "4 s window · 2 s stride · 16 frames at bin centres",
                "trainingProtocol": VIOLENCE_MODEL_PROTOCOL,
                "message": "Fine-tuned MC3-18 ready (4 s / 2 s / 16 frames, 112x112 crop).",
            },
            "weapon": {
                "checkpointFound": bool(
                    self.runtime.weapon_checkpoint and self.runtime.weapon_checkpoint.is_file()
                ),
                "thresholdsFound": threshold_found,
                "workerConnected": self.runtime.yolov9_root.is_dir() and threshold_found,
                "loaded": self.weapon.is_loaded,
                "modelVersion": self.weapon.MODEL_VERSION,
                "classes": list(self.weapon.EXPECTED_CLASSES),
                "checkpoint": (
                    self.runtime.weapon_checkpoint.name if self.runtime.weapon_checkpoint else None
                ),
                "message": "Selected nine-source YOLOv9-T Epoch 6 ready (Knife/Gun) with six validation-calibrated thresholds.",
            },
            "vlm": {
                "modelCacheFound": self.runtime.hf_cache.exists(),
                "workerConnected": True,
                "loaded": self.vlm.is_loaded,
                "modelId": "Qwen/Qwen3-VL-2B-Instruct",
                "message": "Qwen3-VL 2B NF4 native-video worker; concurrency is limited to one.",
            },
        }

    def list_jobs(self) -> list[AnalysisJob]:
        with self._lock:
            return sorted(
                (job.model_copy(deep=True) for job in self._jobs.values()),
                key=lambda item: item.created_at,
                reverse=True,
            )

    def get_job(self, job_id: str) -> AnalysisJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def start(self, camera_id: str) -> AnalysisJob:
        with self._startup_lock:
            camera = self.repository.get_camera(camera_id)
            if not camera:
                raise KeyError("Camera not found")
            if not camera.enabled:
                raise ValueError("Camera is disabled")
            with self._lock:
                active = [
                    job for job in self._jobs.values()
                    if job.status in {AnalysisStatus.QUEUED, AnalysisStatus.RUNNING}
                ]
                if any(job.camera_id == camera_id for job in active):
                    raise ValueError("This camera already has an active analysis job")
                if len(active) >= self.runtime.max_active_feeds:
                    raise ValueError(
                        f"At most {self.runtime.max_active_feeds} camera feeds can run concurrently"
                    )
            # One deep-copied settings object is the immutable-by-convention
            # policy snapshot for this analysis run. Later changes take effect
            # on the next run; only the Borderline master switch is re-read by
            # its optional worker before sending supplemental notifications.
            settings = self.repository.get_settings().model_copy(deep=True)
            # First-load bitsandbytes quantisation must not race active MC3/YOLO
            # CUDA inference. Preload Qwen before the feed is submitted; subsequent
            # VLM jobs reuse the resident model and remain lower-priority/asynchronous.
            if (
                settings.vlm.borderline_review_enabled
                or settings.vlm.scene_description_enabled
            ) and not self.vlm.is_loaded:
                try:
                    self.vlm.load(settings.vlm.model_id, settings.vlm.video_token_budget)
                except Exception as exc:
                    LOGGER.warning(
                        "Optional Qwen preload failed; detector startup will continue (%s).",
                        type(exc).__name__,
                    )
            # Load both alert-critical models before the browser starts playback so
            # first-use CUDA setup does not create an avoidable timeline offset.
            self.mc3.load()
            self.weapon.load()
            active = [
                job for job in self._jobs.values()
                if job.status in {AnalysisStatus.QUEUED, AnalysisStatus.RUNNING}
            ]
            if len(active) >= self.runtime.max_active_feeds:
                raise ValueError(
                    f"At most {self.runtime.max_active_feeds} camera feeds can run concurrently"
                )
            job = AnalysisJob(
                camera_id=camera.id,
                camera_name=camera.name,
                duration_seconds=camera.duration_seconds,
                debug_mode_enabled=settings.detection_debug_mode_enabled,
            )
            cancel = Event()
            self._jobs[job.id] = job
            self._cancel[job.id] = cancel
            self._feed_pool.submit(self._run, job.id, cancel)
            return job.model_copy(deep=True)

    def cancel(self, job_id: str) -> AnalysisJob:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError("Analysis job not found")
            self._cancel[job_id].set()
            return self._jobs[job_id].model_copy(deep=True)

    def _update(self, job_id: str, **changes) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)

    def _increment(self, job_id: str, **deltas: int) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in deltas.items():
                setattr(job, key, getattr(job, key) + value)

    def _record_debug_event(
        self,
        job_id: str,
        *,
        detector: Literal["violence", "weapon"],
        sensitivity_mode: SensitivityMode,
        source_seconds: float,
        window_end_seconds: float | None,
        scores: dict[str, float],
        thresholds: dict[str, float],
        positive_labels: list[str],
        status: Literal["negative", "positive", "alert", "review_queued"],
        boxes: list[AnalysisDebugBox] | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if not job.debug_mode_enabled:
                return
            sequence = job.debug_event_count + 1
            job.debug_event_count = sequence
            job.debug_events.append(
                AnalysisDebugEvent(
                    sequence=sequence,
                    detector=detector,
                    sensitivity_mode=sensitivity_mode,
                    source_seconds=source_seconds,
                    window_end_seconds=window_end_seconds,
                    scores=scores,
                    thresholds=thresholds,
                    positive_labels=positive_labels,
                    boxes=boxes or [],
                    status=status,
                )
            )
            # The browser incrementally assembles the complete log. Keep only
            # a short overlap here so frequent polling never resends the history.
            if len(job.debug_events) > 64:
                del job.debug_events[:-64]

    @staticmethod
    def _uniform_frames(ring, start: float, end: float, count: int):
        candidates = [(timestamp, frame) for timestamp, frame in ring if start - 0.08 <= timestamp <= end + 0.08]
        if not candidates:
            raise RuntimeError("No decoded frames are available for the MC3 window")
        targets = [start + (end - start) * (index + 0.5) / count for index in range(count)]
        return [min(candidates, key=lambda item: abs(item[0] - target))[1] for target in targets]

    @staticmethod
    def _buffer_frame(frame, max_long_edge: int = 960):
        """Bound ring-buffer RAM while retaining ample detail for evidence/VLM."""

        import cv2

        height, width = frame.shape[:2]
        long_edge = max(height, width)
        if long_edge <= max_long_edge:
            return frame.copy()
        scale = max_long_edge / long_edge
        return cv2.resize(
            frame,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )

    def _encode_clip(self, frames: list, fps: float) -> bytes:
        import av
        import cv2

        if not frames:
            raise ValueError("Cannot encode an empty evidence clip")
        temp = self.runtime.data_dir / "tmp" / f"clip_{uuid4().hex}.mp4"
        temp.parent.mkdir(parents=True, exist_ok=True)
        try:
            height, width = frames[0].shape[:2]
            output_width, output_height = width + (width % 2), height + (height % 2)
            rate = Fraction(max(1.0, min(float(fps), 12.0))).limit_denominator(1000)
            with av.open(
                str(temp), "w", format="mp4", options={"movflags": "+faststart"}
            ) as container:
                stream = container.add_stream("libx264", rate=rate)
                stream.width, stream.height = output_width, output_height
                stream.pix_fmt = "yuv420p"
                stream.options = {"preset": "veryfast", "crf": "23"}
                for frame in frames:
                    if frame.shape[1] != output_width or frame.shape[0] != output_height:
                        frame = cv2.copyMakeBorder(
                            frame,
                            0,
                            output_height - frame.shape[0],
                            0,
                            output_width - frame.shape[1],
                            cv2.BORDER_REPLICATE,
                        )
                    video_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
                    for packet in stream.encode(video_frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            return temp.read_bytes()
        finally:
            temp.unlink(missing_ok=True)

    def _encode_source_interval(
        self, camera, start_seconds: float, end_seconds: float, output_fps: float
    ) -> bytes:
        """Stream a continuous interval to disk without retaining all frames in RAM."""

        import cv2

        source = self.media_store.materialize(camera.storage_path)
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise RuntimeError("OpenCV could not reopen the uploaded video for evidence")
        source_fps = float(capture.get(cv2.CAP_PROP_FPS))
        if not math.isfinite(source_fps) or source_fps <= 0:
            source_fps = max(1.0, float(output_fps))
        encode_fps = max(1.0, min(float(output_fps), 12.0))
        start_frame = max(0, math.floor(start_seconds * source_fps))
        capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        frame_index = start_frame
        next_output = max(0.0, start_seconds)
        output_interval = 1.0 / encode_fps
        temp = self.runtime.data_dir / "tmp" / f"interval_{uuid4().hex}.mp4"
        temp.parent.mkdir(parents=True, exist_ok=True)
        container = None
        stream = None
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                timestamp = frame_index / source_fps
                frame_index += 1
                if timestamp > end_seconds + (1.0 / source_fps):
                    break
                if timestamp + 1e-6 < next_output:
                    continue
                buffered = self._buffer_frame(frame)
                if container is None:
                    import av

                    height, width = buffered.shape[:2]
                    output_width, output_height = width + (width % 2), height + (height % 2)
                    container = av.open(
                        str(temp), "w", format="mp4", options={"movflags": "+faststart"}
                    )
                    stream = container.add_stream(
                        "libx264", rate=Fraction(encode_fps).limit_denominator(1000)
                    )
                    stream.width, stream.height = output_width, output_height
                    stream.pix_fmt = "yuv420p"
                    stream.options = {"preset": "veryfast", "crf": "23"}
                if buffered.shape[1] != output_width or buffered.shape[0] != output_height:
                    buffered = cv2.copyMakeBorder(
                        buffered,
                        0,
                        output_height - buffered.shape[0],
                        0,
                        output_width - buffered.shape[1],
                        cv2.BORDER_REPLICATE,
                    )
                video_frame = av.VideoFrame.from_ndarray(buffered, format="bgr24")
                for packet in stream.encode(video_frame):
                    container.mux(packet)
                next_output += output_interval
                while next_output <= timestamp:
                    next_output += output_interval
        finally:
            capture.release()
            if container is not None and stream is not None:
                for packet in stream.encode():
                    container.mux(packet)
                container.close()
        try:
            if container is None or not temp.is_file():
                raise RuntimeError("No frames were available for the merged evidence clip")
            return temp.read_bytes()
        finally:
            temp.unlink(missing_ok=True)

    def _temporary_context(self, content: bytes, alert_id: str) -> Path:
        path = self.runtime.data_dir / "tmp" / "vlm_context" / f"{alert_id}_{uuid4().hex}.mp4"
        path.write_bytes(content)
        return path

    def _attach_evidence(
        self,
        alert,
        *,
        media_type: str,
        suffix: str,
        content_type: str,
        content: bytes,
        start_seconds: float,
        end_seconds: float,
    ) -> EvidenceRecord:
        stored = self.media_store.save_evidence_bytes(
            alert.id, media_type, suffix, content_type, content
        )
        evidence = EvidenceRecord(
            alert_id=alert.id,
            media_type=media_type,
            storage_path=stored.storage_path,
            content_type=stored.content_type,
            sha256=stored.sha256,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )
        self.repository.save_evidence(evidence, stored.size_bytes)
        return evidence

    def _replace_evidence(self, alert, **kwargs) -> EvidenceRecord:
        old_paths = self.repository.delete_evidence(alert.id, kwargs["media_type"])
        for storage_path in old_paths:
            self.media_store.delete(storage_path)
        return self._attach_evidence(alert, **kwargs)

    def _submit_description(self, alert_id: str, context_path: Path, settings) -> None:
        def task() -> None:
            try:
                description = self.vlm.describe_scene(context_path, settings.vlm)
                self.repository.update_description(alert_id, "ready", description)
            except Exception as exc:
                self.repository.update_description(
                    alert_id, "failed", None, type(exc).__name__
                )
            finally:
                # Only private temporary contexts are removed. Persisted evidence
                # paths live below media/evidence and are never passed here.
                if "vlm_context" in context_path.parts:
                    context_path.unlink(missing_ok=True)

        self._vlm_queue.submit(2, task)

    def _finalise_new_alert(
        self,
        alert,
        *,
        evidence_type: str,
        evidence_content: bytes | None,
        evidence_suffix: str,
        evidence_content_type: str,
        start_seconds: float,
        end_seconds: float,
        scene_context: bytes | None,
        settings,
        schedule_description: bool = True,
    ) -> bool:
        if alert.detection_count != 1:
            return False
        cooldown = (
            settings.weapon.cooldown_seconds
            if str(alert.type) == "weapon"
            else settings.violence.cooldown_seconds
        )
        self.notifications.dispatch(
            alert,
            cooldown,
            settings.alert_notification_escalation,
        )
        # Optional evidence encoding and storage happen after alert persistence
        # and notification scheduling so codec or disk failures cannot suppress
        # a detector-confirmed alert.
        if evidence_content:
            try:
                self._attach_evidence(
                    alert,
                    media_type=evidence_type,
                    suffix=evidence_suffix,
                    content_type=evidence_content_type,
                    content=evidence_content,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                )
            except Exception as exc:
                self.repository.set_alert_evidence_status(alert.id, "failed")
                LOGGER.warning(
                    "Could not persist alert evidence (%s).", type(exc).__name__
                )
        else:
            self.repository.set_alert_evidence_status(alert.id, "failed")
        if settings.vlm.scene_description_enabled and schedule_description:
            if not scene_context:
                self.repository.update_description(
                    alert.id, "failed", None, "EvidenceContextUnavailable"
                )
            else:
                try:
                    context_path = self._temporary_context(scene_context, alert.id)
                    self._submit_description(alert.id, context_path, settings)
                except Exception as exc:
                    self.repository.update_description(
                        alert.id, "failed", None, type(exc).__name__
                    )
                    LOGGER.warning(
                        "Could not queue alert scene description (%s).",
                        type(exc).__name__,
                    )
        return True

    def _persist_violence_alert(
        self,
        alert,
        camera,
        context_bytes: bytes | None,
        source_fps: float,
        settings,
        *,
        schedule_description: bool = True,
    ) -> bool:
        start = float(alert.source_start_seconds or 0.0)
        end = float(alert.source_end_seconds or start)
        if alert.detection_count == 1:
            return self._finalise_new_alert(
                alert,
                evidence_type="violence_clip",
                evidence_content=context_bytes,
                evidence_suffix=".mp4",
                evidence_content_type="video/mp4",
                start_seconds=start,
                end_seconds=end,
                scene_context=context_bytes,
                settings=settings,
                schedule_description=schedule_description,
            )

        self._schedule_evidence_merge(alert, camera, source_fps, settings)
        self.notifications.dispatch(
            alert,
            escalation_settings=settings.alert_notification_escalation,
        )
        return False

    def _schedule_evidence_merge(self, alert, camera, source_fps: float, settings) -> None:
        """Debounce merged evidence until the configured incident gap is quiet."""

        expected_end = float(alert.source_end_seconds or 0.0)
        delay = max(0.1, float(settings.violence.incident_merge_gap_seconds))

        def rebuild() -> None:
            try:
                latest = next(
                    (item for item in self.repository.list_alerts() if item.id == alert.id), None
                )
                if not latest or float(latest.source_end_seconds or 0.0) != expected_end:
                    return
                start = float(latest.source_start_seconds or 0.0)
                combined = self._encode_source_interval(
                    camera, start, expected_end, source_fps
                )
                # A newer detection may have arrived while encoding. Its own
                # timer will create the final, longer evidence; never overwrite
                # it with this stale interval.
                current = next(
                    (item for item in self.repository.list_alerts() if item.id == alert.id), None
                )
                if not current or float(current.source_end_seconds or 0.0) != expected_end:
                    return
                self._replace_evidence(
                    current,
                    media_type="violence_clip",
                    suffix=".mp4",
                    content_type="video/mp4",
                    content=combined,
                    start_seconds=start,
                    end_seconds=expected_end,
                )
                if settings.vlm.scene_description_enabled:
                    try:
                        self._submit_description(
                            current.id, self._temporary_context(combined, current.id), settings
                        )
                    except Exception as exc:
                        self.repository.update_description(
                            current.id, "failed", None, type(exc).__name__
                        )
                        LOGGER.warning(
                            "Could not queue merged scene description (%s).",
                            type(exc).__name__,
                        )
            except Exception as exc:
                LOGGER.warning(
                    "Could not rebuild merged alert evidence (%s).",
                    type(exc).__name__,
                )
            finally:
                with self._lock:
                    if self._evidence_timers.get(alert.id) is timer:
                        self._evidence_timers.pop(alert.id, None)

        with self._lock:
            previous = self._evidence_timers.pop(alert.id, None)
            if previous:
                previous.cancel()
            timer = Timer(delay, rebuild)
            timer.daemon = True
            self._evidence_timers[alert.id] = timer
            timer.start()

    def _submit_borderline_review(
        self,
        job_id: str,
        camera,
        probability: float,
        start: float,
        end: float,
        context_bytes: bytes | None,
        source_fps: float,
        settings,
        alert_id: str,
        detection_event_key: str,
        context_error: str | None = None,
    ) -> None:
        context_path = (
            self.runtime.data_dir / "tmp" / "borderline_reviews"
            / f"{detection_event_key}.mp4"
        )
        if context_error is None and context_bytes:
            try:
                context_path.parent.mkdir(parents=True, exist_ok=True)
                context_path.write_bytes(context_bytes)
            except Exception as exc:
                context_error = type(exc).__name__
        self._increment(job_id, pending_vlm_jobs=1)
        if hasattr(self.repository, "create_borderline_review_task"):
            task_id = None
            try:
                task_id = self.repository.create_borderline_review_task(
                    alert_id=alert_id,
                    camera_id=camera.id,
                    detection_event_key=detection_event_key,
                    detector_confidence=probability,
                    source_start_seconds=start,
                    source_end_seconds=end,
                    context_path=str(context_path),
                    model_version=settings.vlm.model_id,
                    prompt_version=settings.vlm.binary_prompt_version,
                    config_version=settings.config_version,
                    vlm_settings=settings.vlm.model_dump(mode="json"),
                )
                if context_error is not None or not context_bytes:
                    self.repository.finish_borderline_review_task(
                        task_id,
                        status="failed",
                        error_code=context_error or "EvidenceContextUnavailable",
                    )
                    self._remove_borderline_context(context_path)
                    self._increment(
                        job_id, pending_vlm_jobs=-1, borderline_reviews_processed=1
                    )
                    return
                self._vlm_queue.submit(
                    1, self._run_persisted_borderline_review, task_id, job_id
                )
            except Exception as exc:
                if task_id:
                    self.repository.finish_borderline_review_task(
                        task_id, status="failed", error_code=type(exc).__name__
                    )
                self._record_borderline_review_status(alert_id, "failed")
                self._remove_borderline_context(context_path)
                self._increment(
                    job_id, pending_vlm_jobs=-1, borderline_reviews_processed=1
                )
                LOGGER.warning(
                    "Could not queue a Borderline review (%s).",
                    type(exc).__name__,
                )
            return

        if context_error is not None or not context_bytes:
            self._record_borderline_review_status(alert_id, "failed")
            self._remove_borderline_context(context_path)
            self._increment(
                job_id, pending_vlm_jobs=-1, borderline_reviews_processed=1
            )
            return

        def task() -> None:
            try:
                if not self.repository.get_settings().vlm.borderline_review_enabled:
                    self._record_review_disabled(alert_id)
                    return
                review = self.vlm.review_borderline(context_path, settings.vlm)
                self._record_borderline_review_status(
                    alert_id, "positive" if review["raise_alert"] else "negative"
                )
                alert = next(
                    (item for item in self.repository.list_alerts() if item.id == alert_id),
                    None,
                )
                if alert and review["raise_alert"]:
                    self.notifications.dispatch(
                        alert,
                        escalation_settings=self.repository.get_settings().alert_notification_escalation,
                    )
            except Exception as exc:
                LOGGER.warning("Borderline review failed (%s).", type(exc).__name__)
                self._record_borderline_review_status(alert_id, "failed")
            finally:
                self._remove_borderline_context(context_path)
                self._increment(job_id, pending_vlm_jobs=-1, borderline_reviews_processed=1)

        self._vlm_queue.submit(1, task)

    def _run_persisted_borderline_review(self, task_id: str, job_id: str | None) -> None:
        task = self.repository.claim_borderline_review_task(task_id)
        if task is None:
            return
        context_path = Path(task["context_path"])
        counter_complete = True
        try:
            review_settings_snapshot = self.repository.get_settings()
            if not review_settings_snapshot.vlm.borderline_review_enabled:
                self.repository.finish_borderline_review_task(task_id, status="cancelled")
                self._remove_borderline_context(context_path)
                return
            if not context_path.is_file():
                raise FileNotFoundError("Borderline review context is missing.")
            review_settings = VlmSettings.model_validate(
                json.loads(task["vlm_settings_json"])
            )
            review = self.vlm.review_borderline(context_path, review_settings)
            promoted = bool(review["raise_alert"])
            terminal_status = "positive" if promoted else "negative"
            self.repository.finish_borderline_review_task(
                task_id,
                status=terminal_status,
                raise_alert=promoted,
                uncertainty=review["uncertainty"],
                brief_reason=review["brief_reason"],
            )
            try:
                self.repository.save_borderline_review(
                    camera_id=task["camera_id"],
                    alert_id=task["alert_id"],
                    detector_confidence=task["detector_confidence"],
                    raise_alert=promoted,
                    uncertainty=review["uncertainty"],
                    brief_reason=review["brief_reason"],
                    source_start_seconds=task["source_start_seconds"],
                    source_end_seconds=task["source_end_seconds"],
                    model_version=review_settings.model_id,
                    prompt_version=task["prompt_version"],
                    config_version=task["config_version"],
                )
            except Exception as exc:
                # The durable task row is the source of truth. Failure to
                # mirror its result into the legacy reporting table must not
                # downgrade a valid result or rerun the model.
                LOGGER.warning(
                    "Could not mirror Borderline review audit data (%s).",
                    type(exc).__name__,
                )
            alert = next(
                (item for item in self.repository.list_alerts() if item.id == task["alert_id"]),
                None,
            )
            current_settings = self.repository.get_settings()
            # A running VLM call may finish after the Web master switch is
            # turned off. Persist the result for audit, but do not send a new
            # Borderline-only notification in that case.
            if promoted and alert and current_settings.vlm.borderline_review_enabled:
                try:
                    self.notifications.dispatch(
                        alert,
                        escalation_settings=current_settings.alert_notification_escalation,
                        borderline_review_task_id=task_id,
                    )
                except Exception as exc:
                    # The positive result and eligible-delivery marker are
                    # already durable. Do not downgrade the review if outbox
                    # persistence needs to be retried after restart.
                    LOGGER.warning(
                        "Could not queue Borderline notification for task %s (%s).",
                        task_id,
                        type(exc).__name__,
                    )
            if job_id and job_id in self._jobs:
                self._update(
                    job_id,
                    latest_result=(
                        f"VLM review {task['source_start_seconds']:.1f}-"
                        f"{task['source_end_seconds']:.1f}s: "
                        f"{'raise alert' if promoted else 'do not raise'}"
                    ),
                )
            self._remove_borderline_context(context_path)
        except Exception as exc:
            attempts = int(task["attempt_count"])
            retryable = isinstance(exc, (OSError, RuntimeError, TimeoutError, ConnectionError))
            if isinstance(exc, FileNotFoundError):
                retryable = False
            retry = (
                retryable
                and attempts < 3
                and self.repository.get_settings().vlm.borderline_review_enabled
            )
            self.repository.finish_borderline_review_task(
                task_id,
                status="queued" if retry else "failed",
                error_code=type(exc).__name__,
            )
            if retry:
                counter_complete = False
                timer = Timer(
                    5 * attempts,
                    lambda: self._vlm_queue.submit(
                        1, self._run_persisted_borderline_review, task_id, job_id
                    ),
                )
                timer.daemon = True
                timer.start()
            else:
                self._remove_borderline_context(context_path)
                LOGGER.warning("Borderline review failed after retries (%s).", type(exc).__name__)
            if job_id and job_id in self._jobs:
                self._update(
                    job_id,
                    latest_result=(
                        f"VLM review {task['source_start_seconds']:.1f}-"
                        f"{task['source_end_seconds']:.1f}s failed: {type(exc).__name__}"
                    ),
                )
        finally:
            if counter_complete and job_id and job_id in self._jobs:
                self._increment(job_id, pending_vlm_jobs=-1, borderline_reviews_processed=1)

    def _record_borderline_review_status(self, alert_id: str, status: str) -> None:
        alert = next((item for item in self.repository.list_alerts() if item.id == alert_id), None)
        if alert is not None and alert.borderline_review_status != "positive":
            self.repository.save_alert(alert.model_copy(update={"borderline_review_status": status}))

    def _record_review_disabled(self, alert_id: str) -> None:
        self._record_borderline_review_status(alert_id, "disabled")

    def _remove_borderline_context(self, context_path: str | Path) -> None:
        review_root = (self.runtime.data_dir / "tmp" / "borderline_reviews").resolve()
        try:
            candidate = Path(context_path).resolve()
            candidate.relative_to(review_root)
            if candidate.is_file():
                candidate.unlink()
        except (OSError, ValueError) as exc:
            LOGGER.warning(
                "Could not remove a private Borderline context (%s).",
                type(exc).__name__,
            )

    def _process_violence_window(
        self, job_id: str, camera, ring, window, source_fps: float, settings
    ) -> None:
        start, end = window
        sampled = self._uniform_frames(
            ring, start, end, settings.violence.detector_input_frames
        )
        probability = self.mc3.infer(sampled)
        job = self._jobs[job_id]
        self._update(
            job_id,
            violence_windows_processed=job.violence_windows_processed + 1,
            latest_result=f"MC3 {start:.1f}-{end:.1f}s: {probability:.4f}",
        )
        recording_floor = violence_recording_threshold(settings.violence)
        borderline_floor, borderline_ceiling = violence_borderline_range(settings.violence)
        if probability < recording_floor:
            self._record_debug_event(
                job_id,
                detector="violence",
                sensitivity_mode=SensitivityMode.SENSITIVE,
                source_seconds=start,
                window_end_seconds=end,
                scores={"Fight": probability},
                thresholds={"recording_floor": recording_floor},
                positive_labels=[],
                status="negative",
            )
            return

        borderline = borderline_floor <= probability < borderline_ceiling
        context_frames = [frame for timestamp, frame in ring if start - 0.08 <= timestamp <= end + 0.08]
        context_bytes = None
        context_error = None
        try:
            context_bytes = self._encode_clip(context_frames, min(source_fps, 20.0))
        except Exception as exc:
            context_error = type(exc).__name__
            LOGGER.warning(
                "Could not encode alert context (%s); keeping the detector alert.",
                context_error,
            )
        alert = self.alert_service.record_violence_candidate(
            camera,
            probability,
            source_start_seconds=start,
            source_end_seconds=end,
            incident_not_before=self._jobs[job_id].started_at,
            settings=settings,
        )
        if alert is None:
            return
        created = self._persist_violence_alert(alert, camera, context_bytes, source_fps, settings)
        if borderline and settings.vlm.borderline_review_enabled:
            self._submit_borderline_review(
                job_id, camera, probability, start, end, context_bytes,
                source_fps, settings, alert.id, alert.detection_event_key,
                context_error=context_error,
            )
        elif borderline:
            self._record_review_disabled(alert.id)
        if created:
            self._increment(job_id, alerts_created=1)
        self._record_debug_event(
            job_id,
            detector="violence",
            sensitivity_mode=SensitivityMode.SENSITIVE,
            source_seconds=start,
            window_end_seconds=end,
            scores={"Fight": probability},
            thresholds={
                "recording_floor": recording_floor,
                "sensitive": settings.violence.sensitive_threshold,
                "moderate": settings.violence.moderate_threshold,
                "certain": settings.violence.certain_threshold,
            },
            positive_labels=["Fight"],
            status=(
                "review_queued"
                if borderline and settings.vlm.borderline_review_enabled
                else "alert" if created else "positive"
            ),
        )

    def _process_weapon_frame(
        self, job_id: str, camera, frame, source_seconds: float, ring, source_fps: float, settings
    ) -> None:
        profile = {
            class_name: min(
                getattr(getattr(settings.weapon.class_thresholds, mode), class_name)
                for mode in ("sensitive", "moderate", "certain")
            )
            for class_name in self.weapon.EXPECTED_CLASSES
        }
        detections = self.weapon.infer(
            frame,
            profile,
            candidate_floor=settings.weapon.nms_candidate_floor,
            iou_threshold=settings.weapon.nms_iou_threshold,
            include_below_threshold=True,
        )
        class_scores = {
            name: max(
                item.confidence for item in detections if item.class_name == name
            )
            for name in self.weapon.EXPECTED_CLASSES
            if any(item.class_name == name for item in detections)
        }
        qualifying_detections = [
            item for item in detections if item.confidence >= profile[item.class_name]
        ]
        screenshot_bytes = None
        if qualifying_detections:
            import cv2

            try:
                annotated = self.weapon.annotate(frame, qualifying_detections)
                ok, encoded = cv2.imencode(
                    ".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 92]
                )
                if not ok:
                    raise RuntimeError("OpenCV could not encode weapon evidence")
                screenshot_bytes = encoded.tobytes()
            except Exception as exc:
                LOGGER.warning(
                    "Could not encode weapon evidence (%s); keeping the detector result.",
                    type(exc).__name__,
                )
        candidate_frames = self._weapon_evidence_candidates.get(camera.id)
        if candidate_frames is None or candidate_frames.maxlen != settings.weapon.m_window:
            candidate_frames = deque(
                candidate_frames or (), maxlen=settings.weapon.m_window
            )
            self._weapon_evidence_candidates[camera.id] = candidate_frames
        candidate_frames.append((source_seconds, screenshot_bytes))
        evidence_times = [
            evidence_seconds
            for evidence_seconds, content in candidate_frames
            if content is not None
        ]
        confidence = max(class_scores.values(), default=0.0)
        alert = self.weapon_gate.observe(
            camera,
            confidence,
            source_seconds=source_seconds,
            source_start_seconds=min(evidence_times) if evidence_times else source_seconds,
            model_version=self.weapon.MODEL_VERSION,
            incident_not_before=self._jobs[job_id].started_at,
            class_scores=class_scores,
            settings=settings,
        )
        current = self._jobs[job_id]
        label = ", ".join(
            f"{item.class_name} {item.confidence:.2f}" for item in qualifying_detections
        ) or "no weapon"
        self._update(
            job_id,
            weapon_frames_processed=current.weapon_frames_processed + 1,
            latest_result=f"YOLO {source_seconds:.1f}s: {label}",
        )
        created = False
        if alert and alert.detection_count == 1:
            context_bytes = None
            if settings.vlm.scene_description_enabled:
                context_frames = [
                    item for timestamp, item in ring
                    if timestamp >= source_seconds - 3.0
                ]
                if not context_frames:
                    context_frames = [frame]
                try:
                    context_bytes = self._encode_clip(
                        context_frames, min(source_fps, 20.0)
                    )
                except Exception as exc:
                    LOGGER.warning(
                        "Could not encode weapon scene context (%s).",
                        type(exc).__name__,
                    )
            created = self._finalise_new_alert(
                alert,
                evidence_type="weapon_screenshot",
                evidence_content=None,
                evidence_suffix=".jpg",
                evidence_content_type="image/jpeg",
                start_seconds=source_seconds,
                end_seconds=source_seconds,
                scene_context=context_bytes,
                settings=settings,
            )
            if created:
                self._update(job_id, alerts_created=current.alerts_created + 1)
                saved_evidence = 0
                for evidence_seconds, content in candidate_frames:
                    if content is None:
                        continue
                    try:
                        self._attach_evidence(
                            alert,
                            media_type="weapon_screenshot",
                            suffix=".jpg",
                            content_type="image/jpeg",
                            content=content,
                            start_seconds=evidence_seconds,
                            end_seconds=evidence_seconds,
                        )
                        saved_evidence += 1
                    except Exception as exc:
                        self.repository.set_alert_evidence_status(alert.id, "failed")
                        LOGGER.warning(
                            "Could not persist initial weapon evidence (%s).",
                            type(exc).__name__,
                        )
                if not saved_evidence:
                    self.repository.set_alert_evidence_status(alert.id, "failed")
        elif alert:
            if screenshot_bytes:
                try:
                    self._attach_evidence(
                        alert,
                        media_type="weapon_screenshot",
                        suffix=".jpg",
                        content_type="image/jpeg",
                        content=screenshot_bytes,
                        start_seconds=source_seconds,
                        end_seconds=source_seconds,
                    )
                except Exception as exc:
                    self.repository.set_alert_evidence_status(alert.id, "failed")
                    LOGGER.warning(
                        "Could not persist additional weapon evidence (%s).",
                        type(exc).__name__,
                    )
            self.notifications.dispatch(
                alert,
                escalation_settings=settings.alert_notification_escalation,
            )

        if self._jobs[job_id].debug_mode_enabled:
            positive_labels = sorted({item.class_name for item in qualifying_detections})
            debug_status = "alert" if alert else "positive" if positive_labels else "negative"
            frame_height, frame_width = frame.shape[:2]
            debug_boxes = [
                AnalysisDebugBox(
                    class_name=item.class_name,
                    confidence=item.confidence,
                    threshold=item.threshold,
                    x1=max(0.0, min(1.0, item.xyxy[0] / frame_width)),
                    y1=max(0.0, min(1.0, item.xyxy[1] / frame_height)),
                    x2=max(0.0, min(1.0, item.xyxy[2] / frame_width)),
                    y2=max(0.0, min(1.0, item.xyxy[3] / frame_height)),
                )
                for item in sorted(detections, key=lambda item: item.confidence, reverse=True)[:12]
            ]
            self._record_debug_event(
                job_id,
                detector="weapon",
                sensitivity_mode=SensitivityMode.SENSITIVE,
                source_seconds=source_seconds,
                window_end_seconds=None,
                scores=class_scores,
                thresholds={
                    f"{mode}.{class_name}": getattr(
                        getattr(settings.weapon.class_thresholds, mode), class_name
                    )
                    for mode in ("sensitive", "moderate", "certain")
                    for class_name in self.weapon.EXPECTED_CLASSES
                },
                positive_labels=positive_labels,
                status=debug_status,
                boxes=debug_boxes,
            )

    def _run(self, job_id: str, cancel: Event) -> None:
        import cv2

        started = time.perf_counter()
        camera = self.repository.get_camera(self._jobs[job_id].camera_id)
        if not camera:
            self._update(job_id, status=AnalysisStatus.FAILED, error="Camera not found")
            return
        self.weapon_gate.reset(camera.id)
        self._weapon_evidence_candidates.pop(camera.id, None)
        analysing = camera.model_copy(
            update={"status": CameraStatus.ANALYSING, "updated_at": datetime.now(timezone.utc)}
        )
        self.repository.save_camera(analysing)
        self._update(
            job_id,
            status=AnalysisStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        capture = None
        try:
            source = self.media_store.materialize(camera.storage_path)
            settings = self.repository.get_settings()
            capture = cv2.VideoCapture(str(source))
            if not capture.isOpened():
                raise RuntimeError("OpenCV could not open the uploaded video")
            source_fps = capture.get(cv2.CAP_PROP_FPS)
            if not math.isfinite(source_fps) or source_fps <= 0:
                source_fps = 25.0
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = camera.duration_seconds or (frame_count / source_fps if frame_count else 0)
            if duration <= 0:
                raise RuntimeError("The video duration could not be determined")
            self._update(job_id, duration_seconds=duration)
            detector_segment = settings.violence.segment_seconds
            detector_stride = settings.violence.stride_seconds
            windows = generate_segment_windows(
                duration,
                detector_segment,
                detector_stride,
                include_partial_tail=True,
            )
            window_index = 0
            next_weapon_sample = 0.0
            weapon_interval = 1.0 / settings.weapon.sample_fps
            buffer_fps = min(source_fps, 12.0)
            buffer_interval = 1.0 / buffer_fps
            next_buffer_sample = 0.0
            ring = deque()
            frame_index = 0
            last_timestamp = 0.0
            playback_started = time.perf_counter()
            while not cancel.is_set():
                ok, frame = capture.read()
                if not ok:
                    break
                timestamp = frame_index / source_fps
                # Uploaded footage is a CCTV simulation: never analyse a frame
                # before the browser should have reached that point in playback.
                wait_seconds = playback_started + timestamp - time.perf_counter()
                if wait_seconds > 0 and cancel.wait(wait_seconds):
                    break
                last_timestamp = timestamp
                frame_index += 1
                if timestamp + 1e-6 >= next_buffer_sample:
                    ring.append((timestamp, self._buffer_frame(frame)))
                    next_buffer_sample += buffer_interval
                    while next_buffer_sample <= timestamp:
                        next_buffer_sample += buffer_interval
                keep_after = timestamp - detector_segment - 1.0
                while ring and ring[0][0] < keep_after:
                    ring.popleft()

                if timestamp + 1e-6 >= next_weapon_sample:
                    self._process_weapon_frame(
                        job_id, camera, frame, timestamp, ring, buffer_fps, settings
                    )
                    next_weapon_sample += weapon_interval
                    while next_weapon_sample <= timestamp:
                        next_weapon_sample += weapon_interval

                while window_index < len(windows) and timestamp + (1.0 / source_fps) >= windows[window_index][1]:
                    self._process_violence_window(
                        job_id, camera, ring, windows[window_index], buffer_fps, settings
                    )
                    window_index += 1

                progress = min(99.5, timestamp / duration * 100)
                elapsed = time.perf_counter() - started
                eta = elapsed * (100 - progress) / progress if progress > 0 else None
                self._update(
                    job_id,
                    source_seconds=timestamp,
                    progress_percent=progress,
                    eta_seconds=max(0.0, eta) if eta is not None else None,
                )

            while not cancel.is_set() and window_index < len(windows):
                self._process_violence_window(
                    job_id, camera, ring, windows[window_index], buffer_fps, settings
                )
                window_index += 1

            status = AnalysisStatus.CANCELLED if cancel.is_set() else AnalysisStatus.COMPLETED
            pending = self._jobs[job_id].pending_vlm_jobs
            self._update(
                job_id,
                status=status,
                progress_percent=100 if status == AnalysisStatus.COMPLETED else self._jobs[job_id].progress_percent,
                source_seconds=min(duration, last_timestamp),
                eta_seconds=0,
                latest_result=(
                    f"Base detectors complete; {pending} VLM review(s) still queued."
                    if status == AnalysisStatus.COMPLETED and pending
                    else self._jobs[job_id].latest_result
                ),
                finished_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            self._update(
                job_id,
                status=AnalysisStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                eta_seconds=0,
                finished_at=datetime.now(timezone.utc),
            )
        finally:
            self._weapon_evidence_candidates.pop(camera.id, None)
            if capture is not None:
                capture.release()
            ready_camera = self.repository.get_camera(camera.id)
            if ready_camera:
                final_status = (
                    CameraStatus.ERROR
                    if self._jobs[job_id].status == AnalysisStatus.FAILED
                    else CameraStatus.READY
                )
                self.repository.save_camera(
                    ready_camera.model_copy(
                        update={
                            "status": final_status,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    )
                )

    def shutdown(self) -> None:
        with self._lock:
            for cancel in self._cancel.values():
                cancel.set()
            for timer in self._evidence_timers.values():
                timer.cancel()
            self._evidence_timers.clear()
        self._feed_pool.shutdown(wait=False, cancel_futures=True)
        self._vlm_queue.shutdown()
