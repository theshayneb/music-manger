"""The alphabet jump bar (# A B C … Z) above the songs, like MusicBee's.

Click a letter to jump to the first artist starting with it; click another within two
seconds to narrow it down (M, then Y: My Chemical Romance). Typing letters while the
song list has the focus does the same.
"""

from __future__ import annotations

import string
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QTableView, QToolButton, QWidget

from ..core.albums import artist_sort_key, sort_name
from ..core.tags import Track

AGAIN_SECONDS = 2.0


def jump_key(t: Track) -> str:
    return artist_sort_key(sort_name(t) or t.title or t.name)


def find(tracks: list[Track], prefix: str) -> int:
    """Index of the first song whose artist starts with prefix ("#" = not a letter); if none
    does, the first that comes after it. -1 when there are no songs."""
    if not tracks:
        return -1
    keys = [jump_key(t) for t in tracks]
    if prefix == "#":
        return next((i for i, k in enumerate(keys) if k and not k[0].isalpha()), 0)
    prefix = prefix.casefold()
    exact = next((i for i, k in enumerate(keys) if k.startswith(prefix)), -1)
    if exact >= 0:
        return exact
    after = [i for i, k in enumerate(keys) if k > prefix]
    return min(after, key=lambda i: keys[i]) if after else len(tracks) - 1


class Typed:
    """Letters typed or clicked close together make one prefix."""

    def __init__(self) -> None:
        self.text = ""
        self.at = 0.0

    def add(self, letters: str) -> str:
        now = time.monotonic()
        if letters == "#" or now - self.at > AGAIN_SECONDS or self.text == "#":
            self.text = ""
        self.text += letters
        self.at = now
        return self.text


class JumpBar(QWidget):
    jump = Signal(str)  # one letter (or "#")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(0)
        for letter in ["#", *string.ascii_uppercase]:
            b = QToolButton(text=letter)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setToolTip("Jump to artists starting with this. Click more letters to narrow it down.")
            b.clicked.connect(lambda _=False, letter=letter: self.jump.emit(letter))
            layout.addWidget(b, 1)


class JumpTable(QTableView):
    """A table where typing letters jumps by artist (instead of searching its first column)."""

    typed = Signal(str)

    def keyboardSearch(self, search: str) -> None:  # noqa: N802 - Qt's name
        text = search[-1:] if search else ""
        if text.isalnum():
            self.typed.emit(text)
