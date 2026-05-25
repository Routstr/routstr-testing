"""Thin wrappers around docker compose for the orchestrator."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _have_docker() -> bool:
    return shutil.which("docker") is not None


def up(compose_file: Path, *, project_dir: Path) -> tuple[bool, str]:
    if not _have_docker():
        return False, "docker CLI not available"
    proc = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "up", "-d"],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, proc.stdout + proc.stderr


def down(compose_file: Path, *, project_dir: Path) -> tuple[bool, str]:
    if not _have_docker():
        return False, "docker CLI not available"
    proc = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "down", "-v"],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, proc.stdout + proc.stderr


def services(compose_file: Path, *, project_dir: Path) -> list[str]:
    if not _have_docker():
        return []
    proc = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "config",
            "--services",
        ],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def dump_logs(
    compose_file: Path, *, project_dir: Path, logs_dir: Path
) -> dict[str, Path]:
    """Dump per-service logs to logs/<service>.log.

    Returns a mapping of service -> path-on-disk.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for service in services(compose_file, project_dir=project_dir):
        proc = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "logs",
                "--no-color",
                service,
            ],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
        )
        target = logs_dir / f"{service}.log"
        target.write_text(proc.stdout + proc.stderr)
        written[service] = target
    return written
