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
    # Reduced to 2 slots: at ~$50 equity this gives ~$22/slot vs ~$15 with 3 slots.
    # More capital per trade → strategies have room to breathe before hitting stop.
    MAX_OPEN_POSITIONS = 2
    TARGET_POSITIONS_NUM = 2

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
    FEE_RATE = 0.0016       # 0.16% maker fee per side
    ROUND_TRIP_FEE = 0.0032 # 0.32% total 
    MIN_PROFIT_THRESHOLD = 0.02  # 2% min to sell (guarantees fee clearance)
    DEFAULT_ORDER_TYPE = 'limit'
    
    # ─── RISK:REWARD ───
    # 1:3 ratio: break-even win rate = 25% (well below historical 28.6%)
    # Old: 3% SL / 4% TP required 57% win rate — mathematically impossible.
    STOP_LOSS = 0.02        # 2% hard stop
    TAKE_PROFIT = 0.06      # 6% target
    
    # ─── KILL SWITCH & DAILY LIMITS ───
    MAX_DRAWDOWN = 0.20           # 20% max drawdown
    MAX_DAILY_TRADES = 10         # Max 10 trades per day
    MAX_DAILY_FEE_BUDGET = 5.00   # Stop if fees exceed $5.00
    
    # ─── ADVANCED RISK CONTROLS ───
    MAX_HOLD_TIME_HOURS = 6       # Give trades 6h to play out
    TRADING_HOURS = (0, 24)       # 24/7 Crypto Markets
    TREND_FILTER = True           # Only buy if price > SMA20
    
    def validate(self):
        if not self.API_KEY or not self.SECRET_KEY:
            raise ValueError("API Keys not found. Please set EXCHANGE_API_KEY and EXCHANGE_SECRET_KEY in .env file.")
        if not self.EXCHANGE_ID:
             raise ValueError("Exchange ID not found. Please set EXCHANGE_ID (e.g. kraken, coinbase) in .env file.")
