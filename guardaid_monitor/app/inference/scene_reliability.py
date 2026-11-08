"""Bounded scene retries; diagnostics never contain image bytes or credentials."""
from __future__ import annotations

import json
import math
from pathlib import Path
import socket
from urllib.error import HTTPError, URLError

MAX_ATTEMPTS = 3
WALL_BUDGET_SECONDS = 180
POLICY_VERSION = "scene-reliability-v2-length-fallback"


class SceneFormatError(ValueError):
    pass


class SceneLengthError(SceneFormatError):
    """A structurally valid complete answer exceeds preferred prose lengths."""


class SceneOutputTruncated(ValueError):
    pass


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def write_diagnostic(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(json_safe(value), ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def classify(error, attempt):
    """Only explicitly transient transport/service failures are retryable."""
    message = str(error)
    if isinstance(error, HTTPError):
        try:
            message += ': ' + error.read(8192).decode('utf-8', errors='replace')
        except Exception:
            pass
    lower = message.lower()
    if any(word in lower for word in ('out of memory', 'cuda oom', 'context length', 'context window', 'context capacity')):
        return 'context_capacity' if 'context' in lower else 'model_out_of_memory', False, 0, message
    if isinstance(error, SceneOutputTruncated):
        return 'output_truncated', True, 0, message
    if isinstance(error, SceneFormatError):
        return 'output_format', True, 0, message
    if isinstance(error, HTTPError):
        retry = error.code in {429, 502, 503, 504}
        delay = 1 if attempt == 1 else 3
        if error.code == 429 and error.headers:
            from datetime import datetime, timezone
            from email.utils import parsedate_to_datetime
            raw = error.headers.get('Retry-After')
            try:
                delay = max(delay, float(raw))
            except (ValueError, TypeError):
                try:
                    delay = max(delay, (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        return f'ollama_http_{error.code}', retry, delay, message
    if isinstance(error, (URLError, TimeoutError, socket.timeout, ConnectionError)):
        return 'ollama_unavailable', True, 1 if attempt == 1 else 3, message
    return 'evidence_invalid' if isinstance(error, (ValueError, OSError)) else 'scene_generation_failed', False, 0, message
