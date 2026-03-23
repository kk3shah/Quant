import ccxt
import os
from dotenv import load_dotenv
from termcolor import colored

load_dotenv()

def debug_kraken():
    print("--- Kraken Live Debug ---")
    exchange = ccxt.kraken({
        'apiKey': os.getenv('EXCHANGE_API_KEY'),
        'secret': os.getenv('EXCHANGE_SECRET_KEY'),
    })
    
    try:
        balance = exchange.fetch_balance()
        print(colored("Balance fetched successfully.", "green"))
        
        if 'total' in balance:
            print("\nTotal Assets:")
            for currency, amount in balance['total'].items():
                if amount > 0:
                    print(f"  {currency}: {amount}")
        
        # Test a ticker fetch
        try:
            ticker = exchange.fetch_ticker('BTC/USD')
            print(f"\nBTC/USD Last: {ticker['last']}")
        except Exception as e:
            print(colored(f"\nFailed to fetch BTC/USD: {e}", "red"))

    except Exception as e:
        print(colored(f"Critical Error: {e}", "red"))

if __name__ == "__main__":
    debug_kraken()
