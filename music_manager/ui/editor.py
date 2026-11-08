"""The tag editor in the sidebar: cover art, the main tags, rating and lyrics.

With several songs selected, a field that differs between them shows "(mixed)" and is
only written if you change it. Nothing is written until you click Save (Ctrl+S).
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import tags
from ..core.tags import Details, Track

MIXED = "(mixed)"
COVER_SIZE = 150


class RatingStars(QWidget):
    changed = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.value = 0
        self.mixed = False
        self.buttons: list[QToolButton] = []
        for n in range(1, 6):
            b = QToolButton(text="☆")
            b.setAutoRaise(True)
            b.setToolTip(f"{n} star{'s' if n > 1 else ''} (Ctrl+{n}). Click again to clear.")
            b.setStyleSheet("QToolButton { font-size: 16px; color: #f2b01e; padding: 0px; }")
            b.setFixedWidth(24)
            b.clicked.connect(lambda _=False, n=n: self._click(n))
            layout.addWidget(b)
            self.buttons.append(b)
        self.note = QLabel("")
        layout.addWidget(self.note)
        layout.addStretch(1)

    def _click(self, n: int) -> None:
        self.set_value(0 if (n == self.value and not self.mixed) else n)
        self.changed.emit(self.value)

    def set_value(self, value: int, mixed: bool = False) -> None:
        self.value, self.mixed = value, mixed
        for i, b in enumerate(self.buttons, start=1):
            b.setText("★" if (not mixed and i <= value) else "☆")
        self.note.setText(MIXED if mixed else "")


class ComboField(QComboBox):
    """A text box with a drop-down of the values the library already uses (Genre, Language,
    Tempo). Anything can still be typed. Looks like a QLineEdit to the editor."""

    textEdited = Signal(str)
    returnPressed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMaxVisibleItems(20)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.lineEdit().textEdited.connect(self.textEdited)
        self.lineEdit().returnPressed.connect(self.returnPressed)
        self.activated.connect(lambda _i: self.textEdited.emit(self.currentText()))
        completer = self.completer()
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        # Scrolling over the Tags panel scrolls the panel, not the value (unless you're in this box).
        if self.hasFocus() and self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()

    def setText(self, text: str) -> None:  # noqa: N802 - QLineEdit's name
        self.setEditText(text)

    def text(self) -> str:
        return self.currentText()

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802
        self.lineEdit().setPlaceholderText(text)

    def set_choices(self, values: list[str]) -> None:
        current = self.currentText()
        self.blockSignals(True)
        self.clear()
        self.addItems(values)
        self.setEditText(current)
        self.blockSignals(False)


class TagsField(ComboField):
    """Obsidian tags: picking one from the list adds it to those already there."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.activated.disconnect()
        self.activated.connect(self._add)
        self._before = ""

    def showPopup(self) -> None:  # noqa: N802
        self._before = self.lineEdit().text()
        super().showPopup()

    def _add(self, index: int) -> None:
        picked = self.itemText(index)
        current = tags.split_tags(self._before)
        if picked.casefold() not in {c.casefold() for c in current}:
            current.append(picked)
        text = " ".join(f"#{c}" for c in current)
        self.setEditText(text)
        self._before = ""
        self.textEdited.emit(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self.setEditText(" ".join(f"#{t}" for t in tags.split_tags(text)))


CHOICE_FIELDS = ("genre", "language", "tempo", "obsidian_tags", "album_type")
COVER_MIME = "application/x-music-manager-cover"


class TagEditor(QWidget):
    save_requested = Signal(list, dict)  # paths, changes
    rating_requested = Signal(list, int)  # paths, stars (saved straight away)
    lookup_requested = Signal()
    repair_requested = Signal()
    lyrics_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.tracks: list[Track] = []
        self.changed: dict = {}
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        self.heading = QLabel("No song selected")
        self.heading.setWordWrap(True)
        self.heading.setStyleSheet("font-weight: 600;")
        outer.addWidget(self.heading)
        self.problem = QLabel("")
        self.problem.setWordWrap(True)
        self.problem.setStyleSheet("color: #e0605a;")
        self.repair_btn = QPushButton("🛠  Repair file…")
        self.repair_btn.clicked.connect(self.repair_requested)
        outer.addWidget(self.problem)
        outer.addWidget(self.repair_btn)

        self.cover = QLabel()
        self.cover.setFixedSize(COVER_SIZE, COVER_SIZE)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setStyleSheet("QLabel { border: 1px solid palette(mid); }")
        # Option A: the cover with small icon buttons beside it.
        cover_row = QHBoxLayout()
        cover_row.addStretch(1)
        cover_row.addWidget(self.cover)
        art_buttons = QVBoxLayout()
        art_buttons.setSpacing(2)
        cover_row.addLayout(art_buttons)
        cover_row.addStretch(1)
        outer.addLayout(cover_row)
        self.cover_info = QLabel("")
        self.cover_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_info.setStyleSheet("color: palette(placeholder-text);")
        outer.addWidget(self.cover_info)
        self.cover_info.hide()
        def art_button(icon: str, tip: str) -> QToolButton:
            b = QToolButton(text=icon)
            b.setToolTip(tip)
            b.setFixedSize(30, 28)
            art_buttons.addWidget(b)
            return b

        self.art_file = art_button("📂", "Album art from a file…")
        self.art_copy = art_button("⧉", "Copy this album art, to Paste it into another song (kept exactly)")
        self.art_paste = art_button("📋", "Paste a picture: one copied here, or with Copy image in a web browser")
        self.art_remove = art_button("✕", "Remove the album art")
        self.art_save = art_button("💾", "Save the album art as a picture file…")
        art_buttons.addStretch(1)

        # Two tabs, so the panel stays short: the tags every song needs, and the extra ones.
        self.tag_tabs = QTabWidget()
        standard, extra = QWidget(), QWidget()
        form = QFormLayout(standard)
        more = QFormLayout(extra)
        for f in (form, more):
            f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            f.setContentsMargins(6, 6, 6, 6)
        self.edits: dict[str, QLineEdit] = {}

        def field(key: str):  # type: ignore[no-untyped-def]
            edit = TagsField() if key == "obsidian_tags" else ComboField() if key in CHOICE_FIELDS else QLineEdit()
            self.edits[key] = edit
            return edit

        for key in ("title", "artist", "album_artist", "album", "year", "genre", "language", "tempo"):
            form.addRow(tags.FIELD_LABELS[key], field(key))
        for key in ("album_type", "edition", "original_artist", "original_album", "original_year", "obsidian_tags"):
            more.addRow(tags.FIELD_LABELS[key], field(key))
        self.edits["album_type"].setToolTip("e.g. album, single, ep, compilation, live, or album;live. Saved as "
                                            "both MusicBrainz Album Type and RELEASETYPE, kept the same.")
        self.edits["genre"].setToolTip("Several genres: separate them with ;")
        self.edits["tempo"].setToolTip("MusicBee's Tempo field (e.g. Slow, Medium, Fast)")
        self.edits["original_artist"].setToolTip("Who first recorded it (for a cover). Several: separate with ;")
        self.edits["original_year"].setToolTip("The year it first came out")
        self.edits["obsidian_tags"].setToolTip(
            "Tags for the song's note in your Obsidian vault, e.g. #feminism #holiday. Pick from the list to add "
            "one. Saved as the file's “Obsidian Tags” tag, which Media Manager can read into the note's tags.")
        self.edits["obsidian_tags"].setPlaceholderText("#feminism #holiday")
        self.edits["edition"].setToolTip("e.g. Deluxe Edition, iTunes Version (saved like MusicBee: TXXX:TSST). "
                                         "Plex and Symfonium don't show it.")
        nums = QGridLayout()
        for _col, key in enumerate(("track", "track_total", "disc", "disc_total")):
            edit = QLineEdit()
            edit.setMaximumWidth(48)
            self.edits[key] = edit
        nums.addWidget(self.edits["track"], 0, 0)
        nums.addWidget(QLabel("of"), 0, 1)
        nums.addWidget(self.edits["track_total"], 0, 2)
        nums.setColumnStretch(3, 1)
        form.addRow("Track #", nums)
        discs = QGridLayout()
        discs.addWidget(self.edits["disc"], 0, 0)
        discs.addWidget(QLabel("of"), 0, 1)
        discs.addWidget(self.edits["disc_total"], 0, 2)
        discs.setColumnStretch(3, 1)
        form.addRow("Disc #", discs)
        self.rating = RatingStars()
        form.addRow("Rating", self.rating)
        self.tag_tabs.addTab(standard, "Standard")
        self.tag_tabs.addTab(extra, "Extra")
        other = QWidget()
        other_box = QVBoxLayout(other)
        other_box.setContentsMargins(6, 6, 6, 6)
        self.extra_title = QLabel("Other tags in this file (✕ deletes one when you Save)")
        self.extra_title.setWordWrap(True)
        other_box.addWidget(self.extra_title)
        self.extra = QTableWidget(0, 3)
        self.extra.setHorizontalHeaderLabels(["Tag", "Value", ""])
        self.extra.verticalHeader().setVisible(False)
        self.extra.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed)
        self.extra.itemChanged.connect(self._frame_edited)
        self.extra.setWordWrap(False)
        self.extra.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.extra.horizontalHeader().resizeSection(0, 110)
        self.extra.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.extra.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.extra.horizontalHeader().resizeSection(2, 30)
        self.extra.setMinimumHeight(220)
        other_box.addWidget(self.extra)
        self.tag_tabs.addTab(other, "Other")
        outer.addWidget(self.tag_tabs)
        lookups = QHBoxLayout()
        self.lookup_btn = QPushButton("🔎 Look up online")
        self.lookup_btn.setToolTip("Tags, album art and lyrics from iTunes, MusicBrainz, Deezer and more (Ctrl+L)")
        self.lookup_btn.clicked.connect(self.lookup_requested)
        lookups.addWidget(self.lookup_btn)
        self.find_lyrics_btn = QPushButton("🔎 Find lyrics")
        self.find_lyrics_btn.setToolTip("Look up just the lyrics (Ctrl+Shift+L). With several songs selected, "
                                        "those without lyrics get them")
        self.find_lyrics_btn.clicked.connect(self.lyrics_requested)
        lookups.addWidget(self.find_lyrics_btn)
        lookups.addStretch(1)
        outer.addLayout(lookups)
        lyrics_label = QLabel("Lyrics")
        lyrics_label.setToolTip("Saved in the file and as a .txt next to it")
        outer.addWidget(lyrics_label)
        self.lyrics = QPlainTextEdit()
        self.lyrics.setPlaceholderText("Paste lyrics here")
        self.lyrics.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        outer.addWidget(self.lyrics, 1)

        buttons = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("color: palette(placeholder-text);")
        self.revert_btn = QPushButton("Undo changes")
        self.save_btn = QPushButton("Save  (Ctrl+S)")
        self.save_btn.setDefault(True)
        buttons.addWidget(self.status, 1)
        buttons.addWidget(self.revert_btn)
        buttons.addWidget(self.save_btn)
        outer.addLayout(buttons)

        for key, edit in self.edits.items():
            edit.textEdited.connect(lambda text, key=key: self._edited(key, text))
            edit.returnPressed.connect(self.save)
        self.lyrics.textChanged.connect(self._lyrics_edited)
        self.rating.changed.connect(lambda v: self.rating_requested.emit([t.path for t in self.tracks], v))
        self.save_btn.clicked.connect(self.save)
        self.revert_btn.clicked.connect(lambda: self.show_tracks(self.tracks, self._details))
        self.art_file.clicked.connect(self._art_from_file)
        self.art_paste.clicked.connect(self._art_paste)
        self.art_copy.clicked.connect(self._art_copy)
        self.art_remove.clicked.connect(self._art_remove)
        self.art_save.clicked.connect(self._art_save)
        self._details: Details | None = None
        self.show_tracks([], None)

    # ------------------------------------------------------------ showing
    def show_tracks(self, tracks: list[Track], details: Details | None) -> None:
        """details: of the first track (its cover and lyrics), or None while loading."""
        self._loading = True
        self.tracks, self._details, self.changed = list(tracks), details, {}
        many = len(tracks) > 1
        writable = bool(tracks) and all(tags.can_write(t.path) for t in tracks)
        if not tracks:
            self.heading.setText("No song selected")
        elif many:
            self.heading.setText(f"{len(tracks)} songs selected")
        else:
            self.heading.setText(tracks[0].name)
        for key, edit in self.edits.items():
            values = {getattr(t, key) for t in tracks}
            if len(values) == 1:
                edit.setText(values.pop())
                edit.setPlaceholderText("")
            else:
                edit.setText("")
                edit.setPlaceholderText(MIXED if tracks else "")
            edit.setEnabled(writable)
        ratings = {t.rating for t in tracks}
        self.rating.set_value(next(iter(ratings)) if len(ratings) == 1 else 0, mixed=len(ratings) > 1)
        self.rating.setEnabled(writable)
        self.lyrics.setEnabled(writable and not many)
        if many:
            self.lyrics.setPlainText("")
            with_lyrics = sum(t.has_lyrics for t in tracks)
            self.lyrics.setPlaceholderText(f"Select one song to see or edit its lyrics ({with_lyrics} of "
                                           f"{len(tracks)} have lyrics)")
        else:
            self.lyrics.setPlaceholderText("Paste lyrics here")
            self.lyrics.setPlainText(details.lyrics if details else "")
        self._show_cover(details.cover if details else None, tracks)
        self._show_frames(details.frames if (details and not many) else [], many)
        self.lookup_btn.setEnabled(len(tracks) == 1)
        for b in (self.art_file, self.art_paste, self.art_remove):
            b.setEnabled(writable)
        self.art_save.setEnabled(bool(details and details.cover))
        self.art_copy.setEnabled(bool(details and details.cover))
        self.save_btn.setEnabled(False)
        self.revert_btn.setEnabled(False)
        broken = [t for t in tracks if t.error]
        if len(tracks) == 1 and broken:
            self.problem.setText(broken[0].error)
        elif broken:
            self.problem.setText(f"{len(broken)} of these songs can't be read properly. Repair them first.")
        else:
            self.problem.setText("")
        self.problem.setVisible(bool(broken))
        self.repair_btn.setVisible(bool(broken))
        if broken:
            self.status.setText("Repair the file before editing its tags")
            for edit in self.edits.values():
                edit.setEnabled(False)
            self.rating.setEnabled(False)
            self.lyrics.setEnabled(False)
            self.lookup_btn.setEnabled(False)
            for b in (self.art_file, self.art_paste, self.art_remove):
                b.setEnabled(False)
        elif tracks and not writable:
            self.status.setText("Convert to MP3 to edit tags")
        else:
            self.status.setText("")
        self._loading = False

    def _show_cover(self, data: bytes | None, tracks: list[Track], new: bool = False) -> None:
        image = QImage.fromData(QByteArray(data)) if data else QImage()
        if image.isNull():
            self.cover.setPixmap(QPixmap())
            covers = sum(t.has_cover for t in tracks)
            if not tracks:
                self.cover.setText("")
            elif len(tracks) > 1 and 0 < covers < len(tracks):
                self.cover.setText(f"{covers} of {len(tracks)} have art")
            else:
                self.cover.setText("No album art" if not data else "Can't show this picture")
            self._cover_note("")
            return
        pix = QPixmap.fromImage(image).scaled(COVER_SIZE, COVER_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation)
        self.cover.setPixmap(pix)
        # No size or format (not needed); only what helps: unsaved, or how many of several songs have art.
        info = "Not saved yet" if new else ""
        if len(tracks) > 1 and not new:
            info = f"{sum(t.has_cover for t in tracks)} of {len(tracks)} songs have art"
        self._cover_note(info)

    def _cover_note(self, text: str) -> None:
        """The line under the cover takes no room when it has nothing to say."""
        self.cover_info.setText(text)
        self.cover_info.setVisible(bool(text))

    # ------------------------------------------------------------ editing
    def _mark(self) -> None:
        dirty = bool(self.changed)
        self.save_btn.setEnabled(dirty)
        self.revert_btn.setEnabled(dirty)
        self.status.setText("Not saved" if dirty else "")

    def _edited(self, key: str, text: str) -> None:
        if not self._loading:
            self.changed[key] = text
            self._mark()

    def _lyrics_edited(self) -> None:
        if not self._loading and len(self.tracks) == 1:
            self.changed["lyrics"] = self.lyrics.toPlainText()
            self._mark()

    def set_cover(self, data: bytes) -> None:
        if not self.tracks or not data:
            return
        self.changed["cover"] = (data, tags.picture_mime(data))
        self._show_cover(data, self.tracks, new=True)
        self.art_save.setEnabled(True)
        self.art_copy.setEnabled(True)
        self._mark()

    def _art_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Album art", "", "Pictures (*.jpg *.jpeg *.png *.webp)")
        if path:
            with open(path, "rb") as f:
                self.set_cover(f.read())

    def _art_copy(self) -> None:
        data = self.changed["cover"][0] if self.changed.get("cover") else None
        if data is None and self._details:
            data = self._details.cover
        if not data:
            return
        from PySide6.QtCore import QMimeData

        mime = QMimeData()
        mime.setData(COVER_MIME, QByteArray(data))  # the exact picture, for Paste here
        image = QImage.fromData(QByteArray(data))
        if not image.isNull():
            mime.setImageData(image)  # and a plain picture for other apps
        QGuiApplication.clipboard().setMimeData(mime)
        self.status.setText("Album art copied: select another song and click Paste")

    def _art_paste(self) -> None:
        clip = QGuiApplication.clipboard()
        mime = clip.mimeData()
        if mime is not None and mime.hasFormat(COVER_MIME):
            self.set_cover(bytes(mime.data(COVER_MIME)))
            return
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile() and url.toLocalFile().lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    with open(url.toLocalFile(), "rb") as f:
                        self.set_cover(f.read())
                    return
        image = clip.image()
        if image.isNull():
            self.status.setText("No picture on the clipboard")
            return
        from PySide6.QtCore import QBuffer, QIODevice

        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.convertToFormat(QImage.Format.Format_RGB32).save(buf, "JPG", 92)
        self.set_cover(bytes(buf.data()))

    def _art_remove(self) -> None:
        if not self.tracks:
            return
        self.changed["cover"] = None
        self.cover.setPixmap(QPixmap())
        self.cover.setText("Art will be removed")
        self._cover_note("")
        self._mark()

    def _art_save(self) -> None:
        data = None
        if self.changed.get("cover"):
            data = self.changed["cover"][0]
        elif self._details and self._details.cover:
            data = self._details.cover
        if not data:
            return
        ext = ".png" if tags.picture_mime(data) == "image/png" else ".jpg"
        base = (self.tracks[0].album or self.tracks[0].title or "cover") if self.tracks else "cover"
        path, _ = QFileDialog.getSaveFileName(self, "Save album art", base + ext, f"Picture (*{ext})")
        if path:
            with open(path, "wb") as f:
                f.write(data)

    def apply_field(self, key: str, value: str) -> None:
        """Set a field as if typed (used by online lookups)."""
        if key in self.edits:
            self.edits[key].setText(value)
            self._edited(key, value)

    def _show_frames(self, frames: list[tuple[str, str, str]], many: bool) -> None:
        self._loading_frames = True
        self.extra.setRowCount(len(frames))
        self._frame_keys = [key for key, _n, _v in frames]
        for r, (key, name, value) in enumerate(frames):
            name_item = QTableWidgetItem(name)
            name_item.setToolTip(key)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            value_item = QTableWidgetItem(value)
            editable = tags.frame_is_text(key)
            hint = "Double-click to change it" if editable else "(can't be edited here)"
            value_item.setToolTip(f"{value}\n\n{hint}")
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            value_item.setFlags(flags | Qt.ItemFlag.ItemIsEditable if editable else flags)
            if not editable:
                value_item.setForeground(self.palette().placeholderText())
            self.extra.setItem(r, 0, name_item)
            self.extra.setItem(r, 1, value_item)
            button = QToolButton(text="✕")
            button.setAutoRaise(True)
            button.setToolTip(f"Delete the {name} tag from this file (when you Save)")
            button.clicked.connect(lambda _=False, key=key, r=r: self._remove_frame(key, r))
            self.extra.setCellWidget(r, 2, button)
        self.extra.resizeRowsToContents()
        self.extra_title.setText("Double-click a value to change it; ✕ deletes the tag. Both happen when you Save."
                                 if frames else ("Select one song to see its other tags" if many
                                                 else "No other tags in this file"))
        self.extra.setVisible(bool(frames))
        self.tag_tabs.setTabText(2, f"Other ({len(frames)})" if frames else "Other")
        self._loading_frames = False

    def _frame_edited(self, item: QTableWidgetItem) -> None:
        if getattr(self, "_loading_frames", False) or item.column() != 1:
            return
        key = self._frame_keys[item.row()]
        self.changed.setdefault("set_frames", {})[key] = item.text()
        font = item.font()
        font.setItalic(True)  # changed, not saved yet
        self._loading_frames = True
        item.setFont(font)
        self._loading_frames = False
        self._mark()

    def _remove_frame(self, key: str, row: int) -> None:
        removing = self.changed.setdefault("remove", [])
        font = self.extra.item(row, 0).font()
        if key in removing:  # clicked again: keep it after all
            removing.remove(key)
            if not removing:
                del self.changed["remove"]
            font.setStrikeOut(False)
        else:
            removing.append(key)
            font.setStrikeOut(True)
        for col in (0, 1):
            self.extra.item(row, col).setFont(font)
        self._mark()

    def set_choices(self, choices: dict[str, list[str]]) -> None:
        """The values offered in the Genre, Language and Tempo drop-downs."""
        for key, values in choices.items():
            edit = self.edits.get(key)
            if isinstance(edit, ComboField):
                edit.set_choices(values)

    def apply_lookup(self, fields: dict[str, str], cover: bytes | None, lyrics: str | None) -> None:
        """Put what was chosen in the lookup window into the editor, ready to Save."""
        for key, value in fields.items():
            self.apply_field(key, value)
        if cover:
            self.set_cover(cover)
        if lyrics is not None and len(self.tracks) == 1:
            self.lyrics.setPlainText(lyrics)  # textChanged marks it as changed
        self._mark()

    def save(self) -> None:
        if self.tracks and self.changed:
            self.save_requested.emit([t.path for t in self.tracks], dict(self.changed))

    def has_unsaved(self) -> bool:
        return bool(self.changed)
