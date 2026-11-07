"""The album view: an artist heading, then each album with its cover and details on the
left and its songs on the right, split by edition / disc. Laid out like MusicBee's
"album and tracks" view, with a few choices (Layout menu) instead of dozens.

It's a table whose first column is one tall cell per album (a span) painted by
ArtDelegate; the other columns are the song columns of the plain list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QStyledItemDelegate, QStyleOptionViewItem, QTableView

from ..core import albums as grouping
from ..core.tags import Track
from .jump import JumpTable
from .model import COLUMNS, TrackModel, fmt_time

ROW = 24
HEADING_ROW = 40
ART_COLUMN = [("art", "Album")]
# Song columns in the order of the MusicBee screen: #, Title, Artist, Album Artist, Rating, Plays, Time.
FIRST = ["track", "title", "artist", "album_artist", "rating", "plays", "length"]
ALBUM_COLUMNS = ART_COLUMN + [c for k in FIRST for c in COLUMNS if c[0] == k] + [
    c for c in COLUMNS if c[0] not in FIRST]


@dataclass
class Row:
    kind: str                     # section | sub | track | pad | blank | gap
    text: str = ""
    track: Track | None = None
    album: grouping.Album | None = None
    alt: bool = False             # alternate shading for song rows


class AlbumModel(QAbstractTableModel):
    def __init__(self, tracks_model: TrackModel) -> None:
        super().__init__()
        self.tracks_model = tracks_model
        self.columns = ALBUM_COLUMNS
        self.rows: list[Row] = []
        self.spans: list[tuple[int, int, int, int]] = []
        self._row_of: dict[str, int] = {}
        self.covers: dict[tuple, QPixmap] = {}
        self.cover_loader = None  # set by the window: fn(album) -> None (loads in the background)
        self.layout = {"group": "artist", "order": "year", "sides": True, "blank": True, "art": 150}

    # ------------------------------------------------------------ building
    def art_size(self) -> int:
        return int(self.layout.get("art", 150))

    def rebuild(self, tracks: list[Track]) -> None:
        self.beginResetModel()
        rows: list[Row] = []
        spans: list[tuple[int, int, int, int]] = []
        ncols = len(self.columns)
        art_rows = max(4, -(-(self.art_size() + 20) // ROW))
        sections = grouping.build(tracks, self.layout.get("group", "artist"), self.layout.get("order", "year"),
                                  bool(self.layout.get("sides", True)))
        for s_index, section in enumerate(sections):
            if section.heading:
                if s_index:
                    rows.append(Row("gap"))
                    spans.append((len(rows) - 1, 0, 1, ncols))
                rows.append(Row("section", section.heading))
                spans.append((len(rows) - 1, 0, 1, ncols))
            for a_index, album in enumerate(section.albums):
                if a_index or not section.heading:
                    rows.append(Row("gap"))
                    spans.append((len(rows) - 1, 0, 1, ncols))
                start = len(rows)
                for g_index, group in enumerate(album.groups):
                    if group.heading:
                        if g_index and self.layout.get("blank", True):
                            rows.append(Row("blank", album=album))
                            spans.append((len(rows) - 1, 1, 1, ncols - 1))
                        rows.append(Row("sub", group.heading, album=album))
                        spans.append((len(rows) - 1, 1, 1, ncols - 1))
                    for i, t in enumerate(group.tracks):
                        rows.append(Row("track", track=t, album=album, alt=bool(i % 2)))
                while len(rows) - start < art_rows:
                    rows.append(Row("pad", album=album))
                    spans.append((len(rows) - 1, 1, 1, ncols - 1))
                spans.append((start, 0, len(rows) - start, 1))
        self.rows, self.spans = rows, spans
        self._row_of = {os.path.normcase(r.track.path): i for i, r in enumerate(rows) if r.track}
        self.endResetModel()

    def apply_spans(self, view: QTableView) -> None:
        view.clearSpans()
        header = view.horizontalHeader()
        ncols = len(self.columns)
        # Headings start in the first song column that's shown (a span starting in a hidden one isn't drawn).
        first = next((c for c in range(1, ncols) if not header.isSectionHidden(c)), 1)
        for row, col, rs, cs in self.spans:
            if col == 1:
                col, cs = first, ncols - first
            if rs > 1 or cs > 1:
                view.setSpan(row, col, rs, cs)
        for i, r in enumerate(self.rows):
            if r.kind == "section":
                view.setRowHeight(i, HEADING_ROW)
            elif r.kind == "gap":
                view.setRowHeight(i, 14)

    # ------------------------------------------------------------ for the window
    def track(self, row: int) -> Track | None:
        return self.rows[row].track if 0 <= row < len(self.rows) else None

    def row_of(self, path: str) -> int:
        return self._row_of.get(os.path.normcase(path), -1)

    def order(self) -> list[Track]:
        return [r.track for r in self.rows if r.track]

    def album_rows(self, row: int) -> list[int]:
        album = self.rows[row].album if 0 <= row < len(self.rows) else None
        return [i for i, r in enumerate(self.rows) if r.track and r.album is album] if album else []

    def first_row_of(self, album: grouping.Album) -> int:
        return next((i for i, r in enumerate(self.rows) if r.album is album), -1)

    def set_cover(self, key: tuple, pixmap: QPixmap) -> None:
        self.covers[key] = pixmap
        for i, r in enumerate(self.rows):
            if r.album is not None and r.album.key == key:
                self.dataChanged.emit(self.index(i, 0), self.index(i, 0))
                break

    # ------------------------------------------------------------ Qt API
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[no-untyped-def]
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section][1]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        row = self.rows[index.row()]
        if row.kind == "track" and index.column() > 0:
            return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        return Qt.ItemFlag.ItemIsEnabled

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[no-untyped-def]
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        key = self.columns[index.column()][0]
        if row.kind == "section":
            if role == Qt.ItemDataRole.DisplayRole and index.column() == 0:
                return row.text
            if role == Qt.ItemDataRole.FontRole:
                font = QFont()
                font.setPointSizeF(font.pointSizeF() * 1.35)
                font.setBold(True)
                return font
            return None
        if row.kind == "sub":
            if role == Qt.ItemDataRole.DisplayRole and index.column() >= 1:
                return row.text
            if role == Qt.ItemDataRole.FontRole:
                font = QFont()
                font.setItalic(True)
                return font
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor("#c9b27c")
            return None
        if row.kind != "track" or row.track is None or key == "art":
            return None
        t = row.track
        if role == Qt.ItemDataRole.DisplayRole:
            return TrackModel.display(t, key)
        if role == Qt.ItemDataRole.BackgroundRole and row.alt:
            return QColor(255, 255, 255, 8)
        if role == Qt.ItemDataRole.ForegroundRole:
            if t.error:
                return QColor("#e0605a")
            if key == "rating":
                return QColor("#f2b01e")
        if role == Qt.ItemDataRole.FontRole and t.path == self.tracks_model.playing:
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ItemDataRole.ToolTipRole:
            if t.error:
                return f"Couldn't read this file: {t.error}"
            return t.path if key == "name" else None
        if role == Qt.ItemDataRole.TextAlignmentRole and key in ("length", "bitrate", "year", "gain", "plays"):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


class AlbumTable(JumpTable):
    """The album view's table: the arrow keys (and Page Up/Down, Home/End) only stop on songs,
    never on the headings, blank lines or the space beside a cover."""

    def moveCursor(self, action, modifiers):  # type: ignore[no-untyped-def]  # noqa: N802
        index = super().moveCursor(action, modifiers)
        model = self.model()
        if not isinstance(model, AlbumModel) or not index.isValid():
            return index
        A = QAbstractItemView.CursorAction
        step = -1 if action in (A.MoveUp, A.MovePageUp, A.MoveHome, A.MovePrevious, A.MoveLeft) else 1
        if action == A.MoveHome:
            step = 1
        elif action == A.MoveEnd:
            step = -1
        row = index.row()
        while 0 <= row < len(model.rows) and model.rows[row].kind != "track":
            row += step
        if not 0 <= row < len(model.rows):  # nothing that way: stay where you are
            row = self.currentIndex().row()
            if not 0 <= row < len(model.rows) or model.rows[row].kind != "track":
                return self.currentIndex()
        return model.index(row, max(1, index.column()))


def paint_stars(p: QPainter, x: int, y: int, value: float, size: int = 14) -> None:
    """Five stars with half stars, e.g. 2.5."""
    font = QFont(p.font())
    font.setPixelSize(size)
    p.setFont(font)
    step = size + 1
    for i in range(5):
        rect = QRect(x + i * step, y, step, size + 4)
        p.setPen(QColor("#5b5f66"))
        p.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "★")
        fill = min(1.0, max(0.0, value - i))
        if fill > 0:
            p.save()
            p.setClipRect(QRect(x + i * step, y, int(step * fill) if fill < 1 else step, size + 4))
            p.setPen(QColor("#b9bcc2"))
            p.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "★")
            p.restore()


class ArtDelegate(QStyledItemDelegate):
    """Paints the album cell (cover + details) and the artist headings."""

    def __init__(self, model: AlbumModel, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.model = model

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        row = self.model.rows[index.row()]
        if row.kind == "section" and index.column() == 0:
            p.save()
            font = QFont(option.font)
            font.setPointSizeF(font.pointSizeF() * 1.35)
            font.setBold(True)
            p.setFont(font)
            p.setPen(option.palette.text().color())
            r = option.rect.adjusted(8, 0, -8, 0)
            p.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, row.text)
            p.setPen(option.palette.mid().color())
            p.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
            p.restore()
            return
        if index.column() != 0 or row.album is None:
            super().paint(p, option, index)
            return
        album = row.album
        size = self.model.art_size()
        rect = option.rect
        p.save()
        art = QRect(rect.left() + 8, rect.top() + 6, size, size)
        pixmap = self.model.covers.get(album.key)
        if pixmap is None and album.cover_path and self.model.cover_loader:
            self.model.covers[album.key] = QPixmap()  # loading
            self.model.cover_loader(album)
        if pixmap is not None and not pixmap.isNull():
            scaled = pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(art.left() + (size - scaled.width()) // 2, art.top() + (size - scaled.height()) // 2, scaled)
        else:
            p.fillRect(art, QColor("#2c2f34"))
            p.setPen(QColor("#5b5f66"))
            p.drawText(art, Qt.AlignmentFlag.AlignCenter, "♪")
        p.setPen(QColor("#e8e8e8"))
        p.drawRect(art.adjusted(-1, -1, 0, 0))
        x = art.right() + 14
        width = rect.right() - x - 6
        y = art.top() + 2
        bold = QFont(option.font)
        bold.setBold(True)
        bold.setPointSizeF(option.font.pointSizeF() * 1.08)
        p.setFont(bold)
        p.setPen(option.palette.text().color())
        line = QRect(x, y, width, 20)
        p.drawText(line, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(album.title_line(), Qt.TextElideMode.ElideRight, width))
        y += 22
        if album.rating:
            paint_stars(p, x, y, album.rating)
        y += 20
        small = QFont(option.font)
        small.setPointSizeF(option.font.pointSizeF() * 0.92)
        p.setFont(small)
        p.setPen(option.palette.placeholderText().color())
        for text in (album.genre, album.tracks_line(), fmt_time(album.duration) if album.duration else ""):
            if text:
                p.drawText(QRect(x, y, width, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width))
            y += 19
        p.restore()


def thumbnail(path: str, size: int):  # type: ignore[no-untyped-def]
    """A song's embedded cover scaled to size, as a QImage (safe in a worker thread).
    Kept in %LOCALAPPDATA%\\MusicManager\\covers so the album view is quick next time."""
    import hashlib

    from PySide6.QtGui import QImage

    from ..core import tags
    from ..core.settings import cache_dir

    try:
        st = os.stat(path)
    except OSError:
        return None
    name = hashlib.sha1(f"{os.path.normcase(path)}|{st.st_mtime}|{st.st_size}|{size}".encode()).hexdigest()
    cached = cache_dir() / "covers" / f"{name}.jpg"
    if cached.exists():
        image = QImage(str(cached))
        if not image.isNull():
            return image
    data = tags.read(path, with_details=True).cover
    if not data:
        return None
    image = QImage.fromData(data)
    if image.isNull():
        return None
    if image.width() > size or image.height() > size:
        image = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        image.convertToFormat(QImage.Format.Format_RGB32).save(str(cached), "JPG", 90)
    except OSError:
        pass
    return image
