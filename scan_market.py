#!/usr/bin/env python3
"""
MARKET SCANNER (The Control Tower) — Reformed
Strategy: Find LIQUID coins that HAVEN'T MOVED MUCH yet.
Let the strategy engine decide when to actually buy.

Old approach: Buy coins already up 3-20% = buying AFTER the move
New approach: Find liquid, stable coins = let strategies find the entry
"""
import os
import ccxt
import json
import time
from datetime import datetime
from termcolor import colored
from dotenv import load_dotenv
from config import Config

load_dotenv()

# CONFIGURATION
SCAN_INTERVAL_SECONDS = 900  # 15 Minutes
OUTPUT_FILE = "data/targets.json"

# REFORMED CRITERIA
# Key insight: Don't buy coins that already pumped.
# Find liquid coins and let the strategy engine decide timing.
CRITERIA = {
    'min_price': 0.01,       
    'max_price': Config.PRICE_UPPER_LIMIT,
    'min_gain_24h': -2.0,   # Consolidating pairs
    'max_gain_24h': 2.0,    # Consolidating pairs
    'min_volume': 100000,    # Broadened from $200k to catch more candidates
    'max_candidates': Config.SCAN_TOP_N,
}

def scan_market():
    API_KEY = os.getenv('EXCHANGE_API_KEY')
    SECRET_KEY = os.getenv('EXCHANGE_SECRET_KEY')
    
    exchange = ccxt.kraken({
        'apiKey': API_KEY,
        'secret': SECRET_KEY,
        'enableRateLimit': True
    })
    
    print(colored(f"--- Running Market Scan ---", "cyan"))
    
    try:
        exchange.load_markets()
        tickers = exchange.fetch_tickers()
        
        candidates = []
        
        # Filter Universe (USD pairs only, no stablecoins, no geo-restricted coins)
        restricted = ['WAR', 'FIDD', 'GRASS']  # CA:ON restricted pairs

        # Load auto-blacklisted pairs from persistent file (populated on CA:ON rejections)
        _blacklist_file = 'data/restricted_pairs.json'
        if os.path.exists(_blacklist_file):
            try:
                with open(_blacklist_file) as _f:
                    _dynamic = json.load(_f)
                restricted = list(set(restricted) | set(_dynamic))
            except Exception:
                pass
        
        # Exclude stablecoins (expanded list) and fiat-pair suffixes.
        # USDG, USDD, FRAX, TUSD, GUSD, PYUSD were previously slipping through.
        STABLECOIN_PREFIXES = [
            'USDT', 'USDC', 'DAI', 'PAX', 'USDG', 'USDD',
            'FRAX', 'TUSD', 'GUSD', 'PYUSD', 'BUSD', 'LUSD',
        ]
        FIAT_SUFFIXES = ['AUD', 'EUR', 'GBP', 'CAD']

        universe = [
            s for s in tickers.keys()
            if s.endswith('/USD')
            and not any(s.split('/')[0] == sc for sc in STABLECOIN_PREFIXES)
            and not any(x in s for x in FIAT_SUFFIXES)
            and not any(s.startswith(f"{r}/") for r in restricted)
            and s not in Config.BLOCKED_SYMBOLS
        ]
        
        print(f"  Scanning {len(universe)} USD pairs...")
        
        for symbol in universe:
            try:
                ticker = tickers[symbol]
                
                # DATA CHECKS
                if 'last' not in ticker or ticker['last'] is None: continue
                if 'quoteVolume' not in ticker or ticker['quoteVolume'] is None: continue
                if 'percentage' not in ticker or ticker['percentage'] is None: continue
                
                price = ticker['last']
                volume = ticker['quoteVolume']
                change_24h = ticker['percentage']
                
                # 1. PRICE CHECK
                if price > CRITERIA['max_price'] or price < CRITERIA['min_price']:
                    continue
                    
                # 2. VOLUME CHECK (Liquidity — the most important filter)
                if volume < CRITERIA['min_volume']:
                    continue
                    
                # 3. STABILITY CHECK (not already pumped or crashing)
                if change_24h < CRITERIA['min_gain_24h']:
                    continue  # Extreme Crash (>15%) — avoid falling knives
                    
                if change_24h > CRITERIA['max_gain_24h']:
                    continue  # Extreme Pump (>40%) — too late
                
                candidates.append({
                    'symbol': symbol,
                    'price': price,
                    'change_24h': change_24h,
                    'volume': volume,
                    'last_updated': datetime.now().isoformat(),
                    'rationale': f"Liquid ({change_24h:+.1f}%, ${volume/1000:.0f}k vol)"
                })
                
            except Exception:
                pass
        
        # Sort by consolidation (closest to 0 absolute gain) then Volume
        candidates.sort(key=lambda x: (abs(x['change_24h']), -x['volume']))
        
        # Cap at max candidates
        candidates = candidates[:CRITERIA['max_candidates']]
        
        if candidates:
            print(f"  [CONTROL TOWER] Found {len(candidates)} targets. Updating data/targets.json")
        
        # Save to JSON
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(candidates, f, indent=2)
            
        print(f"\n  Scan complete: {len(candidates)} targets saved to {OUTPUT_FILE}")
        
    except Exception as e:
        print(f"Scanner Failure: {e}")

if __name__ == "__main__":
    while True:
        scan_market()
        print(f"Sleeping for {SCAN_INTERVAL_SECONDS/60:.0f} minutes...")
        time.sleep(SCAN_INTERVAL_SECONDS)
