"""The main window: Review / Library on the left, the song list, the tag editor, the player."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable

from PySide6.QtCore import QByteArray, QItemSelectionModel, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDockWidget,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core import convert, fileops, keep, library, loudness, naming, tags
from ..core.ffmpeg import find_ffmpeg
from ..core.settings import Settings
from ..core.tags import Track
from .dialogs import PlanDialog, SettingsDialog, ask_text
from .editor import TagEditor
from .model import COLUMN_KEYS, HIDDEN_BY_DEFAULT, TrackFilter, TrackModel
from .player import PlayerBar
from .theme import apply_theme
from .workers import Background

VIEWS = {"review": "Review", "library": "Library"}
SHOW_FILTERS = [
    ("", "All songs"),
    ("tags", "Missing tags (title, artist, album or year)"),
    ("cover", "No album art"),
    ("lyrics", "No lyrics"),
    ("gain", "Volume not measured"),
    ("low", "Low bitrate (under 192 kbps)"),
]
SCAN_CHUNK = 150


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, ffmpeg: str | None = None):
        super().__init__()
        self.settings = settings
        self.ffmpeg = ffmpeg if ffmpeg is not None else find_ffmpeg(settings.ffmpeg_path or None)
        self.bg = Background(1)        # tag writes, conversions, file operations (in order)
        self.scan_bg = Background(1)   # reading folders
        self.cache = library.TagCache()
        self.view = settings.last_view if settings.last_view in VIEWS else "review"
        self._scan_gen = 0
        self._details_gen = 0
        self._busy = False
        self._stale: set[str] = set()  # views to reload when next shown
        self.setWindowTitle(f"Music Manager {__version__}")
        self.resize(1400, 860)

        self.model = TrackModel()
        self.proxy = TrackFilter()
        self.proxy.setSourceModel(self.model)
        self._build_ui()
        self._build_actions()
        self._restore_layout()
        QTimer.singleShot(0, self._start)

    # ================================================================ building
    def _build_ui(self) -> None:
        self.views = QListWidget()
        self.views.setMaximumWidth(220)
        for key, label in VIEWS.items():
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.views.addItem(item)
        self.views.setStyleSheet("QListWidget { font-size: 15px; } QListWidget::item { padding: 8px; }")
        self.views.setFixedHeight(self.views.sizeHintForRow(0) * len(VIEWS) + 8)
        self.view_hint = QLabel("")
        self.view_hint.setWordWrap(True)
        self.view_hint.setStyleSheet("color: palette(placeholder-text); padding: 4px;")
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.views)
        lv.addWidget(self.view_hint)
        lv.addStretch(1)
        left.setMaximumWidth(220)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setStretchLastSection(True)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_menu)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self.table, 1)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        self.editor = TagEditor()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.editor)
        scroll.setMinimumWidth(340)
        self.editor_dock = QDockWidget("Tags", self)
        self.editor_dock.setObjectName("tags")
        self.editor_dock.setWidget(scroll)
        self.editor_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.editor_dock)

        self.player = PlayerBar(self.settings.volume)
        player_dock = QDockWidget("Player", self)
        player_dock.setObjectName("player")
        player_dock.setTitleBarWidget(QWidget())
        player_dock.setWidget(self.player)
        player_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, player_dock)

        self.status = QLabel("")
        self.statusBar().addWidget(self.status, 1)
        self.count_label = QLabel("")
        self.statusBar().addPermanentWidget(self.count_label)

        self.views.currentItemChanged.connect(self._view_clicked)
        self.table.doubleClicked.connect(lambda index: self.play_row(index.row()))
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._selection_changed())
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.editor.save_requested.connect(self.save_tags)
        self.editor.rating_requested.connect(self.set_rating)
        self.player.next_requested.connect(lambda: self.play_relative(1))
        self.player.previous_requested.connect(lambda: self.play_relative(-1))
        self.player.play_requested.connect(self.play_selected)
        self.player.finished.connect(self._song_finished)
        self.proxy.rowsInserted.connect(lambda *_: self._update_count())
        self.proxy.rowsRemoved.connect(lambda *_: self._update_count())
        self.proxy.modelReset.connect(self._update_count)
        self.proxy.layoutChanged.connect(self._update_count)

    def _build_actions(self) -> None:
        bar = QToolBar("Main")
        bar.setObjectName("main")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(bar)

        def action(text: str, slot: Callable, keys: str | list[str] | None = None, tip: str = "",
                   toolbar: bool = True) -> QAction:
            a = QAction(text, self)
            a.triggered.connect(lambda _=False: slot())
            if keys:
                a.setShortcuts([QKeySequence(k) for k in ([keys] if isinstance(keys, str) else keys)])
                a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            shown = ", ".join(k for k in ([keys] if isinstance(keys, str) else (keys or [])))
            a.setToolTip(f"{tip or text}{f' ({shown})' if shown else ''}")
            self.addAction(a)
            if toolbar:
                bar.addAction(a)
            return a

        self.keep_action = action("✔ Keep", self.keep_selected, "K",
                                  "Convert to MP3 if needed, rename, and move into the Library")
        self.delete_action = action("🗑 Delete", self.delete_selected, "Delete", "Send to the Recycle Bin")
        bar.addSeparator()
        self.convert_action = action("Convert to MP3", self.convert_selected, None,
                                     "Make an MP3 of the selected FLAC/WAV/M4A songs")
        self.rename_action = action("Rename…", self.rename_selected, None, "Rename from tags")
        self.gain_action = action("Measure volume", self.measure_selected, None,
                                  "Write ReplayGain tags so players can even out the volume")
        bar.addSeparator()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search title, artist, album…  (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(320)
        self.search.textChanged.connect(self.proxy.set_search)
        bar.addWidget(self.search)
        self.show_filter = QComboBox()
        for key, label in SHOW_FILTERS:
            self.show_filter.addItem(label, key)
        self.show_filter.currentIndexChanged.connect(
            lambda _i: self.proxy.set_missing(self.show_filter.currentData()))
        bar.addWidget(QLabel("  Show "))
        bar.addWidget(self.show_filter)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        action("⟳ Refresh", self.reload, "F5", "Read the folder again")
        action("⚙ Settings", self.open_settings, "Ctrl+,")

        action("Save tags", self.editor.save, "Ctrl+S", toolbar=False)
        action("Search", lambda: (self.search.setFocus(), self.search.selectAll()), "Ctrl+F", toolbar=False)
        action("Rename one", self.rename_one, "F2", toolbar=False)
        action("Show in Explorer", self.show_in_explorer, "Ctrl+E", toolbar=False)
        action("Play / pause", self.player.toggle, "Space", toolbar=False)
        action("Back 10 s", lambda: self.player.skip(-10), "Ctrl+Left", toolbar=False)
        action("Forward 30 s", lambda: self.player.skip(30), "Ctrl+Right", toolbar=False)
        action("Next song", lambda: self.play_relative(1), "Ctrl+Down", toolbar=False)
        action("Previous song", lambda: self.play_relative(-1), "Ctrl+Up", toolbar=False)
        for n in range(0, 6):
            action(f"{n} stars", lambda n=n: self.set_rating(self.selected_paths(), n), f"Ctrl+{n}",
                   toolbar=False)
        # Enter plays the selected song, but only in the list (in the tag boxes it saves).
        for keys in ("Return", "Enter"):
            QShortcut(QKeySequence(keys), self.table, self.play_selected,
                      context=Qt.ShortcutContext.WidgetShortcut)

    def _restore_layout(self) -> None:
        if self.settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(self.settings.window_geometry.encode()))
        if self.settings.window_state:
            self.restoreState(QByteArray.fromBase64(self.settings.window_state.encode()))
        header = self.table.horizontalHeader()
        state = self.settings.column_state.get("table")
        if state:
            header.restoreState(QByteArray.fromBase64(state.encode()))
        else:
            for col, key in enumerate(COLUMN_KEYS):
                header.setSectionHidden(col, key in HIDDEN_BY_DEFAULT)
            widths = {"rating": 70, "title": 240, "artist": 180, "album": 180, "year": 50, "length": 55,
                      "bitrate": 70, "format": 50, "lyrics": 50, "cover": 40, "gain": 75}
            for col, key in enumerate(COLUMN_KEYS):
                if key in widths:
                    header.resizeSection(col, widths[key])

    def _start(self) -> None:
        if not self.settings.review_folder and not self.settings.library_folder:
            self.open_settings(first_run=True)
        for row in range(self.views.count()):
            if self.views.item(row).data(Qt.ItemDataRole.UserRole) == self.view:
                self.views.setCurrentRow(row)
        if not self.ffmpeg:
            self.flash("ffmpeg wasn't found: converting to MP3 and measuring volume won't work.")

    # ================================================================ views & loading
    def folder(self, view: str | None = None) -> str:
        view = view or self.view
        return self.settings.review_folder if view == "review" else self.settings.library_folder

    def _view_clicked(self, item: QListWidgetItem | None, _old=None) -> None:  # type: ignore[no-untyped-def]
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if key == self.view and self.model.tracks and key not in self._stale:
            return
        if not self._confirm_unsaved():
            return
        self.view = key
        self.settings.last_view = key
        self.reload()

    def reload(self) -> None:
        self._stale.discard(self.view)
        folder = self.folder()
        is_review = self.view == "review"
        self.keep_action.setEnabled(is_review)
        self.view_hint.setText(folder or "No folder chosen yet: open ⚙ Settings.")
        self.view_hint.setToolTip(folder)
        self.model.set_tracks([])
        self.editor.show_tracks([], None)
        self._scan_gen += 1
        gen = self._scan_gen
        if not folder:
            self.status.setText("")
            return
        if not os.path.isdir(folder):
            self.status.setText(f"Can't open {folder} (is the drive connected?)")
            return
        subfolders = self.settings.review_subfolders if is_review else self.settings.library_subfolders
        self.status.setText(f"Reading {folder}…")
        self.scan_bg.run(lambda: library.list_songs(folder, subfolders),
                         lambda paths, err: self._scan_listed(gen, paths or [], err))

    def _scan_listed(self, gen: int, paths: list[str], error: str | None) -> None:
        if gen != self._scan_gen:
            return
        if error:
            self.status.setText(f"Couldn't read the folder: {error}")
            return
        if not paths:
            self.status.setText("No songs here.")
            return
        self._scan_chunk(gen, paths, 0)

    def _scan_chunk(self, gen: int, paths: list[str], start: int) -> None:
        chunk = paths[start:start + SCAN_CHUNK]

        def read() -> list[Track]:
            return [self.cache.get(p) for p in chunk]

        def done(tracks: list[Track] | None, error: str | None) -> None:
            if gen != self._scan_gen:
                return
            first = start == 0
            self.model.append(tracks or [])
            if first:
                self._sort_default()
                self._select_row(0)
            end = start + len(chunk)
            if end < len(paths):
                self.status.setText(f"Reading tags… {end} of {len(paths)}")
                self._scan_chunk(gen, paths, end)
            else:
                self.status.setText("")
                self.scan_bg.run(self.cache.save)

        self.scan_bg.run(read, done)

    def _sort_default(self) -> None:
        if self.proxy.sortColumn() < 0:
            col = COLUMN_KEYS.index("artist" if self.view == "library" else "name")
            self.table.sortByColumn(col, Qt.SortOrder.AscendingOrder)

    def _update_count(self) -> None:
        shown, total = self.proxy.rowCount(), len(self.model.tracks)
        text = f"{total} songs" if shown == total else f"{shown} of {total} songs shown"
        self.count_label.setText(text)

    # ================================================================ selection
    def selected_rows(self) -> list[int]:
        """Source-model rows, in the order shown."""
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.proxy.mapToSource(self.proxy.index(r, 0)).row() for r in rows]

    def selected_tracks(self) -> list[Track]:
        return [self.model.tracks[r] for r in self.selected_rows()]

    def selected_paths(self) -> list[str]:
        return [t.path for t in self.selected_tracks()]

    def _select_row(self, proxy_row: int) -> None:
        count = self.proxy.rowCount()
        if count == 0:
            return
        proxy_row = max(0, min(proxy_row, count - 1))
        index = self.proxy.index(proxy_row, 0)
        self.table.selectionModel().setCurrentIndex(
            index, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
        self.table.scrollTo(index)

    def _select_path(self, path: str) -> bool:
        row = self.model.row_of(path)
        if row < 0:
            return False
        proxy_index = self.proxy.mapFromSource(self.model.index(row, 0))
        if not proxy_index.isValid():
            return False
        self._select_row(proxy_index.row())
        return True

    def _selection_changed(self) -> None:
        tracks = self.selected_tracks()
        if self.editor.has_unsaved() and [t.path for t in tracks] != [t.path for t in self.editor.tracks]:
            if not self._confirm_unsaved():
                return
        self._load_details(tracks)

    def _load_details(self, tracks: list[Track]) -> None:
        self._details_gen += 1
        gen = self._details_gen
        self.editor.show_tracks(tracks, None)
        if not tracks:
            return
        first = tracks[0].path

        def done(details, _err) -> None:  # type: ignore[no-untyped-def]
            if gen == self._details_gen and details is not None:
                self.editor.show_tracks(tracks, details)

        self.scan_bg.run(lambda: tags.read(first, with_details=True), done)

    def _confirm_unsaved(self) -> bool:
        """Unsaved tag edits: save them, throw them away, or stay. True = carry on."""
        if not self.editor.has_unsaved():
            return True
        names = ", ".join(t.name for t in self.editor.tracks[:3])
        box = QMessageBox(QMessageBox.Icon.Question, "Unsaved tags", f"Save your changes to {names}?",
                          QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard, self)
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        if box.exec() == QMessageBox.StandardButton.Save:
            self.save_tags([t.path for t in self.editor.tracks], dict(self.editor.changed))
        self.editor.changed = {}
        return True

    # ================================================================ playing
    def play_row(self, proxy_row: int) -> None:
        index = self.proxy.index(proxy_row, 0)
        if not index.isValid():
            return
        track = self.model.tracks[self.proxy.mapToSource(index).row()]
        self.play_track(track)

    def play_track(self, track: Track) -> None:
        if not self.player.available():
            self.flash("Playing isn't available (Qt Multimedia is missing).")
            return
        title = " – ".join(x for x in (track.artist, track.title) if x) or track.name
        self.player.play(track.path, title)
        self.model.set_playing(track.path)

    def play_selected(self) -> None:
        tracks = self.selected_tracks()
        if tracks:
            self.play_track(tracks[0])
        elif self.proxy.rowCount():
            self._select_row(0)
            self.play_row(0)

    def _playing_proxy_row(self) -> int:
        row = self.model.row_of(self.player.path or self.model.playing)
        if row < 0:
            return -1
        return self.proxy.mapFromSource(self.model.index(row, 0)).row()

    def play_relative(self, step: int) -> None:
        current = self._playing_proxy_row()
        if current < 0:
            rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
            current = rows[0] - step if rows else -step
        target = current + step
        if 0 <= target < self.proxy.rowCount():
            self._select_row(target)
            self.play_row(target)

    def _song_finished(self) -> None:
        if self.settings.autoplay_next:
            self.play_relative(1)

    def _release(self, paths: list[str]) -> tuple[str, int, bool] | None:
        """Let go of the playing file if it's one of paths (so it can be changed)."""
        if self.player.path and any(fileops.same_path(self.player.path, p) for p in paths):
            return self.player.release()
        return None

    def _resume(self, state: tuple[str, int, bool] | None, new_path: str | None = None) -> None:
        if not state:
            return
        path = new_path or state[0]
        if os.path.exists(path):
            track = next((t for t in self.model.tracks if fileops.same_path(t.path, path)), None)
            title = " – ".join(x for x in (track.artist, track.title) if x) if track else ""
            self.player.resume(path, state[1], state[2], title or os.path.basename(path))
            self.model.set_playing(path)

    # ================================================================ tag writing
    def _refresh_tracks(self, mapping: dict[str, str]) -> None:
        """mapping {old path: new path}: re-read those songs and update their rows."""
        def read() -> list[tuple[str, Track]]:
            out = []
            for old, new in mapping.items():
                track = tags.read_track(new)
                self.cache.forget(old)
                self.cache.put(track)
                out.append((old, track))
            return out

        def done(result, _err) -> None:  # type: ignore[no-untyped-def]
            for old, track in result or []:
                self.model.replace(old, track)
            selected = self.selected_tracks()
            if selected and any(t.path in mapping.values() for t in selected):
                self._load_details(selected)
            self.bg.run(self.cache.save)

        self.bg.run(read, done)

    def save_tags(self, paths: list[str], changes: dict) -> None:
        if not paths or not changes:
            return
        state = self._release(paths)
        self.editor.changed = {}
        self.status.setText(f"Saving tags of {len(paths)} song{'s' if len(paths) != 1 else ''}…")

        def work() -> list[tuple[str, str]]:
            failures = []
            for path in paths:
                try:
                    tags.write(path, changes)
                except Exception as exc:  # noqa: BLE001
                    failures.append((path, str(exc)))
            return failures

        def done(failures, error) -> None:  # type: ignore[no-untyped-def]
            self._resume(state)
            self._report("Saved tags", len(paths), failures, error)
            self._refresh_tracks({p: p for p in paths})

        self.bg.run(work, done)

    def set_rating(self, paths: list[str], stars: int) -> None:
        paths = [p for p in paths if tags.can_write(p)]
        if paths:
            self.save_tags(paths, {"rating": stars})

    # ================================================================ keep / delete
    def keep_selected(self) -> None:
        if self.view != "review" or self._busy:
            return
        tracks = self.selected_tracks()
        if not tracks:
            return
        if not self.settings.library_folder or not os.path.isdir(self.settings.library_folder):
            QMessageBox.information(self, "Keep", "Choose your Library folder in Settings first.")
            self.open_settings()
            return
        if not self._confirm_unsaved():
            return
        if any(convert.needs_convert(t.path) for t in tracks) and not self.ffmpeg:
            QMessageBox.warning(self, "Keep", "ffmpeg wasn't found, so FLAC songs can't be converted to MP3.")
            return
        rename = self.settings.rename_on_keep
        dialog = PlanDialog(
            "Keep", f"These songs will go into your Library ({self.settings.library_folder}):",
            tracks, self.settings, lambda pattern: keep.plan(tracks, self.settings, pattern), "Keep",
            pattern=rename, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if rename:
            self.settings.rename_pattern = dialog.current_pattern()
        rows = [(t, dest) for t, dest, _note in dialog.rows if dest]
        playing = self.player.is_playing()
        next_row = self._row_after([t.path for t, _ in rows])
        self._release([t.path for t, _ in rows])
        self._run_each(
            "Keeping", rows,
            lambda item: keep.keep_one(self.ffmpeg, item[0], item[1], self.settings),
            lambda results, failures: self._after_keep(results, failures, next_row, playing))

    def _after_keep(self, results: list, failures: list, next_row: str | None, playing: bool) -> None:
        kept = [item[0].path for item, _res in results]
        warnings = [w for _item, (_dest, ws) in results for w in ws]
        for p in kept:
            self.cache.forget(p)
        self.model.remove(kept)
        self._stale.add("library")
        self._report("Kept", len(kept), failures, None, warnings)
        self._after_removal(next_row, playing)

    def delete_selected(self) -> None:
        if self._busy:
            return
        paths = self.selected_paths()
        if not paths:
            return
        if self.settings.confirm_delete:
            what = os.path.basename(paths[0]) if len(paths) == 1 else f"these {len(paths)} songs"
            answer = QMessageBox.question(self, "Delete", f"Send {what} to the Recycle Bin?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.editor.changed = {}
        playing = self.player.is_playing() and any(fileops.same_path(self.player.path, p) for p in paths)
        next_row = self._row_after(paths)
        if self._release(paths) is None and self.player.is_playing():
            playing = False  # another song is playing: leave it alone

        def done(failures, error) -> None:  # type: ignore[no-untyped-def]
            failed = {p for p, _ in failures or []}
            gone = [p for p in paths if p not in failed]
            for p in gone:
                self.cache.forget(p)
            self.model.remove(gone)
            self._report("Deleted", len(gone), failures, error)
            self._after_removal(next_row, playing)

        self.bg.run(lambda: fileops.recycle(paths), done)

    def _row_after(self, paths: list[str]) -> str | None:
        """The song shown after the last of paths (to select once they're gone)."""
        keys = {os.path.normcase(p) for p in paths}
        rows = [self.proxy.mapFromSource(self.model.index(self.model.row_of(p), 0)).row() for p in paths]
        rows = [r for r in rows if r >= 0]
        if not rows:
            return None
        for r in list(range(max(rows) + 1, self.proxy.rowCount())) + list(range(min(rows) - 1, -1, -1)):
            track = self.model.tracks[self.proxy.mapToSource(self.proxy.index(r, 0)).row()]
            if os.path.normcase(track.path) not in keys:
                return track.path
        return None

    def _after_removal(self, next_path: str | None, playing: bool) -> None:
        if next_path and self._select_path(next_path):
            if playing and self.settings.autoplay_next:
                self.play_selected()
        elif not self.model.tracks:
            self.player.clear()
            self.model.set_playing("")

    # ================================================================ convert / rename / gain
    def convert_selected(self) -> None:
        tracks = [t for t in self.selected_tracks() if convert.needs_convert(t.path)]
        if not tracks:
            self.flash("Select FLAC (or other non-MP3) songs to convert.")
            return
        if not self.ffmpeg:
            QMessageBox.warning(self, "Convert", "ffmpeg wasn't found, so songs can't be converted.")
            return
        quality = self.settings.mp3_quality

        def build(_pattern: str) -> list:
            return [(t, os.path.splitext(t.name)[0] + ".mp3", f"MP3 {quality}" + (
                ", original to the Recycle Bin" if self.settings.recycle_after_convert else "")) for t in tracks]

        dialog = PlanDialog("Convert to MP3", "Each MP3 is saved next to the original, with the same tags, "
                            "art, lyrics and rating:", tracks, self.settings, build, "Convert", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._release([t.path for t in tracks])
        self._run_each(
            "Converting", tracks,
            lambda t: convert.to_mp3(self.ffmpeg, t.path, quality, self.settings.recycle_after_convert),
            lambda results, failures: (self._report("Converted", len(results), failures, None), self.reload()))

    def rename_selected(self) -> None:
        tracks = self.selected_tracks()
        if not tracks:
            return
        dialog = PlanDialog("Rename", "New names from the songs' tags:", tracks, self.settings,
                            lambda pattern: naming.plan(tracks, pattern), "Rename", pattern=True, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._do_rename({t.path: dest for t, dest, _ in dialog.rows if dest})

    def rename_one(self) -> None:
        tracks = self.selected_tracks()
        if len(tracks) != 1:
            return
        track = tracks[0]
        name = ask_text(self, "Rename", "New name:", track.name)
        if not name or name == track.name:
            return
        problem = fileops.check_new_name(track.path, name)
        if problem:
            QMessageBox.warning(self, "Rename", problem)
            return
        self._do_rename({track.path: os.path.join(os.path.dirname(track.path), name.strip())})

    def _do_rename(self, plan: dict[str, str]) -> None:
        if not plan:
            return
        state = self._release(list(plan))

        def done(result, error) -> None:  # type: ignore[no-untyped-def]
            renamed, failures = result or ({}, [])
            self._report("Renamed", len(renamed), failures, error)
            if state:
                self._resume(state, renamed.get(state[0]))
            self._refresh_tracks(renamed)
            if renamed:
                QTimer.singleShot(200, lambda: self._select_path(next(iter(renamed.values()))))

        self.bg.run(lambda: fileops.rename(plan), done)

    def measure_selected(self) -> None:
        tracks = [t for t in self.selected_tracks() if tags.can_write(t.path)]
        if not tracks:
            return
        if not self.ffmpeg:
            QMessageBox.warning(self, "Measure volume", "ffmpeg wasn't found.")
            return
        state = self._release([t.path for t in tracks])

        def finished(results, failures) -> None:  # type: ignore[no-untyped-def]
            self._resume(state)
            self._report("Measured the volume of", len(results), failures, None)
            self._refresh_tracks({t.path: t.path for t, _ in results})

        self._run_each("Measuring volume", tracks, lambda t: loudness.analyze(self.ffmpeg, t.path), finished)

    # ================================================================ helpers
    def _run_each(self, verb: str, items: list, fn: Callable, finished: Callable) -> None:
        """Run fn(item) for each item in the background, one after another, showing progress.
        finished(results [(item, result)], failures [(name, error)]) runs on the UI thread."""
        self._busy = True
        results: list = []
        failures: list = []

        def name_of(item) -> str:  # type: ignore[no-untyped-def]
            track = item[0] if isinstance(item, tuple) else item
            return track.name if isinstance(track, Track) else str(track)

        def step(i: int) -> None:
            if i >= len(items):
                self._busy = False
                finished(results, failures)
                return
            self.status.setText(f"{verb} {i + 1} of {len(items)}: {name_of(items[i])}…")

            def done(result, error) -> None:  # type: ignore[no-untyped-def]
                if error:
                    failures.append((name_of(items[i]), error))
                else:
                    results.append((items[i], result))
                step(i + 1)

            self.bg.run(lambda: fn(items[i]), done)

        step(0)

    def _report(self, verb: str, count: int, failures, error: str | None,  # type: ignore[no-untyped-def]
                warnings: list[str] | None = None) -> None:
        failures = list(failures or [])
        if error:
            failures.append(("", error))
        text = f"{verb} {count} song{'s' if count != 1 else ''}." if count else ""
        if failures:
            lines = "\n".join(f"• {os.path.basename(p) + ': ' if p else ''}{e}" for p, e in failures[:12])
            QMessageBox.warning(self, "Some songs couldn't be changed", f"{text}\n\n{lines}".strip())
        if warnings:
            text += "  " + " ".join(sorted(set(warnings)))[:200]
        self.flash(text)

    def flash(self, text: str) -> None:
        self.status.setText(text)

    def _context_menu(self, pos) -> None:  # type: ignore[no-untyped-def]
        tracks = self.selected_tracks()
        if not tracks:
            return
        menu = QMenu(self)
        menu.addAction("▶ Play", self.play_selected)
        if self.view == "review":
            menu.addAction("✔ Keep (K)", self.keep_selected)
        menu.addAction("🗑 Delete", self.delete_selected)
        menu.addSeparator()
        if any(convert.needs_convert(t.path) for t in tracks):
            menu.addAction("Convert to MP3…", self.convert_selected)
        menu.addAction("Rename from tags…", self.rename_selected)
        if len(tracks) == 1:
            menu.addAction("Rename… (F2)", self.rename_one)
        menu.addAction("Measure volume", self.measure_selected)
        rating = menu.addMenu("Rating")
        for n in range(5, -1, -1):
            rating.addAction(("★" * n) or "No rating", lambda n=n: self.set_rating(self.selected_paths(), n))
        menu.addSeparator()
        menu.addAction("Show in Explorer (Ctrl+E)", self.show_in_explorer)
        menu.addAction("Open in default app", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(tracks[0].path)))
        menu.addAction("Copy path", lambda: QGuiApplication.clipboard().setText(
            "\n".join(t.path for t in tracks)))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _header_menu(self, pos) -> None:  # type: ignore[no-untyped-def]
        header = self.table.horizontalHeader()
        menu = QMenu(self)
        for col in range(self.model.columnCount()):
            title = self.model.headerData(col, Qt.Orientation.Horizontal)
            a = menu.addAction(title)
            a.setCheckable(True)
            a.setChecked(not header.isSectionHidden(col))
            a.toggled.connect(lambda on, col=col: header.setSectionHidden(col, not on))
        menu.exec(header.mapToGlobal(pos))

    def show_in_explorer(self) -> None:
        tracks = self.selected_tracks()
        if not tracks:
            return
        path = os.path.normpath(tracks[0].path)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", path])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def open_settings(self, first_run: bool = False) -> None:
        old = (self.settings.review_folder, self.settings.library_folder,
               self.settings.review_subfolders, self.settings.library_subfolders)
        dialog = SettingsDialog(self.settings, self, first_run=first_run)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        apply_theme(self.settings.theme)
        self.settings.save()
        new = (self.settings.review_folder, self.settings.library_folder,
               self.settings.review_subfolders, self.settings.library_subfolders)
        if new != old and not first_run:
            self._stale.update(VIEWS)
            self.reload()

    # ================================================================ closing
    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._confirm_unsaved()
        self.player.clear()
        self._scan_gen += 1
        self.settings.volume = self.player.volume.value()
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.settings.window_state = bytes(self.saveState().toBase64()).decode()
        self.settings.column_state["table"] = bytes(self.table.horizontalHeader().saveState().toBase64()).decode()
        try:
            self.settings.save()
        except OSError:
            pass
        self.bg.shutdown()
        self.scan_bg.shutdown()
        self.cache.save()
        super().closeEvent(event)

