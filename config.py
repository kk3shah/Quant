import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    EXCHANGE_ID = os.getenv('EXCHANGE_ID', 'kraken')
    API_KEY = os.getenv('EXCHANGE_API_KEY')
    SECRET_KEY = os.getenv('EXCHANGE_SECRET_KEY')
    
    # ─── MODE ───
    PAPER_TRADING = False  # SET TO False WHEN READY FOR LIVE
    
    # ─── TRADING SETTINGS ───
    QUOTE_CURRENCY = 'USD'
    TIMEFRAME = '15m'       # 15-Min candles for intraday volatility
    BOT_INTERVAL_MINUTES = 5  # Run every 5 min
    
    # ─── UNIVERSE ───
    SCAN_TOP_N = 50         # Broader Universe for Multi-Strategy Engine
    PRICE_UPPER_LIMIT = 100.0  # Allow mid-caps
    MIN_VOLUME_24H = 200000    # $200k min daily volume for small-cap runners
    
    # ─── POSITION SIZING ───
    # Fixed $15 per trade (Kraken minimum). Slot count is derived dynamically
    # from available cash: slots = floor(cash × 0.90 / FIXED_ALLOCATION_USD).
    # This means the bot always makes as many $15 trades as cash allows rather
    # than concentrating into fewer large positions.
    FIXED_ALLOCATION_USD = 15.0   # exactly Kraken's minimum — every trade is this size
    MAX_OPEN_POSITIONS   = 5      # hard cap; actual slots limited by cash / 15

    # ─── DUST FILTER ───
    MIN_POSITION_VALUE_USD = 1.00  # ignore any holding worth less than $1

    # ─── DIVERSIFICATION: sector/correlation groups ───
    # Don't hold more than 1 coin from the same group simultaneously.
    CORRELATION_GROUPS = {
        'btc_large_cap':  {'BTC', 'ETH', 'SOL', 'AVAX', 'LINK'},
        'mid_cap_alts':   {'XRP', 'ADA', 'DOT', 'ATOM', 'NEAR', 'HBAR'},
        'meme_spec':      {'DOGE', 'SHIB', 'PEPE', 'FLOKI', 'TRUMP', 'BONK'},
        'defi_infra':     {'UNI', 'AAVE', 'CRV', 'MKR', 'COMP', 'SNX'},
        'layer2_rollup':  {'OP', 'ARB', 'MATIC', 'IMX', 'ZK', 'STRK'},
        'liquid_stable':  {'USDT', 'USDC', 'DAI', 'FRAX', 'USD1'},
    }
    
    # ─── COMMISSION & FEES (Limit Orders = Maker Rate) ───
    FEE_RATE = 0.003        # 0.30% maker fee per side (actual Kraken rate for this account)
    ROUND_TRIP_FEE = 0.006  # 0.60% total (maker both sides)
    TAKER_FEE_RATE = 0.006  # 0.60% taker fee (used for stop-loss market sells)
    DEFAULT_ORDER_TYPE = 'limit'  # CRITICAL: limit orders = 0.30% maker vs 0.60% taker — halves fees
    
    # ─── RISK:REWARD ───
    # 2.5:1 ratio → break-even win rate = 28.6%.
    # WHY CHANGED: 2% SL was too tight for crypto. A single 15-min candle
    # regularly moves 2-5% on normal volatility. ALGO was bought at $0.088,
    # stopped out at -2% ($0.087), then rallied to $0.107 (+20%) — the bot
    # sold a winner as a loser. 4% SL gives positions room to breathe through
    # normal volatility before mean-reversion strategies can work.
    STOP_LOSS = 0.035       # 3.5% hard stop (was 4% — tightened slightly, 12h losses showed trades rarely recovered past -2%)
    TAKE_PROFIT = 0.08      # 8% target (maintains 2.3:1 R:R)
    MIN_PROFIT_THRESHOLD = 0.008  # 0.8% min to sell (clears 0.60% round-trip maker fees + small buffer)

    # ─── KILL SWITCH & DAILY LIMITS ───
    MAX_DRAWDOWN = 0.20           # 20% max drawdown
    MAX_DAILY_TRADES = 10         # Max 10 trades per day
    MAX_DAILY_FEE_BUDGET = 5.00   # Stop if fees exceed $5.00

    # ─── ADVANCED RISK CONTROLS ───
    MAX_HOLD_TIME_HOURS = 2       # 2h safety net (momentum_exit at 1h catches most; data: 3h+ holds have 9% win rate)
    TRADING_HOURS = (0, 24)       # 24/7 Crypto Markets
    TREND_FILTER = True           # Only buy if price > SMA20
    
    def validate(self):
        if not self.API_KEY or not self.SECRET_KEY:
            raise ValueError("API Keys not found. Please set EXCHANGE_API_KEY and EXCHANGE_SECRET_KEY in .env file.")
        if not self.EXCHANGE_ID:
             raise ValueError("Exchange ID not found. Please set EXCHANGE_ID (e.g. kraken, coinbase) in .env file.")
