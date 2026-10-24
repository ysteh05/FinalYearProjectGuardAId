"""One bounded-memory media worker; durable backlog contains descriptors, never frames."""
from datetime import datetime, timezone
import json
import logging
from threading import Event, RLock, Thread
import time
from uuid import uuid4

LOGGER = logging.getLogger(__name__)


class MediaTaskDeferred(Exception):
    """Dependency still preparing; defer without consuming a codec retry."""


class MediaTaskStore:
    def __init__(self, repository):
        self.repository = repository
        self.persistent = hasattr(repository, '_connect')
        self.lock = RLock()
        self.memory = {}

    def enqueue(self, key, kind, payload, priority):
        encoded = json.dumps(payload, allow_nan=False, separators=(',', ':'))
        if len(encoded.encode('utf-8')) > 256 * 1024:
            raise ValueError('Media task descriptor exceeds 256 KiB')
        row = dict(id='media_' + uuid4().hex, dedup_key=key, kind=kind,
                   alert_id=payload['alert_id'], camera_id=payload['camera']['id'],
                   payload_json=encoded, priority=priority, status='queued', attempts=0,
                   available_at=0.0, created_at=datetime.now(timezone.utc).isoformat(),
                   finished_at=None, error_code=None)
        if not self.persistent:
            with self.lock:
                previous = next((r for r in self.memory.values() if r['dedup_key'] == key), None)
                if previous:
                    return previous['id'], False
                self.memory[row['id']] = row
                return row['id'], True
        with self.repository._lock, self.repository._connect() as connection:
            cursor = connection.execute('INSERT OR IGNORE INTO media_tasks(' + ','.join(row) +
                ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
            existing = connection.execute('SELECT id FROM media_tasks WHERE dedup_key=?', (key,)).fetchone()
            connection.commit()
            return existing['id'], cursor.rowcount == 1

    def recover(self):
        if self.persistent:
            with self.repository._lock, self.repository._connect() as connection:
                # Route exhausted interrupted attempts through the worker's
                # terminal callback too, so evidence cannot remain 'preparing'.
                # The worker will not run their codec a fourth time.
                connection.execute("UPDATE media_tasks SET status='queued',available_at=0, "
                                   "error_code='worker_interrupted' WHERE status='running'")
                connection.commit()

    def recover_scenes(self, pending_alert_ids):
        if not self.persistent:
            return
        with self.repository._lock, self.repository._connect() as connection:
            for alert_id in pending_alert_ids:
                connection.execute("UPDATE media_tasks SET status='queued',available_at=0,attempts=0 "
                    "WHERE alert_id=? AND kind IN ('weapon_context','final_incident') AND status='completed'", (alert_id,))
            connection.commit()

    def claim(self):
        now = time.time()
        if not self.persistent:
            with self.lock:
                rows = [r for r in self.memory.values() if r['status'] == 'queued' and r['available_at'] <= now]
                if not rows:
                    return None
                row = min(rows, key=lambda r: (r['priority'], r['created_at']))
                row.update(status='running', attempts=row['attempts'] + 1)
                return dict(row)
        with self.repository._lock, self.repository._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute("SELECT * FROM media_tasks WHERE status='queued' AND available_at<=? "
                                     'ORDER BY priority,created_at,id LIMIT 1', (now,)).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute("UPDATE media_tasks SET status='running',attempts=attempts+1 WHERE id=?", (row['id'],))
            connection.commit()
            return {**dict(row), 'status': 'running', 'attempts': row['attempts'] + 1}

    def finish(self, task, error=None):
        if isinstance(error, MediaTaskDeferred):
            changes = dict(status='queued', available_at=time.time()+.5,
                           attempts=max(0, task['attempts']-1), error_code=None, finished_at=None)
            if not self.persistent:
                with self.lock:
                    self.memory[task['id']].update(changes)
            else:
                with self.repository._lock, self.repository._connect() as connection:
                    connection.execute('UPDATE media_tasks SET ' + ','.join(f'{k}=?' for k in changes) +
                                       ' WHERE id=?', (*changes.values(),task['id']))
                    connection.commit()
            return 'queued'
        retry = error is not None and task['attempts'] < 3
        changes = dict(status='queued' if retry else 'failed' if error else 'completed',
                       error_code=type(error).__name__ if error else None,
                       available_at=time.time() + min(30, 2 ** task['attempts']) if retry else 0,
                       finished_at=None if retry else datetime.now(timezone.utc).isoformat())
        if not self.persistent:
            with self.lock:
                self.memory[task['id']].update(changes)
        else:
            with self.repository._lock, self.repository._connect() as connection:
                connection.execute('UPDATE media_tasks SET ' + ','.join(f'{k}=?' for k in changes) +
                                   ' WHERE id=?', (*changes.values(), task['id']))
                connection.commit()
        return changes['status']

    def pending_evidence(self, alert_id):
        if not self.persistent:
            with self.lock:
                return any(r['alert_id']==alert_id and r['kind']=='weapon_evidence' and
                           r['status'] in {'queued','running'} for r in self.memory.values())
        with self.repository._connect() as connection:
            return bool(connection.execute("SELECT 1 FROM media_tasks WHERE alert_id=? "
                "AND kind='weapon_evidence' AND status IN ('queued','running') LIMIT 1",(alert_id,)).fetchone())

    def pending(self, camera_id=None):
        if not self.persistent:
            with self.lock:
                return sum(r['status'] in {'queued', 'running'} and (camera_id is None or r['camera_id'] == camera_id)
                           for r in self.memory.values())
        with self.repository._connect() as connection:
            sql = "SELECT count(*) FROM media_tasks WHERE status IN ('queued','running')"
            params = ()
            if camera_id is not None:
                sql += ' AND camera_id=?'
                params = (camera_id,)
            return connection.execute(sql, params).fetchone()[0]


class MediaTaskWorker:
    def __init__(self, store, handler, on_terminal=None):
        self.store, self.handler, self.on_terminal = store, handler, on_terminal
        self.wake = Event()
        self.stopping = Event()
        self.thread = None
        self.lock = RLock()
        self.closed = False

    def start(self):
        with self.lock:
            if self.closed:
                return
            if self.thread and self.thread.is_alive():
                return
            self.store.recover()
            self.stopping.clear()
            self.thread = Thread(target=self._run, name='guardaid-media', daemon=True)
            self.thread.start()

    def notify(self):
        self.start()
        self.wake.set()

    def _run(self):
        unsettled = None
        while not self.stopping.is_set():
            try:
                if unsettled is None:
                    task = self.store.claim()
                    if task is None:
                        self.wake.wait(.5)
                        self.wake.clear()
                        continue
                    error = None
                    try:
                        if task['attempts'] > 3 and task.get('error_code') == 'worker_interrupted':
                            raise RuntimeError('Media worker interrupted during its final attempt')
                        self.handler(task, json.loads(task['payload_json']))
                    except Exception as exc:
                        error = exc
                        if not isinstance(exc, MediaTaskDeferred):
                            LOGGER.exception('Media task %s failed on attempt %s', task['id'], task['attempts'])
                    unsettled = (task, error)
                task, error = unsettled
                status = self.store.finish(task, error)
                unsettled = None
                if status != 'queued' and self.on_terminal:
                    self.on_terminal(task, status, error)
            except Exception:
                LOGGER.exception('Media task bookkeeping will retry')
                self.stopping.wait(.5)

    def stop(self):
        self.closed = True
        self.stopping.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=30)
