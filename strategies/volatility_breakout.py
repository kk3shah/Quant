import pandas as pd
import numpy as np
from strategies.base import BaseStrategy

class VolatilityBreakoutStrategy(BaseStrategy):
    """
    High-Volatility Breakout Strategy (15m Timeframe):
    - Wait for tight consolidation (low Bollinger Band width).
    - Buy on strong volume spike combined with price breaking above Upper BB.
    - Aggressive setup designed for 6%+ intraday runs.
    """

    def get_signal(self, symbol, bars):
        if len(bars) < 25:
            return {'symbol': symbol, 'signal': 'HOLD (NO_DATA)', 'score': 0, 'meta': {}}

        # Bollinger Bands (20, 2)
        sma20 = bars['close'].rolling(window=20).mean()
        std20 = bars['close'].rolling(window=20).std()
        upper_bb = (sma20 + 2 * std20)
        lower_bb = (sma20 - 2 * std20)
        
        # Band Width (Measure of consolidation/squeeze)
        bb_width = (upper_bb - lower_bb) / sma20
        
        # Volume Spike
        vol_avg = bars['volume'].rolling(window=20).mean()
        
        current_price = bars['close'].iloc[-1]
        current_upper_bb = upper_bb.iloc[-1]
        current_lower_bb = lower_bb.iloc[-1]
        current_vol = bars['volume'].iloc[-1]
        current_vol_avg = vol_avg.iloc[-1]
        current_bb_width = bb_width.iloc[-1]
        
        # Calculate recent minimum BB width to detect squeeze
        min_width_recent = bb_width.iloc[-10:-1].min()

        signal_type = 'HOLD'
        score = 0

        # BUY CONDITIONS
        # 1. Price is breaking OR has just broken above the Upper BB.
        breaking_out = current_price >= current_upper_bb * 0.995
        
        # 2. Volume is significantly higher than average
        volume_spike = current_vol > (current_vol_avg * 1.2)
        
        # 3. Consolidation preceded the breakout (Squeeze)
        # Prevents buying into something that's already been running up wildly and is exhausted.
        # Adjusted: 0.30 allows for 30% band width, accommodating high baseline volatility coins
        was_squeezed = min_width_recent < 0.30

        # Avoid buying if price is already completely parabolic (>10% above BB in one candle)
        too_far = current_price > current_upper_bb * 1.10

        if breaking_out and volume_spike and was_squeezed and not too_far:
            signal_type = 'BUY (BREAKOUT)'
            score = 80
            
            # If volume is massive, prioritize
            if current_vol > (current_vol_avg * 2.0):
                score = 95
        
        # SELL CONDITIONS (Strategy level fallback, though Handler manages TP/SL)
        rsi = self._calculate_rsi(bars)
        if rsi.iloc[-1] > 80:
             signal_type = 'SELL (OVEREXTENDED)'
             score = 0

        # ─── AUDIT CONTEXT ───
        vol_ratio_val = round(current_vol / current_vol_avg, 3) if current_vol_avg > 0 else 0
        rsi_val = round(rsi.iloc[-1], 2) if hasattr(rsi, 'iloc') else None
        conditions_checked = {
            'breaking_out':  {'value': round(current_price, 6), 'threshold': f'>={round(current_upper_bb*0.995,6)} (upper_bb×0.995)', 'passed': breaking_out},
            'volume_spike':  {'value': vol_ratio_val, 'threshold': '>=1.2× avg', 'passed': volume_spike},
            'was_squeezed':  {'value': round(min_width_recent, 4), 'threshold': '<0.30 BB_width', 'passed': was_squeezed},
            'not_parabolic': {'value': round(current_price, 6), 'threshold': f'<={round(current_upper_bb*1.10,6)} (upper_bb×1.10)', 'passed': not too_far},
        }
        if 'BUY' in signal_type:
            trigger_condition = (
                f"Price {current_price:.5f} broke above upper_BB {current_upper_bb:.5f}, "
                f"vol_ratio={vol_ratio_val}×, squeeze min_width={min_width_recent:.3f}"
            )
            needs_for_trigger = None
        else:
            parts = []
            if not breaking_out: parts.append(f"Price {current_price:.5f} not breaking upper_BB {current_upper_bb:.5f} (need ≥{current_upper_bb*0.995:.5f})")
            if not volume_spike: parts.append(f"vol_ratio={vol_ratio_val} too low (need ≥1.2×)")
            if not was_squeezed: parts.append(f"No prior squeeze: min_width={min_width_recent:.3f} ≥ 0.30")
            if too_far:          parts.append(f"Price {current_price:.5f} parabolic vs upper_BB {current_upper_bb:.5f} (>10% above)")
            trigger_condition = None
            needs_for_trigger = "; ".join(parts) if parts else signal_type

        return {
            'symbol': symbol,
            'strategy': 'VOL_BREAKOUT',
            'signal': signal_type,
            'score': score,
            'meta': {
                'price': current_price,
                'upper_bb': round(current_upper_bb, 6),
                'lower_bb': round(current_lower_bb, 6),
                'bb_width': round(current_bb_width, 4),
                'min_width_recent': round(min_width_recent, 4),
                'vol_ratio': vol_ratio_val,
                'rsi': rsi_val,
                'breaking_out': breaking_out,
                'volume_spike': volume_spike,
                'was_squeezed': was_squeezed,
                'too_far': too_far,
                'trigger_condition': trigger_condition,
                'needs_for_trigger': needs_for_trigger,
                'conditions_checked': conditions_checked,
            }
        }
    
    def _calculate_rsi(self, bars, periods=14):
        delta = bars['close'].diff()
        gain = delta.clip(lower=0)
        loss = -1 * delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1/periods, min_periods=periods, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/periods, min_periods=periods, adjust=False).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def place_buy_order(self, symbol, fixed_qty=None):
        if fixed_qty:
            self.execution_handler.submit_order(symbol, fixed_qty, 'buy')
