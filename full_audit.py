#!/usr/bin/env python3
"""
Full Account Audit
Fetches LEDGER history (not just trades) to find every single cent.
"""
import os
import ccxt
import pandas as pd
from dotenv import load_dotenv
from termcolor import colored

load_dotenv()

def print_header(title):
    print(colored(f"\n{'='*60}\n{title}\n{'='*60}", 'cyan', attrs=['bold']))

def run_audit():
    # Setup Exchange
    API_KEY = os.getenv('EXCHANGE_API_KEY')
    SECRET_KEY = os.getenv('EXCHANGE_SECRET_KEY')
    
    if not API_KEY or not SECRET_KEY:
        print("ERROR: API keys not found!")
        return
    
    exchange = ccxt.kraken({
        'apiKey': API_KEY,
        'secret': SECRET_KEY,
        'enableRateLimit': True
    })
    
    print("Connecting to Kraken...")
    
    # 1. Fetch Current Balance
    print_header("CURRENT BALANCE")
    try:
        balance = exchange.fetch_balance()
        total_usd_value = 0
        
        # Get USD value of all assets
        if 'total' in balance:
            for currency, amount in balance['total'].items():
                if amount > 0:
                    try:
                        if currency == 'USD' or currency == 'ZUSD':
                            usd_val = amount
                        elif currency == 'KFEE':
                            usd_val = 0 # Fee credits
                        else:
                            ticker = exchange.fetch_ticker(f"{currency}/USD")
                            usd_val = amount * ticker['last']
                        
                        total_usd_value += usd_val
                        print(f"{currency:<5}: {amount:>12.6f} (~${usd_val:.2f})")
                    except:
                        print(f"{currency:<5}: {amount:>12.6f} (Price fetch failed)")
        
        print(f"\nTOTAL ACCOUNT VALUE: ${total_usd_value:.2f}")
        
    except Exception as e:
        print(f"Error fetching balance: {e}")
        return

    # 2. Fetch Full Ledger (Deposits, Withdrawals, Fees, Trades)
    print_header("FULL LEDGER ANALYSIS")
    try:
        # Fetch last 1000 ledger entries (should cover everything for a new bot)
        ledger = exchange.fetch_ledger(limit=None)  # Fetch maximum available
        df = pd.DataFrame(ledger)
        
        if df.empty:
            print("No ledger history found.")
            return

        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        # Filter types
        deposits = df[df['type'] == 'deposit']
        withdrawals = df[df['type'] == 'withdrawal']
        trades = df[df['type'] == 'trade']
        fees = df[df['type'] == 'fee'] # Some exchanges verify fees separately
        margin = df[df['type'] == 'margin'] # Margin interest
        
        # Calculate Totals
        total_deposited = deposits['amount'].sum() if not deposits.empty else 0
        total_withdrawn = withdrawals['amount'].sum() if not withdrawals.empty else 0
        
        # Calculate Fees
        total_fees = 0.0
        if 'fee' in df.columns:
            # Helper to extract fee cost safely
            def get_fee_cost(x):
                if isinstance(x, dict):
                    return float(x.get('cost', 0))
                try:
                    return float(x)
                except:
                    return 0.0
            
            total_fees = df['fee'].apply(get_fee_cost).sum()
            print(f"Direct Fees Paid: ${total_fees:.2f}")
        
        
        print(f"Total Deposited:  ${total_deposited:.2f}")
        print(f"Total Withdrawn:  ${total_withdrawn:.2f}")
        print(f"Direct Fees Paid: ${total_fees:.2f} (Estimate from ledger column)")
        
        net_input = total_deposited - total_withdrawn
        
        if net_input == 0 and total_deposited == 0:
            print(colored("\n⚠️  No deposits found in recent ledger history.", "yellow"))
            print("Assuming start capital based on user report: $100.00")
            net_input = 100.0
        
        # Only calculate if we have a valid base
        if net_input != 0:
            pnl_realized = total_usd_value - net_input
            roi_pct = (pnl_realized / net_input) * 100
            print(f"\nNET INPUT:       ${net_input:.2f}")
            print(f"CURRENT VALUE:   ${total_usd_value:.2f}")
            print(colored(f"REAL P&L:        ${pnl_realized:.2f} ({roi_pct:.1f}%)", 'red' if pnl_realized < 0 else 'green'))
        else:
             print(f"\nCURRENT VALUE:   ${total_usd_value:.2f}")
        
        # 3. Trade Analysis by Trade ID (Grouping by 'refid' or similar if possible, or just raw)
        print_header("TOP LOSS GENERATORS (Ledger Based)")
        
        # Parse fee cost from trades
        trade_losses = []
        
        # It's hard to reconstruct exact P&L per trade just from ledger without matching opens/closes
        # But we can look at the 'net' amount change per asset
        
        # Alternative: Just look at the trades again but with fee context
        all_trades = exchange.fetch_my_trades(limit=None)
        trades_df = pd.DataFrame(all_trades)
        
        if not trades_df.empty:
             trades_df['cost_including_fee'] = trades_df.apply(lambda x: x['cost'] + (x['fee']['cost'] if x['fee'] else 0), axis=1)
             
             print(f"Total Trading Volume: ${trades_df['cost'].sum():.2f}")
             
             total_fees_from_trades = trades_df.apply(lambda x: x['fee']['cost'] if x['fee'] else 0, axis=1).sum()
             print(f"Total Trading Fees:   ${total_fees_from_trades:.2f}")

             # Re-run the P&L logic but strictly summing realized P&L
             # ... (Similar logic to analyze_trades.py but we trust the Ledger P&L more)
             
    except Exception as e:
        print(f"Error analyzing ledger: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_audit()
