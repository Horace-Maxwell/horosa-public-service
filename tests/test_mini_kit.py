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
    assert {path.name for path in SCRIPTS} >= {"setup_mini.sh", "load_image.sh", "run_container.sh", "check.sh", "install_daily_restart.sh"}


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


def _restart_env(tmp_path: Path, *, restarts: bool) -> dict[str, str]:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    flag = tmp_path / "restarted"
    started = f'if [ -f "{flag}" ]; then echo 2026-11-10T04:00:00Z; else echo 2026-11-09T00:00:00Z; fi'
    _stub(stub_dir, "docker", f'case "$*" in *Running*) echo true;; *StartedAt*) {started};; esac\n')
    kick = f'touch "{flag}"' if restarts else ":"
    _stub(stub_dir, "launchctl", f'echo "$@" >> "{tmp_path}/launchctl.log"\ncase "$1" in kickstart) {kick};; esac\n')
    _stub(stub_dir, "curl", "exit 0\n")
    _stub(stub_dir, "sleep", "exit 0\n")
    return {"PATH": f"{stub_dir}:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path)}


@pytest.mark.skipif(shutil.which("plutil") is None, reason="needs macOS plutil")
def test_daily_restart_installer_schedules_04_00_and_proves_one_restart(tmp_path: Path):
    done = subprocess.run(["bash", str(MINI / "install_daily_restart.sh")], env=_restart_env(tmp_path, restarts=True),
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    plist = (tmp_path / "Library/LaunchAgents/com.horosa.public-daily-restart.plist").read_text(encoding="utf-8")
    assert "<key>Hour</key><integer>4</integer>" in plist and "<key>Minute</key><integer>0</integer>" in plist
    assert "<string>colima-horosa-plugin</string>" in plist and "<string>restart</string>" in plist
    assert str(tmp_path / "bin/docker") in plist
    calls = (tmp_path / "launchctl.log").read_text(encoding="utf-8")
    assert "bootstrap" in calls and "kickstart -k" in calls and "ready after" in done.stdout


@pytest.mark.skipif(shutil.which("plutil") is None, reason="needs macOS plutil")
def test_daily_restart_installer_fails_when_the_container_does_not_restart(tmp_path: Path):
    done = subprocess.run(["bash", str(MINI / "install_daily_restart.sh")], env=_restart_env(tmp_path, restarts=False),
                          capture_output=True, text=True)
    assert done.returncode == 1 and "did not restart the container" in done.stdout
