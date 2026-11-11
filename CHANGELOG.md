# Changelog

## 0.8.1

- Fixed: since 0.8.0, playing stopped after each song instead of going on to the next one. Songs start the way
  they did before 0.8.0 again.
- Another try at the start of songs being cut off: each song starts muted, and a quarter of a second in (once
  Windows' sound output is running) it jumps back to 0:00 and the sound comes on, so the beginning isn't lost.
  You may notice a quarter-second wait before a song starts.

## 0.8.0

- The Keep review shows what each song will become: **Becomes: Music\Artist - Title.mp3**, with its current
  name under it.
- **Genre from the artist's other songs**: a song with no Genre gets the genre most of your other songs by the
  same Artist have (also when you type an Artist), ready to Save. Moving on without saving it just drops it.
- **Suggestions while typing** Artist, Album Artist, Album, Original Artist and Original Album: names already in
  your library that contain what you've typed (any case). Genre, Language, Tempo and Album Type suggest the same way.
- **Tempo** drop-down in the order Very Slow, Slow, Moderate, Fast, Very Fast (then any others you use).
- The ✓ in the Lyrics and Art columns is centred.
- Fixed: saving tags of the song that's playing made it pause for a moment. The tags are now written while it
  keeps playing (only if Windows won't allow that is it paused briefly, as before).
- Fixed: Keep restarted the song that was playing even when another song was kept. Now only keeping the song that's
  playing moves on to the next one.
- A song now starts once its file has loaded, from 0:00 (the first moment could be lost before).

## 0.7.5

- Songs whose Album Artist is **"Various Artists"** are grouped and sorted under their own **Artist** in the
  album view and the jump bar (a compilation's songs show under each artist, with that compilation's cover).
  The files' tags aren't changed.

## 0.7.4

- Fixed: 0.7.3 made the Tags panel's tab area darker (its tabs were drawn by the app instead of by Qt). They're
  Qt's normal tabs again, with the same lighter grey as before; each tab still only as tall as what's in it.

## 0.7.3

- Fixed: a gap under Rating in the Standard tab. The Obsidian tag chips in the Extra tab set the height of every
  tab; now each tab is exactly as tall as what's in it.
- Fixed: the song's file name at the top of the Tags panel took one line on some tabs and two on others. It's
  always one line now, shortened in the middle with "…" when it doesn't fit (hover for the whole name).
- **📁 Add Folder ▾** and the location of the folder being shown moved from the Folders panel to the toolbar.

## 0.7.2

- The Tags panel's tabs are only as tall as the tab that's open (no empty space under Rating).
- The title, artist, rating and plays under the album art are gone (they're in the Tags panel and the song list).

## 0.7.1

- The **album art and its five buttons** (from file, copy, paste, remove, save as) **moved out of the Tags panel**
  to under the Folders list, with the buttons in a row under the art. They show the selected song, the one the
  Tags panel is editing, so the buttons always change that song. With nothing selected, the playing song's art
  is shown (buttons off). The Tags panel now starts with the tags. (0.7.0 showed the art twice.)

## 0.7.0

- Under the Folders list:
  - **The album art of the song playing**, or of the selected song when nothing plays, with its title, artist,
    album, year, rating, plays and length.
  - **Library check-up**: how many Library songs have no album art, no lyrics, timestamped lyrics, missing tags,
    no volume measurement or a low bitrate, plus how many songs wait in Review and how many of those aren't MP3s,
    and how many songs you kept this week. Click a line to show those songs. The numbers update as you work.
- Show filter **Not MP3 (to convert)**.

## 0.6.2

- Easier-to-read Obsidian tag chips (style A): brighter text on a filled pill, a slightly bigger font and more
  room; the song's tags are solid blue and bold.

## 0.6.1

- **Obsidian tags as chips**: every tag used in your library shows as a chip under the Obsidian tags box (Extra
  tab), in alphabetical order. Click chips to add or remove tags; the ones the song has are highlighted. The box
  can still be typed in, and a new tag typed there becomes a chip too. Several tags are saved as separate values,
  which Media Manager reads as a list for the note's `tags`. (With several songs selected whose tags differ, the
  chips are off, so they can't give every song the same tags by accident.)

## 0.6.0

- **Ratings in your Obsidian scale** everywhere: 💣 (1 star), ⭐⭐, ⭐⭐⭐, ⭐⭐⭐⭐, 🩷 (5 stars). The Tags panel's
  Rating line has one button per level (the current one highlighted; click it again to clear). Albums show
  their average as the nearest level.
- **Ratings go to Plex** when you change them here (with Plex set up): two Plex points per star, as Media Manager
  does (💣 = 2 … 🩷 = 10); no rating removes it. A tick box in Settings turns it off. The file's own rating is
  unchanged (Windows / MusicBee style).
- **Album view sort**: Sort Artist, then albums by year; inside an album by disc or vinyl side, then the standard
  songs, then each edition in alphabetical order, then track number. Fixed: a standard song with the same
  track number as an edition song could show under the edition's heading.
- Fixed: albums of one artist were out of year order when only some had a Sort Artist.
- The **artist headings show how many songs** each artist has, e.g. "Beyoncé (12)".
- **Columns can be dragged** to rearrange them in the album view too (their order, widths and which are shown
  are remembered).
- **Typed changes are kept** until you Save or Undo: saving something else (a rating, say) or switching tabs no
  longer wipes what you've typed in the Tags panel.
- **Search Google for the lyrics** in Find lyrics (one song, and the review for several): opens the search in your
  browser; paste the lyrics into the box and they're ticked to be used.
- Fixed: some song rows in the album view were taller than others.
- Fixed: the ▾ arrow drew over the text of the Albums and ＋ Add buttons.

## 0.5.4

- Find lyrics for several songs no longer asks first: it looks up every selected song (with or without lyrics)
  and goes straight to the review, where nothing is saved until you click Save. Songs whose lyrics are the same
  as what's found start unticked.

## 0.5.3

- **Narrower Tags panel** (layout A): a 150 px cover with small icon buttons beside it (from file, copy, paste,
  remove, save as), Disc # on its own line, smaller star buttons. The panel can now be dragged down to about
  290 px wide without cutting anything off.
- Three tabs: **Standard**, **Extra** and **Other**. Other lists every tag in the file this app has no box for
  (with how many in the tab name). **Double-click a value to change it**, or **✕** to delete the tag; both happen
  when you Save. Pictures, IDs and counters can be deleted but not edited.

## 0.5.2

- **Find lyrics for several songs** now shows what it found for each song, next to the lyrics it has now, before
  anything is saved: "+ new", "↻ replaces (timestamped) lyrics", "= same as now" or "✗ nothing found", and where
  they came from. Untick any that aren't right, edit the found lyrics if needed, then **Save**. Its first question
  can also include songs that already have lyrics (to replace timestamped ones, say).
- **Show → Lyrics with timestamps**: songs whose lyrics (in the file or the .txt) have lines like `[00:12.34]`,
  or a separate synced-lyrics tag. Lines like `[Verse 1]` or `[Chorus]` don't count.
- A changed lyrics .txt (edited elsewhere) is noticed and read again.

## 0.5.1

- The Tags panel has two tabs: **Standard tags** (Title, Artist, Album Artist, Album, Year, Genre, Language,
  Tempo, Track #, Disc #, Rating) and **Extra tags** (Album Type, Edition, Original Artist, Original Album,
  Original Year, Obsidian tags).
- **Album Type** (e.g. album, single, ep, compilation, live, album;live) with a drop-down. It's saved as both
  `MusicBrainz Album Type` and `RELEASETYPE`, kept the same.
- Scrolling over the Tags panel scrolls the panel; it no longer changes the value of a drop-down under the mouse.
- Smaller "Look up online" button, with "Find lyrics" beside it; no gap under the album art.
- Fixed: after saving from the Tags panel, ↓ went to the top of the album view instead of the next song.
- Fixed: the arrow keys (and Page Up/Down) in the album view stopped on the empty rows beside a cover and between
  albums; they now go from song to song.
- **Find lyrics** for several songs can also **replace the lyrics songs already have** (a tick box in its
  question; before, songs with lyrics, including just a .txt, were always left alone).
- A song picked right as a folder opens is no longer swapped for the first song.

## 0.5.0

- **Keep** first shows **every tag of each song, its album art and its lyrics**, with ⚠ beside anything empty
  (and a count per song). **Looks right: Keep** carries on; **Fix this song first** selects it so you can fix it.
- **Keep can replace** a song with the same name already in the Library: tick "Replace songs with the same name"
  in the Keep window (off by default; the old song and its lyrics file go to the Recycle Bin).
- **Obsidian tags** in the Tags panel (e.g. `#feminism #holiday`; pick from the list to add one). Saved in the
  file as the tag `Obsidian Tags`, one value per tag, so Media Manager can put them in the song note's tags.
- **Original Artist, Original Album and Original Year** can be edited (ID3 TOPE, TOAL, TORY).
- **Copy** album art and **Paste** it into another song (the picture is copied exactly, not re-compressed).
- **Folders panel**: **＋ Add** your own folders, and **save a search** (the search box plus the Show filter, e.g.
  "No album art" in the Library) as an entry of its own. Right-click an entry to rename or remove it.
- Each entry in the Folders panel **remembers where you were** (selection, scroll position, search) when you switch
  away and back, without reading the folder again.
- After saving tags or lyrics from the Tags panel (or Find lyrics), the **song list has the keyboard again**, so ↓
  goes to the next song.
- Fixed: saving tags while a song played **started it over**; it now carries on from where it was.
- "Look up metadata online" and "Find lyrics" sit side by side.
- Several values in one tag (e.g. two genres from MusicBee) are kept as separate values when saving, as MusicBee
  writes them.

## 0.4.0

- **Plays from Plex**: with your Plex server's address and token in ⚙ Settings (same as the Media Manager
  plugin: Get Info → View XML → the X-Plex-Token), the Plays column shows Plex's play counts, read when the app
  starts, on F5 and every 10 minutes. Songs are matched by the file path Plex has, else by file name. Hover a count
  to see Plex's and the file's. **Test** in Settings checks the connection.
- **Plex rescans your Library folder** a few seconds after you Keep songs or change tags there (can be switched
  off). Nothing else is ever changed in Plex.
- **Find lyrics** on its own: **Ctrl+Shift+L**, the button by Lyrics, or right-click. One song: the lyrics found
  (LRCLIB, lyrics.ovh, Genius) to check and save in one click. Several songs: the ones without lyrics get them,
  saved straight away; you're told which weren't found.
- **Every tag in the file** is listed in the Tags panel under "Other tags", each with ✕ to delete it when you Save
  (click ✕ again to keep it).
- **Alphabet jump bar** (# A–Z) above the songs: click a letter, or several in a row (M then Y for My Chemical
  Romance), to jump to that artist. Typing letters in the list does the same.
- **Genre, Language and Tempo** are drop-downs of the values your library already uses; you can still type a new one.
- **Folders panel** (Review / Library) can be dragged to any edge of the window or floated; along the top or bottom
  it takes a single row.
- Bigger play / previous / next buttons; the −10s / +30s buttons are gone (Ctrl+← / Ctrl+→ still skip).
- The album art's size and format are no longer shown in the Tags panel.
- Fixed: **vinyl sides** (Disc "Side A" / "Side B") are album sides in the album view, with their own headings and
  in the right order.
- Fixed: an album could show twice when its songs' album names differed only in a curly quote, accents or case, or
  only some songs had a Sort Artist.

## 0.3.0

- **Album view** (the Library opens in it; **F3** or **▦ Layout** switches to the song list and back), laid out like
  MusicBee's album and tracks view:
  - an **artist heading** (by Sort Artist, so "The Vaccines" sorts under V);
  - each album with its **cover** and, to the right of it, **Album (Year)**, the **album rating** (average of the
    rated songs, in half stars), **Genre**, **Tracks / Album** (e.g. 04 / 13) and the **album's length**;
  - its songs split by **album side**: headings like *Disc 01*, *Deluxe Edition • Disc 01*, *iTunes Version*,
    with a blank line before each;
  - sorted Sort Artist / Year / Disc-Track# / Title.
  - Click a cover to select the whole album (then edit all its songs at once).
  - **▦ Layout** menu: headings by Artist, Genre, Year or nothing; albums by year or title; small, medium or large
    covers; edition/disc headings and the blank lines on or off. Right-click the column headers to choose columns
    (#, Title, Artist, Album Artist, Rating, Plays and Time by default).
- **Edition** is kept the same in MusicBee's own "Edition" custom field (the `Songs-DB_Custom` comment that holds
  it, found from your library), so MusicBee and Music Manager always agree.
- Reads **Sort Artist** (ID3 TSO2/TSOP) for sorting.

## 0.2.1

- **Files that can't be read** ("can't sync to MPEG frame"): the Tags panel now says what's wrong, e.g. "This is
  really a M4A file with a .mp3 name" or "The MP3 audio has damage or junk at the start", and still shows the
  file's tags. **🛠 Repair file…** (in the Tags panel or right-click) fixes it:
  - a damaged MP3 is rebuilt from its own audio frames, **without re-encoding** (no quality lost);
  - another format with an .mp3 name is converted to a real MP3.
  Tags, art, lyrics, rating and MusicBee's custom fields are kept; the original goes to the Recycle Bin and the
  repaired file keeps its name. Editing is switched off until the file is repaired (before, Save seemed to do
  nothing because the tags couldn't be read back).
- **Plays** column: the play count stored in the file (ID3 PCNT, the POPM counter, or a PLAY_COUNT tag), shown
  only, never written.
- Online lookup: live concert recordings, bootlegs and broadcasts now rank below the studio album.

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
