import numpy as np
import pandas as pd
import json
from datetime import date
from termcolor import colored
try:
    import audit_logger
except ImportError:
    audit_logger = None
from strategies.volatility_breakout import VolatilityBreakoutStrategy
from strategies.volatility_squeeze import VolatilitySqueezeStrategy
from strategies.momentum import MomentumStrategy
from strategies.momentum_pullback import MomentumPullbackStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend_surfer import TrendSurferStrategy
from strategies.supertrend import SupertrendStrategy
from strategies.deep_value import DeepValueStrategy
from strategies.spectral import DominantCycleAnalyzer

class StrategyEngine:
    def __init__(self, data_handler, execution_handler):
        self.data_handler = data_handler
        self.execution_handler = execution_handler
        self.daily_trade_count = 0
        self.daily_fee_total = 0.0
        self._last_reset_date = None

        # Load starting_equity from disk so kill switch survives restarts.
        # Previously this was only in memory — a restart after losses reset the budget.
        self.starting_equity = self._load_starting_equity()

        # Reconcile positions.json against real Kraken balance on startup.
        # Removes phantom positions (in file but not on exchange) so they don't
        # block slot counting. Adds untracked real holdings so stop-loss works.
        self._reconcile_positions()

        # Initialize All Strategies
        self.strategies = {
            'VOL_BREAKOUT': VolatilityBreakoutStrategy(data_handler, execution_handler),
            'VOL_SQUEEZE': VolatilitySqueezeStrategy(data_handler, execution_handler),
            'MOMENTUM': MomentumStrategy(data_handler, execution_handler),
            'MOMENTUM_PB': MomentumPullbackStrategy(data_handler, execution_handler),
            'MEAN_REV': MeanReversionStrategy(data_handler, execution_handler),
            'TREND_SURFER': TrendSurferStrategy(data_handler, execution_handler),
            'SUPERTREND': SupertrendStrategy(data_handler, execution_handler),
            'DEEP_VALUE': DeepValueStrategy(data_handler, execution_handler)
        }
        
        # Regime -> Strategy Routing
        self.regime_strategies = {
            'VOLATILE':       ['VOL_BREAKOUT', 'VOL_SQUEEZE', 'MOMENTUM'],
            'TRENDING_BULL':  ['TREND_SURFER', 'SUPERTREND', 'MOMENTUM'],
            'TRENDING_BEAR':  ['MEAN_REV', 'DEEP_VALUE'],
            'RANGING':        ['MOMENTUM_PB', 'VOL_SQUEEZE', 'MEAN_REV'],
            'CYCLICAL':       ['MOMENTUM_PB', 'MEAN_REV'],
            'UNKNOWN':        ['MOMENTUM_PB', 'SUPERTREND'],
        }

    def _reset_daily_counters(self):
        """Reset daily trade/fee counters at midnight."""
        today = date.today()
        if self._last_reset_date != today:
            self.daily_trade_count = 0
            self.daily_fee_total = 0.0
            self._last_reset_date = today

    def _load_starting_equity(self):
        """Load persisted starting_equity from disk so kill switch survives restarts."""
        import json, os
        session_file = 'data/session.json'
        if os.path.exists(session_file):
            try:
                with open(session_file, 'r') as f:
                    data = json.load(f)
                val = data.get('starting_equity')
                if val and val > 0:
                    print(f"  [SESSION] Loaded starting_equity from disk: ${val:.2f}")
                    return val
            except: pass
        return None

    def _reconcile_positions(self):
        """
        Reconcile positions.json against the live Kraken balance on startup.

        - PHANTOM entries (in file, not on exchange or < MIN_POSITION_VALUE_USD):
          removed so they stop consuming slots.
        - UNTRACKED holdings (on exchange, not in file, value >= MIN_POSITION_VALUE_USD):
          added with entry_price = current live price so stop-loss / take-profit work.
          (We don't know the real entry price, so we use current price — this means
          the position starts at 0% P&L; any further drop will hit the stop.)

        All changes are printed and Telegram-alerted so you always know what changed.
        """
        import json, os, time as _time
        from config import Config
        pos_file = 'data/positions.json'
        fiat = {'USD', 'ZUSD', 'USDT', 'USDC', 'KFEE', 'ZCAD', 'CAD', 'EUR', 'GBP'}

        try:
            balance = self.execution_handler.exchange.fetch_balance()
            kraken_holdings = {
                asset: qty
                for asset, qty in balance.get('total', {}).items()
                if asset not in fiat and qty and qty > 0.0001
            }
        except Exception as e:
            print(f"  [RECONCILE] Could not fetch balance: {e}")
            return

        pos_data = {}
        if os.path.exists(pos_file):
            try:
                with open(pos_file) as f:
                    pos_data = json.load(f)
            except Exception:
                pass

        changes = []

        # 1. Remove phantoms — positions tracked locally but not (or dust) on Kraken
        for symbol in list(pos_data.keys()):
            asset = symbol.split('/')[0]
            kraken_qty = kraken_holdings.get(asset, 0)
            real_value = 0.0
            if kraken_qty > 0.0001:
                try:
                    ticker = self.execution_handler.exchange.fetch_ticker(symbol)
                    real_value = kraken_qty * ticker['last']
                except Exception:
                    pass
            if real_value < Config.MIN_POSITION_VALUE_USD:
                del pos_data[symbol]
                msg = (f"  [RECONCILE] Removed phantom position {symbol} "
                       f"(Kraken has ${real_value:.4f} — below ${Config.MIN_POSITION_VALUE_USD} threshold)")
                print(msg)
                changes.append(msg)

        # 2. Add untracked real holdings
        for asset, qty in kraken_holdings.items():
            symbol = f"{asset}/{Config.QUOTE_CURRENCY}"
            if symbol in pos_data:
                continue  # already tracked
            try:
                ticker = self.execution_handler.exchange.fetch_ticker(symbol)
                value = qty * ticker['last']
                if value < Config.MIN_POSITION_VALUE_USD:
                    continue
                # Use current price as synthetic entry (best we can do without history)
                pos_data[symbol] = {
                    'entry_price': ticker['last'],
                    'entry_time': int(_time.time() * 1000),
                    'strategy': 'RECONCILED',
                    'peak_price': ticker['last'],
                }
                msg = (f"  [RECONCILE] Added untracked {symbol}: "
                       f"qty={qty:.4f}, price=${ticker['last']:.5f}, value=${value:.2f} "
                       f"(entry_price set to current — stop-loss now active)")
                print(msg)
                changes.append(msg)
            except Exception:
                pass

        if changes:
            try:
                with open(pos_file, 'w') as f:
                    json.dump(pos_data, f, indent=2)
                print(f"  [RECONCILE] positions.json updated ({len(changes)} changes)")
                # Alert Telegram
                try:
                    import requests as _req
                    import os as _os
                    _token = _os.getenv('TELEGRAM_BOT_TOKEN', '8436312230:AAELpXdhwwt4b6oe2Ysd0X4LSwWjcH4313c')
                    _chat  = _os.getenv('TELEGRAM_CHAT_ID', '5572465493')
                    _body  = '🔄 <b>Position reconciliation on startup</b>\n' + '\n'.join(changes)
                    _req.post(f"https://api.telegram.org/bot{_token}/sendMessage",
                              json={'chat_id': _chat, 'text': _body, 'parse_mode': 'HTML'},
                              timeout=10)
                except Exception:
                    pass
            except Exception as e:
                print(f"  [RECONCILE] Failed to write positions.json: {e}")
        else:
            print("  [RECONCILE] positions.json is in sync with Kraken balance.")

    def _save_starting_equity(self, equity):
        """Persist starting_equity so it survives bot restarts."""
        import json, os
        os.makedirs('data', exist_ok=True)
        try:
            # Preserve existing fields (daily_equity, etc.)
            existing = {}
            if os.path.exists('data/session.json'):
                with open('data/session.json', 'r') as f:
                    existing = json.load(f)
            existing['starting_equity'] = equity
            with open('data/session.json', 'w') as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            print(f"  [SESSION] Warning: could not save starting_equity: {e}")

    def _save_daily_equity_snapshot(self, equity):
        """Save a daily equity snapshot for 24h P&L tracking in Telegram reports.
        Updates once per day (when the stored date differs from today)."""
        import json, os, datetime
        session_file = 'data/session.json'
        today = datetime.date.today().isoformat()
        try:
            existing = {}
            if os.path.exists(session_file):
                with open(session_file, 'r') as f:
                    existing = json.load(f)
            last_date = existing.get('daily_equity_date')
            if last_date != today:
                # Roll: yesterday's equity becomes the reference for today's P&L
                existing['daily_equity'] = existing.get('pending_daily_equity', equity)
                existing['daily_equity_date'] = today
            # Always store current equity as pending for tomorrow's reference
            existing['pending_daily_equity'] = equity
            with open(session_file, 'w') as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            print(f"  [SESSION] Warning: could not save daily equity snapshot: {e}")

    def _calculate_equity(self):
        """Calculate total account equity in USD."""
        import time
        for attempt in range(3):
            try:
                balance = self.execution_handler.exchange.fetch_balance()
                total = 0.0
                equity_logs = []
                
                if 'total' in balance:
                    for currency, amount in balance['total'].items():
                        if amount < 0.000001:  # Lower threshold
                            continue
                            
                        # 1. CASH (USD / Stable)
                        if currency in ['USD', 'ZUSD', 'USDT', 'USDC']:
                            total += amount
                            equity_logs.append(f"{currency}: ${amount:.2f}")
                        elif currency in ['KFEE', 'ZCAD', 'CAD', 'EUR']:
                            continue # Ignore non-USD fiat / fees
                        else:
                            # 2. ASSET VALUE
                            pair = f"{currency}/USD"
                            try:
                                ticker = self.execution_handler.exchange.fetch_ticker(pair)
                                val = amount * ticker['last']
                                total += val
                                equity_logs.append(f"{currency}: ${val:.2f}")
                            except:
                                # Try with USDT if USD fails
                                try:
                                    ticker = self.execution_handler.exchange.fetch_ticker(f"{currency}/USDT")
                                    val = amount * ticker['last']
                                    total += val
                                    equity_logs.append(f"{currency}: ${val:.2f}")
                                except:
                                    pass # Ticker not found
                
                # Debug log for tiny equities
                if total < 5.0 and total > 0:
                    print(colored(f"  [DEBUG] Tiny Equity Detected: ${total:.2f} ({', '.join(equity_logs)})", "yellow"))
                    
                return total
            except Exception as e:
                if "Rate limit exceeded" in str(e):
                    time.sleep(2 * (attempt + 1))
                    continue
                print(colored(f"  [ERROR] Equity calculation failed: {e}", "red"))
                return 0.0
        return 0.0

    def determine_regime(self, symbol, bars):
        """
        DETERMINES MARKET REGIME USING PURE MATH.
        1. Volatility (StdDev of % changes)
        2. Trend Strength (ADX Proxy: Slope of SMA)
        """
        if bars.empty or len(bars) < 30:
            return "UNKNOWN"

        # 1. Volatility (15m window)
        volatility = bars['close'].pct_change().tail(15).std()
        
        # 2. Trend Strength (Slope of last 10 candles SMA 20)
        bars['SMA_20'] = bars['close'].rolling(window=20).mean()
        sma_slice = bars['SMA_20'].iloc[-10:] 
        
        slope = 0
        if len(sma_slice) == 10:
            x = np.arange(10)
            y = sma_slice.values
            slope, _ = np.polyfit(x, y, 1) # Linear regression slope
            
        current_price = bars['close'].iloc[-1]
        
        # THRESHOLDS (Tuned for 15m Timeframe)
        # Volatility > 0.4% per candle is HIGH (Crash/Pump territory)
        is_volatile = volatility > 0.004 
        
        # Slope > 0.05% per minute (approx) is TRENDING
        normalized_slope = slope / current_price
        is_trending = abs(normalized_slope) > 0.0002 
        
        regime = "RANGING"
        if is_volatile:
            regime = "VOLATILE"
        elif is_trending:
            if normalized_slope > 0:
                regime = "TRENDING_BULL"
            else:
                regime = "TRENDING_BEAR"
        
        # 3. SPECTRAL ANALYSIS (FFT Heartbeat)
        prices = bars['close'].values
        dominant_period = DominantCycleAnalyzer.get_dominant_period(prices)
        snr = DominantCycleAnalyzer.get_signal_to_noise(prices)

        # Meta info for debug
        meta = {
            'vol': volatility,
            'slope': normalized_slope,
            'regime': regime,
            'dom_period': dominant_period,
            'snr': snr
        }

        # If SNR is high (>0.5), it's a strongly cyclical market
        if snr > 0.4 and regime == "RANGING":
            regime = "CYCLICAL"
            meta['regime'] = "CYCLICAL"

        return regime, meta

    def check_global_trend(self):
        """Backward-compat wrapper — returns 'BEARISH' / 'BULLISH' / 'NEUTRAL'."""
        gauge = self.compute_market_gauge()
        return gauge['label']

    def compute_market_gauge(self):
        """
        Rich market context from BTC + ETH + SOL.
        Returns dict with:
          label:         'BEARISH' / 'BULLISH' / 'NEUTRAL'
          strength:      -100 to +100 (negative = bearish, positive = bullish)
          min_dip_pct:   minimum distance below SMA20 to allow a buy
          require_green: whether to require a green confirmation candle
          detail:        human-readable string for logging
        """
        scores = []   # one per reference coin
        details = []
        try:
            for coin, weight in [('BTC/USD', 0.45), ('ETH/USD', 0.35), ('SOL/USD', 0.20)]:
                bars = self.data_handler.get_historical_data(coin, timeframe='1h', limit=50)
                if bars.empty:
                    continue
                price = bars['close'].iloc[-1]
                sma20 = bars['close'].rolling(window=20).mean().iloc[-1]
                # Distance from SMA as % (positive = above, negative = below)
                dist_pct = (price - sma20) / sma20 * 100

                # Recent momentum: last-4-candle trend
                last4 = bars['close'].iloc[-4:].tolist()
                mom = (last4[-1] - last4[0]) / last4[0] * 100 if last4[0] else 0

                # Coin score: blend of position vs SMA and short-term momentum
                # dist_pct capped to [-5, +5], mom capped to [-3, +3]
                capped_dist = max(-5, min(5, dist_pct))
                capped_mom = max(-3, min(3, mom))
                coin_score = (capped_dist * 12) + (capped_mom * 10)  # range ~ -90 to +90
                scores.append((coin.split('/')[0], coin_score * weight, dist_pct, mom))
                details.append(f"{coin.split('/')[0]}:{dist_pct:+.1f}%SMA,{mom:+.1f}%mom")
        except Exception as e:
            print(f"  Market gauge error: {e}")

        if not scores:
            return {'label': 'NEUTRAL', 'strength': 0, 'min_dip_pct': 0.008,
                    'require_green': False, 'detail': 'no data'}

        strength = sum(s for _, s, _, _ in scores)  # weighted sum, ~ -100 to +100
        strength = max(-100, min(100, strength))

        # Adaptive thresholds based on market strength
        # Dip filter is the primary gate (data: 0.8% dip → 71% WR, 1.0% → 83%)
        # Green candle only in strong bear (too strict otherwise — blocks winners)
        if strength > 30:
            label, min_dip, req_green = 'BULLISH', 0.005, False    # bull: mild dip ok
        elif strength > 0:
            label, min_dip, req_green = 'BULLISH', 0.007, False    # mild bull
        elif strength > -30:
            label, min_dip, req_green = 'BEARISH', 0.008, False    # mild bear: need real dip
        else:
            label, min_dip, req_green = 'BEARISH', 0.012, True     # strong bear: deep dips + green candle

        detail = f"mkt={strength:+.0f} ({', '.join(details)}) → dip≥{min_dip*100:.1f}%{'🟢' if req_green else ''}"
        return {'label': label, 'strength': strength, 'min_dip_pct': min_dip,
                'require_green': req_green, 'detail': detail}

    def _quick_indicators(self, bars):
        """Extract a snapshot of key indicators from bars for audit logging."""
        try:
            import math
            price = bars['close'].iloc[-1]
            delta = bars['close'].diff()
            gain = delta.clip(lower=0)
            loss = (-delta).clip(lower=0)
            avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            rs = avg_gain / avg_loss
            rsi_val = (100 - (100 / (1 + rs))).iloc[-1]
            sma20 = bars['close'].rolling(20).mean().iloc[-1]
            vol_ratio = None
            if 'volume' in bars:
                v_avg = bars['volume'].rolling(20).mean().iloc[-1]
                v_cur = bars['volume'].iloc[-1]
                if v_avg and v_avg > 0:
                    vol_ratio = round(v_cur / v_avg, 3)
            return {
                'price': round(price, 6),
                'rsi': round(rsi_val, 2) if not math.isnan(rsi_val) else None,
                'sma20': round(sma20, 6),
                'vol_ratio': vol_ratio,
            }
        except Exception:
            return {}

    def _update_peak_price(self, symbol, current_price):
        """Persist the highest price seen during a hold to positions.json."""
        import os
        pos_file = 'data/positions.json'
        try:
            pos_data = {}
            if os.path.exists(pos_file):
                with open(pos_file, 'r') as f:
                    pos_data = json.load(f)
            if symbol in pos_data:
                prev_peak = pos_data[symbol].get('peak_price', 0) or 0
                if current_price > prev_peak:
                    pos_data[symbol]['peak_price'] = current_price
                    with open(pos_file, 'w') as f:
                        json.dump(pos_data, f, indent=2)
        except Exception:
            pass

    def _get_peak_price(self, symbol):
        """Retrieve the tracked peak price from positions.json."""
        try:
            with open('data/positions.json', 'r') as f:
                return json.load(f).get(symbol, {}).get('peak_price')
        except Exception:
            return None

    def analyze_batch(self, universe):
        """
        MULTI-STRATEGY BATCH ANALYSIS (Reformed):
        1. Kill-switch check
        2. Manage Exits
        3. Scan & Enter WITH strategy confirmation
        """
        print(colored("\n--- Starting Batch Analysis (Reformed) ---", "cyan"))
        
        from config import Config

        # ─── SELF-LEARNING: Load per-strategy thresholds from optimizer ───
        try:
            from strategies.optimizer import load_params as _load_opt_params
            _strategy_params = _load_opt_params().get('strategies', {})
        except Exception:
            _strategy_params = {}

        # ─── KILL SWITCH: Check drawdown ───
        self._reset_daily_counters()
        current_equity = self._calculate_equity()
        
        if current_equity == 0.0:
            # Equity calculation failed (API error / rate limit) — skip drawdown check.
            # Never treat a failed API call as a real $0 equity event.
            print(colored("  [EQUITY] Warning: equity returned $0 — likely API failure. Skipping drawdown check.", "yellow"))
        else:
            if self.starting_equity is None:
                self.starting_equity = current_equity
                self._save_starting_equity(current_equity)
                print(f"  [EQUITY] Starting equity recorded: ${current_equity:.2f}")

            # Save daily equity snapshot for 24h P&L tracking in Telegram reports
            self._save_daily_equity_snapshot(current_equity)

            if self.starting_equity > 0:
                drawdown = (self.starting_equity - current_equity) / self.starting_equity
                print(f"  [EQUITY] Current: ${current_equity:.2f} | Start: ${self.starting_equity:.2f} | DD: {drawdown*100:.1f}%")
                if drawdown > Config.MAX_DRAWDOWN:
                    print(colored(f"  🚨 KILL SWITCH: Drawdown {drawdown*100:.1f}% > {Config.MAX_DRAWDOWN*100}%.", "red", attrs=['bold']))
                    # Liquidate all open positions
                    if not Config.PAPER_TRADING:
                        self.execution_handler.liquidate_all()

                    # CRITICAL: Reset starting_equity to current after liquidation.
                    # Otherwise the bot is permanently stuck: the old baseline is
                    # unreachable, drawdown stays >20% forever, and the bot never
                    # trades again. Accept the loss and restart from current equity.
                    print(colored(f"  [KILL SWITCH] Resetting starting_equity: ${self.starting_equity:.2f} → ${current_equity:.2f}", "yellow"))
                    self.starting_equity = current_equity
                    self._save_starting_equity(current_equity)

                    # Alert Telegram
                    try:
                        import requests as _req
                        _token = os.getenv('TELEGRAM_BOT_TOKEN', '8436312230:AAELpXdhwwt4b6oe2Ysd0X4LSwWjcH4313c')
                        _chat  = os.getenv('TELEGRAM_CHAT_ID', '5572465493')
                        _req.post(f"https://api.telegram.org/bot{_token}/sendMessage",
                                  json={'chat_id': _chat, 'parse_mode': 'HTML',
                                        'text': (f"🚨 <b>KILL SWITCH fired</b>\n"
                                                 f"Drawdown: {drawdown*100:.1f}%\n"
                                                 f"Liquidated all positions.\n"
                                                 f"Baseline reset: ${self.starting_equity:.2f}\n"
                                                 f"Bot will resume trading next cycle.")},
                                  timeout=10)
                    except Exception:
                        pass
                    return
        
        # ─── DAILY LIMITS CHECK ───
        if self.daily_trade_count >= Config.MAX_DAILY_TRADES:
            print(colored(f"  [LIMIT] Daily trade limit reached ({self.daily_trade_count}/{Config.MAX_DAILY_TRADES}). Skipping buys.", "yellow"))
            # Still manage exits below, just skip new entries
        
        if Config.PAPER_TRADING:
            print(colored("  [PAPER] Running in PAPER TRADING mode — no real orders will be placed.", "magenta"))
        
        buy_signals = []
        _market_gauge = self.compute_market_gauge()
        global_trend = _market_gauge['label']
        print(f"  [MARKET] {colored(_market_gauge['detail'], 'red' if global_trend=='BEARISH' else 'green')}")
        
        # 1. MANAGE POSITIONS
        positions = self.execution_handler.get_positions()
        fiat = ['USD', 'CAD', 'EUR', 'USDT', 'USDC', 'ZUSD', 'ZCAD', 'KFEE']

        # ─── Pre-compute cycle audit data (reused at end of cycle) ───
        # Count only real positions — dust (value < $1) is ignored everywhere.
        def _live_value(asset, qty):
            """Best-effort USD value for a position using cached ticker data."""
            try:
                sym = f"{asset}/{Config.QUOTE_CURRENCY}"
                t = self.execution_handler.exchange.fetch_ticker(sym)
                return qty * t['last']
            except Exception:
                return 0.0

        _open_pos_count = sum(
            1 for a, q in positions.items()
            if a not in fiat and q > 0.0001 and _live_value(a, q) >= Config.MIN_POSITION_VALUE_USD
        )
        _drawdown_pct = None
        if self.starting_equity and current_equity and self.starting_equity > 0:
            _drawdown_pct = (self.starting_equity - current_equity) / self.starting_equity * 100
        _cash_log = 0.0
        try:
            _acct_log = self.execution_handler.get_account()
            _cash_log = _acct_log.get(Config.QUOTE_CURRENCY, {}).get('free', 0.0)
            if _cash_log == 0.0 and 'ZUSD' in _acct_log:
                _cash_log = _acct_log.get('ZUSD', {}).get('free', 0.0)
        except Exception:
            pass
        _portfolio_heat_pct = None
        if current_equity and current_equity > 0:
            _portfolio_heat_pct = (current_equity - _cash_log) / current_equity * 100

        # Signal stats accumulated during entry scan (populated below)
        _cycle_signals = []  # {symbol, strategy, signal, score, regime}

        # ─── CANCEL STALE OPEN ORDERS (limit buys/sells that didn't fill) ───
        # Prevents locked-up cash from unfilled limit orders
        try:
            _open_orders = self.execution_handler.exchange.fetch_open_orders()
            if _open_orders:
                import time as _time
                for _oo in _open_orders:
                    _oo_age_min = 0
                    if _oo.get('datetime'):
                        _oo_age_min = (pd.Timestamp.now(tz='UTC') - pd.Timestamp(_oo['datetime'])).total_seconds() / 60
                    # Cancel any order older than 5 minutes (one full cycle)
                    if _oo_age_min > 5:
                        print(colored(f"  [STALE ORDER] Cancelling {_oo['side']} {_oo['symbol']} ({_oo_age_min:.0f}min old)", "yellow"))
                        try:
                            self.execution_handler.exchange.cancel_order(_oo['id'])
                        except Exception:
                            pass
        except Exception as _e:
            print(f"  Warning: could not check open orders: {_e}")

        print(colored("--- Checking Portfolio for Exits ---", "cyan"))
        for asset, qty in positions.items():
            if asset in fiat or qty < 0.0001: continue

            symbol = f"{asset}/{Config.QUOTE_CURRENCY}"

            # Fetch bars
            bars = self.data_handler.get_historical_data(symbol, timeframe=Config.TIMEFRAME, limit=60)
            if bars.empty: continue

            current_price = bars['close'].iloc[-1]

            # ─── DUST FILTER: skip positions worth less than $1 ───
            if qty * current_price < Config.MIN_POSITION_VALUE_USD:
                print(f"  > {symbol}: dust position (${qty * current_price:.4f} < ${Config.MIN_POSITION_VALUE_USD}). Ignoring.")
                continue

            entry_price = self.execution_handler.get_entry_price(symbol)
            
            if entry_price:
                roi = (current_price - entry_price) / entry_price
                print(f"  > {symbol}: PnL {roi*100:.2f}% | Entry: {entry_price:.5f} | Curr: {current_price:.5f}")

                # ─── Track peak price every cycle ───
                self._update_peak_price(symbol, current_price)

                # 1. HARD STOP LOSS (Volatility Guard)
                if roi < -Config.STOP_LOSS:
                     print(colored(f"  [STOP LOSS] {symbol} hit -{Config.STOP_LOSS*100}%. CUTTING LOSS.", "red", attrs=['bold']))
                     if audit_logger:
                         _et_sl = self.execution_handler.get_entry_time(symbol)
                         _hh_sl = None
                         try:
                             if _et_sl and hasattr(_et_sl, 'timestamp'):
                                 _hh_sl = (pd.Timestamp.now() - _et_sl).total_seconds() / 3600
                         except Exception:
                             pass
                         _peak_sl = self._get_peak_price(symbol)
                         _strat_sl = self.execution_handler.get_origin_strategy(symbol)
                         _ind_exit = self._quick_indicators(bars)
                         audit_logger.log_trade_exit(
                             symbol=symbol, exit_reason='stop_loss',
                             exit_price=current_price, entry_price=entry_price,
                             pnl_pct=round(roi * 100, 4),
                             pnl_usd=round(qty * (current_price - entry_price), 6),
                             hold_duration_hours=_hh_sl,
                             regime=regime if 'regime' in dir() else None,
                             btc_trend=global_trend,
                             exit_detail=f"Price {current_price:.5f} fell below entry {entry_price:.5f} by {abs(roi)*100:.2f}% (threshold {Config.STOP_LOSS*100}%)",
                             indicators_at_exit=_ind_exit,
                             peak_price=_peak_sl,
                             strategy=_strat_sl,
                             submitted_exit_price=current_price,
                         )
                     self.execution_handler.submit_order(symbol, qty, 'sell', order_type='market', is_strategy_exit=True)
                     continue

                # 2. SMART EXIT SYSTEM (replaces old trailing stop + time exit)
                should_sell = False
                exit_reason = None

                entry_time = self.execution_handler.get_entry_time(symbol)
                if entry_time and isinstance(entry_time, str):
                    try:
                        entry_time = pd.to_datetime(entry_time)
                    except:
                        pass
                hold_hours = None
                if entry_time and hasattr(entry_time, 'timestamp'):
                    hold_hours = (pd.Timestamp.now() - entry_time).total_seconds() / 3600

                # A. TAKE PROFIT — bank gains at target
                if roi >= Config.TAKE_PROFIT:
                    print(colored(f"  [TAKE PROFIT] {symbol} hit +{roi*100:.1f}% (Target {Config.TAKE_PROFIT*100}%). Banking.", "green", attrs=['bold']))
                    should_sell = True
                    exit_reason = 'take_profit'

                # B. BREAK-EVEN STOP — use tracked peak price to detect "was up, now fading"
                # If peak ROI was > 1.5% but current ROI drifted back to ~0.5%, sell to protect capital
                _peak_p = self._get_peak_price(symbol)
                if not should_sell and _peak_p and entry_price:
                    peak_roi = (_peak_p - entry_price) / entry_price
                    if peak_roi > 0.015 and roi < 0.005:
                        print(colored(f"  [BREAK-EVEN STOP] {symbol} peaked at +{peak_roi*100:.1f}%, now +{roi*100:.1f}%. Protecting capital.", "green"))
                        should_sell = True
                        exit_reason = 'breakeven_stop'

                # C. TRAILING STOP — tighter: activate at 1.5% ROI, trail at 1.5%
                if not should_sell:
                    recent_high = bars['high'].tail(12).max()
                    drawdown_from_peak = (current_price - recent_high) / recent_high
                    if roi > 0.015 and drawdown_from_peak < -0.015:
                        print(colored(f"  [TRAILING STOP] {symbol} dropped {drawdown_from_peak*100:.1f}% from peak. Banking {roi*100:.1f}%.", "green"))
                        should_sell = True
                        exit_reason = 'trailing_stop'

                # D. TAKE SMALL WINS — if profitable and above min threshold, take it
                if not should_sell and roi >= Config.MIN_PROFIT_THRESHOLD:
                    # In a bear market, take any win above 1%
                    if global_trend == 'BEARISH':
                        print(colored(f"  [BEAR PROFIT TAKE] {symbol} +{roi*100:.1f}% in bear market. Taking it.", "green"))
                        should_sell = True
                        exit_reason = 'bear_profit_take'

                # E. MOMENTUM CHECK — if not profitable after 1h, the thesis is broken.
                # Data: 11 trades held 3-6h had 9% win rate. Trades that work win in <2h.
                # Tier 1: any loss after 1h → cut (catches -0.2% drifters early)
                # Tier 2: still negative after 30min AND losing momentum → cut
                if not should_sell and hold_hours:
                    if hold_hours > 1.0 and roi < 0:
                        print(colored(f"  [MOMENTUM EXIT] {symbol} at {roi*100:.1f}% after {hold_hours:.1f}h. Thesis broken.", "yellow"))
                        should_sell = True
                        exit_reason = 'momentum_exit'
                    elif hold_hours > 0.5 and roi < -0.01:
                        # Down >1% after 30min — strong negative signal
                        print(colored(f"  [EARLY CUT] {symbol} at {roi*100:.1f}% after {hold_hours:.1f}h. Cutting early.", "yellow"))
                        should_sell = True
                        exit_reason = 'early_cut'

                # F. TIME-BASED EXIT (safety net — should rarely trigger now)
                if not should_sell and hold_hours and hold_hours > Config.MAX_HOLD_TIME_HOURS:
                    print(colored(f"  [TIME EXIT] {symbol} held > {Config.MAX_HOLD_TIME_HOURS}h. Closing.", "yellow"))
                    should_sell = True
                    exit_reason = 'time_exit'

                if should_sell:
                    if audit_logger:
                        _hh_exit = hold_hours
                        _peak_exit = self._get_peak_price(symbol)
                        _strat_exit = self.execution_handler.get_origin_strategy(symbol)
                        _ind_exit2 = self._quick_indicators(bars)
                        # Build human-readable exit_detail
                        _hh_str = f"{_hh_exit:.1f}h" if _hh_exit else "?h"
                        _detail = f"{exit_reason}: ROI={roi*100:+.2f}% after {_hh_str}"
                        audit_logger.log_trade_exit(
                            symbol=symbol, exit_reason=exit_reason or 'unknown',
                            exit_price=current_price, entry_price=entry_price,
                            pnl_pct=round(roi * 100, 4),
                            pnl_usd=round(qty * (current_price - entry_price), 6),
                            hold_duration_hours=_hh_exit,
                            regime=regime if 'regime' in dir() else None,
                            btc_trend=global_trend,
                            exit_detail=_detail,
                            indicators_at_exit=_ind_exit2,
                            peak_price=_peak_exit,
                            strategy=_strat_exit,
                            submitted_exit_price=current_price,
                        )
                    # Use limit orders for profitable exits (saves 0.30% fee),
                    # market orders for loss exits (need immediate execution)
                    _exit_order_type = 'limit' if roi > 0 else 'market'
                    self.execution_handler.submit_order(symbol, qty, 'sell', order_type=_exit_order_type, is_strategy_exit=True)
                    continue

            # 3. STRATEGY SPECIFIC EXIT (Regime Aware) - Kept as backup signal
            try:
                regime, _ = self.determine_regime(symbol, bars)
            except:
                regime = "UNKNOWN"
            
            # Try to get the strategy that opened this coin
            strategy_name = self.execution_handler.get_origin_strategy(symbol)
            active_strategy = self.strategies.get(strategy_name) if strategy_name else None
            if active_strategy is None:
                active_strategy = self.strategies.get('MOMENTUM_PB')  # safe fallback
            
            # Check for Strategy Exit Signal if available
            bars['global_trend'] = global_trend
            signal = active_strategy.get_signal(symbol, bars)
            
            if 'SELL' in signal['signal']:
                 print(colored(f"  [STRATEGY EXIT] {symbol} ({strategy_name if strategy_name else 'FALLBACK'}): {signal['signal']}. Selling.", "yellow"))
                 if audit_logger:
                     _et_se = self.execution_handler.get_entry_time(symbol)
                     _ep_se = self.execution_handler.get_entry_price(symbol)
                     _hh_se = None
                     _pnl_pct_se = None
                     _pnl_usd_se = None
                     _curr_se = bars['close'].iloc[-1]
                     try:
                         if _et_se and hasattr(_et_se, 'timestamp'):
                             _hh_se = (pd.Timestamp.now() - _et_se).total_seconds() / 3600
                         if _ep_se:
                             _roi_se = (_curr_se - _ep_se) / _ep_se
                             _pnl_pct_se = round(_roi_se * 100, 4)
                             _pnl_usd_se = round(qty * (_curr_se - _ep_se), 6)
                     except Exception:
                         pass
                     _peak_se = self._get_peak_price(symbol)
                     audit_logger.log_trade_exit(
                         symbol=symbol, exit_reason='strategy_signal',
                         exit_price=_curr_se, entry_price=_ep_se,
                         pnl_pct=_pnl_pct_se, pnl_usd=_pnl_usd_se,
                         hold_duration_hours=_hh_se,
                         regime=regime,
                         btc_trend=global_trend,
                         exit_detail=f"Strategy {strategy_name} emitted {signal['signal']} at price {_curr_se:.5f}",
                         indicators_at_exit=self._quick_indicators(bars),
                         peak_price=_peak_se,
                         strategy=strategy_name,
                         submitted_exit_price=_curr_se,
                     )
                 _se_otype = 'limit' if (_pnl_pct_se and _pnl_pct_se > 0) else 'market'
                 self.execution_handler.submit_order(symbol, qty, 'sell', order_type=_se_otype, is_strategy_exit=True, strategy_name=strategy_name)
                 continue
        
        # 2. SCAN OPPORTUNITIES WITH STRATEGY CONFIRMATION
        import time
        
        # 2A. LOAD TARGETS FROM CONTROL TOWER
        print(colored("  > Reading targets from Control Tower (targets.json)...", "cyan"))
        targets = []
        try:
            with open('data/targets.json', 'r') as f:
                targets = json.load(f)
        except Exception as e:
            print(f"  > No targets found or error reading json: {e}")
            
        if not targets:
            print(colored("  > No targets provided by Control Tower.", "yellow"))
            if audit_logger:
                audit_logger.log_cycle(equity=current_equity, cash=_cash_log,
                    open_positions_count=_open_pos_count, daily_trade_count=self.daily_trade_count,
                    market_regime=global_trend, drawdown_pct=_drawdown_pct,
                    portfolio_heat_pct=_portfolio_heat_pct, daily_fees_paid=self.daily_fee_total)
            return

        # 2B. CHECK DAILY LIMITS BEFORE ENTRIES
        if self.daily_trade_count >= Config.MAX_DAILY_TRADES:
            print(colored(f"  > Daily trade limit reached ({self.daily_trade_count}/{Config.MAX_DAILY_TRADES}). No new entries.", "yellow"))
            return

        # NOTE: No hard bear market gate here. Individual strategies have their own
        # bear filters (MEAN_REV blocks unless RSI<25 in BEARISH, MOMENTUM blocks
        # all BEARISH entries, etc.). A blanket gate caused the bot to sit idle for
        # weeks whenever BTC dipped below SMA20.

        # 3. EXECUTION LOGIC
        # ─── CASH POSITIONS ───
        acct = self.execution_handler.get_account()
        if acct is None:
            print(colored("  [ERROR] Failed to fetch account info. Skipping batch.", "red"))
            return
            
        cash = acct.get(Config.QUOTE_CURRENCY, {}).get('free', 0.0)
        if cash == 0 and 'ZUSD' in acct:
             cash = acct.get('ZUSD', {}).get('free', 0.0)
        
        print(f"  > Cash Available: ${cash:.2f}")

        # ─── Count real (non-dust) active positions ───
        active_positions = 0
        open_assets = set()   # {asset_ticker} of real holdings (for correlation check)
        for a, q in positions.items():
            if a not in fiat and q > 0.0001:
                try:
                    sym = f"{a}/{Config.QUOTE_CURRENCY}"
                    ticker_p = self.execution_handler.exchange.fetch_ticker(sym)['last']
                    if q * ticker_p >= Config.MIN_POSITION_VALUE_USD:
                        active_positions += 1
                        open_assets.add(a)
                except Exception:
                    pass  # If price fetch fails, be conservative and don't count it

        # ─── POSITION SIZING: fixed $15 per trade, slots driven by cash ───
        alloc = Config.FIXED_ALLOCATION_USD          # e.g. $15.00
        usable_cash = cash * 0.90                    # keep 10% as fee/slippage buffer
        cash_slots  = int(usable_cash / alloc)       # how many $15 trades cash can fund
        hard_cap    = Config.MAX_OPEN_POSITIONS - active_positions
        slots_available = max(0, min(hard_cap, cash_slots))
        base_allocation = alloc

        print(f"  > Active Positions: {active_positions} | Cash: ${cash:.2f} | "
              f"Cash slots: {cash_slots} | Hard cap slots: {hard_cap} → {slots_available} open")
        print(f"  > Per-trade allocation: ${base_allocation:.2f} (fixed)")
        print(f"  > Open Assets: {open_assets or 'none'}")

        if slots_available == 0:
            if cash_slots == 0:
                print(colored(f"  > Not enough cash for a ${alloc:.0f} trade (have ${cash:.2f}).", "yellow"))
            else:
                print(colored(f"  > Max positions ({Config.MAX_OPEN_POSITIONS}) reached. Skipping buys.", "yellow"))
            if audit_logger:
                audit_logger.log_cycle(equity=current_equity, cash=_cash_log,
                    open_positions_count=_open_pos_count, daily_trade_count=self.daily_trade_count,
                    market_regime=global_trend, drawdown_pct=_drawdown_pct,
                    portfolio_heat_pct=_portfolio_heat_pct, daily_fees_paid=self.daily_fee_total)
            return

        if cash < alloc:
            print(f"  > Insufficient cash (${cash:.2f} < ${alloc:.0f} minimum).")
            if audit_logger:
                audit_logger.log_cycle(equity=current_equity, cash=_cash_log,
                    open_positions_count=_open_pos_count, daily_trade_count=self.daily_trade_count,
                    market_regime=global_trend, drawdown_pct=_drawdown_pct,
                    portfolio_heat_pct=_portfolio_heat_pct, daily_fees_paid=self.daily_fee_total)
            return

        # ─── DIVERSIFICATION HELPERS ───
        def _group_of(ticker):
            """Return correlation group name for a ticker, or None."""
            for grp, members in Config.CORRELATION_GROUPS.items():
                if ticker.upper() in members:
                    return grp
            return None

        def _is_correlated(new_ticker):
            """True if a coin in the same correlation group is already held."""
            g = _group_of(new_ticker)
            if g is None:
                return False
            for held in open_assets:
                if _group_of(held) == g:
                    return True
            return False

        strategies_entered_this_cycle = set()  # for strategy-diversity preference
        trades_made = 0

        for target in targets:
            if trades_made >= slots_available:
                break

            # Daily limit check (in case we entered some already)
            if self.daily_trade_count >= Config.MAX_DAILY_TRADES:
                print(colored(f"  > Daily trade limit hit. Stopping entries.", "yellow"))
                break

            symbol = target['symbol']
            asset_ticker = symbol.split('/')[0]

            # ─── Already holding (real position, not dust) ───
            if asset_ticker in open_assets:
                print(f"    > Already holding {symbol}. Skipping.")
                continue

            # ─── DUST check on target (shouldn't matter, but guard) ───
            if positions.get(asset_ticker, 0) > 0.0001:
                try:
                    _tp = self.execution_handler.exchange.fetch_ticker(symbol)['last']
                    if positions[asset_ticker] * _tp < Config.MIN_POSITION_VALUE_USD:
                        pass  # treat as empty
                    else:
                        print(f"    > Already holding {symbol}. Skipping.")
                        continue
                except Exception:
                    pass
            
            # ─── STRATEGY CONFIRMATION (REFORM #2) ───
            # Don't buy blindly — run the strategy and only buy if it says BUY
            bars = self.data_handler.get_historical_data(symbol, timeframe=Config.TIMEFRAME, limit=60)
            if bars.empty:
                print(f"    > {symbol}: No data available. Skipping.")
                continue
            
            # Determine regime and pick strategy
            try:
                regime, meta = self.determine_regime(symbol, bars)
            except:
                regime = "UNKNOWN"
            
            # Evaluate all strategies appropriate for this regime
            best_signal = None
            best_score = -1
            
            allowed_strategies = self.regime_strategies.get(regime, self.regime_strategies['UNKNOWN'])
            bars['global_trend'] = global_trend
            # Pass scalar FFT info down specifically instead of a dict
            bars['meta_dom_period'] = meta.get('dom_period')
            
            _all_buy_signals_this_target = []  # track competing signals for TRADE_ENTRY log

            for strat_name in allowed_strategies:
                # Skip strategies disabled by optimizer (consecutive-loss cooldown)
                _opt_p = _strategy_params.get(strat_name, {})
                if not _opt_p.get('enabled', True):
                    print(colored(f"    > {strat_name}: optimizer cooldown active — skipping", "yellow"))
                    continue

                strat = self.strategies[strat_name]
                sig = strat.get_signal(symbol, bars)
                _meta = sig.get('meta', {}) or {}

                # Accumulate cycle-level signal stats
                _cycle_signals.append({
                    'symbol': symbol, 'strategy': strat_name,
                    'signal': sig['signal'], 'score': sig.get('score', 0), 'regime': regime,
                    'skip_reason': _meta.get('needs_for_trigger') if 'BUY' not in sig['signal'] else None,
                })

                if audit_logger:
                    _is_buy = 'BUY' in sig['signal']
                    audit_logger.log_signal(
                        symbol=symbol, strategy=strat_name,
                        signal_type=sig['signal'], score=sig.get('score', 0),
                        regime=regime,
                        skip_reason=None if _is_buy else sig['signal'],
                        indicators=_meta,
                        conditions_checked=_meta.get('conditions_checked'),
                        needs_for_trigger=_meta.get('needs_for_trigger'),
                    )

                if 'BUY' in sig['signal']:
                    # Optimizer min_score gate: only act on high-conviction signals
                    _min_score = _strategy_params.get(strat_name, {}).get('min_score', 60)
                    if sig.get('score', 0) < _min_score:
                        print(f"    > {strat_name}: score {sig.get('score',0)} "
                              f"< min_score {_min_score} (optimizer gate) — skipping")
                        continue
                    _all_buy_signals_this_target.append({
                        'strategy': strat_name, 'signal': sig['signal'],
                        'score': sig.get('score', 0), 'regime': regime,
                    })
                    if sig['score'] > best_score:
                        best_score = sig['score']
                        best_signal = sig

            if not best_signal or 'BUY' not in best_signal['signal']:
                print(f"    > {symbol}: No strategies triggered a BUY ({regime} regime). SKIPPING.")
                if audit_logger:
                    all_reasons = [s['skip_reason'] for s in _cycle_signals
                                   if s['symbol'] == symbol and s['skip_reason']]
                    audit_logger.log_signal(
                        symbol=symbol, strategy='ENGINE',
                        signal_type='HOLD', score=0, regime=regime,
                        skip_reason=f'no_strategy_buy_in_{regime}_regime',
                        needs_for_trigger=f"All {len(allowed_strategies)} strategies declined: " +
                                          "; ".join(all_reasons[:3]),
                    )
                continue

            # ─── CORRELATION FILTER ───
            if _is_correlated(asset_ticker):
                grp = _group_of(asset_ticker)
                held_peer = next((h for h in open_assets if _group_of(h) == grp), '?')
                print(colored(f"    > {symbol}: skipped — correlated with {held_peer} (group: {grp})", "yellow"))
                continue

            # ─── STRATEGY DIVERSITY PREFERENCE ───
            winning_strategy = best_signal['strategy']
            if winning_strategy in strategies_entered_this_cycle:
                # Check if any runner-up uses a different strategy
                alt = next(
                    (s for s in _all_buy_signals_this_target
                     if s['strategy'] not in strategies_entered_this_cycle),
                    None
                )
                if alt:
                    print(colored(f"    > {symbol}: preferring {alt['strategy']} (score {alt['score']}) over duplicate {winning_strategy}", "cyan"))
                    # Swap in the alt signal (score is lower but brings diversity)
                    best_signal = next(
                        s for s in [strat.get_signal(symbol, bars)
                                    for strat in [self.strategies[alt['strategy']]]]
                        if True
                    )
                    winning_strategy = alt['strategy']
                else:
                    print(colored(f"    > {symbol}: all signals use {winning_strategy} (already entered this cycle). Proceeding.", "cyan"))

            # Competing signals = other strategies that also said BUY (runner-ups)
            _competing = [s for s in _all_buy_signals_this_target
                          if s['strategy'] != best_signal.get('strategy')]
            
            # ─── ENTRY QUALITY GATE (data-driven filters) ───
            # Winners buy deep dips (avg -0.9% below SMA20).
            # Losers buy near the SMA (avg -0.3%). Filter weak entries.
            _eq_price = bars['close'].iloc[-1]
            _eq_sma20 = bars['close'].rolling(window=20).mean().iloc[-1]
            _eq_dip = (_eq_sma20 - _eq_price) / _eq_sma20 if _eq_sma20 else 0  # positive = below SMA
            _eq_green = bars['close'].iloc[-1] > bars['open'].iloc[-1]  # current candle is green
            _eq_vol_avg = bars['volume'].rolling(window=20).mean().iloc[-1] if 'volume' in bars else 1
            _eq_vol_cur = bars['volume'].iloc[-1] if 'volume' in bars else 1
            _eq_vol_ratio = _eq_vol_cur / _eq_vol_avg if _eq_vol_avg > 0 else 0

            # 1. Minimum dip depth (adaptive to market conditions)
            _min_dip = _market_gauge['min_dip_pct']
            if _eq_dip < _min_dip:
                print(f"    > {symbol}: dip {_eq_dip*100:.2f}% < min {_min_dip*100:.1f}% (too close to SMA). SKIP.")
                continue

            # 2. Green candle confirmation in bear markets
            if _market_gauge['require_green'] and not _eq_green:
                print(f"    > {symbol}: bear market requires green candle confirmation. SKIP.")
                continue

            # 3. Minimum volume floor (no dead markets)
            if _eq_vol_ratio < 0.3:
                print(f"    > {symbol}: vol ratio {_eq_vol_ratio:.2f} < 0.3 (dead volume). SKIP.")
                continue

            print(colored(f"  >>> CONFIRMED BUY: {symbol} | Strategy: {best_signal['strategy']} ({best_signal['signal']}) | Score: {best_signal['score']} | Regime: {regime} | Dip: {_eq_dip*100:.1f}% | Vol: {_eq_vol_ratio:.1f}x", "green", attrs=['bold']))

            price = self.data_handler.get_latest_price(symbol)
            if not price: continue

            # Fixed allocation — always exactly FIXED_ALLOCATION_USD ($15).
            # ATR scaling was removed: it could reduce below Kraken's $15 minimum,
            # silently blocking every trade. Log ATR for audit purposes only.
            try:
                atr_series = (bars['high'] - bars['low']).tail(14)
                atr = atr_series.mean()
                atr_scale = 1.0  # kept for audit log compatibility
            except Exception:
                atr = 0.0
                atr_scale = 1.0

            allocation_per_trade = base_allocation  # always Config.FIXED_ALLOCATION_USD
            print(f"    > {symbol}: allocation=${allocation_per_trade:.2f} (fixed)")

            qty = allocation_per_trade / price
            
            # ─── PAPER TRADING MODE ───
            if Config.PAPER_TRADING:
                print(colored(f"  [PAPER] Would BUY {qty:.4f} {symbol} @ ${price:.6f} (~${allocation_per_trade:.2f}) via {Config.DEFAULT_ORDER_TYPE}", "magenta"))
                self.execution_handler.submit_order(
                    symbol, qty, 'buy', order_type=Config.DEFAULT_ORDER_TYPE, price=price, strategy_name=best_signal['strategy']
                )
                trades_made += 1
                self.daily_trade_count += 1
                cash -= allocation_per_trade
                continue
            
            # ─── LIVE ORDER (Limit by default) ───
            order = self.execution_handler.submit_order(
                symbol, qty, 'buy', order_type=Config.DEFAULT_ORDER_TYPE, price=price, strategy_name=best_signal['strategy']
            )
            
            if order:
                if audit_logger:
                    _ep_entry = order.get('average') or order.get('price') or price
                    _sig_meta = best_signal.get('meta', {}) or {}
                    audit_logger.log_trade_entry(
                        symbol=symbol, strategy=best_signal['strategy'],
                        entry_price=_ep_entry,
                        submitted_price=price,
                        quantity=qty,
                        allocated_usd=allocation_per_trade,
                        stop_loss_price=round(_ep_entry * (1 - Config.STOP_LOSS), 6) if _ep_entry else None,
                        take_profit_price=round(_ep_entry * (1 + Config.TAKE_PROFIT), 6) if _ep_entry else None,
                        regime=regime,
                        btc_trend=global_trend,
                        signal_score=best_signal.get('score'),
                        trigger_condition=_sig_meta.get('trigger_condition'),
                        indicators=_sig_meta,
                        atr_value=atr,
                        atr_scale=atr_scale,
                        competing_signals=_competing,
                    )
                trades_made += 1
                self.daily_trade_count += 1
                cash -= allocation_per_trade
                # Track for intra-cycle diversification
                open_assets.add(asset_ticker)
                strategies_entered_this_cycle.add(best_signal['strategy'])

        # ─── AUDIT: CYCLE SUMMARY (logged at end so signal stats are complete) ───
        if audit_logger:
            _n_eval = len(_cycle_signals)
            _n_buy  = sum(1 for s in _cycle_signals if 'BUY' in s['signal'])
            _n_skip = _n_eval - _n_buy
            _top_skips = sorted(
                [s for s in _cycle_signals if 'BUY' not in s['signal'] and s['score'] > 0],
                key=lambda x: x['score'], reverse=True
            )[:3]
            audit_logger.log_cycle(
                equity=current_equity,
                cash=_cash_log,
                open_positions_count=_open_pos_count,
                daily_trade_count=self.daily_trade_count,
                market_regime=global_trend,
                drawdown_pct=_drawdown_pct,
                signals_evaluated=_n_eval,
                signals_skipped=_n_skip,
                signals_triggered=_n_buy,
                top_skipped_signals=_top_skips,
                portfolio_heat_pct=_portfolio_heat_pct,
                daily_fees_paid=self.daily_fee_total,
            )
