"""The "Look up online" window: tags, album art and lyrics for one song.

Nothing is written here: what you pick goes into the Tags panel, marked "Not saved",
and is written when you click Save there.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import online
from ..core.tags import FIELD_LABELS, Details, Track
from .workers import Background

LOOKUP_FIELDS = ["title", "artist", "album_artist", "album", "edition", "year", "genre",
                 "track", "track_total", "disc", "disc_total"]
# Filled-in values of these are only replaced if you tick them yourself.
PERSONAL_FIELDS = {"title", "artist", "genre", "edition"}
THUMB = 160
MAX_COVERS = 8

_net: Background | None = None


def net() -> Background:
    """One shared pool for web requests, so closing the window never waits for a slow site."""
    global _net
    if _net is None:
        _net = Background(6)
    return _net


def _label(key: str) -> str:
    return {"track_total": "Tracks on album", "disc_total": "Discs", "track": "Track #",
            "disc": "Disc #"}.get(key, FIELD_LABELS.get(key, key.title()))


def open_google_lyrics(title: str, artist: str) -> None:
    """A Google search for "<song> by <artist> lyrics" in the web browser."""
    import urllib.parse

    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    query = f'"{title}" by {artist} lyrics' if artist else f'"{title}" lyrics'
    QDesktopServices.openUrl(QUrl("https://www.google.com/search?q=" + urllib.parse.quote_plus(query)))


def _same(a: str, b: str) -> bool:
    """"03" and "3" are the same track number; "Halo" and "halo" the same title."""
    a, b = a.strip(), b.strip()
    if a.isdigit() and b.isdigit():
        return int(a) == int(b)
    return a.casefold() == b.casefold()


class LookupDialog(QDialog):
    def __init__(self, track: Track, details: Details | None, parent: QWidget | None = None,
                 lyrics_only: bool = False):
        super().__init__(parent)
        self.lyrics_only = lyrics_only
        self.track, self.details = track, details
        self.setWindowTitle(f"Look up online — {track.name}")
        self.resize(1080, 680)
        self._alive = True
        self._gen = 0
        self.candidates: list[online.Candidate] = []
        self.cover_data: dict[str, bytes] = {}
        self.lyrics_found: list[online.Lyrics] = []
        self.chosen_fields: dict[str, str] = {}
        self.chosen_cover: bytes | None = None
        self.chosen_lyrics: str | None = None
        self.finished.connect(lambda _r: setattr(self, "_alive", False))

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        artist, title = track.artist, track.title
        if not title:
            artist, title = online.guess_from_filename(track.name)
            artist = track.artist or artist
        self.artist = QLineEdit(artist)
        self.title = QLineEdit(title)
        self.search_btn = QPushButton("Search")
        self.search_btn.setDefault(True)
        row.addWidget(QLabel("Artist"))
        row.addWidget(self.artist, 1)
        row.addWidget(QLabel("Title"))
        row.addWidget(self.title, 2)
        row.addWidget(self.search_btn)
        layout.addLayout(row)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._build_tags_tab()
        self._build_art_tab()
        self._build_lyrics_tab()
        if lyrics_only:  # just the lyrics: nothing to untick
            self.tabs.removeTab(1)
            self.tabs.removeTab(0)
            self.setWindowTitle(f"Find lyrics — {track.name}")
            self.resize(760, 640)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: palette(placeholder-text);")
        bottom.addWidget(self.status, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Save these lyrics" if lyrics_only else "Use what's ticked")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        bottom.addWidget(self.buttons)
        layout.addLayout(bottom)

        self.search_btn.clicked.connect(self.search)
        self.artist.returnPressed.connect(self.search)
        self.title.returnPressed.connect(self.search)
        if title:
            self.search()

    # ================================================================ tabs
    def _build_tags_tab(self) -> None:
        page = QWidget()
        v = QVBoxLayout(page)
        split = QSplitter()
        self.results = QListWidget()
        self.results.setMinimumWidth(380)
        self.results.setSpacing(4)
        self.results.setWordWrap(True)
        self.results.currentRowChanged.connect(self._show_candidate)
        split.addWidget(self.results)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.compare = QTableWidget(len(LOOKUP_FIELDS), 4)
        self.compare.setHorizontalHeaderLabels(["Use", "Tag", "Now", "Found"])
        self.compare.verticalHeader().setVisible(False)
        self.compare.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        header = self.compare.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for r, key in enumerate(LOOKUP_FIELDS):
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            use.setCheckState(Qt.CheckState.Unchecked)
            self.compare.setItem(r, 0, use)
            name = QTableWidgetItem(_label(key))
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.compare.setItem(r, 1, name)
            now = QTableWidgetItem(str(getattr(self.track, key, "") or ""))
            now.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.compare.setItem(r, 2, now)
            found = QTableWidgetItem("")
            found.setToolTip("Double-click to change it before using it")
            self.compare.setItem(r, 3, found)
        self.compare.itemChanged.connect(self._found_edited)
        rv.addWidget(self.compare, 1)
        ticks = QHBoxLayout()
        all_btn, none_btn = QPushButton("Tick all"), QPushButton("Tick none")
        all_btn.clicked.connect(lambda: self._tick_all(True))
        none_btn.clicked.connect(lambda: self._tick_all(False))
        self.source_link = QLabel("")
        self.source_link.setOpenExternalLinks(True)
        ticks.addWidget(self.source_link, 1)
        ticks.addWidget(all_btn)
        ticks.addWidget(none_btn)
        rv.addLayout(ticks)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        v.addWidget(split)
        self.tabs.addTab(page, "Tags")

    def _build_art_tab(self) -> None:
        page = QWidget()
        v = QVBoxLayout(page)
        self.art = QListWidget()
        self.art.setViewMode(QListWidget.ViewMode.IconMode)
        self.art.setIconSize(QSize(THUMB, THUMB))
        self.art.setGridSize(QSize(THUMB + 40, THUMB + 64))
        self.art.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.art.setMovement(QListWidget.Movement.Static)
        self.art.setWordWrap(True)
        self.art.currentItemChanged.connect(lambda *_: self._art_selected())
        v.addWidget(self.art, 1)
        self.use_art = QCheckBox("Use the selected cover")
        v.addWidget(self.use_art)
        if self.details and self.details.cover:
            self._add_cover_item("current", self.details.cover, "Current")
        self.tabs.addTab(page, "Album art")

    def _build_lyrics_tab(self) -> None:
        page = QWidget()
        v = QVBoxLayout(page)
        split = QSplitter()
        self.lyric_list = QListWidget()
        self.lyric_list.setMaximumWidth(220)
        self.lyric_list.currentRowChanged.connect(self._lyrics_selected)
        split.addWidget(self.lyric_list)
        self.lyric_text = QPlainTextEdit()
        self.lyric_text.setPlaceholderText("Lyrics found online show here. You can edit them before using them, or "
                                           "paste lyrics from a web page.")
        self.lyric_text.textChanged.connect(self._lyrics_typed)
        split.addWidget(self.lyric_text)
        split.setStretchFactor(1, 1)
        v.addWidget(split, 1)
        bottom = QHBoxLayout()
        self.use_lyrics = QCheckBox("Use these lyrics")
        bottom.addWidget(self.use_lyrics, 1)
        google = QPushButton("Search Google for the lyrics")
        google.setToolTip("Opens a Google search in your browser. Copy the lyrics from there and paste them into "
                          "the box above, then tick “Use these lyrics”.")
        google.clicked.connect(self._google_lyrics)
        bottom.addWidget(google)
        v.addLayout(bottom)
        if self.details and self.details.lyrics.strip():
            self.lyric_list.addItem("Current")
        self.tabs.addTab(page, "Lyrics")

    # ================================================================ searching
    def search(self) -> None:
        artist, title = self.artist.text().strip(), self.title.text().strip()
        if not title:
            self.status.setText("Type a title to search for.")
            return
        self._gen += 1
        gen = self._gen
        self.results.clear()
        self.candidates = []
        self._clear_found()
        for i in range(self.art.count() - 1, -1, -1):
            if self.art.item(i).data(Qt.ItemDataRole.UserRole) != "current":
                self.art.takeItem(i)
        has_current = bool(self.details and self.details.lyrics.strip())
        while self.lyric_list.count() > (1 if has_current else 0):
            self.lyric_list.takeItem(self.lyric_list.count() - 1)
        self.lyrics_found = []
        self.status.setText("Looking for lyrics…" if self.lyrics_only else
                            "Searching iTunes, MusicBrainz and Deezer, and looking for lyrics…")
        self._pending = 1 if self.lyrics_only else 2
        self._problems: list[str] = []
        length = self.track.length
        album = self.track.album
        if not self.lyrics_only:
            net().run(lambda: online.search(artist, title, length), lambda res, err: self._got_tags(gen, res, err))
        net().run(lambda: online.find_lyrics(artist, title, album, length),
                  lambda res, err: self._got_lyrics(gen, res, err))

    def _ok(self, gen: int) -> bool:
        return self._alive and gen == self._gen

    def _done_one(self, problems: list[str]) -> None:
        self._problems += problems
        self._pending -= 1
        if self._pending <= 0:
            found = (f"Found on {len(self.lyrics_found)} sites" if self.lyrics_only else
                     f"{len(self.candidates)} matches, {len(self.lyrics_found)} lyrics")
            if self.lyrics_only and not self.lyrics_found:
                found = "No lyrics found. Check the artist and title above and search again"
            if self._problems:
                found += ".  Not reachable: " + "; ".join(self._problems)
            self.status.setText(found)

    def _got_tags(self, gen: int, result, error: str | None) -> None:  # type: ignore[no-untyped-def]
        if not self._ok(gen):
            return
        candidates, problems = result or ([], [error] if error else [])
        self.candidates = candidates
        for c in candidates:
            bits = [c.album + (f" ({c.edition})" if c.edition else ""), c.year]
            if c.track:
                bits.append(f"track {c.track}" + (f"/{c.track_total}" if c.track_total else ""))
            if c.disc and (c.disc != "1" or c.disc_total not in ("", "1")):
                bits.append(f"disc {c.disc}" + (f"/{c.disc_total}" if c.disc_total else ""))
            if c.genre:
                bits.append(c.genre)
            text = f"{c.title} — {c.artist}\n{' · '.join(b for b in bits if b)}\n{c.source}"
            if c.detail:
                text += f" · {c.detail}"
            item = QListWidgetItem(text)
            item.setToolTip(c.url)
            self.results.addItem(item)
        if candidates:
            self.results.setCurrentRow(0)
        self._load_covers(gen, online.covers_for(candidates, MAX_COVERS))
        self._done_one(problems)

    def _got_lyrics(self, gen: int, result, error: str | None) -> None:  # type: ignore[no-untyped-def]
        if not self._ok(gen):
            return
        found, problems = result or ([], [error] if error else [])
        self.lyrics_found = found
        for lyr in found:
            lines = len(lyr.text.splitlines())
            item = QListWidgetItem(f"{lyr.source}\n{lines} lines")
            item.setToolTip(lyr.url)
            self.lyric_list.addItem(item)
        has_current = bool(self.details and self.details.lyrics.strip())
        if found:
            self.lyric_list.setCurrentRow(1 if has_current else 0)
            if not has_current:
                self.use_lyrics.setChecked(True)
        elif has_current:
            self.lyric_list.setCurrentRow(0)
        self._done_one(problems)

    # ================================================================ tags
    def _clear_found(self) -> None:
        self.compare.blockSignals(True)
        for r in range(len(LOOKUP_FIELDS)):
            self.compare.item(r, 3).setText("")
            self.compare.item(r, 0).setCheckState(Qt.CheckState.Unchecked)
        self.compare.blockSignals(False)
        self.source_link.setText("")

    def _show_candidate(self, row: int) -> None:
        if row < 0 or row >= len(self.candidates):
            return
        c = self.candidates[row]
        values = c.fields()
        self.compare.blockSignals(True)
        for r, key in enumerate(LOOKUP_FIELDS):
            found = values.get(key, "")
            now = str(getattr(self.track, key, "") or "")
            self.compare.item(r, 3).setText(found)
            differs = bool(found) and not _same(found, now)
            tick = bool(found) and ((not now) or (differs and key not in PERSONAL_FIELDS))
            self.compare.item(r, 0).setCheckState(Qt.CheckState.Checked if tick else Qt.CheckState.Unchecked)
            self.compare.item(r, 3).setForeground(self.palette().text() if differs or not now
                                                  else self.palette().placeholderText())
        self.compare.blockSignals(False)
        if c.url:
            self.source_link.setText(f'<a href="{c.url}">Open on {c.source}</a>')

    def _found_edited(self, item: QTableWidgetItem) -> None:
        if item.column() == 3:
            self.compare.item(item.row(), 0).setCheckState(
                Qt.CheckState.Checked if item.text().strip() else Qt.CheckState.Unchecked)

    def _tick_all(self, on: bool) -> None:
        for r in range(len(LOOKUP_FIELDS)):
            if self.compare.item(r, 3).text() or not on:
                self.compare.item(r, 0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    # ================================================================ art
    def _load_covers(self, gen: int, covers: list[online.Cover]) -> None:
        for cover in covers:
            def done(data, _err, cover=cover) -> None:  # type: ignore[no-untyped-def]
                if self._ok(gen) and data:
                    self._add_cover_item(cover.url, data, f"{cover.source}\n{cover.note}")

            net().run(lambda url=cover.url: online.get_image(url), done)

    def _add_cover_item(self, key: str, data: bytes, label: str) -> None:
        image = QImage.fromData(QByteArray(data))
        if image.isNull():
            return
        self.cover_data[key] = data
        size = f"{image.width()} × {image.height()}, {len(data) // 1024} KB"
        thumb = QPixmap.fromImage(image.scaled(THUMB, THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
        item = QListWidgetItem(QIcon(thumb), f"{label.splitlines()[0]}\n{size}")
        item.setToolTip(label.replace("\n", " — ") + f"\n{size}")
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setData(Qt.ItemDataRole.UserRole + 1, image.width() * image.height())
        if key == "current":
            self.art.insertItem(0, item)
            return
        # Keep the found covers biggest first (after the current one).
        pixels = image.width() * image.height()
        pos = self.art.count()
        for i in range(self.art.count()):
            other = self.art.item(i)
            if other.data(Qt.ItemDataRole.UserRole) != "current" and \
                    (other.data(Qt.ItemDataRole.UserRole + 1) or 0) < pixels:
                pos = i
                break
        self.art.insertItem(pos, item)

    def _art_selected(self) -> None:
        item = self.art.currentItem()
        self.use_art.setChecked(bool(item) and item.data(Qt.ItemDataRole.UserRole) != "current")

    # ================================================================ lyrics
    def _lyrics_typed(self) -> None:
        # Typing or pasting in the box means you want what's there.
        if self.lyric_text.hasFocus() and self.lyric_text.toPlainText().strip():
            self.use_lyrics.setChecked(True)

    def _lyrics_selected(self, row: int) -> None:
        if row < 0:
            return
        has_current = bool(self.details and self.details.lyrics.strip())
        if has_current and row == 0:
            self.lyric_text.setPlainText(self.details.lyrics)  # type: ignore[union-attr]
            self.use_lyrics.setChecked(False)
            return
        index = row - (1 if has_current else 0)
        if 0 <= index < len(self.lyrics_found):
            self.lyric_text.setPlainText(self.lyrics_found[index].text)
            self.use_lyrics.setChecked(True)

    def _google_lyrics(self) -> None:
        open_google_lyrics(self.title.text().strip(), self.artist.text().strip())
        # Ready for pasting: an empty box (unless it already has lyrics) and "use" ticked once you paste.
        self.lyric_list.clearSelection()
        self.lyric_text.setFocus()

    # ================================================================ done
    def _accept(self) -> None:
        self.chosen_fields = {}
        for r, key in enumerate(LOOKUP_FIELDS):
            if self.compare.item(r, 0).checkState() == Qt.CheckState.Checked:
                self.chosen_fields[key] = self.compare.item(r, 3).text().strip()
        self.chosen_cover = None
        item = self.art.currentItem()
        if self.use_art.isChecked() and item and item.data(Qt.ItemDataRole.UserRole) != "current":
            self.chosen_cover = self.cover_data.get(item.data(Qt.ItemDataRole.UserRole))
        self.chosen_lyrics = self.lyric_text.toPlainText().strip() if self.use_lyrics.isChecked() else None
        self.accept()
