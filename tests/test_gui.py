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


@needs_ffmpeg
def test_lookup_fills_the_tags_panel(app, song, tmp_path, monkeypatch):
    from music_manager.core import online
    from music_manager.ui import lookup as lookup_ui
    from music_manager.ui.editor import TagEditor

    from .conftest import PNG
    from .test_online import ITUNES

    path = song("Beyoncé - Halo.mp3")
    tags.write(str(path), {"artist": "Beyoncé", "title": "Halo", "genre": "R&B"})
    found = online.parse_itunes(ITUNES)
    monkeypatch.setattr(online, "search", lambda a, t, length=0: (found, []))
    monkeypatch.setattr(online, "find_lyrics", lambda *a, **k: ([online.Lyrics("LRCLIB", "Remember those walls")], []))
    monkeypatch.setattr(online, "get_image", lambda url: PNG)

    details = tags.read(str(path), with_details=True)
    dialog = lookup_ui.LookupDialog(details.track, details)
    end = time.time() + 5
    while time.time() < end and (dialog.results.count() == 0 or dialog.art.count() == 0 or not dialog.lyrics_found):
        app.processEvents()
        lookup_ui.net().wait(20)
    app.processEvents()
    assert dialog.results.count() == 1 and dialog.art.count() == 1
    dialog.art.setCurrentRow(0)
    dialog._accept()
    # Empty tags are filled; the genre you chose ("R&B") isn't replaced by iTunes' "R&B/Soul" unless ticked.
    assert dialog.chosen_fields["album"] == "I Am... Sasha Fierce"
    assert dialog.chosen_fields["edition"] == "Deluxe Edition" and dialog.chosen_fields["track"] == "2"
    assert "genre" not in dialog.chosen_fields and "title" not in dialog.chosen_fields
    assert dialog.chosen_cover == PNG and dialog.chosen_lyrics == "Remember those walls"

    editor = TagEditor()
    editor.show_tracks([details.track], details)
    editor.apply_lookup(dialog.chosen_fields, dialog.chosen_cover, dialog.chosen_lyrics)
    assert editor.changed["album"] == "I Am... Sasha Fierce" and editor.changed["lyrics"] == "Remember those walls"
    assert editor.changed["cover"][0] == PNG


@needs_ffmpeg
def test_broken_file_shows_why_and_can_be_repaired(app, song, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from music_manager.ui.main_window import MainWindow

    good = song("dl/x.mp3", seconds=4)
    tags.write(str(good), {"title": "Lunar Eclipse", "artist": "The Vaccines"})
    raw = good.read_bytes()
    end = tags._skip_id3(raw)
    bad = good.with_name("The Vaccines - Lunar Eclipse.mp3")
    bad.write_bytes(raw[:end] + bytes((i * 7) % 255 for i in range(1_200_000)) + raw[end:])
    good.unlink()
    window = MainWindow(Settings(review_folder=str(bad.parent), library_folder=str(tmp_path)), FFMPEG)
    window.show()
    wait(app, window, 5, until=lambda: len(window.model.tracks) == 1)
    window._select_path(str(bad))
    wait(app, window, 2, until=lambda: window.editor.problem.text() != "")
    assert window.editor.problem.isVisibleTo(window.editor) and window.editor.repair_btn.isVisibleTo(window.editor)
    assert window.editor.edits["title"].text() == "Lunar Eclipse" and not window.editor.edits["title"].isEnabled()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.repair_selected()
    wait(app, window, 15, until=lambda: not window._busy and not window.model.tracks[0].error)
    assert not window.model.tracks[0].error and window.model.tracks[0].title == "Lunar Eclipse"
    window.close()
