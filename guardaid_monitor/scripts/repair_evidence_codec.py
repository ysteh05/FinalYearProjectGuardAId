from __future__ import annotations

from fractions import Fraction
import hashlib
from pathlib import Path
import sqlite3
import sys

import av
import cv2


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from app.config import RuntimeConfig  # noqa: E402
from app.media import LocalMediaStore  # noqa: E402
from app.sqlite_repository import SQLiteRepository  # noqa: E402


def transcode(path: Path) -> bool:
    temporary = path.with_name(f"{path.stem}.h264-repair.mp4")
    already_h264 = False
    with av.open(str(path)) as source:
        input_stream = source.streams.video[0]
        if input_stream.codec_context.name == "h264":
            already_h264 = True
        else:
            rate = input_stream.average_rate or Fraction(12, 1)
            width = input_stream.codec_context.width
            height = input_stream.codec_context.height
            output_width, output_height = width + (width % 2), height + (height % 2)
            with av.open(
                str(temporary), "w", format="mp4", options={"movflags": "+faststart"}
            ) as destination:
                output_stream = destination.add_stream("libx264", rate=rate)
                output_stream.width, output_stream.height = output_width, output_height
                output_stream.pix_fmt = "yuv420p"
                output_stream.options = {"preset": "veryfast", "crf": "23"}
                for decoded in source.decode(input_stream):
                    frame = decoded.to_ndarray(format="bgr24")
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
                    for packet in output_stream.encode(video_frame):
                        destination.mux(packet)
                for packet in output_stream.encode():
                    destination.mux(packet)
    if already_h264:
        return False
    try:
            temporary.replace(path)
            return True
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    config = RuntimeConfig.from_env()
    repository = SQLiteRepository(config)
    media_store = LocalMediaStore(config)
    repaired = 0
    for alert in repository.list_alerts():
        for evidence in alert.evidence:
            if evidence.content_type != "video/mp4":
                continue
            path = media_store.materialize(evidence.storage_path)
            if not transcode(path):
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with sqlite3.connect(config.database_path) as connection:
                connection.execute(
                    "UPDATE media_files SET size_bytes=?,sha256=? WHERE relative_path=?",
                    (path.stat().st_size, digest, evidence.storage_path),
                )
                connection.commit()
            repaired += 1
            print(f"repaired {evidence.id}: {path.name}")
    print(f"complete: {repaired} evidence clip(s) repaired")


if __name__ == "__main__":
    main()
