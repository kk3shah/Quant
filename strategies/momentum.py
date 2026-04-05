from strategies.base import BaseStrategy
from termcolor import colored
import pandas as pd
import numpy as np

class MomentumStrategy(BaseStrategy):
    """
    Scalping Momentum Strategy (1-Minute):
    - Trades micro-trends.
    - Uses shorter lookbacks (30 minutes) for Z-Score.
    """
    def get_signal(self, symbol, bars):
        """
        Returns a Signal dict: {symbol, score, type, meta}
        Score: Z-Score (Higher is better for breakout)
        """
        # 1. Z-Score (Adaptive Window via FFT)
        # Default to 60, but use FFT heartbeat if provided by engine
        window = 60
        if 'meta_dom_period' in bars and pd.notna(bars['meta_dom_period'].iloc[-1]):
            window = int(bars['meta_dom_period'].iloc[-1])
            
        bars['mean'] = bars['close'].rolling(window=window).mean()
        bars['std'] = bars['close'].rolling(window=window).std()
        current_z = (bars['close'].iloc[-1] - bars['mean'].iloc[-1]) / bars['std'].iloc[-1]
        
        # 2. Check Global Trend (Scalar)
        if 'global_trend' in bars:
            global_trend = bars['global_trend'].iloc[-1]
        else:
            global_trend = 'NEUTRAL'

        # SCORE CALCULATION
        # We want High Z-Score. 
        signal_type = 'HOLD'
        
        # QUANT FILTER: VOLUME CONFIRMATION
        # Is current volume > 1.5x Average Volume?
        # Note: 'volume' column might be missing if we only fetched close? 
        # DataHandler usually sets 'volume'.
        vol_spike = False
        if 'volume' in bars:
             avg_vol = bars['volume'].rolling(window=20).mean().iloc[-1]
             cur_vol = bars['volume'].iloc[-1]
             if cur_vol > (avg_vol * 2.0):
                 vol_spike = True
        else:
             vol_spike = True # Default to True if no volume data (fallback)

        if current_z > 2.0:
            # Bear filter relaxed: Z>2.0 is already a strong move.  Requiring
            # Z>2.5 in bear markets blocked every signal for days.  Instead,
            # apply the same Z>2.0 threshold universally and let the volume
            # filter catch low-conviction moves.
            if not vol_spike and current_z < 2.5:
                # If no volume support, we need a HUGE breakout (Z > 2.5) to believe it
                signal_type = 'HOLD (LOW_VOL)'
            else:
                signal_type = 'BUY'
        
        # ─── AUDIT CONTEXT ───
        vol_ratio_val = None
        avg_vol_val = None
        cur_vol_val = None
        if 'volume' in bars:
            avg_vol_val = bars['volume'].rolling(window=20).mean().iloc[-1]
            cur_vol_val = bars['volume'].iloc[-1]
            vol_ratio_val = round(cur_vol_val / avg_vol_val, 3) if avg_vol_val and avg_vol_val > 0 else None

        z_ok = current_z > 2.0
        vol_filter = vol_spike or current_z >= 2.5

        conditions_checked = {
            'z_score_above_2': {'value': round(current_z, 4), 'threshold': '>2.0', 'passed': z_ok},
            'vol_spike_or_strong_z': {'value': round(current_z, 4), 'threshold': 'vol>2x avg OR z>2.5', 'passed': vol_filter},
        }
        if 'BUY' in signal_type:
            trigger_condition = (
                f"Z-score={current_z:.3f} > 2.0 with vol_spike={vol_spike} "
                f"(vol_ratio={vol_ratio_val}), window={window}"
            )
            needs_for_trigger = None
        else:
            parts = []
            if not z_ok:      parts.append(f"Z-score={current_z:.3f} below 2.0 threshold")
            if not vol_filter: parts.append(f"No volume spike (vol_ratio={vol_ratio_val}) and Z-score<2.5")
            trigger_condition = None
            needs_for_trigger = "; ".join(parts) if parts else signal_type

        return {
            'symbol': symbol,
            'strategy': 'MOMENTUM',
            'signal': signal_type,
            'score': current_z,
            'meta': {
                'z_score': round(current_z, 4),
                'vol_spike': vol_spike,
                'vol_ratio': vol_ratio_val,
                'window_used': window,
                'global_trend': global_trend,
                'trigger_condition': trigger_condition,
                'needs_for_trigger': needs_for_trigger,
                'conditions_checked': conditions_checked,
            }
        }

    def run_on_data(self, symbol, bars):
        # ... (Deprecated or used for legacy direct calls? We'll maintain it for now by calling get_signal)
        pass

    def place_buy_order(self, symbol, scaler=1.0, fixed_qty=None):
        if fixed_qty:
            self.execution_handler.submit_order(symbol, fixed_qty, 'buy')
            return

        quote_currency = symbol.split('/')[1]
        positions = self.execution_handler.get_positions()
        quote_amount = positions.get(quote_currency, 0.0)
        
        # DYNAMIC SIZING DEFAULT
        # ...
        target_amount_usd = quote_amount * 0.95 * scaler
        
        price = self.data_handler.get_latest_price(symbol)
        if price:
            qty = target_amount_usd / price
            self.execution_handler.submit_order(symbol, qty, 'buy')
