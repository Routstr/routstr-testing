"""Boot throwaway routstr-core nodes for whole-system secret-lifecycle e2e (#553).

The standing compose ``node-a`` is one long-lived container with every secret
pre-supplied, so it cannot exercise the BOOT-TIME secret behaviours #553 adds:
provisioning + persisting a Fernet key when ``ROUTSTR_SECRET_KEY`` is unset,
generating + logging a first-run admin password, bricking on a key change, and
recovering an encrypted nsec on a later boot after ``NSEC`` has left the env.
Those need ephemeral nodes
booted with tailored env — two of them across a *pair* of boots sharing one
volume.

This module drives the *already-built* node image (the orchestrator builds it for
the standing stack) with ``docker run``, attached to the compose network so the
node reaches ``relay`` / ``mock-openai`` / the mints by service name, writing to a
throwaway volume that is removed afterwards. Nothing here touches the standing
``node-a`` container or its volume.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import httpx

from tests.integration.targets import is_remote, unavailable

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Every ephemeral container carries this label so a hard-killed run's leftovers can
# be found and reaped without ever touching the compose stack.
_LIFECYCLE_LABEL = "routstr-lifecycle-e2e"

# The in-container SQLite path the node writes to (see base_node_env's DATABASE_URL).
# Single source of truth so the boot env and copy_db can't drift apart.
_DB_PATH = "/data/node.db"

# The Fernet key compose.yml bakes into node-a as its default. Tests that boot an
# ephemeral node with an encrypted secret must use this exact key so the blob is
# readable the same way the standing node reads it.
COMPOSE_SECRET_KEY = "W5PvCGEnbMTde00OFubyfhPPO2-f6aQP5ullyqoBfRQ="


def require_local_docker() -> None:
    """Skip/fail unless we can boot local containers (see targets.unavailable)."""
    if is_remote():
        unavailable(
            "secret-lifecycle e2e boots throwaway local containers; "
            "it does not apply to a remote target"
        )
    if shutil.which("docker") is None:
        unavailable("docker CLI required to boot ephemeral nodes")
    _reap_stale_lifecycle_nodes_once()


@lru_cache(maxsize=1)
def _reap_stale_lifecycle_nodes_once() -> None:
    """Remove ephemeral nodes left behind by a previously hard-killed run.

    Serving nodes now publish to an ephemeral host port (no fixed-port wedge), so a
    leaked container can't block a fresh run; this just keeps dead containers from
    piling up. Scoped to our label, so it never removes a compose service. Runs at
    most once per process (``lru_cache``, matching ``_project_name``).
    """
    proc = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label={_LIFECYCLE_LABEL}"],
        capture_output=True,
        text=True,
    )
    ids = proc.stdout.split()
    if ids:
        subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, text=True)


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )


@lru_cache(maxsize=1)
def _project_name() -> str:
    proc = _compose("config", "--format", "json")
    if proc.returncode != 0:
        unavailable(
            f"could not read compose config: {(proc.stderr or proc.stdout)[:200]}"
        )
    try:
        return str(json.loads(proc.stdout)["name"])
    except (ValueError, KeyError) as exc:  # pragma: no cover - defensive
        unavailable(f"could not parse compose project name: {exc}")


def node_image() -> str:
    """The image compose built for ``node-a`` (``<project>-node-a`` by convention)."""
    return f"{_project_name()}-node-a"


def compose_network() -> str:
    """The default network compose created (``<project>_default``)."""
    return f"{_project_name()}_default"


def base_node_env() -> dict[str, str]:
    """Minimal env to boot a node, mirroring the compose ``node-a`` essentials.

    Deliberately omits ``ROUTSTR_SECRET_KEY``, ``ADMIN_PASSWORD``, ``NSEC`` and
    ``NPUB`` — each test layers in exactly the secret env it is exercising (and
    proves the *absence* of one by leaving it out). ``DATABASE_URL`` points at the
    mounted ``/data`` volume so state persists across a paired reboot.

    ``HTTP_URL`` is left at the node's ``http://localhost:8000`` sentinel on
    purpose: a node treats that value as "no real endpoint" and skips the Nostr
    listing publish entirely, so an ephemeral node carrying an nsec never announces
    onto the shared ``relay`` (whose named volume would otherwise retain the dead
    "LifecycleNode" listing for discovery scenarios on a reused stack). Clearing
    ``RELAYS`` would be worse — an empty relay list falls back to *public* relays.
    """
    return {
        "DATABASE_URL": f"sqlite+aiosqlite:///{_DB_PATH}",
        "RELAYS": "ws://relay:8080",
        "UPSTREAM_BASE_URL": "http://mock-openai:3000",
        "UPSTREAM_API_KEY": "test-key",
        "CASHU_MINTS": "http://primary-mint:3338",
        "NAME": "LifecycleNode",
        "DESCRIPTION": "secret-lifecycle e2e node",
        "HTTP_URL": "http://localhost:8000",
        "CORS_ORIGINS": "*",
        "FIXED_COST_PER_REQUEST": "1",
        "FIXED_PER_1K_INPUT_TOKENS": "10",
        "FIXED_PER_1K_OUTPUT_TOKENS": "30",
        "FIXED_PRICING": "false",
    }


@dataclass
class BootResult:
    """Outcome of booting a node until it either exits or comes up serving."""

    exited: bool
    exit_code: int | None
    logs: str


@dataclass
class ServingNode:
    cid: str
    base_url: str

    def logs(self) -> str:
        return _logs(self.cid)


def _run_args(env: dict[str, str], *, volume: str, publish: bool) -> list[str]:
    args = [
        "docker", "run", "-d",
        "--label", _LIFECYCLE_LABEL,
        "--network", compose_network(),
        "-v", f"{volume}:/data",
    ]
    if publish:
        # Publish to an ephemeral host port (``0`` = let docker pick a free one)
        # rather than a fixed port, so a leaked container from a hard-killed run
        # can never wedge later runs by holding the port.
        args += ["-p", "0:8000"]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    args.append(node_image())
    return args


def _docker_run(env: dict[str, str], *, volume: str, publish: bool) -> str:
    proc = subprocess.run(
        _run_args(env, volume=volume, publish=publish),
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        unavailable(
            f"failed to start ephemeral node: {(proc.stderr or proc.stdout)[:300]}"
        )
    return proc.stdout.strip()


def _published_host_port(cid: str) -> int:
    """Read back the ephemeral host port docker assigned to container port 8000."""
    proc = subprocess.run(
        ["docker", "port", cid, "8000"], capture_output=True, text=True
    )
    for line in proc.stdout.splitlines():
        _, _, port = line.strip().rpartition(":")
        if port.isdigit():
            return int(port)
    unavailable(
        "could not determine the ephemeral node's published port: "
        f"{(proc.stdout or proc.stderr)[:200]}"
    )


def copy_db(cid: str) -> bytes:
    """Copy an ephemeral node's SQLite state off the container for at-rest inspection.

    Reads ``_DB_PATH`` (the same path ``base_node_env``'s DATABASE_URL points at) plus
    its ``-wal`` sidecar, and returns the concatenated bytes. The node runs SQLite in WAL
    mode, so a freshly committed row lives in ``node.db-wal`` until it is checkpointed
    into ``node.db``; copying only the main file would miss recent writes and report a
    misleadingly clean database. The ``-wal`` may be absent (already checkpointed) —
    that's fine, whatever is present is inspected. Must be called while ``cid`` is
    still alive (i.e. inside the ``serving_node`` block).
    """
    blobs: list[bytes] = []
    with tempfile.TemporaryDirectory() as tmp:
        for suffix, required in (("", True), ("-wal", False)):
            dest = Path(tmp) / f"node.db{suffix}"
            proc = subprocess.run(
                ["docker", "cp", f"{cid}:{_DB_PATH}{suffix}", str(dest)],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0 or not dest.exists():
                if required:
                    unavailable(
                        "could not copy ephemeral node DB for at-rest inspection: "
                        f"{(proc.stderr or proc.stdout)[:300]}"
                    )
                continue
            blobs.append(dest.read_bytes())
    return b"".join(blobs)


def _logs(cid: str) -> str:
    proc = subprocess.run(["docker", "logs", cid], capture_output=True, text=True)
    return proc.stdout + proc.stderr


def _state(cid: str) -> tuple[str, int]:
    proc = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", cid],
        capture_output=True,
        text=True,
    )
    status, _, code = proc.stdout.strip().partition(" ")
    return status or "unknown", int(code) if code.strip() else 0


def _rm(cid: str) -> None:
    subprocess.run(["docker", "rm", "-f", cid], capture_output=True, text=True)


@contextmanager
def throwaway_volume() -> Iterator[str]:
    """A named docker volume, removed on exit. Survives container removal so a
    paired reboot sees the first boot's on-disk state."""
    name = f"routstr-lifecycle-{uuid.uuid4().hex[:12]}"
    subprocess.run(["docker", "volume", "create", name], capture_output=True, text=True)
    try:
        yield name
    finally:
        subprocess.run(
            ["docker", "volume", "rm", "-f", name], capture_output=True, text=True
        )


def boot_until_settled(
    env: dict[str, str], *, volume: str, timeout: int = 60
) -> BootResult:
    """Boot a node and report whether it fails fast (exits) or stays up.

    Returns as soon as the container exits (``exited=True`` with its exit code).
    If it is still running after ``timeout`` it booted successfully
    (``exited=False``) — that is the signal a fail-fast did *not* happen. Either
    way the container is removed; the volume is the caller's to manage.
    """
    cid = _docker_run(env, volume=volume, publish=False)
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, code = _state(cid)
            if status == "exited":
                return BootResult(exited=True, exit_code=code, logs=_logs(cid))
            time.sleep(1)
        return BootResult(exited=False, exit_code=None, logs=_logs(cid))
    finally:
        _rm(cid)


@contextmanager
def serving_node(
    env: dict[str, str], *, volume: str, timeout: int = 90
) -> Iterator[ServingNode]:
    """Boot a node, wait until it serves ``/v1/info``, yield a handle, then tear it
    down. Fails (or skips, ad hoc) if it exits during boot or never comes up."""
    cid = _docker_run(env, volume=volume, publish=True)
    base = f"http://localhost:{_published_host_port(cid)}"
    try:
        deadline = time.time() + timeout
        last_err = ""
        while time.time() < deadline:
            status, code = _state(cid)
            if status == "exited":
                unavailable(
                    f"ephemeral node exited during boot (code {code}):\n"
                    f"{_logs(cid)[-1000:]}"
                )
            try:
                resp = httpx.get(f"{base}/v1/info", timeout=3)
                if resp.status_code < 500:
                    yield ServingNode(cid=cid, base_url=base)
                    return
            except httpx.HTTPError as exc:
                last_err = str(exc)
            time.sleep(1)
        unavailable(
            f"ephemeral node never became reachable at {base} ({last_err})\n"
            f"{_logs(cid)[-1000:]}"
        )
    finally:
        _rm(cid)
