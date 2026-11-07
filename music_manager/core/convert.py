"""Convert songs (FLAC, WAV, M4A...) to MP3 with ffmpeg's LAME encoder.

The MP3 goes next to the original with the same name (".mp3", or "_1" if that's
taken). Tags, cover art, lyrics and rating are copied by this app (not ffmpeg),
so they're written exactly the way the rest of the app writes them. The lyrics
file follows the new name. The original can then go to the Recycle Bin.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from send2trash import send2trash

from . import sidecar, tags
from .ffmpeg import FfmpegError, last_lines, run
from .fileops import friendly_error, unique_path
from .settings import MP3_QUALITIES


def needs_convert(path: str) -> bool:
    return Path(path).suffix.lower() != ".mp3"


def to_mp3(ffmpeg: str, src: str, quality: str = "V0", recycle_original: bool = False) -> str:
    """Convert src to MP3. Returns the new file's path."""
    source = Path(src)
    dest = unique_path(source.with_suffix(".mp3"))
    part = dest.with_name(dest.stem + ".converting.mp3")
    quality_args = MP3_QUALITIES.get(quality, MP3_QUALITIES["V0"])[1]
    args = ["-y", "-i", str(source), "-map", "0:a:0", "-map_metadata", "-1",
            "-c:a", "libmp3lame", *quality_args, "-id3v2_version", "0", "-write_id3v1", "0",
            "-f", "mp3", str(part)]
    try:
        proc = run(ffmpeg, args)
        if proc.returncode != 0 or not part.exists() or part.stat().st_size == 0:
            raise FfmpegError(f"couldn't convert {source.name}: {last_lines(proc.stderr_text)}")
        tags.copy_all(str(source), str(part))
        os.replace(part, dest)
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
    # The lyrics file belongs to the new MP3. Usually it already does ("Song.txt" is the
    # lyrics file of both Song.flac and Song.mp3); if the MP3 got another name, copy it.
    for companion in sidecar.companions(src):
        target = dest.with_suffix(companion.suffix)
        if not target.exists():
            shutil.copy2(companion, target)
    if recycle_original:
        # Only the original song: its lyrics file is now the MP3's.
        try:
            send2trash(str(source))
        except OSError as exc:
            raise OSError(f"{dest.name} was made, but {source.name} couldn't go to the Recycle Bin: "
                          f"{friendly_error(exc)}") from exc
    return str(dest)
