import pandas as pd
import numpy as np
from termcolor import colored
from strategies.base import BaseStrategy

class VolatilitySqueezeStrategy(BaseStrategy):
    """
    Volatility Squeeze Strategy (15-Minute):
    - Setup: Bollinger Bands inside Keltner Channels (Squeeze).
    - Trigger: Momentum expansion (Breakout of Upper Band).
    - Exit: Momentum loss or Band reversion.
    """
    
    def get_signal(self, symbol, bars):
        """
        Returns Signal dict: {symbol, score, type, meta}
        """
        if len(bars) < 25:
            return {'symbol': symbol, 'signal': 'HOLD (NO_DATA)', 'score': 0, 'meta': {}}

        # 1. INDICATORS
        
        # Bollinger Bands (20, 2.0)
        bars['sma20'] = bars['close'].rolling(window=20).mean()
        bars['std20'] = bars['close'].rolling(window=20).std()
        bars['upper_bb'] = bars['sma20'] + (2.0 * bars['std20'])
        bars['lower_bb'] = bars['sma20'] - (2.0 * bars['std20'])
        
        # Keltner Channels (20, 1.5 ATR)
        # TR calculation
        bars['tr0'] = abs(bars['high'] - bars['low'])
        bars['tr1'] = abs(bars['high'] - bars['close'].shift())
        bars['tr2'] = abs(bars['low'] - bars['close'].shift())
        bars['tr'] = bars[['tr0', 'tr1', 'tr2']].max(axis=1)
        bars['atr'] = bars['tr'].rolling(window=20).mean()
        
        bars['upper_kc'] = bars['sma20'] + (1.5 * bars['atr'])
        bars['lower_kc'] = bars['sma20'] - (1.5 * bars['atr'])
        
        # Momentum (Delta of Close vs Linear Reg or just Delta for speed)
        # Using simple Price vs SMA delta for Momentum direction
        bars['mom'] = bars['close'] - bars['sma20']

        # CURRENT VALUES
        row = bars.iloc[-1]
        
        # LOGIC
        # 1. Check for SQUEEZE (Consolidation)
        # BB strictly inside KC
        in_squeeze = (row['lower_bb'] > row['lower_kc']) and (row['upper_bb'] < row['upper_kc'])
        
        # 2. Check for BREAKOUT (Expansion)
        # Price > Upper BB (or at least Upper SMA + 1.5 std)
        breakout = row['close'] > row['upper_bb']
        
        # SCORING
        signal_type = 'HOLD'
        score = 0
        
        if breakout:
            # Did we just come out of a squeeze? (Check previous candle)
            prev_row = bars.iloc[-2]
            was_squeezing = (prev_row['lower_bb'] > prev_row['lower_kc']) and (prev_row['upper_bb'] < prev_row['upper_kc'])
            
            if was_squeezing or in_squeeze: # Immediate breakout
                signal_type = 'BUY'
                score = 100 # PERFECT TTM SQUEEZE FIRE
            else:
                # Just a band breakout, still good momentum
                signal_type = 'BUY (MOM)' 
                score = 80
        
        elif in_squeeze:
            signal_type = 'HOLD (SQUEEZE)'
            score = 50 # Watchlist this!

        # Global Trend Filter (Safety)
        # IMPORTANT: use elif so this never overwrites an already-confirmed BUY signal.
        # Previously this was a bare `if`, which silently discarded every BUY in bear markets.
        elif 'global_trend' in bars:
            if bars['global_trend'].iloc[-1] == 'BEARISH':
                signal_type = 'HOLD (BTC_BEAR)'
                score = 0

        # ─── AUDIT CONTEXT ───
        prev_row = bars.iloc[-2]
        was_squeezing = (prev_row['lower_bb'] > prev_row['lower_kc']) and (prev_row['upper_bb'] < prev_row['upper_kc'])
        conditions_checked = {
            'breakout':      {'value': round(row['close'], 6), 'threshold': f">{round(row['upper_bb'],6)} (upper_BB)", 'passed': breakout},
            'was_squeezing': {'value': was_squeezing, 'threshold': 'BB inside KC on prev candle', 'passed': was_squeezing},
            'in_squeeze':    {'value': in_squeeze, 'threshold': 'BB inside KC now', 'passed': in_squeeze},
        }
        if 'BUY' in signal_type:
            squeeze_detail = "after squeeze" if (was_squeezing or in_squeeze) else "momentum only (no prior squeeze)"
            trigger_condition = (
                f"Price {row['close']:.5f} broke above upper_BB {row['upper_bb']:.5f} {squeeze_detail}; "
                f"ATR={row['atr']:.6f}, mom={row['mom']:.6f}"
            )
            needs_for_trigger = None
        else:
            parts = []
            if not breakout:    parts.append(f"Price {row['close']:.5f} not above upper_BB {row['upper_bb']:.5f}")
            if in_squeeze:      parts.append(f"Still in squeeze (BB inside KC) — awaiting breakout")
            if signal_type == 'HOLD (BTC_BEAR)': parts.append("Market BEARISH (BTC/ETH below SMA20)")
            trigger_condition = None
            needs_for_trigger = "; ".join(parts) if parts else signal_type

        return {
            'symbol': symbol,
            'strategy': 'VOL_SQUEEZE',
            'signal': signal_type,
            'score': score,
            'meta': {
                'price': row['close'],
                'upper_bb': round(row['upper_bb'], 6),
                'lower_bb': round(row['lower_bb'], 6),
                'upper_kc': round(row['upper_kc'], 6),
                'lower_kc': round(row['lower_kc'], 6),
                'atr': round(row['atr'], 8),
                'momentum': round(row['mom'], 6),
                'in_squeeze': in_squeeze,
                'was_squeezing': was_squeezing,
                'breakout': breakout,
                'stop_loss': round(row['sma20'], 6),
                'trigger_condition': trigger_condition,
                'needs_for_trigger': needs_for_trigger,
                'conditions_checked': conditions_checked,
            }
        }

    def place_buy_order(self, symbol, fixed_qty=None):
        if fixed_qty:
            self.execution_handler.submit_order(symbol, fixed_qty, 'buy')
