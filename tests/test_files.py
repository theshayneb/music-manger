import os

from music_manager.core import convert, fileops, keep, loudness, naming, sidecar, tags
from music_manager.core.settings import Settings
from music_manager.core.tags import Track

from .conftest import FFMPEG, PNG


def test_safe_names():
    assert fileops.safe_name("AC/DC") == "AC-DC"
    assert fileops.safe_name('What? "Yes": No.') == "What 'Yes' - No"


def test_rename_pattern_and_missing_tags():
    t = Track(path="C:/x/old.mp3", artist="Adele", title="Hello", track="3")
    assert naming.format_name("{artist} - {title}", t) == ("Adele - Hello", None)
    assert naming.format_name("{track:02} {title}", t) == ("03 Hello", None)
    assert naming.format_name("{artist} - {album}", t) == ("", "no Album")


def test_rename_plan_never_clashes(tmp_path):
    (tmp_path / "Adele - Hello.mp3").write_bytes(b"")
    a = Track(path=str(tmp_path / "a.mp3"), artist="Adele", title="Hello")
    b = Track(path=str(tmp_path / "b.mp3"), artist="Adele", title="Hello")
    (tmp_path / "a.mp3").write_bytes(b"")
    (tmp_path / "b.mp3").write_bytes(b"")
    names = [os.path.basename(dest) for _t, dest, _n in naming.plan([a, b], "{artist} - {title}")]
    assert names == ["Adele - Hello_1.mp3", "Adele - Hello_2.mp3"]


def test_lyrics_file_follows_moves_and_renames(tmp_path):
    src = tmp_path / "in" / "x.mp3"
    src.parent.mkdir()
    src.write_bytes(b"x")
    src.with_suffix(".txt").write_text("lyrics", encoding="utf-8")
    (tmp_path / "out").mkdir()
    moved, failures = fileops.move([str(src)], str(tmp_path / "out"))
    assert not failures
    new = moved[str(src)]
    assert os.path.exists(new) and os.path.exists(os.path.splitext(new)[0] + ".txt")
    done, _ = fileops.rename({new: str(tmp_path / "out" / "Artist - Title.mp3")})
    assert (tmp_path / "out" / "Artist - Title.txt").read_text(encoding="utf-8") == "lyrics"
    assert not (tmp_path / "out" / "x.txt").exists()


def test_flac_converts_to_v0_mp3_with_everything(song):
    flac = str(song("Song.flac", seconds=4))
    tags.write(flac, {"title": "T", "artist": "A", "album": "Al", "year": "2020", "rating": 4,
                      "lyrics": "words", "cover": (PNG, "image/png")})
    mp3 = convert.to_mp3(FFMPEG, flac, "V0", recycle_original=False)
    t = tags.read_track(mp3)
    assert mp3.endswith("Song.mp3") and t.bitrate_mode == "VBR"
    assert (t.title, t.artist, t.album, t.year, t.rating) == ("T", "A", "Al", "2020", 4)
    assert t.has_cover and t.lyrics_in_file and t.lyrics_file
    assert not any(".converting." in n for n in os.listdir(os.path.dirname(mp3)))


def test_replaygain_tags(song):
    mp3 = str(song("loud.mp3", seconds=4))
    gain, peak = loudness.analyze(FFMPEG, mp3)
    assert -30 < gain < 30 and 0 < peak <= 1.5
    assert tags.read_track(mp3).gain == f"{gain:+.2f} dB"


def test_keep_converts_renames_and_moves_into_library(song, tmp_path):
    flac = song("downloads/track01.flac")
    library = tmp_path / "Music"
    library.mkdir()
    tags.write(str(flac), {"title": "Hello", "artist": "Adele", "lyrics": "words"})
    settings = Settings(library_folder=str(library), review_folder=str(flac.parent))
    [(track, dest, note)] = keep.plan([tags.read_track(str(flac))], settings)
    assert os.path.basename(dest) == "Adele - Hello.mp3" and "convert FLAC" in note
    final, warnings = keep.keep_one(FFMPEG, track, dest, settings)
    assert not warnings
    assert sorted(os.listdir(library)) == ["Adele - Hello.mp3", "Adele - Hello.txt"]
    assert not os.path.exists(flac) and not sidecar.lyrics_path(flac).exists()
    t = tags.read_track(final)
    assert t.gain and t.title == "Hello"


def test_keep_can_replace_a_song_already_in_the_library(song, tmp_path):
    new = song("dl/new.mp3")
    library = tmp_path / "Music"
    old = song("Music/Adele - Hello.mp3", seconds=1)
    old.with_suffix(".txt").write_text("old lyrics", encoding="utf-8")
    tags.write(str(new), {"title": "Hello", "artist": "Adele"})
    settings = Settings(library_folder=str(library), review_folder=str(new.parent), gain_on_keep=False)
    track = tags.read_track(str(new))
    [(_t, dest, _n)] = keep.plan([track], settings)
    assert os.path.basename(dest) == "Adele - Hello_1.mp3"  # not replacing: a new name
    [(_t, dest, note)] = keep.plan([track], settings, overwrite=True)
    assert os.path.basename(dest) == "Adele - Hello.mp3" and "replaces" in note
    keep.keep_one(FFMPEG, track, dest, settings, overwrite=True)
    assert sorted(os.listdir(library)) == ["Adele - Hello.mp3"]  # the old one and its lyrics are recycled
    assert tags.read_track(str(library / "Adele - Hello.mp3")).length > 2
