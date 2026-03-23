import pandas as pd
import numpy as np
from termcolor import colored
from strategies.base import BaseStrategy

class SupertrendStrategy(BaseStrategy):
    """
    Supertrend Strategy (The Trend Master):
    - Uses ATR to calculate dynamic Upper/Lower bands.
    - If Price > Upper Band (Red), Flip to Green (Buy).
    - If Price < Lower Band (Green), Flip to Red (Sell).
    - Acts as a Trailing Stop.
    """
    
    def get_signal(self, symbol, bars):
        """
        Returns Signal dict: {symbol, score, type, meta}
        """
        # Need enough data for ATR and recursive calculation (at least 50 bars)
        if len(bars) < 50:
            return {'symbol': symbol, 'signal': 'HOLD (NO_DATA)', 'score': 0, 'meta': {}}

        # 1. PARAMETERS (Standard Crypto Settings)
        period = 10
        multiplier = 3.0
        
        # 2. CALCULATE ATR
        bars['tr0'] = abs(bars['high'] - bars['low'])
        bars['tr1'] = abs(bars['high'] - bars['close'].shift(1))
        bars['tr2'] = abs(bars['low'] - bars['close'].shift(1))
        bars['tr'] = bars[['tr0', 'tr1', 'tr2']].max(axis=1)
        bars['atr'] = bars['tr'].rolling(period).mean()
        
        # 3. CALCULATE SUPERTREND BANDS
        hl2 = (bars['high'] + bars['low']) / 2
        bars['basic_upper'] = hl2 + (multiplier * bars['atr'])
        bars['basic_lower'] = hl2 - (multiplier * bars['atr'])
        
        # 4. ITERATIVE LOGIC (Trend Flip)
        # We need to iterate to maintain state of 'final_upper', 'final_lower', and 'trend'
        # To speed this up, we'll just loop the last few rows or do a full vector loop if not too heavy.
        # Since we only have ~100 bars, a full loop is fine.
        
        final_upper = [0.0] * len(bars)
        final_lower = [0.0] * len(bars)
        trend = [True] * len(bars) # True = Green (Bullish), False = Red (Bearish)
        
        # Initialize
        close = bars['close'].values
        basic_upper = bars['basic_upper'].values
        basic_lower = bars['basic_lower'].values
        
        for i in range(1, len(bars)):
            # Warning: nan checks
            if np.isnan(basic_upper[i]): continue
            
            # UPPER BAND LOGIC
            # If current basic upper < prev final upper, move it down.
            # OR if prev close > prev final upper (we were already below it), keep it tight.
            if basic_upper[i] < final_upper[i-1] or close[i-1] > final_upper[i-1]:
                final_upper[i] = basic_upper[i]
            else:
                final_upper[i] = final_upper[i-1]
                
            # LOWER BAND LOGIC
            if basic_lower[i] > final_lower[i-1] or close[i-1] < final_lower[i-1]:
                final_lower[i] = basic_lower[i]
            else:
                final_lower[i] = final_lower[i-1]
                
            # TREND LOGIC
            # If was Green (True) and Close < Lower -> Flip to Red
            # If was Red (False) and Close > Upper -> Flip to Green
            
            prev_trend = trend[i-1]
            
            if prev_trend and close[i] < final_lower[i]:
                trend[i] = False
            elif not prev_trend and close[i] > final_upper[i]:
                trend[i] = True
            else:
                trend[i] = prev_trend
                
            # If Trend is Green, Supertrend Line is Lower Band. If Red, it's Upper Band.
        
        bars['supertrend'] = np.where(trend, final_lower, final_upper)
        bars['trend_green'] = trend
        
        # CURRENT STATE
        current_trend = trend[-1]
        prev_trend = trend[-2]
        price = close[-1]
        stop_level = final_lower[-1] if current_trend else final_upper[-1]
        
        # LOGIC
        signal_type = 'HOLD'
        score = 0
        
        if current_trend and not prev_trend:
            # FRESH FLIP TO GREEN (BUY)
            signal_type = 'BUY'
            score = 100
        elif current_trend:
            # TREND CONTINUATION
            # Check momentum?
            signal_type = 'HOLD (UPTREND)'
            score = 50
        else:
            signal_type = 'HOLD (DOWNTREND)'
            score = 0
            
        # ─── AUDIT CONTEXT ───
        trend_flipped = current_trend and not prev_trend
        atr_val = bars['atr'].iloc[-1] if 'atr' in bars else None
        conditions_checked = {
            'trend_green':    {'value': current_trend, 'threshold': 'price > supertrend upper band', 'passed': current_trend},
            'fresh_flip':     {'value': trend_flipped, 'threshold': 'prev=RED, now=GREEN', 'passed': trend_flipped},
        }
        if 'BUY' in signal_type:
            trigger_condition = (
                f"Supertrend flipped GREEN: price {price:.5f} crossed above "
                f"final_upper {final_upper[-2]:.5f}; ATR={round(atr_val,6) if atr_val else 'N/A'}, "
                f"stop_level={stop_level:.5f}"
            )
            needs_for_trigger = None
        else:
            parts = []
            if not current_trend:  parts.append(f"Supertrend is RED (price {price:.5f} below upper band {final_upper[-1]:.5f})")
            elif not trend_flipped: parts.append(f"Supertrend already GREEN — no fresh flip (continuation, not entry signal)")
            trigger_condition = None
            needs_for_trigger = "; ".join(parts) if parts else signal_type

        return {
            'symbol': symbol,
            'strategy': 'SUPERTREND',
            'signal': signal_type,
            'score': score,
            'meta': {
                'price': price,
                'stop_loss': round(stop_level, 6),
                'is_green': current_trend,
                'trend_flipped': trend_flipped,
                'atr': round(atr_val, 8) if atr_val is not None else None,
                'final_upper': round(final_upper[-1], 6),
                'final_lower': round(final_lower[-1], 6),
                'trigger_condition': trigger_condition,
                'needs_for_trigger': needs_for_trigger,
                'conditions_checked': conditions_checked,
            }
        }

    def place_buy_order(self, symbol, fixed_qty=None):
        if fixed_qty:
            self.execution_handler.submit_order(symbol, fixed_qty, 'buy')
