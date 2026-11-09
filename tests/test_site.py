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
inventory_spec = importlib.util.spec_from_file_location("privacy_inventory", PROJECT / "scripts/privacy_inventory.py")
privacy_inventory = importlib.util.module_from_spec(inventory_spec)
assert inventory_spec and inventory_spec.loader
inventory_spec.loader.exec_module(privacy_inventory)


def test_pages_pass_the_publish_check():
    assert verify_site.verify() == []


def test_a_placeholder_coming_back_blocks_publishing(tmp_path, monkeypatch):
    copy = tmp_path / "site"
    shutil.copytree(PROJECT / "site", copy)
    terms = copy / "terms.html"
    terms.write_text(terms.read_text(encoding="utf-8").replace("Hongxu Dong", "&lt;PUBLISHER_NAME&gt;"), encoding="utf-8")
    monkeypatch.setattr(verify_site, "SITE", copy)
    assert any("terms.html: placeholders remain" in problem for problem in verify_site.verify())


def test_files_github_pages_would_drop_block_publishing(tmp_path, monkeypatch):
    copy = tmp_path / "site"
    shutil.copytree(PROJECT / "site", copy)
    (copy / "style.css").rename(copy / "_style.css")
    monkeypatch.setattr(verify_site, "SITE", copy)
    problems = verify_site.verify()
    assert any("_style.css" in problem and "GitHub Pages" in problem for problem in problems)
    assert any("missing asset style.css" in problem for problem in problems)


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
    # The engine keeps a volatile result cache until its process ends (docs/PUBLIC_CONTAINER.md). The policy may only promise
    # "at most 24 hours" because the mini kit restarts the container every day at 04:00.
    privacy = (PROJECT / "site/privacy.html").read_text(encoding="utf-8")
    guide = (PROJECT / "docs/PUBLIC_CONTAINER.md").read_text(encoding="utf-8")
    installer = (PROJECT / "deploy/mini/install_daily_restart.sh").read_text(encoding="utf-8")
    assert "从不写入磁盘、数据库或日志" in privacy and "最长保留 24 小时" in privacy and "at most 24 hours" in privacy
    assert "不持久化请求缓存" in guide and "进程结束后该缓存消失" in guide and "install_daily_restart.sh" in guide
    assert "<key>Hour</key><integer>4</integer>" in installer and "restart</string>" in installer


def test_privacy_names_every_kind_of_input_the_tools_take():
    privacy = (PROJECT / "site/privacy.html").read_text(encoding="utf-8")
    for statement in ("两个人各自的出生资料", "问题文字", "确认备注", "粗略位置提示", "匿名会话 ID", "取整到 0.01°", "工具返回什么"):
        assert statement in privacy, statement


def test_privacy_table_drifting_from_the_contract_blocks_publishing(tmp_path, monkeypatch):
    copy = tmp_path / "site"
    shutil.copytree(PROJECT / "site", copy)
    contract = json.loads(privacy_inventory.CONTRACT.read_text(encoding="utf-8"))
    contract["tools"][0]["fields"].append("questionText")
    drifted = tmp_path / "public_surface.json"
    drifted.write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setattr(privacy_inventory, "CONTRACT", drifted)
    monkeypatch.setattr(verify_site, "SITE", copy)
    monkeypatch.setattr(verify_site, "_privacy_inventory", lambda: privacy_inventory)
    assert any("out of date with public_surface.json" in problem for problem in verify_site.verify())


def test_an_unclassified_new_field_blocks_publishing(tmp_path, monkeypatch):
    contract = json.loads(privacy_inventory.CONTRACT.read_text(encoding="utf-8"))
    contract["tools"][0]["fields"].append("subjectNickname")
    drifted = tmp_path / "public_surface.json"
    drifted.write_text(json.dumps(contract), encoding="utf-8")
    problems = privacy_inventory.check(contract_path=drifted)
    assert problems and "subjectNickname" in problems[0] and "classify" in problems[0]


def test_privacy_table_markers_missing_blocks_publishing(tmp_path, monkeypatch):
    copy = tmp_path / "site"
    shutil.copytree(PROJECT / "site", copy)
    page = copy / "privacy.html"
    page.write_text(page.read_text(encoding="utf-8").replace("<!-- inventory:end -->", ""), encoding="utf-8")
    assert any("markers missing" in problem for problem in privacy_inventory.check(page=page))
