#!/usr/bin/env python3
"""Copy the files of the public source release into an empty folder; stop if local material would go with them."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
FILES = {".gitignore", ".python-version", "LICENSE", "pyproject.toml", "uv.lock", "docs/PUBLIC_CONTAINER.md"}
FOLDERS = ("deploy/", "plugins/", "scripts/", "site/", "src/", "tests/", "vendor/")
RENAMED = {"docs/PUBLIC_README.md": "README.md"}
# These two carry the markers below on purpose, so the scan skips them.
SCAN_EXEMPT = {"scripts/export_public_source.py", "tests/test_public_export.py"}


def selected(names: list[str]) -> dict[str, str]:
    """Map source path to released path. Anything not listed stays private by default."""
    chosen = {name: name for name in names if name in FILES or name.startswith(FOLDERS)}
    chosen.update({name: RENAMED[name] for name in names if name in RENAMED})
    return chosen


def local_markers() -> tuple[str, ...]:
    return (str(Path.home()), Path.home().name, "HANDOFF-", "CODEX_PROMPT", "Desktop/")


def problems(root: Path, chosen: dict[str, str]) -> list[str]:
    found = []
    for name in sorted(chosen):
        if name in SCAN_EXEMPT:
            continue
        data = (root / name).read_bytes()
        found += [f"{name}: contains {marker!r}" for marker in local_markers() if marker.encode() in data]
    return found


def tracked_names() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=PROJECT, capture_output=True, text=True, check=True)
    return [name for name in listed.stdout.split("\0") if name and (PROJECT / name).is_file()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New or empty folder")
    destination = parser.parse_args().destination
    if destination.exists() and any(destination.iterdir()):
        print(f"{destination} is not empty", file=sys.stderr)
        return 2
    chosen = selected(tracked_names())
    found = problems(PROJECT, chosen)
    if found:
        print("\n".join(found), file=sys.stderr)
        return 1
    for source, released in chosen.items():
        target = destination / released
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT / source, target)
    print(f"exported {len(chosen)} files to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
