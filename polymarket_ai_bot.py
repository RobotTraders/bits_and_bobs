import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

# ── Configuration ─────────────────────────────────────────────────────────────

load_dotenv()

PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY")
FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# 0 = EOA wallet (MetaMask, hardware wallet)
# 1 = Email wallet (account created with email)
# 2 = Browser wallet proxy
SIGNATURE_TYPE = 1

MARKET_URL = "https://polymarket.com/event/bitcoin-above-on-march-21/bitcoin-above-72k-on-march-21"  # Paste the market URL here
BET_AMOUNT = 10.0
DRY_RUN = True
# DRY_RUN = False

MIN_CONFIDENCE = "Medium"  # Minimum claude confidence to place a bet (Low, Medium, High)
# MIN_CONFIDENCE = "Low"   # Bet on any confidence level
# MIN_CONFIDENCE = "High"  # Only bet if Claude is highly confident

ANTHROPIC_MODEL = "claude-sonnet-4-6"  # https://docs.anthropic.com/en/docs/about-claude/models
# ANTHROPIC_MODEL = "claude-opus-4-6"
WEB_SEARCH_MAX = 3  # Max web searches Claude can do (0 to disable)


# ── The Prompt ────────────────────────────────────────────────────────────────

PROMPT = """I'm going to ask you a question. Based on your knowledge, reasoning,
and any web research you can do, answer by calling the provided tool.

{question}"""

TOOL_DEFINITION = {
    "name": "answer",
    "description": "Submit a structured answer to the question.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["Yes", "No"]},
            "confidence": {"type": "string", "enum": ["Low", "Medium", "High"]},
            "reasoning": {"type": "string", "description": "2-3 sentence rationale"},
        },
        "required": ["decision", "confidence", "reasoning"],
    },
}

# ── Functions ─────────────────────────────────────────────────────────────────

def get_market(url):
    slug = url.rstrip("/").split("/")[-1]

    r = requests.get("https://gamma-api.polymarket.com/events", params={"slug": slug})
    if r.status_code != 200:
        raise Exception(f"Polymarket API error {r.status_code}: {r.text}")
    events = r.json()

    if events and events[0].get("markets"):
        markets = events[0]["markets"]
        if len(markets) > 1:
            print(f"{ts()} This event has multiple markets:\n")
            for m in markets:
                print(f"  - {m['question']}")
                print(f"    https://polymarket.com/market/{m['slug']}\n")
            print(f"{ts()} Copy one of the URLs above into MARKET_URL and run again.")
            raise SystemExit()
        m = markets[0]
    else:
        r = requests.get("https://gamma-api.polymarket.com/markets", params={"slug": slug})
        if r.status_code != 200:
            raise Exception(f"Polymarket API error {r.status_code}: {r.text}")
        markets = r.json()
        if not markets:
            raise ValueError(f"No market found for: {slug}")
        m = markets[0]

    prices = json.loads(m.get("outcomePrices", "[]"))
    tokens = json.loads(m.get("clobTokenIds", "[]"))
    return {
        "question": m["question"],
        "yes_price": float(prices[0]),
        "no_price": float(prices[1]),
        "yes_token_id": tokens[0],
        "no_token_id": tokens[1],
    }

def place_order(client, token_id, amount):
    order = MarketOrderArgs(token_id=token_id, amount=amount, side=BUY, order_type=OrderType.FOK)
    signed = client.create_market_order(order)
    return client.post_order(signed, OrderType.FOK)

def ask_claude(question):
    prompt = PROMPT.format(question=question)
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    tools = [TOOL_DEFINITION]
    if WEB_SEARCH_MAX > 0:
        tools.insert(0, {"type": "web_search_20250305", "name": "web_search", "max_uses": WEB_SEARCH_MAX})

    payload = {
        "model": ANTHROPIC_MODEL, "max_tokens": 1024,
        "tools": tools,
        "messages": [{"role": "user", "content": prompt}],
    }
    r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=60)
    if r.status_code != 200:
        raise Exception(f"Claude API error {r.status_code}: {r.text}")
    for block in r.json().get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == "answer":
            return block["input"]
    raise ValueError("Claude did not return an answer tool call")

def ts():
    return datetime.now().strftime("%H:%M:%S")

# ── Main ──────────────────────────────────────────────────────────────────────

CONFIDENCE_RANK = {"Low": 1, "Medium": 2, "High": 3}

def main():
    print(f"\n{ts()} Fetching market...")
    try:
        market = get_market(MARKET_URL)
    except Exception as e:
        print(f"{ts()} Polymarket error: {e}")
        return
    print(f"{ts()} Market: \"{market['question']}\"")
    print(f"{ts()} Current odds: Yes {market['yes_price']*100:.1f}% | No {market['no_price']*100:.1f}%")

    print(f"\n{ts()} Asking Claude...")
    try:
        result = ask_claude(market["question"])
    except Exception as e:
        print(f"{ts()} Claude error: {e}")
        return

    decision = result["decision"]
    confidence = result["confidence"]
    reasoning = result["reasoning"]

    print(f"\n{ts()} Reasoning: {reasoning}")
    print(f"{ts()} Decision: {decision}")
    print(f"{ts()} Confidence: {confidence}")

    if CONFIDENCE_RANK[confidence] < CONFIDENCE_RANK[MIN_CONFIDENCE]:
        print(f"\n{ts()} Confidence too low ({confidence} < {MIN_CONFIDENCE}). Skipping bet.")
        return

    token_id = market["yes_token_id"] if decision == "Yes" else market["no_token_id"]
    if DRY_RUN:
        print(f"\n{ts()} DRY RUN — would bet ${BET_AMOUNT:.2f} on {decision}")
    else:
        client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=137,
                            signature_type=SIGNATURE_TYPE, funder=FUNDER_ADDRESS)
        client.set_api_creds(client.derive_api_key())
        print(f"\n{ts()} Placing bet... ${BET_AMOUNT:.2f} on {decision}")
        try:
            place_order(client, token_id, BET_AMOUNT)
        except Exception as e:
            print(f"{ts()} Polymarket error: {e}")
            return
        print(f"{ts()} ✓ Bet placed")

    print(f"\n{ts()} Done.")

if __name__ == "__main__":
    main()
