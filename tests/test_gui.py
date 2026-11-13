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
    monkeypatch.setattr(main_window.KeepReview, "exec", lambda self: (self.accept(), 1)[1])
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


@needs_ffmpeg
def test_album_view(app, song, tmp_path):
    from music_manager.ui.main_window import MainWindow

    for n, disc, ed, title in (("02", "01", "", "Halo"), ("08", "01", "Deluxe Edition", "Beautiful"),
                               ("01", "02", "", "Single Ladies")):
        p = song(f"music/Beyoncé - {title}.mp3", seconds=1)
        tags.write(str(p), {"title": title, "artist": "Beyoncé", "album": "I Am... Sasha Fierce", "year": "2008",
                            "track": n, "disc": disc, "disc_total": "02", "edition": ed})
    music = tmp_path / "music"
    settings = Settings(library_folder=str(music), last_view="library")
    window = MainWindow(settings, FFMPEG)
    window.show()
    wait(app, window, 5, until=lambda: len(window.album_model.order()) == 3)
    assert window.album_mode()
    assert [t.title for t in window.order()] == ["Halo", "Beautiful", "Single Ladies"]
    subs = [r.text for r in window.album_model.rows if r.kind == "sub"]
    assert subs == ["Disc 01", "Deluxe Edition • Disc 01", "Disc 02"]
    window._album_clicked(window.album_model.index(window.album_model.row_of(str(music / "Beyoncé - Halo.mp3")), 0))
    assert len(window.selected_tracks()) == 3
    window.set_mode("list")
    assert not window.album_mode() and len(window.selected_tracks()) == 3
    window.close()


def test_keep_review_marks_what_is_empty(app, song):
    from music_manager.ui.review import KeepReview, empties

    path = song("a.mp3")
    tags.write(str(path), {"title": "Hello", "artist": "Adele"})
    d = tags.read(str(path), with_details=True)
    missing = empties(d)
    assert "Album" in missing and "Album art" in missing and "Lyrics" in missing and "Title" not in missing
    review = KeepReview([d])
    review._fix()
    assert review.fix == str(path)


@needs_ffmpeg
def test_views_remember_where_you_were_and_saved_searches(app, song, tmp_path):
    from music_manager.ui.main_window import MainWindow

    for i in range(3):
        p = song(f"music/Artist {i} - Song {i}.mp3", seconds=1)
        tags.write(str(p), {"title": f"Song {i}", "artist": f"Artist {i}", "album": "A" if i else ""})
    review = tmp_path / "dl"
    review.mkdir()
    settings = Settings(library_folder=str(tmp_path / "music"), review_folder=str(review), last_view="library",
                        view_modes={"library": "list", "review": "list"})
    window = MainWindow(settings, FFMPEG)
    window.show()
    wait(app, window, 5, until=lambda: len(window.model.tracks) == 3)
    window._select_path(str(tmp_path / "music" / "Artist 2 - Song 2.mp3"))
    window._select_view_item("review")
    wait(app, window, 2)
    window._select_view_item("library")
    wait(app, window, 2, until=lambda: bool(window.selected_tracks()))
    assert [t.title for t in window.selected_tracks()] == ["Song 2"]
    # A saved search: Library songs matching "Song 1".
    window.search.setText("Song 1")
    from music_manager.ui import main_window as mw

    original = mw.ask_text
    mw.ask_text = lambda *a, **k: "Song one"
    try:
        window.save_search_view()
    finally:
        mw.ask_text = original
    wait(app, window, 3, until=lambda: window.view == "search:Song one" and window.proxy.rowCount() == 1)
    assert window.view == "search:Song one" and window.proxy.rowCount() == 1
    window._select_view_item("library")  # the Library's own search comes back: none
    wait(app, window, 2)
    assert window.search.text() == "" and window.proxy.rowCount() == 3
    assert window.settings.saved_searches[0]["source"] == "library"
    window.close()


def test_arrow_keys_in_the_album_view_only_stop_on_songs(app, song, tmp_path):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from music_manager.ui.main_window import MainWindow

    for album, title in (("One", "A"), ("Two", "B")):
        p = song(f"music/X - {title}.mp3", seconds=1)
        tags.write(str(p), {"title": title, "artist": "X", "album": album, "track": "1"})
    window = MainWindow(Settings(library_folder=str(tmp_path / "music"), last_view="library"), FFMPEG)
    window.show()
    wait(app, window, 5, until=lambda: len(window.album_model.order()) == 2)
    window._select_path(str(tmp_path / "music" / "X - A.mp3"))
    window.albums.setFocus()
    app.sendEvent(window.albums, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
    wait(app, window, 1)
    assert [t.title for t in window.selected_tracks()] == ["B"]  # straight past the padding and the gap

    # Saving from the Tags panel gives the keyboard back to the list, on the same song: ↓ goes on from there.
    window._select_path(str(tmp_path / "music" / "X - A.mp3"))
    wait(app, window, 1, until=lambda: window.editor.edits["title"].text() == "A")
    window.editor.edits["genre"].setFocus()
    window.editor.apply_field("genre", "Pop")
    window.editor.save()
    wait(app, window, 3, until=lambda: tags.read_track(str(tmp_path / "music" / "X - A.mp3")).genre == "Pop")
    wait(app, window, 1)
    assert window.albums.hasFocus() or app.focusWidget() is window.albums or not app.focusWidget()
    assert window.album_model.track(window.albums.currentIndex().row()).title == "A"
    app.sendEvent(window.albums, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
    wait(app, window, 1)
    assert [t.title for t in window.selected_tracks()] == ["B"]
    window.close()


def test_lyrics_review_ticks_only_what_changes(app):
    from music_manager.core.online import Lyrics
    from music_manager.core.tags import Track
    from music_manager.ui.lyrics_review import Found, LyricsReview, status

    items = [Found(Track(path="a.mp3"), "", Lyrics("LRCLIB", "new words")),
             Found(Track(path="b.mp3"), "[00:01.00] old\n[00:02.00] words", Lyrics("Genius", "plain words")),
             Found(Track(path="c.mp3"), "same words", Lyrics("LRCLIB", "Same  words")),
             Found(Track(path="d.mp3"), "", None)]
    review = LyricsReview(items)
    assert [i.use for i in items] == [True, True, False, False]
    assert status(items[1]).startswith("↻ replaces timestamped") and status(items[3]) == "✗ nothing found"
    review.songs.setCurrentRow(1)
    review.found.setPlainText("plain words, fixed")
    review.use.setChecked(False)
    assert [(i.track.path, i.text) for i in review.chosen()] == [("a.mp3", "new words")]
    review.use.setChecked(True)
    assert [i.text for i in review.chosen()] == ["new words", "plain words, fixed"]


def test_typed_changes_survive_other_saves_and_tab_switches(app, song):
    from music_manager.ui.editor import TagEditor

    path = song("x.mp3")
    tags.write(str(path), {"title": "Old", "rating": 2})
    d = tags.read(str(path), with_details=True)
    editor = TagEditor()
    editor.show_tracks([d.track], d)
    editor.apply_field("title", "New title")       # Standard tab
    editor.tag_tabs.setCurrentIndex(1)
    editor.apply_field("edition", "Deluxe")         # Extra tab
    editor.tag_tabs.setCurrentIndex(0)
    # Something else gets saved (the rating, say) and the song is shown again:
    tags.write(str(path), {"rating": 5})
    d2 = tags.read(str(path), with_details=True)
    editor.show_tracks([d2.track], d2)
    assert editor.edits["title"].text() == "New title" and editor.edits["edition"].text() == "Deluxe"
    assert editor.changed == {"title": "New title", "edition": "Deluxe"}
    assert editor.rating.value == 5 and editor.rating.buttons[4].isChecked()
    editor.revert_btn.click()  # Undo changes drops them
    assert editor.edits["title"].text() == "Old" and not editor.changed


@needs_ffmpeg
def test_album_view_columns_can_be_dragged(app, song, tmp_path):
    from music_manager.ui.main_window import MainWindow

    p = song("music/X - A.mp3", seconds=1)
    tags.write(str(p), {"title": "A", "artist": "X", "album": "One", "track": "1"})
    window = MainWindow(Settings(library_folder=str(tmp_path / "music"), last_view="library"), FFMPEG)
    window.show()
    wait(app, window, 5, until=lambda: len(window.album_model.order()) == 1)
    keys = [k for k, _ in window.album_model.columns]
    title, artist = keys.index("title"), keys.index("artist")
    window.albums.horizontalHeader().moveSection(artist, title)  # drag Artist before Title
    wait(app, window, 1)
    keys = [k for k, _ in window.album_model.columns]
    assert keys[0] == "art" and keys.index("artist") < keys.index("title")
    assert window.albums.horizontalHeader().visualIndex(keys.index("artist")) == keys.index("artist")
    window.close()
    assert [c["key"] for c in window.settings.album_columns][:4] == keys[:4]


def test_obsidian_tag_chips(app, song):
    from mutagen.id3 import ID3

    from music_manager.ui.editor import TagEditor

    path = song("x.mp3")
    tags.write(str(path), {"obsidian_tags": "funny"})
    d = tags.read(str(path), with_details=True)
    editor = TagEditor()
    editor.set_choices({"obsidian_tags": ["holiday", "funny", "Feminism"]})
    editor.show_tracks([d.track], d)
    assert list(editor.chip_buttons) == ["Feminism", "funny", "holiday"]  # alphabetical
    assert [c.isChecked() for c in editor.chip_buttons.values()] == [False, True, False]
    editor.chip_buttons["Feminism"].click()
    editor.chip_buttons["holiday"].click()
    editor.chip_buttons["funny"].click()  # off again
    assert editor.edits["obsidian_tags"].text() == "#Feminism #holiday"
    editor.edits["obsidian_tags"].setText("#Feminism #holiday #atheism")  # typed by hand
    editor.edits["obsidian_tags"].editingFinished.emit()
    assert "atheism" in editor.chip_buttons
    tags.write(str(path), {"obsidian_tags": editor.edits["obsidian_tags"].text()})
    # Several separate values in the file: Media Manager reads them as a list for the note's tags.
    frame = ID3(str(path))["TXXX:Obsidian Tags"]
    assert frame.text == ["Feminism", "holiday", "atheism"]


@needs_ffmpeg
def test_folders_panel_card_and_checkup(app, song, tmp_path):
    from music_manager.ui.main_window import MainWindow

    from .conftest import PNG

    a = song("music/X - A.mp3", seconds=1)
    b = song("music/X - B.mp3", seconds=1)
    song("dl/new.flac", seconds=1)
    tags.write(str(a), {"title": "A", "artist": "X", "album": "One", "year": "2001", "cover": (PNG, "image/png"),
                        "lyrics": "[00:01.00] la\n[00:02.00] la"})
    tags.write(str(b), {"title": "B", "artist": "X"})
    window = MainWindow(Settings(library_folder=str(tmp_path / "music"), review_folder=str(tmp_path / "dl"),
                                 last_view="library", view_modes={"library": "list"}), FFMPEG)
    window.show()
    wait(app, window, 5, until=lambda: window.checkup.numbers["songs"].text() == "2")
    n = {k: v.text() for k, v in window.checkup.numbers.items()}
    assert (n["songs"], n["cover"], n["lyrics"], n["synced"], n["tags"]) == ("2", "1", "1", "1", "1")
    window._select_path(str(a))
    wait(app, window, 2, until=lambda: window.editor.cover.pixmap() is not None
         and not window.editor.cover.pixmap().isNull())
    assert window.now_card.heading.text() == "SELECTED" and "A" in window.now_card.text.text()
    # The art and its buttons are the Tags panel's own, shown under the Folders list.
    assert window.editor.art_panel.parentWidget() is window.now_card and window.editor.art_paste.isEnabled()
    window._open_check("library", "cover")
    wait(app, window, 1)
    assert window.show_filter.currentData() == "cover" and window.proxy.rowCount() == 1
    window.close()


def test_genre_from_the_artists_other_songs_and_suggestions(app, song):
    from music_manager.ui.editor import TagEditor

    path = song("x.mp3")
    tags.write(str(path), {"title": "New one", "artist": "Common Rider"})
    d = tags.read(str(path), with_details=True)
    editor = TagEditor()
    editor.set_artist_genres({"common rider": "Reggae/Ska"})
    editor.set_choices({"artist": ["Common Rider", "Operation Ivy"], "tempo": ["Very Slow", "Slow", "Moderate"]})
    editor.show_tracks([d.track], d)
    assert editor.edits["genre"].text() == "Reggae/Ska" and editor.changed == {"genre": "Reggae/Ska"}
    completer = editor.edits["artist"].completer()
    completer.setCompletionPrefix("rider")
    assert completer.currentCompletion() == "Common Rider"  # matches anywhere in the name, any case
    # A song that has a genre keeps it.
    tags.write(str(path), {"genre": "Punk"})
    d = tags.read(str(path), with_details=True)
    editor.show_tracks([d.track], d, keep_pending=False)
    assert editor.edits["genre"].text() == "Punk" and not editor.changed


@needs_ffmpeg
def test_keep_window_has_names_and_replace_and_extra_tags(app, song, tmp_path):
    from PySide6.QtWidgets import QLabel

    from music_manager.core import keep
    from music_manager.ui.review import KeepReview

    new = song("dl/x.mp3")
    (tmp_path / "Music").mkdir()
    song("Music/Adele - Hello.mp3", seconds=1)
    tags.write(str(new), {"title": "Hello", "artist": "Adele", "album_type": "album;live"})
    settings = Settings(library_folder=str(tmp_path / "Music"))
    track = tags.read_track(str(new))
    review = KeepReview([tags.read(str(new), with_details=True)], settings=settings,
                        planner=lambda pattern, overwrite: keep.plan([track], settings, pattern, overwrite))
    texts = [w.text() for w in review.page.widget().findChildren(QLabel)]
    assert "album;live" in texts and any("Becomes:" in t and "Hello_1.mp3" in t for t in texts)
    review.replace.setChecked(True)
    texts = [w.text() for w in review.page.widget().findChildren(QLabel)]
    assert any("Becomes:" in t and "Adele - Hello.mp3" in t and "replaces" in t for t in texts)
    assert review.overwrite and review.rows[0][1].endswith("Adele - Hello.mp3")
    review.pattern.setCurrentText("{title}")
    assert review.rows[0][1].endswith("Hello.mp3") and "Adele" not in review.rows[0][1].rsplit("/", 1)[-1]
