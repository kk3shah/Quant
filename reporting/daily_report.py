from termcolor import colored

from termcolor import colored

def print_daily_report(execution_handler):
    """
    Generates a detailed portfolio report.
    """
    balance = execution_handler.get_account()
    positions = execution_handler.get_positions()
    exchange = execution_handler.exchange
    
    print("="*60)
    print(colored("DAILY PORTFOLIO DASHBOARD", "cyan", attrs=['bold']))
    print("="*60)
    
    total_equity = 0.0
    
    # 1. CASH
    usd_cash = 0.0
    if balance and 'total' in balance:
        usd_cash = balance['total'].get('USD', 0.0)
        if usd_cash == 0 and 'ZUSD' in balance['total']:
            usd_cash = balance['total'].get('ZUSD', 0.0)
        
        total_equity += usd_cash
        print(f"💵 USD CASH:     ${usd_cash:,.2f}")
    
    # 2. POSITIONS
    print("\n📦 ASSETS HOLDINGS:")
    print(f"{'Asset':<8} {'Qty':<10} {'Price':<10} {'Value':<10} {'Entry':<10} {'P&L %':<8}")
    print("-" * 65)
    
    fiat = ['USD', 'ZUSD', 'KFEE', 'CAD']
    
    for asset, qty in positions.items():
        if qty < 0.0001: continue
        if asset in fiat: continue # Already counted in cash (approx)
        
        # Try to match pair
        symbol = f"{asset}/USD"
        current_price = 0.0
        entry_price = 0.0
        val = 0.0
        pnl_pct = 0.0
        
        try:
            ticker = exchange.fetch_ticker(symbol)
            current_price = ticker['last']
            val = qty * current_price
            total_equity += val
            
            entry_price = execution_handler.get_entry_price(symbol)
            if entry_price and entry_price > 0:
                pnl_pct = (current_price - entry_price) / entry_price * 100
                
        except:
             pass # Price fetch failed
             
        pnl_str = f"{pnl_pct:+.2f}%" if entry_price else "N/A"
        pnl_color = 'green' if pnl_pct > 0 else 'red'
        
        print(f"{asset:<8} {qty:<10.4f} ${current_price:<9.4f} ${val:<9.2f} ${entry_price if entry_price else 0:<9.4f} {colored(pnl_str, pnl_color)}")

    print("-" * 65)
    print(colored(f"💰 TOTAL EQUITY: ${total_equity:,.2f}", "green", attrs=['bold']))
    print("="*60 + "\n")
