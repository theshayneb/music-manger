# Music Manager — notes for agents

Windows 11 desktop app, Python 3.12 + PySide6, tags via mutagen, conversion and loudness via a bundled ffmpeg.
Sister project of theshayneb/photo-manager (same structure, build and release flow).

- `music_manager/core/` has no Qt imports and holds all logic: `tags.py` (read/write ID3v2.3 + FLAC),
  `sidecar.py` (the `<song>.txt` lyrics file that must follow every move/rename/delete), `fileops.py`,
  `naming.py` (rename patterns), `convert.py` (FLAC → MP3), `loudness.py` (ReplayGain 2.0 via ebur128),
  `keep.py` (the Keep pipeline), `library.py` (folder scan + tag cache), `settings.py`.
- `music_manager/ui/` is the Qt layer. Writes and file operations run on `MainWindow.bg` (one thread, in order);
  folder scans on `MainWindow.scan_bg`. The player must release a file before it's changed (`_release`).
- Tests: `python -m pytest` (needs ffmpeg on PATH or in `vendor/ffmpeg`). GUI tests run offscreen.

The user's requirements (keep them):
- Everything must work with Plex ("Prefer local metadata" on), Plexamp and Symfonium.
- Library folder is FLAT (no Artist/Album folders) and in OneDrive; Review folder is on an external drive.
- Lyrics: embedded AND `<song>.txt` (not .lrc). No `cover.jpg`. Ratings ARE written into files.
- All kept music ends up as MP3 (V0 by default). Never re-encode MP3s.
- Keep it simple and reliable.
