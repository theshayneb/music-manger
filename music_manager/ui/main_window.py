"""The main window: Review / Library on the left, the song list, the tag editor, the player."""

from __future__ import annotations

import os
import subprocess
import sys
import time
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
from .albums import AlbumModel, AlbumTable, ArtDelegate, thumbnail
from .dialogs import PlanDialog, SettingsDialog, ask_text
from .editor import ElidedLabel, TagEditor
from .jump import JumpBar, JumpTable, Typed, find
from .lookup import LookupDialog, net
from .lyrics_review import Found, LyricsReview
from .model import COLUMN_KEYS, HIDDEN_BY_DEFAULT, PLEX_PLAYS, TrackFilter, TrackModel, stars
from .player import PlayerBar
from .review import KeepReview
from .sidebar import CheckUp, NowCard, counts_for, inside, kept_this_week
from .theme import apply_theme
from .workers import Background

SHOW_FILTERS = [
    ("", "All songs"),
    ("tags", "Missing tags (title, artist, album or year)"),
    ("cover", "No album art"),
    ("lyrics", "No lyrics"),
    ("gain", "Volume not measured"),
    ("low", "Low bitrate (under 192 kbps)"),
    ("synced", "Lyrics with timestamps ([00:12.34] lines)"),
    ("notmp3", "Not MP3 (to convert)"),
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
        self.view = settings.last_view or "review"
        self._states: dict[str, dict] = {}  # per view: its songs, selection, scroll position, search
        self._pending_position: dict = {}
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
        self.views.setStyleSheet("QListWidget { font-size: 15px; } QListWidget::item { padding: 6px 8px; }")
        self.views.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.views.customContextMenuRequested.connect(self._views_menu)
        self._fill_views()
        self.add_view_btn = QToolButton()
        self.add_view_btn.setText("📁 Add Folder ▾")
        self.add_view_btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
        self.add_view_btn.setToolTip("Add a folder, or save the current search (e.g. Library songs with no album art)")
        self.add_view_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        add_menu = QMenu(self)
        add_menu.addAction("Add a folder…", self.add_folder_view)
        add_menu.addAction("Save the current search and Show filter…", self.save_search_view)
        self.add_view_btn.setMenu(add_menu)
        # The folder being shown, in the toolbar (one line; the whole path in the tooltip).
        self.view_hint = ElidedLabel("")
        self.view_hint.setFixedWidth(280)  # a toolbar gives a stretchy label no room, so: a set width
        self.view_hint.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.view_hint.setStyleSheet("color: palette(placeholder-text); padding: 0 6px;")
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.views)
        # Under the folders: the cover of the song playing (or selected), then the Library check-up.
        self.now_card = NowCard()
        lv.addWidget(self.now_card)
        self.checkup = CheckUp()
        self.checkup.picked.connect(self._open_check)
        lv.addWidget(self.checkup)
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
        self.albums = AlbumTable()
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
        # Columns can be dragged; the move is made in the model (see _album_column_moved), because the
        # album cells are spans, which don't survive moved header sections.
        aheader.setSectionsMovable(True)
        aheader.sectionMoved.connect(self._album_column_moved)
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
        self.now_card.set_art(self.editor.art_panel)  # the art and its buttons sit under the Folders list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.editor)
        scroll.setMinimumWidth(290)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        # After saving from the Tags panel the song list has the keyboard again (↓ for the next song).
        self.editor.save_requested.connect(lambda paths, changes: (self.save_tags(paths, changes), self.focus_list()))
        self.editor.rating_requested.connect(self.set_rating)
        self.editor.lookup_requested.connect(self.lookup_selected)
        self.editor.repair_requested.connect(self.repair_selected)
        self.editor.lyrics_requested.connect(self.find_lyrics_selected)
        self.player.next_requested.connect(lambda: self.play_relative(1))
        self.player.previous_requested.connect(lambda: self.play_relative(-1))
        self.player.play_requested.connect(self.play_selected)
        self.player.finished.connect(self._song_finished)
        if self.player.player is not None:  # the card follows playing / stopping
            self.player.player.playbackStateChanged.connect(lambda _s: self._update_now())
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
        self.layout_btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
        self.layout_menu = QMenu(self)
        self.layout_menu.aboutToShow.connect(self._fill_layout_menu)
        self.layout_btn.setMenu(self.layout_menu)
        bar.addWidget(self.view_hint)
        bar.addWidget(self.add_view_btn)
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
        """The album view's columns: their order, widths and which are shown, saved by name (the
        model's own column order is what moves, since its cover and heading cells span columns)."""
        saved = [c for c in self.settings.album_columns if isinstance(c, dict) and c.get("key")]
        if saved:
            by_key = {k: c for k, c in ((c[0], c) for c in self.album_model.columns)}
            order = [c["key"] for c in saved if c["key"] in by_key and c["key"] != "art"]
            rest = [k for k in by_key if k not in order and k != "art"]
            self.album_model.beginResetModel()
            self.album_model.columns = [by_key["art"]] + [by_key[k] for k in order + rest]
            self.album_model.endResetModel()
            self._apply_album_columns({c["key"]: c for c in saved})
            return
        shown = {"art", "track", "title", "artist", "album_artist", "rating", "plays", "length"}
        widths = {"art": 380, "track": 40, "title": 300, "artist": 200, "album_artist": 180, "rating": 90,
                  "plays": 50, "length": 55}
        self._apply_album_columns({k: {"width": widths.get(k, 100), "hidden": k not in shown}
                                   for k, _ in self.album_model.columns})

    def _album_columns_now(self) -> dict[str, dict]:
        header = self.albums.horizontalHeader()
        return {key: {"key": key, "width": header.sectionSize(col), "hidden": header.isSectionHidden(col)}
                for col, (key, _label) in enumerate(self.album_model.columns)}

    def _apply_album_columns(self, config: dict[str, dict]) -> None:
        header = self.albums.horizontalHeader()
        header.blockSignals(True)
        for col, (key, _label) in enumerate(self.album_model.columns):
            c = config.get(key, {})
            header.setSectionHidden(col, bool(c.get("hidden", False)) and key != "art")
            if c.get("width"):
                header.resizeSection(col, int(c["width"]))
        header.blockSignals(False)

    def _album_column_moved(self, _logical: int, old: int, new: int) -> None:
        """A column header was dragged: put the header back and move the column in the model instead."""
        header = self.albums.horizontalHeader()
        header.blockSignals(True)
        header.moveSection(new, old)
        header.blockSignals(False)
        config = self._album_columns_now()
        self.album_model.move_column(old, new)
        self._apply_album_columns(config)
        self._rebuild_albums()

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
            self.now_card.hide()
            self.checkup.hide()
            self._folders_left.setMaximumWidth(16777215)
            self._folders_left.layout().setDirection(QVBoxLayout.Direction.LeftToRight)
        else:
            self.views.setFixedHeight(min(10, self.views.count()) * max(28, self.views.sizeHintForRow(0)) + 8)
            self.views.setMaximumWidth(220)
            self.now_card.show()
            self.checkup.show()
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

    def send_ratings_to_plex(self, paths: list[str], stars: int) -> None:
        """Ratings changed here go to Plex too (when Plex is set up and has the songs)."""
        if not (paths and self.plex_ready() and self.settings.plex_ratings):
            return
        client = Plex(self.settings.plex_url, self.settings.plex_token)
        known = self._plex_tracks

        def work() -> tuple[int, int]:
            tracks = known or by_file(client.all_tracks())
            sent = missing = 0
            for path in paths:
                found = plex_match(path, *tracks)
                if found is None or not found.key:
                    missing += 1
                    continue
                client.rate(found.key, stars)
                sent += 1
            return sent, missing

        def done(result, error) -> None:  # type: ignore[no-untyped-def]
            if error:
                self.flash(f"The rating was saved in the file, but Plex couldn't be updated: {error}")
            elif result and result[1]:
                self.flash(f"Rating sent to Plex for {result[0]}; {result[1]} not found in Plex "
                           "(Plex may not have scanned them yet).")

        net().run(work, done)

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

    # ================================================================ under the folders
    def _update_now(self) -> None:
        """The card under the Folders list: the selected song (whose art the Tags panel edits); with
        nothing selected, the playing song's art, just to look at."""
        selected = self.selected_tracks()
        if selected:
            self.now_card.show_track(selected[0], playing=False)
            return
        if not self.player.path:
            self.now_card.show_track(None, playing=False)
            return
        row = self.model.row_of(self.player.path)
        track = self.model.tracks[row] if row >= 0 else self.cache.get(self.player.path)
        if self.now_card.path == track.path and self.now_card.playing:
            return
        self.now_card.show_track(track, playing=True)
        path = track.path

        def done(image, _err) -> None:  # type: ignore[no-untyped-def]
            from PySide6.QtGui import QPixmap

            if self.now_card.path == path and not self.selected_tracks():
                self.editor.preview_cover(QPixmap.fromImage(image) if image is not None else None)

        self.cover_bg.run(lambda: thumbnail(path, 400), done)

    def _update_checkup(self) -> None:
        """Recount the Library check-up (in the background, from the tag cache plus what's shown)."""
        library, review = self.settings.library_folder, self.settings.review_folder
        shown = {os.path.normcase(t.path): t for t in self.model.tracks}
        with self.cache._lock:  # a scan may be adding to it
            items = list(self.cache._items.values())
        cached = [Track.from_dict(v) for v in items]

        def work() -> dict[str, int]:
            songs = {os.path.normcase(t.path): t for t in cached if os.path.exists(t.path)}
            songs.update(shown)
            in_library = [t for t in songs.values() if inside(t.path, library)]
            in_review = [t for t in songs.values() if inside(t.path, review)]
            return counts_for(in_library, in_review)

        def done(counts, _err) -> None:  # type: ignore[no-untyped-def]
            if counts:
                self.checkup.set_counts(counts, kept_this_week(self.settings.kept_log))

        self.scan_bg.run(work, done)

    def _open_check(self, view: str, show: str) -> None:
        """A check-up line was clicked: show those songs."""
        if self.view != view:
            self._select_view_item(view)

        def apply() -> None:
            self.search.clear()
            self.show_filter.setCurrentIndex(max(0, self.show_filter.findData(show)))

        QTimer.singleShot(0, apply)

    def _start(self) -> None:
        self._folders_moved(self.dockWidgetArea(self.folders_dock))
        self._restore_album_columns()
        if not self.settings.review_folder and not self.settings.library_folder:
            self.open_settings(first_run=True)
        if self.view_def(self.view) is None:
            self.view = "review"
        self._select_view_item(self.view)
        QTimer.singleShot(500, self._update_checkup)
        if not self.ffmpeg:
            self.flash("ffmpeg wasn't found: converting to MP3 and measuring volume won't work.")

    # ================================================================ views & loading
    def view_defs(self) -> list[dict]:
        """Review, Library, your own folders, then your saved searches."""
        out = [{"key": "review", "label": "Review", "kind": "review"},
               {"key": "library", "label": "Library", "kind": "library"}]
        for f in self.settings.extra_folders:
            out.append({"key": "folder:" + f["path"], "label": f.get("label") or os.path.basename(f["path"]),
                        "kind": "folder", "path": f["path"], "subfolders": f.get("subfolders", True)})
        for q in self.settings.saved_searches:
            out.append({"key": "search:" + q["label"], "label": "🔍 " + q["label"], "kind": "search", **q})
        return out

    def view_def(self, key: str | None = None) -> dict | None:
        key = key or self.view
        return next((d for d in self.view_defs() if d["key"] == key), None)

    def folder(self, view: str | None = None) -> str:
        d = self.view_def(view) or {"kind": "review"}
        if d["kind"] == "search":
            return self.folder(d.get("source", "library"))
        if d["kind"] == "folder":
            return d["path"]
        return self.settings.review_folder if d["kind"] == "review" else self.settings.library_folder

    def subfolders(self, view: str | None = None) -> bool:
        d = self.view_def(view) or {"kind": "review"}
        if d["kind"] == "search":
            return self.subfolders(d.get("source", "library"))
        if d["kind"] == "folder":
            return bool(d.get("subfolders", True))
        return self.settings.review_subfolders if d["kind"] == "review" else self.settings.library_subfolders

    def can_keep(self, view: str | None = None) -> bool:
        """Keep moves songs into the Library, so it's for every folder but the Library itself."""
        folder = self.folder(view)
        return bool(folder) and not fileops.same_path(folder, self.settings.library_folder or "\0")

    def _fill_views(self) -> None:
        self.views.blockSignals(True)
        self.views.clear()
        for d in self.view_defs():
            item = QListWidgetItem(d["label"])
            item.setData(Qt.ItemDataRole.UserRole, d["key"])
            if d["kind"] == "search":
                where = self.view_def(d.get("source", "library"))
                item.setToolTip(f"In {where['label'] if where else d.get('source')}: "
                                f"“{d.get('search', '')}” {dict(SHOW_FILTERS).get(d.get('missing', ''), '')}")
            elif d["kind"] == "folder":
                item.setToolTip(d["path"])
            self.views.addItem(item)
        self.views.blockSignals(False)
        self.views.setFixedHeight(min(10, self.views.count()) * max(28, self.views.sizeHintForRow(0)) + 8)

    def _select_view_item(self, key: str) -> None:
        for row in range(self.views.count()):
            if self.views.item(row).data(Qt.ItemDataRole.UserRole) == key:
                self.views.setCurrentRow(row)
                return

    def add_folder_view(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(self, "Add a folder to the Folders panel")
        if not folder:
            return
        folder = os.path.normpath(folder)
        if any(fileops.same_path(folder, f["path"]) for f in self.settings.extra_folders):
            self._select_view_item("folder:" + folder)
            return
        label = ask_text(self, "Add a folder", "Name in the Folders panel:", os.path.basename(folder) or folder)
        if label is None:
            return
        self.settings.extra_folders.append({"path": folder, "label": label.strip() or os.path.basename(folder),
                                            "subfolders": True})
        self.settings.save()
        self._fill_views()
        self._select_view_item("folder:" + folder)

    def save_search_view(self) -> None:
        text, missing = self.search.text().strip(), self.show_filter.currentData()
        if not text and not missing:
            QMessageBox.information(self, "Save search", "Type something in the search box, or choose a Show "
                                    "filter (e.g. No album art), then save it here.")
            return
        d = self.view_def() or {"key": "library", "kind": "library"}
        source = d.get("source", d["key"]) if d["kind"] == "search" else d["key"]
        default = text or dict(SHOW_FILTERS).get(missing, "Search")
        label = ask_text(self, "Save search", "Name in the Folders panel:", default)
        if not label or not label.strip():
            return
        label = label.strip()
        self.settings.saved_searches = [q for q in self.settings.saved_searches if q["label"] != label]
        self.settings.saved_searches.append({"label": label, "source": source, "search": text, "missing": missing})
        self.settings.save()
        # The search now lives in its own entry: the view it came from goes back to showing everything.
        self.search.clear()
        self.show_filter.setCurrentIndex(0)
        self._fill_views()
        self._select_view_item("search:" + label)

    def _views_menu(self, pos) -> None:  # type: ignore[no-untyped-def]
        item = self.views.itemAt(pos)
        d = self.view_def(item.data(Qt.ItemDataRole.UserRole)) if item else None
        menu = QMenu(self)
        if d and d["kind"] in ("folder", "search"):
            menu.addAction("Rename…", lambda: self._rename_view(d))
            if d["kind"] == "folder":
                sub = menu.addAction("Include subfolders")
                sub.setCheckable(True)
                sub.setChecked(bool(d.get("subfolders", True)))
                sub.toggled.connect(lambda on: self._set_folder_subfolders(d, on))
            menu.addAction("Remove from the Folders panel", lambda: self._remove_view(d))
            menu.addSeparator()
        menu.addAction("Add a folder…", self.add_folder_view)
        menu.addAction("Save the current search and Show filter…", self.save_search_view)
        menu.exec(self.views.mapToGlobal(pos))

    def _rename_view(self, d: dict) -> None:
        old = d["label"]
        label = ask_text(self, "Rename", "Name in the Folders panel:", old)
        if not label or not label.strip() or label.strip() == old:
            return
        if d["kind"] == "folder":
            for f in self.settings.extra_folders:
                if f["path"] == d["path"]:
                    f["label"] = label.strip()
        else:
            for q in self.settings.saved_searches:
                if q["label"] == old:
                    q["label"] = label.strip()
            if self.view == d["key"]:
                self.view = "search:" + label.strip()
        self.settings.save()
        self._fill_views()
        self._select_view_item(self.view)

    def _set_folder_subfolders(self, d: dict, on: bool) -> None:
        for f in self.settings.extra_folders:
            if f["path"] == d["path"]:
                f["subfolders"] = on
        self.settings.save()
        self._stale.add(d["key"])
        if self.view == d["key"]:
            self.reload()

    def _remove_view(self, d: dict) -> None:
        if d["kind"] == "folder":
            self.settings.extra_folders = [f for f in self.settings.extra_folders if f["path"] != d["path"]]
        else:
            self.settings.saved_searches = [q for q in self.settings.saved_searches if q["label"] != d["label"]]
        self.settings.save()
        self._states.pop(d["key"], None)
        was_current = self.view == d["key"]
        self._fill_views()
        if was_current:
            self.view = "library"
            self._select_view_item("library")
            self.reload()
        else:
            self._select_view_item(self.view)

    # ---------------------------------------------------------------- remembering where you were
    def _save_state(self) -> None:
        self._states[self.view] = {
            "tracks": list(self.model.tracks),
            "selected": self.selected_paths(),
            "scroll_list": self.table.verticalScrollBar().value(),
            "scroll_albums": self.albums.verticalScrollBar().value(),
            "search": self.search.text(),
            "missing": self.show_filter.currentData(),
        }

    def _restore_position(self, state: dict) -> None:
        if not state:
            return
        rows = [r for r in (self._row_of(p) for p in state.get("selected", [])) if r >= 0]
        if rows:
            self._select_rows(rows)
        bar = self.albums.verticalScrollBar() if self.album_mode() else self.table.verticalScrollBar()
        bar.setValue(state.get("scroll_albums" if self.album_mode() else "scroll_list", 0))

    def _mark_others_stale(self) -> None:
        """Files changed: the other views' remembered songs need reading again when shown."""
        self._stale.update(k for k in self._states if k != self.view)

    def _view_clicked(self, item: QListWidgetItem | None, _old=None) -> None:  # type: ignore[no-untyped-def]
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if key == self.view and self.model.tracks and key not in self._stale:
            return
        if not self._confirm_unsaved():
            return
        if self.model.tracks or self.view in self._states:
            self._save_state()
        self.view = key
        self.settings.last_view = key
        self.reload(use_remembered=True)

    def reload(self, use_remembered: bool = False) -> None:
        state = self._states.get(self.view, {})
        fresh = use_remembered and state.get("tracks") is not None and self.view not in self._stale
        self._stale.discard(self.view)
        d = self.view_def() or {"kind": "review"}
        # The search and Show filter belong to the view: a saved search brings its own.
        if use_remembered or d["kind"] == "search":
            text = state.get("search", d.get("search", "")) if state else d.get("search", "")
            missing = state.get("missing", d.get("missing", "")) if state else d.get("missing", "")
            self.search.setText(text)
            self.show_filter.setCurrentIndex(max(0, self.show_filter.findData(missing)))
        self._apply_mode()
        folder = self.folder()
        self.keep_action.setEnabled(self.can_keep())
        self.view_hint.setText(folder or "No folder chosen yet: open ⚙ Settings.")
        self.view_hint.setToolTip(folder)
        self._scan_gen += 1
        gen = self._scan_gen
        self._pending_position = state if use_remembered else {}
        if fresh:  # back to a view seen before: just as it was, no reading
            self.model.set_tracks(state["tracks"])
            self._rebuild_albums()
            QTimer.singleShot(0, lambda: self._restore_position(state))
            self.status.setText("")
            return
        self.model.set_tracks([])
        self.editor.show_tracks([], None)
        if not folder:
            self.status.setText("")
            return
        if not os.path.isdir(folder):
            self.status.setText(f"Can't open {folder} (is the drive connected?)")
            return
        subfolders = self.subfolders()
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
                if not self._pending_position:
                    QTimer.singleShot(100, self._select_first)
            end = start + len(chunk)
            if end < len(paths):
                self.status.setText(f"Reading tags… {end} of {len(paths)}")
                self._scan_chunk(gen, paths, end)
            else:
                self.status.setText("")
                self.scan_bg.run(self.cache.save)
                self._update_choices()
                self._update_checkup()
                if self._pending_position:
                    pending = self._pending_position
                    self._pending_position = {}
                    QTimer.singleShot(150, lambda: self._restore_position(pending))
                if self._plex_tracks is None:
                    self.refresh_plex_plays()
                else:
                    self._apply_plex()

        self.scan_bg.run(read, done)

    def _sort_default(self) -> None:
        if self.proxy.sortColumn() < 0:
            col = COLUMN_KEYS.index("artist" if self.view != "review" else "name")
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
        if self.selected_tracks():
            return  # you've already picked one
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
        self._update_now()

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
        if self.editor.changed == {"genre": self.editor._auto_genre}:
            self.editor.changed = {}  # only the Genre the app filled in: no need to ask
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
            self._update_checkup()

        self.bg.run(read, done)

    def _update_choices(self) -> None:
        """Genre, Language and Tempo drop-downs: every value used in either folder (from the tag cache)."""
        found: dict[str, dict[str, str]] = {"genre": {}, "language": {}, "tempo": {}, "obsidian_tags": {},
                                            "album_type": {t.casefold(): t for t in tags.ALBUM_TYPES},
                                            "artist": {}, "album_artist": {}, "album": {},
                                            "original_artist": {}, "original_album": {}}
        with self.cache._lock:
            cached = list(self.cache._items.values())
        tracks = [t.to_dict() for t in self.model.tracks] + cached
        # Each artist's usual genre (to fill in Genre for a song by them that has none).
        from collections import Counter

        genres_by_artist: dict[str, Counter] = {}
        for item in tracks:
            artist, genre = str(item.get("artist") or "").strip(), str(item.get("genre") or "").strip()
            if artist and genre:
                genres_by_artist.setdefault(artist.casefold(), Counter())[genre] += 1
        self.editor.set_artist_genres({a: c.most_common(1)[0][0] for a, c in genres_by_artist.items()})
        for item in tracks:
            for key, values in found.items():
                many = key in ("genre", "obsidian_tags")
                parts = str(item.get(key) or "").split(";") if many else [str(item.get(key) or "")]
                for part in parts:
                    part = part.strip()
                    if part:
                        values.setdefault(part.casefold(), part)
        choices = {k: sorted(v.values(), key=str.casefold) for k, v in found.items()}
        # Tempo: slowest to fastest, then any others the library has.
        usual_tempos = ["Very Slow", "Slow", "Moderate", "Fast", "Very Fast"]
        choices["tempo"] = usual_tempos + [v for v in choices["tempo"] if v.casefold() not in
                                           {u.casefold() for u in usual_tempos}]
        # Album types: the usual ones in their usual order, then any others the library has.
        usual = [t for t in tags.ALBUM_TYPES]
        choices["album_type"] = usual + [v for v in choices["album_type"] if v.casefold() not in
                                         {u.casefold() for u in usual}]
        self.editor.set_choices(choices)

    def save_tags(self, paths: list[str], changes: dict, released: tuple | None = None) -> None:
        """Write tags in the background. The playing song keeps playing: its file is only let go of
        (a short pause) if Windows won't let the tags be written while it plays."""
        if not paths or not changes:
            return
        if released is None:
            # Only what's being saved stops being "not saved": other boxes you've typed in keep their changes.
            for key, value in changes.items():
                if self.editor.changed.get(key) == value:
                    del self.editor.changed[key]
        self.status.setText(f"Saving tags of {len(paths)} song{'s' if len(paths) != 1 else ''}…")
        playing = self.player.path

        def work() -> tuple[list[tuple[str, str]], list[str]]:
            failures, in_use = [], []
            for path in paths:
                try:
                    tags.write(path, changes)
                except PermissionError as exc:
                    if released is None and playing and fileops.same_path(path, playing):
                        in_use.append(path)  # try again once the player has let go of it
                    else:
                        failures.append((path, str(exc)))
                except Exception as exc:  # noqa: BLE001
                    failures.append((path, str(exc)))
            return failures, in_use

        def done(result, error) -> None:  # type: ignore[no-untyped-def]
            failures, in_use = result or ([], [])
            if in_use:
                self.save_tags(in_use, changes, released=self._release(in_use) or ())
            if released:
                self._resume(released)
            saved = [p for p in paths if p not in in_use]
            if not saved and not failures:
                return
            self._report("Saved tags", len(saved) - len(failures), failures, error)
            self._mark_others_stale()
            if "rating" in changes:
                self.send_ratings_to_plex([p for p in saved if p not in {f for f, _e in (failures or [])}],
                                          int(changes["rating"]))
            library = self.settings.library_folder
            if library and any(fileops.same_path(os.path.dirname(p), library) or
                               os.path.normcase(p).startswith(os.path.normcase(library) + os.sep) for p in saved):
                self.plex_rescan_soon()
            self._refresh_tracks({p: p for p in saved})

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

    def focus_list(self) -> None:
        self.active().setFocus(Qt.FocusReason.OtherFocusReason)

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
            self.focus_list()
            return
        # No question first: every selected song is looked up, and nothing is saved until the review.
        missing = tracks

        # 1. Look them all up (nothing is saved yet)...
        def look(t: Track) -> Found:
            current = tags.read(t.path, with_details=True).lyrics
            found = online.best_lyrics(t.artist, t.title or os.path.splitext(t.name)[0], t.album, t.length)
            return Found(t, current, found)

        def looked(results, failures) -> None:  # type: ignore[no-untyped-def]
            items = [found for _t, found in results]
            if failures:
                self._report("Looked up lyrics for", len(items), failures, None)
            if not items:
                return
            # 2. ...show what was found next to what each song has now, and save only the ticked ones.
            review = LyricsReview(items, self)
            if review.exec() != QDialog.DialogCode.Accepted:
                self.flash("Nothing was saved.")
                return
            chosen = review.chosen()
            if not chosen:
                return
            state = self._release([i.track.path for i in chosen])

            def save(item: Found) -> None:
                tags.write(item.track.path, {"lyrics": item.text})

            def saved(results, failures) -> None:  # type: ignore[no-untyped-def]
                self._resume(state)
                self._report("Saved lyrics for", len(results), failures, None)
                self._mark_others_stale()
                self._refresh_tracks({i.track.path: i.track.path for i, _r in results})
                self.focus_list()

            self._run_each("Saving lyrics", chosen, save, saved)

        self._run_each("Looking up lyrics", missing, look, looked)

    def set_rating(self, paths: list[str], stars: int) -> None:
        paths = [p for p in paths if tags.can_write(p)]
        if paths:
            self.save_tags(paths, {"rating": stars})

    # ================================================================ keep / delete
    def keep_selected(self) -> None:
        if not self.can_keep() or self._busy:
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
        # First a last look at everything each song has (⚠ for what's empty)...
        details = [tags.read(t.path, with_details=True) for t in tracks]
        planned = {t.path: dest for t, dest, _n in keep.plan(tracks, self.settings) if dest}
        review = KeepReview(details, self, becomes=planned)
        if review.exec() != QDialog.DialogCode.Accepted:
            if review.fix:
                self._select_path(review.fix)
                self.editor.setFocus()
            return
        # ...then the names they'll have, and whether to replace songs already in the Library.
        rename = self.settings.rename_on_keep
        dialog = PlanDialog(
            "Keep", f"These songs will go into your Library ({self.settings.library_folder}):",
            tracks, self.settings,
            lambda pattern, opts: keep.plan(tracks, self.settings, pattern, opts.get("overwrite", False)), "Keep",
            pattern=rename, parent=self,
            options=[("overwrite", "Replace songs with the same name in the Library (the old ones go to the "
                                   "Recycle Bin). Otherwise the new song gets “_1” added.", False)])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if rename:
            self.settings.rename_pattern = dialog.current_pattern()
        overwrite = dialog.chosen().get("overwrite", False)
        rows = [(t, dest) for t, dest, _note in dialog.rows if dest]
        # Only when the song being kept is the one playing does the next one start; any other song
        # that's playing is left alone.
        kept_paths = [t.path for t, _ in rows]
        playing = self.player.is_playing() and any(fileops.same_path(self.player.path, p) for p in kept_paths)
        next_row = self._row_after(kept_paths)
        self._release(kept_paths)
        self._run_each(
            "Keeping", rows,
            lambda item: keep.keep_one(self.ffmpeg, item[0], item[1], self.settings, overwrite),
            lambda results, failures: self._after_keep(results, failures, next_row, playing))

    def _after_keep(self, results: list, failures: list, next_row: str | None, playing: bool) -> None:
        kept = [item[0].path for item, _res in results]
        now = time.time()
        self.settings.kept_log = [t for t in self.settings.kept_log if t > now - 30 * 86400] + [now] * len(kept)
        warnings = [w for _item, (_dest, ws) in results for w in ws]
        for p in kept:
            self.cache.forget(p)
        self.model.remove(kept)
        self._stale.add("library")
        self._mark_others_stale()
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
            self._mark_others_stale()
            self._update_checkup()
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
            self._mark_others_stale()
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
        if self.can_keep():
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
            rating.addAction(stars(n) or "No rating", lambda n=n: self.set_rating(self.selected_paths(), n))
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
            self._stale.update(d["key"] for d in self.view_defs())
            self._states.clear()
            self.reload()
        self.refresh_plex_plays()

    # ================================================================ album view
    def _apply_mode(self) -> None:
        self.stack.setCurrentWidget(self.active())
        self.layout_btn.setText("▦ Albums ▾" if self.album_mode() else "☰ Song list ▾")
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
        current_track = self.album_model.track(self.albums.currentIndex().row())
        current_path = current_track.path if current_track else ""
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
            current = self.album_model.row_of(current_path) if current_path else -1
            self.albums.selectionModel().setCurrentIndex(
                self.album_model.index(current if current >= 0 else rows[0], 1),
                QItemSelectionModel.SelectionFlag.NoUpdate)
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
        self.settings.album_columns = list(self._album_columns_now().values())
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

