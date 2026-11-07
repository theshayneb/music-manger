import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from music_manager.core import plex


@pytest.fixture
def server(tmp_path):
    """A pretend Plex server with one music library holding two songs."""
    music = str(tmp_path / "Music")
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            seen.append(self.path)
            if self.headers.get("X-Plex-Token") != "secret":
                self.send_response(401)
                self.end_headers()
                return
            if self.path.startswith("/library/sections/7/all"):
                body = {"MediaContainer": {"totalSize": 2, "Metadata": [
                    {"ratingKey": "1", "viewCount": 41, "userRating": 8,
                     "Media": [{"Part": [{"file": os.path.join(music, "Beyoncé - Halo.mp3")}]}]},
                    {"ratingKey": "2", "Media": [{"Part": [{"file": "/elsewhere/Rilo Kiley - Portions for Foxes.mp3"}]}]},
                ]}}
            elif self.path.startswith("/library/sections/7/refresh"):
                body = {}
            elif self.path.startswith("/library/sections"):
                body = {"MediaContainer": {"Directory": [
                    {"key": "1", "type": "movie", "title": "Movies", "Location": [{"path": "C:/Movies"}]},
                    {"key": "7", "type": "artist", "title": "Music", "Location": [{"path": music}]}]}}
            else:
                body = {}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{httpd.server_port}", music, seen
    httpd.shutdown()


def test_play_counts_by_path_then_by_name(server):
    url, music, _seen = server
    tracks = plex.Plex(url, "secret").all_tracks()
    paths, names = plex.by_file(tracks)
    assert plex.match(os.path.join(music, "Beyoncé - Halo.mp3"), paths, names).plays == 41
    # Plex has it at another path (another drive letter, say): matched by file name.
    found = plex.match(os.path.join(music, "Rilo Kiley - Portions for Foxes.mp3"), paths, names)
    assert found is not None and found.plays == 0
    assert plex.match(os.path.join(music, "Unknown.mp3"), paths, names) is None


def test_wrong_token_and_rescan(server):
    url, music, seen = server
    with pytest.raises(plex.PlexError, match="token"):
        plex.Plex(url, "wrong").music_sections()
    assert plex.Plex(url, "secret").refresh_folder(music) == "Music"
    assert any(p.startswith("/library/sections/7/refresh?path=") for p in seen)
