"""Group songs into albums for the album view (like MusicBee's "album and tracks" view).

Sort order: Sort Artist / Year / Album / Disc-Track# / Title.
Inside an album, songs are split by "album side": edition and disc, e.g.
"Disc 01", "Deluxe Edition • Disc 01", "Disc 02", "iTunes Version".
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .tags import Track

GROUPINGS = {"artist": "Artist", "genre": "Genre", "year": "Year", "none": "Nothing"}
ALBUM_ORDERS = {"year": "Year", "title": "Album title"}


def _int(value: str) -> int:
    m = re.search(r"\d+", value or "")
    return int(m.group(0)) if m else 0


def album_artist(t: Track) -> str:
    return (t.album_artist or t.artist or "").strip()


def artist_sort_key(name: str) -> str:
    """"The Vaccines" sorts as "vaccines" (like MusicBee); accents don't matter."""
    import unicodedata

    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold().strip()
    return re.sub(r"^(the|a|an)\s+", "", text)


@dataclass
class Group:
    heading: str            # "" = no heading
    tracks: list[Track]


@dataclass
class Album:
    key: tuple
    artist: str
    title: str
    year: str = ""
    genre: str = ""
    rating: float = 0.0     # average of the rated songs, to the nearest half star
    have: int = 0
    total: int = 0
    duration: float = 0.0
    cover_path: str = ""    # a song of this album that has embedded art
    groups: list[Group] = field(default_factory=list)

    @property
    def tracks(self) -> list[Track]:
        return [t for g in self.groups for t in g.tracks]

    def title_line(self) -> str:
        name = self.title or "(no album)"
        return f"{name} ({self.year})" if self.year else name

    def tracks_line(self) -> str:
        return f"{self.have:02d} / {self.total:02d}" if self.total else f"{self.have:02d}"


@dataclass
class Section:
    heading: str            # "" when not grouping
    albums: list[Album]


def _rating(tracks: list[Track]) -> float:
    rated = [t.rating for t in tracks if t.rating]
    if not rated:
        return 0.0
    return math.floor(sum(rated) / len(rated) * 2 + 0.5) / 2


def _side(t: Track, multi_disc: bool) -> str:
    parts = [t.edition.strip()] if t.edition.strip() else []
    if multi_disc and _int(t.disc):
        parts.append(f"Disc {_int(t.disc):02d}")
    return " • ".join(parts)


def make_album(key: tuple, tracks: list[Track], sides: bool = True) -> Album:
    tracks = sorted(tracks, key=lambda t: (_int(t.disc), _int(t.track), t.title.casefold(), t.name.casefold()))
    discs = {_int(t.disc) for t in tracks if _int(t.disc)}
    multi_disc = len(discs) > 1 or any(_int(t.disc_total) > 1 for t in tracks)
    groups: list[Group] = []
    for t in tracks:
        heading = _side(t, multi_disc) if sides else ""
        if groups and groups[-1].heading == heading:
            groups[-1].tracks.append(t)
        else:
            groups.append(Group(heading, [t]))
    years = Counter(t.year for t in tracks if t.year)
    genres = Counter(t.genre for t in tracks if t.genre)
    return Album(
        key=key,
        artist=Counter(album_artist(t) for t in tracks).most_common(1)[0][0],
        title=Counter(t.album for t in tracks).most_common(1)[0][0],
        year=min(years) if years else "",
        genre=genres.most_common(1)[0][0] if genres else "",
        rating=_rating(tracks),
        have=len(tracks),
        total=max((_int(t.track_total) for t in tracks), default=0),
        duration=sum(t.length for t in tracks),
        cover_path=next((t.path for t in tracks if t.has_cover), ""),
        groups=groups,
    )


def build(tracks: list[Track], group_by: str = "artist", order: str = "year", sides: bool = True) -> list[Section]:
    by_album: dict[tuple, list[Track]] = {}
    for t in tracks:
        artist = album_artist(t)
        sort = artist_sort_key(t.sort_artist or artist)
        key = (sort, t.album.strip().casefold())
        by_album.setdefault(key, []).append(t)
    albums = [make_album(key, ts, sides) for key, ts in by_album.items()]

    def album_order(a: Album):  # type: ignore[no-untyped-def]
        within = (a.year or "9999", a.title.casefold()) if order == "year" else (a.title.casefold(), a.year)
        return (a.key[0], *within)

    albums.sort(key=album_order)
    if group_by == "none":
        return [Section("", albums)]
    sections: dict[str, list[Album]] = {}
    for a in albums:
        if group_by == "genre":
            heading = a.genre or "(no genre)"
        elif group_by == "year":
            heading = a.year or "(no year)"
        else:
            heading = a.artist or "(no artist)"
        sections.setdefault(heading, []).append(a)
    if group_by == "artist":
        keys = sorted(sections, key=lambda h: (artist_sort_key(sections[h][0].key[0]), h.casefold()))
    elif group_by == "year":
        keys = sorted(sections, reverse=True)
    else:
        keys = sorted(sections, key=str.casefold)
    if group_by in ("genre", "year"):  # inside a genre/year: by artist, then year
        for h in keys:
            sections[h].sort(key=album_order)
    return [Section(h, sections[h]) for h in keys]
