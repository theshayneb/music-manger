"""Look songs up online: tags, album art and lyrics. No accounts or API keys needed.

Tags and art: iTunes Search, MusicBrainz (+ Cover Art Archive) and Deezer.
Lyrics: LRCLIB, then Genius and lyrics.ovh (the same kind of sites Lyrics Reloaded uses).

Every function here is plain Python (no Qt) and safe to call from a worker thread.
The parse_* functions turn a site's JSON into Candidates and are tested offline.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .. import __version__

USER_AGENT = f"MusicManager/{__version__} ( https://github.com/theshayneb/music-manger )"
TIMEOUT = 12
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class OnlineError(Exception):
    pass


# ================================================================ data

@dataclass
class Cover:
    source: str
    url: str            # full size
    thumb: str = ""     # small version for the picker (falls back to url)
    width: int = 0      # 0 = unknown until downloaded
    height: int = 0
    note: str = ""      # e.g. the album it belongs to


@dataclass
class Candidate:
    source: str
    title: str = ""
    artist: str = ""
    album_artist: str = ""
    album: str = ""
    year: str = ""
    genre: str = ""
    track: str = ""
    track_total: str = ""
    disc: str = ""
    disc_total: str = ""
    edition: str = ""
    length: float = 0.0
    detail: str = ""     # extra info shown in the list, e.g. "Compilation · GB"
    url: str = ""        # the page on the site
    covers: list[Cover] = field(default_factory=list)
    score: float = 0.0

    def fields(self) -> dict[str, str]:
        keys = ("title", "artist", "album_artist", "album", "year", "genre",
                "track", "track_total", "disc", "disc_total", "edition")
        return {k: getattr(self, k) for k in keys}


@dataclass
class Lyrics:
    source: str
    text: str
    url: str = ""
    synced: bool = False


# ================================================================ http

def _request(url: str, params: dict | None = None, accept: str = "application/json",
             timeout: float = TIMEOUT, limit: int = 5 * 1024 * 1024) -> bytes:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https hosts
            data = resp.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return b""
        raise OnlineError(f"{urllib.parse.urlsplit(url).hostname} answered {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise OnlineError(f"couldn't reach {urllib.parse.urlsplit(url).hostname}: {reason}") from exc
    if len(data) > limit:
        raise OnlineError("the answer was too big")
    return data


def get_json(url: str, params: dict | None = None) -> dict | list:
    data = _request(url, params)
    if not data:
        return {}
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise OnlineError(f"{urllib.parse.urlsplit(url).hostname} sent something unexpected") from exc


def get_image(url: str) -> bytes:
    return _request(url, accept="image/*", timeout=30, limit=MAX_IMAGE_BYTES)


_mb_lock = threading.Lock()
_mb_last = 0.0


def _musicbrainz_json(path: str, params: dict) -> dict:
    """MusicBrainz allows one request per second per app."""
    global _mb_last
    with _mb_lock:
        wait = 1.1 - (time.monotonic() - _mb_last)
        if wait > 0:
            time.sleep(wait)
        try:
            return get_json(f"https://musicbrainz.org/ws/2/{path}", {**params, "fmt": "json"})  # type: ignore[return-value]
        finally:
            _mb_last = time.monotonic()


# ================================================================ matching

def normalize(text: str) -> str:
    """For comparing: "Beyoncé ft. JAY-Z" -> "beyonce", "Halo (Remastered)" -> "halo"."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"\s*[\(\[].*?[\)\]]", " ", text)
    text = re.split(r"\s+(?:feat\.?|ft\.?|featuring|with)\s+", text)[0]
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def similarity(a: str, b: str) -> float:
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def score(c: Candidate, artist: str, title: str, length: float = 0) -> float:
    s = 60 * similarity(c.title, title) + 35 * similarity(c.artist, artist)
    if length and c.length:
        diff = abs(length - c.length)
        s += 10 if diff <= 3 else (4 if diff <= 10 else -10)
    if "compilation" in c.detail.lower() or c.album_artist.lower() == "various artists":
        s -= 8  # the original album is nearly always the one wanted
    unwanted = r"\b(live|karaoke|instrumental|remix|tribute|bootleg|broadcast|demo)\b"
    if re.search(unwanted, f"{c.title} {c.album} {c.detail} {c.album_artist}".lower()) and \
            not re.search(unwanted, title.lower()):
        s -= 15  # e.g. MusicBrainz's "Album + Live · Bootleg" concert recordings
    if re.match(r"\d{4}-\d{2}-\d{2}", c.album):
        s -= 10  # concert recordings named by date
    if not c.year:
        s -= 4
    return round(s, 1)


def guess_from_filename(name: str) -> tuple[str, str]:
    """"Beyoncé - Halo.mp3" -> ("Beyoncé", "Halo"); "03 - Halo.flac" -> ("", "Halo")."""
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name)
    stem = re.sub(r"[_]+", " ", stem).strip()
    parts = [p.strip() for p in re.split(r"\s+-\s+", stem) if p.strip()]
    parts = [p for p in parts if not re.fullmatch(r"\d{1,3}", p)]
    if len(parts) >= 2:
        return parts[0], parts[-1]
    title = re.sub(r"^\d{1,3}[.\s]+", "", parts[0]) if parts else stem
    return "", title


EDITION_WORDS = re.compile(
    r"[\(\[]\s*([^\)\]]*\b(?:deluxe|edition|version|remaster(?:ed)?|expanded|anniversary|bonus|special)\b[^\)\]]*)"
    r"\s*[\)\]]", re.IGNORECASE)


def split_edition(album: str) -> tuple[str, str]:
    """"I Am... Sasha Fierce (Deluxe Edition)" -> ("I Am... Sasha Fierce", "Deluxe Edition")."""
    m = EDITION_WORDS.search(album or "")
    if not m:
        return album, ""
    base = (album[:m.start()] + album[m.end():]).strip(" -")
    return " ".join(base.split()), m.group(1).strip()


def _year(date: str) -> str:
    m = re.match(r"(\d{4})", date or "")
    return m.group(1) if m else ""


def _num(value) -> str:  # type: ignore[no-untyped-def]
    try:
        n = int(value)
    except (TypeError, ValueError):
        return ""
    return str(n) if n > 0 else ""


# ================================================================ iTunes

def parse_itunes(data: dict) -> list[Candidate]:
    out = []
    for r in data.get("results", []) if isinstance(data, dict) else []:
        if r.get("wrapperType") != "track" or r.get("kind", "song") != "song":
            continue
        album, edition = split_edition(r.get("collectionName", ""))
        c = Candidate(
            source="iTunes",
            title=r.get("trackName", ""),
            artist=r.get("artistName", ""),
            album_artist=r.get("collectionArtistName") or r.get("artistName", ""),
            album=album, edition=edition,
            year=_year(r.get("releaseDate", "")),
            genre=r.get("primaryGenreName", ""),
            track=_num(r.get("trackNumber")), track_total=_num(r.get("trackCount")),
            disc=_num(r.get("discNumber")), disc_total=_num(r.get("discCount")),
            length=(r.get("trackTimeMillis") or 0) / 1000,
            url=r.get("trackViewUrl", ""),
        )
        art = r.get("artworkUrl100", "")
        if art:
            c.covers.append(Cover("iTunes", re.sub(r"/\d+x\d+bb\.", "/3000x3000bb.", art),
                                  re.sub(r"/\d+x\d+bb\.", "/400x400bb.", art), note=r.get("collectionName", "")))
        out.append(c)
    return out


def search_itunes(artist: str, title: str) -> list[Candidate]:
    data = get_json("https://itunes.apple.com/search",
                    {"term": f"{artist} {title}".strip(), "entity": "song", "limit": 15, "country": "US"})
    return parse_itunes(data)  # type: ignore[arg-type]


# ================================================================ MusicBrainz

def _credit(credits: list | None) -> str:
    if not credits:
        return ""
    return "".join(f"{c.get('name') or c.get('artist', {}).get('name', '')}{c.get('joinphrase', '')}"
                   for c in credits).strip()


def parse_musicbrainz(data: dict) -> list[Candidate]:
    out = []
    for rec in data.get("recordings", []) if isinstance(data, dict) else []:
        artist = _credit(rec.get("artist-credit"))
        tags = sorted(rec.get("tags") or [], key=lambda t: -int(t.get("count", 0)))
        genre = tags[0]["name"].title() if tags else ""
        for rel in (rec.get("releases") or [])[:6]:
            group = rel.get("release-group") or {}
            types = [group.get("primary-type") or "", *(group.get("secondary-types") or [])]
            album, edition = split_edition(rel.get("title", ""))
            edition = edition or (rel.get("disambiguation") or "")
            media = rel.get("media") or [{}]
            medium = media[0]
            tracks = medium.get("track") or [{}]
            c = Candidate(
                source="MusicBrainz",
                title=rec.get("title", ""), artist=artist,
                album_artist=_credit(rel.get("artist-credit")) or artist,
                album=album, edition=edition,
                year=_year(rel.get("date") or rec.get("first-release-date", "")),
                genre=genre,
                track=_num(tracks[0].get("number")), track_total=_num(medium.get("track-count")),
                disc=_num(medium.get("position")),
                length=(rec.get("length") or 0) / 1000,
                detail=" · ".join(x for x in (" + ".join(t for t in types if t), rel.get("country", ""),
                                              rel.get("status", "") if rel.get("status") != "Official" else "")
                                  if x),
                url=f"https://musicbrainz.org/release/{rel.get('id', '')}",
            )
            if c.disc == "1":
                c.disc = ""  # MusicBrainz search results don't say how many discs there are
            if rel.get("id"):
                base = f"https://coverartarchive.org/release/{rel['id']}/front"
                c.covers.append(Cover("Cover Art Archive", base, base + "-500", note=rel.get("title", "")))
            out.append(c)
    return out


def search_musicbrainz(artist: str, title: str) -> list[Candidate]:
    def q(s: str) -> str:
        return '"' + s.replace("\\", "").replace('"', "") + '"'

    query = f"recording:{q(title)}" + (f" AND artist:{q(artist)}" if artist else "")
    data = _musicbrainz_json("recording", {"query": query, "limit": 8})
    return parse_musicbrainz(data)


# ================================================================ Deezer

def parse_deezer_track(track: dict, album: dict | None = None) -> Candidate:
    alb = album or track.get("album") or {}
    name, edition = split_edition(alb.get("title", ""))
    genres = ((album or {}).get("genres") or {}).get("data") or []
    artist = (track.get("artist") or {}).get("name", "")
    c = Candidate(
        source="Deezer",
        title=track.get("title", ""),
        artist=artist,
        album_artist=((album or {}).get("artist") or {}).get("name", "") or artist,
        album=name, edition=edition,
        year=_year(track.get("release_date") or (album or {}).get("release_date", "")),
        genre=genres[0]["name"] if genres else "",
        track=_num(track.get("track_position")), track_total=_num((album or {}).get("nb_tracks")),
        disc=_num(track.get("disk_number")),
        length=float(track.get("duration") or 0),
        url=track.get("link", ""),
    )
    for source in (alb, track.get("album") or {}):
        cover = source.get("cover_xl") or source.get("cover_big")
        if cover:
            side = 1000 if source.get("cover_xl") else 0
            c.covers.append(Cover("Deezer", cover, source.get("cover_medium") or cover, side, side,
                                  note=alb.get("title", "")))
            break
    return c


def search_deezer(artist: str, title: str) -> list[Candidate]:
    query = f'artist:"{artist}" track:"{title}"' if artist else title
    data = get_json("https://api.deezer.com/search", {"q": query, "limit": 6})
    hits = data.get("data", []) if isinstance(data, dict) else []
    if not hits and artist:
        data = get_json("https://api.deezer.com/search", {"q": f"{artist} {title}", "limit": 6})
        hits = data.get("data", []) if isinstance(data, dict) else []
    out = []
    for hit in hits[:5]:
        try:  # track number, disc and date are only in the full track; genre only in the album
            track = get_json(f"https://api.deezer.com/track/{int(hit['id'])}") or hit
            album_id = (track.get("album") or {}).get("id")  # type: ignore[union-attr]
            album = get_json(f"https://api.deezer.com/album/{int(album_id)}") if album_id else None
        except (OnlineError, KeyError, TypeError, ValueError):
            track, album = hit, None
        out.append(parse_deezer_track(track, album))  # type: ignore[arg-type]
    return out


# ================================================================ everything at once

SOURCES = {"iTunes": search_itunes, "MusicBrainz": search_musicbrainz, "Deezer": search_deezer}


def search(artist: str, title: str, length: float = 0) -> tuple[list[Candidate], list[str]]:
    """All sources at once. Returns (candidates best first, [problems])."""
    if not title.strip():
        return [], ["type a title to search for"]
    results: list[Candidate] = []
    problems: list[str] = []
    with ThreadPoolExecutor(len(SOURCES)) as pool:
        futures = {name: pool.submit(fn, artist, title) for name, fn in SOURCES.items()}
        for name, future in futures.items():
            try:
                results += future.result()
            except Exception as exc:  # noqa: BLE001 - one site failing mustn't stop the others
                problems.append(f"{name}: {exc}")
    for c in results:
        c.score = score(c, artist, title, length)
    results.sort(key=lambda c: -c.score)
    # Same album + track from two sites: keep the better one, but keep both covers.
    seen: dict[tuple, Candidate] = {}
    unique = []
    for c in results:
        key = (c.source, normalize(c.album), c.year, c.track, normalize(c.title))
        if key in seen:
            continue
        seen[key] = c
        unique.append(c)
    return unique, problems


def covers_for(candidates: list[Candidate], limit: int = 12) -> list[Cover]:
    """Different covers from the best candidates (best first)."""
    out, seen = [], set()
    for c in candidates:
        for cover in c.covers:
            if cover.url not in seen:
                seen.add(cover.url)
                out.append(cover)
    return out[:limit]


# ================================================================ lyrics

_TIMESTAMP = re.compile(r"^\s*\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]\s*", re.MULTILINE)


def strip_timestamps(lrc: str) -> str:
    lines = [_TIMESTAMP.sub("", line) for line in lrc.splitlines()]
    lines = [line for line in lines if not re.match(r"^\s*\[[a-z]+:.*\]\s*$", line)]  # [ar:...] headers
    return "\n".join(lines).strip()


def lyrics_lrclib(artist: str, title: str, album: str = "", length: float = 0) -> Lyrics | None:
    params = {"artist_name": artist, "track_name": title}
    if album:
        params["album_name"] = album
    if length:
        params["duration"] = str(int(round(length)))
    data = get_json("https://lrclib.net/api/get", params) if artist else {}
    if not data or not isinstance(data, dict) or not (data.get("plainLyrics") or data.get("syncedLyrics")):
        found = get_json("https://lrclib.net/api/search", {"track_name": title, "artist_name": artist}
                         if artist else {"q": title})
        found = [f for f in found if isinstance(f, dict) and (f.get("plainLyrics") or f.get("syncedLyrics"))] \
            if isinstance(found, list) else []
        if not found:
            return None
        found.sort(key=lambda f: -(similarity(f.get("trackName", ""), title) * 2
                                   + similarity(f.get("artistName", ""), artist)))
        data = found[0]
        if similarity(data.get("trackName", ""), title) < 0.6:
            return None
    if data.get("instrumental"):
        return None
    text = data.get("plainLyrics") or strip_timestamps(data.get("syncedLyrics") or "")
    return Lyrics("LRCLIB", text.strip(), f"https://lrclib.net/api/get/{data.get('id', '')}") if text.strip() else None


def parse_genius_page(page: str) -> str:
    """The lyrics from a Genius song page (the parts marked data-lyrics-container)."""
    parts = []
    for m in re.finditer(r'<div[^>]*data-lyrics-container="true"[^>]*>', page):
        start, depth, i = m.end(), 1, m.end()
        for tag in re.finditer(r"<(/?)div\b[^>]*>", page[start:]):
            depth += -1 if tag.group(1) else 1
            if depth == 0:
                i = start + tag.start()
                break
        else:
            i = len(page)
        chunk = page[start:i]
        # Bits Genius puts inside the container that aren't lyrics (header, "Read more").
        chunk = re.sub(r'<div[^>]*data-exclude-from-selection="true"[^>]*>.*?</div>\s*', "", chunk, flags=re.S)
        chunk = re.sub(r"<br\s*/?>", "\n", chunk)
        chunk = re.sub(r"<[^>]+>", "", chunk)
        parts.append(html.unescape(chunk))
    text = "\n".join(parts)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    text = re.sub(r"^\d+\s+Contributors?.*?Lyrics\s*", "", text, flags=re.S)
    return text


def lyrics_genius(artist: str, title: str) -> Lyrics | None:
    data = get_json("https://genius.com/api/search/multi", {"q": f"{artist} {title}".strip()})
    hits = []
    for section in ((data.get("response") or {}).get("sections") or []) if isinstance(data, dict) else []:
        if section.get("type") in ("song", "top_hit"):
            hits += [h.get("result") or {} for h in section.get("hits", []) if h.get("type") == "song"]
    best = None
    for hit in hits:
        hit_artist = (hit.get("primary_artist") or {}).get("name", "")
        s = similarity(hit.get("title", ""), title) * 2 + similarity(hit_artist, artist)
        if s >= 2.2 and (best is None or s > best[0]):
            best = (s, hit)
    if not best or not best[1].get("url", "").startswith("https://genius.com/"):
        return None
    url = best[1]["url"]
    page = _request(url, accept="text/html", limit=8 * 1024 * 1024).decode("utf-8", errors="replace")
    text = parse_genius_page(page)
    return Lyrics("Genius", text, url) if text else None


def lyrics_ovh(artist: str, title: str) -> Lyrics | None:
    if not artist:
        return None
    url = "https://api.lyrics.ovh/v1/" + urllib.parse.quote(artist, safe="") + "/" + urllib.parse.quote(title, safe="")
    data = get_json(url)
    text = (data.get("lyrics") or "").replace("\r\n", "\n").strip() if isinstance(data, dict) else ""
    text = re.sub(r"^Paroles de la chanson .*? par .*?\n", "", text)
    return Lyrics("lyrics.ovh", text) if text else None


def find_lyrics(artist: str, title: str, album: str = "", length: float = 0) -> tuple[list[Lyrics], list[str]]:
    jobs = {
        "LRCLIB": lambda: lyrics_lrclib(artist, title, album, length),
        "Genius": lambda: lyrics_genius(artist, title),
        "lyrics.ovh": lambda: lyrics_ovh(artist, title),
    }
    found: list[Lyrics] = []
    problems: list[str] = []
    with ThreadPoolExecutor(len(jobs)) as pool:
        futures = {name: pool.submit(fn) for name, fn in jobs.items()}
        for name, future in futures.items():
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{name}: {exc}")
                continue
            if result:
                found.append(result)
    return found, problems


LYRICS_PREFERENCE = ["LRCLIB", "lyrics.ovh", "Genius"]


def best_lyrics(artist: str, title: str, album: str = "", length: float = 0) -> Lyrics | None:
    """The lyrics to use without asking (for many songs at once): LRCLIB first (matched by artist,
    title and length), then lyrics.ovh, then Genius."""
    found, problems = find_lyrics(artist, title, album, length)
    if not found and problems and len(problems) == 3:
        raise OnlineError("; ".join(problems))
    found.sort(key=lambda lyr: LYRICS_PREFERENCE.index(lyr.source) if lyr.source in LYRICS_PREFERENCE else 9)
    return found[0] if found else None
