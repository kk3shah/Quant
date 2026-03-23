import pandas as pd
import numpy as np
from strategies.base import BaseStrategy
from termcolor import colored

class MomentumPullbackStrategy(BaseStrategy):
    """
    Momentum Pullback Strategy (RANGING Markets):
    - Designed for liquid coins in normal/calm conditions.
    - Buy on mild RSI pullback (35-45) near lower Bollinger Band.
    - Requires above-average volume (confirmation of interest).
    - Exit when RSI > 65 or price hits upper BB.
    """

    def get_signal(self, symbol, bars):
        """
        Returns Signal dict: {symbol, score, signal, meta}
        """
        if len(bars) < 30:
            return {'symbol': symbol, 'signal': 'HOLD (NO_DATA)', 'score': 0, 'meta': {}}

        # ─── INDICATORS ───

        # RSI (14)
        delta = bars['close'].diff()
        gain = delta.clip(lower=0)
        loss = -1 * delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        rs = avg_gain / avg_loss
        bars['rsi'] = 100 - (100 / (1 + rs))

        current_rsi = bars['rsi'].iloc[-1]
        if pd.isna(current_rsi):
            current_rsi = 50.0

        # Bollinger Bands (20, 2)
        sma20 = bars['close'].rolling(window=20).mean()
        std20 = bars['close'].rolling(window=20).std()
        lower_bb = (sma20 - 2 * std20).iloc[-1]
        upper_bb = (sma20 + 2 * std20).iloc[-1]
        middle_bb = sma20.iloc[-1]

        current_price = bars['close'].iloc[-1]

        # Volume Check (current vs 20-bar average)
        # Guard against missing volume column (same pattern as MomentumStrategy)
        if 'volume' in bars:
            vol_avg = bars['volume'].rolling(window=20).mean().iloc[-1]
            current_vol = bars['volume'].iloc[-1]
            vol_ratio = current_vol / vol_avg if vol_avg > 0 else 0
        else:
            vol_ratio = 1.0  # Assume acceptable volume if data absent

        # Distance from lower BB (as % of price)
        bb_distance = (current_price - lower_bb) / current_price if current_price > 0 else 1.0

        # SMA Slope (trend direction check — avoid buying in freefall)
        sma_short = bars['close'].rolling(window=10).mean()
        if len(sma_short.dropna()) >= 10:
            slope_raw = (sma_short.iloc[-1] - sma_short.iloc[-10]) / sma_short.iloc[-10]
        else:
            slope_raw = 0

        is_freefall = slope_raw < -0.03  # Dropping > 3% in SMA over 10 1h candles = true crash

        # Global Trend Filter
        global_trend = 'NEUTRAL'
        if 'global_trend' in bars:
            global_trend = bars['global_trend'].iloc[-1]

        # ─── SIGNAL LOGIC ───
        signal_type = 'HOLD'
        score = 0

        # BUY CONDITIONS (All must be true):
        # 1. RSI in pullback zone: 30–45 (not extreme, just a dip)
        # 2. Price within 2% of lower BB (near support)
        # 3. Volume >= 0.7x average (market is alive, not dead)
        # 4. NOT in freefall (slope check)
        # 5. NOT in full bear mode

        rsi_in_zone = 20 <= current_rsi <= 45
        near_lower_bb = bb_distance <= 0.02  # Within 2% of lower BB
        volume_ok = vol_ratio >= 0.7
        not_crashing = not is_freefall
        not_full_bear = global_trend != 'BEARISH'

        if rsi_in_zone and near_lower_bb and volume_ok and not_crashing and not_full_bear:
            signal_type = 'BUY (PULLBACK)'
            # Score: lower RSI = better entry. RSI 20 → score 95, RSI 45 → score 70
            score = 95 - (current_rsi - 20)
        elif rsi_in_zone and volume_ok and not_crashing and not_full_bear:
            # Weaker signal: RSI is pulled back but not near BB
            # Still viable if RSI is quite low
            if current_rsi <= 40:
                signal_type = 'BUY (MILD_PULLBACK)'
                score = 70 - (current_rsi - 20)

        # SELL CONDITIONS
        if current_rsi > 65:
            signal_type = 'SELL (RSI_HIGH)'
            score = 0
        elif current_price > upper_bb:
            signal_type = 'SELL (UPPER_BB)'
            score = 0

        # ─── AUDIT CONTEXT ───
        conditions_checked = {
            'rsi_in_zone':   {'value': round(current_rsi, 2), 'threshold': '20-45', 'passed': rsi_in_zone},
            'near_lower_bb': {'value': round(bb_distance, 4), 'threshold': '<=0.02', 'passed': near_lower_bb},
            'volume_ok':     {'value': round(vol_ratio, 3),   'threshold': '>=0.7',  'passed': volume_ok},
            'not_crashing':  {'value': round(slope_raw, 4),   'threshold': '>-0.03', 'passed': not_crashing},
            'not_full_bear': {'value': global_trend,          'threshold': '!=BEARISH', 'passed': not_full_bear},
        }
        if 'BUY' in signal_type:
            trigger_condition = (
                f"RSI={current_rsi:.1f} in 20-45 zone, price {bb_distance*100:.2f}% from "
                f"lower_BB={lower_bb:.5f}, vol_ratio={vol_ratio:.2f}"
            )
            needs_for_trigger = None
        else:
            parts = []
            if not rsi_in_zone:   parts.append(f"RSI={current_rsi:.1f} not in 20-45 (need 20≤RSI≤45)")
            if not near_lower_bb: parts.append(f"price {bb_distance*100:.2f}% from lower BB (need ≤2%)")
            if not volume_ok:     parts.append(f"vol_ratio={vol_ratio:.2f} too low (need ≥0.7)")
            if is_freefall:       parts.append(f"SMA slope={slope_raw*100:.2f}% in freefall (need >-3%)")
            if global_trend == 'BEARISH': parts.append("market BEARISH — BTC/ETH both below SMA20")
            trigger_condition = None
            needs_for_trigger = "; ".join(parts) if parts else signal_type

        return {
            'symbol': symbol,
            'strategy': 'MOMENTUM_PB',
            'signal': signal_type,
            'score': score,
            'meta': {
                'rsi': round(current_rsi, 2),
                'price': current_price,
                'lower_bb': round(lower_bb, 6),
                'upper_bb': round(upper_bb, 6),
                'middle_bb': round(middle_bb, 6),
                'bb_dist': round(bb_distance, 4),
                'vol_ratio': round(vol_ratio, 3),
                'slope': round(slope_raw, 4),
                'is_freefall': is_freefall,
                'global_trend': global_trend,
                'trigger_condition': trigger_condition,
                'needs_for_trigger': needs_for_trigger,
                'conditions_checked': conditions_checked,
            }
        }

    def place_buy_order(self, symbol, fixed_qty=None):
        if fixed_qty:
            self.execution_handler.submit_order(symbol, fixed_qty, 'buy')
