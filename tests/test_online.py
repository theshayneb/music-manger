"""Offline tests: the sites' answers (in the shape their APIs document) become the right tags."""

from music_manager.core import online

ITUNES = {"resultCount": 1, "results": [{
    "wrapperType": "track", "kind": "song", "artistName": "Beyoncé",
    "collectionName": "I Am... Sasha Fierce (Deluxe Edition)",
    "trackName": "Halo", "collectionArtistName": "Beyoncé", "releaseDate": "2008-11-14T08:00:00Z",
    "primaryGenreName": "R&B/Soul", "trackNumber": 2, "trackCount": 11, "discNumber": 1, "discCount": 2,
    "trackTimeMillis": 261640, "trackViewUrl": "https://music.apple.com/x",
    "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/Music/v4/aa/source/100x100bb.jpg"}]}

MUSICBRAINZ = {"recordings": [{
    "id": "r1", "title": "Halo", "length": 261000,
    "artist-credit": [{"name": "Beyoncé", "joinphrase": "", "artist": {"name": "Beyoncé"}}],
    "first-release-date": "2008-11-12",
    "releases": [{"id": "rel1", "title": "I Am… Sasha Fierce", "status": "Official", "date": "2008-11-14",
                  "country": "US", "release-group": {"id": "rg1", "primary-type": "Album"},
                  "track-count": 11,
                  "media": [{"position": 1, "format": "CD", "track-count": 11,
                             "track": [{"id": "t1", "number": "2", "title": "Halo"}]}]}]}]}

DEEZER_TRACK = {"id": 1, "title": "Halo", "duration": 261, "track_position": 2, "disk_number": 1,
                "release_date": "2008-11-14", "link": "https://www.deezer.com/track/1",
                "artist": {"name": "Beyoncé"},
                "album": {"id": 9, "title": "I AM...SASHA FIERCE", "cover_xl": "https://cdn-images.dzcdn.net/x/1000x1000.jpg",
                          "cover_medium": "https://cdn-images.dzcdn.net/x/250x250.jpg"}}
DEEZER_ALBUM = {"id": 9, "title": "I AM...SASHA FIERCE", "nb_tracks": 11, "release_date": "2008-11-14",
                "genres": {"data": [{"name": "R&B"}]}, "artist": {"name": "Beyoncé"}}


def test_itunes():
    [c] = online.parse_itunes(ITUNES)
    assert (c.title, c.artist, c.album_artist, c.album, c.edition) == (
        "Halo", "Beyoncé", "Beyoncé", "I Am... Sasha Fierce", "Deluxe Edition")
    assert (c.year, c.genre, c.track, c.track_total, c.disc, c.disc_total) == ("2008", "R&B/Soul", "2", "11", "1", "2")
    assert c.covers[0].url.endswith("/3000x3000bb.jpg") and c.covers[0].thumb.endswith("/400x400bb.jpg")


def test_musicbrainz():
    [c] = online.parse_musicbrainz(MUSICBRAINZ)
    assert (c.title, c.artist, c.album, c.year, c.track, c.track_total) == (
        "Halo", "Beyoncé", "I Am… Sasha Fierce", "2008", "2", "11")
    assert c.disc == "" and "Album" in c.detail
    assert c.covers[0].url == "https://coverartarchive.org/release/rel1/front"


def test_deezer():
    c = online.parse_deezer_track(DEEZER_TRACK, DEEZER_ALBUM)
    assert (c.title, c.album, c.year, c.genre, c.track, c.track_total) == (
        "Halo", "I AM...SASHA FIERCE", "2008", "R&B", "2", "11")
    assert c.covers[0].width == 1000


def test_matching_and_ranking():
    assert online.normalize("Beyoncé ft. JAY-Z") == "beyonce"
    assert online.normalize("Halo (Remastered)") == "halo"
    good = online.Candidate("x", title="Halo", artist="Beyoncé", length=261)
    live = online.Candidate("x", title="Halo (Live)", artist="Beyoncé", album="I Am... Yours (Live)", length=300)
    assert online.score(good, "Beyonce", "Halo", 261) > online.score(live, "Beyonce", "Halo", 261)


def test_guess_from_filename():
    assert online.guess_from_filename("Beyoncé - Halo.mp3") == ("Beyoncé", "Halo")
    assert online.guess_from_filename("03 - Halo.flac") == ("", "Halo")
    assert online.guess_from_filename("Beyoncé - I Am... Sasha Fierce - 02 - Halo.flac") == ("Beyoncé", "Halo")


def test_split_edition():
    assert online.split_edition("B'Day (Deluxe Edition)") == ("B'Day", "Deluxe Edition")
    assert online.split_edition("Rumours (Super Deluxe)") == ("Rumours", "Super Deluxe")
    assert online.split_edition("Lemonade") == ("Lemonade", "")


def test_lyrics_helpers():
    assert online.strip_timestamps("[ar:X]\n[00:01.20]Hello\n[00:03.00] it's me") == "Hello\nit's me"
    page = ('<div data-lyrics-container="true" class="x"><div data-exclude-from-selection="true">'
            '<span>5 Contributors</span></div>[Verse 1]<br/>Remember those walls I built?<br><i>Well</i>, '
            'baby they&#x27;re tumbling down</div><div>footer</div>')
    assert online.parse_genius_page(page) == ("[Verse 1]\nRemember those walls I built?\n"
                                              "Well, baby they're tumbling down")


def test_search_keeps_going_when_a_site_fails(monkeypatch):
    def broken(_a, _t):
        raise online.OnlineError("couldn't reach musicbrainz.org")

    monkeypatch.setattr(online, "SOURCES", {"iTunes": lambda a, t: online.parse_itunes(ITUNES), "MusicBrainz": broken})
    found, problems = online.search("Beyoncé", "Halo", 261)
    assert [c.source for c in found] == ["iTunes"] and problems == ["MusicBrainz: couldn't reach musicbrainz.org"]
