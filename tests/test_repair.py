import subprocess

from mutagen.id3 import COMM, ID3

from music_manager.core import repair, tags

from .conftest import FFMPEG


def _tag_like_musicbee(path):
    tags.write(str(path), {"title": "Lunar Eclipse", "artist": "The Vaccines", "rating": 4, "lyrics": "la la"})
    id3 = ID3(str(path))
    id3.add(COMM(encoding=1, lang="eng", desc="Songs-DB_Custom1", text=["English"]))
    id3.update_to_v23()
    id3.save(str(path), v2_version=3)


def test_mp3_with_junk_before_the_audio_is_rebuilt_without_reencoding(song, tmp_path):
    good = song("good.mp3", seconds=5)
    _tag_like_musicbee(good)
    raw = good.read_bytes()
    tag_end = tags._skip_id3(raw)
    bad = tmp_path / "The Vaccines - Lunar Eclipse.mp3"
    junk = bytes((i * 7) % 255 for i in range(1_200_000))  # more than mutagen searches for the audio
    bad.write_bytes(raw[:tag_end] + junk + raw[tag_end:])
    t = tags.read_track(str(bad))
    assert "can't sync" in t.error.lower() or "damage" in t.error.lower()
    assert t.title == "Lunar Eclipse"  # the tags still show
    assert repair.describe(str(bad))[1] is False

    repair.repair(FFMPEG, str(bad))
    fixed = tags.read(str(bad), with_details=True)
    assert not fixed.track.error and fixed.track.length > 4
    assert (fixed.track.title, fixed.track.artist, fixed.track.rating) == ("Lunar Eclipse", "The Vaccines", 4)
    assert fixed.lyrics == "la la" and fixed.extra.get("Custom 1 (MusicBee)") == "English"
    assert fixed.track.bitrate_mode == tags.read_track(str(good)).bitrate_mode  # same audio, not re-encoded


def test_m4a_named_mp3_is_recognised_and_converted(tmp_path):
    bad = tmp_path / "Song.mp3"
    subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=4", "-c:a", "aac",
                    "-metadata", "title=Real Title", "-metadata", "artist=Real Artist", "-f", "ipod", str(bad)],
                   check=True)
    t = tags.read_track(str(bad))
    assert t.real_format == "M4A" and "really a M4A" in t.error
    assert repair.describe(str(bad))[1] is True
    repair.repair(FFMPEG, str(bad))
    t = tags.read_track(str(bad))
    assert not t.error and tags.sniff(str(bad)) == "MP3"
    assert (t.title, t.artist) == ("Real Title", "Real Artist")
