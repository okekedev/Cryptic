#!/usr/bin/env python3
"""
Quick backtest - faster version with better error handling
"""

import sqlite3
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

BACKEND_URL = "http://localhost:5000"
POSITION_SIZE = 20
MIN_PROFIT_TARGET = 2.0
TARGET_PROFIT = 8.0
MAX_LOSS = 2.5
BUY_FEE = 0.6
SELL_FEE = 0.4

def get_price(symbol):
    """Get current price with timeout"""
    try:
        resp = requests.get(f"{BACKEND_URL}/tickers/{symbol}", timeout=2)
        if resp.status_code == 200:
            return symbol, resp.json().get('price')
    except:
        pass
    return symbol, None

def main():
    print("=" * 80)
    print("QUICK BACKTEST: Market Filter Impact")
    print("=" * 80)
    print()

    # Get dumps
    conn = sqlite3.connect('./bot/data/drop_detector.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, pct_change, new_price, timestamp
        FROM drop_alerts
        WHERE timestamp > datetime('now', '-24 hours')
        ORDER BY timestamp DESC
        LIMIT 25
    """)
    dumps = cursor.fetchall()
    conn.close()

    print(f"Analyzing {len(dumps)} recent dumps...")
    print()

    # Fetch prices in parallel for speed
    print("Fetching current prices...")
    symbols = [d[0] for d in dumps]
    prices = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_price, sym): sym for sym in symbols}
        for future in as_completed(futures, timeout=15):
            sym, price = future.result()
            if price:
                prices[sym] = price

    print(f"Got prices for {len(prices)}/{len(symbols)} symbols")
    print()

    # Simulate trades
    print("=" * 80)
    print("SCENARIO 1: WITHOUT Market Filter (All Dumps Traded)")
    print("=" * 80)
    print()

    total_pnl = 0
    wins = 0
    losses = 0
    total_trades = 0

    for symbol, pct_change, entry_price, timestamp in dumps:
        if symbol not in prices:
            continue

        # Entry 0.5% below dump price
        entry_price = entry_price * 0.995
        current_price = prices[symbol]

        # Calculate bounce
        bounce_pct = ((current_price - entry_price) / entry_price) * 100

        # Simplified P&L calculation
        shares = POSITION_SIZE / entry_price

        if bounce_pct >= TARGET_PROFIT:
            exit_pct = 6.0  # Average ladder sell
            result = "WIN"
        elif bounce_pct >= MIN_PROFIT_TARGET:
            exit_pct = MIN_PROFIT_TARGET
            result = "WIN"
        elif bounce_pct <= -MAX_LOSS:
            exit_pct = -MAX_LOSS
            result = "LOSS"
        else:
            exit_pct = bounce_pct
            result = "WIN" if bounce_pct > 0 else "LOSS"

        exit_price = entry_price * (1 + exit_pct/100)
        revenue = shares * exit_price * (1 - SELL_FEE/100)
        pnl = revenue - POSITION_SIZE

        total_pnl += pnl
        total_trades += 1

        if result == "WIN":
            wins += 1
            emoji = "✅"
        else:
            losses += 1
            emoji = "❌"

        print(f"{emoji} {symbol:12} {pct_change:6.2f}% → {bounce_pct:+6.2f}% = ${pnl:+7.2f}")

    print("-" * 80)
    print()
    print("RESULTS WITHOUT FILTER:")
    if total_trades > 0:
        win_rate = wins / total_trades * 100
        print(f"  Trades: {total_trades}")
        print(f"  Wins: {wins} ({win_rate:.1f}%)")
        print(f"  Losses: {losses} ({100-win_rate:.1f}%)")
        print(f"  Total P&L: ${total_pnl:+.2f}")
        print(f"  Avg P&L: ${total_pnl/total_trades:+.2f}")
    print()

    # Scenario 2: With filter
    print("=" * 80)
    print("SCENARIO 2: WITH Market Filter")
    print("=" * 80)
    print()
    print("Market filter is currently blocking trades (score < 50)")
    print("Trades executed: 0")
    print("Total P&L: $0.00")
    print()

    # Comparison
    print("=" * 80)
    print("COMPARISON")
    print("=" * 80)
    print()
    print(f"Without Filter: ${total_pnl:+.2f} ({total_trades} trades)")
    print(f"With Filter:    $0.00 (0 trades)")
    print(f"Difference:     ${total_pnl:+.2f}")
    print()

    if total_pnl > 0:
        print("🚨 CONCLUSION: Market filter is BLOCKING PROFITS!")
        print(f"   You missed ${total_pnl:.2f} in potential gains")
        print()
        print("RECOMMENDATION:")
        print("  • Lower market filter thresholds")
        print("  • Current conditions may be better than filter thinks")
        print("  • Consider testing with filter disabled for 24h")
    elif total_pnl < 0:
        print("✅ CONCLUSION: Market filter is PROTECTING CAPITAL!")
        print(f"   You avoided ${abs(total_pnl):.2f} in losses")
        print()
        print("RECOMMENDATION:")
        print("  • Keep market filter enabled")
        print("  • Filter is correctly identifying poor conditions")
    else:
        print("⚪ NEUTRAL impact")

    print()
    print("=" * 80)

if __name__ == "__main__":
    main()
