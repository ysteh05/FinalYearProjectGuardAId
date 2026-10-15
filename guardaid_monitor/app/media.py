from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
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

    def delete(self, storage_path: str) -> None:
        path = self._local_path(storage_path)
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass

    def materialize(self, storage_path: str) -> Path:
        path = self._local_path(storage_path)
        if not path.is_file():
            raise FileNotFoundError(storage_path)
        return path


# Backward-compatible test alias. Operational code uses LocalMediaStore.
DemoMediaStore = LocalMediaStore
