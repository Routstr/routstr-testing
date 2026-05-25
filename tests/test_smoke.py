"""Trivial sanity tests so the orchestrator always has something to drive.

These tests are intentionally service-free: they prove the runner→pytest
→junit→sqlite pipeline works end-to-end without needing routstrd or any
vendor service to be alive.
"""

import os


def test_orchestrator_reaches_pytest():
    """If pytest collects this, the orchestrator's selection logic works."""
    assert True


def test_scenario_env_is_propagated():
    """orchestrate.py exports SCENARIO_ID to pytest's env."""
    assert os.environ.get("SCENARIO_ID") == "smoke"
