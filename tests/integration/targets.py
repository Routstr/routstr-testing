"""Target resolution for integration tests — local stack OR remote nodes.

Local (`TARGET_PROFILE` unset/`local`): nodes are the compose containers, reached
on the host at :8001/:8002, and the `routstr-cli` reaches them on the docker
network at `http://node-a:8000`. routstrd is local at :8091.

Remote (`TARGET_PROFILE=remote`): the orchestrator exports `REMOTE_NODE_URLS`
(comma-separated) and optional `REMOTE_NODE_ADMIN_TOKEN_<i>` / `ROUTSTRD_URL`.
API calls and the CLI both target the remote URL. Admin tokens come from env
(remote nodes' admin password isn't ours), so admin-driven tests skip when a
token is absent.

This keeps every paid/routing scenario runnable against a deployed node without
changing the test bodies — they call these resolvers instead of hardcoding URLs.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

ADMIN_PASSWORD = os.environ.get("NODE_A_ADMIN_PASSWORD", "test-admin-pw")

# Local defaults (host-facing API ports / docker-network CLI hostnames).
_LOCAL_API = ["http://localhost:8001", "http://localhost:8002"]
_LOCAL_CLI = ["http://node-a:8000", "http://node-b:8000"]


def _remote_urls() -> list[str]:
    raw = os.environ.get("REMOTE_NODE_URLS", "")
    return [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]


def is_remote() -> bool:
    return os.environ.get("TARGET_PROFILE", "local").strip().lower() == "remote"


def node_count() -> int:
    return len(_remote_urls()) if is_remote() else len(_LOCAL_API)


def node_api_url(i: int = 0) -> str:
    """URL pytest (httpx) uses to reach node i."""
    urls = _remote_urls()
    if is_remote():
        return urls[i]
    return _LOCAL_API[i]


def node_cli_url(i: int = 0) -> str:
    """URL `routstr --node` uses to reach node i."""
    if is_remote():
        return _remote_urls()[i]
    return _LOCAL_CLI[i]


def node_marker(i: int = 0) -> str:
    """Hostname routstrd's discovered baseUrl for node i should contain.

    Local: `node-a` / `node-b`. Remote: the remote URL's host. Lets routing
    assertions identify which node served without hardcoding `node-a`.
    """
    return urlparse(node_cli_url(i)).hostname or node_cli_url(i)


def routstrd_url() -> str:
    return os.environ.get("ROUTSTRD_URL", "http://localhost:8091").rstrip("/")


def cli_runner_container() -> str:
    """Container the `routstr-cli` runs in (override for non-default project name)."""
    return os.environ.get("CLI_CONTAINER", "routstr-testing-cli-runner-1")


def admin_token(i: int = 0) -> str | None:
    """Admin token for node i.

    Remote: from `REMOTE_NODE_ADMIN_TOKEN_<i>` (None if unset → caller skips).
    Local: minted via `POST /admin/api/login` with the node admin password.
    """
    env_tok = os.environ.get(f"REMOTE_NODE_ADMIN_TOKEN_{i}", "").strip()
    if env_tok:
        return env_tok
    if is_remote():
        return None  # no token supplied for this remote node
    try:
        with httpx.Client(base_url=node_api_url(i), timeout=10) as c:
            r = c.post("/admin/api/login", json={"password": ADMIN_PASSWORD})
            return r.json().get("token") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def node_reachable(i: int = 0) -> bool:
    try:
        return httpx.get(f"{node_api_url(i)}/v1/info", timeout=5).status_code < 500
    except httpx.HTTPError:
        return False
