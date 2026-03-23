import schedule
import time
import sys
import os
import datetime
import ccxt
from termcolor import colored


class _Tee:
    """Write to multiple streams simultaneously (console + log file)."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
            except Exception:
                pass

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self):
        return False

from config import Config
from data.handler import DataHandler
from execution.handler import ExecutionHandler
from strategies.engine import StrategyEngine
from reporting.daily_report import print_daily_report
try:
    import notifier
except ImportError:
    notifier = None

# Module-level engine to persist kill-switch state & daily counters across cycles
_engine = None

def run_bot():
    global _engine
    print(colored("\n--- Starting Trading Routine (Reformed) ---", "cyan"))
    
    # 1. Setup
    conf = Config()
    try:
        conf.validate()
    except ValueError as e:
        print(colored(str(e), "red"))
        return

    # Connect to Exchange
    exchange_class = getattr(ccxt, conf.EXCHANGE_ID)
    exchange = exchange_class({
        'apiKey': conf.API_KEY,
        'secret': conf.SECRET_KEY,
        'enableRateLimit': True,
    })
    
    # Initialize Handlers
    data_handler = DataHandler(exchange)
    execution_handler = ExecutionHandler(exchange)
    
    # Check connection
    try:
        balance = execution_handler.get_account()
        print(colored(f"Connected to {conf.EXCHANGE_ID.upper()}.", "green"))
    except Exception as e:
        print(colored(f"Failed to connect to {conf.EXCHANGE_ID}. Check API Keys.", "red"))
        return

    # 1B. INITIAL SWEEP: Sell positions that have hit the minimum profit threshold.
    # Use MIN_PROFIT_THRESHOLD (2%) so we don't cut winners at 0.7% every cycle.
    if not conf.PAPER_TRADING:
        execution_handler.liquidate_profitable_positions(min_profit_pct=conf.MIN_PROFIT_THRESHOLD)

    # 2. Run Strategy Engine (persist instance for kill-switch & daily counters)
    if _engine is None:
        _engine = StrategyEngine(data_handler, execution_handler)
    else:
        # Update handler references in case exchange reconnected
        _engine.data_handler = data_handler
        _engine.execution_handler = execution_handler
    
    print(colored(f"Scanning Top {conf.SCAN_TOP_N} Liquid Pairs...", "cyan"))
    universe = data_handler.get_top_pairs(quote_currency=conf.QUOTE_CURRENCY, limit=conf.SCAN_TOP_N)
    
    if not universe:
        print(colored("No pairs found. Check connection.", "yellow"))
    
    # BATCH ANALYSIS (Ranking & Execution)
    try:
        _engine.analyze_batch(universe)
    except Exception as e:
        print(colored(f"Fatal Error in Batch Analysis: {e}", "red")) 

    # 3. Report
    print_daily_report(execution_handler)
    
    print(colored("--- Routine Finished ---\n", "cyan"))

def _get_current_equity() -> float:
    """Quick equity fetch for notifications — returns 0.0 on any error."""
    try:
        conf = Config()
        exchange_class = getattr(ccxt, conf.EXCHANGE_ID)
        exchange = exchange_class({'apiKey': conf.API_KEY, 'secret': conf.SECRET_KEY, 'enableRateLimit': True})
        balance = exchange.fetch_balance()
        usd = balance['total'].get('USD', 0) or balance['total'].get('ZUSD', 0)
        return float(usd)
    except Exception:
        return 0.0


def _send_daily_summary():
    """Runs at 20:00 UTC daily, pushes equity + open position summary to Telegram."""
    if not notifier:
        return
    try:
        import json, os
        conf = Config()
        exchange_class = getattr(ccxt, conf.EXCHANGE_ID)
        exchange = exchange_class({'apiKey': conf.API_KEY, 'secret': conf.SECRET_KEY, 'enableRateLimit': True})
        balance = exchange.fetch_balance()
        usd = balance['total'].get('USD', 0) or balance['total'].get('ZUSD', 0)
        equity = float(usd)

        # Add value of crypto holdings
        fiat = {'USD', 'ZUSD', 'KFEE', 'CAD', 'EUR', 'GBP'}
        open_positions = []
        for asset, qty in balance['total'].items():
            if asset in fiat or not qty or qty < 0.0001:
                continue
            try:
                ticker = exchange.fetch_ticker(f"{asset}/USD")
                price = ticker['last']
                val = qty * price
                if val < 1.0:
                    continue
                equity += val
                pos_data = {}
                pos_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'positions.json')
                if os.path.exists(pos_file):
                    with open(pos_file) as f:
                        pos_data = json.load(f)
                entry = pos_data.get(f"{asset}/USD", {}).get('entry_price', 0)
                pnl_pct = ((price - entry) / entry * 100) if entry else 0
                open_positions.append({'symbol': f"{asset}/USD", 'pnl_pct': pnl_pct})
            except Exception:
                pass

        # Starting equity from session.json
        starting_equity = 0.0
        session_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'session.json')
        if os.path.exists(session_file):
            with open(session_file) as f:
                starting_equity = json.load(f).get('starting_equity', 0.0)

        day_trades = _engine.daily_trade_count if _engine and hasattr(_engine, 'daily_trade_count') else 0
        notifier.notify_daily_summary(equity, starting_equity, open_positions, day_trades)
    except Exception as e:
        print(f"[NOTIFIER] Daily summary error: {e}")


def main():
    conf = Config()
    interval = conf.BOT_INTERVAL_MINUTES
    mode = "PAPER" if conf.PAPER_TRADING else "LIVE"

    # ─── DAILY-ROLLING LOG FILE ───
    logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, f"bot_{datetime.date.today():%Y-%m-%d}.log")
    _log_file = open(log_path, 'a', buffering=1)  # line-buffered
    sys.stdout = _Tee(sys.__stdout__, _log_file)
    sys.stderr = _Tee(sys.__stderr__, _log_file)

    print(colored(f"Antigravity Crypto Bot Initialized [{mode} MODE].", "magenta"))
    print(f"Scheduled to run every {interval} minutes.")
    print(f"Max {conf.MAX_DAILY_TRADES} trades/day | {conf.STOP_LOSS*100}% SL / {conf.TAKE_PROFIT*100}% TP | Kill-switch at {conf.MAX_DRAWDOWN*100}% DD")

    # Schedule the trading job
    schedule.every(interval).minutes.do(run_bot)

    # Daily summaries at 9am, 12pm, 5pm, 9pm EST (UTC-5)
    schedule.every().day.at("14:00").do(_send_daily_summary)  # 9am EST
    schedule.every().day.at("17:00").do(_send_daily_summary)  # 12pm EST
    schedule.every().day.at("22:00").do(_send_daily_summary)  # 5pm EST
    schedule.every().day.at("02:00").do(_send_daily_summary)  # 9pm EST

    # Run once immediately
    run_bot()

    # Startup notification (after first cycle so equity is fresh)
    if notifier:
        try:
            _startup_equity = _get_current_equity()
            notifier.notify_startup(_startup_equity, mode)
        except Exception:
            pass
    
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
