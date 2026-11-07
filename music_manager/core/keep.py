"""Keep: move a reviewed song into the Library, ready for Plex.

For each song: convert to MP3 if it isn't one, measure its volume (ReplayGain),
rename it from its tags, and move it (with its lyrics .txt) into the Library folder.
"""

from __future__ import annotations

from pathlib import Path

from . import convert, fileops, loudness, naming
from .ffmpeg import FfmpegError
from .settings import MP3_QUALITIES, Settings
from .tags import Track


def plan(tracks: list[Track], settings: Settings, pattern: str | None = None,
         overwrite: bool = False) -> list[tuple[Track, str | None, str]]:
    """[(track, path in the Library, note)] for each song. overwrite: a song with the same name in
    the Library is replaced (it goes to the Recycle Bin) instead of the new one getting "_1"."""
    pattern = (pattern or settings.rename_pattern) if settings.rename_on_keep else "{name}"
    rows = []
    for t, dest, note in naming.plan(tracks, pattern, settings.library_folder, ext=".mp3", overwrite=overwrite):
        notes = [f"convert {t.format} → MP3 {settings.mp3_quality}"] if convert.needs_convert(t.path) else []
        if note and note != "already named like this":
            notes.append(note)
        rows.append((t, dest, "; ".join(notes)))
    return rows


def keep_one(ffmpeg: str | None, track: Track, dest: str, settings: Settings,
             overwrite: bool = False) -> tuple[str, list[str]]:
    """Returns (where the song ended up, [warnings])."""
    warnings: list[str] = []
    path = track.path
    if convert.needs_convert(path):
        if not ffmpeg:
            raise FfmpegError("ffmpeg wasn't found, so it can't be converted to MP3")
        quality = settings.mp3_quality if settings.mp3_quality in MP3_QUALITIES else "V0"
        path = convert.to_mp3(ffmpeg, path, quality, recycle_original=settings.recycle_after_convert)
    if settings.gain_on_keep and ffmpeg:
        try:
            loudness.analyze(ffmpeg, path)
        except (FfmpegError, OSError) as exc:
            warnings.append(f"volume not measured: {exc}")
    if overwrite and Path(dest).exists() and not fileops.same_path(dest, path):
        failures = fileops.recycle([dest])  # the old one (and its lyrics file) to the Recycle Bin
        if failures:
            raise OSError(f"the song already in the Library couldn't be replaced: {failures[0][1]}")
    final = fileops.unique_path(Path(dest), ignore=path)
    fileops.move_song(path, str(final))
    return str(final), warnings
