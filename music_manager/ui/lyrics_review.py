"""Find lyrics for several songs: what was found for each, next to what it has now, before
anything is saved. Each song can be ticked or not, and the found lyrics edited."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core import tags
from ..core.online import Lyrics
from ..core.tags import Track


@dataclass
class Found:
    track: Track
    current: str
    found: Lyrics | None
    text: str = ""       # what will be saved (the found lyrics, maybe edited)
    use: bool = False


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def status(item: Found) -> str:
    if item.found is None:
        return "✗ nothing found"
    if _same(item.found.text, item.current):
        return f"= same as now ({item.found.source})"
    if not item.current.strip():
        return f"+ new, from {item.found.source}"
    return f"↻ replaces {'timestamped ' if tags.is_synced(item.current) else ''}lyrics, from {item.found.source}"


class LyricsReview(QDialog):
    def __init__(self, items: list[Found], parent: QWidget | None = None):
        super().__init__(parent)
        self.items = items
        for item in items:
            item.text = item.found.text if item.found else ""
            # Ticked: anything found that's different from what the song has now.
            item.use = bool(item.found) and not _same(item.text, item.current)
        self.setWindowTitle(f"Find lyrics — check {len(items)} songs")
        self.resize(1100, 700)
        layout = QVBoxLayout(self)
        intro = QLabel("Untick any that aren't right. Found lyrics can be edited before saving. "
                       "Ticked ones are saved in the file and as the song's .txt.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        split = QSplitter()
        self.songs = QListWidget()
        self.songs.setMinimumWidth(300)
        self.songs.setSpacing(2)
        for _item in items:
            row = QListWidgetItem()
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            self.songs.addItem(row)
        self._label_all()
        self.songs.currentRowChanged.connect(self._show)
        self.songs.itemChanged.connect(self._ticked)
        split.addWidget(self.songs)
        right = QWidget()
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        now_box = QVBoxLayout()
        self.now_title = QLabel("Now")
        now_box.addWidget(self.now_title)
        self.now = QPlainTextEdit()
        self.now.setReadOnly(True)
        now_box.addWidget(self.now)
        found_box = QVBoxLayout()
        self.found_title = QLabel("Found")
        found_box.addWidget(self.found_title)
        self.found = QPlainTextEdit()
        self.found.textChanged.connect(self._edited)
        found_box.addWidget(self.found)
        self.use = QCheckBox("Use these lyrics for this song")
        self.use.toggled.connect(self._use_toggled)
        found_box.addWidget(self.use)
        rl.addLayout(now_box, 1)
        rl.addLayout(found_box, 1)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        buttons = QHBoxLayout()
        tick_all, tick_none = QPushButton("Tick all found"), QPushButton("Tick none")
        tick_all.clicked.connect(lambda: self._tick_every(True))
        tick_none.clicked.connect(lambda: self._tick_every(False))
        buttons.addWidget(tick_all)
        buttons.addWidget(tick_none)
        self.summary = QLabel("")
        buttons.addWidget(self.summary, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = box.button(QDialogButtonBox.StandardButton.Ok)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        buttons.addWidget(box)
        layout.addLayout(buttons)
        self._loading = False
        self.songs.setCurrentRow(0)
        self._summary()

    def chosen(self) -> list[Found]:
        return [i for i in self.items if i.use and i.text.strip()]

    # ------------------------------------------------------------
    def _label_all(self) -> None:
        self.songs.blockSignals(True)
        for row, item in enumerate(self.items):
            widget_item = self.songs.item(row)
            widget_item.setText(f"{item.track.name}\n{status(item)}")
            widget_item.setCheckState(Qt.CheckState.Checked if item.use else Qt.CheckState.Unchecked)
            if item.found is None:
                widget_item.setFlags(widget_item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        self.songs.blockSignals(False)

    def _show(self, row: int) -> None:
        if row < 0:
            return
        item = self.items[row]
        self._loading = True
        synced = " (timestamped)" if tags.is_synced(item.current) else ""
        self.now_title.setText(f"Now{synced}" if item.current.strip() else "Now: no lyrics")
        self.now.setPlainText(item.current)
        if item.found:
            where = f" — {item.found.url}" if item.found.url else ""
            self.found_title.setText(f"Found on {item.found.source}{where}")
        else:
            self.found_title.setText("Nothing found (try Find lyrics on this song alone, where the artist and "
                                     "title can be changed)")
        self.found.setPlainText(item.text)
        self.found.setReadOnly(item.found is None)
        self.use.setEnabled(item.found is not None)
        self.use.setChecked(item.use)
        self._loading = False

    def _edited(self) -> None:
        if self._loading:
            return
        row = self.songs.currentRow()
        if row >= 0:
            self.items[row].text = self.found.toPlainText()

    def _use_toggled(self, on: bool) -> None:
        if self._loading:
            return
        row = self.songs.currentRow()
        if row >= 0:
            self.items[row].use = on
            self._label_all()
            self._summary()

    def _ticked(self, widget_item: QListWidgetItem) -> None:
        row = self.songs.row(widget_item)
        self.items[row].use = widget_item.checkState() == Qt.CheckState.Checked
        if row == self.songs.currentRow():
            self._loading = True
            self.use.setChecked(self.items[row].use)
            self._loading = False
        self._summary()

    def _tick_every(self, on: bool) -> None:
        for item in self.items:
            item.use = on and item.found is not None
        self._label_all()
        self._show(self.songs.currentRow())
        self._summary()

    def _summary(self) -> None:
        n = len(self.chosen())
        missing = sum(1 for i in self.items if i.found is None)
        self.summary.setText(f"{n} to save" + (f" · {missing} not found" if missing else ""))
        self.ok.setText(f"Save {n}" if n else "Save")
        self.ok.setEnabled(n > 0)
