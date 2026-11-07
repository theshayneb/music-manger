"""ReplayGain: measure how loud a song is and write the volume correction as tags.

Uses ReplayGain 2.0: ffmpeg's EBU R128 meter measures the song's loudness, and the
gain is how far that is from the -18 LUFS reference (e.g. a loud modern song at
-8 LUFS gets "-10.00 dB"). The audio itself is NOT changed; players that support
ReplayGain (Symfonium, MusicBee, foobar2000, ...) turn the volume down or up.
Plexamp ignores these tags and uses Plex's own loudness analysis instead.
"""

from __future__ import annotations

import re

from . import tags
from .ffmpeg import FfmpegError, last_lines, run

REFERENCE_LUFS = -18.0

_INTEGRATED = re.compile(r"I:\s*(-?[\d.]+|-inf)\s*LUFS")
_PEAK = re.compile(r"Peak:\s*(-?[\d.]+|-inf)\s*dBFS")


def measure(ffmpeg: str, path: str) -> tuple[float, float]:
    """(gain in dB, true peak as a fraction of full scale)."""
    proc = run(ffmpeg, ["-i", path, "-map", "0:a:0", "-af", "ebur128=peak=true:framelog=quiet",
                        "-f", "null", "-"])
    text = proc.stderr_text  # type: ignore[attr-defined]
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else ""
    m_i = _INTEGRATED.search(summary)
    if proc.returncode != 0 or not m_i:
        raise FfmpegError(f"couldn't measure the volume: {last_lines(text)}")
    if m_i.group(1) == "-inf":
        raise FfmpegError("the song is silent")
    loudness = float(m_i.group(1))
    m_p = _PEAK.search(summary)
    peak_db = float(m_p.group(1)) if m_p and m_p.group(1) != "-inf" else -120.0
    return round(REFERENCE_LUFS - loudness, 2), round(10 ** (peak_db / 20), 6)


def analyze(ffmpeg: str, path: str) -> tuple[float, float]:
    """Measure and write REPLAYGAIN_TRACK_GAIN / _PEAK into the file."""
    gain, peak = measure(ffmpeg, path)
    tags.write(path, {"gain": (gain, peak)})
    return gain, peak
