#!/usr/bin/env python3
"""
Verified fill P&L audit for Kraken trades.

This intentionally ignores the bot's audit_log.json because that file can contain
submitted-but-unfilled orders and rejected exits. P&L here is reconstructed from
Kraken fills only, using FIFO lots and net fees.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import argparse
import csv
import json
import os
from pathlib import Path
import time
from typing import Any

import ccxt
from dotenv import load_dotenv


USD_LIKE = {"USD", "ZUSD", "USDT", "USDC", "KFEE"}
OUT_DIR = Path("data")


@dataclass
class Lot:
    qty: float
    cost_usd: float
    opened_at: str
    buy_trade_id: str | None


def fee_cost(trade: dict[str, Any]) -> tuple[float, str | None]:
    fee = trade.get("fee") or {}
    try:
        return float(fee.get("cost") or 0.0), fee.get("currency")
    except Exception:
        return 0.0, fee.get("currency")


def parse_symbol(symbol: str) -> tuple[str, str]:
    if "/" not in symbol:
        return symbol, "USD"
    base, quote = symbol.split("/", 1)
    return base, quote


def quote_fee_usd(trade: dict[str, Any], base: str, quote: str) -> float:
    cost, currency = fee_cost(trade)
    if not cost:
        return 0.0
    if currency in USD_LIKE or currency in {quote, f"Z{quote}"}:
        return cost
    if currency == base:
        return cost * float(trade["price"])
    return 0.0


def effective_qty(trade: dict[str, Any], base: str) -> float:
    qty = float(trade["amount"])
    fee, currency = fee_cost(trade)
    if trade["side"] == "buy" and currency == base:
        return max(0.0, qty - fee)
    return qty


def dedupe_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for t in trades:
        key = (
            t.get("id"),
            t.get("order"),
            t.get("timestamp"),
            t.get("symbol"),
            t.get("side"),
            t.get("amount"),
            t.get("price"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    out.sort(key=lambda x: (x.get("timestamp") or 0, str(x.get("id") or "")))
    return out


def fetch_my_trades_page(
    exchange: ccxt.Exchange,
    symbol: str | None,
    since_ms: int | None,
    page_size: int,
    offset: int,
    retries: int = 6,
) -> list[dict[str, Any]]:
    for attempt in range(retries):
        try:
            batch = exchange.fetch_my_trades(symbol, since=since_ms, limit=page_size, params={"ofs": offset})
            time.sleep(1.25)
            return batch
        except Exception as exc:
            msg = str(exc).lower()
            if "rate limit" in msg or "invalid nonce" in msg:
                delay = min(45, 4 * (attempt + 1))
                label = symbol or "ALL"
                print(f"  {label}: Kraken throttled request at offset {offset}; sleeping {delay}s", flush=True)
                time.sleep(delay)
                continue
            raise
    raise RuntimeError(f"Kraken fetch_my_trades repeatedly throttled for {symbol or 'ALL'} at offset {offset}")


def fetch_all_trades(exchange: ccxt.Exchange, since_ms: int | None, max_pages: int) -> list[dict[str, Any]]:
    """Fetch all available fills. Prefer all-symbol pagination, then per-symbol fallback."""
    trades: list[dict[str, Any]] = []
    page_size = 50

    try:
        offset = 0
        seen_ids = set()
        pages = 0
        while pages < max_pages:
            batch = fetch_my_trades_page(exchange, None, since_ms, page_size, offset)
            pages += 1
            if not batch:
                break
            before = len(seen_ids)
            for trade in batch:
                seen_ids.add(trade.get("id") or (
                    trade.get("timestamp"), trade.get("symbol"), trade.get("side"),
                    trade.get("amount"), trade.get("price"),
                ))
            trades.extend(batch)
            print(f"  page {pages}: fetched {len(trades)} fills via all-symbol trades", flush=True)
            if len(batch) < page_size or len(seen_ids) == before:
                break
            offset += len(batch)
        if trades:
            print()
            return dedupe_trades(trades)
    except Exception as exc:
        if trades:
            print(f"  all-symbol pagination stopped after {len(trades)} raw fills ({exc})")
            return dedupe_trades(trades)
        print(f"  all-symbol fetch unavailable ({exc}); falling back to closed-order symbols")

    exchange.load_markets()
    symbols = set()
    try:
        orders = exchange.fetch_closed_orders(since=since_ms, limit=None)
        symbols.update(o.get("symbol") for o in orders if o.get("symbol"))
    except Exception as exc:
        print(f"  closed-order symbol discovery failed: {exc}")

    try:
        balance = exchange.fetch_balance()
        for currency, amount in (balance.get("total") or {}).items():
            if amount and amount > 0 and currency not in USD_LIKE:
                symbol = f"{currency}/USD"
                if symbol in exchange.symbols:
                    symbols.add(symbol)
    except Exception:
        pass

    for symbol in sorted(symbols):
        try:
            offset = 0
            seen_ids = set()
            pages = 0
            while pages < max_pages:
                batch = fetch_my_trades_page(exchange, symbol, since_ms, page_size, offset)
                pages += 1
                if not batch:
                    break
                before = len(seen_ids)
                for trade in batch:
                    seen_ids.add(trade.get("id") or (
                        trade.get("timestamp"), trade.get("symbol"), trade.get("side"),
                        trade.get("amount"), trade.get("price"),
                    ))
                trades.extend(batch)
                if len(batch) < page_size or len(seen_ids) == before:
                    break
                offset += len(batch)
            print(f"  {symbol}: collected {sum(1 for t in trades if t.get('symbol') == symbol)} fills", flush=True)
        except Exception as exc:
            print(f"  {symbol}: skipped ({exc})")

    return dedupe_trades(trades)


def current_price(exchange: ccxt.Exchange, symbol: str) -> float | None:
    try:
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker["last"])
    except Exception:
        return None


def current_prices(exchange: ccxt.Exchange, symbols: list[str]) -> dict[str, float | None]:
    prices: dict[str, float | None] = {}
    if not symbols:
        return prices
    try:
        for i in range(0, len(symbols), 100):
            chunk = symbols[i:i + 100]
            tickers = exchange.fetch_tickers(chunk)
            for symbol in chunk:
                ticker = tickers.get(symbol) or {}
                prices[symbol] = float(ticker["last"]) if ticker.get("last") is not None else None
            time.sleep(1.25)
        return prices
    except Exception as exc:
        print(f"  bulk ticker fetch failed ({exc}); falling back only where needed", flush=True)

    for symbol in symbols:
        prices[symbol] = current_price(exchange, symbol)
        time.sleep(1.25)
    return prices


def fifo_pnl(exchange: ccxt.Exchange, trades: list[dict[str, Any]]) -> dict[str, Any]:
    lots: dict[str, deque[Lot]] = defaultdict(deque)
    realized_rows = []
    per_symbol = defaultdict(lambda: {
        "buys": 0, "sells": 0, "buy_notional": 0.0, "sell_notional": 0.0,
        "fees_usd": 0.0, "realized_pnl_usd": 0.0, "matched_qty": 0.0,
        "wins": 0, "losses": 0, "unmatched_sell_qty": 0.0,
        "unmatched_sell_proceeds_usd": 0.0,
    })

    for t in trades:
        symbol = t["symbol"]
        side = t["side"]
        base, quote = parse_symbol(symbol)
        if quote not in USD_LIKE and quote != "USD":
            continue

        qty = effective_qty(t, base)
        notional = float(t["cost"])
        fee_usd = quote_fee_usd(t, base, quote)
        stats = per_symbol[symbol]
        stats["fees_usd"] += fee_usd

        if side == "buy":
            stats["buys"] += 1
            stats["buy_notional"] += notional
            lots[symbol].append(Lot(
                qty=qty,
                cost_usd=notional + fee_usd,
                opened_at=t.get("datetime") or "",
                buy_trade_id=t.get("id"),
            ))
            continue

        if side != "sell":
            continue

        stats["sells"] += 1
        stats["sell_notional"] += notional
        remaining = qty
        sell_proceeds_total = max(0.0, notional - fee_usd)

        while remaining > 1e-12 and lots[symbol]:
            lot = lots[symbol][0]
            matched = min(remaining, lot.qty)
            lot_fraction = matched / lot.qty if lot.qty else 0.0
            sell_fraction = matched / qty if qty else 0.0
            cost_basis = lot.cost_usd * lot_fraction
            proceeds = sell_proceeds_total * sell_fraction
            pnl = proceeds - cost_basis

            realized_rows.append({
                "symbol": symbol,
                "sell_time": t.get("datetime"),
                "buy_time": lot.opened_at,
                "qty": matched,
                "entry_cost_usd": cost_basis,
                "exit_proceeds_usd": proceeds,
                "pnl_usd": pnl,
                "pnl_pct": (pnl / cost_basis * 100.0) if cost_basis else None,
                "sell_trade_id": t.get("id"),
                "buy_trade_id": lot.buy_trade_id,
            })

            stats["realized_pnl_usd"] += pnl
            stats["matched_qty"] += matched
            if pnl >= 0:
                stats["wins"] += 1
            else:
                stats["losses"] += 1

            lot.qty -= matched
            lot.cost_usd -= cost_basis
            remaining -= matched
            if lot.qty <= 1e-12:
                lots[symbol].popleft()

        if remaining > 1e-12:
            stats["unmatched_sell_qty"] += remaining
            stats["unmatched_sell_proceeds_usd"] += sell_proceeds_total * (remaining / qty if qty else 0.0)

    open_rows = []
    unrealized_total = 0.0
    open_cost_total = 0.0
    open_value_total = 0.0
    open_symbols = [
        symbol for symbol, q in lots.items()
        if any(lot.qty > 1e-12 for lot in q)
    ]
    marks = current_prices(exchange, open_symbols)
    for symbol, q in lots.items():
        mark = marks.get(symbol)
        for lot in q:
            if lot.qty <= 1e-12:
                continue
            market_value = lot.qty * mark if mark else None
            unrealized = (market_value - lot.cost_usd) if market_value is not None else None
            open_cost_total += lot.cost_usd
            if market_value is not None:
                open_value_total += market_value
                unrealized_total += unrealized or 0.0
            open_rows.append({
                "symbol": symbol,
                "buy_time": lot.opened_at,
                "qty": lot.qty,
                "cost_basis_usd": lot.cost_usd,
                "mark_price": mark,
                "market_value_usd": market_value,
                "unrealized_pnl_usd": unrealized,
                "unrealized_pnl_pct": (unrealized / lot.cost_usd * 100.0) if unrealized is not None and lot.cost_usd else None,
                "buy_trade_id": lot.buy_trade_id,
            })

    realized_total = sum(r["pnl_usd"] for r in realized_rows)
    fees_total = sum(v["fees_usd"] for v in per_symbol.values())
    unmatched_sell_proceeds = sum(v["unmatched_sell_proceeds_usd"] for v in per_symbol.values())
    total_wins = sum(v["wins"] for v in per_symbol.values())
    total_losses = sum(v["losses"] for v in per_symbol.values())
    total_closed = total_wins + total_losses

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "fills": len(trades),
            "symbols": len(per_symbol),
            "realized_pnl_usd": realized_total,
            "unrealized_pnl_usd": unrealized_total,
            "total_pnl_usd": realized_total + unrealized_total,
            "fees_usd": fees_total,
            "unmatched_sell_proceeds_unknown_basis_usd": unmatched_sell_proceeds,
            "closed_lots": len(realized_rows),
            "open_lots": len(open_rows),
            "open_cost_basis_usd": open_cost_total,
            "open_market_value_usd": open_value_total,
            "wins": total_wins,
            "losses": total_losses,
            "win_rate": total_wins / total_closed if total_closed else None,
        },
        "by_symbol": dict(sorted(per_symbol.items())),
        "realized_lots": realized_rows,
        "open_lots": open_rows,
    }


def write_outputs(result: dict[str, Any]) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    json_path = OUT_DIR / "real_trade_pnl.json"
    json_path.write_text(json.dumps(result, indent=2, default=str))

    for name in ("realized_lots", "open_lots"):
        rows = result[name]
        csv_path = OUT_DIR / f"{name}.csv"
        if not rows:
            csv_path.write_text("")
            continue
        with csv_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-days", type=int, default=None, help="Limit to the last N days. Default: all available.")
    parser.add_argument("--max-pages", type=int, default=200, help="Pagination safety cap per fetch path.")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("EXCHANGE_API_KEY")
    secret = os.getenv("EXCHANGE_SECRET_KEY")
    if not api_key or not secret:
        raise SystemExit("Missing EXCHANGE_API_KEY / EXCHANGE_SECRET_KEY in .env")

    exchange = ccxt.kraken({"apiKey": api_key, "secret": secret, "enableRateLimit": True})
    since_ms = None
    if args.since_days:
        since_ms = exchange.milliseconds() - args.since_days * 24 * 60 * 60 * 1000

    print("Fetching verified Kraken fills...")
    trades = fetch_all_trades(exchange, since_ms, args.max_pages)
    print(f"Fetched {len(trades)} unique fills")
    result = fifo_pnl(exchange, trades)
    write_outputs(result)

    summary = result["summary"]
    print("\nREALIZED + OPEN P&L SUMMARY")
    print(f"  Fills:              {summary['fills']}")
    print(f"  Symbols:            {summary['symbols']}")
    print(f"  Closed FIFO lots:   {summary['closed_lots']}")
    print(f"  Open FIFO lots:     {summary['open_lots']}")
    print(f"  Fees:               ${summary['fees_usd']:.4f}")
    print(f"  Realized P&L:       ${summary['realized_pnl_usd']:.4f}")
    print(f"  Unrealized P&L:     ${summary['unrealized_pnl_usd']:.4f}")
    print(f"  Total P&L:          ${summary['total_pnl_usd']:.4f}")
    if summary["win_rate"] is not None:
        print(f"  Win rate:           {summary['wins']}/{summary['wins'] + summary['losses']} ({summary['win_rate'] * 100:.1f}%)")
    print("\nWrote:")
    print("  data/real_trade_pnl.json")
    print("  data/realized_lots.csv")
    print("  data/open_lots.csv")


if __name__ == "__main__":
    main()
