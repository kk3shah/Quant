#!/usr/bin/env python3
"""
Trade Analysis Script
Downloads and analyzes all historical trades from Kraken to understand losses.
"""
import os
import ccxt
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from collections import defaultdict
import json

load_dotenv()

def fetch_all_trades(exchange, since_days=30):
    """Fetch all trades from the last N days."""
    since = exchange.milliseconds() - (since_days * 24 * 60 * 60 * 1000)
    
    all_trades = []
    exchange.load_markets()
    
    # Get all USD pairs (our trading universe)
    exchange.load_markets()
    
    # OPTIMIZATION: Get symbols from Closed Orders first
    # This ensures we ONLY analyze pairs the user has actually traded.
    print("Fetching past orders to identify traded pairs...")
    seen_symbols = set()
    try:
        # Fetch last 1000 orders (should cover 30 days easily for a bot)
        # Kraken might paginate, but 1000 is a good start
        orders = exchange.fetch_closed_orders(since=since, limit=None) # Limit None might default to specific number in CCXT
        
        for o in orders:
            seen_symbols.add(o['symbol'])
            
        print(f"  > Found activity on {len(seen_symbols)} pairs: {seen_symbols}")
        
    except Exception as e:
        print(f"  > Could not fetch closed orders ({e}). Falling back to Top 50 volume scan.")
        # Fallback to top volume
        all_symbols = [s for s in exchange.symbols if '/USD' in s and 'USDT' not in s]
        tickers = exchange.fetch_tickers(all_symbols)
        sorted_pairs = sorted(tickers.items(), key=lambda x: x[1]['quoteVolume'] or 0, reverse=True)
        seen_symbols = [pair[0] for pair in sorted_pairs[:50]]

        seen_symbols = [pair[0] for pair in sorted_pairs[:50]]
    
    # ADDED: Ensure we scan assets we currently hold
    try:
        seen_symbols = set(seen_symbols) # Normalize to set
        balance = exchange.fetch_balance()
        for currency, amount in balance['total'].items():
            if amount > 0 and currency != 'USD' and currency != 'KFEE':
                # Construct pair (assuming USD base)
                pair = f"{currency}/USD"
                if pair in exchange.symbols:
                    print(f"  > Adding held asset to scan: {pair}")
                    seen_symbols.add(pair)
    except Exception as e:
        print(f"  > Error checking balance for symbols: {e}")

    symbols = list(seen_symbols) # Unique list

    print(f"Fetching executed trades for {len(symbols)} pairs...")
    
    for symbol in symbols:
        try:
            trades = exchange.fetch_my_trades(symbol, since=since, limit=500)
            if trades:
                all_trades.extend(trades)
                print(f"  {symbol}: {len(trades)} trades")
        except Exception as e:
            if 'Unknown asset pair' not in str(e):
                print(f"  {symbol}: Error - {e}")
    
    return all_trades

def analyze_trades(trades):
    """Analyze trades to find patterns in losses."""
    if not trades:
        print("No trades found!")
        return None
    
    # Convert to DataFrame
    df = pd.DataFrame(trades)
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['date'] = df['datetime'].dt.date
    
    print(f"\n{'='*60}")
    print(f"TRADE ANALYSIS REPORT")
    print(f"{'='*60}")
    print(f"Total Trades: {len(df)}")
    print(f"Date Range: {df['datetime'].min()} to {df['datetime'].max()}")
    print(f"Unique Symbols: {df['symbol'].nunique()}")
    
    # Separate buys and sells
    buys = df[df['side'] == 'buy']
    sells = df[df['side'] == 'sell']
    
    print(f"\nBuy Trades: {len(buys)}")
    print(f"Sell Trades: {len(sells)}")
    
    # Calculate total spent and received
    total_spent = (buys['cost']).sum()
    total_received = (sells['cost']).sum()
    
    print(f"\nTotal Spent (Buys): ${total_spent:.2f}")
    print(f"Total Received (Sells): ${total_received:.2f}")
    
    # Trading P&L by Symbol (Match buys to sells)
    print(f"\n{'='*60}")
    print(f"P&L BY SYMBOL (Completed Round-Trips)")
    print(f"{'='*60}")
    
    pnl_by_symbol = {}
    symbol_analysis = {}
    
    for symbol in df['symbol'].unique():
        symbol_trades = df[df['symbol'] == symbol].sort_values('datetime')
        symbol_buys = symbol_trades[symbol_trades['side'] == 'buy']
        symbol_sells = symbol_trades[symbol_trades['side'] == 'sell']
        
        total_buy_cost = symbol_buys['cost'].sum()
        total_buy_qty = symbol_buys['amount'].sum()
        total_sell_cost = symbol_sells['cost'].sum()
        total_sell_qty = symbol_sells['amount'].sum()
        
        # Only analyze completed round trips
        if total_buy_qty > 0 and total_sell_qty > 0:
            # Proportional P&L
            qty_matched = min(total_buy_qty, total_sell_qty)
            
            avg_buy_price = total_buy_cost / total_buy_qty
            avg_sell_price = total_sell_cost / total_sell_qty
            
            pnl = (avg_sell_price - avg_buy_price) * qty_matched
            pnl_pct = ((avg_sell_price / avg_buy_price) - 1) * 100
            
            pnl_by_symbol[symbol] = {
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'num_buys': len(symbol_buys),
                'num_sells': len(symbol_sells),
                'avg_buy': avg_buy_price,
                'avg_sell': avg_sell_price,
                'total_volume': total_buy_cost + total_sell_cost
            }
            
    # Detailed trade analysis
            symbol_analysis[symbol] = []
            for _, trade in symbol_trades.iterrows():
                symbol_analysis[symbol].append({
                    'time': trade['datetime'].strftime('%Y-%m-%d %H:%M'),
                    'side': trade['side'],
                    'price': trade['price'],
                    'amount': trade['amount'],
                    'cost': trade['cost'],
                    'fee': trade['fee']['cost'] if trade['fee'] else 0
                })
    
    # Sort by P&L
    sorted_pnl = sorted(pnl_by_symbol.items(), key=lambda x: x[1]['pnl'])
    
    total_pnl = 0
    total_fees = 0
    winners = 0
    losers = 0
    
    print(f"\n{'Symbol':<15} {'Gross P&L':>10} {'Fees':>8} {'Net P&L':>10} {'Net%':>7} {'Buys':>5} {'Sells':>5}")
    print("-" * 90)
    
    for symbol, data in sorted_pnl:
        gross_pnl = data['pnl']
        
        # Calculate fees for this symbol
        symbol_trades = df[df['symbol'] == symbol]
        symbol_fees = symbol_trades.apply(lambda x: x['fee']['cost'] if x['fee'] else 0, axis=1).sum()
        
        net_pnl = gross_pnl - symbol_fees
        
        total_pnl += net_pnl
        total_fees += symbol_fees
        
        if net_pnl >= 0:
            winners += 1
        else:
            losers += 1
        
        marker = "🔴" if net_pnl < 0 else "🟢"
        print(f"{marker} {symbol:<12} ${gross_pnl:>9.2f} ${symbol_fees:>7.2f} ${net_pnl:>9.2f} {data['pnl_pct']:>6.2f}% {data['num_buys']:>5} {data['num_sells']:>5}")
    
    print("-" * 90)
    print(f"{'TOTAL':<15} ${total_pnl:>9.2f} (Fees: ${total_fees:.2f})")
    print(f"\nNet Win Rate: {winners}/{winners+losers} ({winners/(winners+losers)*100:.1f}%)")
    
    # Export detailed data
    output = {
        'summary': {
            'total_trades': len(df),
            'total_net_pnl': total_pnl,
            'total_fees': total_fees,
            'win_rate': winners / (winners + losers) if (winners + losers) > 0 else 0,
            'winners': winners,
            'losers': losers,
            'total_spent': total_spent,
            'total_received': total_received
        },
        'by_symbol': pnl_by_symbol,
        'detailed_trades': symbol_analysis
    }
    
    with open('trade_analysis.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    return output

def main():
    # Setup Exchange
    API_KEY = os.getenv('EXCHANGE_API_KEY')
    SECRET_KEY = os.getenv('EXCHANGE_SECRET_KEY')
    
    if not API_KEY or not SECRET_KEY:
        print("ERROR: API keys not found in .env file!")
        return
    
    exchange = ccxt.kraken({
        'apiKey': API_KEY,
        'secret': SECRET_KEY,
        'enableRateLimit': True
    })
    
    print("Connecting to Kraken...")
    
    # Fetch trades
    trades = fetch_all_trades(exchange, since_days=30)
    
    # Analyze
    analysis = analyze_trades(trades)
    
    if analysis:
        print(f"\n{'='*60}")
        print(f"KEY FINDINGS")
        print(f"{'='*60}")
        
        summary = analysis['summary']
        
        print(f"\n📊 Total Net P&L: ${summary['total_net_pnl']:.2f}")
        print(f"📉 Fees Paid:     ${summary['total_fees']:.2f}")
        print(f"📈 Win Rate: {summary['win_rate']*100:.1f}%")
        print(f"💰 Total Spent: ${summary['total_spent']:.2f}")
        print(f"💵 Total Received: ${summary['total_received']:.2f}")
        
        if summary['total_net_pnl'] < 0:
            print(f"\n⚠️  DIAGNOSIS: The bot is generating net LOSSES.")
            print(f"   Common issues to investigate:")
            print(f"   1. Entry timing (buying after the move)")
            print(f"   2. Exit timing (selling too early or too late)")
            print(f"   3. Fee erosion (too many small trades)")
            print(f"   4. Wrong market conditions (trending vs ranging)")

if __name__ == "__main__":
    main()
