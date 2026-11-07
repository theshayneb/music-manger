# Changelog

## 0.1.1

- Fixed: the year was saved in an ID3v2.4-style frame (TDRC) inside ID3v2.3 tags, which some players don't
  read. It's now saved as TYER, like MusicBee does. Saving a song's tags again fixes songs saved with 0.1.0.
- **Language** and **Tempo** (MusicBee's TEMPO field) in the Tags panel, and as columns you can show.
- Track numbers are saved with a leading zero (`03/11`), matching the existing library.

## 0.1.0

- **Review** (Downloads) and **Library** (Music) views, chosen on first start.
- Player: play/pause, seek, ±10/30 s, previous/next, volume; plays the next song when one ends.
- **Keep (K)**: converts FLAC/WAV/M4A to MP3 (V0 by default), measures ReplayGain, renames from tags
  (`{artist} - {title}` by default) and moves the song and its lyrics `.txt` into the Library, after a preview.
- **Delete**: to the Recycle Bin, with the lyrics file.
- Tag editor for one or many songs: title, artist, album artist, album, year, genre, track/disc numbers,
  rating, album art (file, paste, remove, save) and lyrics (saved in the file and as a `.txt`).
- Convert to MP3, Rename from tags (with preview) and F2, Measure volume.
- Search, and Show filters: missing tags, no art, no lyrics, volume not measured, low bitrate.
