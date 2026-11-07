import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from music_manager.core.ffmpeg import find_ffmpeg  # noqa: E402

FFMPEG = find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))


def make_audio(path: Path, seconds: float = 3, freq: int = 440, codec_args: list[str] | None = None) -> Path:
    """A short stereo sine wave as FLAC/MP3/WAV (from the extension)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", f"sine=frequency={freq}:duration={seconds}", "-ac", "2", *(codec_args or []), str(path)]
    subprocess.run(args, check=True, stdin=subprocess.DEVNULL)
    return path


@pytest.fixture
def song(tmp_path):
    if not FFMPEG:
        pytest.skip("needs ffmpeg")

    def make(name: str, **kw) -> Path:
        return make_audio(tmp_path / name, **kw)

    return make


PNG = (Path(__file__).resolve().parents[1] / "music_manager" / "assets" / "icon.png").read_bytes()


@pytest.fixture(autouse=True)
def close_all_windows():
    """Close and delete every window a test leaves behind, while Qt is still running."""
    yield
    try:
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


