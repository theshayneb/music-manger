# Music Manager

A simple Windows app for deciding which new music to keep, and getting the keepers into your
Plex library with good tags.

- **Review**: your Downloads folder. Play a song, skip ahead, then **K** to keep it or **Delete** to send it
  to the Recycle Bin. The next song starts playing.
- **Library**: your Music folder. Fix tags, art, lyrics and ratings on songs you already have.
- **Keep** converts FLAC (and WAV, M4A…) to MP3 (V0 by default), measures its volume (ReplayGain),
  renames it from its tags (`Artist - Title.mp3` by default) and moves it into Music, with its lyrics file.

## Install

1. Open the repository's **Releases** page (or **Actions → latest Build → Artifacts**) and download
   `MusicManager-windows.zip`.
2. Unzip it anywhere, e.g. `C:\Apps\MusicManager`.
3. Run `MusicManager.exe`. ffmpeg is included. The first time, choose your Review and Library folders.

Windows SmartScreen may warn the first time because the app isn't signed: choose **More info → Run anyway**.

## Using it

| Do this | How |
|---|---|
| Play | Double-click a song or press **Enter**. **Space** play/pause, **Ctrl+←** back 10 s, **Ctrl+→** forward 30 s, **Ctrl+↑/↓** previous/next |
| Keep (Review only) | **K** or **✔ Keep**. A preview shows each song's new name and what will happen before anything changes |
| Delete | **Delete** (Recycle Bin, with its lyrics file) |
| Edit tags | Select one or more songs and type in the **Tags** panel; **Enter** or **Ctrl+S** saves. With several songs selected, fields that differ show *(mixed)* and are only changed if you type in them |
| Rating | Click the stars or **Ctrl+1…5**, **Ctrl+0** clears (saved straight away) |
| Album art | **From file…**, or copy an image in your browser and click **Paste**. **Save as…** saves the picture |
| Lyrics | Paste into the Lyrics box (one song at a time) and save |
| Rename | **Rename…** (from tags, with a preview) or **F2** for one song |
| Convert to MP3 | **Convert to MP3** for FLAC/WAV/M4A songs without keeping them |
| Measure volume | Writes ReplayGain tags for the selected songs |
| Find songs that need work | **Show**: missing tags, no album art, no lyrics, volume not measured, low bitrate |
| Search | **Ctrl+F**: every word must match the title, artist, album, genre or file name |
| Columns | Right-click the column headers to show or hide columns; drag to reorder |
| Show in Explorer | **Ctrl+E** |
| Refresh | **F5** |

## What gets written, and why

Everything is written so that Plex (with **Prefer local metadata** on), Plexamp and Symfonium read it.

| What | Where |
|---|---|
| Title, Artist, Album Artist, Album, Year, Genre, Track #, Disc # | ID3v2.3 tags (MP3) / Vorbis comments (FLAC). Several genres: `Rock; Pop` |
| Album art | Embedded in the file only (no `cover.jpg`) |
| Lyrics | In the file (ID3 `USLT`) **and** as `<song name>.txt` next to it, because Plex only reads the `.txt`. The `.txt` follows the song when it's renamed, moved or deleted |
| Rating | In the file (ID3 `POPM`, the way Windows Media Player, MusicBee and Mp3tag write it). Plex doesn't read it; other players do |
| Volume (ReplayGain) | `REPLAYGAIN_TRACK_GAIN` / `_PEAK` tags. The audio isn't changed. Symfonium uses these (Settings → Playback → ReplayGain). Plexamp ignores them and uses Plex's own loudness analysis |

Converting re-encodes the audio once from the lossless original. MP3s are never re-encoded: turning a 128 kbps
MP3 into V0 makes it bigger, not better. Use **Show → Low bitrate** to find songs worth replacing.

## Settings

Saved in `%APPDATA%\MusicManager\settings.json`. Tags of the Library are cached in
`%LOCALAPPDATA%\MusicManager\tags.json` (safe to delete). Unexpected errors are written to
`%LOCALAPPDATA%\MusicManager\errors.log`.

## Development

```bat
run.bat
```

creates a `.venv`, installs dependencies, downloads ffmpeg into `vendor\ffmpeg` and starts the app.

```bat
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
powershell -File scripts\build.ps1
```

Every push to `main` builds the Windows exe in GitHub Actions. To publish a release, bump `__version__` in
`music_manager/__init__.py` (and `pyproject.toml`), add a `CHANGELOG.md` entry and push to `main`: the build creates
the matching tag and release if that version has none yet.
