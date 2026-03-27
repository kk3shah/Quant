"""
Self-Learning Strategy Optimizer
=================================
Reads TRADE_EXIT events from data/audit_log.json after each bot cycle,
computes per-strategy win rates, and auto-adjusts entry thresholds in
data/strategy_params.json so the bot self-corrects without manual intervention.

Adjustment rules
----------------
1. win_rate < 40%  with >= 3 trades  → raise min_score by 5  (max 90)
   Entry criteria tighten — strategy only fires on high-conviction setups.
2. win_rate > 60%  with >= 5 trades  → lower min_score by 2  (min 50)
   Entry criteria relax — capture more opportunities from a proven edge.
3. 3+ consecutive losses             → disable strategy for COOLDOWN_CYCLES
   Hard cooldown so a strategy can't keep haemorrhaging capital.
4. Cooldown expires                  → auto re-enable, log & alert.
"""

import json
import os
from datetime import datetime, timezone

# ── File paths ──────────────────────────────────────────────────────────────
PARAMS_FILE = 'data/strategy_params.json'
AUDIT_LOG   = 'data/audit_log.json'

# ── Tuning knobs ────────────────────────────────────────────────────────────
MIN_TRADES_TO_ADJUST  = 3    # don't adjust until we have this many trades
COOLDOWN_CYCLES       = 6    # ~30 min at 5-min intervals
SCORE_RAISE_STEP      = 5    # raise min_score by this on underperformance
SCORE_LOWER_STEP      = 2    # lower min_score by this on outperformance
MIN_SCORE_FLOOR       = 50   # never require a score below this
MAX_SCORE_CEILING     = 90   # never require a score above this
RECENT_EXITS_WINDOW   = 60   # number of recent TRADE_EXIT events to analyse

ALL_STRATEGIES = [
    'VOL_BREAKOUT', 'VOL_SQUEEZE', 'MOMENTUM',
    'MOMENTUM_PB', 'MEAN_REV', 'TREND_SURFER',
    'SUPERTREND', 'DEEP_VALUE',
]

DEFAULTS = {
    'min_score': 60,
    'enabled': True,
    'disabled_until_cycle': None,
    'win_rate': None,
    'avg_pnl_pct': None,
    'trade_count': 0,
    'consecutive_losses': 0,
}


# ── Persistence helpers ──────────────────────────────────────────────────────

def load_params() -> dict:
    """Load data/strategy_params.json, backfilling missing strategies with defaults."""
    if os.path.exists(PARAMS_FILE):
        try:
            with open(PARAMS_FILE) as f:
                data = json.load(f)
            strats = data.setdefault('strategies', {})
            for s in ALL_STRATEGIES:
                if s not in strats:
                    strats[s] = dict(DEFAULTS)
            return data
        except Exception:
            pass
    return {
        'VERSION': 1,
        'updated_at': None,
        'strategies': {s: dict(DEFAULTS) for s in ALL_STRATEGIES},
    }


def save_params(data: dict):
    os.makedirs('data', exist_ok=True)
    data['updated_at'] = datetime.now(timezone.utc).isoformat()
    with open(PARAMS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


# ── Audit-log analysis ────────────────────────────────────────────────────────

def _load_recent_exits(n: int = RECENT_EXITS_WINDOW) -> list:
    """
    Return the last `n` TRADE_EXIT records from audit_log.json.
    Result is ordered most-recent first.
    """
    exits = []
    if not os.path.exists(AUDIT_LOG):
        return exits
    try:
        with open(AUDIT_LOG) as f:
            lines = f.readlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get('event') != 'TRADE_EXIT':
                continue
            exits.append(obj)
            if len(exits) >= n:
                break
    except Exception:
        pass
    return exits  # most-recent first


def _compute_stats(exits: list) -> dict:
    """
    Group exits by strategy and return per-strategy performance metrics.

    Returns:
        {
          strategy_name: {
            win_rate:           float  (0–1)
            avg_pnl_pct:        float
            trade_count:        int
            consecutive_losses: int    (from most-recent going backward)
          }
        }
    """
    from collections import defaultdict
    by_strat: dict = defaultdict(list)  # strategy -> [pnl_pct, ...]  (most-recent first)
    for ex in exits:
        s = ex.get('strategy')
        if not s:
            continue
        pnl = ex.get('pnl_pct') or 0.0
        by_strat[s].append(float(pnl))

    stats = {}
    for strat, pnls in by_strat.items():
        count    = len(pnls)
        wins     = sum(1 for p in pnls if p > 0)
        win_rate = wins / count if count else 0.0
        avg_pnl  = sum(pnls) / count if count else 0.0

        # Consecutive losses from the most-recent trade backward
        consec = 0
        for p in pnls:
            if p <= 0:
                consec += 1
            else:
                break

        stats[strat] = {
            'win_rate':           round(win_rate, 3),
            'avg_pnl_pct':        round(avg_pnl, 3),
            'trade_count':        count,
            'consecutive_losses': consec,
        }
    return stats


# ── Main optimizer entry point ────────────────────────────────────────────────

def run_optimizer(current_cycle: int = 0) -> list:
    """
    Call this at the end of every bot cycle.

    - Loads current strategy params
    - Re-enables any strategies whose cooldown has expired
    - Reads recent exits and recomputes stats
    - Adjusts min_score thresholds and enables/disables strategies
    - Saves updated params
    - Returns a list of human-readable change messages for logging / Telegram

    Args:
        current_cycle: the global bot cycle counter (used for cooldown expiry)
    """
    changes = []
    params     = load_params()
    strategies = params['strategies']

    # ── Step 1: Re-enable strategies whose cooldown has passed ──
    for s, p in strategies.items():
        until = p.get('disabled_until_cycle')
        if until is not None and current_cycle >= until:
            p['enabled'] = True
            p['disabled_until_cycle'] = None
            msg = (f"[OPTIMIZER] {s} re-enabled after cooldown "
                   f"(cycle {current_cycle} >= {until})")
            print(msg)
            changes.append(msg)

    # ── Step 2: Compute per-strategy stats from recent exits ──
    exits = _load_recent_exits()
    stats = _compute_stats(exits)

    # ── Step 3: Apply adjustment rules ──
    for s in ALL_STRATEGIES:
        if s not in stats:
            continue  # no trade history yet — leave defaults untouched

        st = stats[s]
        p  = strategies.setdefault(s, dict(DEFAULTS))

        # Always refresh telemetry fields
        p['win_rate']           = st['win_rate']
        p['avg_pnl_pct']        = st['avg_pnl_pct']
        p['trade_count']        = st['trade_count']
        p['consecutive_losses'] = st['consecutive_losses']

        # Don't stack adjustments on a strategy that is already cooling down
        if not p.get('enabled', True):
            continue

        n = st['trade_count']

        # Rule 1 — Disable on consecutive loss streak
        if st['consecutive_losses'] >= 3 and n >= MIN_TRADES_TO_ADJUST:
            until = current_cycle + COOLDOWN_CYCLES
            p['enabled']              = False
            p['disabled_until_cycle'] = until
            msg = (f"[OPTIMIZER] {s}: {st['consecutive_losses']} consecutive losses "
                   f"→ DISABLED for {COOLDOWN_CYCLES} cycles (~{COOLDOWN_CYCLES * 5} min) "
                   f"[win_rate={st['win_rate']:.0%}, avg={st['avg_pnl_pct']:+.2f}%]")
            print(msg)
            changes.append(msg)
            continue

        # Rule 2 — Underperforming: tighten entry score
        if n >= MIN_TRADES_TO_ADJUST and st['win_rate'] < 0.40:
            old_score = p['min_score']
            p['min_score'] = min(old_score + SCORE_RAISE_STEP, MAX_SCORE_CEILING)
            if p['min_score'] != old_score:
                msg = (f"[OPTIMIZER] {s}: win_rate={st['win_rate']:.0%} ({n} trades, "
                       f"avg={st['avg_pnl_pct']:+.2f}%) "
                       f"→ min_score raised {old_score} → {p['min_score']}")
                print(msg)
                changes.append(msg)

        # Rule 3 — Outperforming: loosen entry score
        elif n >= 5 and st['win_rate'] > 0.60:
            old_score = p['min_score']
            p['min_score'] = max(old_score - SCORE_LOWER_STEP, MIN_SCORE_FLOOR)
            if p['min_score'] != old_score:
                msg = (f"[OPTIMIZER] {s}: win_rate={st['win_rate']:.0%} ({n} trades, "
                       f"avg={st['avg_pnl_pct']:+.2f}%) "
                       f"→ min_score lowered {old_score} → {p['min_score']}")
                print(msg)
                changes.append(msg)

    save_params(params)
    return changes


def get_strategy_status() -> list:
    """
    Return a list of summary strings suitable for a Telegram status report.
    Only includes strategies that have at least one recorded trade.
    """
    params = load_params()
    lines  = []
    for s in ALL_STRATEGIES:
        p = params['strategies'].get(s, {})
        n = p.get('trade_count', 0)
        if n == 0:
            continue
        wr    = p.get('win_rate') or 0
        ms    = p.get('min_score', 60)
        enabled = p.get('enabled', True)
        status = '🟢' if enabled else '🔴 (cooldown)'
        lines.append(f"  {status} {s}: {wr:.0%} win rate ({n}T) · min_score={ms}")
    return lines
