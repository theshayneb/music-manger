"""Find and run ffmpeg (bundled with the app; used to convert to MP3 and measure loudness)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


class FfmpegError(Exception):
    pass


def _bundled() -> list[Path]:
    places = []
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        places += [base / "ffmpeg" / "ffmpeg.exe", Path(sys.executable).parent / "ffmpeg" / "ffmpeg.exe"]
    root = Path(__file__).resolve().parents[2]
    places += [root / "vendor" / "ffmpeg" / "ffmpeg.exe", root / "vendor" / "ffmpeg" / "ffmpeg"]
    return places


def find_ffmpeg(preferred: str | None = None) -> str | None:
    if preferred and Path(preferred).is_file():
        return preferred
    for place in _bundled():
        if place.is_file():
            return str(place)
    return shutil.which("ffmpeg")


def run(ffmpeg: str, args: list[str], timeout: float = 600) -> subprocess.CompletedProcess:
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW: no console popping up
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", *args],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout, creationflags=flags,
    )
    proc.stderr_text = proc.stderr.decode("utf-8", errors="replace")  # type: ignore[attr-defined]
    return proc


def last_lines(text: str, count: int = 3) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return " / ".join(lines[-count:]) if lines else "unknown error"

