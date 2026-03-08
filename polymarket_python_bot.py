import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    OrderArgs,
    MarketOrderArgs,
    OrderType,
    BalanceAllowanceParams,
    AssetType,
)
from py_clob_client.order_builder.constants import BUY, SELL


# ── Configuration ──────────────────────────────────────────────────────────────

load_dotenv()

PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY")
FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS")

# 0 = EOA wallet (MetaMask, hardware wallet)
# 1 = Email wallet (If account created with email)
# 2 = Browser wallet proxy
SIGNATURE_TYPE = 1

TRADE_SIZE = 0.5  # 50% of available balance per trade
MARKETS_LIMIT = 100
MAX_POSITIONS = 3

# ----------------

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

client = ClobClient(
    CLOB_API,
    key=PRIVATE_KEY,
    chain_id=137,  # Polygon
    signature_type=SIGNATURE_TYPE,
    funder=FUNDER_ADDRESS,
)
creds = client.derive_api_key()
client.set_api_creds(creds)


# ── Layer 1: API Wrappers ─────────────────────────────────────────────────────


def get_markets(**filters):
    params = {
        "limit": MARKETS_LIMIT,
        "active": True,
        "closed": False,
    }
    params.update(filters)
    response = requests.get(f"{GAMMA_API}/markets", params=params)
    return response.json()


def get_balance():
    balance = client.get_balance_allowance(
        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    usdc = int(balance["balance"]) / 1e6
    return usdc


def get_price(token_id):
    midpoint = float(client.get_midpoint(token_id)["mid"])
    best_ask = float(client.get_price(token_id, side="BUY")["price"])
    best_bid = float(client.get_price(token_id, side="SELL")["price"])
    spread = float(client.get_spread(token_id)["spread"])
    return {
        "midpoint": midpoint,
        "best_ask": best_ask,
        "best_bid": best_bid,
        "spread": spread,
    }


def get_positions(address=None):
    addr = address or FUNDER_ADDRESS
    response = requests.get(f"{DATA_API}/positions", params={"user": addr})
    positions = response.json()
    print(f"{datetime.now().strftime('%H:%M:%S')} - {len(positions)} open positions")
    return positions


def place_order(token_id, side, amount, price=None):
    if price is None:
        # Market order — amount is dollars to spend
        order = MarketOrderArgs(token_id=token_id, amount=amount, side=side, order_type=OrderType.FOK)
        signed = client.create_market_order(order)
        resp = client.post_order(signed, OrderType.FOK)
    else:
        # Limit order — amount is number of shares, price is per share
        order = OrderArgs(token_id=token_id, price=price, size=amount, side=side)
        signed = client.create_order(order)
        resp = client.post_order(signed, OrderType.GTC)
    return resp


# ── Layer 2: Strategy ─────────────────────────────────────────────────────────


def find_markets():
    # All tag IDs: https://gamma-api.polymarket.com/tags
    # Crypto=21, Politics=2, Finance=120
    # YES between 15¢–40¢: crowd says unlikely, bot says underpriced (bullish bot)
    min_price = 0.15
    max_price = 0.40
    min_volume = 10000  # liquidity filter: > $10K 24h volume
    sort_by = "volume24hr"

    markets = get_markets(tag_id=21)

    candidates = []
    for m in markets:
        prices = json.loads(m.get("outcomePrices", "[]"))
        volume = float(m.get("volume24hr", 0))
        if len(prices) >= 2:
            yes_price = float(prices[0])
            if min_price <= yes_price <= max_price and volume >= min_volume:
                token_ids = json.loads(m["clobTokenIds"])
                m["yes_token_id"] = token_ids[0]
                m["no_token_id"] = token_ids[1]
                candidates.append(m)

    # Most liquid first: better fills
    candidates.sort(key=lambda m: float(m.get(sort_by, 0)), reverse=True)

    print(f"{datetime.now().strftime('%H:%M:%S')} - Found {len(candidates)} markets matching strategy")
    return candidates


def should_trade(price_data):
    # Skip if spread is too wide: execution cost eats the edge
    max_spread = 0.05
    return price_data["spread"] < max_spread


# ── Layer 3: Main ─────────────────────────────────────────────────────────────


def main():
    ts = lambda: datetime.now().strftime("%H:%M:%S")

    print(f"{ts()} - Scanning markets...")
    markets = find_markets()

    if not markets:
        print(f"{ts()} - No markets match strategy criteria")
        print(f"{ts()} - Done.")
        return

    balance = get_balance()
    print(f"{ts()} - Balance: ${balance:.2f}")

    positions = get_positions()
    held_tokens = {p["asset"] for p in positions}

    amount = round(balance * TRADE_SIZE, 2)

    for market in markets:
        print(f"\n{ts()} - --- {market['question']} ---")

        if len(held_tokens) >= MAX_POSITIONS:
            print(f"{ts()} - Max positions reached, stopping")
            break

        if market["yes_token_id"] in held_tokens or market["no_token_id"] in held_tokens:
            print(f"{ts()} - Already in this market, skipping")
            continue

        price_data = get_price(market["yes_token_id"])
        print(f"{ts()} - YES price: {price_data['best_ask']:.2f} | Spread: {price_data['spread']:.2f}")

        if not should_trade(price_data):
            print(f"{ts()} - Spread too wide, skipping")
            continue

        print(f"{ts()} - Placing order...")
        place_order(market["yes_token_id"], BUY, amount=amount)
        held_tokens.add(market["yes_token_id"])
        print(f"{ts()} - ✓ Trade executed")

    print(f"\n{ts()} - Done.")


if __name__ == "__main__":
    main()
