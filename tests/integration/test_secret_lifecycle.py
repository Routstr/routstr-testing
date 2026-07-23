"""Boot-time secret lifecycle for routstr-core #553 (config ownership).

Whole-system proof of the secret behaviours that only show up across a real
container *boot* — the things the standing single-boot stack (and the in-repo
unit/integration suite) structurally cannot exercise:

  * a node carrying a Nostr identity but no ``ROUTSTR_SECRET_KEY`` PROVISIONS
    one — generates a Fernet key, persists it beside the DB, warns once — and
    boots with the nsec still encrypted at rest, the identity surviving a later
    keyless reboot from the persisted key (issue step 2; key custody is flexible,
    encryption at rest is not);
  * a fresh node with no admin password GENERATES one, hashes it into the Secret
    store, and logs it once with the admin URL — and that logged password logs in
    (issue step 4 / bootstrap branch 3);
  * changing ``ROUTSTR_SECRET_KEY`` under an already-encrypted nsec BRICKS the
    node (fail fast) rather than silently losing the identity — #553 has no key
    rotation, only detection;
  * an nsec encrypted into the Secret store SURVIVES a later boot once ``NSEC``
    has left the env (the #553 upgrade path), instead of being clobbered back to
    empty (bootstrap branch 1 + the strip-on-write regression guard).

These each boot throwaway nodes with tailored env via ``node_boot`` (the standing
node-a can't be reconfigured per-test). Marked ``destructive`` so they auto-skip
under TARGET_PROFILE=remote — they need local docker, not a deployed node.
"""
from __future__ import annotations

import re

import httpx
import pytest

from tests.integration import node_boot
from tests.integration.targets import bearer_headers, mint_admin_token

pytestmark = pytest.mark.destructive

# A second, unmistakably different valid Fernet key (urlsafe-b64 of 32 zero bytes),
# used to prove a key change bricks the node (vs node_boot.COMPOSE_SECRET_KEY).
FERNET_KEY_2 = "A" * 43 + "="
# A valid 64-char hex private key the node's nsec parser accepts like a bech32 nsec.
SEED_NSEC = "1" * 64
ADMIN_PW = "lifecycle-admin-pw"

# The first-run log line bootstrap_secrets emits: "...shown only now): <pw>\nLog in at <url>/admin..."
_GENERATED_PW = re.compile(r"shown only now\):\s*(\S+)")


@pytest.fixture(autouse=True)
def _local_docker() -> None:
    node_boot.require_local_docker()


def test_node_without_secret_key_provisions_and_persists_one() -> None:
    """A node with a Nostr identity but no ROUTSTR_SECRET_KEY provisions its own key.

    #553's key custody is flexible while encryption at rest is not: a missing
    ``ROUTSTR_SECRET_KEY`` is PROVISIONED, not fatal. The node generates a Fernet
    key, persists it beside the SQLite DB (so it rides the same volume), prints a
    one-time back-up notice, and boots — with the nsec still encrypted at rest,
    never plaintext. Because the key was persisted, the identity survives a later
    boot that still supplies no key (the key file is read back). Pre-2026-07 the
    node instead refused to boot on a missing key — that inversion is the RED this
    discriminates.
    """
    base = node_boot.base_node_env()
    # No ROUTSTR_SECRET_KEY on purpose — the node must provision one.
    seed = {**base, "NSEC": SEED_NSEC, "ADMIN_PASSWORD": ADMIN_PW}
    with node_boot.throwaway_volume() as vol:
        # Boot 1: keyless -> generate + persist a key, warn once, encrypt the nsec.
        with node_boot.serving_node(seed, volume=vol) as node1:
            logs = node1.logs()
            assert "No ROUTSTR_SECRET_KEY was set" in logs, (
                "a keyless boot must announce it generated its own key:\n"
                f"{logs[-1500:]}"
            )
            assert "BACK UP THIS FILE" in logs, (
                "the generated-key notice must tell the operator to back it up "
                f"(the key is unrecoverable if lost):\n{logs[-1500:]}"
            )
            npub1 = httpx.get(f"{node1.base_url}/v1/info", timeout=15).json().get("npub")
            assert npub1 and str(npub1).startswith("npub1"), f"boot 1 npub: {npub1!r}"
            db_bytes = node_boot.copy_db(node1.cid)

        # Positive control against a vacuous pass: the encrypted nsec ciphertext
        # (``fernet:v1:`` prefix) must actually be on disk, so the plaintext-absence
        # check below is meaningful rather than passing on an empty/partial copy.
        assert b"fernet:v1:" in db_bytes, (
            "no fernet ciphertext found on disk — the secret store was not persisted "
            "to the copied DB, so the plaintext-absence check would be vacuous"
        )
        assert SEED_NSEC.encode() not in db_bytes, (
            "the seeded nsec is on disk in plaintext — a self-provisioned key must "
            "still encrypt secrets at rest, not skip encryption when no key was set"
        )

        # Boot 2: STILL no key in env -> the node must read back the persisted key
        # file and decrypt the identity, proving the generated key was persisted
        # (not held only in memory and lost with the process).
        with node_boot.serving_node(
            {**base, "ADMIN_PASSWORD": ADMIN_PW}, volume=vol
        ) as node2:
            npub2 = httpx.get(f"{node2.base_url}/v1/info", timeout=15).json().get("npub")
            assert npub2 == npub1, (
                "the self-provisioned key was not persisted: the identity changed "
                f"across a keyless reboot ({npub1!r} -> {npub2!r}), so the encrypted "
                "nsec could not be decrypted from the same volume"
            )


def test_first_run_generates_and_logs_admin_password() -> None:
    """A fresh node with no admin password generates, logs, and accepts one.

    Bootstrap branch 3: with no ADMIN_PASSWORD anywhere, the node mints a random
    password, hashes it into the Secret store, and logs it once with the admin
    URL (the first-run UX — there is no setup screen). The logged password must
    actually authenticate. Pre-#553 there is no such generated-password log.
    """
    env = {**node_boot.base_node_env(), "ROUTSTR_SECRET_KEY": node_boot.COMPOSE_SECRET_KEY}
    # No ADMIN_PASSWORD, no NSEC.
    with node_boot.throwaway_volume() as vol, node_boot.serving_node(
        env, volume=vol
    ) as node:
        logs = node.logs()
        match = _GENERATED_PW.search(logs)
        assert match, (
            "first boot must generate + log an admin password:\n"
            f"{logs[-1500:]}"
        )
        generated = match.group(1)
        assert "/admin" in logs, (
            f"the first-run log must point the operator at the admin URL:\n{logs[-1500:]}"
        )
        token = mint_admin_token(node.base_url, generated)
        assert token, "the generated admin password must actually log in"


def test_key_change_bricks_encrypted_nsec() -> None:
    """Changing ROUTSTR_SECRET_KEY under an encrypted nsec bricks the node.

    #553 ships no key rotation (single Fernet key, not MultiFernet); a changed key
    must be DETECTED and fail fast, never silently boot with a dead identity. Boot
    once to encrypt the nsec, then reboot the same volume under a different key.
    """
    base = node_boot.base_node_env()
    with node_boot.throwaway_volume() as vol:
        # Boot 1: store the encrypted nsec under the compose default key.
        with node_boot.serving_node(
            {**base, "ROUTSTR_SECRET_KEY": node_boot.COMPOSE_SECRET_KEY, "NSEC": SEED_NSEC,
             "ADMIN_PASSWORD": ADMIN_PW},
            volume=vol,
        ):
            pass

        # Boot 2: same volume, only the key changed.
        result = node_boot.boot_until_settled(
            {**base, "ROUTSTR_SECRET_KEY": FERNET_KEY_2, "NSEC": SEED_NSEC,
             "ADMIN_PASSWORD": ADMIN_PW},
            volume=vol,
            timeout=50,
        )

    assert result.exited and result.exit_code != 0, (
        "a changed ROUTSTR_SECRET_KEY must brick the node (fail fast), not boot "
        f"silently with an undecryptable nsec.\n{result.logs[-1200:]}"
    )
    assert "Stored nsec cannot be decrypted" in result.logs, (
        f"the brick must explain the key mismatch:\n{result.logs[-1200:]}"
    )


def test_encrypted_nsec_survives_second_boot_without_env_nsec() -> None:
    """An nsec encrypted at boot 1 survives boot 2 once NSEC has left the env.

    The #553 upgrade path: an operator drops the legacy ``NSEC`` from ``.env``
    after first boot has migrated it into the encrypted Secret store. The node
    must decrypt it back into memory on the next boot — not clobber the identity
    to empty while re-deriving settings from the (now nsec-less) env+blob. Proven
    black-box: ``GET /admin/api/settings`` reports ``nsec: "[REDACTED]"`` while a
    key is held and the npub stays stable across the env-less reboot.
    """
    base = node_boot.base_node_env()
    with node_boot.throwaway_volume() as vol:
        # Boot 1: NSEC supplied via env -> encrypted into the Secret store.
        with node_boot.serving_node(
            {**base, "ROUTSTR_SECRET_KEY": node_boot.COMPOSE_SECRET_KEY, "NSEC": SEED_NSEC,
             "ADMIN_PASSWORD": ADMIN_PW},
            volume=vol,
        ) as node1:
            token1 = mint_admin_token(node1.base_url, ADMIN_PW)
            assert token1, "boot-1 admin login (env password) must work"
            settings1 = httpx.get(
                f"{node1.base_url}/admin/api/settings", headers=bearer_headers(token1), timeout=15
            ).json()
            assert settings1.get("nsec") == "[REDACTED]", (
                f"boot 1 should hold the Nostr identity, got nsec={settings1.get('nsec')!r}"
            )
            npub1 = httpx.get(f"{node1.base_url}/v1/info", timeout=15).json().get("npub")
            assert npub1 and str(npub1).startswith("npub1"), f"boot 1 npub: {npub1!r}"

        # Boot 2: SAME volume + key, but NSEC removed from the env.
        with node_boot.serving_node(
            {**base, "ROUTSTR_SECRET_KEY": node_boot.COMPOSE_SECRET_KEY, "ADMIN_PASSWORD": ADMIN_PW},
            volume=vol,
        ) as node2:
            token2 = mint_admin_token(node2.base_url, ADMIN_PW)
            assert token2, "boot-2 admin login must still work after the env-less reboot"
            settings2 = httpx.get(
                f"{node2.base_url}/admin/api/settings", headers=bearer_headers(token2), timeout=15
            ).json()
            assert settings2.get("nsec") == "[REDACTED]", (
                "the Nostr identity was lost on the second boot once NSEC left the "
                "env — the encrypted nsec must be decrypted from the Secret store, "
                f"not clobbered to empty. got nsec={settings2.get('nsec')!r}"
            )
            npub2 = httpx.get(f"{node2.base_url}/v1/info", timeout=15).json().get("npub")
            assert npub2 == npub1, (
                f"the node's npub must be stable across the env-less reboot: "
                f"{npub1!r} -> {npub2!r}"
            )
