import pandas as pd
from strategies.base import BaseStrategy
from termcolor import colored

class MeanReversionStrategy(BaseStrategy):
    """
    Scalping Mean Reversion (1-Minute):
    - Uses RSI logic (Buy < 30, Sell > 70) for quick flips in ranging markets.
    """
    def get_signal(self, symbol, bars):
        """
        Returns Signal dict.
        Score: Priority to RSI < 30 (Lower is better). Score = 100 - RSI (so higher score is "more oversold").
        """
        # Adaptive Period Tuning (FFT)
        period = 14
        if 'meta_dom_period' in bars and pd.notna(bars['meta_dom_period'].iloc[-1]):
            period = int(bars['meta_dom_period'].iloc[-1])
            
        # RSI & BB Calculation
        delta = bars['close'].diff()
        gain = delta.clip(lower=0)
        loss = -1 * delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss
        bars['rsi'] = 100 - (100 / (1 + rs))
        
        current_rsi = bars['rsi'].iloc[-1]
        if pd.isna(current_rsi): current_rsi = 50.0

        # BB (Adaptive Window)
        sma = bars['close'].rolling(window=period).mean()
        std = bars['close'].rolling(window=period).std()
        lower_bb = (sma - 2*std).iloc[-1]
        current_price = bars['close'].iloc[-1]
        
        # ADX CALCULATION (Trend Strength)
        # Simplified ATR and DX for 14 periods to avoid massive library overhead
        # TR = Max(H-L, Abs(H-Cprev), Abs(L-Cprev))
        # But we only have 'close'. We will use Volatility as proxy for ADX strength in this simplified version
        # OR better: Use Slope of SMA as trend strength proxy.
        
        # PROXY ADX: 15-min SMA Slope Normalized
        # If slope is very negative, it's a strong downtrend.
        sma_short = bars['close'].rolling(window=10).mean()
        if len(sma_short) >= 10:
            slope_raw = (sma_short.iloc[-1] - sma_short.iloc[-10]) / sma_short.iloc[-10]
        else:
            slope_raw = 0
            
        is_crashing = slope_raw < -0.02 # Dropped > 2% in SMA over 10 1h candles = real crash
        
        # Global Filter
        if 'global_trend' in bars:
            global_trend = bars['global_trend'].iloc[-1]
        else:
            global_trend = 'NEUTRAL'
            
        signal_type = 'HOLD'
        
        # BUY CONDITION
        # 1. RSI < 30 (Oversold)
        # 2. Price < Lower BB (Statistical Extreme)
        # 3. NOT Crashing (Slope Check / ADX Proxy) - CRITICAL QUANT FILTER
        
        if current_rsi < 30 and current_price < lower_bb * 1.01:
            if global_trend == 'BEARISH' and current_rsi > 25:
                signal_type = 'HOLD (BTC_BEAR)'
            elif is_crashing:
                signal_type = 'HOLD (CRASHING)' # Don't catch the knife
            else:
                signal_type = 'BUY'
        
        # Score: 100 - RSI (e.g. RSI 20 => Score 80. RSI 10 => Score 90).
        score = 100 - current_rsi
        if signal_type.startswith('HOLD'):
             score = 0 # Downgrade score if filtered
        
        # ─── AUDIT CONTEXT ───
        rsi_ok = current_rsi < 30
        below_bb = current_price < lower_bb * 1.01
        not_crashing = not is_crashing
        bear_ok = not (global_trend == 'BEARISH' and current_rsi > 25)
        conditions_checked = {
            'rsi_oversold':  {'value': round(current_rsi, 2), 'threshold': '<30', 'passed': rsi_ok},
            'below_lower_bb':{'value': round(current_price, 6), 'threshold': f'<{round(lower_bb*1.01,6)} (lower_BB×1.01)', 'passed': below_bb},
            'not_crashing':  {'value': round(slope_raw, 4), 'threshold': '>-0.02', 'passed': not_crashing},
            'bear_filter':   {'value': global_trend, 'threshold': 'not BEARISH unless RSI<25', 'passed': bear_ok},
        }
        if 'BUY' in signal_type:
            trigger_condition = (
                f"RSI={current_rsi:.1f} < 30 oversold AND price {current_price:.5f} "
                f"< lower_BB {lower_bb:.5f} (period={period})"
            )
            needs_for_trigger = None
        else:
            parts = []
            if not rsi_ok:    parts.append(f"RSI={current_rsi:.1f} not oversold (need <30)")
            if not below_bb:  parts.append(f"Price {current_price:.5f} not below lower_BB {lower_bb:.5f}×1.01={lower_bb*1.01:.5f}")
            if is_crashing:   parts.append(f"SMA slope={slope_raw*100:.2f}% (crashing, need >-2%)")
            if not bear_ok:   parts.append(f"Market BEARISH and RSI={current_rsi:.1f} > 25 (need RSI<25 to override)")
            trigger_condition = None
            needs_for_trigger = "; ".join(parts) if parts else signal_type

        return {
            'symbol': symbol,
            'strategy': 'MEAN_REV',
            'signal': signal_type,
            'score': score,
            'meta': {
                'rsi': round(current_rsi, 2),
                'price': current_price,
                'lower_bb': round(lower_bb, 6),
                'slope': round(slope_raw, 4),
                'is_crashing': is_crashing,
                'period_used': period,
                'global_trend': global_trend,
                'trigger_condition': trigger_condition,
                'needs_for_trigger': needs_for_trigger,
                'conditions_checked': conditions_checked,
            }
        }

    def run_on_data(self, symbol, bars):
        pass

    def place_buy_order(self, symbol, fixed_qty=None):
        if fixed_qty:
            # Direct execution from Engine (DCA or Weighted Sizing)
            self.execution_handler.submit_order(symbol, fixed_qty, 'buy')
            return

        quote_currency = symbol.split('/')[1]
        positions = self.execution_handler.get_positions()
        quote_amount = positions.get(quote_currency, 0.0)
        
        from config import Config
        
        # DYNAMIC SIZING: Split capital among remaining slots
        fiat = ['USD', 'CAD', 'EUR', 'USDT', 'USDC', 'ZUSD', 'ZCAD', 'KFEE']
        active_count = 0
        for a, q in positions.items():
             if a not in fiat and q > 0.0001:
                 active_count += 1
        
        remaining_slots = Config.MAX_OPEN_POSITIONS - active_count
        if remaining_slots < 1: 
            remaining_slots = 1
            
        # DYNAMIC SIZING DEFAULT
        # ... (Same as before if called without fixed_qty)
        target_amount_usd = quote_amount * 0.95 
        
        price = self.data_handler.get_latest_price(symbol)
        if price:
            qty = target_amount_usd / price
            self.execution_handler.submit_order(symbol, qty, 'buy')
