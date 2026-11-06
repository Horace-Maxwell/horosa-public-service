#!/usr/bin/env python3
"""Check the horosa.org pages before publishing: placeholders replaced, required statements present, one publisher name."""
from __future__ import annotations

import re
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "site"
PLACEHOLDER = re.compile(r"&lt;[A-Z_]{3,}&gt;|<[A-Z_]{3,}>")
REQUIRED = {
    "index.html": ["AGPL-3.0", "不存储", "娱乐参考"],
    "privacy.html": ["不存储请求内容", "Cloudflare", "当前位置", "13 岁"],
    "terms.html": ["AGPL-3.0", "不构成医疗、法律、财务", "按现状提供"],
    "support.html": ["mailto:", "issues", "healthz"],
}


def verify(*, allow_placeholders: bool = False) -> list[str]:
    problems: list[str] = []
    publishers: set[str] = set()
    for name, needles in REQUIRED.items():
        path = SITE / name
        if not path.is_file():
            problems.append(f"{name}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                problems.append(f"{name}: missing statement {needle!r}")
        if "<script" in text.lower():
            problems.append(f"{name}: scripts are not allowed")
        if not allow_placeholders and PLACEHOLDER.search(text):
            problems.append(f"{name}: placeholders remain: {sorted(set(PLACEHOLDER.findall(text)))}")
        match = re.search(r'class="publisher">([^<]+)<', text)
        if match:
            publishers.add(match.group(1))
        for asset in re.findall(r'(?:href|src)="/([\w.-]+\.(?:css|svg))"', text):
            if not (SITE / asset).is_file():
                problems.append(f"{name}: missing asset {asset}")
    problems += [f"{path.name}: GitHub Pages does not publish names starting with _" for path in sorted(SITE.iterdir()) if path.name.startswith("_")]
    if len(publishers) > 1:
        problems.append(f"publisher name differs between pages: {sorted(publishers)}")
    if (SITE / "CNAME").read_text(encoding="utf-8").strip() != "horosa.org":
        problems.append("CNAME must be horosa.org")
    return problems


if __name__ == "__main__":
    issues = verify(allow_placeholders="--allow-placeholders" in sys.argv)
    for issue in issues:
        print(issue, file=sys.stderr)
    raise SystemExit(2 if issues else 0)
