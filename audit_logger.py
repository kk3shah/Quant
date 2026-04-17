"""
Append-only audit logger. One JSON object per line in data/audit_log.json.
Every event includes: timestamp, session_id, bot_version, event type.
All writes are fire-and-forget — errors are silently suppressed to never crash the bot.
"""
import json
import os
import datetime
import subprocess

_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'audit_log.json')

# ─── SESSION + VERSION — set once at import time ───
SESSION_ID = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
try:
    _repo_dir = os.path.dirname(os.path.abspath(__file__))
    BOT_VERSION = subprocess.check_output(
        ['git', '-C', _repo_dir, 'rev-parse', '--short', 'HEAD'],
        stderr=subprocess.DEVNULL, timeout=2,
    ).decode().strip()
    if not BOT_VERSION:  # try parent dir
        BOT_VERSION = subprocess.check_output(
            ['git', '-C', os.path.dirname(_repo_dir), 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip()
except Exception:
    BOT_VERSION = 'v1.0-reformed'  # fallback label


# ─── HELPERS ───

def _r(v, d=4):
    """Safe round — returns None if value is None or not numeric."""
    try:
        return round(float(v), d) if v is not None else None
    except Exception:
        return v


def _to_python(v):
    """Convert numpy scalars or other non-JSON-native types to Python primitives."""
    # numpy scalars expose .item() to get the Python equivalent
    if hasattr(v, 'item') and type(v).__module__.startswith('numpy'):
        return v.item()
    return v


def _clean(d):
    """Recursively round floats and strip NaN from a dict for clean JSON output."""
    import math
    if not isinstance(d, dict):
        return d
    out = {}
    for k, v in d.items():
        v = _to_python(v)  # unwrap numpy scalars first
        if isinstance(v, bool):
            out[k] = v  # must be before float check (bool subclasses int)
        elif isinstance(v, float):
            out[k] = None if math.isnan(v) or math.isinf(v) else round(v, 6)
        elif isinstance(v, dict):
            out[k] = _clean(v)
        elif isinstance(v, list):
            out[k] = [_clean(i) if isinstance(i, dict) else _to_python(i) for i in v]
        else:
            out[k] = v
    return out


def _log(event: dict):
    try:
        os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
        event['timestamp'] = datetime.datetime.utcnow().isoformat() + 'Z'
        event['session_id'] = SESSION_ID
        event['bot_version'] = BOT_VERSION
        with open(_LOG_FILE, 'a') as f:
            f.write(json.dumps(_clean(event), default=str) + '\n')
    except Exception:
        pass  # Never crash the bot over logging


# ─── LOSS ANALYSIS ───

def _loss_analysis(exit_reason, pnl_pct, regime, strategy, indicators_at_entry):
    """Build structured post-mortem for losing trades."""
    if pnl_pct is None or pnl_pct >= 0:
        return None

    signs = []
    ind = indicators_at_entry or {}

    vol_ratio   = ind.get('vol_ratio')
    rsi         = ind.get('rsi')
    z_score     = ind.get('z_score')
    bb_dist     = ind.get('bb_dist')
    slope       = ind.get('slope')
    is_crashing = ind.get('is_crashing')

    if vol_ratio is not None and vol_ratio < 0.8:
        signs.append(f"Weak volume at entry (vol_ratio={vol_ratio:.2f}, threshold 0.8) — low conviction")
    if rsi is not None and rsi > 42:
        signs.append(f"RSI={rsi:.1f} at entry — not deeply oversold, marginal pullback entry")
    if z_score is not None and 2.0 < z_score < 2.3:
        signs.append(f"Z-score={z_score:.2f} barely above 2.0 threshold — borderline breakout")
    if bb_dist is not None and bb_dist > 0.015:
        signs.append(f"Price was {bb_dist*100:.1f}% from lower BB — not at true support level")
    if slope is not None and slope < -0.01:
        signs.append(f"SMA slope={slope*100:.2f}% negative — downward drift at entry")
    if is_crashing:
        signs.append("is_crashing=True at entry — bot entered against a falling knife")

    if exit_reason == 'stop_loss':
        cause = f"Price fell through {abs(pnl_pct):.2f}% stop in {regime} regime via {strategy}"
        if regime == 'VOLATILE':
            fix = "In VOLATILE regime use tighter 1.5% stop or require vol_ratio > 1.5 for entry"
        elif regime == 'TRENDING_BEAR':
            fix = "Avoid longs in TRENDING_BEAR — limit to MEAN_REV/DEEP_VALUE with extra confirmation"
        elif strategy in ('MOMENTUM_PB', 'MEAN_REV'):
            fix = "Require vol_ratio > 1.0 AND RSI < 35 for stricter entry; current thresholds too loose"
        else:
            fix = f"Review {strategy} entry conditions in {regime} regime — tighten vol or RSI filter"
    elif exit_reason == 'time_exit':
        cause = f"Trade stalled in {regime} regime — no momentum materialized in hold window"
        fix = "Add mid-hold momentum check (e.g. if ROI < -1% after 3h, exit early)"
    elif exit_reason == 'trailing_stop':
        cause = f"Trailing stop triggered while still slightly profitable in {regime} regime"
        fix = "Trail width may be too tight for this regime; consider widening in RANGING markets"
    else:
        cause = f"{exit_reason} triggered a small loss in {regime} regime"
        fix = f"Review {strategy} exit conditions in {regime} regime"

    return {
        'probable_cause': cause,
        'indicator_warning_signs': signs,
        'suggested_fix': fix,
    }


# ─── PUBLIC LOG FUNCTIONS ───

def log_cycle(equity, cash, open_positions_count, daily_trade_count, market_regime,
              drawdown_pct=None, signals_evaluated=0, signals_skipped=0,
              signals_triggered=0, top_skipped_signals=None,
              portfolio_heat_pct=None, daily_fees_paid=None):
    """Log state at the end of every bot cycle."""
    _log({
        'event': 'CYCLE',
        'equity': _r(equity),
        'cash': _r(cash),
        'portfolio_heat_pct': _r(portfolio_heat_pct),
        'open_positions': open_positions_count,
        'daily_trade_count': daily_trade_count,
        'daily_fees_paid': _r(daily_fees_paid),
        'market_regime': market_regime,
        'drawdown_pct': _r(drawdown_pct),
        'signals_evaluated': signals_evaluated,
        'signals_skipped': signals_skipped,
        'signals_triggered': signals_triggered,
        'top_skipped_signals': top_skipped_signals or [],
    })


def log_signal(symbol, strategy, signal_type, score, regime, skip_reason=None,
               indicators=None, conditions_checked=None, needs_for_trigger=None):
    """
    Log every signal evaluated by a strategy.

    conditions_checked: {name: {value, threshold, passed}} — every gate the strategy tested
    needs_for_trigger: human-readable string of what would need to change for this to fire
    """
    _log({
        'event': 'SIGNAL',
        'symbol': symbol,
        'strategy': strategy,
        'signal_type': signal_type,
        'score': _r(score),
        'regime': regime,
        'skip_reason': skip_reason,
        'needs_for_trigger': needs_for_trigger,
        'conditions_checked': _clean(conditions_checked or {}),
        'indicators': _clean(indicators or {}),
    })


def log_trade_entry(symbol, strategy, entry_price, quantity, allocated_usd,
                    stop_loss_price, take_profit_price,
                    regime=None, btc_trend=None, signal_score=None,
                    trigger_condition=None, indicators=None,
                    atr_value=None, atr_scale=None, submitted_price=None,
                    competing_signals=None):
    """Log every confirmed trade entry with full decision context."""
    slippage = None
    if entry_price and submitted_price and submitted_price > 0:
        slippage = _r((entry_price - submitted_price) / submitted_price * 100)

    rationale = None
    if all(v is not None for v in [allocated_usd, atr_scale, atr_value, entry_price]):
        try:
            rationale = (
                f"Alloc=${allocated_usd:.2f} (ATR_scale={atr_scale:.2f} × base); "
                f"ATR={atr_value:.6f} = {atr_value/entry_price*100:.3f}% of price"
            )
        except Exception:
            pass

    _log({
        'event': 'TRADE_ENTRY',
        'symbol': symbol,
        'strategy': strategy,
        'trigger_condition': trigger_condition,
        'entry_price': entry_price,
        'submitted_price': submitted_price,
        'slippage_pct': slippage,
        'quantity': _r(quantity, 8),
        'allocated_usd': _r(allocated_usd),
        'stop_loss_price': _r(stop_loss_price, 6),
        'take_profit_price': _r(take_profit_price, 6),
        'regime': regime,
        'btc_trend': btc_trend,
        'signal_score': _r(signal_score),
        'atr_value': _r(atr_value, 8),
        'atr_scale': _r(atr_scale),
        'position_size_rationale': rationale,
        'indicators_at_entry': _clean(indicators or {}),
        'competing_signals': competing_signals or [],
    })


def log_trade_exit(symbol, exit_reason, exit_price, entry_price, pnl_pct, pnl_usd,
                   hold_duration_hours, regime=None, btc_trend=None, exit_detail=None,
                   indicators_at_exit=None, indicators_at_entry=None,
                   peak_price=None, strategy=None, submitted_exit_price=None):
    """
    Log every trade exit with full context and automatic loss analysis.
    exit_reason: stop_loss | take_profit | trailing_stop | time_exit | strategy_signal | manual | liquidation
    exit_detail: human string, e.g. "Dropped 1.8% from peak $9.54 (trail threshold 1.5%)"
    """
    outcome = 'UNKNOWN'
    if pnl_pct is not None:
        outcome = 'WIN' if pnl_pct >= 0 else 'LOSS'

    peak_pnl_pct = None
    if peak_price and entry_price and entry_price > 0:
        peak_pnl_pct = _r((peak_price - entry_price) / entry_price * 100)

    exit_slip = None
    if exit_price and submitted_exit_price and submitted_exit_price > 0:
        exit_slip = _r((exit_price - submitted_exit_price) / submitted_exit_price * 100)

    loss_analysis = _loss_analysis(
        exit_reason=exit_reason, pnl_pct=pnl_pct, regime=regime or 'UNKNOWN',
        strategy=strategy or 'UNKNOWN', indicators_at_entry=indicators_at_entry,
    )

    _log({
        'event': 'TRADE_EXIT',
        'symbol': symbol,
        'strategy': strategy,
        'exit_reason': exit_reason,
        'exit_detail': exit_detail,
        'outcome': outcome,
        'exit_price': exit_price,
        'submitted_exit_price': submitted_exit_price,
        'exit_slippage_pct': exit_slip,
        'entry_price': entry_price,
        'pnl_pct': _r(pnl_pct),
        'pnl_usd': _r(pnl_usd, 6),
        'hold_duration_hours': _r(hold_duration_hours, 3),
        'peak_price': peak_price,
        'peak_pnl_pct': peak_pnl_pct,
        'regime_at_exit': regime,
        'btc_trend_at_exit': btc_trend,
        'indicators_at_exit': _clean(indicators_at_exit or {}),
        'indicators_at_entry': _clean(indicators_at_entry or {}),
        'loss_analysis': loss_analysis,
    })


def log_entry_gate(symbol, strategy, passed, dip_pct, min_dip_pct, vol_ratio,
                   green_candle, require_green, market_strength, regime,
                   block_reason=None, score=None):
    """Log every entry quality gate decision — both passes and blocks.
    This is the key dataset for tuning entry filters post-session."""
    _log({
        'event': 'ENTRY_GATE',
        'symbol': symbol,
        'strategy': strategy,
        'passed': passed,
        'block_reason': block_reason,
        'score': _r(score),
        'dip_pct': _r(dip_pct, 4),
        'min_dip_pct': _r(min_dip_pct, 4),
        'vol_ratio': _r(vol_ratio, 3),
        'green_candle': green_candle,
        'require_green': require_green,
        'market_strength': _r(market_strength, 1),
        'regime': regime,
    })


def log_market_gauge(strength, label, min_dip_pct, require_green,
                     btc_dist, btc_mom, eth_dist, eth_mom, sol_dist, sol_mom):
    """Log market gauge snapshot each cycle for trend analysis."""
    _log({
        'event': 'MARKET_GAUGE',
        'strength': _r(strength, 1),
        'label': label,
        'min_dip_pct': _r(min_dip_pct, 4),
        'require_green': require_green,
        'btc_dist_pct': _r(btc_dist, 2),
        'btc_mom_pct': _r(btc_mom, 2),
        'eth_dist_pct': _r(eth_dist, 2),
        'eth_mom_pct': _r(eth_mom, 2),
        'sol_dist_pct': _r(sol_dist, 2),
        'sol_mom_pct': _r(sol_mom, 2),
    })


def log_rejected_order(symbol, side, reason):
    """Log every order rejected by the exchange."""
    _log({
        'event': 'ORDER_REJECTED',
        'symbol': symbol,
        'side': side,
        'reason': str(reason),
    })


# ─── PERFORMANCE TRACKER ───
# Accumulates trade stats across cycles for Telegram reporting.
# Stored in data/perf_stats.json — reset daily.

_PERF_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'perf_stats.json')


def _load_perf():
    try:
        with open(_PERF_FILE, 'r') as f:
            data = json.load(f)
        # Reset if date changed
        if data.get('date') != datetime.date.today().isoformat():
            return _empty_perf()
        return data
    except Exception:
        return _empty_perf()


def _empty_perf():
    return {
        'date': datetime.date.today().isoformat(),
        'trades_entered': 0,
        'trades_exited': 0,
        'wins': 0,
        'losses': 0,
        'total_pnl_usd': 0.0,
        'total_fees_usd': 0.0,
        'exit_reasons': {},       # {reason: count}
        'strategy_stats': {},     # {strategy: {entered, wins, losses, pnl_usd}}
        'entry_gate_passed': 0,
        'entry_gate_blocked': 0,
        'gate_block_reasons': {}, # {reason: count}
    }


def _save_perf(data):
    try:
        os.makedirs(os.path.dirname(_PERF_FILE), exist_ok=True)
        with open(_PERF_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def record_entry(strategy):
    """Call after a successful trade entry."""
    p = _load_perf()
    p['trades_entered'] += 1
    s = p['strategy_stats'].setdefault(strategy, {'entered': 0, 'wins': 0, 'losses': 0, 'pnl_usd': 0.0})
    s['entered'] += 1
    _save_perf(p)


def record_exit(strategy, exit_reason, pnl_usd):
    """Call after a trade exit with P&L."""
    p = _load_perf()
    p['trades_exited'] += 1
    pnl = float(pnl_usd) if pnl_usd else 0.0
    p['total_pnl_usd'] += pnl
    if pnl >= 0:
        p['wins'] += 1
    else:
        p['losses'] += 1
    p['exit_reasons'][exit_reason] = p['exit_reasons'].get(exit_reason, 0) + 1
    s = p['strategy_stats'].setdefault(strategy or 'UNKNOWN', {'entered': 0, 'wins': 0, 'losses': 0, 'pnl_usd': 0.0})
    if pnl >= 0:
        s['wins'] += 1
    else:
        s['losses'] += 1
    s['pnl_usd'] += pnl
    _save_perf(p)


def record_fee(fee_usd):
    """Accumulate fees paid."""
    p = _load_perf()
    p['total_fees_usd'] += float(fee_usd) if fee_usd else 0.0
    _save_perf(p)


def record_entry_gate(passed, block_reason=None):
    """Accumulate entry gate pass/block stats."""
    p = _load_perf()
    if passed:
        p['entry_gate_passed'] += 1
    else:
        p['entry_gate_blocked'] += 1
        if block_reason:
            p['gate_block_reasons'][block_reason] = p['gate_block_reasons'].get(block_reason, 0) + 1
    _save_perf(p)


def get_perf_stats():
    """Return current day's performance stats for Telegram report."""
    return _load_perf()
