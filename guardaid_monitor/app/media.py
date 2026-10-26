from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import shutil
import subprocess
import threading
from typing import BinaryIO, Protocol
from uuid import uuid4

from .config import RuntimeConfig


ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


@dataclass(frozen=True, slots=True)
class StoredMedia:
    storage_path: str
    size_bytes: int
    content_type: str
    sha256: str | None = None


class MediaStore(Protocol):
    def save_camera_video(
        self,
        camera_id: str,
        original_filename: str,
        content_type: str,
        stream: BinaryIO,
    ) -> StoredMedia: ...

    def save_evidence_bytes(
        self,
        alert_id: str,
        evidence_kind: str,
        suffix: str,
        content_type: str,
        content: bytes,
    ) -> StoredMedia: ...

    def delete(self, storage_path: str) -> None: ...
    def materialize(self, storage_path: str) -> Path: ...
    def materialize_browser_playback(self, storage_path: str) -> Path: ...


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_VIDEO_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_VIDEO_SUFFIXES))
        raise ValueError(f"Unsupported video type. Allowed extensions: {allowed}")
    return suffix


class LocalMediaStore:
    def __init__(self, config: RuntimeConfig) -> None:
        self._root = (config.data_dir / "media").resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_bytes = config.max_upload_mb * 1024 * 1024
        self._preview_lock_guard = threading.Lock()
        self._preview_locks: dict[str, threading.Lock] = {}

    def _local_path(self, storage_path: str) -> Path:
        relative = Path(storage_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid local media path")
        candidate = (self._root / relative).resolve()
        if self._root not in candidate.parents:
            raise ValueError("Unsafe media path")
        return candidate

    def save_camera_video(
        self,
        camera_id: str,
        original_filename: str,
        content_type: str,
        stream: BinaryIO,
    ) -> StoredMedia:
        suffix = _safe_suffix(original_filename)
        storage_path = f"camera_sources/{camera_id}/{uuid4().hex}{suffix}"
        destination = self._local_path(storage_path)
        destination.parent.mkdir(parents=True, exist_ok=True)

        size = 0
        try:
            with destination.open("wb") as output:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise ValueError("The uploaded video exceeds the configured size limit.")
                    output.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return StoredMedia(storage_path, size, content_type or "application/octet-stream")

    def save_evidence_bytes(
        self,
        alert_id: str,
        evidence_kind: str,
        suffix: str,
        content_type: str,
        content: bytes,
    ) -> StoredMedia:
        if evidence_kind not in {"violence_clip", "bullying_clip", "weapon_screenshot"}:
            raise ValueError("Unsupported evidence kind")
        if suffix.lower() not in {".mp4", ".jpg", ".jpeg", ".png"}:
            raise ValueError("Unsupported evidence file type")
        if len(content) > self._max_bytes:
            raise ValueError("Evidence exceeds the configured size limit")
        storage_path = f"evidence/{alert_id}/{uuid4().hex}{suffix.lower()}"
        destination = self._local_path(storage_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return StoredMedia(
            storage_path=storage_path,
            size_bytes=len(content),
            content_type=content_type,
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def save_task_evidence(self, alert_id, task_id, suffix, content_type, content):
        # Stable, atomically replaced output makes retries safe across crashes.
        if not task_id.startswith('media_') or not task_id[6:].isalnum():
            raise ValueError('Invalid media task ID')
        storage_path = f'evidence/{alert_id}/{task_id}{suffix}'
        destination = self._local_path(storage_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(suffix + '.part')
        temporary.write_bytes(content)
        temporary.replace(destination)
        return StoredMedia(storage_path, len(content), content_type, hashlib.sha256(content).hexdigest())

    def delete(self, storage_path: str) -> None:
        path = self._local_path(storage_path)
        with self._preview_lock(storage_path):
            path.unlink(missing_ok=True)
            path.with_name(path.stem + '.raw.jpg').unlink(missing_ok=True)
            preview = self._playback_preview_path(storage_path)
            preview.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass

    def save_unannotated_evidence(self, storage_path: str, content: bytes) -> Path:
        """Private same-frame companion, never registered as a gallery image."""
        target = self._local_path(storage_path)
        path = target.with_name(target.stem + '.raw.jpg')
        temporary = path.with_suffix('.jpg.part')
        temporary.write_bytes(content)
        temporary.replace(path)
        return path

    def _playback_preview_path(self, storage_path: str) -> Path:
        key = hashlib.sha256(storage_path.encode("utf-8")).hexdigest()
        return self._local_path(f"playback_previews/{key}.mp4")

    def _preview_lock(self, storage_path: str) -> threading.Lock:
        with self._preview_lock_guard:
            return self._preview_locks.setdefault(storage_path, threading.Lock())

    def materialize_browser_playback(self, storage_path: str) -> Path:
        """Return browser-playable media, keeping the original inference source untouched."""
        source = self.materialize(storage_path)
        if source.suffix.lower() != ".avi":
            return source

        preview = self._playback_preview_path(storage_path)
        if preview.is_file() and preview.stat().st_size > 0:
            return preview

        with self._preview_lock(storage_path):
            if preview.is_file() and preview.stat().st_size > 0:
                return preview
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("FFmpeg is required to create a browser-playable AVI preview.")

            preview.parent.mkdir(parents=True, exist_ok=True)
            # Repeating the cache hash can exceed Windows' path limit even when
            # the final preview is accessible, hiding a successful FFmpeg output.
            temporary = preview.with_name(f"{uuid4().hex}.part.mp4")
            command = [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-nostdin",
                "-y",
                "-i", str(source),
                "-map", "0:v:0",
                "-map", "0:a:0?",
                "-sn",
                "-dn",
                "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-fps_mode", "passthrough",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                "-f", "mp4",
                str(temporary),
            ]
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=3600,
                )
                if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
                    raise RuntimeError("FFmpeg could not create a playable preview from this AVI file.")
                temporary.replace(preview)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("AVI preview conversion exceeded the one-hour safety limit.") from exc
            except OSError as exc:
                raise RuntimeError("AVI preview conversion could not be started or saved.") from exc
            finally:
                temporary.unlink(missing_ok=True)
        return preview

    def materialize(self, storage_path: str) -> Path:
        path = self._local_path(storage_path)
        if not path.is_file():
            raise FileNotFoundError(storage_path)
        return path


# Backward-compatible test alias. Operational code uses LocalMediaStore.
DemoMediaStore = LocalMediaStore
