"""Create the repeat/escalation tones from the existing approved two-beep sound."""

from __future__ import annotations

import wave
from pathlib import Path


RAW = Path(__file__).resolve().parents[1] / "android/app/src/main/res/raw"
SOURCE = RAW / "guardaid_urgent_beep_v7.wav"


def main() -> None:
    with wave.open(str(SOURCE), "rb") as source:
        params = source.getparams()
        if params.nchannels != 1 or params.sampwidth != 2 or params.framerate != 44_100:
            raise ValueError("The source alert sound format changed; inspect before regenerating.")
        source_frames = source.readframes(source.getnframes())

    # The approved v7 alert contains a 300 ms beep, 180 ms pause, a second
    # 300 ms beep, and 180 ms trailing silence. Reuse the exact first pulse.
    bytes_per_frame = params.nchannels * params.sampwidth
    beep_frames = round(params.framerate * 0.30)
    gap_frames = round(params.framerate * 0.18)
    beep = source_frames[: beep_frames * bytes_per_frame]
    silence = bytes(gap_frames * bytes_per_frame)

    for beep_count, filename in (
        (3, "guardaid_urgent_repeat_3beep_v1.wav"),
        (4, "guardaid_urgent_escalated_4beep_v1.wav"),
    ):
        frames = bytearray()
        for index in range(beep_count):
            frames.extend(beep)
            if index < beep_count - 1:
                frames.extend(silence)
        frames.extend(silence)
        output = RAW / filename
        with wave.open(str(output), "wb") as target:
            target.setparams(params)
            target.writeframes(frames)
        with wave.open(str(output), "rb") as check:
            duration = check.getnframes() / check.getframerate()
            if check.getnframes() != len(frames) // bytes_per_frame:
                raise IOError(f"WAV verification failed for {output.name}")
        print(f"{output.name}: {beep_count} beeps, {duration:.2f}s")


if __name__ == "__main__":
    main()
