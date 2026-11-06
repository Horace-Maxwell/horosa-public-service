"""The compliance pages say what the service actually does, carry the real publisher, and block publishing on any placeholder."""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_site", PROJECT / "scripts/verify_site.py")
verify_site = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(verify_site)


def test_pages_pass_the_publish_check():
    assert verify_site.verify() == []


def test_a_placeholder_coming_back_blocks_publishing(tmp_path, monkeypatch):
    copy = tmp_path / "site"
    shutil.copytree(PROJECT / "site", copy)
    terms = copy / "terms.html"
    terms.write_text(terms.read_text(encoding="utf-8").replace("Hongxu Dong", "&lt;PUBLISHER_NAME&gt;"), encoding="utf-8")
    monkeypatch.setattr(verify_site, "SITE", copy)
    assert any("terms.html: placeholders remain" in problem for problem in verify_site.verify())


def test_site_and_manifest_name_the_same_publisher_and_contact():
    manifest = json.loads((PROJECT / "plugins/openai/horosa/plugin.json").read_text(encoding="utf-8"))
    name, email = manifest["author"]["name"], manifest["author"]["email"]
    assert manifest["extensions"]["com.openai"]["interface"]["developerName"] == name
    for page in ("index", "privacy", "terms", "support"):
        assert f'class="publisher">{name}<' in (PROJECT / f"site/{page}.html").read_text(encoding="utf-8")
    assert f'href="mailto:{email}"' in (PROJECT / "site/support.html").read_text(encoding="utf-8")


def test_terms_and_manifest_offer_the_service_source_and_the_licence_is_present():
    service = "https://github.com/Horace-Maxwell/horosa-public-service"
    manifest = json.loads((PROJECT / "plugins/openai/horosa/plugin.json").read_text(encoding="utf-8"))
    terms = (PROJECT / "site/terms.html").read_text(encoding="utf-8")
    assert manifest["repository"] == service
    assert f'href="{service}"' in terms and 'href="https://github.com/Horace-Maxwell/horosa-skill"' in terms
    assert (PROJECT / "LICENSE").read_text(encoding="utf-8").lstrip().startswith("GNU AFFERO GENERAL PUBLIC LICENSE")


def test_privacy_text_matches_the_container_facts():
    privacy = (PROJECT / "site/privacy.html").read_text(encoding="utf-8")
    guide = (PROJECT / "docs/PUBLIC_CONTAINER.md").read_text(encoding="utf-8")
    assert "不存储请求内容" in privacy and "进程结束即消失" in privacy
    assert "不持久化请求缓存" in guide and "进程结束后该缓存消失" in guide
