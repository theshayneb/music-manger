"""Read and write song tags with mutagen.

MP3 is the format that matters (everything kept ends up as MP3), so MP3 tags are
written as ID3v2.3, which Plex, Symfonium, MusicBee, Windows and nearly every
other player read. FLAC is also writable, so a song can be tagged while it's
still being reviewed; the tags are carried over when it's converted.

Lyrics are written both INTO the file (ID3 USLT / FLAC LYRICS) and to a
"<song name>.txt" file next to it, because Plex only reads the .txt file.
Ratings (0-5 stars) are written into the file too (ID3 POPM, as Windows Media
Player / MusicBee / Mp3tag write it), even though Plex keeps its own ratings.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    POPM,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TLAN,
    TPE1,
    TPE2,
    TPOS,
    TRCK,
    TXXX,
    USLT,
    ID3NoHeaderError,
)
from mutagen.mp3 import MP3, BitrateMode

from . import sidecar

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma", ".aiff", ".aif"}
WRITABLE = {".mp3", ".flac"}

# Text fields shown and edited in the app.
TEXT_FIELDS = ["title", "artist", "album_artist", "album", "year", "genre",
               "track", "track_total", "disc", "disc_total", "language", "tempo"]

FIELD_LABELS = {
    "title": "Title", "artist": "Artist", "album_artist": "Album Artist", "album": "Album",
    "year": "Year", "genre": "Genre", "track": "Track #", "track_total": "of",
    "disc": "Disc #", "disc_total": "of", "lyrics": "Lyrics", "rating": "Rating",
    "language": "Language", "tempo": "Tempo",
}

POPM_EMAIL = "Windows Media Player 9 Series"
STARS_TO_POPM = {0: 0, 1: 1, 2: 64, 3: 128, 4: 196, 5: 255}


def is_audio(path: str | Path) -> bool:
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def can_write(path: str | Path) -> bool:
    return Path(path).suffix.lower() in WRITABLE


def popm_to_stars(value: int) -> int:
    if value <= 0:
        return 0
    if value < 32:
        return 1
    if value < 96:
        return 2
    if value < 160:
        return 3
    if value < 224:
        return 4
    return 5


@dataclass
class Track:
    """What the song list shows. Cheap to keep for thousands of songs (no art or lyrics)."""

    path: str
    format: str = ""
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
    language: str = ""      # ID3 TLAN, e.g. "English"
    tempo: str = ""         # MusicBee's Tempo (ID3 TXXX:TEMPO), e.g. "Fast"
    rating: int = 0
    has_cover: bool = False
    lyrics_in_file: bool = False
    lyrics_file: bool = False
    gain: str = ""          # ReplayGain track gain, e.g. "-6.20 dB"
    length: float = 0.0     # seconds
    bitrate: int = 0        # kbps
    bitrate_mode: str = ""  # CBR / VBR / ""
    sample_rate: int = 0
    size: int = 0
    mtime: float = 0.0
    error: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def has_lyrics(self) -> bool:
        return self.lyrics_in_file or self.lyrics_file

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Details:
    """Everything the tag editor needs for one song."""

    track: Track
    lyrics: str = ""
    cover: bytes | None = None
    cover_mime: str = ""
    extra: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- helpers

def _split_number(value: str) -> tuple[str, str]:
    """"3/12" -> ("3", "12")."""
    value = (value or "").strip()
    if "/" in value:
        a, b = value.split("/", 1)
        return a.strip(), b.strip()
    return value, ""


def _join_number(number: str, total: str, pad: bool = False) -> str:
    """("3", "11") -> "3/11"; with pad (track numbers): "03/11", as the existing library has them."""
    number, total = (number or "").strip(), (total or "").strip()
    if not number:
        return ""
    if pad:
        number = pad_track(number)
        total = pad_track(total) if total else ""
    return f"{number}/{total}" if total else number


def pad_track(value: str) -> str:
    return value.zfill(2) if value.isdigit() else value


def _first(values) -> str:  # type: ignore[no-untyped-def]
    if not values:
        return ""
    return str(values[0]).strip()


def _stat(track: Track) -> None:
    try:
        st = os.stat(track.path)
        track.size, track.mtime = st.st_size, st.st_mtime
    except OSError:
        pass


def _info(track: Track, audio) -> None:  # type: ignore[no-untyped-def]
    info = getattr(audio, "info", None)
    if info is None:
        return
    track.length = float(getattr(info, "length", 0) or 0)
    track.bitrate = int(round((getattr(info, "bitrate", 0) or 0) / 1000))
    track.sample_rate = int(getattr(info, "sample_rate", 0) or 0)
    mode = getattr(info, "bitrate_mode", None)
    if mode is not None and isinstance(audio, MP3):
        track.bitrate_mode = "CBR" if mode == BitrateMode.CBR else (
            "VBR" if mode in (BitrateMode.VBR, BitrateMode.ABR) else "")


# ---------------------------------------------------------------- reading

def read(path: str, with_details: bool = False) -> Details:
    track = Track(path=path, format=Path(path).suffix.lower().lstrip(".").upper())
    details = Details(track=track)
    _stat(track)
    track.lyrics_file = sidecar.lyrics_path(path).exists()
    try:
        ext = Path(path).suffix.lower()
        if ext == ".mp3":
            _read_mp3(details)
        elif ext == ".flac":
            _read_flac(details)
        else:
            _read_other(details)
    except Exception as exc:  # noqa: BLE001 - a broken file mustn't stop the list loading
        track.error = str(exc) or exc.__class__.__name__
    if with_details and not details.lyrics and track.lyrics_file:
        details.lyrics = sidecar.read_lyrics(path)
    if not with_details:
        details.lyrics, details.cover = "", None
    return details


def read_track(path: str) -> Track:
    return read(path).track


def _read_mp3(d: Details) -> None:
    t = d.track
    audio = MP3(t.path)
    _info(t, audio)
    tags = audio.tags
    if tags is None:
        return
    t.title = _first(tags.get("TIT2"))
    t.artist = _first(tags.get("TPE1"))
    t.album_artist = _first(tags.get("TPE2"))
    t.album = _first(tags.get("TALB"))
    genre = tags.get("TCON")
    t.genre = "; ".join(genre.genres) if genre else ""
    year = tags.get("TDRC") or tags.get("TYER")
    t.year = _first(year)[:4] if year else ""
    t.track, t.track_total = _split_number(_first(tags.get("TRCK")))
    t.disc, t.disc_total = _split_number(_first(tags.get("TPOS")))
    popms = tags.getall("POPM")
    if popms:
        chosen = next((p for p in popms if p.email == POPM_EMAIL), popms[0])
        t.rating = popm_to_stars(int(chosen.rating))
    t.has_cover = bool(tags.getall("APIC"))
    uslt = tags.getall("USLT")
    t.lyrics_in_file = any(str(u.text).strip() for u in uslt)
    t.language = _first(tags.get("TLAN"))
    for frame in tags.getall("TXXX"):
        desc = frame.desc.upper()
        if desc == "REPLAYGAIN_TRACK_GAIN":
            t.gain = _first(frame.text)
        elif desc == "TEMPO":
            t.tempo = _first(frame.text)
    d.lyrics = next((str(u.text) for u in uslt if str(u.text).strip()), "")
    pics = tags.getall("APIC")
    if pics:
        pic = next((p for p in pics if p.type == 3), pics[0])
        d.cover, d.cover_mime = pic.data, pic.mime


def _vorbis(tags, *keys: str) -> str:  # type: ignore[no-untyped-def]
    for key in keys:
        values = tags.get(key)
        if values:
            return str(values[0]).strip()
    return ""


def _read_flac(d: Details) -> None:
    t = d.track
    audio = FLAC(t.path)
    _info(t, audio)
    tags = audio.tags or {}
    t.title = _vorbis(tags, "title")
    t.language = _vorbis(tags, "language")
    t.tempo = _vorbis(tags, "tempo")
    t.artist = _vorbis(tags, "artist")
    t.album_artist = _vorbis(tags, "albumartist", "album artist", "album_artist")
    t.album = _vorbis(tags, "album")
    t.genre = "; ".join(tags.get("genre", [])) if tags else ""
    t.year = _vorbis(tags, "date", "year")[:4]
    t.track, total = _split_number(_vorbis(tags, "tracknumber"))
    t.track_total = total or _vorbis(tags, "tracktotal", "totaltracks")
    t.disc, total = _split_number(_vorbis(tags, "discnumber"))
    t.disc_total = total or _vorbis(tags, "disctotal", "totaldiscs")
    rating = _vorbis(tags, "rating")
    if rating:
        try:
            value = float(rating)
            t.rating = int(round(value)) if value <= 5 else int(round(value / 20))
        except ValueError:
            pass
    t.gain = _vorbis(tags, "replaygain_track_gain")
    d.lyrics = (tags.get("lyrics") or tags.get("unsyncedlyrics") or [""])[0] if tags else ""
    t.lyrics_in_file = bool(d.lyrics.strip())
    t.has_cover = bool(audio.pictures)
    if audio.pictures:
        pic = next((p for p in audio.pictures if p.type == 3), audio.pictures[0])
        d.cover, d.cover_mime = pic.data, pic.mime


def _read_other(d: Details) -> None:
    t = d.track
    audio = mutagen.File(t.path, easy=True)
    if audio is None:
        return
    _info(t, audio)
    tags = audio.tags or {}

    def get(key: str) -> str:
        try:
            return _first(tags.get(key))
        except (KeyError, ValueError):
            return ""

    t.title, t.artist, t.album = get("title"), get("artist"), get("album")
    t.album_artist, t.genre, t.year = get("albumartist"), get("genre"), get("date")[:4]
    t.track, t.track_total = _split_number(get("tracknumber"))
    t.disc, t.disc_total = _split_number(get("discnumber"))


# ---------------------------------------------------------------- writing

class ReadOnlyFormat(Exception):
    pass


def write(path: str, changes: dict, lyrics_file: bool = True) -> None:
    """Write changes to one file. Keys: any of TEXT_FIELDS (str, "" removes),
    "rating" (0-5), "lyrics" (str, "" removes), "cover" ((bytes, mime) or None to remove),
    "gain" ((gain_db, peak) or None to remove).
    Lyrics also go to (or are removed from) the "<song>.txt" file next to it."""
    ext = Path(path).suffix.lower()
    if ext == ".mp3":
        _write_mp3(path, changes)
    elif ext == ".flac":
        _write_flac(path, changes)
    else:
        raise ReadOnlyFormat(f"{ext.upper().lstrip('.')} files can't be tagged here (convert to MP3 first)")
    if "lyrics" in changes and lyrics_file:
        sidecar.write_lyrics(path, changes["lyrics"] or "")


def _write_mp3(path: str, c: dict) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    def text(frame_id: str, cls, value: str) -> None:  # type: ignore[no-untyped-def]
        tags.delall(frame_id)
        if value:
            tags.add(cls(encoding=1, text=[value]))

    simple = {"title": ("TIT2", TIT2), "artist": ("TPE1", TPE1), "album_artist": ("TPE2", TPE2),
              "album": ("TALB", TALB)}
    for key, (fid, cls) in simple.items():
        if key in c:
            text(fid, cls, c[key].strip())
    if "genre" in c:
        tags.delall("TCON")
        genres = [g.strip() for g in c["genre"].split(";") if g.strip()]
        if genres:  # one "Rock; Pop" string, as MusicBee writes it in ID3v2.3
            tags.add(TCON(encoding=1, text=["; ".join(genres)]))
    if "year" in c:
        tags.delall("TDRC")
        tags.delall("TYER")
        if c["year"].strip():
            tags.add(TDRC(encoding=1, text=[c["year"].strip()]))
    if "track" in c or "track_total" in c:
        old = _split_number(_first(tags.get("TRCK")))
        value = _join_number(c.get("track", old[0]), c.get("track_total", old[1]), pad=True)
        text("TRCK", TRCK, value)
    if "disc" in c or "disc_total" in c:
        old = _split_number(_first(tags.get("TPOS")))
        value = _join_number(c.get("disc", old[0]), c.get("disc_total", old[1]))
        text("TPOS", TPOS, value)
    if "rating" in c:
        stars = max(0, min(5, int(c["rating"])))
        popms = tags.getall("POPM")
        count = max((p.count for p in popms if hasattr(p, "count")), default=0)
        tags.delall("POPM")
        if stars:
            tags.add(POPM(email=POPM_EMAIL, rating=STARS_TO_POPM[stars], count=count))
    if "lyrics" in c:
        tags.delall("USLT")
        if (c["lyrics"] or "").strip():
            tags.add(USLT(encoding=1, lang="eng", desc="", text=c["lyrics"]))
    if "cover" in c:
        tags.delall("APIC")
        if c["cover"]:
            data, mime = c["cover"]
            tags.add(APIC(encoding=1, mime=mime, type=3, desc="", data=data))
    if "language" in c:
        text("TLAN", TLAN, c["language"].strip())
    if "tempo" in c:
        for frame in list(tags.getall("TXXX")):
            if frame.desc.upper() == "TEMPO":
                tags.delall(frame.HashKey)
        if c["tempo"].strip():
            tags.add(TXXX(encoding=1, desc="TEMPO", text=[c["tempo"].strip()]))
    if "gain" in c:
        for frame in list(tags.getall("TXXX")):
            if frame.desc.upper() in ("REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK"):
                tags.delall(frame.HashKey)
        if c["gain"]:
            gain, peak = c["gain"]
            tags.add(TXXX(encoding=1, desc="REPLAYGAIN_TRACK_GAIN", text=[f"{gain:+.2f} dB"]))
            tags.add(TXXX(encoding=1, desc="REPLAYGAIN_TRACK_PEAK", text=[f"{peak:.6f}"]))
    if "comment" in c:
        tags.delall("COMM")
        if c["comment"]:
            tags.add(COMM(encoding=1, lang="eng", desc="", text=[c["comment"]]))
    # mutagen keeps tags as v2.4 in memory; convert v2.4-only frames (e.g. the year, TDRC -> TYER)
    # or the v2.3 file would contain frames that v2.3 readers don't know.
    tags.update_to_v23()
    tags.save(path, v2_version=3, v23_sep="; ")


def _write_flac(path: str, c: dict) -> None:
    audio = FLAC(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    def put(key: str, value: str, *aliases: str) -> None:
        for k in (key, *aliases):
            if k in tags:
                del tags[k]
        if value:
            tags[key] = [value]

    simple = {"title": "title", "artist": "artist", "album_artist": "albumartist", "album": "album",
              "year": "date", "track": "tracknumber", "track_total": "tracktotal",
              "disc": "discnumber", "disc_total": "disctotal", "language": "language", "tempo": "tempo"}
    aliases = {"albumartist": ("album artist", "album_artist"), "tracktotal": ("totaltracks",),
               "disctotal": ("totaldiscs",), "date": ("year",)}
    for key, vorbis in simple.items():
        if key in c:
            value = c[key].strip()
            if key in ("track", "disc"):
                value = _split_number(value)[0]
            put(vorbis, value, *aliases.get(vorbis, ()))
    if "genre" in c:
        put("genre", "")
        genres = [g.strip() for g in c["genre"].split(";") if g.strip()]
        if genres:
            tags["genre"] = genres
    if "rating" in c:
        stars = max(0, min(5, int(c["rating"])))
        put("rating", str(stars * 20) if stars else "")
    if "lyrics" in c:
        put("lyrics", (c["lyrics"] or "") if (c["lyrics"] or "").strip() else "", "unsyncedlyrics")
    if "gain" in c:
        put("replaygain_track_gain", "")
        put("replaygain_track_peak", "")
        if c["gain"]:
            gain, peak = c["gain"]
            tags["replaygain_track_gain"] = [f"{gain:+.2f} dB"]
            tags["replaygain_track_peak"] = [f"{peak:.6f}"]
    if "comment" in c:
        put("comment", c["comment"] or "")
    if "cover" in c:
        audio.clear_pictures()
        if c["cover"]:
            data, mime = c["cover"]
            pic = Picture()
            pic.type, pic.mime, pic.data = 3, mime, data
            audio.add_picture(pic)
    audio.save()


def copy_all(src: str, dest: str) -> None:
    """Copy every tag the app knows (plus art, embedded lyrics, rating and ReplayGain) from src
    to dest. The lyrics FILE isn't touched (the caller decides where it goes)."""
    d = read(src, with_details=True)
    t = d.track
    changes: dict = {key: getattr(t, key) for key in TEXT_FIELDS}
    changes["rating"] = t.rating
    changes["lyrics"] = d.lyrics
    changes["cover"] = (d.cover, d.cover_mime or "image/jpeg") if d.cover else None
    write(dest, changes, lyrics_file=False)


def picture_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"GIF":
        return "image/gif"
    return "image/jpeg"
