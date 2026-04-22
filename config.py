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
    MAX_OPEN_POSITIONS   = 3      # hard cap; fewer, better positions

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
    MAX_SPREAD_PCT = 0.004        # skip entries when bid/ask spread is wider than 0.40%
    MIN_EXPECTED_GROSS_MOVE = 0.025  # require at least 2.5% plausible upside before entering
    
    # ─── RISK:REWARD ───
    # 2.5:1 ratio → break-even win rate = 28.6%.
    # WHY CHANGED: 2% SL was too tight for crypto. A single 15-min candle
    # regularly moves 2-5% on normal volatility. ALGO was bought at $0.088,
    # stopped out at -2% ($0.087), then rallied to $0.107 (+20%) — the bot
    # sold a winner as a loser. 4% SL gives positions room to breathe through
    # normal volatility before mean-reversion strategies can work.
    STOP_LOSS = 0.035       # 3.5% hard stop (was 4% — tightened slightly, 12h losses showed trades rarely recovered past -2%)
    TAKE_PROFIT = 0.08      # 8% target (maintains 2.3:1 R:R)
    MIN_PROFIT_THRESHOLD = 0.018  # take-profit floor after fees/spread buffer

    # ─── KILL SWITCH & DAILY LIMITS ───
    MAX_DRAWDOWN = 0.20           # 20% max drawdown
    MAX_DAILY_TRADES = 3          # verified fill count; avoid fee churn
    MAX_DAILY_FEE_BUDGET = 1.00   # Stop if fees exceed $1.00
    MAX_DAILY_LOSS_PCT = 0.005    # stop new entries if equity is down 0.5% from daily open

    # ─── ADVANCED RISK CONTROLS ───
    MAX_HOLD_TIME_HOURS = 2       # 2h safety net (momentum_exit at 1h catches most; data: 3h+ holds have 9% win rate)
    TRADING_HOURS = (0, 24)       # 24/7 Crypto Markets
    TREND_FILTER = True           # Only buy if price > SMA20
    ENABLE_OPTIMIZER = False      # freeze self-learning until it uses verified Kraken net P&L
    TARGET_MAX_AGE_MINUTES = 30   # reject stale Control Tower targets
    MAX_ENTRIES_PER_CYCLE = 1     # do not spray multiple fresh entries in one scan

    # Verified-fill audit showed these symbols were the largest repeat loss drivers.
    BLOCKED_SYMBOLS = {
        'FIS/USD', 'BILLY/USD', 'DRV/USD', 'ADI/USD', 'SAHARA/USD',
        'KAVA/USD', 'ZRX/USD', 'NIGHT/USD', 'SKY/USD', 'OOB/USD',
        'PUMP/USD', 'KAS/USD',
    }
    
    def validate(self):
        if not self.API_KEY or not self.SECRET_KEY:
            raise ValueError("API Keys not found. Please set EXCHANGE_API_KEY and EXCHANGE_SECRET_KEY in .env file.")
        if not self.EXCHANGE_ID:
             raise ValueError("Exchange ID not found. Please set EXCHANGE_ID (e.g. kraken, coinbase) in .env file.")
