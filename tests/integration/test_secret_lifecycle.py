"""Boot-time secret lifecycle for routstr-core #553 (config ownership).

Whole-system proof of the secret behaviours that only show up across a real
container *boot* — the things the standing single-boot stack (and the in-repo
unit/integration suite) structurally cannot exercise:

  * a node carrying a Nostr identity refuses to start without
    ``ROUTSTR_SECRET_KEY`` and prints the key-generation command (issue step 2);
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


def test_node_refuses_to_boot_without_secret_key() -> None:
    """A node with a Nostr identity must fail fast when ROUTSTR_SECRET_KEY is unset.

    ``ROUTSTR_SECRET_KEY`` is required to encrypt the nsec at rest; #553 makes its
    absence a hard boot failure (no "secrets disabled" fallback), and the error
    must hand the operator the generation command. Pre-#553 the node ignores the
    var and boots anyway — that is the RED this discriminates.
    """
    env = {**node_boot.base_node_env(), "NSEC": SEED_NSEC, "ADMIN_PASSWORD": ADMIN_PW}
    # No ROUTSTR_SECRET_KEY on purpose.
    with node_boot.throwaway_volume() as vol:
        result = node_boot.boot_until_settled(env, volume=vol, timeout=50)

    assert result.exited and result.exit_code != 0, (
        "node booted without ROUTSTR_SECRET_KEY — #553 requires it to fail fast.\n"
        f"{result.logs[-1200:]}"
    )
    assert "ROUTSTR_SECRET_KEY" in result.logs, (
        f"fail-fast must name the missing key:\n{result.logs[-1200:]}"
    )
    assert "Fernet.generate_key" in result.logs, (
        f"fail-fast must print the key-generation command:\n{result.logs[-1200:]}"
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
