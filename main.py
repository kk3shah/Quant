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
_cycle_count = 0
_last_summary_cycle = 0   # send summary every 6 cycles (~30 min at 5-min intervals)

def run_bot():
    global _engine, _cycle_count, _last_summary_cycle
    _cycle_count += 1
    print(colored(f"\n--- Starting Trading Routine (Reformed) [cycle #{_cycle_count}] ---", "cyan"))
    
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

    # Send Telegram summary every 6 cycles (~30 min) so you always see activity
    if notifier and (_cycle_count - _last_summary_cycle) >= 6:
        _last_summary_cycle = _cycle_count
        _send_daily_summary()

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
    """Comprehensive Telegram report: equity in USD+CAD, all positions, slot/cash status, bot health."""
    if not notifier:
        return
    try:
        import json, os, time as _time
        conf = Config()
        exchange_class = getattr(ccxt, conf.EXCHANGE_ID)
        exchange = exchange_class({'apiKey': conf.API_KEY, 'secret': conf.SECRET_KEY, 'enableRateLimit': True})
        balance = exchange.fetch_balance()

        # ── Cash ──────────────────────────────────────────────────
        usd_cash = float(balance['total'].get('USD', 0) or balance['total'].get('ZUSD', 0))

        # ── CAD rate ──────────────────────────────────────────────
        cad_rate = 1.37  # fallback
        try:
            cad_rate = exchange.fetch_ticker('USDCAD')['last']
        except Exception:
            try:
                cad_rate = exchange.fetch_ticker('USD/CAD')['last']
            except Exception:
                pass

        # ── Load local position state ─────────────────────────────
        base = os.path.dirname(os.path.abspath(__file__))
        pos_file = os.path.join(base, 'data', 'positions.json')
        pos_data = {}
        if os.path.exists(pos_file):
            try:
                with open(pos_file) as f:
                    pos_data = json.load(f)
            except Exception:
                pass

        # ── Build positions list ───────────────────────────────────
        fiat = {'USD', 'ZUSD', 'KFEE', 'CAD', 'EUR', 'GBP', 'USDT', 'USDC'}
        positions_detail = []
        total_crypto_value = 0.0

        for asset, qty in balance['total'].items():
            if asset in fiat or not qty or qty < 0.0001:
                continue
            try:
                ticker = exchange.fetch_ticker(f"{asset}/USD")
                price  = ticker['last']
                value  = qty * price
                if value < conf.MIN_POSITION_VALUE_USD:
                    continue
                total_crypto_value += value

                symbol    = f"{asset}/USD"
                p         = pos_data.get(symbol, {})
                entry     = p.get('entry_price') or 0
                peak      = p.get('peak_price') or price
                strategy  = p.get('strategy') or '—'
                entry_ms  = p.get('entry_time') or 0
                hold_h    = (_time.time() * 1000 - entry_ms) / 3_600_000 if entry_ms else 0
                pnl_pct   = ((price - entry) / entry * 100) if entry else 0
                pnl_usd   = (price - entry) * qty if entry else 0
                peak_pct  = ((peak  - entry) / entry * 100) if entry else 0

                positions_detail.append({
                    'symbol': symbol, 'qty': qty, 'price': price,
                    'value': value, 'entry': entry, 'pnl_pct': pnl_pct,
                    'pnl_usd': pnl_usd, 'peak_pct': peak_pct,
                    'strategy': strategy, 'hold_h': hold_h,
                })
            except Exception:
                pass

        equity      = usd_cash + total_crypto_value
        equity_cad  = equity * cad_rate
        cash_cad    = usd_cash * cad_rate

        # ── Session P&L ───────────────────────────────────────────
        starting_equity = equity
        session_file = os.path.join(base, 'data', 'session.json')
        if os.path.exists(session_file):
            try:
                with open(session_file) as f:
                    starting_equity = json.load(f).get('starting_equity', equity)
            except Exception:
                pass

        session_pnl     = equity - starting_equity
        session_pnl_pct = (session_pnl / starting_equity * 100) if starting_equity else 0
        pnl_sign        = '+' if session_pnl >= 0 else ''
        pnl_emoji       = '📈' if session_pnl >= 0 else '📉'

        # ── Slot maths ────────────────────────────────────────────
        n_pos           = len(positions_detail)
        slots_free      = conf.MAX_OPEN_POSITIONS - n_pos
        target_per_slot = (equity * 0.90) / conf.TARGET_POSITIONS_NUM
        avail_per_slot  = (usd_cash * 0.90) / max(slots_free, 1)
        alloc           = min(target_per_slot, avail_per_slot)

        day_trades  = _engine.daily_trade_count  if _engine and hasattr(_engine, 'daily_trade_count')  else 0
        day_fees    = _engine.daily_fee_total     if _engine and hasattr(_engine, 'daily_fee_total')    else 0.0

        # ── EST label ────────────────────────────────────────────
        now_utc  = datetime.datetime.utcnow()
        now_est  = now_utc - datetime.timedelta(hours=5)
        ts_label = now_est.strftime('%I:%M %p EST  •  %a %d %b %Y')

        # ── Build message ─────────────────────────────────────────
        lines = [
            f"📊 <b>QUANT BOT REPORT</b>",
            f"<i>{ts_label}</i>",
            f"",
            f"💰 <b>EQUITY</b>",
            f"  USD: <b>${equity:,.2f}</b>   CAD: <b>${equity_cad:,.2f}</b>",
            f"  Session start: ${starting_equity:,.2f}",
            f"  {pnl_emoji} Net P&L: {pnl_sign}${session_pnl:.2f} ({pnl_sign}{session_pnl_pct:.2f}%)",
            f"",
        ]

        if positions_detail:
            lines.append(f"📦 <b>OPEN POSITIONS  ({n_pos}/{conf.MAX_OPEN_POSITIONS} slots)</b>")
            lines.append("─────────────────────────")
            for p in positions_detail:
                p_sign = '+' if p['pnl_pct'] >= 0 else ''
                p_emoji = '🟢' if p['pnl_pct'] >= 0 else '🔴'
                lines += [
                    f"{p_emoji} <b>{p['symbol']}</b>",
                    f"  Entry ${p['entry']:.4f} → Now ${p['price']:.4f}",
                    f"  Qty: {p['qty']:.4f}  |  Value: ${p['value']:.2f}",
                    f"  P&L: <b>{p_sign}{p['pnl_pct']:.2f}%</b> ({p_sign}${p['pnl_usd']:.2f})  |  Peak: {p_sign}{p['peak_pct']:.2f}%",
                    f"  Strategy: {p['strategy']}  |  Held: {p['hold_h']:.1f}h",
                    f"",
                ]
            lines.append("─────────────────────────")
        else:
            lines += [
                f"🔴 <b>NO OPEN POSITIONS</b>",
                f"  Bot is scanning every 5 min but holds nothing.",
                f"  Revenue = $0 while idle. Check slot/cash below.",
                f"",
            ]

        # ── Cash / slot status ────────────────────────────────────
        lines += [
            f"💵 <b>CASH &amp; SLOTS</b>",
            f"  Cash: ${usd_cash:.2f} USD  (${cash_cad:.2f} CAD)",
            f"  Slots: {n_pos} used / {conf.MAX_OPEN_POSITIONS} total  →  {slots_free} free",
            f"  Target/slot: ${target_per_slot:.2f}  |  Avail/slot: ${avail_per_slot:.2f}",
        ]

        if alloc < 15.0 and slots_free > 0:
            lines.append(f"  ⚠️ Alloc ${alloc:.2f} &lt; Kraken $15 min → entries blocked")
        elif slots_free == 0:
            lines.append(f"  ✅ All slots filled")
        else:
            lines.append(f"  ✅ Ready to enter (alloc ${alloc:.2f})")

        # ── Daily activity ────────────────────────────────────────
        lines += [
            f"",
            f"📈 <b>TODAY</b>",
            f"  Trades executed: {day_trades}",
            f"  Fees paid: ${day_fees:.4f}",
        ]

        notifier._send('\n'.join(lines))

    except Exception as e:
        print(f"[NOTIFIER] Daily summary error: {e}")
        try:
            notifier._send(f"⚠️ Summary failed: {e}")
        except Exception:
            pass


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

    # Run once immediately
    run_bot()

    # Startup notification (after first cycle so equity is fresh)
    if notifier:
        try:
            _startup_equity = _get_current_equity()
            _cad_rate = 1.37
            try:
                import ccxt as _ccxt
                _ex = _ccxt.kraken({'apiKey': conf.API_KEY, 'secret': conf.SECRET_KEY, 'enableRateLimit': True})
                _cad_rate = _ex.fetch_ticker('USDCAD')['last']
            except Exception:
                pass
            _target = (_startup_equity * 0.90) / conf.TARGET_POSITIONS_NUM
            notifier.notify_startup(
                equity=_startup_equity,
                mode=mode,
                equity_cad=_startup_equity * _cad_rate,
                max_positions=conf.MAX_OPEN_POSITIONS,
                target_per_slot=_target,
            )
        except Exception:
            pass
        # Send full portfolio summary immediately on startup too
        _send_daily_summary()

    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
