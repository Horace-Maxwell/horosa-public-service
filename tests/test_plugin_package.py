"""The plugin ZIP sources satisfy the portal's final-submission rules we can check offline; negative controls included."""
from __future__ import annotations

import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "plugins/openai/horosa"


def _load():
    spec = importlib.util.spec_from_file_location("build_openai_plugin", PROJECT / "scripts/build_openai_plugin.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


builder = _load()


@pytest.fixture
def filled(tmp_path):
    """A copy of the sources with the human-gated placeholders filled, so the structural checks can run."""
    copy = tmp_path / "horosa"
    shutil.copytree(SOURCE, copy)
    text = (copy / "plugin.json").read_text(encoding="utf-8")
    text = text.replace("<DEMO_VIDEO_URL>", "https://example.org/demo")
    (copy / "plugin.json").write_text(text, encoding="utf-8")
    return copy


def _mutate(copy: Path, change) -> None:
    data = json.loads((copy / "plugin.json").read_text(encoding="utf-8"))
    change(data)
    (copy / "plugin.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_sources_are_blocked_until_the_human_gates_are_filled():
    with pytest.raises(builder.PluginError, match="placeholders"):
        builder.verify(SOURCE, allow_draft_cases=True)


def test_filled_sources_verify_and_build_with_draft_cases_only_when_allowed(filled, tmp_path):
    with pytest.raises(builder.PluginError, match="drafts"):
        builder.verify(filled)
    summary = builder.verify(filled, allow_draft_cases=True)
    assert summary["server"] == "https://mcp.horosa.org/mcp" and summary["draft_cases"] == 8
    archive = builder.build(filled, tmp_path / "out" / "plugin.zip", allow_draft_cases=True)
    names = zipfile.ZipFile(archive).namelist()
    assert "horosa/plugin.json" in names and "horosa/mcp.json" in names and "horosa/skills/horosa/SKILL.md" in names
    assert all(name.startswith("horosa/") for name in names) and len(names) < 50


def test_manifest_matches_truth_files(filled):
    plugin = json.loads((filled / "plugin.json").read_text(encoding="utf-8"))
    contract = json.loads((PROJECT / "src/horosa_public/contracts/public_surface.json").read_text(encoding="utf-8"))
    notes = plugin["extensions"]["com.openai"]["publication"]["release_notes"]
    assert f"{len(contract['tools'])} calculation tools" in notes
    skill = (filled / "skills/horosa/SKILL.md").read_text(encoding="utf-8")
    for item in contract["tools"]:
        assert f"`{item['name']}`" in skill
    assert "`horosa_agent_guidance`" in skill
    interface = plugin["extensions"]["com.openai"]["interface"]
    assert len(interface["shortDescription"]) <= 30 and len(interface["displayName"]) <= 30
    for word in ("free", "best", "most accurate", "$"):
        assert word not in interface["longDescription"].lower()


@pytest.mark.parametrize("mutation", [
    "long_short_description", "bad_category", "http_url", "credential_url", "four_cases", "two_servers", "sse_server",
    "unknown_tool_in_case", "tools_triggered_array", "countries_not_a_list", "missing_icon", "commerce_true", "version_drift", "too_many_prompts", "skill_names_hidden_tool",
])
def test_negative_controls(filled, mutation):
    if mutation == "long_short_description":
        _mutate(filled, lambda d: d["extensions"]["com.openai"]["interface"].__setitem__("shortDescription", "x" * 31))
    elif mutation == "bad_category":
        _mutate(filled, lambda d: d["extensions"]["com.openai"]["interface"].__setitem__("category", "Astrology"))
    elif mutation == "http_url":
        _mutate(filled, lambda d: d["extensions"]["com.openai"]["interface"].__setitem__("privacyPolicyURL", "http://horosa.org/privacy"))
    elif mutation == "credential_url":
        _mutate(filled, lambda d: d["extensions"]["com.openai"]["interface"].__setitem__("supportURL", "https://user:pw@horosa.org/support"))
    elif mutation == "four_cases":
        _mutate(filled, lambda d: d["extensions"]["com.openai"]["review"]["test_cases"]["positive"].pop())
    elif mutation == "two_servers":
        mcp = json.loads((filled / "mcp.json").read_text(encoding="utf-8"))
        mcp["mcpServers"]["second"] = {"type": "streamable-http", "url": "https://other.example/mcp"}
        (filled / "mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
    elif mutation == "sse_server":
        mcp = json.loads((filled / "mcp.json").read_text(encoding="utf-8"))
        mcp["mcpServers"]["horosa"]["type"] = "sse"
        (filled / "mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
    elif mutation == "unknown_tool_in_case":
        _mutate(filled, lambda d: d["extensions"]["com.openai"]["review"]["test_cases"]["positive"][0].__setitem__("tools_triggered", "horosa_astro_chart, horosa_tool_run"))
    elif mutation == "tools_triggered_array":
        _mutate(filled, lambda d: d["extensions"]["com.openai"]["review"]["test_cases"]["positive"][0].__setitem__("tools_triggered", ["horosa_astro_chart"]))
    elif mutation == "countries_not_a_list":
        _mutate(filled, lambda d: d["extensions"]["com.openai"]["publication"].__setitem__("countries", "US"))
    elif mutation == "missing_icon":
        (filled / "assets/icon.svg").unlink()
    elif mutation == "commerce_true":
        _mutate(filled, lambda d: d["extensions"]["com.openai"]["review"].__setitem__("commerce", True))
    elif mutation == "version_drift":
        _mutate(filled, lambda d: d.__setitem__("version", "9.9.9"))
    elif mutation == "too_many_prompts":
        _mutate(filled, lambda d: d["extensions"]["com.openai"]["interface"].__setitem__("defaultPrompt", ["a", "b", "c", "d"]))
    elif mutation == "skill_names_hidden_tool":
        skill = filled / "skills/horosa/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nUse `horosa_tool_run` for anything else.\n", encoding="utf-8")
    with pytest.raises(builder.PluginError):
        builder.verify(filled, allow_draft_cases=True)
