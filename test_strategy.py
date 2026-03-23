import json
import ccxt
from strategies.engine import StrategyEngine
from data.handler import DataHandler
from execution.handler import ExecutionHandler

exchange = ccxt.kraken()
data_handler = DataHandler(exchange)
execution_handler = ExecutionHandler(exchange)
engine = StrategyEngine(data_handler, execution_handler)

print("Fetching targets...")
try:
    with open('data/targets.json', 'r') as f:
        targets = json.load(f)
except:
    targets = [{'symbol': 'NEAR/USD'}]
    
for target in targets[:5]:
    symbol = target['symbol']
    print(f"\n--- Testing {symbol} ---")
    bars = data_handler.get_historical_data(symbol, timeframe='15m', limit=50)
    if bars.empty:
        print("No data.")
        continue
        
    
    try:
        regime, meta = engine.determine_regime(symbol, bars)
    except:
        regime = "UNKNOWN"
    
    print(f"Regime: {regime}")
    
    global_trend = "NEUTRAL"
    bars['global_trend'] = global_trend
    
    allowed_strategies = engine.regime_strategies.get(regime, engine.regime_strategies['UNKNOWN'])
    
    best_signal = None
    best_score = -1
    
    for strat_name in allowed_strategies:
        strat = engine.strategies[strat_name]
        sig = strat.get_signal(symbol, bars)
        print(f"  [{strat_name}] -> {sig['signal']} (Score: {sig['score']})")
        
        if 'BUY' in sig['signal'] and sig['score'] > best_score:
            best_score = sig['score']
            best_signal = sig
            
    if best_signal:
        print(f"\n✅ BEST SIGNAL: {best_signal['strategy']} -> {best_signal['signal']} (Score: {best_signal['score']})")
    else:
        print("\n❌ NO BUY SIGNALS")
