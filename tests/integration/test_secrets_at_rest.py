"""Config-ownership secret storage at rest (routstr-core #553).

Whole-system proof that operator-supplied secrets handed to a real node via
container env (ADMIN_PASSWORD, NSEC) are migrated into the encrypted Secret store
at boot, instead of living in plaintext in the editable settings blob:

  * the env-seeded admin password still authenticates (POST /admin/api/login) —
    it was hashed into the Secret store, not broken;
  * ``admin_password`` is no longer an editable settings field (GET
    /admin/api/settings omits it — it became a one-way hash, not config);
  * the node exposes a dedicated nsec rotation endpoint (PATCH /admin/api/nsec)
    that derives the npub — the Nostr identity is a rotatable secret, not a blob
    field smuggled through the general settings PATCH;
  * neither the plaintext admin password, the seeded nsec, nor a freshly rotated
    nsec appears anywhere in the node's on-disk SQLite database.

Each test boots its OWN throwaway node via ``node_boot`` (fresh volume, tailored
env), the same ephemeral mechanism ``test_secret_lifecycle`` uses. These are
boot-time / state-mutating behaviours (one rotates the identity), so they must run
against a pristine per-test node rather than the shared standing ``node-a`` — that
keeps each test order-independent, leaves no state to restore, and lets us seed the
plaintext directly through the front door (no compose/pytest ``.env`` divergence).

Discriminating: RED against a node that keeps secrets in the plaintext settings
blob (pre-#553); GREEN once they move into the Fernet/scrypt-backed Secret store
and ``admin_password`` leaves the settings model. Marked ``destructive`` (needs
local docker, not a deployed node): auto-skips under TARGET_PROFILE=remote.
"""
from __future__ import annotations

import httpx
import pytest

from tests.integration import node_boot
from tests.integration.targets import bearer_headers, mint_admin_token

pytestmark = pytest.mark.destructive

ADMIN_PW = "at-rest-admin-pw"
# The nsec the node is seeded with via env. The node stores secrets verbatim (only
# ``.strip()``, no bech32<->hex normalization), so this exact string must NOT survive
# in the DB once #553 encrypts it.
SEED_NSEC = "nsec1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqsmhltgl"
# A valid 64-char hex private key the node's nsec parser accepts like a bech32 nsec.
# After PATCH /admin/api/nsec this is the node's *live* nsec (stored verbatim), so it
# too must be absent from disk — the check that guards the rotated identity.
ROTATE_NSEC_HEX = "1" * 64


@pytest.fixture(autouse=True)
def _local_docker() -> None:
    node_boot.require_local_docker()


def _seed_env() -> dict[str, str]:
    """A node booted with a known admin password and nsec supplied via env."""
    return {
        **node_boot.base_node_env(),
        "ROUTSTR_SECRET_KEY": node_boot.COMPOSE_SECRET_KEY,
        "ADMIN_PASSWORD": ADMIN_PW,
        "NSEC": SEED_NSEC,
    }


def test_admin_password_is_not_a_settings_field() -> None:
    """admin_password must not be an editable settings field.

    Under #553 the admin password is a one-way scrypt hash in the Secret store,
    not a value you read or write through the settings blob. GET /admin/api/settings
    must therefore not carry an ``admin_password`` key at all (a node that still
    exposes it — redacted or not — is keeping the password as blob config).
    """
    with node_boot.throwaway_volume() as vol, node_boot.serving_node(
        _seed_env(), volume=vol
    ) as node:
        token = mint_admin_token(node.base_url, ADMIN_PW)
        assert token, (
            "the env-seeded admin password must log in — proving it was hashed into "
            "the Secret store at boot, not broken by the migration"
        )
        resp = httpx.get(
            f"{node.base_url}/admin/api/settings", headers=bearer_headers(token), timeout=15
        )
    assert resp.status_code == 200, (
        f"GET /admin/api/settings failed: HTTP {resp.status_code}: {resp.text[:300]}"
    )
    data = resp.json()
    assert "admin_password" not in data, (
        "admin_password is still exposed as a settings field — under #553 it must "
        "be a one-way hash in the Secret store, not an editable config value. "
        f"settings keys: {sorted(data)[:40]}"
    )


def test_nsec_rotation_endpoint_derives_npub() -> None:
    """The node exposes a dedicated nsec rotation endpoint that derives the npub.

    The Nostr identity is a rotatable secret with its own write path
    (PATCH /admin/api/nsec), not a field smuggled through the general settings
    PATCH (which strips it). A 404/405 means that write path is missing.
    """
    with node_boot.throwaway_volume() as vol, node_boot.serving_node(
        _seed_env(), volume=vol
    ) as node:
        token = mint_admin_token(node.base_url, ADMIN_PW)
        assert token, "the env-seeded admin password must log in"
        resp = httpx.patch(
            f"{node.base_url}/admin/api/nsec",
            json={"nsec": ROTATE_NSEC_HEX},
            headers=bearer_headers(token),
            timeout=15,
        )
    assert resp.status_code == 200, (
        "PATCH /admin/api/nsec should rotate the node's Nostr identity (200) — a "
        f"404/405 means the dedicated nsec write path is missing. "
        f"Got HTTP {resp.status_code}: {resp.text[:300]}"
    )
    body = resp.json()
    assert body.get("ok") is True, f"expected ok=True, got {body!r}"
    assert str(body.get("npub", "")).startswith("npub1"), (
        f"endpoint should derive and return the npub for the new key, got {body!r}"
    )


def test_no_plaintext_secret_in_node_database() -> None:
    """The operator's plaintext password and nsec must not survive on disk.

    The flagship #553 guarantee: secrets supplied via env are hashed/encrypted
    into the Secret store, never persisted in the plaintext settings blob.

    The settings blob is written lazily (a fresh node leaves it empty), so a bare
    "grep the DB" would pass vacuously on a node that *would* persist secrets the
    moment the blob is touched. We therefore first force a settings persist with a
    harmless edit, then rotate the identity (so the check also covers the *live*
    secret after a rotation, not only the env seed), assert the probe actually
    landed on disk (so the secret-absence check is meaningful, not vacuous), then
    assert the raw DB bytes contain none of the plaintext secrets. A node that keeps
    secrets in the settings blob (pre-#553) writes them alongside the probe and
    fails here.
    """
    probe = "at-rest-probe-node-name"
    with node_boot.throwaway_volume() as vol, node_boot.serving_node(
        _seed_env(), volume=vol
    ) as node:
        token = mint_admin_token(node.base_url, ADMIN_PW)
        assert token, "the env-seeded admin password must log in"
        headers = bearer_headers(token)
        with httpx.Client(base_url=node.base_url, timeout=15) as client:
            resp = client.patch(
                "/admin/api/settings", json={"name": probe}, headers=headers
            )
            assert resp.status_code == 200, (
                "settings PATCH (to force a blob persist) failed: "
                f"HTTP {resp.status_code}: {resp.text[:300]}"
            )
            resp = client.patch(
                "/admin/api/nsec", json={"nsec": ROTATE_NSEC_HEX}, headers=headers
            )
            assert resp.status_code == 200, (
                "nsec rotation (to make ROTATE_NSEC_HEX the live secret) failed: "
                f"HTTP {resp.status_code}: {resp.text[:300]}"
            )
        db_bytes = node_boot.copy_db(node.cid)

    # Guard against a vacuous pass: the probe must have been persisted, proving a
    # settings blob was actually written to disk for the secret-absence check to mean
    # anything. A pre-#553 node persists admin_password/nsec into that same blob.
    assert probe.encode() in db_bytes, (
        "the settings blob was not persisted to disk after a PATCH, so the "
        "plaintext-secret check below would be vacuous — the node's persistence "
        "path changed; pick a field that actually writes the blob"
    )

    assert ADMIN_PW.encode() not in db_bytes, (
        "the plaintext admin password is present in the node's on-disk database — "
        "it must be stored only as a one-way scrypt hash in the Secret store, never "
        "in the settings blob"
    )
    assert SEED_NSEC.encode() not in db_bytes, (
        "the seeded plaintext nsec is present in the node's on-disk database — the "
        "Nostr identity must be Fernet-encrypted in the Secret store, not kept in "
        "the settings blob"
    )
    assert ROTATE_NSEC_HEX.encode() not in db_bytes, (
        "the rotated plaintext nsec is present in the node's on-disk database — "
        "after PATCH /admin/api/nsec the node's live Nostr identity is this value, "
        "and it must be Fernet-encrypted in the Secret store, not persisted verbatim"
    )
