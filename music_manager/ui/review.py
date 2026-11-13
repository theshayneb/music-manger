"""The last look before Keep: every tag of each song, its album art and its lyrics,
with ⚠ beside anything that's empty."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core import tags
from ..core.tags import Details, Track
from .model import stars

WARN = "⚠"
# As the Tags panel's tabs: the standard tags, then the extra ones.
STANDARD = [("title", "Title"), ("artist", "Artist"), ("album_artist", "Album Artist"), ("album", "Album"),
            ("year", "Year"), ("genre", "Genre"), ("language", "Language"), ("tempo", "Tempo"),
            ("track", "Track #"), ("disc", "Disc #"), ("rating", "Rating")]
EXTRA = [("album_type", "Album Type"), ("edition", "Edition"), ("original_artist", "Original Artist"),
         ("original_album", "Original Album"), ("original_year", "Original Year"),
         ("obsidian_tags", "Obsidian tags")]
FIELDS = STANDARD + EXTRA
ART = 220


def value_of(t: Track, key: str) -> str:
    if key in ("track", "disc"):
        number, total = getattr(t, key), getattr(t, key + "_total")
        return f"{number} of {total}" if number and total else number
    if key == "rating":
        return stars(t.rating)
    if key == "obsidian_tags":
        return " ".join(f"#{x}" for x in tags.split_tags(t.obsidian_tags))
    return str(getattr(t, key, "") or "")


def empties(d: Details) -> list[str]:
    """The names of what's missing: empty tags, no album art, no lyrics."""
    out = [label for key, label in FIELDS if not value_of(d.track, key)]
    if not d.cover:
        out.append("Album art")
    if not d.lyrics.strip():
        out.append("Lyrics")
    return out


class KeepReview(QDialog):
    """exec() accepted = carry on with Keep. Rejected with .fix set = the song to fix first."""

    def __init__(self, details: list[Details], parent: QWidget | None = None,
                 becomes: dict[str, str] | None = None, planner=None, settings=None):  # type: ignore[no-untyped-def]
        """becomes: {song's path: what Keep will make of it} (folder and new name).
        planner(pattern, overwrite) -> [(track, new path or None, note)]: with it, the window also has the
        file-name pattern and "replace" choices, and .rows / .overwrite say what to do."""
        super().__init__(parent)
        self.details = details
        self.becomes = becomes or {}
        self.notes: dict[str, str] = {}
        self.planner, self.settings = planner, settings
        self.rows: list = []
        self.overwrite = False
        self.fix: str | None = None
        self.setWindowTitle(f"Check before keeping — {len(details)} song{'s' if len(details) != 1 else ''}")
        self.resize(980, 720)
        layout = QVBoxLayout(self)
        intro = QLabel(f"Everything these songs will go into the Library with. {WARN} marks what's empty.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        split = QSplitter()
        self.songs = QListWidget()
        self.songs.setMaximumWidth(320)
        for d in details:
            missing = empties(d)
            item = QListWidgetItem(f"{d.track.name}\n{WARN} {len(missing)} empty" if missing else
                                   f"{d.track.name}\n✓ nothing empty")
            item.setToolTip("Empty: " + ", ".join(missing) if missing else "Nothing empty")
            self.songs.addItem(item)
        self.songs.setSpacing(3)
        self.songs.currentRowChanged.connect(self._show)
        split.addWidget(self.songs)
        self.page = QScrollArea()
        self.page.setWidgetResizable(True)
        split.addWidget(self.page)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        if len(details) == 1:
            self.songs.hide()
        self.pattern = None
        self.replace = None
        if planner is not None:
            from PySide6.QtWidgets import QCheckBox, QComboBox

            choices = QHBoxLayout()
            if settings is not None and settings.rename_on_keep:
                choices.addWidget(QLabel("File names"))
                self.pattern = QComboBox()
                self.pattern.setEditable(True)
                self.pattern.addItems(settings.rename_presets)
                self.pattern.setCurrentText(settings.rename_pattern)
                self.pattern.setToolTip("{artist} {title} {album} {albumartist} {year} {genre} {track} {disc} "
                                        "{name}; {track:02} adds a leading zero")
                self.pattern.currentTextChanged.connect(lambda _t: self._replan())
                choices.addWidget(self.pattern, 1)
            self.replace = QCheckBox("Replace songs with the same name in the Library (old ones to the Recycle Bin)")
            self.replace.setToolTip("Otherwise the new song gets “_1” added to its name")
            self.replace.toggled.connect(lambda _on: self._replan())
            choices.addWidget(self.replace)
            layout.addLayout(choices)
        buttons = QDialogButtonBox()
        self.ok = buttons.addButton("Looks right: Keep", QDialogButtonBox.ButtonRole.AcceptRole)
        fix = buttons.addButton("Fix this song first", QDialogButtonBox.ButtonRole.RejectRole)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.ok.clicked.connect(self.accept)
        fix.clicked.connect(self._fix)
        cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)
        self.songs.setCurrentRow(0)
        if planner is not None:
            self._replan()

    def current_pattern(self) -> str:
        return self.pattern.currentText().strip() if self.pattern is not None else ""

    def _replan(self) -> None:
        """Work out (again) where each song goes and what it's called, and show it."""
        from ..core import naming

        pattern = self.current_pattern()
        if pattern and naming.unknown_placeholders(pattern):
            self.ok.setEnabled(False)
            return
        self.overwrite = bool(self.replace and self.replace.isChecked())
        self.rows = self.planner(pattern or None, self.overwrite)
        self.becomes = {t.path: dest for t, dest, _n in self.rows if dest}
        self.notes = {t.path: note for t, _d, note in self.rows}
        self.ok.setEnabled(bool(self.becomes))
        self._show(self.songs.currentRow())

    def _fix(self) -> None:
        row = max(0, self.songs.currentRow())
        self.fix = self.details[row].track.path
        self.reject()

    def _show(self, row: int) -> None:
        if row < 0:
            return
        d = self.details[row]
        t = d.track
        page = QWidget()
        outer = QVBoxLayout(page)
        top = QHBoxLayout()
        art = QLabel()
        art.setFixedSize(ART, ART)
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art.setStyleSheet("QLabel { border: 1px solid palette(mid); }")
        image = QImage.fromData(QByteArray(d.cover)) if d.cover else QImage()
        if image.isNull():
            art.setText(f"{WARN}\nNo album art")
            art.setStyleSheet("QLabel { border: 1px solid #c98a2b; color: #e0a040; }")
        else:
            art.setPixmap(QPixmap.fromImage(image).scaled(ART, ART, Qt.AspectRatioMode.KeepAspectRatio,
                                                          Qt.TransformationMode.SmoothTransformation))
        top.addWidget(art, 0, Qt.AlignmentFlag.AlignTop)
        form = QFormLayout()

        def section(text: str) -> None:
            head = QLabel(text)
            head.setStyleSheet("color: palette(placeholder-text); font-size: 11px; font-weight: 600;"
                               " padding-top: 6px; border-bottom: 1px solid palette(mid);")
            form.addRow(head)

        for key, label in FIELDS:
            if key == "title":
                section("STANDARD TAGS")
            elif key == "album_type":
                section("EXTRA TAGS")
            value = value_of(t, key)
            text = QLabel(value if value else f"<span style='color:#e0a040'>{WARN} empty</span>")
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text.setWordWrap(True)
            name = QLabel(f"{WARN} {label}" if not value else label)
            if not value:
                name.setStyleSheet("color: #e0a040;")
            form.addRow(name, text)
        info = f"{t.format}, {t.bitrate} kbps" + (" (will be converted to MP3)" if t.format != "MP3" else "")
        becomes = self.becomes.get(t.path)
        if becomes:
            import html
            import os

            where = os.path.join(os.path.basename(os.path.dirname(becomes)), os.path.basename(becomes))
            note = self.notes.get(t.path, "")
            file_text = QLabel(f"<b>Becomes: {html.escape(where)}</b><br>"
                               f"<span style='color:#9aa0a8'>Now: {html.escape(t.name)}<br>{html.escape(info)}"
                               + (f"<br>{html.escape(note)}" if note else "") + "</span>")
            file_text.setToolTip(becomes)
        else:
            file_text = QLabel(f"{t.name}\n{info}")
        file_text.setWordWrap(True)
        section("FILE")
        form.addRow("File", file_text)
        top.addLayout(form, 1)
        outer.addLayout(top)
        title = QLabel("Lyrics" if d.lyrics.strip() else f"{WARN} Lyrics: none")
        if not d.lyrics.strip():
            title.setStyleSheet("color: #e0a040;")
        outer.addWidget(title)
        lyrics = QPlainTextEdit(d.lyrics)
        lyrics.setReadOnly(True)
        lyrics.setMinimumHeight(220)
        outer.addWidget(lyrics, 1)
        self.page.setWidget(page)
