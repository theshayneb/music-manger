"""Real requests to the sites. Only run when MUSIC_MANAGER_LIVE=1 (GitHub Actions does, and prints what came back)."""

import os

import pytest

from music_manager.core import online

pytestmark = pytest.mark.skipif(os.environ.get("MUSIC_MANAGER_LIVE") != "1", reason="set MUSIC_MANAGER_LIVE=1")


@pytest.mark.parametrize("name", list(online.SOURCES))
def test_each_tag_source(name):
    found = online.SOURCES[name]("Beyoncé", "Halo")
    for c in found[:3]:
        print(f"{name}: {c.fields()} covers={[x.url for x in c.covers]} detail={c.detail!r}")
    assert any(online.similarity(c.title, "Halo") > 0.9 and c.album and c.year for c in found)


def test_search_all():
    found, problems = online.search("Rilo Kiley", "Portions for Foxes", 202)
    print("problems:", problems)
    for c in found[:5]:
        print(c.score, c.source, c.fields())
    assert found and online.similarity(found[0].album, "More Adventurous") > 0.9


def test_covers_download():
    found, _ = online.search("Beyoncé", "Halo")
    covers = online.covers_for(found, 6)
    sizes = []
    for cover in covers:
        try:
            data = online.get_image(cover.url)
        except online.OnlineError as exc:
            print(cover.source, "failed:", exc)
            continue
        sizes.append((cover.source, len(data)))
    print(sizes)
    assert any(size > 20_000 for _s, size in sizes)


@pytest.mark.parametrize("fn", [online.lyrics_lrclib, online.lyrics_genius, online.lyrics_ovh])
def test_each_lyrics_source(fn):
    try:
        found = fn("Beyoncé", "Halo")
    except online.OnlineError as exc:
        pytest.skip(f"{fn.__name__}: {exc}")
    print(fn.__name__, repr(found.text[:300]) if found else None)
    assert found and "remember" in found.text.lower()
