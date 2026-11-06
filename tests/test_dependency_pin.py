"""The skill is a pinned release artefact: the lock must carry the wheel hash published in SHA256SUMS.txt."""
from __future__ import annotations

import tomllib
from pathlib import Path

import horosa_skill

ROOT = Path(__file__).resolve().parents[1]
SKILL_VERSION = "0.47.1"
# From https://github.com/Horace-Maxwell/horosa-skill/releases/download/v0.47.1/SHA256SUMS.txt
RELEASE_WHEEL_SHA256 = "4499ec6d265d8d5668ecf67af0fca41e0561b683e3bee3c824d437671e6b2c75"
WHEEL_URL = f"https://github.com/Horace-Maxwell/horosa-skill/releases/download/v{SKILL_VERSION}/horosa_skill-{SKILL_VERSION}-py3-none-any.whl"


def test_installed_skill_is_the_pinned_release():
    assert horosa_skill.__version__ == SKILL_VERSION


def test_lock_records_the_published_wheel_hash():
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert f'{{ url = "{WHEEL_URL}", hash = "sha256:{RELEASE_WHEEL_SHA256}" }}' in lock


def test_pyproject_pins_wheel_url_and_sdk_line():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = project["dependencies"]
    assert any(dep == f"horosa-skill @ {WHEEL_URL}" for dep in dependencies)
    assert any(dep.startswith("mcp[cli]==") for dep in dependencies)


def test_vendored_skill_files_record_their_tag():
    provenance = (ROOT / "vendor" / f"horosa-skill-v{SKILL_VERSION}" / "PROVENANCE.txt").read_text(encoding="utf-8")
    assert f"tag: v{SKILL_VERSION}" in provenance
    for name in ("scripts/runtime_seed.py", "contracts/runtime_python_lock.json"):
        assert f"{name} blob=" in provenance and f"{name} blob=missing" not in provenance
