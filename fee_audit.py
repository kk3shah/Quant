#!/usr/bin/env python3
"""
Fee Impact Analysis
Analyze how much fees and commissions are eating into profits.
"""
import os
import ccxt
import pandas as pd
from termcolor import colored
from dotenv import load_dotenv

load_dotenv()

def run_fee_audit():
    API_KEY = os.getenv('EXCHANGE_API_KEY')
    SECRET_KEY = os.getenv('EXCHANGE_SECRET_KEY')
    
    exchange = ccxt.kraken({
        'apiKey': API_KEY,
        'secret': SECRET_KEY
    })
    
    print("Fetching FULL trade history (Iterating Ledger)...")
    
    # KRAKEN SPECIFIC: Use Ledger to get all history, as fetch_my_trades is per-symbol or limited
    # We will fetch all ledger entries of type 'trade' and 'fee'
    
    all_ledger = []
    offset = 0
    BATCH_SIZE = 50
    
    while True:
        try:
            # Kraken pagination uses 'ofs' in params for offset
            current_batch = exchange.fetch_ledger(limit=BATCH_SIZE, params={'ofs': offset})
            if not current_batch:
                break
            
            all_ledger.extend(current_batch)
            offset += BATCH_SIZE
            print(f"  Fetched {len(all_ledger)} entries...", end='\r')
            
            if len(current_batch) < BATCH_SIZE:
                break
        except Exception as e:
            print(f"Error checking ledger: {e}")
            break
            
    print(f"\nTotal Ledger Entries: {len(all_ledger)}")
    
    # Filter for trades and fees
    # Convert to DataFrame
    df_ledger = pd.DataFrame(all_ledger)
    
    # We need to construct 'trades' from ledger entries or use the info directly
    # Kraken ledger 'trade' entries have 'amount' (qty), 'balance' (new balance), 'fee', 'refid'
    
    # Group by 'refid' (Trade ID) to combine the two sides of a trade (e.g. USD and BTC movements)
    # A single trade often generates 2 ledger entries (Asset -Qty, Quote +Qty) + Fee
    
    trades_data = []
    
    if not df_ledger.empty:
        # Filter only trade-related types
        trade_entries = df_ledger[df_ledger['type'].isin(['trade', 'margin'])]
        
        # Group by reference ID (The Trade ID)
        if 'refid' in trade_entries.columns:
            for refid, group in trade_entries.groupby('refid'):
                if not refid: continue
                
                # Analyze this trade cluster
                # Usually: 
                # 1. Spent: Negative currency amount
                # 2. Received: Positive currency amount
                # 3. Fee: Fee amount
                
                # Attempt to normalize into a "Trade" object
                try:
                    # Find which asset was bought/sold
                    # Using logical deduction: 
                    # If we lost USD and gained BTC, it's a BUY BTC
                    
                    plus = group[group['amount'] > 0]
                    minus = group[group['amount'] < 0]
                    
                    if plus.empty or minus.empty: continue # Internal transfer or weird state
                    
                    bought_asset = plus.iloc[0]['currency']
                    bought_qty = plus.iloc[0]['amount']
                    
                    sold_asset = minus.iloc[0]['currency']
                    sold_qty = abs(minus.iloc[0]['amount'])
                    
                    # Fee
                    # Kraken fees are often deducted from one side.
                    # Sum all fees in this group
                    total_fee_cost = 0
                    
                    def parse_fee(x):
                        if isinstance(x, dict): return float(x.get('cost', 0))
                        try: return float(x)
                        except: return 0.0
                        
                    total_fee_cost = group['fee'].apply(parse_fee).sum()

                    # Reconstruct standard 'trade' dict for our analysis
                    if sold_asset in ['USD', 'USDT', 'USDC', 'ZUSD']:
                        # Buying Crypto with USD
                        symbol = f"{bought_asset}/{sold_asset}"
                        side = 'buy'
                        price = sold_qty / bought_qty if bought_qty else 0
                        cost = sold_qty
                        amount = bought_qty
                    else:
                        # Selling Crypto for USD (or Crypto/Crypto)
                        symbol = f"{sold_asset}/{bought_asset}"
                        side = 'sell'
                        price = bought_qty / sold_qty if sold_qty else 0
                        cost = bought_qty # Value received in quote
                        amount = sold_qty
                    
                    trades_data.append({
                        'datetime': group.iloc[0]['datetime'],
                        'symbol': symbol,
                        'side': side,
                        'price': price,
                        'amount': amount,
                        'cost': cost,
                        'fee_cost': total_fee_cost,
                        'fee_currency': 'USD' # Simplified
                    })
                except Exception as e:
                    # print(f"Skipping trade group {refid}: {e}")
                    pass

    
    # SIMPLE FEE SUMMATION
    print(f"\nAnalyzing {len(df_ledger)} ledger entries for fees...")
    
    total_fees_usd = 0.0
    
    def get_fee_cost(x):
        if isinstance(x, dict):
            return float(x.get('cost', 0))
        try:
            return float(x)
        except:
            return 0.0
            
    if 'fee' in df_ledger.columns:
        total_fees_usd = df_ledger['fee'].apply(get_fee_cost).sum()
        
    print(colored(f"\n💸 TOTAL HISTORICAL FEES", "cyan"))
    print(f"Total entries: {len(df_ledger)}")
    print(f"Total Commission/Fees Paid: {colored(f'${total_fees_usd:.2f}', 'red', attrs=['bold'])}")
    
    # Calculate approx volume
    # Sum of absolute 'amount' for 'trade' entries where currency is USD/USDT/USDC (Spending or Receiving quote)
    # This is a Rough Estimate
    
    quote_currencies = ['USD', 'USDT', 'USDC', 'ZUSD']
    volume_est = 0
    if not df_ledger.empty and 'amount' in df_ledger.columns:
        trade_rows = df_ledger[df_ledger['type'] == 'trade']
        usd_rows = trade_rows[trade_rows['currency'].isin(quote_currencies)]
        volume_est = usd_rows['amount'].abs().sum() / 2 # Divide by 2 (approx) because ledger has both sides? 
        # Actually ledger usually has one entry per asset. If I buy BTC with USD, I get -USD entry and +BTC entry.
        # So summing abs(USD) movement is roughly volume.
        
        volume_est = usd_rows['amount'].abs().sum()

    print(f"Est. Total Volume: ${volume_est:.2f}")
    if volume_est > 0:
        print(f"Effective Fee Rate: {(total_fees_usd/volume_est)*100:.4f}%")

if __name__ == "__main__":
    run_fee_audit()
