"""User settings, saved as JSON in %APPDATA%\\MusicManager\\settings.json."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "MusicManager"


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(Path.home(), ".config")
    return Path(base) / APP_NAME


def cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".cache")
    return Path(base) / APP_NAME


DEFAULT_RENAME_PATTERN = "{artist} - {title}"

DEFAULT_RENAME_PRESETS = [
    DEFAULT_RENAME_PATTERN,
    "{artist} - {album} - {track:02} - {title}",
    "{track:02} - {title}",
    "{title}",
]

# MP3 quality choices: key -> (label, ffmpeg arguments)
MP3_QUALITIES: dict[str, tuple[str, list[str]]] = {
    "V0": ("V0 (variable, ~245 kbps, recommended)", ["-q:a", "0"]),
    "V2": ("V2 (variable, ~190 kbps)", ["-q:a", "2"]),
    "320": ("320 kbps constant", ["-b:a", "320k"]),
    "256": ("256 kbps constant", ["-b:a", "256k"]),
}


@dataclass
class Settings:
    review_folder: str = ""   # Downloads: music being decided on
    library_folder: str = ""  # Music: music kept for good
    review_subfolders: bool = True
    library_subfolders: bool = False
    rename_pattern: str = DEFAULT_RENAME_PATTERN
    rename_presets: list[str] = field(default_factory=lambda: list(DEFAULT_RENAME_PRESETS))
    rename_on_keep: bool = True
    mp3_quality: str = "V0"
    recycle_after_convert: bool = True
    gain_on_keep: bool = True
    confirm_delete: bool = True
    autoplay_next: bool = True
    volume: int = 70
    theme: str = "dark"  # dark | light | system
    last_view: str = "review"
    column_state: dict[str, str] = field(default_factory=dict)
    recent_move_folders: list[str] = field(default_factory=list)
    ffmpeg_path: str = ""
    window_geometry: str = ""
    window_state: str = ""

    path: Path = field(default_factory=lambda: config_dir() / "settings.json", repr=False)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        settings = cls()
        if path:
            settings.path = path
        try:
            data = json.loads(settings.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings
        for key, value in data.items():
            if key == "path" or not hasattr(settings, key):
                continue
            if type(getattr(settings, key)) is type(value):
                setattr(settings, key, value)
        if settings.mp3_quality not in MP3_QUALITIES:
            settings.mp3_quality = "V0"
        return settings

    def save(self) -> None:
        data = asdict(self)
        data.pop("path", None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def remember_move_folder(self, folder: str) -> None:
        folders = [f for f in self.recent_move_folders if os.path.normcase(f) != os.path.normcase(folder)]
        self.recent_move_folders = [folder, *folders][:9]

    def remember_pattern(self, pattern: str) -> None:
        presets = [p for p in self.rename_presets if p != pattern]
        self.rename_presets = [pattern, *presets][:12]
