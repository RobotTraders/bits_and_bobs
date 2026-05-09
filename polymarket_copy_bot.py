import requests
from py_clob_client_v2 import ClobClient, MarketOrderArgs, OrderType, Side


#####################
### CONFIGURATION ###
#####################
TARGET_ADDRESS = "0x0000000000000000000000000000000000000000"

FUNDER_ADDRESS = "your-wallet-address"
PRIVATE_KEY = "your-private-key"
SIGNATURE_TYPE = 1  # 0=EOA (Wallets), 1=Email/Magic, 2=Browser proxy

BET_AMOUNT = 2.0            # Amount in $ to spend on each copied bet

DRY_RUN = True              # True = preview only, False = execute bets
# DRY_RUN = False


############
### APIs ###
############
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
PROFILE_API = "https://gamma-api.polymarket.com"


def get_profile_name(wallet_address: str) -> str:
    response = requests.get(f"{PROFILE_API}/public-profile", params={"address": wallet_address})
    response.raise_for_status()
    profile = response.json()
    return profile.get("name") or profile.get("pseudonym") or wallet_address[:10] + "..."


def get_positions(wallet_address: str) -> list:
    response = requests.get(
        f"{DATA_API}/positions",
        params={"user": wallet_address, "sizeThreshold": 0}
    )
    response.raise_for_status()
    return response.json()


def get_latest_bet(wallet_address: str) -> dict | None:
    response = requests.get(
        f"{DATA_API}/activity",
        params={"user": wallet_address, "limit": 20}
    )
    response.raise_for_status()

    for activity in response.json():
        if activity.get("type") == "TRADE" and activity.get("side") == "BUY":
            return activity
    return None


def already_has_position(my_positions: list, conditionId: str, outcomeIndex: int) -> bool:
    key = lambda c, o: f"{c}_{o}"
    target_key = key(conditionId, outcomeIndex)
    my_keys = {key(p["conditionId"], p["outcomeIndex"]) for p in my_positions}
    return target_key in my_keys


def get_clob_client():
    client = ClobClient(
        CLOB_API,
        key=PRIVATE_KEY,
        chain_id=137,
        signature_type=SIGNATURE_TYPE,
        funder=FUNDER_ADDRESS
    )
    creds = client.derive_api_key()
    client.set_api_creds(creds)
    return client


def place_bet(client, token_id: str, amount: float):
    order = MarketOrderArgs(
        token_id=token_id,
        amount=amount,
        side=Side.BUY,
        order_type=OrderType.FOK
    )
    signed_order = client.create_market_order(order)
    client.post_order(signed_order, OrderType.FOK)


############
### MAIN ###
############
def main():
    target_name = get_profile_name(TARGET_ADDRESS)

    print("\n" + "=" * 60)
    print(f"  Copying: {target_name}")
    print(f"  Bet amount: ${BET_AMOUNT}")
    print(f"  Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    print("=" * 60 + "\n")

    print("[1] Fetching target's latest bet...")
    latest = get_latest_bet(TARGET_ADDRESS)
    if not latest:
        print("    No recent bets found. Nothing to copy.")
        return

    title = latest["title"][:50]
    outcome = latest["outcome"]
    target_size = latest["size"]
    price = latest["price"]

    print(f"    Found: {title}")
    print(f"    Position: {target_size:.1f} {outcome} @ {price * 100:.1f}¢")


    print("\n[2] Checking your positions...")
    my_positions = get_positions(FUNDER_ADDRESS)
    if my_positions:
        print(f"    Your open positions:")
        for pos in my_positions:
            print(f"      - {pos['title'][:40]}: {pos['outcome']}")
    else:
        print("    You have no open positions")

    if already_has_position(my_positions, latest["conditionId"], latest["outcomeIndex"]):
        print(f"    Already in this market. Nothing to do!")
        return

    print("    Not in this market yet. Proceeding...")

    print("\n[3] Placing bet...")
    client = get_clob_client()
    if DRY_RUN:
        print(f"    DRY RUN - would buy ${BET_AMOUNT:.2f} of {outcome}")
    else:
        place_bet(client, latest["asset"], BET_AMOUNT)
        print(f"    Done! Bought ${BET_AMOUNT:.2f} of {outcome}")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"\nPolymarket API Error: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        print(f"\nError: {e}")
