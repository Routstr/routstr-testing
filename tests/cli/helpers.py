"""Shared helpers for the CLI test suite (not pytest fixtures)."""
from __future__ import annotations

import os
import subprocess

CLI_CONTAINER = os.environ.get("CLI_CONTAINER", "routstr-testing-cli-runner-1")
CLI_BIN = "/app/dist/index.js"
NODE_A_EXTERNAL = os.environ.get("NODE_A_URL", "http://localhost:8001")
NODE_A_INTERNAL = "http://node-a:8000"
ADMIN_PASSWORD = os.environ.get("NODE_A_ADMIN_PASSWORD", "test-admin-pw")
DEAD_NODE = "http://localhost:19999"


def run_cli(*args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run the routstr CLI inside the cli-runner container."""
    env_flags: list[str] = []
    if extra_env:
        for k, v in extra_env.items():
            env_flags += ["-e", f"{k}={v}"]
    cmd = ["docker", "exec"] + env_flags + [CLI_CONTAINER, "bun", CLI_BIN] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)
