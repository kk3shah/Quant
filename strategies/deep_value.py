import pandas as pd
import numpy as np
from strategies.base import BaseStrategy

class DeepValueStrategy(BaseStrategy):
    """
    Deep Value Strategy (1-Hour Timeframe):
    - Philosophy: Buy Fear.
    - Setup A (Trend Pullback): Price > EMA 50 AND RSI < 35.
    - Setup B (Crash Bounce): RSI < 25 (Extreme Oversold).
    - Exit: RSI > 60 or Stop Loss.
    """
    
    def get_signal(self, symbol, bars):
        """
        Returns Signal dict: {symbol, score, type, meta}
        """
        if len(bars) < 55:
            return {'symbol': symbol, 'signal': 'HOLD (NO_DATA)', 'score': 0, 'meta': {}}

        # 1. INDICATORS

        # RSI (14) — EWM/Wilder method, consistent with all other strategies.
        # Previously used rolling().mean() (SMA RSI) which is noisier.
        delta = bars['close'].diff()
        gain = delta.clip(lower=0)
        loss = -1 * delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        rs = avg_gain / avg_loss
        bars['rsi'] = 100 - (100 / (1 + rs))

        # EMA 50 (Trend Context)
        bars['ema50'] = bars['close'].ewm(span=50, adjust=False).mean()

        # CURRENT VALUES
        row = bars.iloc[-1]
        prev_row = bars.iloc[-2]
        price = row['close']
        rsi = row['rsi']
        ema50 = row['ema50']
        if pd.isna(rsi): rsi = 50.0

        # Reversal Confirmation — require a green recovery candle with above-average volume.
        # Without this the strategy buys straight into a crash (falling knife).
        current_candle_green = row['close'] > row['open']
        vol_avg = bars['volume'].rolling(window=20).mean().iloc[-1] if 'volume' in bars else 0
        cur_vol = row['volume'] if 'volume' in bars else 0
        vol_confirms = (cur_vol > vol_avg * 1.5) if vol_avg > 0 else False

        reversal_confirmed = current_candle_green and vol_confirms

        # LOGIC
        signal_type = 'HOLD'
        score = 0

        # Condition B: Extreme Crash (RSI < 25) — only buy if reversal is confirmed
        if rsi < 25:
            if reversal_confirmed:
                signal_type = 'BUY (EXTREME_OVERSOLD)'
                score = 100 - rsi  # Lower RSI = higher score
            else:
                signal_type = 'HOLD (AWAITING_REVERSAL)'

        # Condition A: Trend Pullback (RSI < 35 + Uptrend) — require reversal too
        elif rsi < 35 and price > ema50:
            if reversal_confirmed:
                signal_type = 'BUY (TREND_PULLBACK)'
                score = 90 - rsi
            else:
                signal_type = 'HOLD (AWAITING_REVERSAL)'

        elif rsi > 70:
            signal_type = 'SELL (OVERBOUGHT)'
            score = 0

        # ─── AUDIT CONTEXT ───
        vol_ratio_val = round(cur_vol / vol_avg, 3) if (vol_avg and vol_avg > 0 and cur_vol) else None
        price_above_ema50 = price > ema50
        ema50_pct = round((price - ema50) / ema50 * 100, 3) if ema50 else None
        rsi_extreme = rsi < 25
        rsi_pullback = rsi < 35

        conditions_checked = {
            'rsi_extreme (<25)':   {'value': round(rsi, 2), 'threshold': '<25', 'passed': rsi_extreme},
            'rsi_pullback (<35)':  {'value': round(rsi, 2), 'threshold': '<35', 'passed': rsi_pullback},
            'above_ema50':         {'value': round(price, 6), 'threshold': f'>{round(ema50,6)} (EMA50)', 'passed': price_above_ema50},
            'reversal_confirmed':  {'value': reversal_confirmed, 'threshold': 'green candle AND vol>1.5×avg', 'passed': reversal_confirmed},
            'candle_green':        {'value': current_candle_green, 'threshold': 'close > open', 'passed': current_candle_green},
            'vol_confirms':        {'value': vol_ratio_val, 'threshold': '>=1.5× avg', 'passed': vol_confirms},
        }
        if 'BUY' in signal_type:
            if rsi_extreme:
                trigger_condition = f"RSI={rsi:.1f} < 25 extreme oversold with reversal confirmed (green candle, vol_ratio={vol_ratio_val})"
            else:
                trigger_condition = (
                    f"RSI={rsi:.1f} < 35 pullback, price {price:.5f} is {ema50_pct:+.2f}% above EMA50 {ema50:.5f}, "
                    f"reversal confirmed (vol_ratio={vol_ratio_val})"
                )
            needs_for_trigger = None
        else:
            parts = []
            if not rsi_pullback:         parts.append(f"RSI={rsi:.1f} not oversold (need <35, or <25 for extreme)")
            if not reversal_confirmed:
                if not current_candle_green: parts.append("Last candle is RED — no reversal confirmation")
                if not vol_confirms:         parts.append(f"Volume not confirmed: vol_ratio={vol_ratio_val} < 1.5×")
            if rsi_pullback and not price_above_ema50 and not rsi_extreme:
                parts.append(f"Price {price:.5f} below EMA50 {ema50:.5f} ({ema50_pct:+.2f}%) — Setup A requires uptrend")
            trigger_condition = None
            needs_for_trigger = "; ".join(parts) if parts else signal_type

        return {
            'symbol': symbol,
            'strategy': 'DEEP_VALUE',
            'signal': signal_type,
            'score': score,
            'meta': {
                'price': price,
                'rsi': round(rsi, 2),
                'ema50': round(ema50, 6),
                'ema50_pct': ema50_pct,
                'vol_ratio': vol_ratio_val,
                'current_candle_green': current_candle_green,
                'vol_confirms': vol_confirms,
                'reversal_confirmed': reversal_confirmed,
                'trigger_condition': trigger_condition,
                'needs_for_trigger': needs_for_trigger,
                'conditions_checked': conditions_checked,
            }
        }

    def place_buy_order(self, symbol, fixed_qty=None):
        if fixed_qty:
            self.execution_handler.submit_order(symbol, fixed_qty, 'buy')
