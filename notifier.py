"""
Telegram notification module for Quant Bot.

Setup:
  1. Create a bot via @BotFather → copy the token
  2. Message your bot once, then visit:
     https://api.telegram.org/bot<TOKEN>/getUpdates
     to find your chat_id
  3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Railway env vars

All functions are fire-and-forget — they never raise or crash the bot.
"""
import os
import datetime

try:
    import requests as _requests
    _ok = True
except ImportError:
    _ok = False

_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '')
_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')


def _send(text: str) -> None:
    if not _TOKEN or not _CHAT_ID or not _ok:
        return
    try:
        _requests.post(
            f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
            json={'chat_id': _CHAT_ID, 'text': text, 'parse_mode': 'HTML'},
            timeout=10,
        )
    except Exception:
        pass


def notify_startup(equity: float, mode: str) -> None:
    _send(
        f"🤖 <b>Quant Bot Online</b>\n"
        f"Mode: {mode}\n"
        f"Equity: <b>${equity:,.2f}</b>\n"
        f"Time: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )


def notify_trade_entry(symbol: str, strategy: str, price: float,
                       allocation: float, regime: str = '') -> None:
    _send(
        f"📈 <b>BUY</b>  {symbol}\n"
        f"Strategy: {strategy}\n"
        f"Price: ${price:,.4f}   Alloc: ${allocation:,.2f}\n"
        f"Regime: {regime or '—'}"
    )


def notify_trade_exit(symbol: str, pnl_pct: float, exit_reason: str,
                      hold_hours: float = 0, pnl_usd: float = 0) -> None:
    emoji = '✅' if pnl_pct >= 0 else '❌'
    sign  = '+' if pnl_pct >= 0 else ''
    _send(
        f"{emoji} <b>SELL</b>  {symbol}\n"
        f"P&L: {sign}{pnl_pct:.2f}%  ({sign}${pnl_usd:.2f})\n"
        f"Reason: {exit_reason}\n"
        f"Held: {hold_hours:.1f}h"
    )


def notify_daily_summary(equity: float, starting_equity: float,
                          open_positions: list, day_trades: int) -> None:
    """
    open_positions: list of dicts with keys 'symbol' and 'pnl_pct'
    """
    pnl     = equity - starting_equity
    pnl_pct = (pnl / starting_equity * 100) if starting_equity else 0
    sign    = '+' if pnl >= 0 else ''
    emoji   = '📈' if pnl >= 0 else '📉'

    pos_lines = '\n'.join(
        f"  • {p['symbol']}: {p.get('pnl_pct', 0):+.2f}%"
        for p in open_positions
    ) if open_positions else '  None'

    _send(
        f"{emoji} <b>Daily Summary</b> — "
        f"{datetime.datetime.utcnow().strftime('%Y-%m-%d')}\n"
        f"Equity:  <b>${equity:,.2f}</b>  ({sign}{pnl_pct:.2f}%)\n"
        f"Day P&L: {sign}${pnl:.2f}\n"
        f"Trades:  {day_trades}\n"
        f"Open:\n{pos_lines}"
    )


def notify_kill_switch(equity: float, starting_equity: float,
                        dd_pct: float) -> None:
    _send(
        f"🚨 <b>KILL SWITCH TRIGGERED</b>\n"
        f"Drawdown: -{dd_pct:.1f}%\n"
        f"Equity: ${equity:,.2f}  (started ${starting_equity:,.2f})\n"
        f"All positions liquidated."
    )


def notify_error(context: str, error: str) -> None:
    _send(f"⚠️ <b>Bot Error</b>\n{context}\n<code>{error[:300]}</code>")
