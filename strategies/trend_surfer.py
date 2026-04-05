import pandas as pd
import numpy as np
from termcolor import colored
from strategies.base import BaseStrategy

class TrendSurferStrategy(BaseStrategy):
    """
    Trend Surfer Strategy (5-Minute Swing):
    - Safety First: Only trade ABOVE EMA 200.
    - Entry: MACD Crossover (Bullish).
    - Exit: Trailing Stop (2x ATR) or Trend Break.
    """
    
    def get_signal(self, symbol, bars):
        """
        Returns Signal dict: {symbol, score, type, meta}
        """
        if len(bars) < 60:
            return {'symbol': symbol, 'signal': 'HOLD (NO_DATA)', 'score': 0, 'meta': {}}

        # 1. INDICATORS
        # EMA 200 — the documented safety filter (was incorrectly using span=50)
        bars['ema200'] = bars['close'].ewm(span=200, adjust=False).mean()

        # MACD (12, 26, 9)
        exp1 = bars['close'].ewm(span=12, adjust=False).mean()
        exp2 = bars['close'].ewm(span=26, adjust=False).mean()
        bars['macd'] = exp1 - exp2
        bars['signal_line'] = bars['macd'].ewm(span=9, adjust=False).mean()

        # ATR (14) for Stop Loss
        high_low = bars['high'] - bars['low']
        high_close = np.abs(bars['high'] - bars['close'].shift())
        low_close = np.abs(bars['low'] - bars['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        bars['atr'] = true_range.rolling(14).mean()

        # CURRENT VALUES
        price = bars['close'].iloc[-1]
        ema_trend = bars['ema200'].iloc[-1]
        macd = bars['macd'].iloc[-1]
        sig = bars['signal_line'].iloc[-1]
        prev_macd = bars['macd'].iloc[-2]
        prev_sig = bars['signal_line'].iloc[-2]
        atr = bars['atr'].iloc[-1]

        # LOGIC
        signal_type = 'HOLD'
        score = 0

        # 0. GLOBAL MACRO FILTER — penalty, not blanket block.
        # Bear markets produce sharp MACD crossovers on relief rallies that can
        # be very profitable.  Penalise score so only high-conviction setups fire.
        _bear_penalty = 0
        if 'global_trend' in bars:
            global_trend = bars['global_trend'].iloc[-1]
            if global_trend == 'BEARISH':
                _bear_penalty = 20  # applied to score after signal confirmed

        # 1. CHECK TREND — Only trade above EMA 200 (strong long-term filter)
        if price < ema_trend:
            signal_type = 'HOLD (BEAR_TREND)'
            score = 0
        else:
            # 2. CHECK MOMENTUM (MACD Crossover)
            # Bullish Cross: MACD line crosses above Signal line
            if macd > sig and prev_macd <= prev_sig:
                signal_type = 'BUY'
                score = 100  # Authentic crossover

                # Boost if MACD is above 0 (strong trend) vs below 0 (early reversal)
                if macd > 0: score += 10
                score -= _bear_penalty  # 0 in bull, -20 in bear

            elif macd > sig:
                # Already crossed, holding valid uptrend
                signal_type = 'HOLD (UPTREND)'
                score = 50
            else:
                signal_type = 'HOLD (WEAK)'

        # ─── AUDIT CONTEXT ───
        macd_crossover = (macd > sig and prev_macd <= prev_sig)
        price_above_ema = price >= ema_trend
        ema_pct_diff = round((price - ema_trend) / ema_trend * 100, 3) if ema_trend else None
        conditions_checked = {
            'price_above_ema200': {'value': round(price, 6), 'threshold': f'>={round(ema_trend,6)} (EMA200)', 'passed': price_above_ema},
            'macd_crossover':     {'value': round(macd, 8), 'threshold': 'MACD crossed above signal', 'passed': macd_crossover},
            'bear_penalty':       {'value': _bear_penalty, 'threshold': '0 in bull, -20 in bear', 'passed': _bear_penalty == 0},
        }
        if 'BUY' in signal_type:
            trigger_condition = (
                f"MACD crossover: {prev_macd:.6f}→{macd:.6f} crossed signal {sig:.6f}; "
                f"price {price:.5f} is {ema_pct_diff:+.2f}% above EMA200 {ema_trend:.5f}"
            )
            needs_for_trigger = None
        else:
            parts = []
            if not price_above_ema: parts.append(f"Price {price:.5f} below EMA200 {ema_trend:.5f} ({ema_pct_diff:+.2f}%)")
            if not macd_crossover:
                if macd > sig: parts.append(f"MACD {macd:.6f} already above signal (no fresh crossover, in uptrend)")
                else:          parts.append(f"MACD {macd:.6f} below signal {sig:.6f} (no bullish crossover)")
            if _bear_penalty > 0: parts.append(f"Market BEARISH — score penalized by {_bear_penalty}")
            trigger_condition = None
            needs_for_trigger = "; ".join(parts) if parts else signal_type

        return {
            'symbol': symbol,
            'strategy': 'TREND_SURFER',
            'signal': signal_type,
            'score': score,
            'meta': {
                'price': price,
                'ema200': round(ema_trend, 6),
                'ema_pct_diff': ema_pct_diff,
                'macd': round(macd, 8),
                'signal_line': round(sig, 8),
                'prev_macd': round(prev_macd, 8),
                'prev_signal': round(prev_sig, 8),
                'macd_crossover': macd_crossover,
                'atr': round(atr, 8),
                'stop_loss': round(price - (2.0 * atr), 6),
                'trigger_condition': trigger_condition,
                'needs_for_trigger': needs_for_trigger,
                'conditions_checked': conditions_checked,
            }
        }

    def place_buy_order(self, symbol, fixed_qty=None):
        # Default Sizing or Dynamic?
        # User wants "Smart Allocation".
        # If passed fixed_qty, use it.
        if fixed_qty:
            self.execution_handler.submit_order(symbol, fixed_qty, 'buy')
        else:
            # Fallback to Config logic if called directly
             from config import Config
             # ... simplified logic for now
             pass
