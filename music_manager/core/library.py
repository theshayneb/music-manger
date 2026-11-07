"""Find the songs in a folder and read their tags, with a cache so the Music folder
(thousands of songs) opens in a moment after the first time."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from . import tags
from .settings import cache_dir

CACHE_VERSION = 7  # ... 6: sort artist, 7: original artist/album/year, Obsidian tags


def list_songs(folder: str, subfolders: bool) -> list[str]:
    if not folder or not os.path.isdir(folder):
        return []
    found: list[str] = []
    if subfolders:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            found += [os.path.join(root, f) for f in files if tags.is_audio(f) and ".converting." not in f]
    else:
        with os.scandir(folder) as it:
            found = [e.path for e in it if e.is_file() and tags.is_audio(e.name) and ".converting." not in e.name]
    return sorted(found, key=lambda p: p.lower())


class TagCache:
    """{path: Track} saved in %LOCALAPPDATA%\\MusicManager\\tags.json, checked by size + modified time."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or cache_dir() / "tags.json"
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}
        self._dirty = False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") == CACHE_VERSION:
                self._items = data.get("items", {})
        except (OSError, ValueError, AttributeError):
            pass

    def get(self, path: str) -> tags.Track:
        key = os.path.normcase(path)
        try:
            st = os.stat(path)
        except OSError:
            return tags.read_track(path)
        with self._lock:
            hit = self._items.get(key)
        if hit and hit.get("size") == st.st_size and hit.get("mtime") == st.st_mtime and hit.get("path") == path:
            track = tags.Track.from_dict(hit)
            track.lyrics_file = os.path.exists(os.path.splitext(path)[0] + ".txt")
            return track
        track = tags.read_track(path)
        self.put(track)
        return track

    def put(self, track: tags.Track) -> None:
        with self._lock:
            self._items[os.path.normcase(track.path)] = track.to_dict()
            self._dirty = True

    def forget(self, path: str) -> None:
        with self._lock:
            if self._items.pop(os.path.normcase(path), None) is not None:
                self._dirty = True

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            # Keep only songs that still exist, so the cache doesn't grow forever.
            items = {k: v for k, v in self._items.items() if os.path.exists(v.get("path", ""))}
            self._items = items
            self._dirty = False
            data = json.dumps({"version": CACHE_VERSION, "items": items}, ensure_ascii=False)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass
