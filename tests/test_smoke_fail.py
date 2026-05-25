"""Negative-path tests exercised by the smoke_fail scenario."""


def test_intentional_failure():
    """Used to prove the orchestrator persists failed outcomes."""
    assert 1 == 2, "intentional failure to exercise the failure path"
