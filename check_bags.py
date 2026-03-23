import os
import ccxt
from dotenv import load_dotenv

load_dotenv()
exchange = ccxt.kraken({
    'apiKey': os.getenv('EXCHANGE_API_KEY'),
    'secret': os.getenv('EXCHANGE_SECRET_KEY')
})

balance = exchange.fetch_balance()
print('\nOPEN POSITIONS (Assets Held):')
total_val = 0
for currency, amount in balance['total'].items():
    if amount > 0.0001 and currency not in ['ZUSD', 'KFEE']: # Keep USD to show cash
        try:
            if currency == 'USD':
                val = amount
            else:
                ticker = exchange.fetch_ticker(f'{currency}/USD')
                val = amount * ticker['last']
            
            total_val += val
            print(f'{currency}: {amount} (~${val:.2f})')
        except:
             print(f'{currency}: {amount} (Price unknown)')

print(f"\nTotal Portfolio Value: ${total_val:.2f}")
