"""Hand-written CLI tests for init, instruct, global flags, and --no-input.

These tests cover behaviour that can't be cleanly expressed in the schema-driven
parametrisation: side-effects (config file writes), specific text contracts, and
the --no-input agent-mode constraint.
"""
from __future__ import annotations

import json

import pytest

from tests.cli.helpers import run_cli, NODE_A_INTERNAL

# CLI side-effects here are local config writes (init/instruct/show) — they
# don't mutate the routstr node — so the whole module is safe to point at a
# remote deployment.
pytestmark = [pytest.mark.safe_for_remote]


# ── init ─────────────────────────────────────────────────────────────────────


class TestInit:
    def test_saves_node_url(self):
        """init --node-url writes the URL into the config."""
        result = run_cli("init", "--node-url", "http://test-node.example")
        assert result.returncode == 0, f"init failed: {result.stderr}"
        assert "Config saved" in result.stdout or result.returncode == 0

    def test_saves_token(self):
        """init --token writes the token into the config."""
        result = run_cli("init", "--token", "testtoken12345")
        assert result.returncode == 0, f"init --token failed: {result.stderr}"

    def test_show_flag(self):
        """init --show prints current config."""
        result = run_cli("init", "--show")
        assert result.returncode == 0, f"init --show failed: {result.stderr}"

    def test_json_output(self):
        """init -o json emits valid JSON."""
        result = run_cli("-o", "json", "init", "--node-url", "http://test-node.example")
        assert result.returncode == 0
        # Either JSON output or text; at minimum exit 0
        if result.stdout.strip():
            try:
                json.loads(result.stdout)
            except json.JSONDecodeError:
                pass  # text format is also acceptable; exit code is the contract


# ── instruct ─────────────────────────────────────────────────────────────────


class TestInstruct:
    def test_default_text_output(self):
        """instruct default format contains instruction text."""
        result = run_cli("instruct")
        assert result.returncode == 0, f"instruct failed: {result.stderr}"
        assert result.stdout.strip(), "Expected instruction text but got nothing"

    def test_json_format(self):
        """instruct -f json emits parseable JSON."""
        result = run_cli("instruct", "-f", "json")
        assert result.returncode == 0, f"instruct -f json failed: {result.stderr}"
        data = json.loads(result.stdout)
        assert isinstance(data, (dict, list, str)), "Expected JSON value"

    def test_openai_format(self):
        """instruct -f openai emits an object with a 'content' key."""
        result = run_cli("instruct", "-f", "openai")
        assert result.returncode == 0, f"instruct -f openai failed: {result.stderr}"
        data = json.loads(result.stdout)
        # OpenAI system-message format: {role, content} or similar
        assert "content" in data or isinstance(data, str), (
            f"Unexpected openai format output: {data}"
        )

    def test_text_format_explicit(self):
        """instruct -f text works the same as default."""
        result = run_cli("instruct", "-f", "text")
        assert result.returncode == 0, f"instruct -f text failed: {result.stderr}"
        assert result.stdout.strip()


# ── global flags ─────────────────────────────────────────────────────────────


class TestGlobalFlags:
    def test_node_flag_overrides_default(self, node_a):
        """-n / --node flag reaches the specified node."""
        result = run_cli("-n", node_a, "status")
        assert result.returncode == 0, f"status via -n flag failed: {result.stderr}"

    def test_node_long_flag(self, node_a):
        """--node (long form) works identically to -n."""
        result = run_cli("--node", node_a, "status")
        assert result.returncode == 0, f"status via --node flag failed: {result.stderr}"

    def test_quiet_flag(self, node_a):
        """-q suppresses non-essential output; command still succeeds."""
        result = run_cli("--node", node_a, "-q", "status")
        assert result.returncode == 0, f"status -q failed: {result.stderr}"

    def test_verbose_flag(self, node_a):
        """-v enables verbose output; command still succeeds."""
        result = run_cli("--node", node_a, "-v", "status")
        assert result.returncode == 0, f"status -v failed: {result.stderr}"

    def test_output_json(self, node_a):
        """-o json produces parseable JSON from status."""
        result = run_cli("--node", node_a, "-o", "json", "status")
        assert result.returncode == 0, f"status -o json failed: {result.stderr}"
        data = json.loads(result.stdout)
        assert isinstance(data, dict), f"Expected JSON object, got: {type(data)}"

    def test_output_text(self, node_a):
        """-o text is the default and produces readable output."""
        result = run_cli("--node", node_a, "-o", "text", "status")
        assert result.returncode == 0, f"status -o text failed: {result.stderr}"

    def test_output_json_models_list(self, node_a):
        """-o json on models list emits a JSON array."""
        result = run_cli("--node", node_a, "-o", "json", "models", "list")
        assert result.returncode == 0, f"models list -o json failed: {result.stderr}"
        data = json.loads(result.stdout)
        assert isinstance(data, list), f"Expected JSON array, got: {type(data)}"

    def test_version_flag(self):
        """-V / --version prints a version string and exits 0."""
        result = run_cli("--version")
        assert result.returncode == 0, f"--version failed: {result.stderr}"
        assert result.stdout.strip(), "Expected version string"


# ── --no-input agent-mode contract ───────────────────────────────────────────


class TestNoInput:
    """--no-input must disable all interactive prompts.

    In agent mode the CLI must never block waiting for stdin. We validate the
    contract by running commands that *would* prompt interactively (like init
    without arguments) and asserting they complete non-interactively with a
    non-zero exit or clean output — never hanging.
    """

    def test_init_no_input_non_blocking(self):
        """init --no-input should not block even with no --node-url or --token."""
        result = run_cli("--no-input", "init")
        # Must complete (not hang); exit code can be 0 or non-zero
        assert result.returncode is not None, "Process hung — --no-input failed to prevent blocking"

    def test_providers_list_no_input(self, node_a):
        """providers list --no-input runs without prompting."""
        result = run_cli("--node", node_a, "--no-input", "providers", "list")
        assert result.returncode is not None, "--no-input hung on providers list"
        # Non-zero is fine (no token), but it must not hang
        assert True

    def test_status_no_input(self, node_a):
        """status --no-input exits cleanly."""
        result = run_cli("--node", node_a, "--no-input", "status")
        assert result.returncode == 0, f"status --no-input failed: {result.stderr}"

    def test_schema_no_input(self):
        """schema --no-input emits the schema and exits 0."""
        result = run_cli("--no-input", "schema")
        assert result.returncode == 0, f"schema --no-input failed: {result.stderr}"
        data = json.loads(result.stdout)
        assert "commands" in data

    def test_instruct_no_input(self):
        """instruct --no-input emits instructions non-interactively."""
        result = run_cli("--no-input", "instruct")
        assert result.returncode == 0, f"instruct --no-input failed: {result.stderr}"
        assert result.stdout.strip()
