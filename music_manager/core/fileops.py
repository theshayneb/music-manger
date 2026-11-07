"""Delete (to the Recycle Bin), move and rename songs without overwriting anything.

A song's lyrics file ("<song>.txt" / ".lrc") always goes along with it."""

from __future__ import annotations

import errno
import os
import shutil
import time
from pathlib import Path

from send2trash import send2trash

from . import sidecar

IN_USE_RETRY_SECONDS = 3.0
INVALID_NAME_CHARS = set('<>:"/\\|?*')


def same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def unique_path(target: Path, taken: set[str] | None = None, ignore: str | None = None) -> Path:
    """target, or target with _1, _2 ... added if that name is used (by a file other than ignore)."""
    taken = taken or set()

    def used(p: Path) -> bool:
        if ignore and same_path(p, ignore):
            return False
        return p.exists() or os.path.normcase(str(p)) in taken

    if not used(target):
        return target
    n = 1
    while True:
        candidate = target.with_name(f"{target.stem}_{n}{target.suffix}")
        if not used(candidate):
            return candidate
        n += 1


def friendly_error(exc: OSError) -> str:
    if _is_in_use(exc):
        return "the file is in use by another program (close it there and try again)"
    return str(exc)


def _is_in_use(exc: OSError) -> bool:
    # Windows: 32 = sharing violation, 33 = lock violation, 5 = access denied (often a brief lock)
    return getattr(exc, "winerror", None) in (5, 32, 33) or isinstance(exc, PermissionError)


def _is_other_drive(exc: OSError) -> bool:
    return exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17


def _retry(fn, deadline: float):  # type: ignore[no-untyped-def]
    while True:
        try:
            return fn()
        except OSError as exc:
            if _is_in_use(exc) and time.monotonic() < deadline:
                time.sleep(0.25)
                continue
            raise


def move_file(src: str, dest: str, retry_seconds: float = IN_USE_RETRY_SECONDS) -> None:
    """Move one file. Across drives (e.g. external drive -> OneDrive) it's copied, the copy is
    checked, then the original is deleted; if that fails the copy is removed again, so a file
    never ends up in two places or gets lost."""
    deadline = time.monotonic() + retry_seconds
    try:
        _retry(lambda: os.rename(src, dest), deadline)
        return
    except OSError as exc:
        if not _is_other_drive(exc):
            raise
    shutil.copy2(src, dest)
    try:
        if os.path.getsize(dest) != os.path.getsize(src):
            raise OSError(f"the copy of {os.path.basename(src)} is incomplete")
        _retry(lambda: os.remove(src), deadline)
    except OSError:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise


def move_song(src: str, dest: str) -> str:
    """Move/rename a song and its lyrics files. Returns where the song ended up."""
    companions = sidecar.companion_moves(src, dest)
    move_file(src, dest)
    for old, new in companions:
        try:
            # Never overwrite: a lyrics file already named like the new song is left alone.
            if not same_path(old, new) and (not Path(new).exists() or os.path.normcase(old) == os.path.normcase(new)):
                move_file(old, new)
        except OSError:
            pass  # the song itself moved; a lyrics file that couldn't follow is not fatal
    return dest


def move(paths: list[str], folder: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Move songs into folder. Returns ({old: new}, [(path, error)])."""
    moved: dict[str, str] = {}
    failures = []
    for path in paths:
        src = Path(path)
        if same_path(src.parent, folder):
            continue
        try:
            dest = unique_path(Path(folder) / src.name)
            moved[path] = move_song(str(src), str(dest))
        except OSError as exc:
            failures.append((path, friendly_error(exc)))
    return moved, failures


def recycle(paths: list[str]) -> list[tuple[str, str]]:
    """Send songs (and their lyrics files) to the Recycle Bin. Returns [(path, error)]."""
    failures = []
    for path in paths:
        try:
            deadline = time.monotonic() + IN_USE_RETRY_SECONDS
            _retry(lambda p=path: send2trash(p), deadline)
        except OSError as exc:
            failures.append((path, friendly_error(exc)))
            continue
        for companion in sidecar.companions(path):
            try:
                send2trash(str(companion))
            except OSError:
                pass
    return failures


def safe_name(text: str) -> str:
    """Make text usable as a Windows file name: AC/DC -> AC-DC, "What?" -> What."""
    replacements = {"/": "-", "\\": "-", ":": " -", '"': "'", "|": "-"}
    out = "".join(replacements.get(c, "" if (c in INVALID_NAME_CHARS or ord(c) < 32) else c) for c in text)
    out = " ".join(out.split())
    return out.rstrip(". ").strip()


def check_new_name(path: str, new_name: str) -> str | None:
    """Problem with renaming path to new_name (in the same folder), or None if fine."""
    name = new_name.strip()
    if not name or name in (".", ".."):
        return "Please type a name."
    bad = sorted({c for c in name if c in INVALID_NAME_CHARS or ord(c) < 32})
    if bad:
        return "A file name can't contain " + " ".join(bad)
    if name.endswith((".", " ")):
        return "A file name can't end with a dot or a space."
    target = Path(path).with_name(name)
    if target.exists() and not same_path(target, path):
        return f"“{name}” already exists in this folder."
    return None


def rename(plan: dict[str, str]) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Carry out {old path: new path}. Returns ({old: new}, [(path, error)])."""
    done: dict[str, str] = {}
    failures = []
    for src, dest in plan.items():
        try:
            same_file = os.path.normcase(src) == os.path.normcase(dest)  # e.g. only the case changes
            if Path(dest).exists() and not same_file:
                raise FileExistsError(f"{os.path.basename(dest)} already exists")
            done[src] = move_song(src, dest)
        except OSError as exc:
            failures.append((src, friendly_error(exc)))
    return done, failures
