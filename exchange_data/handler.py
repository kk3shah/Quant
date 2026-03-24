import ccxt
import pandas as pd
from datetime import datetime, timedelta
from termcolor import colored

class DataHandler:
    def __init__(self, exchange: ccxt.Exchange):
        self.exchange = exchange
        self.markets_loaded = False

    def load_markets(self):
        if not self.markets_loaded:
            self.exchange.load_markets()
            self.markets_loaded = True

    def get_top_pairs(self, quote_currency='CAD', limit=20):
        """
        Fetches all pairs for the quote currency, sorts by 24h volume, returns top N.
        """
        self.load_markets()
        
        # Filter for CAD pairs (e.g. BTC/CAD, ETH/CAD)
        # Exclude stablecoins or weird pairs if needed
        pairs = []
        for symbol, market in self.exchange.markets.items():
            if market['quote'] == quote_currency and market['active']:
                pairs.append(market)
                
        # Fetch tickers to get volume
        # Note: fetch_tickers might be heavy if getting ALL, but usually fine for <100 pairs
        try:
            tickers = self.exchange.fetch_tickers([p['symbol'] for p in pairs])
            
            # Sort by Quote Volume (baseVolume * price) or just baseVolume
            # Kraken tickers usually have 'quoteVolume' or we calculate roughly
            sorted_tickers = sorted(
                tickers.items(), 
                key=lambda item: item[1].get('quoteVolume', 0) or 0, 
                reverse=True
            )
            
            top_symbols = [symbol for symbol, ticker in sorted_tickers[:limit]]
            
            # Ensure Forex pairs are included if active (User Request)
            forex_pairs = ['USD/CAD', 'EUR/CAD', 'EUR/USD']
            for fx in forex_pairs:
                if fx in self.exchange.markets and self.exchange.markets[fx]['active']:
                    if fx not in top_symbols:
                        top_symbols.insert(0, fx) # Prioritize FX at top
            
            print(f"  [DEBUG] Found {len(pairs)} {quote_currency} pairs. Top 3 by vol: {[s for s, t in sorted_tickers[:3]]}")
            return top_symbols
        except Exception as e:
            print(f"Error fetching top pairs: {e}")
            return []

    def get_stratified_volatile_universe(self, quote_currency='USD'):
        """
        Selects 10 Coinds based on User Spec:
        - 3 Big Cap (High Volume) -> Pick Most Volatile
        - 3 Mid Cap (Mid Volume) -> Pick Most Volatile
        - 4 Small Cap (Lower Volume) -> Pick Most Volatile
        
        Metric for Volatility: (High - Low) / Last  (Daily Range %)
        """
        try:
            # 1. Fetch all tickers to get 24h stats
            tickers = self.exchange.fetch_tickers()
            
            # 2. Filter for Quote Currency & Active
            valid_tickers = []
            for symbol, ticker in tickers.items():
                if f'/{quote_currency}' in symbol and ticker['quoteVolume'] and ticker['last'] and ticker['high'] and ticker['low']:
                    # Exclude stablecoins and weird pairs if possible? 
                    # For now, just trust volume sorting.
                    if 'USDT' in symbol or 'USDC' in symbol or 'DAI' in symbol: continue 
                    
                    volatility = (ticker['high'] - ticker['low']) / ticker['last']
                    valid_tickers.append({
                        'symbol': symbol,
                        'volume': ticker['quoteVolume'],
                        'volatility': volatility
                    })
            
            # 3. Sort by Volume to determine Tiers (Proxy for Market Cap)
            valid_tickers.sort(key=lambda x: x['volume'], reverse=True)
            
            # Define Tiers (Assumes we have at least 100 valid pairs, otherwise slices handle it)
            # Big: Top 15 by Volume
            # Mid: Rank 16-50
            # Small: Rank 51-150
            
            big_pool = valid_tickers[:15]
            mid_pool = valid_tickers[15:50]
            small_pool = valid_tickers[50:150]
            
            # 4. Select Most Volatile from each Tier
            # Sort individual pools by 'volatility' desc
            big_pool.sort(key=lambda x: x['volatility'], reverse=True)
            mid_pool.sort(key=lambda x: x['volatility'], reverse=True)
            small_pool.sort(key=lambda x: x['volatility'], reverse=True)
            
            selected = []
            
            # Pick 3 Big
            for t in big_pool[:3]: selected.append(t['symbol'])
            # Pick 3 Mid
            for t in mid_pool[:3]: selected.append(t['symbol'])
            # Pick 4 Small
            for t in small_pool[:4]: selected.append(t['symbol'])
            
            print(colored(f"  [UNIVERSE] Selected 10 Volatile Assets:", "cyan"))
            print(f"    Big Cap:  {selected[:3]}")
            print(f"    Mid Cap:  {selected[3:6]}")
            print(f"    Small Cap: {selected[6:]}")
            
            return selected
            
        except Exception as e:
            print(f"Error generating stratified universe: {e}")
            return []

    def get_historical_data(self, symbol, timeframe='1d', limit=100):
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not ohlcv:
                return pd.DataFrame()

            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            print(f"Error fetching data for {symbol}: {e}")
            return pd.DataFrame()

    def get_latest_price(self, symbol):
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker['last']
        except Exception as e:
            print(f"Error fetching latest price for {symbol}: {e}")
            return None
