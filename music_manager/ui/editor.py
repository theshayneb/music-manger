"""The tag editor in the sidebar: cover art, the main tags, rating and lyrics.

With several songs selected, a field that differs between them shows "(mixed)" and is
only written if you change it. Nothing is written until you click Save (Ctrl+S).
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import tags
from ..core.tags import Details, Track

MIXED = "(mixed)"
COVER_SIZE = 220


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
            b.setStyleSheet("QToolButton { font-size: 16px; color: #f2b01e; }")
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


class TagEditor(QWidget):
    save_requested = Signal(list, dict)  # paths, changes
    rating_requested = Signal(list, int)  # paths, stars (saved straight away)

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

        self.cover = QLabel()
        self.cover.setFixedSize(COVER_SIZE, COVER_SIZE)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setStyleSheet("QLabel { border: 1px solid palette(mid); }")
        outer.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignHCenter)
        self.cover_info = QLabel("")
        self.cover_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_info.setStyleSheet("color: palette(placeholder-text);")
        outer.addWidget(self.cover_info)
        art = QHBoxLayout()
        self.art_file = QPushButton("From file…")
        self.art_paste = QPushButton("Paste")
        self.art_remove = QPushButton("Remove")
        self.art_save = QPushButton("Save as…")
        self.art_paste.setToolTip("Paste a picture you copied (e.g. Copy image in a web browser)")
        for b in (self.art_file, self.art_paste, self.art_remove, self.art_save):
            art.addWidget(b)
        outer.addLayout(art)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.edits: dict[str, QLineEdit] = {}
        for key in ("title", "artist", "album_artist", "album", "year", "genre"):
            edit = QLineEdit()
            self.edits[key] = edit
            form.addRow(tags.FIELD_LABELS[key], edit)
        self.edits["genre"].setToolTip("Several genres: separate them with ;")
        nums = QGridLayout()
        for _col, key in enumerate(("track", "track_total", "disc", "disc_total")):
            edit = QLineEdit()
            edit.setMaximumWidth(48)
            self.edits[key] = edit
        nums.addWidget(self.edits["track"], 0, 0)
        nums.addWidget(QLabel("of"), 0, 1)
        nums.addWidget(self.edits["track_total"], 0, 2)
        nums.addWidget(QLabel("   Disc"), 0, 3)
        nums.addWidget(self.edits["disc"], 0, 4)
        nums.addWidget(QLabel("of"), 0, 5)
        nums.addWidget(self.edits["disc_total"], 0, 6)
        nums.setColumnStretch(7, 1)
        form.addRow("Track #", nums)
        self.rating = RatingStars()
        form.addRow("Rating", self.rating)
        outer.addLayout(form)

        outer.addWidget(QLabel("Lyrics (saved in the file and as a .txt next to it)"))
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
        for b in (self.art_file, self.art_paste, self.art_remove):
            b.setEnabled(writable)
        self.art_save.setEnabled(bool(details and details.cover))
        self.save_btn.setEnabled(False)
        self.revert_btn.setEnabled(False)
        if tracks and not writable:
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
            self.cover_info.setText("")
            return
        pix = QPixmap.fromImage(image).scaled(COVER_SIZE, COVER_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation)
        self.cover.setPixmap(pix)
        info = f"{image.width()} × {image.height()}, {len(data) // 1024} KB"  # type: ignore[arg-type]
        if len(tracks) > 1 and not new:
            info += f"  (first song; {sum(t.has_cover for t in tracks)} of {len(tracks)} have art)"
        self.cover_info.setText(info + ("  — not saved yet" if new else ""))

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
        self._mark()

    def _art_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Album art", "", "Pictures (*.jpg *.jpeg *.png *.webp)")
        if path:
            with open(path, "rb") as f:
                self.set_cover(f.read())

    def _art_paste(self) -> None:
        clip = QGuiApplication.clipboard()
        mime = clip.mimeData()
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
        self.cover_info.setText("")
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

    def save(self) -> None:
        if self.tracks and self.changed:
            self.save_requested.emit([t.path for t in self.tracks], dict(self.changed))

    def has_unsaved(self) -> bool:
        return bool(self.changed)
