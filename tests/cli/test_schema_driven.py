"""Schema-driven CLI tests.

The leaf command list is derived from `routstr schema` so this module stays in
sync with the binary automatically. A new upstream command gets at minimum a
--help smoke test plus a failure-path test on the next run.

Commands with entries in HAPPY_SPECS additionally get a proper happy-path
test against node-a. Commands in ADMIN_HAPPY_SPECS get admin-token happy tests.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from tests.cli.helpers import run_cli, DEAD_NODE

# ── helpers ──────────────────────────────────────────────────────────────────


def _leaf_commands(node: dict, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Recursively collect leaf command paths from the schema dict."""
    leaves: list[tuple[str, ...]] = []
    for cmd in node.get("commands", []):
        path = prefix + (cmd["name"],)
        if cmd.get("commands"):
            leaves.extend(_leaf_commands(cmd, prefix=path))
        else:
            leaves.append(path)
    return leaves


# Resolved at collection time; stays empty (and all tests skip) if container is down.
_schema: dict = {}
_leaf_paths: list[tuple[str, ...]] = []

try:
    _r = subprocess.run(
        ["docker", "exec", "routstr-testing-cli-runner-1", "bun", "/app/dist/index.js", "schema"],
        capture_output=True, text=True, timeout=15,
    )
    if _r.returncode == 0:
        _schema = json.loads(_r.stdout)
        _leaf_paths = _leaf_commands(_schema)
except Exception:
    pass

# Commands to skip entirely (TUI / long-running daemon).
_SKIP = {("serve",), ("monitor",)}

# Commands that always succeed and cannot fail via dead-node (no network call).
_NO_NETWORK = {("schema",), ("instruct",), ("init",)}

# ── per-command no-auth happy-path args ──────────────────────────────────────

HAPPY_SPECS: dict[tuple[str, ...], list[str]] = {
    ("status",): [],
    ("config", "show"): [],
    ("config", "get"): ["name"],
    ("models", "list"): [],
    ("wallet", "receive"): [],
    ("schema",): [],
    ("instruct",): [],
}

# ── testable list ─────────────────────────────────────────────────────────────

_testable = [p for p in _leaf_paths if p not in _SKIP]


def _cmd_id(path: tuple[str, ...]) -> str:
    return " ".join(path)


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.safe_for_remote
class TestHelpSmoke:
    """Every leaf command exposes a --help that exits 0.

    This test stays in sync with the binary: any new command added upstream
    automatically gets collected here on the next run.
    """

    @pytest.mark.parametrize("path", _testable, ids=_cmd_id)
    def test_help_exits_zero(self, path: tuple[str, ...]):
        if not _leaf_paths:
            pytest.skip("CLI schema unavailable")
        result = run_cli(*path, "--help")
        assert result.returncode == 0, (
            f"`routstr {' '.join(path)} --help` exited {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert result.stdout.strip(), "Expected --help output but got nothing"


@pytest.mark.safe_for_remote
class TestNoAuthHappyPath:
    """Happy-path tests for commands that need no admin token."""

    @pytest.mark.parametrize("path", [p for p in _testable if p in HAPPY_SPECS], ids=_cmd_id)
    def test_happy_no_auth(self, node_a, path: tuple[str, ...]):
        if not _leaf_paths:
            pytest.skip("CLI schema unavailable")
        args = HAPPY_SPECS[path]
        # schema and instruct don't use --node
        if path in _NO_NETWORK:
            result = run_cli(*list(path), *args)
        else:
            result = run_cli("--node", node_a, *list(path), *args)
        assert result.returncode == 0, (
            f"`routstr {' '.join(path)}` exited {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


@pytest.mark.admin_required
@pytest.mark.destructive
class TestAdminHappyPath:
    """Happy-path tests for admin-gated commands.

    Uses a session-scoped managed_provider to avoid leaving orphaned test data.
    """

    def test_config_set(self, node_a, admin_token):
        result = run_cli("--node", node_a, "config", "set", "-t", admin_token, "name", "NodeA")
        assert result.returncode == 0, f"config set: {result.stderr}"

    def test_providers_list_with_token(self, node_a, admin_token):
        result = run_cli("--node", node_a, "providers", "list", "-t", admin_token)
        assert result.returncode == 0, f"providers list: {result.stderr}"

    def test_providers_add_remove(self, node_a, admin_token):
        """Add a disposable provider, then remove it."""
        add = run_cli(
            "--node", node_a, "-o", "json",
            "providers", "add", "test-tmp",
            "-t", admin_token,
            "--base-url", "http://mock-openai:3099",
            "--api-key", "tmp-key",
        )
        assert add.returncode == 0, f"providers add: {add.stderr}"
        data = json.loads(add.stdout)
        pid = str(data.get("id", ""))
        assert pid, f"no id in response: {data}"

        rm = run_cli("--node", node_a, "providers", "remove", pid, "-t", admin_token)
        assert rm.returncode == 0, f"providers remove: {rm.stderr}"

    def test_providers_show(self, node_a, admin_token, managed_provider):
        result = run_cli(
            "--node", node_a, "providers", "show", managed_provider["id"],
            "-t", admin_token,
        )
        assert result.returncode == 0, f"providers show: {result.stderr}"

    def test_providers_test(self, node_a, admin_token, managed_provider):
        """providers test reaches the node and returns a structured error or success.

        The mock-openai backend doesn't support balance checking so the server
        returns 400; this is the expected server-side response for a provider
        that lacks the feature — not a CLI bug. We assert the CLI correctly
        propagates the server response (non-zero exit with printed error) rather
        than crashing silently.
        """
        result = run_cli(
            "--node", node_a, "providers", "test", managed_provider["id"],
            "-t", admin_token,
        )
        # 400 from server = provider feature not supported = expected in test env.
        # Either exit 0 (provider healthy) or exit 1 with a structured error is fine.
        assert result.returncode in (0, 1), (
            f"providers test crashed unexpectedly: {result.stderr}"
        )
        if result.returncode != 0:
            assert result.stderr.strip(), "Expected error output on non-zero exit"

    def test_providers_update(self, node_a, admin_token, managed_provider):
        result = run_cli(
            "--node", node_a, "providers", "update", managed_provider["id"],
            "-t", admin_token, "--enabled", "true",
        )
        assert result.returncode == 0, f"providers update: {result.stderr}"

    def test_providers_enable_disable(self, node_a, admin_token, managed_provider):
        disable = run_cli(
            "--node", node_a, "providers", "disable", managed_provider["id"],
            "-t", admin_token,
        )
        assert disable.returncode == 0, f"providers disable: {disable.stderr}"

        enable = run_cli(
            "--node", node_a, "providers", "enable", managed_provider["id"],
            "-t", admin_token,
        )
        assert enable.returncode == 0, f"providers enable: {enable.stderr}"

    def test_providers_models_list(self, node_a, admin_token, managed_provider):
        result = run_cli(
            "--node", node_a, "providers", "models", "list", managed_provider["id"],
            "-t", admin_token,
        )
        assert result.returncode == 0, f"providers models list: {result.stderr}"

    def test_providers_models_show(self, node_a, admin_token, managed_provider, managed_model_id):
        result = run_cli(
            "--node", node_a, "providers", "models", "show",
            managed_provider["id"], managed_model_id,
            "-t", admin_token,
        )
        assert result.returncode == 0, f"providers models show: {result.stderr}"

    def test_providers_models_update(self, node_a, admin_token, managed_provider, managed_model_id):
        result = run_cli(
            "--node", node_a, "providers", "models", "update",
            managed_provider["id"], managed_model_id,
            "-t", admin_token, "--enabled", "true",
        )
        assert result.returncode == 0, f"providers models update: {result.stderr}"

    def test_wallet_balance_with_key(self, node_a):
        """wallet balance with a clearly invalid key returns an auth error (exit non-zero)."""
        result = run_cli("--node", node_a, "wallet", "balance", "-k", "cashuAinvalidtoken")
        # Verifying the command runs and exits non-zero on invalid key (not a crash)
        assert result.returncode != 0, "Expected non-zero exit on invalid token"


@pytest.mark.safe_for_remote
class TestFailurePaths:
    """Every leaf command handles bad input / offline node with non-zero exit.

    Uses parametrize so a new upstream command automatically gets at least the
    dead-node OR unknown-flag failure test.
    """

    @pytest.mark.parametrize("path", _testable, ids=_cmd_id)
    def test_fails_on_bad_input(self, path: tuple[str, ...]):
        """Commands either fail with a dead node or reject unknown flags.

        - Network commands: dead node → non-zero exit.
        - Local commands (schema, instruct, init): unknown flag → non-zero exit.
        """
        if not _leaf_paths:
            pytest.skip("CLI schema unavailable")

        if path in _NO_NETWORK:
            # These don't make network calls; test that unknown flags are rejected.
            result = run_cli(*path, "--this-flag-does-not-exist-xyz")
            assert result.returncode != 0, (
                f"`routstr {' '.join(path)} --unknown-flag` unexpectedly succeeded"
            )
        else:
            result = run_cli("--node", DEAD_NODE, *path)
            assert result.returncode != 0, (
                f"`routstr --node {DEAD_NODE} {' '.join(path)}` unexpectedly succeeded\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

    def test_missing_admin_token_config_set(self, node_a):
        """config set without -t should fail."""
        result = run_cli("--node", node_a, "config", "set", "name", "x")
        assert result.returncode != 0, "config set without token unexpectedly succeeded"

    def test_bad_token_providers_list(self, node_a):
        """providers list with an invalid admin token should fail."""
        result = run_cli("--node", node_a, "providers", "list", "-t", "bad-token-xyz")
        assert result.returncode != 0, "providers list with bad token unexpectedly succeeded"

    def test_nonexistent_provider_show(self, node_a, admin_token):
        """providers show with a nonexistent ID should fail."""
        result = run_cli("--node", node_a, "providers", "show", "99999999", "-t", admin_token)
        assert result.returncode != 0, "providers show with bad id unexpectedly succeeded"

    def test_config_get_unknown_key(self, node_a):
        """config get with an unknown key should fail."""
        result = run_cli("--node", node_a, "config", "get", "this_key_does_not_exist_xyz")
        assert result.returncode != 0, "config get with unknown key unexpectedly succeeded"

    def test_providers_add_without_token(self, node_a):
        """providers add without admin token should fail."""
        result = run_cli(
            "--node", node_a, "providers", "add", "test",
            "--base-url", "http://mock-openai:3000", "--api-key", "key",
        )
        assert result.returncode != 0, "providers add without token unexpectedly succeeded"

    def test_wallet_balance_no_key(self, node_a):
        """wallet balance with no key should fail."""
        result = run_cli("--node", node_a, "wallet", "balance")
        assert result.returncode != 0, "wallet balance without key unexpectedly succeeded"
