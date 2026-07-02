"""Shared helpers for the foreign-mint swap tests (Phase 1 + Phase 2).

Not a test module (no test_ prefix) so pytest does not collect it.
"""
from __future__ import annotations

import os
import subprocess

import httpx

from tests.integration import targets
from tests.integration.targets import unavailable

# Docker-network hostnames: the node and the minting container reach the mints
# here; host port mappings (3338/3339/3340) are only for the test process itself.
PRIMARY_MINT_INTERNAL = "http://primary-mint:3338"
FOREIGN_MINT_INTERNAL = "http://foreign-mint:3338"
# A second TRUSTED mint (in CASHU_MINTS) that charges a NUT-02 input fee
# (input_fee_ppk=100), unlike the zero-fee primary-mint — the input-fee test
# needs a trusted mint whose same-mint redeem actually loses a per-proof fee.
FEE_MINT_INTERNAL = "http://fee-mint:3338"
FAULT_PROXY_INTERNAL = "http://fault-proxy:3340"

# Tokens are minted with the NODE's own cashu lib (version-matched to the code
# under test) by exec-ing the node container's venv python; the node reaches the
# mints over the compose network. Minting is orthogonal to the app under test —
# the container is just a convenient version-matched cashu host.
NODE_CONTAINER = os.environ.get("NODE_CONTAINER", "routstr-testing-node-a-1")
NODE_VENV_PYTHON = "/.venv/bin/python"

NODE = targets.node_api_url(0)
TOKEN_SATS = 32
BEARER_SATS = 64  # comfortably above any input-fee floor for the key-identity token
# A deliberately large face value: the wallet mints it into many small proofs, so
# the mint's NUT-02 per-proof input fee rounds up to a clearly non-zero sat amount
# (these FakeWallet mints charge input_fee_ppk=100), giving the input-fee tests an
# unambiguous signal rather than a 1-sat rounding edge.
FEE_TOKEN_SATS = 2047

# Fund a wallet at MINT_URL for AMOUNT sat and print the serialized token.
# include_dleq=True: the node runs verify_proofs_dleq on same-mint receive, so a
# token without DLEQ data is rejected. legacy=True (TokenV3/cashuA): nutshell
# issues 64-hex "v2" keyset ids which the compact TokenV4 (cashuB) form truncates
# to 16 hex, breaking the node's keyset lookup; V3 preserves the full id string.
_MINT_SCRIPT = """
import asyncio, os, tempfile
from cashu.wallet.wallet import Wallet

async def main():
    url = os.environ["MINT_URL"]
    amount = int(os.environ["AMOUNT"])
    with tempfile.TemporaryDirectory() as d:
        w = await Wallet.with_db(url, db=d, unit="sat")
        await w.load_mint()
        quote = await w.request_mint(amount)
        await asyncio.sleep(0.5)  # let FakeWallet settle the invoice
        proofs = await w.mint(amount, quote_id=quote.quote)
        token = await w.serialize_proofs(proofs, include_dleq=True, legacy=True)
        # The mint's own NUT-02 input fee for these proofs (sat), computed by the
        # same cashu lib the node uses on receive — this is the ground truth the
        # node's credit must match, derived independently of routstr's code.
        print("FEE:" + str(w.get_fees_for_proofs(proofs)))
        print("TOKEN:" + token)

asyncio.run(main())
"""


def _run_mint(mint_internal_url: str, amount: int) -> str:
    try:
        r = subprocess.run(
            [
                "docker", "exec", "-i",
                "-e", f"MINT_URL={mint_internal_url}",
                "-e", f"AMOUNT={amount}",
                NODE_CONTAINER, NODE_VENV_PYTHON, "-",
            ],
            input=_MINT_SCRIPT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        unavailable(f"cannot mint test token (docker/{NODE_CONTAINER} unavailable): {exc}")
    if r.returncode != 0:
        unavailable(f"mint helper failed (is {NODE_CONTAINER} up?): {r.stderr[-500:]}")
    return r.stdout


def _parse(stdout: str, prefix: str) -> str | None:
    for line in stdout.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def mint_token(mint_internal_url: str, amount: int) -> str:
    """Mint `amount` sat at `mint_internal_url` and return the serialized token."""
    stdout = _run_mint(mint_internal_url, amount)
    token = _parse(stdout, "TOKEN:")
    if token is None:
        unavailable("mint output missing TOKEN: line")
    return token


def mint_token_with_fee(mint_internal_url: str, amount: int) -> tuple[str, int]:
    """Mint `amount` sat and return (token, input_fee_sat).

    The input fee is the mint's own NUT-02 per-proof charge for this token's
    proofs, computed by the cashu lib (the ground truth the node must deduct on
    receive), not by routstr's code under test.
    """
    stdout = _run_mint(mint_internal_url, amount)
    token = _parse(stdout, "TOKEN:")
    fee = _parse(stdout, "FEE:")
    if token is None or fee is None:
        # Report which prefix was missing rather than echoing stdout, which
        # ends in the TOKEN: line and would tail the serialized token.
        missing = " and ".join(
            p for p, v in (("TOKEN:", token), ("FEE:", fee)) if v is None
        )
        unavailable(f"mint output missing {missing} line(s)")
    return token, int(fee)


def topup(cashu_token: str, bearer: str) -> httpx.Response:
    return httpx.post(
        f"{NODE}/v1/wallet/topup",
        params={"cashu_token": cashu_token},
        headers={"Authorization": f"Bearer {bearer}"},
        timeout=90,
    )
