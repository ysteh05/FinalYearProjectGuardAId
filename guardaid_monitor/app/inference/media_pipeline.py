"""Persist small source descriptors; encode evidence outside the detector loop."""
from pathlib import Path
import time

from ..domain import Camera, EvidenceRecord, SystemSettings
from ..media_tasks import MediaTaskDeferred
from .weapon import WeaponDetection


class BackgroundMediaPipeline:
    def _get_alert(self, alert_id):
        if hasattr(self.repository, 'get_alert'):
            return self.repository.get_alert(alert_id)
        return next((a for a in self.repository.list_alerts() if a.id == alert_id), None)

    def _async_media_enabled(self, job_id):
        # Direct/offline processing helpers may execute synchronously. Every
        # running source-clock job uses the durable media worker.
        return hasattr(self, '_media_worker') and str(self._jobs[job_id].status) == 'running'

    def has_pending_media(self, camera_id):
        return bool(self._media_store.pending(camera_id))

    def _queue_media(self, kind, alert, camera, settings, job_id, *, key, **metadata):
        payload = dict(alert_id=alert.id, camera=camera.model_dump(mode='json'),
                       settings=settings.model_dump(mode='json'), job_id=job_id,
                       source_fps=self._source_fps.get(job_id, 12 if kind == 'final_incident' else 1 if kind == 'weapon_context' else 0), **metadata)
        if payload['source_fps'] <= 0:
            raise ValueError('Media work requires the decoded source FPS')
        task_id, created = self._media_store.enqueue(key, kind, payload,
            {'violence_context': 0, 'weapon_context': 2, 'weapon_evidence': 1, 'final_incident': 2}[kind])
        if created and job_id in self._jobs:
            self._increment(job_id, pending_media_jobs=1)
        self._media_worker.notify()
        return task_id

    def _source_frames(self, camera, indices, *, buffered):
        import cv2
        targets = sorted(set(int(i) for i in indices))
        if not targets or targets[0] < 0:
            raise ValueError('Empty or invalid source frame indices')
        capture = self._open_source_capture(self.media_store.materialize(camera.storage_path), background=True)
        try:
            if not capture.isOpened():
                raise RuntimeError('Could not reopen evidence source')
            capture.set(cv2.CAP_PROP_POS_FRAMES, targets[0])
            position = int(round(capture.get(cv2.CAP_PROP_POS_FRAMES)))
            if position > targets[0] or position < 0:
                capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                position = 0
            for target in targets:
                while position < target:
                    if not capture.grab():
                        raise RuntimeError('Evidence source ended before the registered frame')
                    position += 1
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError('Registered evidence frame could not be decoded')
                position += 1
                yield self._buffer_frame(frame) if buffered else frame
        finally:
            capture.release()

    def _track_weapon_scene(self, alert, camera, settings, job_id):
        if not settings.vlm.scene_description_enabled:
            return
        with self._lock:
            if not hasattr(self, '_pending_weapon_scenes'):
                self._pending_weapon_scenes = {}
            self._pending_weapon_scenes[alert.id] = dict(camera=camera,settings=settings,job_id=job_id,
                end_seconds=float(alert.source_end_seconds or 0))

    def _finalise_weapon_incidents(self, job_id, *, next_source_seconds=None, finished=False):
        with self._lock:
            for alert_id,pending in list(getattr(self,'_pending_weapon_scenes',{}).items()):
                if pending['job_id'] != job_id:
                    continue
                weapon=pending['settings'].weapon
                span=(weapon.m_window-1)/weapon.sample_fps
                if not finished and (next_source_seconds is None or
                        next_source_seconds-span <= pending['end_seconds']+weapon.incident_merge_gap_seconds):
                    continue
                if alert_id not in self._scene_tasks:
                    alert=self._get_alert(alert_id)
                    if alert is not None:
                        self._queue_media('weapon_context',alert,pending['camera'],pending['settings'],job_id,
                                          key=f'{alert_id}:weapon_context')
                    self._scene_tasks.add(alert_id)
                self._pending_weapon_scenes.pop(alert_id,None)

    def _attach_task_evidence(self, task, alert, *, content, media_type, suffix, content_type, start, end, raw_content=None):
        evidence_id = 'evidence_' + task['id']
        existing = next((e for e in alert.evidence if e.id == evidence_id), None)
        if existing:
            if raw_content is not None:
                self.media_store.save_unannotated_evidence(existing.storage_path, raw_content)
            return
        if hasattr(self.media_store, 'save_task_evidence'):
            stored = self.media_store.save_task_evidence(alert.id, task['id'], suffix, content_type, content)
        else:
            stored = self.media_store.save_evidence_bytes(alert.id, media_type, suffix, content_type, content)
        if raw_content is not None:
            self.media_store.save_unannotated_evidence(stored.storage_path, raw_content)
        self.repository.save_evidence(EvidenceRecord(id=evidence_id, alert_id=alert.id,
            media_type=media_type, storage_path=stored.storage_path, content_type=content_type,
            sha256=stored.sha256, start_seconds=start, end_seconds=end), stored.size_bytes)

    def _handle_media(self, task, payload):
        import cv2
        started = time.perf_counter()
        alert = self._get_alert(payload['alert_id'])
        if alert is None:
            return
        camera = Camera.model_validate(payload['camera'])
        settings = SystemSettings.model_validate(payload['settings'])
        job_id, kind = payload.get('job_id'), task['kind']
        try:
            if kind == 'weapon_evidence':
                frame = next(self._source_frames(camera, [payload['frame_index']], buffered=False))
                raw_ok, raw_encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if not raw_ok:
                    raise RuntimeError('Could not encode unannotated weapon evidence')
                detections = [WeaponDetection(**d) for d in payload['detections']]
                image = self.weapon.annotate(frame, detections)
                ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if not ok:
                    raise RuntimeError('Could not encode weapon evidence')
                self._attach_task_evidence(task, alert, content=encoded.tobytes(), media_type='weapon_screenshot',
                    suffix='.jpg', content_type='image/jpeg', start=payload['seconds'], end=payload['seconds'],
                    raw_content=raw_encoded.tobytes())
            elif kind == 'weapon_context':
                if self._media_store.pending_evidence(alert.id):
                    raise MediaTaskDeferred('Annotated evidence is still preparing')
                evidence=sorted((e for e in alert.evidence if e.media_type=='weapon_screenshot'),
                    key=lambda e:(float(e.start_seconds or 0),e.created_at,e.id))[:8]
                if not evidence:
                    raise ValueError('No annotated weapon evidence photographs are available')
                self._submit_description(alert.id,None,settings,job_id=job_id,weapon_evidence=evidence)
            elif kind == 'violence_context':
                needs_review = payload.get('needs_review', False) and self._review_needed(alert.id)
                if kind == 'violence_context' and not payload['initial'] and not needs_review:
                    return
                indices = [round(t * payload['source_fps']) for t in payload['frame_times']]
                content = self._encode_clip(self._source_frames(camera, indices, buffered=True), payload['encode_fps'])
                if payload['initial']:
                    self._attach_task_evidence(task, alert, content=content, media_type='violence_clip', suffix='.mp4',
                        content_type='video/mp4', start=payload['start'], end=payload['end'])
                if needs_review and self._review_needed(alert.id):
                    self._submit_borderline_review(job_id, camera, payload['probability'], payload['start'],
                        payload['end'], content, payload['encode_fps'], settings, alert.id, payload['event_key'])
            elif kind == 'final_incident':
                self._complete_violence_incident(alert.id, camera, payload['encode_fps'], settings, job_id,
                    media_task=task)
        finally:
            self._timing.record(job_id, 'media', time.perf_counter() - started)

    def _media_terminal(self, task, status, error):
        import json
        payload = json.loads(task['payload_json'])
        job_id = payload.get('job_id')
        if job_id in self._jobs:
            with self._lock:
                job = self._jobs[job_id]
                job.pending_media_jobs = max(0, job.pending_media_jobs - 1)
                if status == 'failed':
                    job.latest_result = 'Evidence processing failed; see background media task status.'
        if status == 'failed':
            if task['kind'] in {'weapon_evidence','final_incident'} or payload.get('initial'):
                self.repository.set_alert_evidence_status(task['alert_id'], 'failed')
            if task['kind'] == 'violence_context' and payload.get('needs_review') and self._review_needed(task['alert_id']):
                camera = Camera.model_validate(payload['camera'])
                settings = SystemSettings.model_validate(payload['settings'])
                self._submit_borderline_review(job_id, camera, payload['probability'], payload['start'],
                    payload['end'], None, payload['encode_fps'], settings, task['alert_id'], payload['event_key'],
                    context_error=type(error).__name__)
            if task['kind'] in {'weapon_context', 'final_incident'}:
                self.repository.update_description(task['alert_id'], 'failed', None, type(error).__name__)

    def _review_needed(self, alert_id):
        if hasattr(self.repository, 'borderline_review_needed'):
            return self.repository.borderline_review_needed(alert_id)
        alert = self._get_alert(alert_id)
        return bool(alert and str(alert.status) not in {'dismissed', 'resolved'} and
                    not alert.is_borderline_promoted and
                    'certain' not in alert.detection_qualifications.get('violence', []))
