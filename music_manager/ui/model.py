"""The song list: a table model with sortable columns and a search filter."""

from __future__ import annotations

import os

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from ..core.tags import Track

COLUMNS: list[tuple[str, str]] = [
    ("rating", "Rating"),
    ("plays", "Plays"),
    ("title", "Title"),
    ("artist", "Artist"),
    ("album", "Album"),
    ("album_artist", "Album Artist"),
    ("year", "Year"),
    ("genre", "Genre"),
    ("language", "Language"),
    ("tempo", "Tempo"),
    ("track", "#"),
    ("length", "Time"),
    ("bitrate", "Bitrate"),
    ("format", "Type"),
    ("lyrics", "Lyrics"),
    ("cover", "Art"),
    ("gain", "Gain"),
    ("name", "File name"),
]
COLUMN_KEYS = [key for key, _ in COLUMNS]
HIDDEN_BY_DEFAULT = {"album_artist", "genre", "track", "language", "tempo"}

SORT_ROLE = Qt.ItemDataRole.UserRole + 1


# Play counts from Plex, by os.path.normcase(path); filled in by the window when Plex is set up.
PLEX_PLAYS: dict[str, int] = {}


def plays(t: Track) -> int:
    """Plex's play count when Plex has the song, else the one saved in the file."""
    plex = PLEX_PLAYS.get(os.path.normcase(t.path))
    return plex if plex is not None else t.plays


def fmt_time(seconds: float) -> str:
    s = int(round(seconds or 0))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def stars(n: int) -> str:
    return "★" * n if n else ""


class TrackModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.tracks: list[Track] = []
        self.playing: str = ""

    # ------------------------------------------------------------ Qt API
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.tracks)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[no-untyped-def]
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section][1]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[no-untyped-def]
        if not index.isValid():
            return None
        t = self.tracks[index.row()]
        key = COLUMN_KEYS[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self.display(t, key)
        if role == SORT_ROLE:
            return self.sort_value(t, key)
        if role == Qt.ItemDataRole.ToolTipRole:
            if t.error:
                return f"Couldn't read this file: {t.error}"
            if key == "lyrics" and t.has_lyrics:
                where = [w for w, ok in (("in the file", t.lyrics_in_file), (".txt file", t.lyrics_file)) if ok]
                return "Lyrics: " + " and ".join(where)
            if key == "plays":
                plex = PLEX_PLAYS.get(os.path.normcase(t.path))
                if plex is not None:
                    return f"Played {plex} times in Plex" + (f" ({t.plays} in the file)" if t.plays else "")
                return f"{t.plays} plays saved in the file" if t.plays else None
            if key == "bitrate" and t.bitrate:
                return f"{t.bitrate} kbps {t.bitrate_mode}, {t.sample_rate / 1000:g} kHz".strip()
            return t.path if key == "name" else None
        if role == Qt.ItemDataRole.ForegroundRole:
            if t.error:
                return QColor("#e0605a")
            if key == "rating":
                return QColor("#f2b01e")
        if role == Qt.ItemDataRole.FontRole and t.path == self.playing:
            from PySide6.QtGui import QFont

            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ItemDataRole.TextAlignmentRole and key in ("length", "bitrate", "track", "year", "gain", "plays"):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    @staticmethod
    def display(t: Track, key: str) -> str:
        if key == "rating":
            return stars(t.rating)
        if key == "length":
            return fmt_time(t.length) if t.length else ""
        if key == "plays":
            n = plays(t)
            return str(n) if n else ""
        if key == "bitrate":
            return f"{t.bitrate}{' VBR' if t.bitrate_mode == 'VBR' else ''}" if t.bitrate else ""
        if key == "lyrics":
            return "✓" if t.has_lyrics else ""
        if key == "cover":
            return "✓" if t.has_cover else ""
        if key == "name":
            return t.name
        if key == "track":
            return t.track
        return str(getattr(t, key, "") or "")

    @staticmethod
    def sort_value(t: Track, key: str):  # type: ignore[no-untyped-def]
        if key == "plays":
            return float(plays(t))
        if key in ("rating", "length", "bitrate"):
            return float(getattr(t, key) or 0)
        if key == "track":
            try:
                return float(t.track)
            except ValueError:
                return 0.0
        if key in ("lyrics", "cover"):
            return 1 if (t.has_lyrics if key == "lyrics" else t.has_cover) else 0
        if key == "gain":
            try:
                return float(t.gain.split()[0])
            except (ValueError, IndexError):
                return -999.0
        return TrackModel.display(t, key).lower()

    # ------------------------------------------------------------ changes
    def set_tracks(self, tracks: list[Track]) -> None:
        self.beginResetModel()
        self.tracks = list(tracks)
        self.endResetModel()

    def append(self, tracks: list[Track]) -> None:
        if not tracks:
            return
        start = len(self.tracks)
        self.beginInsertRows(QModelIndex(), start, start + len(tracks) - 1)
        self.tracks.extend(tracks)
        self.endInsertRows()

    def row_of(self, path: str) -> int:
        key = os.path.normcase(path)
        for i, t in enumerate(self.tracks):
            if os.path.normcase(t.path) == key:
                return i
        return -1

    def replace(self, old_path: str, track: Track) -> None:
        row = self.row_of(old_path)
        if row < 0:
            return
        self.tracks[row] = track
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))

    def remove(self, paths: list[str]) -> None:
        for path in paths:
            row = self.row_of(path)
            if row >= 0:
                self.beginRemoveRows(QModelIndex(), row, row)
                del self.tracks[row]
                self.endRemoveRows()

    def set_playing(self, path: str) -> None:
        old, self.playing = self.playing, path
        for p in (old, path):
            row = self.row_of(p) if p else -1
            if row >= 0:
                self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))


class TrackFilter(QSortFilterProxyModel):
    """Search: every word must appear in the title, artist, album, album artist, genre or file name."""

    def __init__(self) -> None:
        super().__init__()
        self.words: list[str] = []
        self.missing: str = ""  # "", "lyrics", "cover", "tags", "gain"
        self.setSortRole(SORT_ROLE)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_search(self, text: str) -> None:
        self.words = [w.lower() for w in text.split()]
        self.invalidateFilter()

    def set_missing(self, what: str) -> None:
        self.missing = what
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        model: TrackModel = self.sourceModel()  # type: ignore[assignment]
        t = model.tracks[row]
        if self.missing == "lyrics" and t.has_lyrics:
            return False
        if self.missing == "cover" and t.has_cover:
            return False
        if self.missing == "gain" and t.gain:
            return False
        if self.missing == "tags" and t.title and t.artist and t.album and t.year:
            return False
        if self.missing == "low" and (not t.bitrate or t.bitrate >= 192):
            return False
        if not self.words:
            return True
        hay = " ".join((t.title, t.artist, t.album, t.album_artist, t.genre, t.name)).lower()
        return all(w in hay for w in self.words)
