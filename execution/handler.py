import ccxt
import pandas as pd
from termcolor import colored
try:
    import audit_logger
except ImportError:
    audit_logger = None
try:
    import notifier
except ImportError:
    notifier = None

class ExecutionHandler:
    def __init__(self, exchange: ccxt.Exchange):
        self.exchange = exchange

    def _is_filled(self, order):
        status = (order or {}).get('status')
        remaining = order.get('remaining') if order else None
        filled = order.get('filled') if order else None
        amount = order.get('amount') if order else None
        if status in ('closed', 'filled'):
            return True
        try:
            if remaining is not None and float(remaining) <= 0:
                return True
            if filled is not None and amount is not None and float(amount) > 0:
                return float(filled) >= float(amount) * 0.999
        except Exception:
            pass
        return False

    def _refresh_order(self, order):
        """Best-effort fetch of the current Kraken order status."""
        if not order or not order.get('id'):
            return order
        try:
            return self.exchange.fetch_order(order['id'], order.get('symbol')) or order
        except Exception:
            return order

    def _order_fee_usd(self, order, fallback_qty=0.0, fallback_price=0.0):
        fee_total = 0.0
        for trade in (order or {}).get('trades') or []:
            fee = trade.get('fee') or {}
            try:
                fee_total += float(fee.get('cost') or 0)
            except Exception:
                pass
        if fee_total:
            return fee_total

        fee = (order or {}).get('fee') or {}
        try:
            return float(fee.get('cost') or 0)
        except Exception:
            pass

        from config import Config
        order_type = (order or {}).get('type')
        rate = Config.TAKER_FEE_RATE if order_type == 'market' else Config.FEE_RATE
        return float(fallback_qty or 0) * float(fallback_price or 0) * rate

    def get_account(self):
        """
        Returns the balance dictionary. 
        Note: CCXT fetch_balance structure is different from Alpaca.
        We return the 'total' (free + used) balance for simplicity in Reporting.
        """
        try:
            balance = self.exchange.fetch_balance()
            return balance
        except Exception as e:
            print(colored(f"Error fetching account info: {e}", "red"))
            return None

    
    def liquidate_all(self):
        """
        Emergency: Sells EVERYTHING (non-USD) to free up cash.
        """
        print(colored("🚨 LIQUIDATING ENTIRE PORTFOLIO 🚨", "red", attrs=['bold', 'blink']))
        positions = self.get_positions()
        
        fiat = ['USD', 'ZUSD', 'KFEE'] # Keep USD
        
        for asset, qty in positions.items():
            if asset in fiat or qty < 0.0001:
                continue
            
            # Try to find pair
            symbol = f"{asset}/USD"
            if symbol not in self.exchange.markets:
                print(f"  > Skipping {asset} (No USD pair found)")
                continue
                
            print(f"  > Selling {qty} {asset}...")
            # FORCE SELL (Market order for Zero Latency)
            self.submit_order(symbol, qty, 'sell', order_type='market', is_strategy_exit=True)
        
        print(colored("--- Liquidation Complete ---", "cyan"))
    
    def liquidate_profitable_positions(self, min_profit_pct=0.02): # 2.0%: Covers fees + profit buffer.
        """
        Scans all current holdings. If any are profitable (net of fees), sell them immediately.
        """
        print(colored("--- Checking for Profitable Positions to Liquidate ---", "cyan"))
        positions = self.get_positions()
        
        # Filter for crypto assets (ignore USD/CAD/EUR)
        fiat = ['USD', 'CAD', 'EUR', 'USDT', 'USDC', 'ZUSD', 'ZCAD', 'KFEE']
        
        for asset, qty in positions.items():
            if asset in fiat or qty < 0.0001:
                continue
            
            # Try to find the pair
            from config import Config
            symbol = f"{asset}/{Config.QUOTE_CURRENCY}"
            if symbol not in self.exchange.markets:
                symbol = f"{asset}/USD" # strict fallback
                if symbol not in self.exchange.markets:
                    continue # Skip if no obvious pair
            
            try:
                # Check Profitability
                trades = self.exchange.fetch_my_trades(symbol, limit=30)
                last_buy_price = 0
                for t in reversed(trades):
                    if t['side'] == 'buy' and t['symbol'] == symbol:
                        last_buy_price = t['price']
                        break
                
                if last_buy_price == 0:
                    print(f"  > {asset}: No recent buy price found.")
                    continue
                    
                ticker = self.exchange.fetch_ticker(symbol)
                current_price = ticker['last']
                
                roi = (current_price - last_buy_price) / last_buy_price
                
                if roi > min_profit_pct:
                    print(colored(f"  > [SWEEP] Found Profitable Position: {symbol} (+{roi*100:.2f}%). SELLING NOW.", "green"))
                    # Bypass the Profit Guard check inside submit_order strictly for this sweep?
                    # No, submit_order checks it anyway, but we pass 'market' so it should work.
                    self.submit_order(symbol, qty, 'sell', is_strategy_exit=True)
                else:
                    print(f"  > {symbol} is currently {roi*100:.2f}% (Target: >{min_profit_pct*100:.2f}%). Holding.")
                    
            except Exception as e:
                print(f"Error checking {asset}: {e}")
        
        print(colored("--- Sweep Complete ---\n", "cyan"))
            
    def get_positions(self):
        """
        For Crypto Spot, 'positions' are just non-zero balances in the wallet.
        """
        try:
            balance = self.exchange.fetch_balance()
            # Filter for assets with > 0 total balance
            if 'total' in balance:
                positions = {k: v for k, v in balance['total'].items() if v > 0}
                return positions
            return {}
        except Exception as e:
            print(colored(f"Error fetching positions: {e}", "red"))
            return {}

    def submit_order(self, symbol, qty, side, order_type=None, price=None, is_strategy_exit=False, strategy_name=None):
        """
        Submits an order to Kraken.
        Default: LIMIT Order for lower fees (Reform #3).
        """
        from config import Config
        
        if order_type is None:
            order_type = Config.DEFAULT_ORDER_TYPE  # 'limit' by default
        
        if Config.PAPER_TRADING:
            print(colored(f"  [PAPER] Would {side.upper()} {qty:.6f} {symbol} via {order_type}", "magenta"))
            # Generate mock order object
            import time
            mock_order = {'id': f'paper_{int(time.time()*1000)}', 'symbol': symbol, 'side': side, 'amount': qty, 'price': price, 'timestamp': int(time.time()*1000)}
            self._update_local_state(mock_order, strategy_name=strategy_name)
            return mock_order
        
        if side == 'sell' and not is_strategy_exit:
             # REFORMED PROFIT GUARD (Reform #4)
             # Old: blocked sells between -2% and +0.55% → created bag-holders
             # New: only hold if very close to breakeven, cut losses early
             try:
                 ticker = self.exchange.fetch_ticker(symbol)
                 current_price = ticker['last']
                 
                 trades = self.exchange.fetch_my_trades(symbol, limit=20)
                 last_buy_price = 0
                 for t in reversed(trades):
                     if t['side'] == 'buy' and t['symbol'] == symbol:
                         last_buy_price = t['price']
                         break
                
                 if last_buy_price > 0:
                     roi = (current_price - last_buy_price) / last_buy_price
                     
                     # REFORMED LOGIC: Stop holding bags.
                     # If the bot is actively trying to sell (e.g., from a trailing stop constraint or strategy exit),
                     # we should let it sell regardless of the current ROI. 
                     # The only reason to block a sell is if the ROI is exactly within the fee margin (e.g. 0% to +0.6%) 
                     # where selling would realize a tiny pointless net loss due to fees, BUT only if we aren't crashing.
                     # In a fast breakout strategy, we almost NEVER want to block a sell.
                     pass
             except Exception as e:
                 print(f"Profit guard warning: {e}")

        # MAX POSITION CHECK
        if side == 'buy':
             current_positions = self.get_positions()
             # Filter out dust/fiat
             active_count = 0
             fiat = ['USD', 'CAD', 'EUR', 'USDT', 'USDC', 'ZUSD', 'ZCAD', 'KFEE', 'GBP']
             for a, q in current_positions.items():
                 if a not in fiat and q > 0.0001:
                     active_count += 1
            
        # GUARD: If this is a Strategy Exit (Sell), CANCEL any existing open orders for this symbol first
        # to ensure funds are not locked.
        # GUARD: If this is a Strategy Exit (Sell), CANCEL any existing open orders for this symbol first
        # to ensure funds are not locked.
        if is_strategy_exit and side == 'sell':
             try:
                 # Fetch ALL open orders to strictly avoid symbol matching issues
                 all_open_orders = self.exchange.fetch_open_orders()
                 # Filter manually
                 orders_to_cancel = [o for o in all_open_orders if o['symbol'] == symbol]
                 
                 if orders_to_cancel:
                     print(colored(f"  [EXECUTION] Found {len(orders_to_cancel)} open orders for {symbol}. Cancelling...", "yellow"))
                     for o in orders_to_cancel:
                         try:
                             self.exchange.cancel_order(o['id'])
                         except:
                             pass
                     
                     # 1.5s Sleep to allow Exchange to process cancellation and unlock funds
                     import time
                     time.sleep(1.5)
                     
             except Exception as e:
                 print(f"Error cancelling open orders: {e}")

        # Proceed with Order
        try:
            if order_type == 'limit':
                # Always fetch a fresh quote and use bid/ask to guarantee maker pricing.
                # Previously the engine passed ticker['last'] which could be at or above ask,
                # causing the "limit" order to immediately take liquidity (taker fee).
                ticker = self.exchange.fetch_ticker(symbol)
                if side == 'buy':
                    price = ticker['bid']   # Post below the spread → maker fee (0.16%)
                elif side == 'sell' and price is None:
                    price = ticker['ask']   # Post above the spread → maker fee on exits too

            print(f"Submitting {side.upper()} {order_type} order for {qty} {symbol} @ {price if price else 'MARKET'}...")

            # For exits: capture entry data before state is wiped
            _entry_price = None
            _entry_time_ms = None
            if side == 'sell' and notifier:
                try:
                    import json, os
                    pos_file = 'data/positions.json'
                    if os.path.exists(pos_file):
                        with open(pos_file) as _f:
                            _pd = json.load(_f)
                        _entry_price = _pd.get(symbol, {}).get('entry_price')
                        _entry_time_ms = _pd.get(symbol, {}).get('entry_time')
                except Exception:
                    pass

            order = self.exchange.create_order(symbol, order_type, side, qty, price)
            order = self._refresh_order(order)
            if not self._is_filled(order) and (order_type == 'market' or side == 'sell'):
                import time
                for _ in range(3):
                    time.sleep(1.0)
                    order = self._refresh_order(order)
                    if self._is_filled(order):
                        break
            filled = self._is_filled(order)
            if filled:
                print(colored(f"Order filled: {order['id']}", "green"))
            else:
                print(colored(f"Order submitted but not filled yet: {order['id']} ({order.get('status', 'open')})", "yellow"))

            # Fire-and-forget Telegram alerts
            if notifier and filled:
                try:
                    exec_price = order.get('average') or order.get('price') or price or 0
                    if side == 'buy':
                        notifier.notify_trade_entry(
                            symbol=symbol,
                            strategy=strategy_name or '—',
                            price=exec_price,
                            allocation=qty * exec_price,
                        )
                    elif side == 'sell':
                        pnl_pct = 0.0
                        pnl_usd = 0.0
                        hold_h  = 0.0
                        if _entry_price and exec_price:
                            pnl_pct = (exec_price - _entry_price) / _entry_price * 100
                            pnl_usd = (exec_price - _entry_price) * qty
                        if _entry_time_ms:
                            import time as _time
                            hold_h = (_time.time() * 1000 - _entry_time_ms) / 3_600_000
                        notifier.notify_trade_exit(
                            symbol=symbol,
                            pnl_pct=pnl_pct,
                            exit_reason=f"{'strategy_exit' if is_strategy_exit else 'sweep'}",
                            hold_hours=hold_h,
                            pnl_usd=pnl_usd,
                        )
                except Exception:
                    pass

            # Record local state only after a verified fill. Open limit orders are
            # reconciled on later cycles by live balances, not optimistic intent.
            if filled:
                if not order.get('price'): order['price'] = price
                self._update_local_state(order, strategy_name=strategy_name)
                if audit_logger:
                    audit_logger.record_fee(self._order_fee_usd(
                        order,
                        fallback_qty=order.get('filled') or qty,
                        fallback_price=order.get('average') or order.get('price') or price,
                    ))

            return order
        except ccxt.InvalidOrder as e:
            print(colored(f"Order rejected (Invalid/Minimum Not Met): {e}", "red"))
            if audit_logger:
                audit_logger.log_rejected_order(symbol, side, f"InvalidOrder: {e}")
            # If we're trying to sell dust and it fails, just ignore and move on
            if "minimum not met" in str(e).lower() and side == 'sell':
                 print(colored(f"  > Skipping {symbol} (Dust / below minimum order size).", "yellow"))
            return None
        except ccxt.ExchangeError as e:
            print(colored(f"Order rejected (Exchange Error/Restricted): {e}", "red"))
            if audit_logger:
                audit_logger.log_rejected_order(symbol, side, f"ExchangeError: {e}")
            # Auto-blacklist CA:ON restricted pairs so they never appear in targets again
            _err = str(e).lower()
            if 'eaccount:invalid permissions' in _err or 'trading restricted for ca:on' in _err:
                import json, os
                _ticker = symbol.split('/')[0]
                _bl_file = 'data/restricted_pairs.json'
                os.makedirs('data', exist_ok=True)
                _bl = []
                if os.path.exists(_bl_file):
                    try:
                        with open(_bl_file) as _f:
                            _bl = json.load(_f)
                    except Exception:
                        pass
                if _ticker not in _bl:
                    _bl.append(_ticker)
                    with open(_bl_file, 'w') as _f:
                        json.dump(_bl, _f, indent=2)
                    print(colored(f"  [BLACKLIST] {_ticker} added to data/restricted_pairs.json (CA:ON restricted).", "yellow"))
            return None
        except Exception as e:
            print(colored(f"Order rejected (Unknown Error): {e}", "red"))
            if audit_logger:
                audit_logger.log_rejected_order(symbol, side, f"UnknownError: {e}")
            return None

    def _update_local_state(self, order, strategy_name=None):
        import json, os, time
        os.makedirs('data', exist_ok=True)
        pos_file = 'data/positions.json'

        symbol = order['symbol']
        side = order['side']

        pos_data = {}
        if os.path.exists(pos_file):
            try:
                with open(pos_file, 'r') as f:
                    pos_data = json.load(f)
            except: pass

        if side == 'buy':
            fill_price = order.get('average') or order.get('price')

            # Never write null for entry_price — fall back to live ticker if needed.
            # A null entry_price disables ALL stop-loss and take-profit logic.
            if not fill_price:
                try:
                    ticker = self.exchange.fetch_ticker(symbol)
                    fill_price = ticker['last']
                    print(f"  [STATE] No fill price in order; using live ticker for {symbol}: {fill_price}")
                except Exception as e:
                    print(f"  [STATE] Warning: could not resolve entry_price for {symbol}: {e}")

            pos_data[symbol] = {
                'entry_price': fill_price,
                'entry_time': order.get('timestamp') or int(time.time()*1000),
                'strategy': strategy_name
            }
        elif side == 'sell':
            if symbol in pos_data:
                del pos_data[symbol]

        try:
            with open(pos_file, 'w') as f:
                json.dump(pos_data, f, indent=2)
        except Exception as e:
            print(f"Failed to write local state: {e}")

    def get_entry_price(self, symbol):
        """
        Calculates the average entry price for a current position.
        Uses local state first, falls back to API.
        """
        import json, os
        pos_file = 'data/positions.json'
        if os.path.exists(pos_file):
            try:
                with open(pos_file, 'r') as f:
                    pos_data = json.load(f)
                if symbol in pos_data and pos_data[symbol].get('entry_price'):
                    return pos_data[symbol]['entry_price']
            except: pass
            
        try:
            trades = self.exchange.fetch_my_trades(symbol, limit=50)
            
            # Simple Last Buy Price Proxy
            last_buy_price = 0
            for t in reversed(trades):
                if t['side'] == 'buy' and t['symbol'] == symbol:
                    last_buy_price = t['price']
                    break
            return last_buy_price
        except Exception:
            return None

    def get_entry_time(self, symbol):
        """
        Returns the timestamp of the last BUY for this symbol.
        Used for Time-Based Exits.
        """
        import json, os, pandas as pd
        pos_file = 'data/positions.json'
        if os.path.exists(pos_file):
            try:
                with open(pos_file, 'r') as f:
                    pos_data = json.load(f)
                if symbol in pos_data and pos_data[symbol].get('entry_time'):
                    return pd.to_datetime(pos_data[symbol]['entry_time'], unit='ms')
            except: pass
            
        try:
            trades = self.exchange.fetch_my_trades(symbol, limit=50)
            
            last_buy_time = None
            for t in reversed(trades):
                if t['side'] == 'buy' and t['symbol'] == symbol:
                    last_buy_time = pd.to_datetime(t['timestamp'], unit='ms')
                    break
            return last_buy_time
        except Exception:
            return None

    def get_origin_strategy(self, symbol):
        """
        Retrieves the name of the strategy that opened this position from local state.
        """
        import json, os
        pos_file = 'data/positions.json'
        if os.path.exists(pos_file):
            try:
                with open(pos_file, 'r') as f:
                    pos_data = json.load(f)
                return pos_data.get(symbol, {}).get('strategy')
            except: pass
        return None
