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
    QStackedWidget,
    QTableView,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core import convert, fileops, keep, library, loudness, naming, online, repair, tags
from ..core.ffmpeg import find_ffmpeg
from ..core.plex import Plex, by_file
from ..core.plex import match as plex_match
from ..core.settings import Settings
from ..core.tags import Track
from .albums import AlbumModel, ArtDelegate, thumbnail
from .dialogs import PlanDialog, SettingsDialog, ask_text
from .editor import TagEditor
from .jump import JumpBar, JumpTable, Typed, find
from .lookup import LookupDialog, net
from .model import COLUMN_KEYS, HIDDEN_BY_DEFAULT, PLEX_PLAYS, TrackFilter, TrackModel
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
COLUMNS_STATE_KEY = "table3"
ALBUM_COLUMNS_KEY = "albums1"  # changed when columns are added, so the saved layout isn't misapplied


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, ffmpeg: str | None = None):
        super().__init__()
        self.settings = settings
        self.ffmpeg = ffmpeg if ffmpeg is not None else find_ffmpeg(settings.ffmpeg_path or None)
        self.bg = Background(1)        # tag writes, conversions, file operations (in order)
        self.scan_bg = Background(1)   # reading folders
        self.cover_bg = Background(2)  # album covers for the album view
        self.cache = library.TagCache()
        tags.EDITION_COMM = settings.edition_comm
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
        self._plex_reading = False
        self._plex_tracks: tuple | None = None
        self._plex_timer = QTimer(self)
        self._plex_timer.setInterval(10 * 60 * 1000)  # play counts every 10 minutes
        self._plex_timer.timeout.connect(self.refresh_plex_plays)
        self._plex_timer.start()
        self._plex_scan_timer = QTimer(self)
        self._plex_scan_timer.setSingleShot(True)
        self._plex_scan_timer.setInterval(5000)
        self._plex_scan_timer.timeout.connect(self._plex_rescan)
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
        # A panel you can drag to any edge of the window (or float); on the top or bottom edge
        # Review and Library sit side by side, so it takes one row.
        self.folders_dock = QDockWidget("Folders", self)
        self.folders_dock.setObjectName("folders")
        self.folders_dock.setWidget(left)
        self.folders_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                                      | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.folders_dock.setToolTip("Drag this panel by its title to move it to another edge of the window")
        self.folders_dock.dockLocationChanged.connect(self._folders_moved)
        self._folders_left = left

        self.table = JumpTable()
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
        header.customContextMenuRequested.connect(lambda pos: self._header_menu(pos, self.table))
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        self.album_model = AlbumModel(self.model)
        self.album_model.layout.update(self.settings.album_layout)
        self.album_model.cover_loader = self._load_cover
        self.albums = JumpTable()
        self.albums.setModel(self.album_model)
        self.albums.setItemDelegate(ArtDelegate(self.album_model, self.albums))
        self.albums.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.albums.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.albums.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.albums.verticalHeader().setVisible(False)
        self.albums.verticalHeader().setDefaultSectionSize(24)
        self.albums.setShowGrid(False)
        self.albums.setWordWrap(False)
        self.albums.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.albums.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        aheader = self.albums.horizontalHeader()
        aheader.setSectionsMovable(False)  # the album cells are spans, which don't survive moved columns
        aheader.setStretchLastSection(True)
        aheader.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        aheader.customContextMenuRequested.connect(lambda pos: self._header_menu(pos, self.albums))
        self._album_timer = QTimer(self)
        self._album_timer.setSingleShot(True)
        self._album_timer.setInterval(60)
        self._album_timer.timeout.connect(self._rebuild_albums)
        for signal in (self.proxy.modelReset, self.proxy.rowsInserted, self.proxy.rowsRemoved,
                       self.proxy.dataChanged, self.proxy.layoutChanged):
            signal.connect(lambda *_: self._album_timer.start())

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.albums)
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        self.jump_bar = JumpBar()
        self.jump_bar.jump.connect(self.jump_to)
        self._typed = Typed()
        cv.addWidget(self.jump_bar)
        cv.addWidget(self.stack, 1)
        for view in (self.table, self.albums):
            view.typed.connect(self.jump_to)

        self.setCentralWidget(center)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.folders_dock)

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
        for view in (self.table, self.albums):
            view.doubleClicked.connect(lambda index, view=view: self.play_row(index.row()))
            view.selectionModel().selectionChanged.connect(lambda *_, view=view: self._selection_changed(view))
            view.customContextMenuRequested.connect(lambda pos, view=view: self._context_menu(pos, view))
        self.albums.clicked.connect(self._album_clicked)
        self.editor.save_requested.connect(self.save_tags)
        self.editor.rating_requested.connect(self.set_rating)
        self.editor.lookup_requested.connect(self.lookup_selected)
        self.editor.repair_requested.connect(self.repair_selected)
        self.editor.lyrics_requested.connect(self.find_lyrics_selected)
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
        self.layout_btn = QToolButton()
        self.layout_btn.setText("▦ Layout")
        self.layout_btn.setToolTip("Albums or song list, grouping and sorting (F3 switches between the two)")
        self.layout_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.layout_menu = QMenu(self)
        self.layout_menu.aboutToShow.connect(self._fill_layout_menu)
        self.layout_btn.setMenu(self.layout_menu)
        bar.addWidget(self.layout_btn)
        action("⟳ Refresh", lambda: (self.reload(), self.refresh_plex_plays()), "F5",
               "Read the folder again (and Plex's play counts)")
        action("⚙ Settings", self.open_settings, "Ctrl+,")

        action("Save tags", self.editor.save, "Ctrl+S", toolbar=False)
        action("Albums / song list", self.toggle_layout, "F3", toolbar=False)
        action("Look up online", self.lookup_selected, "Ctrl+L", toolbar=False)
        action("Find lyrics", self.find_lyrics_selected, "Ctrl+Shift+L", toolbar=False)
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
            for view in (self.table, self.albums):
                QShortcut(QKeySequence(keys), view, self.play_selected, context=Qt.ShortcutContext.WidgetShortcut)

    def _restore_layout(self) -> None:
        if self.settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(self.settings.window_geometry.encode()))
        if self.settings.window_state:
            self.restoreState(QByteArray.fromBase64(self.settings.window_state.encode()))
        header = self.table.horizontalHeader()
        state = self.settings.column_state.get(COLUMNS_STATE_KEY)
        if state:
            header.restoreState(QByteArray.fromBase64(state.encode()))
        else:
            for col, key in enumerate(COLUMN_KEYS):
                header.setSectionHidden(col, key in HIDDEN_BY_DEFAULT)
            widths = {"rating": 70, "plays": 50, "title": 240, "artist": 180, "album": 180, "year": 50, "length": 55,
                      "bitrate": 70, "format": 50, "lyrics": 50, "cover": 40, "gain": 75}
            for col, key in enumerate(COLUMN_KEYS):
                if key in widths:
                    header.resizeSection(col, widths[key])

    def _restore_album_columns(self) -> None:
        header = self.albums.horizontalHeader()
        state = self.settings.column_state.get(ALBUM_COLUMNS_KEY)
        if state and header.restoreState(QByteArray.fromBase64(state.encode())):
            return
        shown = {"art", "track", "title", "artist", "album_artist", "rating", "plays", "length"}
        keys = [k for k, _ in self.album_model.columns]
        for col, key in enumerate(keys):
            header.setSectionHidden(col, key not in shown)
        widths = {"art": 380, "track": 40, "title": 300, "artist": 200, "album_artist": 180, "rating": 80,
                  "plays": 50, "length": 55}

        for col, key in enumerate(keys):
            if key in widths:
                header.resizeSection(col, widths[key])

    def jump_to(self, letter: str) -> None:
        """Scroll to the first artist starting with the letters clicked or typed (M, then Y: My…)."""
        prefix = self._typed.add(letter)
        order = self.order()
        i = find(order, prefix)
        if i < 0:
            return
        view = self.active()
        row = self._row_of(order[i].path)
        if self.album_mode():
            # Show the artist heading (or the album) at the top, not just the song.
            rows = self.album_model.rows
            start = row
            while start > 0 and rows[start - 1].kind != "gap" and rows[start].kind != "section":
                start -= 1
            if start > 0 and rows[start - 1].kind == "section":
                start -= 1
            row = start
        index = view.model().index(row, 0)
        view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtTop)
        self.flash(f"Jumped to “{prefix.upper()}”: {order[i].album_artist or order[i].artist or order[i].name}")

    def _folders_moved(self, area: Qt.DockWidgetArea) -> None:
        across = area in (Qt.DockWidgetArea.TopDockWidgetArea, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.views.setFlow(QListWidget.Flow.LeftToRight if across else QListWidget.Flow.TopToBottom)
        self.views.setWrapping(False)
        if across:
            self.views.setFixedHeight(self.views.sizeHintForRow(0) + 8)
            self.views.setMaximumWidth(260)
            self.view_hint.hide()
            self._folders_left.setMaximumWidth(16777215)
            self._folders_left.layout().setDirection(QVBoxLayout.Direction.LeftToRight)
        else:
            self.views.setFixedHeight(self.views.sizeHintForRow(0) * len(VIEWS) + 8)
            self.views.setMaximumWidth(220)
            self.view_hint.show()
            self._folders_left.setMaximumWidth(220)
            self._folders_left.layout().setDirection(QVBoxLayout.Direction.TopToBottom)

    # ================================================================ Plex
    def plex_ready(self) -> bool:
        return bool(self.settings.plex_url.strip() and self.settings.plex_token.strip())

    def refresh_plex_plays(self) -> None:
        """Read every song's play count from Plex (in the background)."""
        if not (self.plex_ready() and self.settings.plex_plays) or self._plex_reading:
            if not self.settings.plex_plays and PLEX_PLAYS:
                PLEX_PLAYS.clear()
                self._plex_changed()
            return
        self._plex_reading = True
        client = Plex(self.settings.plex_url, self.settings.plex_token)

        def done(result, error) -> None:  # type: ignore[no-untyped-def]
            self._plex_reading = False
            if error:
                self.flash(f"Plex play counts not updated: {error}")
                return
            self._plex_tracks = by_file(result or [])
            self._apply_plex()

        net().run(client.all_tracks, done)

    def _apply_plex(self) -> None:
        if not self._plex_tracks:
            return
        paths, names = self._plex_tracks
        for t in self.model.tracks:
            found = plex_match(t.path, paths, names)
            if found is not None:
                PLEX_PLAYS[os.path.normcase(t.path)] = found.plays
        self._plex_changed()

    def _plex_changed(self) -> None:
        if self.model.tracks:
            self.model.dataChanged.emit(self.model.index(0, 0),
                                        self.model.index(len(self.model.tracks) - 1, self.model.columnCount() - 1))
        if self.album_model.rows:
            self.album_model.dataChanged.emit(self.album_model.index(0, 0), self.album_model.index(
                len(self.album_model.rows) - 1, self.album_model.columnCount() - 1))

    def plex_rescan_soon(self) -> None:
        """After Keep or tag changes in the Library: ask Plex to rescan the folder (once, a few seconds later)."""
        if self.plex_ready() and self.settings.plex_refresh and self.settings.library_folder:
            self._plex_scan_timer.start()

    def _plex_rescan(self) -> None:
        client = Plex(self.settings.plex_url, self.settings.plex_token)
        folder = self.settings.library_folder

        def done(name, error) -> None:  # type: ignore[no-untyped-def]
            self.flash(f"Plex couldn't rescan the Library: {error}" if error else f"Plex is rescanning “{name}”.")

        net().run(lambda: client.refresh_folder(folder), done)

    def _start(self) -> None:
        self._folders_moved(self.dockWidgetArea(self.folders_dock))
        self._restore_album_columns()
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
        self._apply_mode()
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
                QTimer.singleShot(100, self._select_first)
            end = start + len(chunk)
            if end < len(paths):
                self.status.setText(f"Reading tags… {end} of {len(paths)}")
                self._scan_chunk(gen, paths, end)
            else:
                self.status.setText("")
                self.scan_bg.run(self.cache.save)
                self._update_choices()
                if self._plex_tracks is None:
                    self.refresh_plex_plays()
                else:
                    self._apply_plex()

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
    # The song list and the album view show the same songs; these work on whichever is shown.
    def album_mode(self) -> bool:
        return self.settings.view_modes.get(self.view, "list") == "albums"

    def active(self) -> QTableView:
        return self.albums if self.album_mode() else self.table

    def _track_at(self, row: int) -> Track | None:
        if self.album_mode():
            return self.album_model.track(row)
        index = self.proxy.index(row, 0)
        return self.model.tracks[self.proxy.mapToSource(index).row()] if index.isValid() else None

    def _row_of(self, path: str) -> int:
        if self.album_mode():
            return self.album_model.row_of(path)
        row = self.model.row_of(path)
        return self.proxy.mapFromSource(self.model.index(row, 0)).row() if row >= 0 else -1

    def order(self) -> list[Track]:
        """The songs in the order shown."""
        if self.album_mode():
            return self.album_model.order()
        return [self.model.tracks[self.proxy.mapToSource(self.proxy.index(r, 0)).row()]
                for r in range(self.proxy.rowCount())]

    def selected_tracks(self) -> list[Track]:
        rows = sorted({i.row() for i in self.active().selectionModel().selectedRows(1 if self.album_mode() else 0)})
        if self.album_mode() and not rows:
            rows = sorted({i.row() for i in self.active().selectionModel().selectedIndexes()})
        out = [self._track_at(r) for r in rows]
        return [t for t in out if t is not None]

    def selected_paths(self) -> list[str]:
        return [t.path for t in self.selected_tracks()]

    def _select_rows(self, rows: list[int]) -> None:
        view = self.active()
        model = view.model()
        selection = view.selectionModel()
        if not rows:
            selection.clearSelection()
            return
        from PySide6.QtCore import QItemSelection

        sel = QItemSelection()
        last = model.columnCount() - 1
        for r in rows:
            sel.select(model.index(r, 1 if self.album_mode() else 0), model.index(r, last))
        selection.setCurrentIndex(model.index(rows[0], 1 if self.album_mode() else 0),
                                  QItemSelectionModel.SelectionFlag.NoUpdate)
        selection.select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        view.scrollTo(model.index(rows[0], 1 if self.album_mode() else 0))

    def _select_first(self) -> None:
        order = self.order()
        if order:
            self._select_path(order[0].path)

    def _select_path(self, path: str) -> bool:
        row = self._row_of(path)
        if row < 0:
            return False
        self._select_rows([row])
        return True

    def _album_clicked(self, index) -> None:  # type: ignore[no-untyped-def]
        """Clicking an album's cover selects all its songs."""
        if index.column() == 0 and self.album_mode():
            rows = self.album_model.album_rows(index.row())
            if rows:
                self._select_rows(rows)

    def _selection_changed(self, view: QTableView | None = None) -> None:
        if view is not None and view is not self.active():
            return
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
    def play_row(self, row: int) -> None:
        track = self._track_at(row)
        if track is not None:
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
            return
        order = self.order()
        if order:
            self._select_path(order[0].path)
            self.play_track(order[0])

    def play_relative(self, step: int) -> None:
        order = self.order()
        keys = [os.path.normcase(t.path) for t in order]
        current = self.player.path or self.model.playing
        if current and os.path.normcase(current) in keys:
            pos = keys.index(os.path.normcase(current))
        else:
            selected = self.selected_tracks()
            pos = keys.index(os.path.normcase(selected[0].path)) - step if selected else -step
        target = pos + step
        if 0 <= target < len(order):
            self._select_path(order[target].path)
            self.play_track(order[target])

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

    def _update_choices(self) -> None:
        """Genre, Language and Tempo drop-downs: every value used in either folder (from the tag cache)."""
        found: dict[str, dict[str, str]] = {"genre": {}, "language": {}, "tempo": {}}
        tracks = [t.to_dict() for t in self.model.tracks] + list(self.cache._items.values())
        for item in tracks:
            for key, values in found.items():
                parts = str(item.get(key) or "").split(";") if key == "genre" else [str(item.get(key) or "")]
                for part in parts:
                    part = part.strip()
                    if part:
                        values.setdefault(part.casefold(), part)
        self.editor.set_choices({k: sorted(v.values(), key=str.casefold) for k, v in found.items()})

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
            library = self.settings.library_folder
            if library and any(fileops.same_path(os.path.dirname(p), library) or
                               os.path.normcase(p).startswith(os.path.normcase(library) + os.sep) for p in paths):
                self.plex_rescan_soon()
            self._refresh_tracks({p: p for p in paths})

        self.bg.run(work, done)

    def lookup_selected(self) -> None:
        tracks = self.selected_tracks()
        if len(tracks) != 1:
            self.flash("Select one song to look up.")
            return
        track = tracks[0]
        if not tags.can_write(track.path):
            self.flash("Convert this song to MP3 first to change its tags.")
            return
        details = self.editor._details if self.editor.tracks == tracks else None
        dialog = LookupDialog(track, details, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if [t.path for t in self.editor.tracks] != [track.path]:
            return  # the selection changed meanwhile
        self.editor.apply_lookup(dialog.chosen_fields, dialog.chosen_cover, dialog.chosen_lyrics)
        picked = len(dialog.chosen_fields) + bool(dialog.chosen_cover) + (dialog.chosen_lyrics is not None)
        self.flash(f"{picked} change{'s' if picked != 1 else ''} from the lookup are in the Tags panel: "
                   "check them and click Save." if picked else "Nothing was picked.")

    def find_lyrics_selected(self) -> None:
        tracks = [t for t in self.selected_tracks() if tags.can_write(t.path) and not t.error]
        if not tracks or self._busy:
            self.flash("Select songs to find lyrics for (MP3 or FLAC).")
            return
        if len(tracks) == 1:
            track = tracks[0]
            details = self.editor._details if [t.path for t in self.editor.tracks] == [track.path] else None
            dialog = LookupDialog(track, details, self, lyrics_only=True)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.chosen_lyrics is not None:
                self.editor.changed.pop("lyrics", None)
                self.save_tags([track.path], {"lyrics": dialog.chosen_lyrics})
            return
        missing = [t for t in tracks if not t.has_lyrics]
        if not missing:
            self.flash(f"All {len(tracks)} songs already have lyrics.")
            return
        answer = QMessageBox.question(
            self, "Find lyrics",
            f"Look up lyrics for the {len(missing)} of these {len(tracks)} songs that have none, and save "
            "them straight away (in the file and as a .txt)?\n\nLRCLIB is tried first, then lyrics.ovh and "
            "Genius. Songs with lyrics already are left alone.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        state = self._release([t.path for t in missing])

        def one(t: Track) -> bool:
            found = online.best_lyrics(t.artist, t.title or os.path.splitext(t.name)[0], t.album, t.length)
            if not found:
                return False
            tags.write(t.path, {"lyrics": found.text})
            return True

        def finished(results, failures) -> None:  # type: ignore[no-untyped-def]
            self._resume(state)
            got = [t for t, ok in results if ok]
            none = [t.name for t, ok in results if not ok]
            self._report("Found lyrics for", len(got), failures, None)
            if none:
                QMessageBox.information(self, "Find lyrics", f"No lyrics were found for {len(none)} songs:\n\n"
                                        + "\n".join(f"• {n}" for n in none[:20]) +
                                        ("\n…" if len(none) > 20 else "") +
                                        "\n\nTry them one at a time (the artist and title can be changed there).")
            self._refresh_tracks({t.path: t.path for t in got})

        self._run_each("Finding lyrics", missing, one, finished)

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
        if kept:
            self.plex_rescan_soon()
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
        order = self.order()
        positions = [i for i, t in enumerate(order) if os.path.normcase(t.path) in keys]
        if not positions:
            return None
        for i in list(range(max(positions) + 1, len(order))) + list(range(min(positions) - 1, -1, -1)):
            if os.path.normcase(order[i].path) not in keys:
                return order[i].path
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

    def repair_selected(self) -> None:
        tracks = [t for t in self.selected_tracks() if t.error]
        if not tracks or self._busy:
            return
        if not self.ffmpeg:
            QMessageBox.warning(self, "Repair", "ffmpeg wasn't found, so files can't be repaired.")
            return
        notes = [f"• {t.name}: {repair.describe(t.path)[0]}" for t in tracks[:8]]
        answer = QMessageBox.question(
            self, "Repair", "\n".join(notes) + "\n\nTags, art, lyrics and rating are kept. The original goes to "
            "the Recycle Bin and the repaired file keeps the same name. Repair now?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        state = self._release([t.path for t in tracks])

        def finished(results, failures) -> None:  # type: ignore[no-untyped-def]
            self._resume(state)
            self._report("Repaired", len(results), failures, None)
            self._refresh_tracks({t.path: t.path for t, _ in results})

        self._run_each("Repairing", tracks,
                       lambda t: repair.repair(self.ffmpeg, t.path, self.settings.mp3_quality), finished)

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

    def _context_menu(self, pos, view: QTableView) -> None:  # type: ignore[no-untyped-def]
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
        if any(t.error for t in tracks):
            menu.addAction("🛠 Repair file…", self.repair_selected)
        if len(tracks) == 1:
            menu.addAction("🔎 Look up online… (Ctrl+L)", self.lookup_selected)
        menu.addAction("🔎 Find lyrics (Ctrl+Shift+L)", self.find_lyrics_selected)
        rating = menu.addMenu("Rating")
        for n in range(5, -1, -1):
            rating.addAction(("★" * n) or "No rating", lambda n=n: self.set_rating(self.selected_paths(), n))
        menu.addSeparator()
        menu.addAction("Show in Explorer (Ctrl+E)", self.show_in_explorer)
        menu.addAction("Open in default app", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(tracks[0].path)))
        menu.addAction("Copy path", lambda: QGuiApplication.clipboard().setText(
            "\n".join(t.path for t in tracks)))
        menu.exec(view.viewport().mapToGlobal(pos))

    def _header_menu(self, pos, view: QTableView) -> None:  # type: ignore[no-untyped-def]
        header = view.horizontalHeader()
        model = view.model()
        menu = QMenu(self)
        for col in range(model.columnCount()):
            title = model.headerData(col, Qt.Orientation.Horizontal)
            a = menu.addAction(title)
            a.setCheckable(True)
            a.setChecked(not header.isSectionHidden(col))
            a.toggled.connect(lambda on, col=col, view=view: self._show_column(view, col, on))
        menu.exec(header.mapToGlobal(pos))

    def _show_column(self, view: QTableView, col: int, on: bool) -> None:
        view.horizontalHeader().setSectionHidden(col, not on)
        if view is self.albums:
            self.album_model.apply_spans(self.albums)

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
        self.refresh_plex_plays()

    # ================================================================ album view
    def _apply_mode(self) -> None:
        self.stack.setCurrentWidget(self.active())
        self.layout_btn.setText("▦ Albums" if self.album_mode() else "☰ Song list")
        if self.album_mode():
            self._rebuild_albums()

    def toggle_layout(self) -> None:
        self.set_mode("list" if self.album_mode() else "albums")

    def set_mode(self, mode: str) -> None:
        selected = self.selected_paths()
        self.settings.view_modes[self.view] = mode
        self._apply_mode()
        rows = [r for r in (self._row_of(p) for p in selected) if r >= 0]
        if rows:
            self._select_rows(rows)
        self._load_details(self.selected_tracks())

    def set_album_layout(self, key: str, value) -> None:  # type: ignore[no-untyped-def]
        self.album_model.layout[key] = value
        self.settings.album_layout = dict(self.album_model.layout)
        if key == "art":
            self.album_model.covers.clear()
        self._rebuild_albums()

    def _fill_layout_menu(self) -> None:
        from PySide6.QtGui import QActionGroup

        from ..core.albums import ALBUM_ORDERS, GROUPINGS

        menu = self.layout_menu
        menu.clear()
        layout = self.album_model.layout

        def radio(title: str, items: dict, current, slot) -> None:  # type: ignore[no-untyped-def]
            menu.addSection(title)
            group = QActionGroup(menu)
            for value, label in items.items():
                a = menu.addAction(label)
                a.setCheckable(True)
                a.setChecked(value == current)
                group.addAction(a)
                a.triggered.connect(lambda _=False, value=value: slot(value))

        radio("Show", {"albums": "Albums (cover, details and songs)", "list": "Song list"},
              "albums" if self.album_mode() else "list", self.set_mode)
        radio("Album view: headings", GROUPINGS, layout.get("group", "artist"),
              lambda v: self.set_album_layout("group", v))
        radio("Album view: albums in order of", ALBUM_ORDERS, layout.get("order", "year"),
              lambda v: self.set_album_layout("order", v))
        radio("Album view: artwork", {120: "Small", 150: "Medium", 200: "Large"}, layout.get("art", 150),
              lambda v: self.set_album_layout("art", v))
        menu.addSection("Album view: inside an album")
        for key, label in (("sides", "Headings for editions and discs (e.g. Deluxe Edition • Disc 01)"),
                           ("blank", "Blank line before those headings")):
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(bool(layout.get(key, True)))
            a.toggled.connect(lambda on, key=key: self.set_album_layout(key, on))

    def _rebuild_albums(self) -> None:
        if not self.album_mode():
            return
        selected = self.selected_paths()
        scroll = self.albums.verticalScrollBar().value()
        tracks = [self.model.tracks[self.proxy.mapToSource(self.proxy.index(r, 0)).row()]
                  for r in range(self.proxy.rowCount())]
        self.albums.selectionModel().blockSignals(True)
        self.album_model.rebuild(tracks)
        self.album_model.apply_spans(self.albums)
        rows = [r for r in (self.album_model.row_of(p) for p in selected) if r >= 0]
        if rows:
            from PySide6.QtCore import QItemSelection

            sel = QItemSelection()
            for r in rows:
                sel.select(self.album_model.index(r, 1), self.album_model.index(r, self.album_model.columnCount() - 1))
            self.albums.selectionModel().select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.albums.selectionModel().blockSignals(False)
        self.albums.verticalScrollBar().setValue(scroll)
        # Learn which MusicBee custom field holds "Edition", so new editions are written there too.
        if not tags.EDITION_COMM:
            fields = [t.edition_field for t in tracks if t.edition_field]
            if fields:
                tags.EDITION_COMM = max(set(fields), key=fields.count)
                self.settings.edition_comm = tags.EDITION_COMM

    def _load_cover(self, album) -> None:  # type: ignore[no-untyped-def]
        from PySide6.QtGui import QPixmap

        size = self.album_model.art_size() * 2  # sharp on high-DPI screens
        path, key = album.cover_path, album.key

        def work():  # type: ignore[no-untyped-def]
            return thumbnail(path, size)

        def done(image, _err) -> None:  # type: ignore[no-untyped-def]
            if image is not None and not image.isNull():
                self.album_model.set_cover(key, QPixmap.fromImage(image))

        self.cover_bg.run(work, done)

    # ================================================================ closing
    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._confirm_unsaved()
        self.player.clear()
        self._scan_gen += 1
        self.settings.volume = self.player.volume.value()
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.settings.window_state = bytes(self.saveState().toBase64()).decode()
        header_state = self.table.horizontalHeader().saveState().toBase64()
        self.settings.column_state[COLUMNS_STATE_KEY] = bytes(header_state).decode()
        album_state = self.albums.horizontalHeader().saveState().toBase64()
        self.settings.column_state[ALBUM_COLUMNS_KEY] = bytes(album_state).decode()
        self.settings.album_layout = dict(self.album_model.layout)
        try:
            self.settings.save()
        except OSError:
            pass
        self.bg.shutdown()
        self.scan_bg.shutdown()
        self.cover_bg.shutdown()
        self.cache.save()
        super().closeEvent(event)

