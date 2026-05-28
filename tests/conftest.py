"""Shared pytest configuration for the routstr-testing harness.

ROU-151 — target-profile-aware skips, mirrors `routstr-core/tests/conftest.py`:

    TARGET_PROFILE=local  → everything runs (current behaviour).
    TARGET_PROFILE=remote → `destructive` tests skip with a clear reason;
                            `admin_required` tests skip unless at least one
                            `REMOTE_NODE_ADMIN_TOKEN_<i>` env var is set.

The orchestrator exports these env vars when invoked with
`--target-profile remote --remote-node-urls ...`.
"""
from __future__ import annotations

import os
import re

import pytest

TARGET_PROFILE_LOCAL = "local"
TARGET_PROFILE_REMOTE = "remote"
VALID_PROFILES = {TARGET_PROFILE_LOCAL, TARGET_PROFILE_REMOTE}

_ADMIN_TOKEN_ENV_PATTERN = re.compile(r"^REMOTE_NODE_ADMIN_TOKEN_\d+$")


def _resolved_profile() -> str:
    raw = os.environ.get("TARGET_PROFILE", TARGET_PROFILE_LOCAL).strip().lower()
    if raw not in VALID_PROFILES:
        print(
            f"[conftest] TARGET_PROFILE={raw!r} is not in {sorted(VALID_PROFILES)}; "
            f"falling back to {TARGET_PROFILE_LOCAL!r}",
            flush=True,
        )
        return TARGET_PROFILE_LOCAL
    return raw


def _any_admin_token_present() -> bool:
    return any(
        _ADMIN_TOKEN_ENV_PATTERN.match(k) and v for k, v in os.environ.items()
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    profile = _resolved_profile()
    if profile != TARGET_PROFILE_REMOTE:
        return

    has_admin = _any_admin_token_present()
    skip_destructive = pytest.mark.skip(
        reason="destructive test skipped under TARGET_PROFILE=remote "
        "(would mutate the remote node's state)",
    )
    skip_admin = pytest.mark.skip(
        reason="admin_required test skipped under TARGET_PROFILE=remote "
        "without any REMOTE_NODE_ADMIN_TOKEN_<i> env var set",
    )

    for item in items:
        markers = {m.name for m in item.iter_markers()}
        if "destructive" in markers:
            item.add_marker(skip_destructive)
            continue
        if "admin_required" in markers and not has_admin:
            item.add_marker(skip_admin)
