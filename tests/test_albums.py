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


def test_vinyl_sides():
    songs = [t("I Should Have Known Better", "02", "Side A", album="A Hard Day's Night", artist="The Beatles",
               album_artist="The Beatles"),
             t("I'll Cry Instead", "02", "Side B", album="A Hard Day's Night", artist="The Beatles",
               album_artist="The Beatles"),
             t("If I Fell", "03", "Side A", album="A Hard Day's Night", artist="The Beatles",
               album_artist="The Beatles")]
    [section] = albums.build(songs)
    [album] = section.albums
    assert [(g.heading, [x.track for x in g.tracks]) for g in album.groups] == [
        ("Side A", ["02", "03"]), ("Side B", ["02"])]


def test_same_album_with_curly_quote_or_sort_artist_stays_together():
    songs = [t("I Want You Back", "08", album="'N Sync", artist="*NSYNC", album_artist="*NSYNC", sort_artist="NSYNC"),
             t("Tearin' Up My Heart", "01", album="’N Sync", artist="*NSYNC", album_artist="*NSYNC")]
    [section] = albums.build(songs)
    assert len(section.albums) == 1 and [x.track for x in section.albums[0].tracks] == ["01", "08"]


def test_jump_bar_finds_artists():
    from music_manager.ui.jump import Typed, find

    songs = [t("x", "1", artist=a, album_artist=a) for a in
             ("Adele", "My Chemical Romance", "Madonna", "The Vaccines", "50 Cent")]
    songs.sort(key=lambda s: albums.artist_sort_key(s.album_artist))
    names = [s.album_artist for s in songs]
    assert names[find(songs, "m")] == "Madonna" and names[find(songs, "my")] == "My Chemical Romance"
    assert names[find(songs, "v")] == "The Vaccines" and names[find(songs, "#")] == "50 Cent"
    assert names[find(songs, "b")] == "Madonna"  # nothing starts with B: the next one
    typed = Typed()
    assert typed.add("M") == "M" and typed.add("Y") == "MY"
