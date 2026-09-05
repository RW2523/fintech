"""T-002 — the environment check script is present, executable and complete."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "env_check.sh"

# docs/02 §2: ports the compose stack binds.
REQUIRED_PORTS = ["5432", "6379", "9000", "7233", "8080", "3000", "8000"]


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT.is_file(), "scripts/env_check.sh missing"
    assert SCRIPT.stat().st_mode & stat.S_IXUSR, "scripts/env_check.sh is not executable"


def test_script_checks_every_documented_port() -> None:
    body = SCRIPT.read_text()
    missing = [p for p in REQUIRED_PORTS if p not in body]
    assert not missing, f"env_check.sh does not check ports {missing}"


def test_script_checks_the_hard_requirements() -> None:
    body = SCRIPT.read_text()
    for probe in ("aarch64", "nvidia-smi", "MemAvailable", "docker", "uv"):
        assert probe in body, f"env_check.sh does not probe {probe!r}"


def test_script_runs_and_reports() -> None:
    """It must exit 0 on a healthy host and always print the table."""
    env = {**os.environ, "SKIP_GPU_RUNTIME": "1"}
    proc = subprocess.run([str(SCRIPT)], capture_output=True, text=True, cwd=ROOT, env=env, timeout=120)
    assert "CHECK" in proc.stdout and "STATUS" in proc.stdout, proc.stdout
    assert "architecture" in proc.stdout
    assert proc.returncode == 0, f"env_check reported a FAIL:\n{proc.stdout}\n{proc.stderr}"


def test_makefile_env_target_calls_the_script() -> None:
    mk = (ROOT / "Makefile").read_text()
    assert "scripts/env_check.sh" in mk
    assert "docker/.env.example" in mk
