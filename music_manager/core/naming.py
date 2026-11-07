"""Rename songs from their tags, e.g. "{artist} - {title}" -> "Adele - Hello.mp3".

Placeholders: {artist} {title} {album} {albumartist} {year} {genre} {track} {disc}
and {name} (the current name without extension). {track:02} pads with zeros (03).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .fileops import safe_name, same_path, unique_path
from .tags import Track

PLACEHOLDER = re.compile(r"\{(\w+)(?::(\d+))?\}")

FIELDS = {
    "artist": "artist", "title": "title", "album": "album", "albumartist": "album_artist",
    "year": "year", "genre": "genre", "track": "track", "disc": "disc",
}

HELP = ("Placeholders: {artist} {title} {album} {albumartist} {year} {genre} {track} {disc} {name}. "
        "{track:02} adds a leading zero (03).")


def unknown_placeholders(pattern: str) -> list[str]:
    return [m.group(1) for m in PLACEHOLDER.finditer(pattern) if m.group(1) not in FIELDS and m.group(1) != "name"]


def format_name(pattern: str, track: Track) -> tuple[str, str | None]:
    """(new base name, problem). Problem is e.g. "no Title" when a used tag is empty."""
    missing: list[str] = []

    def sub(m: re.Match) -> str:
        key, width = m.group(1), m.group(2)
        if key == "name":
            return Path(track.path).stem
        attr = FIELDS.get(key)
        if attr is None:
            return m.group(0)
        value = str(getattr(track, attr) or "").strip()
        if key == "genre":
            value = value.split(";")[0].strip()
        if not value:
            missing.append(key)
            return ""
        if width and value.isdigit():
            value = value.zfill(int(width))
        return safe_name(value)

    name = PLACEHOLDER.sub(sub, pattern)
    if missing:
        labels = {"albumartist": "Album Artist"}
        return "", "no " + ", ".join(labels.get(k, k.title()) for k in missing)
    name = safe_name(name)
    if not name:
        return "", "the name would be empty"
    return name, None


def plan(tracks: list[Track], pattern: str, folder: str | None = None,
         ext: str | None = None) -> list[tuple[Track, str | None, str]]:
    """For each track: (track, new path or None, note). New names never clash with each
    other or with existing files (_1, _2 ... is added). folder: move there as well.
    ext: the extension the file will have by then (".mp3" for a FLAC that gets converted)."""
    out: list[tuple[Track, str | None, str]] = []
    taken: set[str] = set()
    for track in tracks:
        src = Path(track.path)
        base, problem = format_name(pattern, track)
        if problem:
            base = src.stem
        target_dir = Path(folder) if folder else src.parent
        target = target_dir / (base + (ext or src.suffix.lower()))
        dest = unique_path(target, taken, ignore=str(src))
        taken.add(os.path.normcase(str(dest)))
        if same_path(dest, src) and dest.name == src.name:  # a change of case only is still a rename
            out.append((track, None, problem or "already named like this"))
            continue
        note = f"kept its name ({problem})" if problem else ""
        out.append((track, str(dest), note))
    return out
