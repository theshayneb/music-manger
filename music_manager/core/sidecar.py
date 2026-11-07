"""Files that belong to a song and must follow it: "<song name>.txt" (lyrics, which
is what Plex reads) and "<song name>.lrc" (synced lyrics) in the same folder."""

from __future__ import annotations

import os
from pathlib import Path

COMPANION_EXTENSIONS = (".txt", ".lrc")


def lyrics_path(song: str | Path) -> Path:
    return Path(song).with_suffix(".txt")


def companions(song: str | Path) -> list[Path]:
    """Existing companion files of a song."""
    song = Path(song)
    return [song.with_suffix(ext) for ext in COMPANION_EXTENSIONS if song.with_suffix(ext).exists()]


def read_lyrics(song: str | Path) -> str:
    path = lyrics_path(song)
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    encodings = ["utf-16"] if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ["utf-8-sig", "cp1252"]
    for encoding in encodings:
        try:
            return data.decode(encoding).replace("\r\n", "\n")
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def write_lyrics(song: str | Path, text: str) -> None:
    """Write lyrics to "<song>.txt" (UTF-8, Windows line ends), or remove it when text is empty."""
    path = lyrics_path(song)
    if not text.strip():
        if path.exists():
            from send2trash import send2trash

            send2trash(str(path))
        return
    data = text.replace("\r\n", "\n").strip("\n").replace("\n", "\r\n") + "\r\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data.encode("utf-8"))
    os.replace(tmp, path)


def companion_moves(song: str, new_song: str) -> list[tuple[str, str]]:
    """[(old companion, new companion)] for moving/renaming song -> new_song."""
    return [(str(c), str(Path(new_song).with_suffix(c.suffix))) for c in companions(song)]
