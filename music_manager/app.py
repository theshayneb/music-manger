"""Start-up: settings, ffmpeg, main window."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import __version__
from .core.settings import Settings
from .ui.main_window import MainWindow
from .ui.theme import apply_theme

ICON = Path(__file__).resolve().parent / "assets" / "icon.png"


def self_test(out_path: str) -> int:
    """Used by CI on the built exe: check that ffmpeg, mutagen and audio playback load,
    and that a FLAC really converts to a tagged MP3."""
    import json
    import tempfile

    result: dict[str, object] = {}
    try:
        from .core import convert, loudness, tags
        from .core.ffmpeg import find_ffmpeg, run

        settings = Settings()
        ffmpeg = find_ffmpeg()
        result["ffmpeg"] = ffmpeg
        result["icon"] = ICON.exists()
        with tempfile.TemporaryDirectory() as tmp:
            flac = str(Path(tmp) / "test.flac")
            run(ffmpeg or "ffmpeg", ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", flac])
            tags.write(flac, {"title": "Test", "artist": "Self", "rating": 5, "lyrics": "la"})
            mp3 = convert.to_mp3(ffmpeg, flac, "V0")
            loudness.analyze(ffmpeg, mp3)
            t = tags.read_track(mp3)
            result["convert"] = (t.title, t.artist, t.rating, t.lyrics_in_file, bool(t.gain))
            result["convert_ok"] = t.title == "Test" and t.rating == 5 and t.lyrics_in_file and bool(t.gain)
        from PySide6.QtMultimedia import QMediaPlayer

        result["audio"] = QMediaPlayer().isAvailable()
        window = MainWindow(settings, ffmpeg)
        window.close()
        result["ok"] = bool(ffmpeg and result["icon"] and result["audio"] and result["convert_ok"])
    except Exception as exc:  # noqa: BLE001
        result["error"] = repr(exc)
        result["ok"] = False
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f)
    return 0 if result["ok"] else 1


def install_error_handler() -> None:
    """Show unexpected errors instead of failing silently (the exe has no console)."""
    import traceback
    from datetime import datetime

    from PySide6.QtWidgets import QMessageBox

    from .core.settings import cache_dir

    log = cache_dir() / "errors.log"
    showing = False

    def hook(exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        nonlocal showing
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S} (v{__version__})\n{text}\n")
        except OSError:
            pass
        sys.__excepthook__(exc_type, exc, tb)
        if showing or QApplication.instance() is None:
            return
        showing = True
        try:
            QMessageBox.warning(None, "Music Manager — something went wrong",
                                f"{exc_type.__name__}: {exc}\n\nDetails were saved to:\n{log}")
        finally:
            showing = False

    sys.excepthook = hook


def set_app_icon(app: QApplication) -> None:
    if sys.platform == "win32":
        import ctypes

        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MusicManager.MusicManager")
        except (AttributeError, OSError):
            pass
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    app = QApplication(argv)
    set_app_icon(app)
    if "--self-test" in argv:
        return self_test(argv[argv.index("--self-test") + 1])
    app.setApplicationName("Music Manager")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("MusicManager")
    install_error_handler()
    settings = Settings.load()
    apply_theme(settings.theme, app)
    window = MainWindow(settings)
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    window.show()
    code = app.exec()
    del window
    return code
