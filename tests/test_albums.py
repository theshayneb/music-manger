from music_manager.core import albums
from music_manager.core.tags import Track


def t(title, track, disc="", edition="", rating=0, album="I Am... Sasha Fierce", year="2008", total="06", **kw):
    return Track(path=f"C:/m/{title}.mp3", title=title, artist=kw.pop("artist", "Beyoncé"),
                 album_artist=kw.pop("album_artist", "Beyoncé"), album=album, year=year, track=track,
                 track_total=total, disc=disc, disc_total=kw.pop("disc_total", ""), edition=edition,
                 rating=rating, length=kw.pop("length", 200), genre=kw.pop("genre", "R&B"), **kw)


SASHA = [
    t("Single Ladies", "01", "02", rating=3), t("Halo", "02", "01", rating=4), t("Radio", "02", "02", rating=2),
    t("Disappear", "03", "01", rating=3), t("Sweet Dreams", "04", "02", rating=2), t("Ave Maria", "05", "01", rating=3),
    t("Satellites", "06", "01", rating=3), t("That's Why You're Beautiful", "08", "01", "Deluxe Edition", rating=3),
]
BDAY = [t("Upgrade U", "04", album="B'Day", year="2006", total="13", rating=2),
        t("Ring the Alarm", "05", album="B'Day", year="2006", total="13", rating=2),
        t("Irreplaceable", "09", album="B'Day", year="2006", total="13", rating=3),
        t("Lost Yo Mind", "14", album="B'Day", year="2006", total="13", edition="iTunes Version", rating=2)]


def test_like_the_musicbee_screenshot():
    [section] = albums.build(SASHA + BDAY)
    assert section.heading == "Beyoncé"
    bday, sasha = section.albums  # by year
    assert (bday.title_line(), bday.tracks_line(), bday.rating) == ("B'Day (2006)", "04 / 13", 2.5)
    assert [(g.heading, [x.title for x in g.tracks]) for g in bday.groups] == [
        ("", ["Upgrade U", "Ring the Alarm", "Irreplaceable"]), ("iTunes Version", ["Lost Yo Mind"])]
    assert sasha.rating == 3.0 and sasha.have == 8
    assert [(g.heading, [x.track for x in g.tracks]) for g in sasha.groups] == [
        ("Disc 01", ["02", "03", "05", "06"]), ("Deluxe Edition • Disc 01", ["08"]),
        ("Disc 02", ["01", "02", "04"])]


def test_artists_sort_without_the():
    songs = [t("a", "1", artist="The Vaccines", album_artist="The Vaccines", album="Come of Age"),
             t("b", "1", artist="Weezer", album_artist="Weezer", album="Blue"),
             t("c", "1", artist="Adele", album_artist="Adele", album="25")]
    assert [s.heading for s in albums.build(songs)] == ["Adele", "The Vaccines", "Weezer"]


def test_sort_artist_tag_wins():
    songs = [t("a", "1", artist="Rilo Kiley", album_artist="Rilo Kiley", album="X", sort_artist="Kiley, Rilo"),
             t("b", "1", artist="Adele", album_artist="Adele", album="25")]
    assert [s.heading for s in albums.build(songs)] == ["Adele", "Rilo Kiley"]


def test_group_by_genre_and_none():
    songs = SASHA + [t("x", "1", album="Other", genre="Pop")]
    assert [s.heading for s in albums.build(songs, "genre")] == ["Pop", "R&B"]
    assert [len(s.albums) for s in albums.build(songs, "none")] == [2]
