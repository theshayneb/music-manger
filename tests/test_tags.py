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
    assert (t.genre, t.track, t.track_total, t.disc, t.disc_total) == ("Pop; Soul", "1", "11", "1", "1")
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
