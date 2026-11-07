"""Plex: play counts for the Plays column, and asking Plex to rescan the Music folder.

Works like the Media Manager Obsidian plugin: the server's address (e.g.
http://localhost:32400) and your X-Plex-Token. Songs are matched to files by the
path Plex has for them (Plex runs on this PC), else by file name.
Nothing is ever changed in Plex except starting a scan of a folder.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass

TIMEOUT = 20
PAGE = 1000


class PlexError(Exception):
    pass


def normalize_url(url: str) -> str:
    out = (url or "").strip().rstrip("/")
    if out and "://" not in out:
        out = "http://" + out
    return out


@dataclass
class PlexTrack:
    plays: int
    rating: float  # Plex's 0-10 (two per star), 0 = none
    key: str
    file: str


class Plex:
    def __init__(self, url: str, token: str) -> None:
        self.url = normalize_url(url)
        self.token = token.strip()
        # The server is on this PC or the home network: never through a system proxy.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(self, path: str, params: dict | None = None) -> dict:
        if not self.url or not self.token:
            raise PlexError("enter the Plex server's address and token in Settings")
        query = "?" + urllib.parse.urlencode(params) if params else ""
        req = urllib.request.Request(self.url + path + query,
                                     headers={"Accept": "application/json", "X-Plex-Token": self.token})
        try:
            with self._opener.open(req, timeout=TIMEOUT) as resp:
                data = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise PlexError("Plex didn't accept the token") from exc
            raise PlexError(f"Plex answered {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PlexError(f"couldn't reach Plex at {self.url} ({getattr(exc, 'reason', exc)})") from exc
        if not data:
            return {}
        try:
            return json.loads(data.decode("utf-8")).get("MediaContainer", {})
        except ValueError as exc:
            raise PlexError("Plex sent something unexpected") from exc

    def music_sections(self) -> list[dict]:
        return [d for d in self.get("/library/sections").get("Directory", []) if d.get("type") == "artist"]

    def tracks(self, section_key: str) -> list[dict]:
        out: list[dict] = []
        start = 0
        while True:
            page = self.get(f"/library/sections/{urllib.parse.quote(str(section_key))}/all",
                            {"type": 10, "X-Plex-Container-Start": start, "X-Plex-Container-Size": PAGE})
            batch = page.get("Metadata", [])
            out += batch
            total = int(page.get("totalSize") or 0)
            if len(batch) < PAGE or (total and len(out) >= total):
                return out
            start += PAGE

    def all_tracks(self) -> list[PlexTrack]:
        return [t for s in self.music_sections() for t in parse_tracks(self.tracks(s["key"]))]

    def refresh_folder(self, folder: str) -> str:
        """Ask Plex to scan just this folder. Returns the library's name."""
        sections = self.music_sections()
        for s in sections:
            for loc in s.get("Location", []):
                path = loc.get("path", "")
                if path and _inside(folder, path):
                    self.get(f"/library/sections/{s['key']}/refresh", {"path": folder})
                    return s.get("title", "")
        if len(sections) == 1:  # paths look different from here (another drive letter): scan that library
            self.get(f"/library/sections/{sections[0]['key']}/refresh")
            return sections[0].get("title", "")
        raise PlexError("no Plex music library contains that folder")


def _inside(path: str, folder: str) -> bool:
    a = os.path.normcase(os.path.normpath(path)).replace("\\", "/").rstrip("/")
    b = os.path.normcase(os.path.normpath(folder)).replace("\\", "/").rstrip("/")
    return a == b or a.startswith(b + "/")


def parse_tracks(metadata: list[dict]) -> list[PlexTrack]:
    out = []
    for m in metadata:
        try:
            file = m["Media"][0]["Part"][0]["file"]
        except (KeyError, IndexError, TypeError):
            continue
        out.append(PlexTrack(int(m.get("viewCount") or 0), float(m.get("userRating") or 0),
                             str(m.get("ratingKey", "")), file))
    return out


def by_file(tracks: list[PlexTrack]) -> tuple[dict[str, PlexTrack], dict[str, PlexTrack]]:
    """({full path: track}, {file name: track} for names only one track has), both case-insensitive."""
    paths = {os.path.normcase(os.path.normpath(t.file.replace("/", os.sep))): t for t in tracks}
    names: dict[str, PlexTrack | None] = {}
    for t in tracks:
        name = os.path.basename(t.file.replace("\\", "/")).casefold()
        names[name] = None if name in names else t
    return paths, {k: v for k, v in names.items() if v is not None}


def match(path: str, paths: dict[str, PlexTrack], names: dict[str, PlexTrack]) -> PlexTrack | None:
    return paths.get(os.path.normcase(os.path.normpath(path))) or names.get(os.path.basename(path).casefold())
