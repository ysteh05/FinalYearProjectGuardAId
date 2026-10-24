"""Bounded, JSON-safe pipeline timing samples (seconds in, milliseconds out)."""
from collections import OrderedDict, defaultdict, deque
from contextlib import contextmanager
import math
from threading import Lock
import time


class PipelineTiming:
    def __init__(self):
        self.lock = Lock()
        self.jobs = OrderedDict()

    def record(self, job_id, stage, seconds):
        if not math.isfinite(seconds) or seconds < 0:
            return
        with self.lock:
            if job_id not in self.jobs:
                self.jobs[job_id] = defaultdict(lambda: deque(maxlen=512))
                while len(self.jobs) > 64:
                    self.jobs.popitem(last=False)
            self.jobs[job_id][stage].append(seconds * 1000)

    @contextmanager
    def measure(self, job_id, stage):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.record(job_id, stage, time.perf_counter() - start)

    def snapshot(self, job_id):
        with self.lock:
            result = {}
            for stage, values in self.jobs.get(job_id, {}).items():
                data = sorted(values)
                result[stage] = {name: round(data[min(len(data)-1, math.ceil(p*len(data))-1)], 3)
                                 for name, p in (('p50Ms', .5), ('p95Ms', .95), ('p99Ms', .99))}
                result[stage].update(samples=len(data), maxMs=round(data[-1], 3))
            return result
