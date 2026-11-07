"""Repair a song that can't be read: a mis-named file (e.g. M4A called .mp3) or a damaged MP3.

- Damaged MP3 (junk before the audio, broken frames): ffmpeg copies the MP3 audio
  frames into a clean file WITHOUT re-encoding, so nothing is lost.
- Another format with an .mp3 name: it's converted to a real MP3 (V0 by default).
  That's a second lossy encode, but it's the only way to get a real MP3.

Tags, art, lyrics and rating are carried over. The original goes to the Recycle
Bin and the repaired file gets the same name, so its lyrics .txt still matches.
"""

from __future__ import annotations

import os
from pathlib import Path

from mutagen.id3 import ID3
from send2trash import send2trash

from . import tags
from .ffmpeg import FfmpegError, last_lines, run
from .settings import MP3_QUALITIES


def describe(path: str) -> tuple[str, bool]:
    """(what's wrong, whether repairing re-encodes)."""
    real = tags.sniff(path)
    if real in ("", "MP3"):
        return "The MP3 audio is damaged or has junk before it. It'll be rebuilt without re-encoding.", False
    return (f"This is really a {real} file. It'll be converted to a real MP3 "
            "(a second lossy encode: if you can, get a proper MP3 or FLAC of this song instead)."), True


def _saved_tags(path: str) -> dict:
    """Everything worth keeping, read whichever way works."""
    d = tags.read(path, with_details=True)
    t = d.track
    changes: dict = {key: getattr(t, key) for key in tags.TEXT_FIELDS}
    changes["rating"] = t.rating
    changes["lyrics"] = d.lyrics
    if d.cover:
        changes["cover"] = (d.cover, d.cover_mime or tags.picture_mime(d.cover))
    if not any(changes[k] for k in ("title", "artist", "album")):
        try:  # an M4A named .mp3: its own tags
            import mutagen

            other = mutagen.File(path, easy=True)
            if other is not None and other.tags:
                for key, field in (("title", "title"), ("artist", "artist"), ("album", "album"),
                                   ("album_artist", "albumartist"), ("genre", "genre"), ("year", "date")):
                    values = other.tags.get(field)
                    if values:
                        changes[key] = str(values[0])[:4] if key == "year" else str(values[0])
        except Exception:  # noqa: BLE001
            pass
    return changes


def repair(ffmpeg: str, path: str, quality: str = "V0") -> str:
    """Repair path in place (same name). Returns the path."""
    src = Path(path)
    real = tags.sniff(path)
    keep = _saved_tags(path)
    try:
        frames = ID3(path)  # every frame, including ones this app doesn't edit (MusicBee's custom fields)
    except Exception:  # noqa: BLE001
        frames = None
    tmp = src.with_name(src.stem + ".repairing.mp3")
    encode = ["-c:a", "libmp3lame", *MP3_QUALITIES.get(quality, MP3_QUALITIES["V0"])[1]]
    # MP3 inside: copy the audio frames as they are. Otherwise (or if copying fails): encode.
    attempts = [["-c:a", "copy"], encode] if real in ("", "MP3") else [encode]
    try:
        for audio in attempts:
            args = ["-y", "-err_detect", "ignore_err", "-i", str(src), "-map", "0:a:0", "-map_metadata", "-1",
                    *audio, "-id3v2_version", "0", "-write_id3v1", "0", "-f", "mp3", str(tmp)]
            proc = run(ffmpeg, args)
            if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size >= 1024 \
                    and not tags.read_track(str(tmp)).error:
                break
        else:
            raise FfmpegError(f"ffmpeg couldn't read the audio: {last_lines(proc.stderr_text)}")
        if frames is not None and len(frames):
            frames.update_to_v23()
            frames.save(str(tmp), v2_version=3)  # all the original frames first...
        tags.write(str(tmp), keep, lyrics_file=False)  # ...then the ones the app knows, cleanly
        check = tags.read_track(str(tmp))
        if check.error:
            raise FfmpegError(f"the repaired file still can't be read: {check.error}")
        send2trash(str(src))
        os.replace(tmp, src)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return str(src)
