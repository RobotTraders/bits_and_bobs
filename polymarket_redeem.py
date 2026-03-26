import os
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

from eth_abi import encode as eth_encode
from eth_utils import keccak

# pip install git+https://github.com/Polymarket/py-builder-relayer-client.git
from py_builder_relayer_client.client import RelayClient
from py_builder_relayer_client.models import RelayerTxType, OperationType, SafeTransaction
from py_builder_signing_sdk.config import BuilderConfig, BuilderApiKeyCreds

# Rate limits: https://docs.polymarket.com/api-reference/rate-limits
# - Data API /positions: 150 req/10s (not a concern for a single call)
# - Relayer /submit: 25 req/1 min (the bottleneck for batch redemptions)
RELAYER_RETRY_WAIT = 60  # seconds to wait on rate limit before retrying


def redeem_all(private_key, funder_address, signature_type, builder_api_key, builder_secret, builder_passphrase):
    ts = lambda: datetime.now().strftime("%H:%M:%S")

    USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

    # ── Client Setup ──────────────────────────────────────────────────────

    # 0 = Browser wallet (MetaMask, Rabby) → Gnosis Safe
    # 1 = Email account → Proxy wallet
    # 2 = Browser wallet proxy → Gnosis Safe
    wallet_type = RelayerTxType.PROXY if signature_type == 1 else RelayerTxType.SAFE

    client = RelayClient(
        "https://relayer-v2.polymarket.com",
        chain_id=137,
        private_key=private_key,
        builder_config=BuilderConfig(
            local_builder_creds=BuilderApiKeyCreds(
                key=builder_api_key,
                secret=builder_secret,
                passphrase=builder_passphrase,
            )
        ),
        relay_tx_type=wallet_type,
    )

    # ── Find Redeemable Positions ─────────────────────────────────────────

    # redeemable=true filters server-side: only resolved positions with tokens still held.
    # This catches everything, including dust left over from partial sells on the UI.
    try:
        response = requests.get(
            "https://data-api.polymarket.com/positions",
            params={"user": funder_address, "redeemable": "true", "sizeThreshold": 0},
        )
        if response.status_code in (429, 1015):
            print(f"{ts()} - Data API rate limited, waiting {RELAYER_RETRY_WAIT}s...")
            time.sleep(RELAYER_RETRY_WAIT)
            response = requests.get(
                "https://data-api.polymarket.com/positions",
                params={"user": funder_address, "redeemable": "true", "sizeThreshold": 0},
            )
        positions = response.json()
    except Exception as e:
        print(f"{ts()} - Failed to fetch positions: {e}")
        return 0

    # The API may still return positions with size 0 after redemption. Skip them.
    positions = [p for p in positions if float(p.get("size", 0)) > 0]

    if not positions:
        print(f"{ts()} - No positions to redeem")
        return 0

    print(f"{ts()} - Found {len(positions)} redeemable positions")

    # ── Redeem Each Position ──────────────────────────────────────────────

    # Relayer limit: 25 req/min. Each resp.wait() blocks for a few seconds
    # (on-chain confirmation), which acts as a natural throttle. If the relayer
    # rate limits us despite that, we wait and retry once.

    redeemed = 0
    for pos in positions:
        cid = pos.get("conditionId", pos.get("condition_id", ""))
        if not cid:
            continue
        if not cid.startswith("0x"):
            cid = "0x" + cid
        market = pos.get("title", cid[:12])

        try:
            condition_bytes = bytes.fromhex(cid[2:])
            selector = keccak(text="redeemPositions(address,bytes32,bytes32,uint256[])")[:4]
            args = eth_encode(
                ["address", "bytes32", "bytes32", "uint256[]"],
                [USDC_ADDRESS, b"\x00" * 32, condition_bytes, [1, 2]],
            )
            txn = SafeTransaction(
                to=CTF_ADDRESS,
                operation=OperationType.Call,
                data="0x" + (selector + args).hex(),
                value="0",
            )

            try:
                resp = client.execute([txn], f"redeem {cid[:12]}")
                resp.wait()
            except Exception as relay_err:
                status = getattr(relay_err, "status_code", None)
                if status in (429, 1015):
                    print(f"{ts()} - Relayer rate limited (HTTP {status}), waiting {RELAYER_RETRY_WAIT}s...")
                    time.sleep(RELAYER_RETRY_WAIT)
                    resp = client.execute([txn], f"redeem {cid[:12]}")
                    resp.wait()
                else:
                    raise

            redeemed += 1
            print(f"{ts()} - Redeemed: {market}")
        except Exception as e:
            print(f"{ts()} - Failed to redeem {market}: {e}")

    print(f"{ts()} - Redeemed {redeemed}/{len(positions)} positions")
    return redeemed


if __name__ == "__main__":
    load_dotenv()

    # 1 = Email account → Proxy wallet
    # 0 = Browser wallet → Gnosis Safe
    SIGNATURE_TYPE = 1

    redeem_all(
        private_key=os.getenv("POLYMARKET_PRIVATE_KEY"),
        funder_address=os.getenv("POLYMARKET_FUNDER_ADDRESS"),
        signature_type=SIGNATURE_TYPE,
        builder_api_key=os.getenv("POLYMARKET_BUILDER_API_KEY"),
        builder_secret=os.getenv("POLYMARKET_BUILDER_SECRET"),
        builder_passphrase=os.getenv("POLYMARKET_BUILDER_PASSPHRASE"),
    )
