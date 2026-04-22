import schedule
import time
import sys
import os
import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo('America/Toronto')
import ccxt
import requests as _tg_requests
from termcolor import colored

# ── Telegram hard-wired constants (fallback when env vars not set) ──
_TG_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN',  '8436312230:AAELpXdhwwt4b6oe2Ysd0X4LSwWjcH4313c')
_TG_CHAT    = os.getenv('TELEGRAM_CHAT_ID',    '5572465493')

def _tg(text: str) -> None:
    """Fire-and-forget Telegram send — never raises."""
    try:
        r = _tg_requests.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            json={'chat_id': _TG_CHAT, 'text': text, 'parse_mode': 'HTML'},
            timeout=15,
        )
        print(f"[TG] {'OK' if r.ok else f'ERR {r.status_code}: {r.text[:120]}'}")
    except Exception as e:
        print(f"[TG] send failed: {e}")


# ── Fire immediately at module load — before any local import can crash us ──
_tg(f"🟡 main.py loaded — importing modules...")


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

try:
    from config import Config
    from exchange_data.handler import DataHandler
    from execution.handler import ExecutionHandler
    from strategies.engine import StrategyEngine
    from reporting.daily_report import print_daily_report
    try:
        import notifier
    except ImportError:
        notifier = None
    _tg("🟢 <b>Quant Bot online</b> — all modules loaded, starting first cycle...")
except Exception as _import_err:
    _tg(f"🔴 <b>Bot crashed on import</b>\n<code>{_import_err}</code>")
    raise

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

    # SELF-LEARNING: disabled until it learns from verified Kraken net P&L.
    if conf.ENABLE_OPTIMIZER:
        try:
            from strategies.optimizer import run_optimizer
            _opt_changes = run_optimizer(current_cycle=_cycle_count)
            if _opt_changes:
                for msg in _opt_changes:
                    print(colored(msg, "yellow"))
                # Alert Telegram when a strategy is disabled or re-enabled
                _alert_msgs = [m for m in _opt_changes if 'DISABLED' in m or 're-enabled' in m]
                if _alert_msgs:
                    _tg('🤖 <b>Optimizer update</b>\n' + '\n'.join(f'  {m}' for m in _alert_msgs))
        except Exception as _oe:
            print(f"[OPTIMIZER] Error: {_oe}")
    else:
        print("[OPTIMIZER] Disabled: waiting for verified Kraken-fill P&L integration.")

    # 3. Report
    print_daily_report(execution_handler)
    
    print(colored("--- Routine Finished ---\n", "cyan"))

    # Send Telegram summary every 6 cycles (~30 min)
    if (_cycle_count - _last_summary_cycle) >= 6:
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
    try:
        import json, time as _time
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

        # ── 24h P&L (rolling daily, like trading charts) ──────────
        daily_equity_ref = equity
        session_file = os.path.join(base, 'data', 'session.json')
        if os.path.exists(session_file):
            try:
                with open(session_file) as f:
                    _sdata = json.load(f)
                # Use daily_equity (snapshot from start of today) for 24h P&L
                daily_equity_ref = _sdata.get('daily_equity', _sdata.get('starting_equity', equity))
            except Exception:
                pass

        session_pnl     = equity - daily_equity_ref
        session_pnl_pct = (session_pnl / daily_equity_ref * 100) if daily_equity_ref else 0
        pnl_sign        = '+' if session_pnl >= 0 else ''
        pnl_emoji       = '📈' if session_pnl >= 0 else '📉'

        # ── Slot maths ────────────────────────────────────────────
        n_pos           = len(positions_detail)
        slots_free      = conf.MAX_OPEN_POSITIONS - n_pos
        alloc           = conf.FIXED_ALLOCATION_USD   # fixed $15 per trade

        day_trades  = _engine.daily_trade_count  if _engine and hasattr(_engine, 'daily_trade_count')  else 0
        day_fees    = _engine.daily_fee_total     if _engine and hasattr(_engine, 'daily_fee_total')    else 0.0

        # ── ET label (handles EST/EDT automatically) ─────────────
        now_et   = datetime.datetime.now(_ET)
        ts_label = now_et.strftime('%I:%M %p ET  •  %a %d %b %Y')

        # ── Build message ─────────────────────────────────────────
        lines = [
            f"📊 <b>QUANT BOT REPORT</b>",
            f"<i>{ts_label}</i>",
            f"",
            f"💰 <b>EQUITY</b>",
            f"  USD: <b>${equity:,.2f}</b>   CAD: <b>${equity_cad:,.2f}</b>",
            f"  24h open: ${daily_equity_ref:,.2f}",
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
            f"  Alloc/trade: ${alloc:.2f}  |  Cash available: ${usd_cash:.2f}",
        ]

        if alloc < 15.0 and slots_free > 0:
            lines.append(f"  ⚠️ Alloc ${alloc:.2f} &lt; Kraken $15 min → entries blocked")
        elif slots_free == 0:
            lines.append(f"  ✅ All slots filled")
        else:
            lines.append(f"  ✅ Ready to enter (alloc ${alloc:.2f})")

        # ── Daily activity + performance stats ───────────────────
        lines += [
            f"",
            f"📈 <b>TODAY</b>",
            f"  Trades executed: {day_trades}",
            f"  Fees paid: ${day_fees:.4f}",
        ]

        # ── Performance tracker (win rate, P&L, entry gate, exit breakdown) ─
        try:
            import audit_logger as _al
            _ps = _al.get_perf_stats()
            _wins   = _ps.get('wins', 0)
            _losses = _ps.get('losses', 0)
            _total_closed = _wins + _losses
            _pnl_usd = _ps.get('total_pnl_usd', 0.0)
            _fees    = _ps.get('total_fees_usd', 0.0)
            _wr      = (_wins / _total_closed * 100) if _total_closed > 0 else 0
            _avg_pnl = (_pnl_usd / _total_closed) if _total_closed > 0 else 0
            _gate_pass  = _ps.get('entry_gate_passed', 0)
            _gate_block = _ps.get('entry_gate_blocked', 0)
            _gate_total = _gate_pass + _gate_block
            _gate_pct   = (_gate_pass / _gate_total * 100) if _gate_total > 0 else 0
            _block_reasons = _ps.get('gate_block_reasons', {})
            _exit_reasons  = _ps.get('exit_reasons', {})
            _strat_stats   = _ps.get('strategy_stats', {})

            if _total_closed > 0:
                _pnl_sign = '+' if _pnl_usd >= 0 else ''
                lines += [
                    f"",
                    f"🎯 <b>TRADE PERFORMANCE  (today)</b>",
                    f"  Closed: {_total_closed}  |  ✅ {_wins}W  ❌ {_losses}L  |  WR: {_wr:.0f}%",
                    f"  Net P&L: <b>{_pnl_sign}${_pnl_usd:.4f}</b>  |  Avg/trade: {_pnl_sign}${_avg_pnl:.4f}",
                ]
                if _fees > 0:
                    lines.append(f"  Fees paid: ${_fees:.4f}")
            else:
                lines += [f"", f"🎯 <b>TRADE PERFORMANCE</b>  No closed trades today"]

            # Entry gate stats
            if _gate_total > 0:
                lines += [
                    f"",
                    f"🚦 <b>ENTRY GATE</b>  ({_gate_pass} passed / {_gate_total} evaluated = {_gate_pct:.0f}%)",
                ]
                if _block_reasons:
                    top_blocks = sorted(_block_reasons.items(), key=lambda x: -x[1])[:4]
                    for reason, count in top_blocks:
                        lines.append(f"  ✗ {reason}: {count}x")

            # Exit reason breakdown
            if _exit_reasons:
                lines += [f"", f"🚪 <b>EXIT REASONS</b>"]
                for reason, count in sorted(_exit_reasons.items(), key=lambda x: -x[1]):
                    lines.append(f"  {reason}: {count}x")

            # Per-strategy breakdown
            if _strat_stats:
                lines += [f"", f"📊 <b>BY STRATEGY</b>"]
                for strat, ss in sorted(_strat_stats.items()):
                    sw = ss.get('wins', 0)
                    sl = ss.get('losses', 0)
                    sp = ss.get('pnl_usd', 0.0)
                    st = sw + sl
                    if st > 0:
                        sp_sign = '+' if sp >= 0 else ''
                        lines.append(f"  {strat}: {sw}W/{sl}L  {sp_sign}${sp:.4f}")

        except Exception as _pe:
            lines.append(f"  [perf stats error: {_pe}]")

        # ── Optimizer status ──────────────────────────────────────────────
        try:
            from strategies.optimizer import get_strategy_status
            opt_lines = get_strategy_status()
            if opt_lines:
                lines += ['', '🤖 <b>OPTIMIZER</b>'] + opt_lines
        except Exception:
            pass

        _tg('\n'.join(lines))

    except Exception as e:
        print(f"[SUMMARY] Error: {e}")
        _tg(f"⚠️ <b>Summary error</b>\n<code>{str(e)[:300]}</code>")


def main():
    conf = Config()
    interval = conf.BOT_INTERVAL_MINUTES
    mode = "PAPER" if conf.PAPER_TRADING else "LIVE"

    # ─── DAILY-ROLLING LOG FILE ───
    logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, f"bot_{datetime.date.today():%Y-%m-%d}.log")
    _log_file = open(log_path, 'a', buffering=1)
    sys.stdout = _Tee(sys.__stdout__, _log_file)
    sys.stderr = _Tee(sys.__stderr__, _log_file)

    print(colored(f"Antigravity Crypto Bot Initialized [{mode} MODE].", "magenta"))
    print(f"Scheduled to run every {interval} minutes.")
    print(f"Max {conf.MAX_DAILY_TRADES} trades/day | {conf.STOP_LOSS*100}% SL / {conf.TAKE_PROFIT*100}% TP | Kill-switch at {conf.MAX_DRAWDOWN*100}% DD")

    # Schedule the trading job
    schedule.every(interval).minutes.do(run_bot)

    # Run first cycle immediately
    run_bot()

    # After first cycle, send full portfolio summary
    _send_daily_summary()

    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
