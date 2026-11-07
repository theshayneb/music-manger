"""Settings, and the preview shown before Keep / Rename / Convert change anything."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import naming
from ..core.settings import MP3_QUALITIES, Settings
from ..core.tags import Track
from .theme import THEMES


def _folder_row(parent: QWidget, edit: QLineEdit, title: str) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(edit, 1)
    browse = QPushButton("Browse…")

    def pick() -> None:
        folder = QFileDialog.getExistingDirectory(parent, title, edit.text())
        if folder:
            edit.setText(os.path.normpath(folder))

    browse.clicked.connect(pick)
    row.addWidget(browse)
    return row


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None, first_run: bool = False):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Music Manager settings")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        if first_run:
            intro = QLabel("Choose your two folders. <b>Review</b> is where new music waits while you decide "
                           "(e.g. Downloads on the external drive). <b>Library</b> is where music you keep goes "
                           "(your Music folder in OneDrive).")
            intro.setWordWrap(True)
            layout.addWidget(intro)
        form = QFormLayout()
        self.review = QLineEdit(settings.review_folder)
        self.library = QLineEdit(settings.library_folder)
        form.addRow("Review folder", _folder_row(self, self.review, "Review folder"))
        self.review_sub = QCheckBox("Include subfolders")
        self.review_sub.setChecked(settings.review_subfolders)
        form.addRow("", self.review_sub)
        form.addRow("Library folder", _folder_row(self, self.library, "Library folder"))
        self.library_sub = QCheckBox("Include subfolders")
        self.library_sub.setChecked(settings.library_subfolders)
        form.addRow("", self.library_sub)

        self.quality = QComboBox()
        for key, (label, _args) in MP3_QUALITIES.items():
            self.quality.addItem(label, key)
        self.quality.setCurrentIndex(max(0, self.quality.findData(settings.mp3_quality)))
        form.addRow("Convert to MP3 at", self.quality)
        self.recycle = QCheckBox("Send the original (e.g. FLAC) to the Recycle Bin after converting")
        self.recycle.setChecked(settings.recycle_after_convert)
        form.addRow("", self.recycle)

        self.pattern = QComboBox()
        self.pattern.setEditable(True)
        self.pattern.addItems(settings.rename_presets)
        self.pattern.setCurrentText(settings.rename_pattern)
        form.addRow("File names", self.pattern)
        hint = QLabel(naming.HELP)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        form.addRow("", hint)
        self.rename_on_keep = QCheckBox("Rename songs this way when keeping them")
        self.rename_on_keep.setChecked(settings.rename_on_keep)
        form.addRow("", self.rename_on_keep)
        self.gain_on_keep = QCheckBox("Measure volume (ReplayGain) when keeping songs")
        self.gain_on_keep.setChecked(settings.gain_on_keep)
        form.addRow("", self.gain_on_keep)
        self.autoplay = QCheckBox("Play the next song when one ends, and after Keep / Delete")
        self.autoplay.setChecked(settings.autoplay_next)
        form.addRow("", self.autoplay)
        self.confirm = QCheckBox("Ask before deleting")
        self.confirm.setChecked(settings.confirm_delete)
        form.addRow("", self.confirm)
        self.theme = QComboBox()
        for key, label in THEMES.items():
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.theme)))
        form.addRow("Theme", self.theme)
        layout.addLayout(form)

        plex = QFormLayout()
        title = QLabel("<b>Plex</b> (optional): the Plays column shows Plex's play counts, and Plex rescans your "
                       "Library folder after you keep songs or change tags.")
        title.setWordWrap(True)
        layout.addWidget(title)
        self.plex_url = QLineEdit(settings.plex_url)
        self.plex_url.setPlaceholderText("http://localhost:32400")
        plex.addRow("Server address", self.plex_url)
        self.plex_token = QLineEdit(settings.plex_token)
        self.plex_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.plex_token.setPlaceholderText("your X-Plex-Token")
        token_help = QLabel("In Plex Web, open any song, choose ⋯ → Get Info → View XML, and copy what comes after "
                            "X-Plex-Token= in the address bar.")
        token_help.setWordWrap(True)
        token_help.setStyleSheet("color: palette(placeholder-text);")
        plex.addRow("Token", self.plex_token)
        plex.addRow("", token_help)
        test_row = QHBoxLayout()
        test = QPushButton("Test")
        self.plex_result = QLabel("")
        self.plex_result.setWordWrap(True)
        test.clicked.connect(self._test_plex)
        test_row.addWidget(test)
        test_row.addWidget(self.plex_result, 1)
        plex.addRow("", test_row)
        self.plex_plays = QCheckBox("Show Plex's play counts in the Plays column")
        self.plex_plays.setChecked(settings.plex_plays)
        plex.addRow("", self.plex_plays)
        self.plex_refresh = QCheckBox("Ask Plex to rescan the Library folder after Keep and tag changes")
        self.plex_refresh.setChecked(settings.plex_refresh)
        plex.addRow("", self.plex_refresh)
        layout.addLayout(plex)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.error = QLabel("")
        self.error.setStyleSheet("color: #e0605a;")
        layout.addWidget(self.error)

    def _test_plex(self) -> None:
        from PySide6.QtWidgets import QApplication

        from ..core.plex import Plex, PlexError

        self.plex_result.setText("Connecting…")
        QApplication.processEvents()
        try:
            client = Plex(self.plex_url.text(), self.plex_token.text())
            sections = client.music_sections()
        except PlexError as exc:
            self.plex_result.setText(f"✗ {exc}")
            return
        names = ", ".join(s.get("title", "") for s in sections) or "none"
        self.plex_result.setText(f"✓ Connected. Music libraries: {names}")

    def _accept(self) -> None:
        review, library = self.review.text().strip(), self.library.text().strip()
        for label, folder in (("Review", review), ("Library", library)):
            if folder and not os.path.isdir(folder):
                self.error.setText(f"The {label} folder doesn't exist: {folder}")
                return
        if review and library and os.path.normcase(review) == os.path.normcase(library):
            self.error.setText("Review and Library need to be different folders.")
            return
        pattern = self.pattern.currentText().strip() or self.settings.rename_pattern
        bad = naming.unknown_placeholders(pattern)
        if bad:
            self.error.setText("Unknown placeholder: " + ", ".join("{" + b + "}" for b in bad))
            return
        s = self.settings
        s.review_folder, s.library_folder = review, library
        s.review_subfolders, s.library_subfolders = self.review_sub.isChecked(), self.library_sub.isChecked()
        s.mp3_quality = self.quality.currentData()
        s.recycle_after_convert = self.recycle.isChecked()
        s.rename_pattern = pattern
        s.remember_pattern(pattern)
        s.rename_on_keep = self.rename_on_keep.isChecked()
        s.gain_on_keep = self.gain_on_keep.isChecked()
        s.autoplay_next = self.autoplay.isChecked()
        s.confirm_delete = self.confirm.isChecked()
        s.theme = self.theme.currentData()
        s.plex_url, s.plex_token = self.plex_url.text().strip(), self.plex_token.text().strip()
        s.plex_plays, s.plex_refresh = self.plex_plays.isChecked(), self.plex_refresh.isChecked()
        self.accept()


class PlanDialog(QDialog):
    """A table of what will happen to each song, shown before anything changes.

    With pattern=True the rename pattern can be changed and the table updates."""

    def __init__(self, title: str, intro: str, tracks: list[Track], settings: Settings,
                 build, ok_text: str, pattern: bool = False, parent: QWidget | None = None):  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 420)
        self.tracks, self.settings, self.build = tracks, settings, build
        self.rows: list[tuple[Track, str | None, str]] = []
        layout = QVBoxLayout(self)
        label = QLabel(intro)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.pattern = None
        if pattern:
            row = QHBoxLayout()
            row.addWidget(QLabel("Name pattern"))
            self.pattern = QComboBox()
            self.pattern.setEditable(True)
            self.pattern.addItems(settings.rename_presets)
            self.pattern.setCurrentText(settings.rename_pattern)
            self.pattern.currentTextChanged.connect(self.refresh)
            row.addWidget(self.pattern, 1)
            layout.addLayout(row)
            hint = QLabel(naming.HELP)
            hint.setWordWrap(True)
            hint.setStyleSheet("color: palette(placeholder-text);")
            layout.addWidget(hint)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Song", "Becomes", "Note"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)
        self.summary = QLabel("")
        layout.addWidget(self.summary)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.refresh()

    def current_pattern(self) -> str:
        return self.pattern.currentText().strip() if self.pattern else self.settings.rename_pattern

    def refresh(self) -> None:
        pattern = self.current_pattern()
        bad = naming.unknown_placeholders(pattern)
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if bad:
            self.summary.setText("Unknown placeholder: " + ", ".join("{" + b + "}" for b in bad))
            ok.setEnabled(False)
            return
        self.rows = self.build(pattern)
        self.table.setRowCount(len(self.rows))
        changes = 0
        for r, (track, becomes, note) in enumerate(self.rows):
            self.table.setItem(r, 0, QTableWidgetItem(track.name))
            item = QTableWidgetItem(becomes or "—")
            if becomes:
                item.setToolTip(becomes)
                changes += 1
            self.table.setItem(r, 1, item)
            self.table.setItem(r, 2, QTableWidgetItem(note))
        self.summary.setText(f"{changes} of {len(self.rows)} songs will change.")
        ok.setEnabled(changes > 0)

    def accept(self) -> None:  # noqa: D102
        if self.pattern is not None:
            self.settings.remember_pattern(self.current_pattern())
        super().accept()


def ask_text(parent: QWidget, title: str, label: str, text: str) -> str | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(480)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(label))
    edit = QLineEdit(text)
    layout.addWidget(edit)
    base = os.path.splitext(text)[0]
    edit.setSelection(0, len(base))
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    edit.setFocus(Qt.FocusReason.OtherFocusReason)
    return edit.text() if dialog.exec() == QDialog.DialogCode.Accepted else None
