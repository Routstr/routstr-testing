"""Trusted-mint input fee on wallet topup (routstr-core recieve_token).

When a node receives a Cashu token issued by a mint it TRUSTS
(`token.mint in CASHU_MINTS`), it does NOT swap — it redeems same-mint by
swapping the proofs at that mint with `include_fees=True`. If the mint charges a
NUT-02 per-proof input fee (`input_fee_ppk > 0`), the node only ends up holding
`face - input_fee` in fresh proofs, so that — not the face value — is what may be
credited. Crediting the full face over-credits the user and drifts the node's
wallet toward insolvency.

This runs the real trusted-receive path end to end through `POST /v1/wallet/topup`
against the local `fee-mint` nutshell FakeWallet — a second TRUSTED mint (it's in
the node's CASHU_MINTS) that, unlike the zero-fee primary-mint, charges
`input_fee_ppk=100`. A deliberately large, many-proof token makes the per-proof
fee round up to a clearly non-zero sat amount.

Discriminating: RED against a node that credits face value (the pre-fix bug),
GREEN once `recieve_token` deducts the input fee.

Marked `destructive` (creates a key + credits balance) so it auto-skips under
TARGET_PROFILE=remote.
"""
from __future__ import annotations

import pytest

from tests.integration._swap_helpers import (
    BEARER_SATS,
    FEE_MINT_INTERNAL,
    FEE_TOKEN_SATS,
    PRIMARY_MINT_INTERNAL,
    mint_token,
    mint_token_with_fee,
    topup,
)
from tests.integration.targets import require_node

pytestmark = pytest.mark.destructive


@pytest.fixture(scope="module", autouse=True)
def _require_stack() -> None:
    require_node()


def test_trusted_mint_topup_deducts_input_fee() -> None:
    """A trusted-mint token with a real per-proof input fee credits face - fee.

    The Bearer is a primary-mint token (redeemed same-mint at auth to establish
    the key). The topup token is a fee-mint (trusted, input_fee_ppk=100) token,
    large enough to be minted into many proofs so the mint's NUT-02 input fee is a
    clearly non-zero sat amount. Because the same-mint swap on receive pays that
    input fee, the node only holds `face - input_fee`, and crediting more than
    that would mean the node booked sats it does not actually hold.
    """
    bearer = mint_token(PRIMARY_MINT_INTERNAL, BEARER_SATS)
    token, input_fee = mint_token_with_fee(FEE_MINT_INTERNAL, FEE_TOKEN_SATS)

    # Guard: a zero fee would make the assertion vacuous (it would coincide with
    # the buggy full-face credit), so the test would not actually discriminate.
    assert input_fee > 0, (
        f"expected fee-mint (input_fee_ppk=100) to charge a non-zero fee for "
        f"a {FEE_TOKEN_SATS}-sat many-proof token; got {input_fee}. Mint fee config "
        f"changed — pick a face value that yields more proofs."
    )

    r = topup(token, bearer)
    assert r.status_code == 200, f"topup failed: HTTP {r.status_code}: {r.text[:300]}"
    credited = r.json()["msats"]

    expected = (FEE_TOKEN_SATS - input_fee) * 1000
    assert credited == expected, (
        f"trusted-mint topup should credit face - input_fee = "
        f"({FEE_TOKEN_SATS} - {input_fee}) * 1000 = {expected} msats, got {credited}. "
        f"A credit of {FEE_TOKEN_SATS * 1000} means the {input_fee}-sat input fee "
        f"was ignored — the node credited sats it does not hold (over-credit / "
        f"insolvency)."
    )
