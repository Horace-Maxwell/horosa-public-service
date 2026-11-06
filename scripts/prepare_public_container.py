#!/usr/bin/env python3
"""Prepare a pinned Linux/amd64 build context for the public container, then (inside the image) install it.

Inputs are only released artefacts and this repository: the horosa-skill runtime seed and wheel from GitHub
Releases (sha256 pinned in contracts/public_container.json), Node and Temurin archives, two sdists built
from source, and this project's own wheel. The skill's source tree and vendor/ runtime sources are never read.

Ported from horosa-skill branch codex/chatgpt-plugin (052ce55) scripts/prepare_public_container.py.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

PROJECT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT / "src" / "horosa_public" / "contracts" / "public_container.json"
MARKER = ".horosa-public-context"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def vendor_dir(contract: dict) -> Path:
    return PROJECT / contract["skill"]["vendor_dir"]


def check_docker_space(contract: dict) -> dict:
    """Read the selected Docker daemon's free space; never pull or clean data."""
    required = contract.get("minimum_docker_free_bytes")
    if type(required) is not int or required <= 0:
        raise ValueError("磁盘空间阈值无效 / Invalid Docker disk-space threshold")
    result = {"ok": False, "check": "docker_free_space", "required_bytes": required}
    probe = (
        "import json,shutil; usage=shutil.disk_usage('/'); "
        "print(json.dumps(dict(zip(('total_bytes','used_bytes','free_bytes'),usage))))"
    )
    command = [
        "docker", "run", "--rm", "--pull=never", "--read-only", "--network", "none",
        "--platform", contract["platform"], "--user", f"{contract['uid']}:{contract['uid']}",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--entrypoint", "python",
        contract["python_image"], "-c", probe,
    ]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=45)
        usage = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return result | {
            "code": "public.container.disk_probe_failed",
            "message": "无法读取 Docker 空间；请检查所选 daemon 与已缓存的固定基础镜像。 / Could not read Docker free space; check the selected daemon and the cached pinned base image.",
        }
    keys = ("total_bytes", "used_bytes", "free_bytes")
    if (
        not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0 for key in keys)
        or usage["total_bytes"] == 0 or usage["used_bytes"] + usage["free_bytes"] > usage["total_bytes"]
    ):
        return result | {
            "code": "public.container.disk_probe_invalid",
            "message": "Docker 空间返回值无效，已拒绝继续。 / Invalid Docker disk-space response; refused to continue.",
        }
    result.update({key: usage[key] for key in keys})
    result["ok"] = usage["free_bytes"] >= required
    result["code"] = "public.container.disk_space_ok" if result["ok"] else "public.container.disk_space_insufficient"
    result["message"] = (
        "空间预检通过；这不是容量预留。 / Free-space preflight passed; it does not reserve capacity."
        if result["ok"] else
        "Docker 空闲空间不足；请确认构建环境或由用户安排清理，本工具不自动删除任何数据。 / Docker free space is insufficient; confirm the build environment or arrange a cleanup; this tool deletes nothing."
    )
    return result


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verify_artifact(path: Path, specification: dict) -> None:
    if not SHA256.fullmatch(specification.get("sha256", "")):
        raise ValueError("缺少固定校验值 / Missing pinned SHA-256")
    if not path.is_file() or digest(path) != specification["sha256"]:
        raise ValueError("构建输入校验失败 / Build input checksum mismatch")
    if specification.get("size") and path.stat().st_size != specification["size"]:
        raise ValueError("构建输入大小不符 / Build input size mismatch")


def fetch_artifact(specification: dict, cache: Path, *, offline: bool) -> Path:
    target = cache / specification["url"].rsplit("/", 1)[-1]
    if target.is_file():
        verify_artifact(target, specification)
        return target
    if offline:
        raise ValueError("离线构建缺少归档 / Offline build archive is missing")
    if not specification["url"].startswith("https://"):
        raise ValueError("构建下载必须使用 HTTPS / Build downloads require HTTPS")
    cache.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    try:
        with urllib.request.urlopen(specification["url"], timeout=120) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output)
        verify_artifact(partial, specification)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def _seed_helpers(contract: dict):
    path = vendor_dir(contract) / "scripts" / "runtime_seed.py"
    spec = importlib.util.spec_from_file_location("public_runtime_seed", path)
    if spec is None or spec.loader is None:
        raise ValueError("缺少种子构建工具 / Missing seed build helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def locked_mcp_requirements(lock_path: Path, root: str = "horosa-public") -> list[str]:
    """Export the Linux CPython 3.12 runtime closure of this project with hashes from uv.lock.

    The root project itself is excluded (its wheel is built locally); URL distributions such as the
    released horosa-skill wheel are emitted as `name @ url --hash=…` so pip can enforce the pin.
    """
    try:
        from packaging.markers import Marker
    except ImportError:
        from pip._vendor.packaging.markers import Marker
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    environment = {
        "python_version": "3.12", "python_full_version": "3.12.15", "sys_platform": "linux",
        "os_name": "posix", "platform_machine": "x86_64", "platform_system": "Linux",
        "implementation_name": "cpython", "platform_python_implementation": "CPython", "extra": "",
    }
    packages = {}
    for package in lock["package"]:
        markers = package.get("resolution-markers", [])
        if markers and not any(Marker(marker).evaluate(environment) for marker in markers):
            continue
        if package["name"] in packages:
            raise ValueError("锁文件包解析不唯一 / Ambiguous locked package")
        packages[package["name"]] = package
    todo = [(root, ())]
    visited = set()
    selected = set()
    while todo:
        name, extras = todo.pop()
        key = (name, extras)
        if key in visited:
            continue
        visited.add(key)
        package = packages[name]
        dependencies = list(package.get("dependencies", []))
        for extra in extras:
            dependencies.extend(package.get("optional-dependencies", {}).get(extra, []))
        for dependency in dependencies:
            if dependency.get("marker") and not Marker(dependency["marker"]).evaluate(environment):
                continue
            todo.append((dependency["name"], tuple(dependency.get("extra", []))))
        if name != root:
            selected.add(name)
    rows = []
    for name in sorted(selected):
        package = packages[name]
        wheels = package.get("wheels", [])
        hashes = sorted({wheel["hash"] for wheel in wheels})
        if not hashes:
            raise ValueError("MCP 锁文件没有 wheel 校验值 / MCP lock lacks wheel hashes")
        if "url" in package.get("source", {}):
            if len(wheels) != 1 or wheels[0]["url"] != package["source"]["url"]:
                raise ValueError("URL 依赖必须恰好一个 wheel / URL dependency must have exactly one wheel")
            rows.append(f"{name} @ {package['source']['url']} --hash={hashes[0]}")
        else:
            rows.append(f"{name}=={package['version']} " + " ".join(f"--hash={value}" for value in hashes))
    return rows


def build_project_wheel(output: Path) -> Path:
    """Build this project's own wheel into the context (installed with --no-deps in the image)."""
    output.mkdir(parents=True, exist_ok=True)
    _run(["uv", "build", "--wheel", "--out-dir", str(output), str(PROJECT)])
    wheels = sorted(output.glob("horosa_public-*.whl"))
    if len(wheels) != 1:
        raise ValueError("本项目 wheel 构建结果不唯一 / Expected exactly one horosa_public wheel")
    return wheels[0]


def prepare(output: Path, seed_archive: Path | None, *, offline: bool, force: bool) -> None:
    contract = load_contract()
    cache = PROJECT / "build" / "public-downloads"
    seed_archive = seed_archive or fetch_artifact(contract["seed"], cache, offline=offline)
    verify_artifact(seed_archive, contract["seed"])
    seed = _seed_helpers(contract)
    seed.verify_seed(seed_archive, expected_version=contract["seed"]["version"])
    if output.exists():
        if not force or not (output / MARKER).is_file():
            raise ValueError("输出目录已存在；只允许重建本工具的目录 / Output exists; only a marked context can be rebuilt")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    (output / MARKER).write_text("public-container-v2\n", encoding="utf-8")
    tree = seed.materialize_seed(seed_archive, PROJECT / "build" / "public-seed")
    seed.copy_platform_tree(tree, output / "payload", launchers="public", os_dir="linux")
    if not (output / "payload" / "horosa-core-js" / "node_modules").is_dir():
        raise ValueError("种子缺少 horosa-core-js/node_modules / Seed lacks horosa-core-js/node_modules")
    if not (output / "payload" / contract["ephemeris_path"]).is_dir():
        raise ValueError("种子缺少 Swiss Ephemeris 文件 / Seed lacks the Swiss Ephemeris files")
    # Reuse only pure seed dependencies; native macOS binaries never enter the image.
    lock = seed.load_lock(vendor_dir(contract) / "contracts" / "runtime_python_lock.json")
    seed.copy_pure_site_packages(tree, lock, output / "chart-pure")
    native_wheels = [row for row in lock["native"] if row.split("==")[0].lower() not in contract["source_builds"]]
    (output / "chart-native.txt").write_text("\n".join(native_wheels) + "\n", encoding="utf-8")
    (output / "mcp-requirements.txt").write_text("\n".join(locked_mcp_requirements(PROJECT / "uv.lock")) + "\n", encoding="utf-8")
    build_project_wheel(output / "public-wheel")
    for filename in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        source = PROJECT / filename
        if source.is_file():
            shutil.copy2(source, output / filename)
    for filename in ("Dockerfile", "entrypoint.py", "log4j2.xml"):
        shutil.copy2(PROJECT / "deploy" / "public" / filename, output / filename)
    shutil.copy2(Path(__file__), output / "prepare_public_container.py")
    for filename in ("runtime_seed.py", "verify_runtime_release.py"):
        shutil.copy2(vendor_dir(contract) / "scripts" / filename, output / filename)
    (output / "public_container.json").write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "runtime_python_lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    for name in ("node", "java"):
        artifact = fetch_artifact(contract[name], cache, offline=offline)
        shutil.copy2(artifact, output / f"{name}.tar.gz")
    for section in ("source_builds", "build_wheels"):
        (output / section).mkdir()
        for specification in contract[section].values():
            artifact = fetch_artifact(specification, cache, offline=offline)
            shutil.copy2(artifact, output / section / artifact.name)
    # Runtime caches are never inherited, even if a future seed contains one.
    for cache_dir in list((output / "payload").rglob(".horosa-cache")):
        shutil.rmtree(cache_dir)
    print(f"Prepared {output}; docker build --platform linux/amd64 -t horosa-public:local {output}")


def _run(args: list[str], **kwargs) -> None:
    subprocess.run(args, check=True, text=True, encoding="utf-8", errors="replace", **kwargs)


def install(context: Path) -> None:
    """Build-stage only; called inside the pinned Linux/amd64 image."""
    import platform
    import venv

    if sys.platform != "linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise ValueError("必须在 Linux x86_64 构建 / Build requires Linux x86_64")
    contract = json.loads((context / "public_container.json").read_text(encoding="utf-8"))
    root = Path("/opt/horosa")
    shutil.copytree(context / "payload", root, dirs_exist_ok=True)
    for name in ("node", "java"):
        archive = context / f"{name}.tar.gz"
        verify_artifact(archive, contract[name])
        destination = root / name
        destination.mkdir()
        with tarfile.open(archive) as bundle:
            bundle.extractall(destination, filter="data")
        children = list(destination.iterdir())
        if len(children) != 1 or not children[0].is_dir():
            raise ValueError("原生归档结构无效 / Invalid native archive layout")
        extracted = children[0]
        for child in list(extracted.iterdir()):
            child.rename(destination / child.name)
        extracted.rmdir()
    for name in ("chart-venv", "mcp-venv"):
        venv.create(root / name, with_pip=True)
    chart_site = root / "chart-venv/lib/python3.12/site-packages"
    shutil.copytree(context / "chart-pure", chart_site, dirs_exist_ok=True)
    chart_python = str(root / "chart-venv/bin/python")
    mcp_python = str(root / "mcp-venv/bin/python")
    build_wheels = []
    for specification in contract["build_wheels"].values():
        artifact = context / "build_wheels" / specification["url"].rsplit("/", 1)[-1]
        verify_artifact(artifact, specification)
        build_wheels.append(str(artifact))
    _run([chart_python, "-m", "pip", "install", "--no-cache-dir", "--no-deps", "--no-index", *build_wheels])
    built_wheels = context / "built-wheels"
    built_wheels.mkdir()
    for specification in contract["source_builds"].values():
        artifact = context / "source_builds" / specification["url"].rsplit("/", 1)[-1]
        verify_artifact(artifact, specification)
        _run([chart_python, "-m", "pip", "wheel", "--no-cache-dir", "--no-deps", "--no-index", "--no-build-isolation",
              "--wheel-dir", str(built_wheels), str(artifact)])
    _run([chart_python, "-m", "pip", "install", "--no-cache-dir", "--no-deps", "--no-index", *map(str, sorted(built_wheels.glob("*.whl")))])
    _run([chart_python, "-m", "pip", "install", "--no-cache-dir", "--no-deps", "--only-binary=:all:", "-r", str(context / "chart-native.txt")])
    # The released skill wheel and the locked closure are hash-checked; this project's wheel is installed from the context.
    _run([mcp_python, "-m", "pip", "install", "--no-cache-dir", "--no-deps", "--require-hashes", "--only-binary=:all:", "-r", str(context / "mcp-requirements.txt")])
    project_wheels = sorted((context / "public-wheel").glob("horosa_public-*.whl"))
    if len(project_wheels) != 1:
        raise ValueError("缺少本项目 wheel / Missing horosa_public wheel")
    _run([mcp_python, "-m", "pip", "install", "--no-cache-dir", "--no-deps", "--no-index", str(project_wheels[0])])
    if not (root / "horosa-core-js" / "node_modules").is_dir():
        raise ValueError("JS 引擎依赖缺失 / JS engine dependencies missing")
    if not (root / contract["ephemeris_path"]).is_dir():
        raise ValueError("Swiss Ephemeris 文件缺失 / Swiss Ephemeris files missing")
    for filename in ("entrypoint.py", "log4j2.xml", "public_container.json", "runtime_python_lock.json"):
        shutil.copy2(context / filename, root / filename)
    for filename in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        if (context / filename).is_file():
            shutil.copy2(context / filename, root / filename)
    # The pinned full bookworm build image owns the compiler/runtime ABI. Copy
    # dereferenced SONAME files so the slim image does not need mutable apt input.
    system_libs = root / "system-libs"
    system_libs.mkdir()
    for name in ("libstdc++.so.6", "libgcc_s.so.1"):
        source = Path("/usr/lib/x86_64-linux-gnu") / name
        shutil.copy2(source.resolve(strict=True), system_libs / name)
    (root / "no-store").mkdir(mode=0o555)
    (root / "no-store").chmod(0o555)
    # No service user can modify the application, libraries or request-cache root.
    for path in root.rglob("*"):
        if not path.is_symlink():
            path.chmod(path.stat().st_mode & ~0o222)
    root.chmod(0o555)
    _run([chart_python, "-c", "import numpy, pandas, pyarrow, astropy, swisseph, sxtwl, ephem, pendulum, cherrypy"])
    _run([mcp_python, "-c", "import horosa_public, horosa_skill, mcp, pydantic, cryptography; print('public MCP imports ready', horosa_skill.__version__)"])
    _run([str(root / "mcp-venv/bin/horosa-public"), "--version"])
    _run([str(root / "node/bin/node"), "--version"])
    _run([str(root / "java/bin/java"), "-version"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, help="Already downloaded pinned release archive")
    parser.add_argument("--output", type=Path, default=PROJECT / "build" / "public-container")
    parser.add_argument("--offline", action="store_true", help="Require all pinned archives already cached")
    parser.add_argument("--force", action="store_true", help="Rebuild a context created by this script")
    parser.add_argument("--check-docker-space", action="store_true", help="Check daemon disk space only; never prepare, build, pull or clean")
    parser.add_argument("--install-in-container", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.check_docker_space:
        result = check_docker_space(load_contract())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    if args.install_in_container:
        install(args.install_in_container)
    else:
        prepare(args.output.resolve(), args.seed, offline=args.offline, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
