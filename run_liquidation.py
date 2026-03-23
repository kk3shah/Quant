#!/usr/bin/env python3
"""
LIQUIDATION SCRIPT
Sells all assets to free up cash.
"""
import os
import ccxt
from dotenv import load_dotenv
from execution.handler import ExecutionHandler

load_dotenv()

def main():
    API_KEY = os.getenv('EXCHANGE_API_KEY')
    SECRET_KEY = os.getenv('EXCHANGE_SECRET_KEY')
    
    exchange = ccxt.kraken({
        'apiKey': API_KEY,
        'secret': SECRET_KEY,
        'enableRateLimit': True
    })
    
    handler = ExecutionHandler(exchange)
    handler.liquidate_all()

if __name__ == "__main__":
    main()
