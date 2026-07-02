"""Foreign-mint swap on wallet topup (routstr-core swap_to_primary_mint).

When a node receives a Cashu token issued by a mint it does NOT trust
(`token.mint not in CASHU_MINTS`), it swaps the value to its primary mint:
mint-quote on primary -> melt the foreign proofs to pay that invoice ->
mint fresh proofs on primary. This exercises the real swap path end to end
through `POST /v1/wallet/topup`, with two local nutshell FakeWallet mints
(`primary-mint` trusted, `foreign-mint` untrusted) so it runs with real Cashu
crypto and zero real sats.

Run config: the node trusts the in-stack mints (compose defaults
`CASHU_MINTS=http://primary-mint:3338,http://fee-mint:3338`) but NOT
`foreign-mint`, so a `foreign-mint` token is untrusted and triggers the swap
instead of a plain same-mint redeem.

Marked `destructive` (creates a key + credits balance) so it auto-skips under
TARGET_PROFILE=remote.
"""
from __future__ import annotations

import pytest

from tests.integration._swap_helpers import (
    FOREIGN_MINT_INTERNAL,
    PRIMARY_MINT_INTERNAL,
    BEARER_SATS,
    TOKEN_SATS,
    mint_token,
    mint_token_with_fee,
    topup,
)
from tests.integration.targets import require_node

pytestmark = pytest.mark.destructive


@pytest.fixture(scope="module", autouse=True)
def _require_stack() -> None:
    require_node()


def test_foreign_mint_token_swaps_on_topup() -> None:
    """A foreign-mint token tops up the node via the swap path.

    The Bearer is a primary-mint token (redeemed same-mint at auth to establish
    the key); the foreign-mint token is then swapped by /topup. Because a real
    cross-mint swap burns a melt fee_reserve + input fees, the credited msats are
    strictly LESS than the token's face value. A credit equal to the face value
    would mean no swap happened (e.g. a trusted same-mint redeem) — so the strict
    inequality is what proves swap_to_primary_mint actually ran.

    The companion control (test_trusted_mint_token_credits_face_minus_input_fee)
    redeems a trusted-mint token with NO swap and loses only the small per-proof
    input fee, pinning the larger shortfall here to the swap (melt fee_reserve on
    top of the input fee).
    """
    bearer = mint_token(PRIMARY_MINT_INTERNAL, BEARER_SATS)
    foreign = mint_token(FOREIGN_MINT_INTERNAL, TOKEN_SATS)

    r = topup(foreign, bearer)
    assert r.status_code == 200, f"topup failed: HTTP {r.status_code}: {r.text[:300]}"
    credited = r.json()["msats"]

    assert 0 < credited < TOKEN_SATS * 1000, (
        f"foreign-mint topup should swap and credit LESS than the face value "
        f"{TOKEN_SATS * 1000} msats (melt + input fees deducted), got {credited}. "
        f"A credit of exactly {TOKEN_SATS * 1000} would mean no swap occurred."
    )


def test_trusted_mint_token_credits_face_minus_input_fee() -> None:
    """Control: a token from the node's TRUSTED (primary) mint is redeemed
    same-mint with NO swap, so it loses ONLY the mint's NUT-02 per-proof input
    fee (the same-mint receive still swaps the proofs with include_fees=True) —
    not the melt fee_reserve a cross-mint swap adds on top.

    This is the causation guard for the swap test: it shows the shortfall there
    is the swap (melt fee) on top of this input fee, not something incidental to
    /topup. The exact-equality here (face - input_fee) also pins the trusted-path
    credit, so the swap test's strict inequality has a precise lower reference.

    Input-fee correctness in depth lives in
    test_topup_input_fee.py::test_trusted_mint_topup_deducts_input_fee; here we
    keep the small TOKEN_SATS token but still account for its real fee.
    """
    bearer = mint_token(PRIMARY_MINT_INTERNAL, BEARER_SATS)
    trusted, input_fee = mint_token_with_fee(PRIMARY_MINT_INTERNAL, TOKEN_SATS)

    r = topup(trusted, bearer)
    assert r.status_code == 200, f"topup failed: HTTP {r.status_code}: {r.text[:300]}"
    credited = r.json()["msats"]

    expected = (TOKEN_SATS - input_fee) * 1000
    assert credited == expected, (
        f"trusted same-mint topup should credit face - input_fee = "
        f"({TOKEN_SATS} - {input_fee}) * 1000 = {expected} msats (no swap melt fee, "
        f"but the per-proof input fee still applies), got {credited}."
    )
