import os
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from music_manager.core import tags  # noqa: E402
from music_manager.core.settings import Settings  # noqa: E402

from .conftest import FFMPEG, needs_ffmpeg  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait(app, window, seconds=2.0, until=None):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        window.bg.wait(20)
        window.scan_bg.wait(20)
        if until and until():
            app.processEvents()
            return


@needs_ffmpeg
def test_review_edit_and_keep(app, song, tmp_path, monkeypatch):
    from music_manager.ui import main_window
    from music_manager.ui.main_window import MainWindow

    a = song("dl/a.flac")
    song("dl/b.mp3")
    music = tmp_path / "music"
    music.mkdir()
    tags.write(str(a), {"title": "Hello", "artist": "Adele"})
    settings = Settings(review_folder=str(a.parent), library_folder=str(music))
    window = MainWindow(settings, FFMPEG)
    window.show()
    wait(app, window, 5, until=lambda: len(window.model.tracks) == 2)
    assert sorted(t.name for t in window.model.tracks) == ["a.flac", "b.mp3"]

    assert window._select_path(str(a))
    wait(app, window, 2, until=lambda: window.editor.edits["title"].text() == "Hello")
    window.editor.apply_field("album", "25")
    window.editor.save()
    wait(app, window, 3, until=lambda: tags.read_track(str(a)).album == "25")
    assert tags.read_track(str(a)).album == "25"

    monkeypatch.setattr(main_window.PlanDialog, "exec", lambda self: (self.accept(), 1)[1])
    window._select_path(str(a))
    window.keep_selected()
    wait(app, window, 20, until=lambda: os.path.exists(music / "Adele - Hello.mp3") and not window._busy)
    assert os.path.exists(music / "Adele - Hello.mp3")
    assert [t.name for t in window.model.tracks] == ["b.mp3"]
    window.close()
