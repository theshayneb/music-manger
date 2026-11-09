"""Under the Folders list: the cover of the song playing (or the selected one), and the
Library check-up: how many songs need work, each clickable."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from ..core.tags import Track
from .model import fmt_time, stars

# (key, label, Show filter it opens, in which view)
CHECKS = [
    ("songs", "Songs", "", "library"),
    ("cover", "No album art", "cover", "library"),
    ("lyrics", "No lyrics", "lyrics", "library"),
    ("synced", "Timestamped lyrics", "synced", "library"),
    ("tags", "Missing tags", "tags", "library"),
    ("gain", "Volume not measured", "gain", "library"),
    ("low", "Low bitrate (<192)", "low", "library"),
]
REVIEW = [
    ("waiting", "Waiting to review", "", "review"),
    ("convert", "Not MP3 (to convert)", "notmp3", "review"),
]


def count(tracks: list[Track]) -> dict[str, int]:
    """The check-up's numbers for these songs."""
    return {
        "songs": len(tracks),
        "cover": sum(not t.has_cover for t in tracks),
        "lyrics": sum(not t.has_lyrics for t in tracks),
        "synced": sum(t.lyrics_synced for t in tracks),
        "tags": sum(not (t.title and t.artist and t.album and t.year) for t in tracks),
        "gain": sum(not t.gain for t in tracks),
        "low": sum(bool(t.bitrate) and t.bitrate < 192 for t in tracks),
    }


def inside(path: str, folder: str) -> bool:
    if not folder:
        return False
    a, b = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return a.startswith(b.rstrip("\\/") + os.sep)


def kept_this_week(log: list[float]) -> int:
    week_ago = time.time() - 7 * 24 * 3600
    return sum(1 for t in log if t >= week_ago)


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("color: palette(placeholder-text); font-size: 11px; font-weight: 600; padding-top: 10px;"
                        " border-bottom: 1px solid palette(mid);")
    return label


class NowCard(QWidget):
    """Under the Folders list: the selected song's album art with its buttons (the Tags panel's own,
    so they change the song being edited), its title and details. With nothing selected, the
    playing song's art, just to look at."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(4, 0, 4, 0)
        self.box.setSpacing(3)
        self.heading = _section("SELECTED")
        self.box.addWidget(self.heading)
        self.art_slot = QVBoxLayout()
        self.box.addLayout(self.art_slot)
        self.text = QLabel("")
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.box.addWidget(self.text)
        self.path = ""
        self.playing = False

    def set_art(self, art_panel: QWidget) -> None:
        self.art_slot.addWidget(art_panel)

    def show_track(self, track: Track | None, playing: bool) -> None:
        self.heading.setText("NOW PLAYING" if playing else "SELECTED")
        self.path, self.playing = (track.path if track else ""), playing
        if track is None:
            self.text.setText("<span style='color:#9aa0a8'>Select a song</span>")
            return
        import html

        album = track.album + (f" ({track.year})" if track.year else "")
        extra = " · ".join(x for x in (stars(track.rating), f"played {track_plays(track)} times"
                                       if track_plays(track) else "", fmt_time(track.length) if track.length else "")
                           if x)
        self.text.setText(
            f"<b>{html.escape(track.title or track.name)}</b><br>"
            f"<span style='color:#9aa0a8'>{html.escape(' · '.join(x for x in (track.artist, album) if x))}</span>"
            + (f"<br><span style='color:#9aa0a8'>{html.escape(extra)}</span>" if extra else ""))


def track_plays(t: Track) -> int:
    from .model import plays

    return plays(t)


class CheckUp(QWidget):
    """Library check-up: counts you can click to see those songs."""

    picked = Signal(str, str)  # view key, Show filter

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(0)
        self.numbers: dict[str, QLabel] = {}
        for title, rows in (("LIBRARY CHECK-UP", CHECKS), ("REVIEW", REVIEW)):
            layout.addWidget(_section(title))
            grid = QGridLayout()
            grid.setContentsMargins(0, 2, 0, 0)
            grid.setVerticalSpacing(1)
            for r, (key, label, show, view) in enumerate(rows):
                import html

                link = QLabel(f"<a href='#' style='color:#7aa7ff; text-decoration:none'>{html.escape(label)}</a>")
                link.setToolTip(f"Show these songs ({'Review' if view == 'review' else 'Library'})")
                link.linkActivated.connect(lambda _h, view=view, show=show: self.picked.emit(view, show))
                number = QLabel("…")
                number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                grid.addWidget(link, r, 0)
                grid.addWidget(number, r, 1)
                self.numbers[key] = number
            grid.setColumnStretch(0, 1)
            layout.addLayout(grid)
        layout.addWidget(_section("KEPT THIS WEEK"))
        row = QGridLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.addWidget(QLabel("Songs kept"), 0, 0)
        self.kept = QLabel("0")
        self.kept.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.kept, 0, 1)
        row.setColumnStretch(0, 1)
        layout.addLayout(row)
        hint = QLabel("Click one to show those songs")
        hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px; padding-top: 4px;")
        layout.addWidget(hint)

    def set_counts(self, counts: dict[str, int], kept: int) -> None:
        for key, label in self.numbers.items():
            n = counts.get(key)
            label.setText("…" if n is None else f"{n:,}")
            warn = key not in ("songs", "waiting") and bool(n)
            label.setStyleSheet(f"color: {'#e0a040' if warn else 'palette(text)'}; font-weight: 600;")
        self.kept.setText(str(kept))


def counts_for(library: list[Track], review: list[Track]) -> dict[str, int]:
    out = count(library)
    out["waiting"] = len(review)
    out["convert"] = sum(not t.path.lower().endswith(".mp3") for t in review)
    return out


