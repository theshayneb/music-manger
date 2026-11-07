# Changelog

## 0.2.0

- **🔎 Look up online** (Ctrl+L, the button in the Tags panel, or right-click): searches iTunes, MusicBrainz and
  Deezer for the song's tags, shows album covers from iTunes, Cover Art Archive and Deezer with their real sizes
  (biggest first), and finds lyrics on LRCLIB, Genius and lyrics.ovh. Works in Review too, before you keep a song.
  Untagged downloads are searched by their file name ("Artist - Title").
  - Tags: your tags and the found ones side by side. Empty tags are ticked; a different Album, Year, Track or Disc is
    ticked; your Title, Artist, Genre and Edition are only replaced if you tick them. Double-click a found value
    to change it.
  - What you tick goes into the Tags panel as "Not saved"; nothing is written until you click Save.
- **Edition** (e.g. Deluxe Edition, iTunes Version), saved like MusicBee as `TXXX:TSST`. Lookups fill it from album
  names like "B'Day (Deluxe Edition)".
- Other tags in a song (e.g. MusicBee's Custom fields) are listed under the lyrics. They're never changed.
- Tempo is also read from, and saved back to, older MusicBee files that keep it as a comment (`Songs-DB_Tempo`).
- Disc numbers are saved with a leading zero (`01/02`), like track numbers.

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
