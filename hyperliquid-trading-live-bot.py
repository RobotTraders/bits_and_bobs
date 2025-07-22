import os
import ccxt
import pandas as pd
import ta
from dotenv import load_dotenv

load_dotenv()


# ==========================================
# PART 1: HYPERLIQUID CLIENT
# ==========================================

class HyperliquidClient:
    """Simple synchronous client for Hyperliquid exchange using CCXT."""
    
    def __init__(self, wallet_address: str, private_key: str):
        """Initialize the Hyperliquid client.
        
        Args:
            wallet_address: Your Hyperliquid wallet address
            private_key: Your wallet's private key
        """
        if not wallet_address:
            raise ValueError("wallet_address is required")
        
        if not private_key:
            raise ValueError("private_key is required")
            
        try:
            self.exchange = ccxt.hyperliquid({
                "walletAddress": wallet_address,
                "privateKey": private_key,
                "enableRateLimit": True,
            })
            self.markets = {}
            self._load_markets()
        except Exception as e:
            raise Exception(f"Failed to initialize exchange: {str(e)}")

    def _load_markets(self) -> None:
        """Load market data from the exchange."""
        try:
            self.markets = self.exchange.load_markets()
        except Exception as e:
            raise Exception(f"Failed to load markets: {str(e)}")

    def _amount_to_precision(self, symbol: str, amount: float) -> float:
        """Convert amount to exchange precision requirements.
        
        Args:
            symbol: Trading pair symbol
            amount: Order amount to format
            
        Returns:
            Amount formatted with correct precision as float
        """
        try:
            result = self.exchange.amount_to_precision(symbol, amount)
            return float(result)
        except Exception as e:
            raise Exception(f"Failed to format amount precision: {str(e)}")

    def _price_to_precision(self, symbol: str, price: float) -> float:
        """Convert price to exchange precision requirements.
        
        Args:
            symbol: Trading pair symbol
            price: Order price to format
            
        Returns:
            Price formatted with correct precision as float
        """
        try:
            result = self.exchange.price_to_precision(symbol, price)
            return float(result)
        except Exception as e:
            raise Exception(f"Failed to format price precision: {str(e)}")

    def get_current_price(self, symbol: str) -> float:
        """Get the current market price for a symbol.
        
        Args:
            symbol: Trading pair (e.g., "ETH/USDC:USDC")
            
        Returns:
            Current market price
        """
        try:
            return float(self.markets[symbol]["info"]["midPx"])
        except Exception as e:
            raise Exception(f"Failed to get price for {symbol}: {str(e)}")

    def fetch_balance(self) -> dict:
        """Fetch account balance information.
        
        Returns:
            Account balance data
        """
        try:
            result = self.exchange.fetch_balance()
            return result
        except Exception as e:
            raise Exception(f"Failed to fetch balance: {str(e)}")

    def fetch_positions(self, symbols: list[str]) -> list:
        """Fetch open positions for specified symbols.
        
        Args:
            symbols: List of trading pairs
            
        Returns:
            List of position dictionaries with active positions
        """
        try:
            positions = self.exchange.fetch_positions(symbols)
            return [pos for pos in positions if float(pos["contracts"]) != 0]
        except Exception as e:
            raise Exception(f"Failed to fetch positions: {str(e)}")

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 100) -> pd.DataFrame:
        """Fetch OHLCV candlestick data.
        
        Args:
            symbol: Trading pair symbol
            timeframe: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
            limit: Maximum number of candles to fetch
            
        Returns:
            DataFrame with OHLCV data
        """
        try:
            ohlcv_data = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            
            df = pd.DataFrame(
                data=ohlcv_data,
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.set_index("timestamp").sort_index()
            
            numeric_cols = ["open", "high", "low", "close", "volume"]
            df[numeric_cols] = df[numeric_cols].astype(float)
            
            return df
        except Exception as e:
            raise Exception(f"Failed to fetch OHLCV data: {str(e)}")
    
    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a symbol.
        
        Args:
            symbol: Trading pair symbol
            leverage: Leverage multiplier
            
        Returns:
            True if successful
        """
        try:
            self.exchange.set_leverage(leverage, symbol)
            return True
        except Exception as e:
            raise Exception(f"Failed to set leverage: {str(e)}")

    def set_margin_mode(self, symbol: str, margin_mode: str, leverage: int) -> bool:
        """Set margin mode for a symbol.
        
        Args:
            symbol: Trading pair symbol
            margin_mode: "isolated" or "cross"
            leverage: Required leverage multiplier for Hyperliquid
            
        Returns:
            True if successful
        """
        try:
            self.exchange.set_margin_mode(margin_mode, symbol, params={"leverage": leverage})
            return True
        except Exception as e:
            raise Exception(f"Failed to set margin mode: {str(e)}")

    def place_market_order(
        self, 
        symbol: str, 
        side: str, 
        amount: float,
        reduce_only: bool = False,
        take_profit_price: float | None = None,
        stop_loss_price: float | None = None
    ) -> dict:
        """Place a market order with optional take profit and stop loss.
        
        Args:
            symbol: Trading pair symbol
            side: "buy" or "sell"
            amount: Order size in contracts
            reduce_only: If True, order will only reduce position size
            take_profit_price: Optional price level to take profit
            stop_loss_price: Optional price level to stop loss
            
        Returns:
            Order execution details
        """
        try:
            formatted_amount = self._amount_to_precision(symbol, amount)
            
            price = float(self.markets[symbol]["info"]["midPx"])
            formatted_price = self._price_to_precision(symbol, price)
            
            params = {"reduceOnly": reduce_only}
            
            if take_profit_price is not None:
                formatted_tp_price = self._price_to_precision(symbol, take_profit_price)
                params["takeProfitPrice"] = formatted_tp_price
                
            if stop_loss_price is not None:
                formatted_sl_price = self._price_to_precision(symbol, stop_loss_price)
                params["stopLossPrice"] = formatted_sl_price
            
            order_info = {}
            order_info_final = {}
            
            order_info["market_order"] = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=formatted_amount,
                price=formatted_price,
                params=params
            )
            order_info_final["market_order"] = order_info["market_order"]["info"]
            
            if take_profit_price is not None:
                order_info["take_profit_order"] = self._place_take_profit_order(symbol, side, formatted_amount, formatted_price, take_profit_price)
                order_info_final["take_profit_order"] = order_info["take_profit_order"]["info"]
                
            if stop_loss_price is not None:
                order_info["stop_loss_order"] = self._place_stop_loss_order(symbol, side, formatted_amount, formatted_price, stop_loss_price)
                order_info_final["stop_loss_order"] = order_info["stop_loss_order"]["info"]
            
            return order_info_final
        except Exception as e:
            raise Exception(f"Failed to place market order: {str(e)}")

    def _place_take_profit_order(self, symbol: str, side: str, amount: float, price: float, take_profit_price: float) -> dict:
        """Internal method to place a take-profit order."""
        tp_price = self._price_to_precision(symbol, take_profit_price)
        close_side = "sell" if side == "buy" else "buy"
        return self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=close_side,
                amount=amount,
                price=price,
                params={"takeProfitPrice": tp_price, "reduceOnly": True},
            )

    def _place_stop_loss_order(self, symbol: str, side: str, amount: float, price: float, stop_loss_price: float) -> dict:
        """Internal method to place a stop-loss order."""
        sl_price = self._price_to_precision(symbol, stop_loss_price)
        close_side = "sell" if side == "buy" else "buy"
        return self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=close_side,
                amount=amount,
                price=price,
                params={"stopLossPrice": sl_price, "reduceOnly": True},
            )


def my_print(message: str, verbose: bool):
    if verbose:
        print(message)


# ==========================================
# PART 2: STRATEGY CONFIG
# ==========================================

# Trading parameters
params = {
    "symbol": "ETH/USDC:USDC",
    "timeframe": "4h",
    "position_size_pct": 5.0,
    "leverage": 1,
    "margin_mode": "isolated",  # "isolated" or "cross"
    "rsi_length": 14,
    "rsi_overbought": 70,
    "tp_pct": 10.0, 
    "sl_pct": 5.0,
}

# Trading conditions to ignore
ignore_longs = False
ignore_shorts = True
ignore_exit = False
ignore_tp = False
ignore_sl = False

# Verbosity
verbose = True 

# Define Technical Indicators
def compute_indicators(data): # check https://technical-analysis-library-in-python.readthedocs.io/en/latest/ta.html
    """Compute technical indicators"""
    data['RSI'] = ta.momentum.rsi(data['close'], window=params["rsi_length"])
    
    # data['ATR'] = ta.volatility.average_true_range(data['high'], data['low'], data['close'], window=params["..."])

    # data['EMAf'] = ta.trend.ema_indicator(data['close'], params["..."])
    # data['EMAs'] = ta.trend.ema_indicator(data['close'], params["..."])

    # MACD = ta.trend.MACD(data['close'], window_slow=params["..."], window_fast=params["..."], window_sign=params["..."])
    # data['MACD'] = MACD.macd()
    # data['MACD_histo'] = MACD.macd_diff()
    # data['MACD_signal'] = MACD.macd_signal()

    # BB = ta.volatility.BollingerBands(close=data['close'], window=params["..."], window_dev=params["..."])
    # data["BB_lower"] = BB.bollinger_lband()
    # data["BB_upper"] = BB.bollinger_hband()
    # data["BB_avg"] = BB.bollinger_mavg()
    return data

# Long Position Rules
def check_long_entry_condition(row, previous_candle):
    return previous_candle["RSI"] <= params["rsi_overbought"] < row["RSI"]

def check_long_exit_condition(row, previous_candle):
    return previous_candle["RSI"] >= params["rsi_overbought"] > row["RSI"]

def compute_long_tp_level(price):
    return price * (1 + params["tp_pct"] / 100)

def compute_long_sl_level(price):
    return price * (1 - params["sl_pct"] / 100)

# Short Position Rules
def check_short_entry_condition(row, previous_candle):
    pass

def check_short_exit_condition(row, previous_candle):
    pass

def compute_short_tp_level(price):
    pass

def compute_short_sl_level(price):
    pass

# Define position sizing rules
def calculate_position_size(balance):
    return balance * params["position_size_pct"] / 100


# ==========================================
# PART 3: TRADING BOT
# ==========================================

if __name__ == "__main__":
    
    try:
        # ==========================================
        # 1. Initialize Client
        # ==========================================
        wallet_address = os.getenv("HYPERLIQUID_WALLET_ADDRESS")
        private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY")
        client = HyperliquidClient(wallet_address, private_key)

        # ==========================================
        # 2. Get Account Information
        # ==========================================
        balance_info = client.fetch_balance()
        balance = float(balance_info["total"]["USDC"])
        my_print(f"Current balance: {balance} USDC", verbose)

        # ==========================================
        # 3. Get Market Data
        # ==========================================
        # Fetch OHLCV data
        df = client.fetch_ohlcv(params["symbol"], params["timeframe"])

        # Compute indicators
        df = compute_indicators(df)
        
        # Get current and previous candle
        current_candle = df.iloc[-2]
        previous_candle = df.iloc[-3]
        current_price = current_candle['close']

        # ==========================================
        # 4. Check Positions & Execute Strategy
        # ==========================================
        # Check for open positions
        positions = client.fetch_positions([params["symbol"]])
        current_position = positions[0] if positions else None

        if current_position:
            # ----------------------------------------
            # 4a. Position Management
            # ----------------------------------------
            position_side = current_position["side"].lower()
            
            # Check long exit
            if position_side == "long" and not ignore_longs and not ignore_exit:
                if check_long_exit_condition(current_candle, previous_candle):
                    my_print("Long exit signal detected", verbose)
                    client.place_market_order(
                        params["symbol"], 
                        "sell", 
                        abs(current_position["contracts"]),
                        reduce_only=True
                    )
                    my_print("Long position closed", verbose)
            
            # Check short exit
            elif position_side == "short" and not ignore_shorts and not ignore_exit:
                if check_short_exit_condition(current_candle, previous_candle):
                    my_print("Short exit signal detected", verbose)
                    client.place_market_order(
                        params["symbol"], 
                        "buy", 
                        abs(current_position["contracts"]),
                        reduce_only=True
                    )
                    my_print("Short position closed", verbose)

        else:
            # ----------------------------------------
            # 4b. Setup Trading Account
            # ----------------------------------------
            # Set leverage and margin mode before opening new positions
            client.set_leverage(
                symbol=params["symbol"],
                leverage=params["leverage"]
            )
            
            client.set_margin_mode(
                symbol=params["symbol"],
                margin_mode=params["margin_mode"],
                leverage=params["leverage"]
            )
            
            # ----------------------------------------
            # 4c. Entry Management
            # ----------------------------------------
            if not ignore_longs and check_long_entry_condition(current_candle, previous_candle):
                my_print("Long entry signal detected", verbose)
                
                # Calculate position size
                position_size = calculate_position_size(balance)
                amount = position_size / current_price
                
                # Calculate TP/SL levels only if not ignored
                tp_price = None
                sl_price = None
                
                if not ignore_tp:
                    tp_price = compute_long_tp_level(current_price)
                    
                if not ignore_sl:
                    sl_price = compute_long_sl_level(current_price)
                    
                my_print(f"Opening long position with TP at {tp_price} and SL at {sl_price}", verbose)
                
                # Open position with optional TP/SL
                orders = client.place_market_order(
                    params["symbol"], 
                    "buy", 
                    amount,
                    take_profit_price=tp_price,
                    stop_loss_price=sl_price
                )
                
                if orders.get("market_order"):
                    my_print(f"Long position opened: {orders['market_order']['resting']}", verbose)
                    
                    if orders.get("take_profit_order"):
                        my_print(f"Long take profit order placed: {orders['take_profit_order']['resting']}", verbose)
                    
                    if orders.get("stop_loss_order"):
                        my_print(f"Long stop loss order placed: {orders['stop_loss_order']['resting']}", verbose)
            
            # Check short entry
            elif not ignore_shorts and check_short_entry_condition(current_candle, previous_candle):
                my_print("Short entry signal detected", verbose)
                
                # Calculate position size
                position_size = calculate_position_size(balance)
                amount = position_size / current_price
                
                # Calculate TP/SL levels only if not ignored
                tp_price = None
                sl_price = None
                
                if not ignore_tp:
                    tp_price = compute_short_tp_level(current_price)
                    
                if not ignore_sl:
                    sl_price = compute_short_sl_level(current_price)
                    
                my_print(f"Opening short position with TP at {tp_price} and SL at {sl_price}", verbose)
                
                # Open position with optional TP/SL
                orders = client.place_market_order(
                    params["symbol"], 
                    "sell", 
                    amount,
                    take_profit_price=tp_price,
                    stop_loss_price=sl_price
                )
                
                if orders.get("market_order"):
                    my_print(f"Short position opened: {orders['market_order']['resting']}", verbose)
                    
                    if orders.get("take_profit_order"):
                        my_print(f"Short take profit order placed: {orders['take_profit_order']['resting']}", verbose)
                    
                    if orders.get("stop_loss_order"):
                        my_print(f"Short stop loss order placed: {orders['stop_loss_order']['resting']}", verbose)

    except Exception as e:
        my_print(f"Error in main loop: {e}", verbose)
        exit(1) 