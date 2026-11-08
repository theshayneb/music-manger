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
import re
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
    TDOR,
    TDRC,
    TIT2,
    TLAN,
    TOAL,
    TOPE,
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
               "track", "track_total", "disc", "disc_total", "edition", "language", "tempo",
               "original_artist", "original_album", "original_year", "obsidian_tags", "album_type"]

FIELD_LABELS = {
    "title": "Title", "artist": "Artist", "album_artist": "Album Artist", "album": "Album",
    "year": "Year", "genre": "Genre", "track": "Track #", "track_total": "of",
    "disc": "Disc #", "disc_total": "of", "lyrics": "Lyrics", "rating": "Rating",
    "language": "Language", "tempo": "Tempo", "edition": "Edition",
    "original_artist": "Original Artist", "original_album": "Original Album", "original_year": "Original Year",
    "obsidian_tags": "Obsidian tags", "album_type": "Album Type",
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
    sort_artist: str = ""   # ID3 TSO2/TSOP (MusicBee's "Sort Artist"); read only
    album: str = ""
    year: str = ""
    genre: str = ""
    track: str = ""
    track_total: str = ""
    disc: str = ""
    disc_total: str = ""
    edition: str = ""       # e.g. "Deluxe Edition" (ID3 TXXX:TSST, as MusicBee writes it)
    edition_field: str = "" # the MusicBee custom field that also holds the edition, e.g. "Songs-DB_Custom2"
    language: str = ""      # ID3 TLAN, e.g. "English"
    original_artist: str = ""  # ID3 TOPE
    original_album: str = ""   # ID3 TOAL
    original_year: str = ""    # ID3 TORY (v2.4 TDOR)
    album_type: str = ""       # "album;live": TXXX:MusicBrainz Album Type and TXXX:RELEASETYPE (kept the same)
    obsidian_tags: str = ""    # "feminism; holiday": TXXX:Obsidian Tags, one value each, for the Obsidian vault
    tempo: str = ""         # MusicBee's Tempo (ID3 TXXX:TEMPO), e.g. "Fast"
    rating: int = 0
    plays: int = 0          # play count, if a player stored it in the file (never written by this app)
    has_cover: bool = False
    lyrics_in_file: bool = False
    lyrics_file: bool = False
    lyrics_synced: bool = False  # the lyrics have timestamps ("[00:12.34] line"): LRC-style synced lyrics
    lyrics_txt_mtime: float = 0.0  # when the .txt was last changed (so the cache notices new lyrics)
    gain: str = ""          # ReplayGain track gain, e.g. "-6.20 dB"
    real_format: str = ""   # set when the file isn't what its name says (e.g. "M4A" named .mp3)
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
    # Every tag in the file that the Tags panel has no box for: (key, name, value). The key is
    # what write(..., {"remove": [keys]}) deletes.
    frames: list[tuple[str, str, str]] = field(default_factory=list)


# ---------------------------------------------------------------- what a file really is

def _skip_id3(data: bytes) -> int:
    """Offset of the audio after any ID3v2 tag(s) at the start."""
    pos = 0
    while data[pos:pos + 3] == b"ID3" and len(data) >= pos + 10:
        b = data[pos + 6:pos + 10]
        size = (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]
        pos += 10 + size + (10 if data[pos + 5] & 0x10 else 0)
    return pos


def sniff(path: str) -> str:
    """The real format from the file's contents: MP3, M4A, FLAC, OGG, WAV, WEBM, AAC, or "" (unknown/damaged)."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
            if head[:3] == b"ID3" and len(head) >= 10:
                f.seek(0)
                data = f.read(10)
                pos = _skip_id3(data + b"\0" * 16)
                f.seek(pos)
            else:
                f.seek(0)
                pos = 0
            data = f.read(64 * 1024)
    except OSError:
        return ""
    if data[4:8] == b"ftyp":
        return "M4A"
    for magic, name in ((b"fLaC", "FLAC"), (b"OggS", "OGG"), (b"\x1a\x45\xdf\xa3", "WEBM"), (b"0&\xb2u", "WMA")):
        if data.startswith(magic):
            return name
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "WAV"
    # Look for an MPEG audio frame (MP3) or an ADTS frame (raw AAC) near the start.
    for i in range(min(len(data) - 4, 16 * 1024)):
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            layer = (data[i + 1] >> 1) & 0x03
            if layer == 0 and (data[i + 1] & 0xF6) == 0xF0:
                return "AAC"
            if layer == 1 and (data[i + 2] >> 4) not in (0, 15) and ((data[i + 2] >> 2) & 3) != 3:
                return "MP3"
    return ""


def problem_text(path: str, real: str, exc: Exception) -> str:
    ext = Path(path).suffix.lower().lstrip(".").upper()
    if real and real != ext:
        return (f"This is really a {real} file with a .{ext.lower()} name, so players may not play it and "
                f"its tags can't be read properly. Right-click → Repair file to turn it into a real MP3.")
    if real == "MP3":
        return (f"The MP3 audio has damage or junk at the start ({exc}). "
                "Right-click → Repair file to rebuild it without re-encoding.")
    return f"This file seems damaged or isn't music ({exc})."


# ---------------------------------------------------------------- helpers

def _split_number(value: str) -> tuple[str, str]:
    """"3/12" -> ("3", "12"); "Disc 01/02" -> ("01", "02")."""
    value = re.sub(r"^[^\d/]*(?=\d)", "", (value or "").strip())
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
    # Synced (timestamped) lyrics, in the file or in the .txt, or a separate synced-lyrics frame (SYLT).
    txt = sidecar.read_lyrics(path) if track.lyrics_file else ""
    if track.lyrics_file:
        try:
            track.lyrics_txt_mtime = os.stat(sidecar.lyrics_path(path)).st_mtime
        except OSError:
            pass
    track.lyrics_synced = track.lyrics_synced or is_synced(details.lyrics) or is_synced(txt)
    if with_details and not details.lyrics and track.lyrics_file:
        details.lyrics = txt
    if not with_details:
        details.lyrics, details.cover = "", None
    return details


_TIMESTAMP_LINE = re.compile(r"^\s*\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]", re.MULTILINE)


def is_synced(lyrics: str) -> bool:
    """Timestamped (LRC) lyrics: at least two lines starting like "[01:23.45]". Lines like "[Verse 1]" or
    "[Chorus]" (as Genius writes them) don't count."""
    return len(_TIMESTAMP_LINE.findall(lyrics or "")) >= 2


def read_track(path: str) -> Track:
    return read(path).track


def _read_mp3(d: Details) -> None:
    t = d.track
    try:
        audio = MP3(t.path)
    except Exception as exc:  # noqa: BLE001
        # Not readable as MP3 audio: say what the file really is, but still show its tags.
        real = sniff(t.path)
        t.real_format = real if real != "MP3" else ""
        t.error = problem_text(t.path, real, exc)
        try:
            tags = ID3(t.path)
        except Exception:  # noqa: BLE001
            return
    else:
        _info(t, audio)
        tags = audio.tags
    if tags is None:
        return
    t.title = _first(tags.get("TIT2"))
    t.artist = _first(tags.get("TPE1"))
    t.album_artist = _first(tags.get("TPE2"))
    t.sort_artist = _first(tags.get("TSO2")) or (
        _first(tags.get("TSOP")) if not t.album_artist or t.album_artist == _first(tags.get("TPE1")) else "")
    t.album = _first(tags.get("TALB"))
    genre = tags.get("TCON")
    t.genre = "; ".join(genre.genres) if genre else ""
    year = tags.get("TDRC") or tags.get("TYER")
    t.year = _first(year)[:4] if year else ""
    t.track, t.track_total = _split_number(_first(tags.get("TRCK")))
    t.disc, t.disc_total = _split_number(_first(tags.get("TPOS")))
    popms = tags.getall("POPM")
    counts = [int(getattr(p, "count", 0) or 0) for p in popms]
    counts += [int(getattr(p, "count", 0) or 0) for p in tags.getall("PCNT")]
    for frame in tags.getall("TXXX") + [f for f in tags.getall("COMM") if f.desc]:
        if frame.desc.lower().replace("songs-db_", "").replace("_", "").replace(" ", "") in PLAY_COUNT_NAMES:
            counts.append(_int(_first(frame.text)))
    t.plays = max(counts, default=0)
    if popms:
        chosen = next((p for p in popms if p.email == POPM_EMAIL), popms[0])
        t.rating = popm_to_stars(int(chosen.rating))
    t.has_cover = bool(tags.getall("APIC"))
    t.lyrics_synced = bool(tags.getall("SYLT"))
    uslt = tags.getall("USLT")
    t.lyrics_in_file = any(str(u.text).strip() for u in uslt)
    t.language = _first(tags.get("TLAN"))
    t.original_artist = "; ".join(str(x) for x in tags["TOPE"].text) if "TOPE" in tags else ""
    t.original_album = _first(tags.get("TOAL"))
    original = tags.get("TDOR") or tags.get("TORY")
    t.original_year = _first(original)[:4] if original else ""
    t.edition = _first(tags.get("TSST"))
    musicbee_tempo = ""
    for frame in tags.getall("TXXX"):
        desc = frame.desc.upper()
        if desc == "REPLAYGAIN_TRACK_GAIN":
            t.gain = _first(frame.text)
        elif desc == "TEMPO":
            t.tempo = _first(frame.text)
        elif desc == "TSST" and not t.edition:
            t.edition = _first(frame.text)
        elif desc.casefold() in ALBUM_TYPE_TAGS:
            t.album_type = t.album_type or ";".join(str(x).strip() for x in frame.text if str(x).strip())
        elif desc.casefold() == OBSIDIAN_TAGS.casefold():
            t.obsidian_tags = "; ".join(clean_tag(str(x)) for x in frame.text if clean_tag(str(x)))
        elif desc not in KNOWN_TXXX and desc.lower().replace("_", "").replace(" ", "") not in PLAY_COUNT_NAMES:
            d.extra[frame.desc] = " / ".join(str(x) for x in frame.text)
    for frame in tags.getall("COMM"):
        if not frame.desc:
            if str(frame).strip():
                d.extra["Comment"] = str(frame)
            continue
        value = " / ".join(str(x) for x in frame.text)
        if frame.desc.lower() == "songs-db_tempo":
            musicbee_tempo = _first(frame.text)
        elif frame.desc.lower().replace("songs-db_", "").replace("_", "").replace(" ", "") in PLAY_COUNT_NAMES:
            pass
        elif _is_edition_comm(frame.desc, value, t.edition):
            t.edition_field = frame.desc
            t.edition = t.edition or value.strip()
        else:
            d.extra[friendly_comm(frame.desc)] = value
    t.tempo = t.tempo or musicbee_tempo
    d.lyrics = next((str(u.text) for u in uslt if str(u.text).strip()), "")
    pics = tags.getall("APIC")
    if pics:
        pic = next((p for p in pics if p.type == 3), pics[0])
        d.cover, d.cover_mime = pic.data, pic.mime
    d.frames = _other_id3(tags, t)


KNOWN_TXXX = {"REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK", "TEMPO", "TSST", "OBSIDIAN TAGS"}
# The tag Media Manager can read into a note's tags (its music library: source "a tag in the file",
# name "Obsidian Tags"). One value per tag, without the "#".
OBSIDIAN_TAGS = "Obsidian Tags"
ALBUM_TYPE_TAGS = {"musicbrainz album type", "releasetype"}
ALBUM_TYPES = ["album", "single", "ep", "compilation", "soundtrack", "live", "remix", "album;live",
               "album;compilation", "album;soundtrack", "album;remix", "ep;live", "single;live", "broadcast",
               "spokenword", "interview", "audiobook", "dj-mix", "mixtape/street", "demo", "other"]


def clean_tag(value: str) -> str:
    """"#Feminism " -> "Feminism"; spaces inside become hyphens (Obsidian tags can't have spaces)."""
    value = value.strip().lstrip("#").strip()
    return "-".join(value.split())


def split_tags(text: str) -> list[str]:
    """"#feminism, #atheism holiday" or "feminism; atheism" -> ["feminism", "atheism", "holiday"].
    (Obsidian tags have no spaces, so a space separates tags too.)"""
    out: list[str] = []
    for part in re.split(r"[;,\s]+", text or ""):
        tag = clean_tag(part)
        if tag and tag.casefold() not in {o.casefold() for o in out}:
            out.append(tag)
    return out
PLAY_COUNT_NAMES = {"playcount", "playcounter", "plays", "fmpsplaycount"}


def _int(value: str) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


# The MusicBee custom field the user keeps "Edition" in (e.g. "Songs-DB_Custom2"). Learned from the
# library (a custom field holding the same value as TXXX:TSST) and set by the app; "" = not known yet.
EDITION_COMM = ""


def _is_edition_comm(desc: str, value: str, edition: str) -> bool:
    if not desc.lower().startswith("songs-db_custom"):
        return False
    if EDITION_COMM and desc.lower() == EDITION_COMM.lower():
        return True
    return bool(edition) and value.strip().casefold() == edition.strip().casefold()


def friendly_comm(desc: str) -> str:
    """MusicBee keeps its custom fields as comments: "Songs-DB_Custom1" -> "Custom 1 (MusicBee)"."""
    m = re.fullmatch(r"songs-db_custom(\d+)", desc, re.IGNORECASE)
    if m:
        return f"Custom {m.group(1)} (MusicBee)"
    if desc.lower().startswith("songs-db_"):
        return desc[9:].replace("_", " ").title() + " (MusicBee)"
    return f"Comment: {desc}"


FRAME_NAMES = {
    "TCOM": "Composer", "TENC": "Encoded by", "TSSE": "Encoder settings", "TCOP": "Copyright", "TBPM": "BPM",
    "TPUB": "Publisher", "TMED": "Media type", "TOPE": "Original artist", "TIT1": "Grouping", "TIT3": "Subtitle",
    "TSRC": "ISRC", "TDOR": "Original date", "TORY": "Original year", "TSOA": "Sort album", "TSOT": "Sort title",
    "TSO2": "Sort album artist", "TSOP": "Sort artist", "TSOC": "Sort composer", "TEXT": "Lyricist",
    "TOAL": "Original album", "TFLT": "File type", "TKEY": "Key", "TLEN": "Length", "TOWN": "Owner",
    "TPE3": "Conductor", "TPE4": "Remixed by", "TCMP": "Compilation", "TDRL": "Release date", "TDTG": "Tagging date",
    "TMOO": "Mood", "TPRO": "Produced", "TRSN": "Radio station", "WXXX": "Web page", "WOAR": "Artist web page",
    "WCOM": "Buy at", "WOAF": "File web page", "WOAS": "Source web page", "PRIV": "Private data",
    "UFID": "Unique file ID", "GEOB": "Embedded file", "MCDI": "CD identifier", "PCNT": "Play count",
    "POPM": "Rating / play count", "SYLT": "Synced lyrics", "COMM": "Comment", "TXXX": "Custom", "USLT": "Lyrics",
    "APIC": "Picture", "PRIV:": "Private data", "RVA2": "Volume adjustment", "RVAD": "Volume adjustment",
    "TDAT": "Date", "TIME": "Time", "TRDA": "Recording dates", "TSIZ": "Size", "MLLT": "MPEG lookup table",
    "OWNE": "Ownership", "LINK": "Link", "ETCO": "Event timing", "CHAP": "Chapter", "CTOC": "Table of contents",
}
MANAGED_ID3 = {"TIT2", "TPE1", "TPE2", "TALB", "TCON", "TDRC", "TYER", "TRCK", "TPOS", "TLAN", "TSST",
               "TOPE", "TOAL", "TORY", "TDOR"}
MANAGED_VORBIS = {"title", "artist", "albumartist", "album artist", "album_artist", "album", "date", "year", "genre",
                  "tracknumber", "tracktotal", "totaltracks", "discnumber", "disctotal", "totaldiscs", "language",
                  "tempo", "discsubtitle", "edition", "rating", "lyrics", "unsyncedlyrics",
                  "originalartist", "originalalbum", "originalyear", "originaldate", "obsidian tags",
                  "releasetype", "musicbrainz_albumtype"}


def frame_is_text(key: str) -> bool:
    """Whether an "Other tags" entry holds text that can be edited (not pictures, IDs or counters)."""
    if key.startswith(("vorbis:", "TXXX:", "COMM:")):
        return True
    fid = key.split(":", 1)[0]
    return len(fid) == 4 and fid.startswith("T")


def _set_frame_text(frame, value: str) -> None:  # type: ignore[no-untyped-def]
    values = [v.strip() for v in value.split(" / ")] if " / " in value else [value]
    frame.text = [v for v in values if v] or [""]
    frame.encoding = 1  # UTF-16, so any character can be saved


def _frame_text(frame) -> str:  # type: ignore[no-untyped-def]
    text = getattr(frame, "text", None)
    if text is not None and not isinstance(text, (bytes, bytearray)):
        values = text if isinstance(text, list) else [text]
        return " / ".join(str(v) for v in values)[:500]
    if hasattr(frame, "url"):
        return str(frame.url)
    if hasattr(frame, "count") and not hasattr(frame, "rating"):
        return str(frame.count)
    if hasattr(frame, "rating"):
        plays = getattr(frame, "count", 0) or 0
        return f"{popm_to_stars(int(frame.rating))} stars, played {plays} times ({frame.email})"
    data = getattr(frame, "data", None)
    if isinstance(data, (bytes, bytearray)):
        owner = getattr(frame, "owner", "") or getattr(frame, "desc", "")
        return f"{owner + ': ' if owner else ''}{len(data):,} bytes of data"
    return str(frame)[:500]


def _other_id3(tags, t: Track) -> list[tuple[str, str, str]]:  # type: ignore[no-untyped-def]
    """Every frame the Tags panel doesn't show in its own box (title, art, lyrics, rating... do)."""
    out = []
    for key, frame in tags.items():
        fid = frame.FrameID
        if fid in MANAGED_ID3 or fid in ("APIC", "USLT"):
            continue
        desc = getattr(frame, "desc", "") or ""
        if fid == "TXXX" and (desc.upper() in ("TSST", "TEMPO") or desc.casefold() == OBSIDIAN_TAGS.casefold()
                              or desc.casefold() in ALBUM_TYPE_TAGS):
            continue
        if fid == "COMM" and (desc.lower() == "songs-db_tempo" or (t.edition_field and desc == t.edition_field)):
            continue
        if fid == "POPM" and int(getattr(frame, "count", 0) or 0) == 0:
            continue  # just the rating, which has its own stars
        if fid == "TXXX":
            name = desc
        elif fid == "COMM":
            name = friendly_comm(desc) if desc else "Comment"
        else:
            name = FRAME_NAMES.get(fid, fid) + (f" ({desc})" if desc else "")
        out.append((key, name, _frame_text(frame)))
    return sorted(out, key=lambda x: x[1].casefold())


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
    t.original_artist = "; ".join(tags.get("originalartist", [])) if tags else ""
    t.original_album = _vorbis(tags, "originalalbum")
    t.album_type = _vorbis(tags, "releasetype", "musicbrainz_albumtype")
    t.original_year = (_vorbis(tags, "originalyear") or _vorbis(tags, "originaldate"))[:4]
    t.obsidian_tags = "; ".join(clean_tag(x) for x in (tags.get(OBSIDIAN_TAGS.lower(), []) if tags else []))
    t.tempo = _vorbis(tags, "tempo")
    t.edition = _vorbis(tags, "discsubtitle", "edition")
    t.plays = max((_int(_vorbis(tags, k)) for k in ("playcount", "play_count", "fmps_playcount", "play_counter")),
                  default=0)
    t.artist = _vorbis(tags, "artist")
    t.album_artist = _vorbis(tags, "albumartist", "album artist", "album_artist")
    t.sort_artist = _vorbis(tags, "albumartistsort") or _vorbis(tags, "artistsort")
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
    d.frames = [(f"vorbis:{k}", k.upper(), "; ".join(str(v) for v in tags.get(k, [])))
                for k in sorted({k.lower() for k in (tags.keys() if tags else [])}) if k not in MANAGED_VORBIS]


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
        value = _join_number(c.get("disc", old[0]), c.get("disc_total", old[1]), pad=True)
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
    if "original_artist" in c:
        tags.delall("TOPE")
        names = [n.strip() for n in c["original_artist"].split(";") if n.strip()]
        if names:
            tags.add(TOPE(encoding=1, text=names))
    if "original_album" in c:
        text("TOAL", TOAL, c["original_album"].strip())
    if "original_year" in c:
        tags.delall("TDOR")
        tags.delall("TORY")
        if c["original_year"].strip():
            tags.add(TDOR(encoding=1, text=[c["original_year"].strip()]))  # saved as TORY in ID3v2.3
    if "album_type" in c:
        # The same value under both names that players and taggers use.
        for frame in [f for f in tags.getall("TXXX") if f.desc.casefold() in ALBUM_TYPE_TAGS]:
            tags.delall(frame.HashKey)
        value = ";".join(p.strip() for p in re.split(r"[;/,]", c["album_type"]) if p.strip())
        if value:
            for desc in ("MusicBrainz Album Type", "RELEASETYPE"):
                tags.add(TXXX(encoding=1, desc=desc, text=[value]))
    if "obsidian_tags" in c:
        for frame in [f for f in tags.getall("TXXX") if f.desc.casefold() == OBSIDIAN_TAGS.casefold()]:
            tags.delall(frame.HashKey)
        values = split_tags(c["obsidian_tags"])
        if values:
            tags.add(TXXX(encoding=1, desc=OBSIDIAN_TAGS, text=values))
    if "tempo" in c:
        # Older MusicBee versions kept Tempo in a comment: change it where it already is.
        value = c["tempo"].strip()
        txxx = [f for f in tags.getall("TXXX") if f.desc.upper() == "TEMPO"]
        comm = [f for f in tags.getall("COMM") if f.desc.lower() == "songs-db_tempo"]
        for frame in txxx + comm:
            tags.delall(frame.HashKey)
        if value:
            if comm and not txxx:
                tags.add(COMM(encoding=1, lang=comm[0].lang, desc=comm[0].desc, text=[value]))
            else:
                tags.add(TXXX(encoding=1, desc="TEMPO", text=[value]))
    if "edition" in c:
        # ID3v2.3 has no "disc subtitle" frame, so MusicBee writes TXXX:TSST; do the same.
        old_tsst = _first(tags.get("TSST")) or next(
            (_first(f.text) for f in tags.getall("TXXX") if f.desc.upper() == "TSST"), "")
        tags.delall("TSST")
        for frame in [f for f in tags.getall("TXXX") if f.desc.upper() == "TSST"]:
            tags.delall(frame.HashKey)
        if c["edition"].strip():
            tags.add(TXXX(encoding=1, desc="TSST", text=[c["edition"].strip()]))
        # MusicBee shows its own "Edition" custom field: keep it the same.
        old_edition = old_tsst
        comms = [f for f in tags.getall("COMM") if f.desc.lower().startswith("songs-db_custom") and (
            (EDITION_COMM and f.desc.lower() == EDITION_COMM.lower())
            or (old_edition and str(f).strip().casefold() == old_edition.casefold()))]
        lang = comms[0].lang if comms else "eng"
        descs = {f.desc for f in comms} or ({EDITION_COMM} if EDITION_COMM else set())
        for frame in comms:
            tags.delall(frame.HashKey)
        if c["edition"].strip():
            for desc in descs:
                tags.add(COMM(encoding=1, lang=lang, desc=desc, text=[c["edition"].strip()]))
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
    for key, value in c.get("set_frames", {}).items():
        if key in tags and frame_is_text(key) and hasattr(tags[key], "text"):
            if value.strip():
                _set_frame_text(tags[key], value)
            else:
                del tags[key]  # emptied: no tag
    for key in c.get("remove", []):
        if key in tags:
            del tags[key]
    tags.update_to_v23()
    tags.save(path, v2_version=3, v23_sep=None)  # several values: null-separated, as MusicBee writes them


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
              "disc": "discnumber", "disc_total": "disctotal", "language": "language", "tempo": "tempo",
              "edition": "discsubtitle", "original_album": "originalalbum", "original_year": "originalyear"}
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
    if "original_artist" in c:
        put("originalartist", "")
        names = [n.strip() for n in c["original_artist"].split(";") if n.strip()]
        if names:
            tags["originalartist"] = names
    if "album_type" in c:
        value = ";".join(p.strip() for p in re.split(r"[;/,]", c["album_type"]) if p.strip())
        put("releasetype", value, "musicbrainz_albumtype")
        if value:
            tags["musicbrainz_albumtype"] = [value]
    if "obsidian_tags" in c:
        put(OBSIDIAN_TAGS.lower(), "")
        values = split_tags(c["obsidian_tags"])
        if values:
            tags[OBSIDIAN_TAGS.lower()] = values
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
    for key, value in c.get("set_frames", {}).items():
        name = key.split(":", 1)[1] if key.startswith("vorbis:") else key
        if value.strip():
            tags[name] = [v.strip() for v in value.split(";") if v.strip()]
        elif name in tags:
            del tags[name]
    for key in c.get("remove", []):
        name = key.split(":", 1)[1] if key.startswith("vorbis:") else key
        if name in tags:
            del tags[name]
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
