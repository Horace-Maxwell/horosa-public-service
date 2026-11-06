#!/usr/bin/env python3
"""Verify the OpenAI plugin sources and build the submission ZIP.

`verify` applies the portal's final-submission rules we can check offline (field lengths, HTTPS URLs without credentials,
category list, exactly one remote MCP server, review cases 5 + 3, skill front matter, referenced assets, no placeholders,
no secrets) plus this project's own truth checks (manifest version = project version; the skill names only public tools).
`build` refuses to produce a ZIP while review cases are still drafts unless `--allow-draft-cases` is given.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT / "plugins" / "openai" / "horosa"
CATEGORIES = {"Productivity", "Creativity", "Developer Tools", "Business & Operations", "Data & Analytics", "Communication",
              "Education & Research", "Security", "Finance", "Healthcare", "Travel", "Entertainment", "Other"}
LIMITS = {"name": 64, "displayName": 30, "shortDescription": 30, "longDescription": 4000, "developerName": 80,
          "capabilities": (20, 120), "defaultPrompt": (3, 128), "skill_description": 1024, "zip_bytes": 100 * 1024 * 1024, "zip_entries": 5000}
PLACEHOLDER = re.compile(r"<[A-Z_]{3,}>")
SECRET = re.compile(r"(sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{20,})")
URL_FIELDS = ("websiteURL", "supportURL", "privacyPolicyURL", "termsOfServiceURL")


class PluginError(ValueError):
    pass


def _https(url: str, label: str) -> None:
    if not isinstance(url, str) or not url.startswith("https://") or "@" in url.split("//", 1)[1].split("/", 1)[0] or len(url) > 1024:
        raise PluginError(f"{label}: must be an https URL without credentials, at most 1024 characters")


def _square_svg(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")[:4000]
    width = re.search(r'width="(\d+)', text)
    height = re.search(r'height="(\d+)', text)
    if not width or not height or width.group(1) != height.group(1) or int(width.group(1)) < 48:
        raise PluginError(f"{path.name}: icon must be a square SVG of at least 48x48")


def public_tool_names() -> set[str]:
    contract = json.loads((PROJECT / "src/horosa_public/contracts/public_surface.json").read_text(encoding="utf-8"))
    return {item["name"] for item in contract["tools"]} | {contract["guidance"]["name"]}


def verify(source: Path, *, allow_draft_cases: bool = False) -> dict:
    plugin = json.loads((source / "plugin.json").read_text(encoding="utf-8"))
    raw = json.dumps(plugin, ensure_ascii=False)
    if PLACEHOLDER.search(raw):
        raise PluginError("plugin.json still contains placeholders: " + ", ".join(sorted(set(PLACEHOLDER.findall(raw)))))
    if SECRET.search(raw):
        raise PluginError("plugin.json contains something that looks like a secret")
    version = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if plugin.get("version") != version:
        raise PluginError(f"plugin.json version {plugin.get('version')!r} differs from the project version {version!r}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", plugin.get("name", "")):
        raise PluginError("name: lowercase letters, digits and hyphens, at most 64 characters")
    if not plugin.get("description"):
        raise PluginError("description is required")
    openai = plugin.get("extensions", {}).get("com.openai", {})
    interface, review = openai.get("interface", {}), openai.get("review", {})
    for key in ("displayName", "shortDescription", "longDescription", "developerName"):
        value = interface.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > LIMITS[key]:
            raise PluginError(f"interface.{key}: required, at most {LIMITS[key]} characters")
        if key in ("displayName", "shortDescription") and "\n" in value:
            raise PluginError(f"interface.{key}: must be a single line")
    if interface.get("category") not in CATEGORIES:
        raise PluginError("interface.category: not one of the portal categories")
    capabilities = interface.get("capabilities", [])
    if not isinstance(capabilities, list) or len(capabilities) > LIMITS["capabilities"][0] or any(not isinstance(c, str) or not c or len(c) > LIMITS["capabilities"][1] for c in capabilities):
        raise PluginError("interface.capabilities: at most 20 non-empty strings of at most 120 characters")
    prompts = interface.get("defaultPrompt", [])
    if not isinstance(prompts, list) or len(prompts) > LIMITS["defaultPrompt"][0] or len(set(prompts)) != len(prompts) or any(not isinstance(p, str) or not p.strip() or "\n" in p or len(p) > LIMITS["defaultPrompt"][1] or "@" in p for p in prompts):
        raise PluginError("interface.defaultPrompt: at most 3 unique single-line prompts of at most 128 characters, without @mentions")
    for key in URL_FIELDS:
        _https(interface.get(key, ""), f"interface.{key}")
    _https(plugin.get("homepage", ""), "homepage")
    for key in ("composerIcon", "composerIconDark", "logo", "logoDark"):
        reference = interface.get(key)
        if not reference or not reference.startswith("./"):
            raise PluginError(f"interface.{key}: relative ./ path required")
        asset = source / reference[2:]
        if not asset.is_file():
            raise PluginError(f"interface.{key}: {reference} is missing")
        if asset.suffix.lower() == ".svg":
            _square_svg(asset)
        elif asset.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise PluginError(f"interface.{key}: unsupported image type")
        if asset.stat().st_size > 5 * 1024 * 1024:
            raise PluginError(f"interface.{key}: image larger than 5 MiB")
    skill_ref = openai.get("onboardingSkill", "")
    skill_path = source / skill_ref[2:] if skill_ref.startswith("./") else None
    if skill_path is None or not skill_path.is_file() or skill_path.parent.parent.name != "skills" or skill_path.name != "SKILL.md":
        raise PluginError("onboardingSkill must point at ./skills/<name>/SKILL.md")
    skill = skill_path.read_text(encoding="utf-8")
    front = re.match(r"^---\n(.*?)\n---\n", skill, re.S)
    if not front:
        raise PluginError("SKILL.md: front matter missing")
    fields = dict(re.findall(r"^(name|description):\s*(.+)$", front.group(1), re.M))
    if fields.get("name") != skill_path.parent.name or not fields.get("description") or len(fields["description"]) > LIMITS["skill_description"]:
        raise PluginError("SKILL.md: front matter needs name = directory name and a description of at most 1024 characters")
    unknown = sorted(set(re.findall(r"`(horosa_[a-z0-9_]+)`", skill)) - public_tool_names())
    if unknown:
        raise PluginError("SKILL.md names tools that are not on the public surface: " + ", ".join(unknown))
    for forbidden in ("horosa_tool_run", "horosa_dispatch", "horosa_memory", "horosa_report", "uv run", "horosa-skill install", "Claude"):
        if forbidden in skill:
            raise PluginError(f"SKILL.md: must not mention {forbidden!r}")
    cases = review.get("test_cases", {})
    positive, negative = cases.get("positive", []), cases.get("negative", [])
    if len(positive) != 5 or len(negative) != 3:
        raise PluginError("review.test_cases: exactly 5 positive and 3 negative cases are required")
    for case in positive:
        if not all(isinstance(case.get(k), str) and case[k].strip() for k in ("description", "prompt", "tools_triggered", "expected_behavior")):
            raise PluginError("positive case: description, prompt, tools_triggered and expected_behavior are required strings")
        if {name.strip() for name in case["tools_triggered"].split(",")} - public_tool_names():
            raise PluginError("positive case names a tool that is not public")
    for case in negative:
        if not all(isinstance(case.get(k), str) and case[k].strip() for k in ("description", "prompt")):
            raise PluginError("negative case: description and prompt are required")
    drafts = [c for c in positive + negative if str(c.get("expected_behavior", "")).startswith("DRAFT")]
    if drafts and not allow_draft_cases:
        raise PluginError(f"{len(drafts)} review cases are still drafts; run them against the live endpoint and remove the DRAFT prefix")
    _https(review.get("demo_recording_url", ""), "review.demo_recording_url")
    if review.get("commerce") is not False:
        raise PluginError("review.commerce must be false (no digital goods or subscriptions are sold)")
    if not openai.get("publication", {}).get("release_notes"):
        raise PluginError("publication.release_notes is required")
    countries = openai["publication"].get("countries")
    if not isinstance(countries, list) or not all(isinstance(item, str) for item in countries):
        raise PluginError("publication.countries must be a list ([] = no restriction)")
    mcp = json.loads((source / "mcp.json").read_text(encoding="utf-8"))
    servers = mcp.get("mcpServers", {})
    if len(servers) != 1:
        raise PluginError("mcp.json: exactly one remote MCP server")
    (server_name, server), = servers.items()
    if server.get("type") != "streamable-http" or not server.get("url", "").endswith("/mcp"):
        raise PluginError("mcp.json: the server must be streamable-http with a URL ending in /mcp")
    _https(server["url"], f"mcpServers.{server_name}.url")
    for forbidden in ("apps", ".app.json", "hooks", ".mcp.json"):
        if (source / forbidden).exists():
            raise PluginError(f"{forbidden} must not be part of a skills + remote-MCP plugin")
    for path in source.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".json", ".txt", ".yaml", ".yml"} and SECRET.search(path.read_text(encoding="utf-8", errors="replace")):
            raise PluginError(f"{path.relative_to(source)}: contains something that looks like a secret")
    return {"name": plugin["name"], "version": version, "server": server["url"], "skill": str(skill_path.relative_to(source)), "draft_cases": len(drafts)}


def build(source: Path, output: Path, *, allow_draft_cases: bool = False) -> Path:
    summary = verify(source, allow_draft_cases=allow_draft_cases)
    output.parent.mkdir(parents=True, exist_ok=True)
    root = summary["name"]
    entries = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_dir() or path.name == ".DS_Store" or "__pycache__" in path.parts:
                continue
            archive.write(path, f"{root}/{path.relative_to(source).as_posix()}")
            entries += 1
    if entries > LIMITS["zip_entries"] or output.stat().st_size > LIMITS["zip_bytes"]:
        output.unlink()
        raise PluginError("ZIP exceeds the portal limits (5000 entries / 100 MB)")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["verify", "build"])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=PROJECT / "build" / "horosa-plugin.zip")
    parser.add_argument("--allow-draft-cases", action="store_true", help="Package even though review cases are still drafts (local testing only)")
    args = parser.parse_args()
    try:
        if args.command == "verify":
            print(json.dumps(verify(args.source, allow_draft_cases=args.allow_draft_cases), ensure_ascii=False))
        else:
            print(build(args.source, args.output, allow_draft_cases=args.allow_draft_cases))
    except PluginError as exc:
        print(f"plugin check failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
