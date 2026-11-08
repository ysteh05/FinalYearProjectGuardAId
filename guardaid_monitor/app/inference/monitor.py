"""Small, timestamped monitor stream, independent of optional debug logging."""
from collections import deque
from itertools import islice
import json
import math
import time

from ..policy import n_of_m_trigger

MODES = ("sensitive", "moderate", "certain")
PLAYBACK_DELAY_SECONDS = 2.0


class MonitorTimeline:
    def __init__(self, settings, archive_path=None, sync_playback=False):
        self.settings = settings.model_copy(deep=True)
        self.started = None
        self.events = deque(maxlen=256)
        self.sequence = 0
        self.weapon_scores = deque(maxlen=settings.weapon.m_window)
        self.levels = {"violence": 0, "weapon": 0}
        self.archive_path = archive_path
        self.sync_playback = sync_playback
        self.player_time = 0.0
        self.ready_through = 0.0
        if archive_path is not None:
            archive_path.parent.mkdir(parents=True, exist_ok=True)

    def start(self, now=None):
        self.started = time.perf_counter() if now is None else now

    def append(self, detector, source_seconds, *, probability=None, detections=(), shape=None):
        if not math.isfinite(source_seconds) or source_seconds < 0:
            raise ValueError("Non-finite or negative monitor source timestamp")
        s = self.settings
        boxes = []
        if detector == "violence":
            if probability is None or not math.isfinite(probability):
                raise ValueError("Non-finite monitor violence probability")
            level = max((i for i, mode in enumerate(MODES, 1)
                         if probability >= getattr(s.violence, f"{mode}_threshold")), default=0)
            alarm_level = level
            interval = s.violence.stride_seconds
        else:
            scores = {}
            height, width = shape[:2]
            for d in detections:
                if not math.isfinite(d.confidence):
                    raise ValueError("Non-finite monitor weapon confidence")
                scores[d.class_name] = max(scores.get(d.class_name, 0), d.confidence)
                level = max((i for i, mode in enumerate(MODES, 1)
                             if d.confidence >= getattr(getattr(s.weapon.class_thresholds, mode), d.class_name)), default=0)
                if level:
                    boxes.append(dict(className=d.class_name, confidence=d.confidence, level=level,
                                      x1=max(0, min(1, d.xyxy[0] / width)), y1=max(0, min(1, d.xyxy[1] / height)),
                                      x2=max(0, min(1, d.xyxy[2] / width)), y2=max(0, min(1, d.xyxy[3] / height))))
            self.weapon_scores.append(scores)
            confirmed = {name: max((i for i, mode in enumerate(MODES, 1)
                               if n_of_m_trigger(
                                   [row.get(name, -1) for row in self.weapon_scores],
                                   getattr(getattr(s.weapon.class_thresholds, mode), name),
                                   s.weapon.n_required, s.weapon.m_window)), default=0)
                         for name in ("knife", "gun")}
            alarm_level = max(confirmed.values(), default=0)
            boxes = [box for box in boxes if confirmed[box["className"]] > 0]
            level = max((b["level"] for b in boxes), default=0)
            interval = 1 / s.weapon.sample_fps
        self.sequence += 1
        event = dict(sequence=self.sequence, detector=detector, sourceSeconds=source_seconds,
                     validUntilSeconds=source_seconds + interval, probability=probability,
                     level=level, alarmLevel=alarm_level,
                     alarm=alarm_level > self.levels[detector], boxes=boxes)
        self.levels[detector] = alarm_level
        if self.archive_path is not None:
            with self.archive_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(event, allow_nan=False, separators=(",", ":")) + "\n")
        self.events.append(event)
        return event

    def snapshot(self, after=0, duration=None):
        elapsed = None if self.started is None else max(0, time.perf_counter() - self.started)
        available = 0.0 if elapsed is None else min(elapsed, duration) if duration is not None else elapsed
        return dict(elapsedSeconds=elapsed, availableSourceSeconds=available,
                    playbackClock='independent_source',
                    playbackDelaySeconds=PLAYBACK_DELAY_SECONDS, sampleFps=self.settings.weapon.sample_fps,
                    firstWindowSeconds=self.settings.violence.segment_seconds,
                    syncPlayback=self.sync_playback, playerTime=self.player_time,
                    latestSequence=self.sequence,
                    events=[dict(e) for e in self.events if e["sequence"] > after])

    def acknowledge_player(self, seconds):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Invalid playback position")
        elapsed = 0 if self.started is None else time.perf_counter() - self.started
        self.player_time = max(self.player_time, min(seconds, max(0, elapsed - PLAYBACK_DELAY_SECONDS)))

    def can_release(self, seconds):
        # CCTV arrival follows wall time, never the browser's playback position.
        # Slow inference catches up sequentially from the retained source file.
        elapsed = 0 if self.started is None else time.perf_counter() - self.started
        return seconds <= elapsed + 1e-6

    def history(self, after=0, limit=1000):
        if self.archive_path is None or not self.archive_path.is_file():
            return [dict(e) for e in self.events if e["sequence"] > after][:limit]
        with self.archive_path.open(encoding="utf-8") as source:
            return [json.loads(line) for line in islice(source, after, after + limit)]
