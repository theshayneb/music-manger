from mutagen.id3 import ID3

from music_manager.core import sidecar, tags

from .conftest import PNG


def test_mp3_round_trip_writes_id3v23(song):
    path = str(song("a.mp3"))
    tags.write(path, {"title": "Hello", "artist": "Adele", "album_artist": "Adele", "album": "25",
                      "year": "2015", "genre": "Pop; Soul", "track": "1", "track_total": "11",
                      "disc": "1", "disc_total": "1", "rating": 4, "lyrics": "Hello\nit's me",
                      "cover": (PNG, "image/png")})
    d = tags.read(path, with_details=True)
    t = d.track
    assert (t.title, t.artist, t.album_artist, t.album, t.year) == ("Hello", "Adele", "Adele", "25", "2015")
    assert (t.genre, t.track, t.track_total, t.disc, t.disc_total) == ("Pop; Soul", "01", "11", "01", "01")
    assert t.rating == 4 and t.has_cover and t.lyrics_in_file and t.lyrics_file
    assert d.lyrics == "Hello\nit's me" and d.cover == PNG
    assert ID3(path).version[:2] == (2, 3)


def test_rating_is_written_like_windows_media_player(song):
    path = str(song("a.mp3"))
    tags.write(path, {"rating": 5})
    [popm] = ID3(path).getall("POPM")
    assert (popm.email, popm.rating) == ("Windows Media Player 9 Series", 255)
    tags.write(path, {"rating": 0})
    assert ID3(path).getall("POPM") == []


def test_lyrics_go_in_the_file_and_in_a_txt_next_to_it(song):
    path = str(song("Song.mp3"))
    tags.write(path, {"lyrics": "one\ntwo"})
    txt = sidecar.lyrics_path(path)
    assert txt.name == "Song.txt"
    assert txt.read_bytes() == "one\r\ntwo\r\n".encode("utf-8")
    assert tags.read(path, with_details=True).lyrics == "one\ntwo"
    tags.write(path, {"lyrics": ""})
    assert not txt.exists()
    assert not tags.read_track(path).lyrics_in_file


def test_existing_txt_lyrics_are_shown_when_the_file_has_none(song):
    path = song("Song.mp3")
    path.with_suffix(".txt").write_text("from the txt", encoding="utf-8")
    d = tags.read(str(path), with_details=True)
    assert d.lyrics == "from the txt" and d.track.lyrics_file and not d.track.lyrics_in_file


def test_flac_round_trip(song):
    path = str(song("a.flac"))
    tags.write(path, {"title": "T", "artist": "A", "track": "3", "track_total": "9", "rating": 2,
                      "lyrics": "la", "cover": (PNG, "image/png")})
    t = tags.read_track(path)
    assert (t.title, t.artist, t.track, t.track_total, t.rating) == ("T", "A", "3", "9", 2)
    assert t.has_cover and t.lyrics_in_file


def test_only_changed_fields_are_written(song):
    path = str(song("a.mp3"))
    tags.write(path, {"title": "Keep me", "artist": "Old"})
    tags.write(path, {"artist": "New"})
    t = tags.read_track(path)
    assert (t.title, t.artist) == ("Keep me", "New")


def test_broken_file_reports_an_error_instead_of_failing(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not really an mp3")
    assert tags.read_track(str(bad)).error


def _like_the_library(path):
    """Tags exactly as the user's existing songs have them (written by MusicBee, ID3v2.3)."""
    from mutagen.id3 import POPM, TALB, TCON, TIT2, TLAN, TPE1, TPE2, TRCK, TXXX, TYER

    tags_ = ID3()
    for frame in (TALB(encoding=1, text=["More Adventurous"]), TPE2(encoding=1, text=["Rilo Kiley"]),
                  TPE1(encoding=1, text=["Rilo Kiley"]), TCON(encoding=1, text=["Indie Pop"]),
                  TLAN(encoding=1, text=["English"]), TXXX(encoding=1, desc="TEMPO", text=["Fast"]),
                  TIT2(encoding=1, text=["Portions for Foxes"]), TRCK(encoding=1, text=["03/11"]),
                  TYER(encoding=1, text=["2004"]), POPM(email="MusicBee", rating=196, count=0)):
        tags_.add(frame)
    tags_.save(path, v2_version=3)


def test_reads_the_existing_library_tags(song):
    path = str(song("Rilo Kiley - Portions for Foxes.mp3"))
    _like_the_library(path)
    t = tags.read_track(path)
    assert (t.title, t.artist, t.album_artist, t.album) == ("Portions for Foxes", "Rilo Kiley", "Rilo Kiley",
                                                            "More Adventurous")
    assert (t.genre, t.year, t.track, t.track_total) == ("Indie Pop", "2004", "03", "11")
    assert (t.language, t.tempo, t.rating) == ("English", "Fast", 4)


def test_editing_keeps_the_library_style(song):
    path = str(song("x.mp3"))
    _like_the_library(path)
    tags.write(path, {"genre": "Indie Rock", "rating": 4})
    id3 = ID3(path, translate=False)  # the frames as they are in the file
    assert id3.version[:2] == (2, 3)
    assert str(id3["TYER"]) == "2004" and str(id3["TRCK"]) == "03/11"
    assert str(id3["TLAN"]) == "English" and str(id3["TXXX:TEMPO"]) == "Fast"
    [popm] = id3.getall("POPM")
    assert popm.rating == 196
    tags.write(path, {"track": "5", "track_total": "12", "tempo": "Medium", "language": ""})
    id3 = ID3(path, translate=False)
    assert str(id3["TRCK"]) == "05/12" and str(id3["TXXX:TEMPO"]) == "Medium" and "TLAN" not in id3


def test_year_is_stored_as_a_v23_frame(song):
    path = str(song("y.mp3"))
    tags.write(path, {"year": "2004", "title": "T"})
    raw = open(path, "rb").read(4096)
    assert raw[3] == 3 and b"TYER" in raw and b"TDRC" not in raw
    assert tags.read_track(path).year == "2004"


def test_musicbee_extra_tags_are_read_and_kept(song):
    from mutagen.id3 import COMM, TALB, TPOS, TXXX

    path = str(song("Beyoncé - That's Why You're Beautiful.mp3"))
    id3 = ID3()
    for frame in (TALB(encoding=1, text=["I Am... Sasha Fierce"]), TPOS(encoding=1, text=["01/02"]),
                  COMM(encoding=1, lang="eng", desc="Songs-DB_Custom1", text=["English"]),
                  COMM(encoding=1, lang="eng", desc="Songs-DB_Tempo", text=["Slow"]),
                  COMM(encoding=1, lang="eng", desc="Songs-DB_Custom2", text=["Deluxe Edition"]),
                  TXXX(encoding=1, desc="TSST", text=["Deluxe Edition"])):
        id3.add(frame)
    id3.save(path, v2_version=3)
    d = tags.read(path, with_details=True)
    assert (d.track.disc, d.track.disc_total, d.track.edition, d.track.tempo) == ("01", "02", "Deluxe Edition", "Slow")
    assert d.extra == {"Custom 1 (MusicBee)": "English"} and d.track.edition_field == "Songs-DB_Custom2"

    tags.write(path, {"tempo": "Medium", "edition": "Deluxe Edition", "title": "That's Why You're Beautiful"})
    raw = ID3(path, translate=False)
    assert str(raw["COMM:Songs-DB_Tempo:eng"]) == "Medium" and "TXXX:TEMPO" not in raw
    assert str(raw["TXXX:TSST"]) == "Deluxe Edition" and "TSST" not in raw
    assert str(raw["COMM:Songs-DB_Custom1:eng"]) == "English" and str(raw["TPOS"]) == "01/02"


def test_play_count_is_shown_from_whichever_frame_has_it(song):
    from mutagen.id3 import PCNT, POPM, TXXX

    path = str(song("p.mp3"))
    id3 = ID3()
    id3.add(POPM(email="MusicBee", rating=196, count=12))
    id3.add(PCNT(count=41))
    id3.add(TXXX(encoding=1, desc="PLAY_COUNT", text=["7"]))
    id3.save(path, v2_version=3)
    d = tags.read(path, with_details=True)
    assert d.track.plays == 41 and d.extra == {}
    tags.write(path, {"rating": 5, "title": "x"})  # editing never touches the counts
    raw = ID3(path, translate=False)
    assert raw.getall("PCNT")[0].count == 41 and raw.getall("POPM")[0].count == 12
    assert tags.read_track(path).plays == 41


def test_musicbee_edition_custom_field_follows_the_edition(song, monkeypatch):
    from mutagen.id3 import COMM, TSO2, TXXX

    path = str(song("e.mp3"))
    id3 = ID3()
    id3.add(TXXX(encoding=1, desc="TSST", text=["Deluxe Edition"]))
    id3.add(COMM(encoding=1, lang="eng", desc="Songs-DB_Custom2", text=["Deluxe Edition"]))
    id3.add(COMM(encoding=1, lang="eng", desc="Songs-DB_Custom1", text=["English"]))
    id3.add(TSO2(encoding=1, text=["Knowles, Beyoncé"]))
    id3.save(path, v2_version=3)
    d = tags.read(path, with_details=True)
    assert d.track.edition == "Deluxe Edition" and d.track.edition_field == "Songs-DB_Custom2"
    assert d.extra == {"Custom 1 (MusicBee)": "English"} and d.track.sort_artist == "Knowles, Beyoncé"

    tags.write(path, {"edition": "Platinum Edition"})
    raw = ID3(path, translate=False)
    assert str(raw["TXXX:TSST"]) == "Platinum Edition" and str(raw["COMM:Songs-DB_Custom2:eng"]) == "Platinum Edition"
    assert str(raw["COMM:Songs-DB_Custom1:eng"]) == "English"

    # A song with no edition yet gets it in the custom field the library uses.
    other = str(song("f.mp3"))
    monkeypatch.setattr(tags, "EDITION_COMM", "Songs-DB_Custom2")
    tags.write(other, {"edition": "iTunes Version"})
    raw = ID3(other, translate=False)
    assert str(raw["COMM:Songs-DB_Custom2:eng"]) == "iTunes Version" and str(raw["TXXX:TSST"]) == "iTunes Version"


def test_every_other_tag_is_listed_and_can_be_deleted(song):
    from mutagen.id3 import COMM, TENC, TIT2, TXXX

    path = str(song("o.mp3"))
    id3 = ID3()
    for frame in (TIT2(encoding=1, text=["Keep me"]), COMM(encoding=1, lang="eng", desc="", text=["Ripped by X"]),
                  TENC(encoding=1, text=["LAME"]), TXXX(encoding=1, desc="MusicBrainz Album Id", text=["abc"])):
        id3.add(frame)
    id3.save(path, v2_version=3)
    frames = tags.read(path, with_details=True).frames
    names = {name: (key, value) for key, name, value in frames}
    assert names["Comment"][1] == "Ripped by X" and names["Encoded by"][1] == "LAME"
    assert names["MusicBrainz Album Id"][1] == "abc" and "Title" not in names
    tags.write(path, {"remove": [names["Comment"][0], names["Encoded by"][0]]})
    left = {name for _k, name, _v in tags.read(path, with_details=True).frames}
    assert left == {"MusicBrainz Album Id"} and tags.read_track(path).title == "Keep me"


def test_original_fields_and_obsidian_tags(song):
    path = str(song("x.mp3"))
    tags.write(path, {"original_artist": "Cyndi Lauper", "original_album": "She's So Unusual", "original_year": "1983",
                      "obsidian_tags": "#feminism, #holiday atheism", "title": "Girls"})
    t = tags.read_track(path)
    assert (t.original_artist, t.original_album, t.original_year) == ("Cyndi Lauper", "She's So Unusual", "1983")
    assert t.obsidian_tags == "feminism; holiday; atheism"
    raw = open(path, "rb").read(4096)
    # ID3v2.3 frames, and the tags as separate (null-separated) values, which Media Manager reads as a list.
    assert b"TORY" in raw and b"TDOR" not in raw and b"TOPE" in raw and b"TOAL" in raw
    utf16 = "feminism".encode("utf-16-le"), "holiday".encode("utf-16-le")
    assert utf16[0] + b"\x00\x00\xff\xfe" + utf16[1] in raw  # value, null, (byte-order mark) next value
    d = tags.read(path, with_details=True)
    assert [name for _k, name, _v in d.frames] == ["Encoder settings"]  # the rest have their own boxes


def test_album_type_is_one_field_for_both_tags(song):
    from mutagen.id3 import TXXX

    path = str(song("t.mp3"))
    id3 = ID3()
    id3.add(TXXX(encoding=1, desc="MusicBrainz Album Type", text=["album;live"]))
    id3.add(TXXX(encoding=1, desc="RELEASETYPE", text=["album;live"]))
    id3.save(path, v2_version=3)
    d = tags.read(path, with_details=True)
    assert d.track.album_type == "album;live" and not d.frames
    tags.write(path, {"album_type": "album"})
    raw = ID3(path, translate=False)
    assert str(raw["TXXX:MusicBrainz Album Type"]) == "album" and str(raw["TXXX:RELEASETYPE"]) == "album"


def test_new_lyrics_replace_an_existing_txt(song):
    path = song("Artist - Song.mp3")
    path.with_suffix(".txt").write_text("old words", encoding="utf-16")
    tags.write(str(path), {"lyrics": "new words"})
    assert path.with_suffix(".txt").read_bytes() == b"new words\r\n"
    assert tags.read(str(path), with_details=True).lyrics == "new words"
