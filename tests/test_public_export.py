"""The public source export takes only listed paths and refuses to carry local material."""
from __future__ import annotations

import importlib.util
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("export_public_source", PROJECT / "scripts/export_public_source.py")
export = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(export)


def test_unlisted_paths_stay_out_and_the_public_readme_replaces_the_internal_one():
    names = ["CODEX_PROMPT.md", "README.md", "docs/LESSONS.md", "docs/MAC_MINI.md", "docs/MIGRATION.md", "reference/x.patch",
             "HANDOFF-x.local.md", "docs/PUBLIC_CONTAINER.md", "docs/PUBLIC_README.md", "src/horosa_public/server.py", "LICENSE"]
    assert export.selected(names) == {
        "docs/PUBLIC_CONTAINER.md": "docs/PUBLIC_CONTAINER.md",
        "docs/PUBLIC_README.md": "README.md",
        "src/horosa_public/server.py": "src/horosa_public/server.py",
        "LICENSE": "LICENSE",
    }


def test_scan_stops_on_local_material_and_passes_clean_files(tmp_path):
    (tmp_path / "clean.md").write_text("nothing local here", encoding="utf-8")
    (tmp_path / "leak.md").write_text(f"see {Path.home()}/notes", encoding="utf-8")
    (tmp_path / "pointer.md").write_text("see CODEX_PROMPT.md", encoding="utf-8")
    assert export.problems(tmp_path, {"clean.md": "clean.md"}) == []
    assert export.problems(tmp_path, {"leak.md": "leak.md"})
    assert export.problems(tmp_path, {"pointer.md": "pointer.md"})
