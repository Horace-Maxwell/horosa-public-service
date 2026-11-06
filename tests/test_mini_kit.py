"""Mac mini deploy kit: every script parses, and the image loader refuses a tarball whose checksum does not match."""
from __future__ import annotations

import gzip
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

MINI = Path(__file__).resolve().parents[1] / "deploy" / "mini"
SCRIPTS = sorted(MINI.glob("*.sh"))


def test_kit_has_the_documented_scripts():
    assert {path.name for path in SCRIPTS} >= {"setup_mini.sh", "load_image.sh", "run_container.sh", "check.sh"}


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_script_parses(script: Path):
    subprocess.run(["bash", "-n", str(script)], check=True)


def _run_loader(tarball: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    # A docker stub on PATH records whether the loader got as far as `docker load`.
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "docker"
    stub.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/docker-called"\ncat >/dev/null\n', encoding="utf-8")
    stub.chmod(0o755)
    env = {"PATH": f"{stub_dir}:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path)}
    return subprocess.run(["bash", str(MINI / "load_image.sh"), str(tarball)], env=env, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("shasum") is None, reason="needs shasum")
def test_loader_checks_sha256_before_loading(tmp_path: Path):
    tarball = tmp_path / "horosa-public-9.9.9-amd64.tar.gz"
    tarball.write_bytes(gzip.compress(b"not really an image"))
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()

    missing = _run_loader(tarball, tmp_path)
    assert missing.returncode == 1 and "checksum file not found" in missing.stdout

    (tmp_path / f"{tarball.name}.sha256").write_text(f"{'0' * 64}  {tarball.name}\n", encoding="utf-8")
    refused = _run_loader(tarball, tmp_path)
    assert refused.returncode == 1 and "sha256 mismatch" in refused.stdout
    assert not (tmp_path / "docker-called").exists()

    (tmp_path / f"{tarball.name}.sha256").write_text(f"{digest}  {tarball.name}\n", encoding="utf-8")
    accepted = _run_loader(tarball, tmp_path)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert (tmp_path / "docker-called").read_text(encoding="utf-8").strip() == "load"


def _stub(stub_dir: Path, name: str, body: str) -> None:
    path = stub_dir / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.parametrize("busy", [True, False])
def test_run_container_refuses_a_port_another_program_holds(tmp_path: Path, busy: bool):
    # Stubs only: nothing here reaches Docker, the network or a real port.
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    log = tmp_path / "docker.log"
    _stub(stub_dir, "docker", f'echo "$1" >> "{log}"\n[ "$1" = inspect ] && echo true\nexit 0\n')
    _stub(stub_dir, "lsof", 'echo "node 95464 user 12u IPv4 TCP 127.0.0.1:8765 (LISTEN)"\nexit 0\n' if busy else "exit 1\n")
    _stub(stub_dir, "sleep", "exit 0\n")
    _stub(stub_dir, "curl", "exit 0\n")
    env = {"PATH": f"{stub_dir}:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path)}
    result = subprocess.run(["bash", str(MINI / "run_container.sh"), "horosa-public:9.9.9", "8765"],
                            env=env, capture_output=True, text=True)
    calls = log.read_text(encoding="utf-8").split()
    if busy:
        assert result.returncode == 1 and "already in use" in result.stdout and "node 95464" in result.stdout
        assert calls == ["rm"]  # removed its own container, never started a new one
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls[:2] == ["rm", "run"] and "ready after" in result.stdout
