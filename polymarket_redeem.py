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

def redeem_all(private_key, funder_address, signature_type,
               builder_api_key, builder_secret, builder_passphrase,
               output_token="pUSD"):
    ts = lambda: datetime.now().strftime("%H:%M:%S")

    # Rate limits: https://docs.polymarket.com/api-reference/rate-limits
    # - Data API /positions: 150 req/10s (not a concern for a single call)
    # - Relayer /submit: 25 req/1 min (the bottleneck for batch redemptions)
    RELAYER_RETRY_WAIT = 60  # seconds to wait on rate limit before retrying

    # ── Contract addresses (Polygon) ─────────────────────────────────────
    USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

    # V2 collateral adapters: redeem USDC.e from the legacy CTF/NegRiskAdapter,
    # wrap it to pUSD, and send pUSD to the caller. Same 4-arg signature as
    # the legacy CTF.redeemPositions; only conditionId is read.
    CTF_COLLATERAL_ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"
    NEG_RISK_COLLATERAL_ADAPTER = "0xadA2005600Dec949baf300f4C6120000bDB6eAab"
    POLYGON_RPC = "https://polygon-rpc.com"

    # ── Function selectors (pre-computed) ────────────────────────────────
    REDEEM_SELECTOR = keccak(text="redeemPositions(address,bytes32,bytes32,uint256[])")[:4]
    NEG_RISK_REDEEM_SELECTOR = keccak(text="redeemPositions(bytes32,uint256[])")[:4]
    APPROVE_SELECTOR = keccak(text="setApprovalForAll(address,bool)")[:4]
    IS_APPROVED_SELECTOR = keccak(text="isApprovedForAll(address,address)")[:4]

    def _is_approved(adapter):
        args = eth_encode(["address", "address"], [funder_address, adapter])
        payload = {
            "jsonrpc": "2.0", "method": "eth_call", "id": 1,
            "params": [
                {"to": CTF_ADDRESS, "data": "0x" + (IS_APPROVED_SELECTOR + args).hex()},
                "latest",
            ],
        }
        try:
            r = requests.post(POLYGON_RPC, json=payload, timeout=10).json()
            return int(r["result"], 16) == 1
        except Exception:
            return False  # safe default: script will prepend approve

    def _approve_txn(adapter):
        args = eth_encode(["address", "bool"], [adapter, True])
        return SafeTransaction(
            to=CTF_ADDRESS,
            operation=OperationType.Call,
            data="0x" + (APPROVE_SELECTOR + args).hex(),
            value="0",
        )

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
            timeout=15,
        )
        if response.status_code in (429, 1015):
            print(f"{ts()} - Data API rate limited, waiting {RELAYER_RETRY_WAIT}s...")
            time.sleep(RELAYER_RETRY_WAIT)
            response = requests.get(
                "https://data-api.polymarket.com/positions",
                params={"user": funder_address, "redeemable": "true", "sizeThreshold": 0},
                timeout=15,
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

    # ── Pre-flight: track which adapters this wallet has already approved ─
    # The new adapters pull CT tokens via safeBatchTransferFrom, which
    # requires a one-time setApprovalForAll on the CTF. We bundle the
    # approval into the first redeem that needs each adapter.
    approved_adapters = set()
    if output_token == "pUSD":
        for adapter in (CTF_COLLATERAL_ADAPTER, NEG_RISK_COLLATERAL_ADAPTER):
            if _is_approved(adapter):
                approved_adapters.add(adapter)

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
            neg_risk = pos.get("negativeRisk")

            # ── Build Redemption Transaction ──────────────────────────
            # Polymarket has two market structures, each with its own contract
            # and function signature. The Data API's `negativeRisk` boolean
            # identifies which type each position belongs to.
            # Docs: https://docs.polymarket.com/developers/neg-risk/overview

            if neg_risk is True:
                # ── Neg-risk markets ──────────────────────────────────
                # Multi-outcome markets. Two redemption paths depending on
                # which token the user wants out:
                #   pUSD  → new NegRiskCtfCollateralAdapter (4-arg signature
                #           inherited from CtfCollateralAdapter; only
                #           conditionId is used, balances read on-chain).
                #   USDC.e → legacy NegRiskAdapter (2-arg signature).
                if output_token == "pUSD":
                    args = eth_encode(
                        ["address", "bytes32", "bytes32", "uint256[]"],
                        [USDC_ADDRESS, b"\x00" * 32, condition_bytes, [1, 2]],
                    )
                    txn = SafeTransaction(
                        to=NEG_RISK_COLLATERAL_ADAPTER,
                        operation=OperationType.Call,
                        data="0x" + (REDEEM_SELECTOR + args).hex(),
                        value="0",
                    )
                    adapter_for_approval = NEG_RISK_COLLATERAL_ADAPTER
                else:
                    size_raw = int(float(pos.get("size", 0)) * 1e6)
                    outcome_index = int(pos.get("outcomeIndex", 0))
                    amounts = [0, 0]
                    amounts[outcome_index] = size_raw
                    args = eth_encode(["bytes32", "uint256[]"], [condition_bytes, amounts])
                    txn = SafeTransaction(
                        to=NEG_RISK_ADAPTER,
                        operation=OperationType.Call,
                        data="0x" + (NEG_RISK_REDEEM_SELECTOR + args).hex(),
                        value="0",
                    )
                    adapter_for_approval = None

            elif neg_risk is False:
                # ── Standard markets ──────────────────────────────────
                # Binary markets. Same 4-arg redeemPositions signature for
                # both paths; only the destination differs:
                #   pUSD  → CtfCollateralAdapter (redeems then wraps).
                #   USDC.e → legacy CTF directly.
                # indexSets [1, 2] covers both YES/NO outcomes.
                args = eth_encode(
                    ["address", "bytes32", "bytes32", "uint256[]"],
                    [USDC_ADDRESS, b"\x00" * 32, condition_bytes, [1, 2]],
                )
                txn = SafeTransaction(
                    to=CTF_COLLATERAL_ADAPTER if output_token == "pUSD" else CTF_ADDRESS,
                    operation=OperationType.Call,
                    data="0x" + (REDEEM_SELECTOR + args).hex(),
                    value="0",
                )
                adapter_for_approval = CTF_COLLATERAL_ADAPTER if output_token == "pUSD" else None

            else:
                # ── Unsupported market type ───────────────────────────
                # negativeRisk is neither True nor False (missing or new type).
                # Skip rather than send a malformed transaction.
                print(f"{ts()} - Skipping {market}: unsupported market type (negativeRisk={neg_risk!r})")
                continue

            # Bundle the one-time setApprovalForAll into the first redeem
            # that needs each adapter (single relayer round-trip).
            calls = []
            if adapter_for_approval and adapter_for_approval not in approved_adapters:
                calls.append(_approve_txn(adapter_for_approval))
                approved_adapters.add(adapter_for_approval)
            calls.append(txn)

            try:
                resp = client.execute(calls, f"redeem {cid[:12]}")
                resp.wait()
            except Exception as relay_err:
                status = getattr(relay_err, "status_code", None)
                if status in (429, 1015):
                    print(f"{ts()} - Relayer rate limited (HTTP {status}), waiting {RELAYER_RETRY_WAIT}s...")
                    time.sleep(RELAYER_RETRY_WAIT)
                    resp = client.execute(calls, f"redeem {cid[:12]}")
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
