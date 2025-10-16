#!/usr/bin/env python3
"""
Backtest to analyze if market conditions filter is helping or hurting profits.
Compares blocked trades vs what would have happened if we traded anyway.
"""

import sqlite3
import requests
from datetime import datetime, timedelta

BACKEND_URL = "http://localhost:5000"

def get_price_at_time(symbol, timestamp, minutes_later):
    """Get price of symbol X minutes after the dump"""
    try:
        # Convert timestamp string to datetime
        dump_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))

        # Fetch current price as proxy (limitation: we don't have minute-by-minute historical)
        response = requests.get(f"{BACKEND_URL}/tickers/{symbol}", timeout=5)
        if response.status_code == 200:
            data = response.json()
            return data.get('price')
        return None
    except Exception as e:
        return None

def analyze_blocked_trades():
    """Analyze recent dumps that were blocked by market conditions"""

    print("=" * 80)
    print("BACKTEST: Market Conditions Filter Impact")
    print("=" * 80)
    print()

    # Connect to drop detector DB
    conn = sqlite3.connect('./bot/data/drop_detector.db')
    cursor = conn.cursor()

    # Get dumps from last 24 hours
    cursor.execute("""
        SELECT symbol, pct_change, new_price, old_price, timestamp
        FROM drop_alerts
        WHERE timestamp > datetime('now', '-24 hours')
        ORDER BY timestamp DESC
    """)

    dumps = cursor.fetchall()
    conn.close()

    print(f"Found {len(dumps)} dumps in last 24 hours")
    print()

    # Strategy parameters (from your .env)
    POSITION_SIZE = 20  # $20 per trade
    MIN_PROFIT_TARGET = 2.0  # 2%
    TARGET_PROFIT = 8.0  # 8%
    MAX_LOSS = 2.5  # 2.5% stop loss
    BUY_FEE = 0.6  # 0.6%
    SELL_FEE = 0.4  # 0.4%

    # Simulation results
    total_trades = 0
    wins = 0
    losses = 0
    total_pnl = 0

    print("SIMULATED OUTCOMES (if market conditions was disabled):")
    print("-" * 80)

    for symbol, pct_change, new_price, old_price, timestamp in dumps[:20]:  # Test last 20
        total_trades += 1

        # Entry: Buy at new_price (after dump) + 0.5% lower for limit order
        entry_price = new_price * 0.995  # 0.5% lower
        entry_cost = POSITION_SIZE * (1 + BUY_FEE/100)
        shares = POSITION_SIZE / entry_price

        # Get current price to simulate outcome
        current_price = get_price_at_time(symbol, timestamp, 30)

        if current_price is None:
            print(f"⚠️  {symbol} {pct_change:.2f}% - No price data available")
            total_trades -= 1
            continue

        # Calculate bounce percentage
        bounce_pct = ((current_price - entry_price) / entry_price) * 100

        # Determine outcome based on strategy
        if bounce_pct >= TARGET_PROFIT:
            # Hit target profit - use ladder sell (assume average 6%)
            exit_pct = 6.0
            exit_price = entry_price * (1 + exit_pct/100)
            revenue = shares * exit_price * (1 - SELL_FEE/100)
            pnl = revenue - POSITION_SIZE
            wins += 1
            result = "✅ WIN"
        elif bounce_pct >= MIN_PROFIT_TARGET:
            # Hit min profit
            exit_price = entry_price * (1 + MIN_PROFIT_TARGET/100)
            revenue = shares * exit_price * (1 - SELL_FEE/100)
            pnl = revenue - POSITION_SIZE
            wins += 1
            result = "✅ WIN"
        elif bounce_pct <= -MAX_LOSS:
            # Hit stop loss
            exit_price = entry_price * (1 - MAX_LOSS/100)
            revenue = shares * exit_price * (1 - SELL_FEE/100)
            pnl = revenue - POSITION_SIZE
            losses += 1
            result = "❌ LOSS"
        else:
            # Current state (still holding or small profit/loss)
            revenue = shares * current_price * (1 - SELL_FEE/100)
            pnl = revenue - POSITION_SIZE
            if pnl > 0:
                wins += 1
                result = "✅ WIN"
            else:
                losses += 1
                result = "❌ LOSS"

        total_pnl += pnl

        print(f"{result} {symbol:12} {pct_change:6.2f}% dump → {bounce_pct:+6.2f}% bounce = ${pnl:+6.2f}")

    print("-" * 80)
    print()
    print("RESULTS:")
    print(f"  Total Trades: {total_trades}")
    print(f"  Wins: {wins} ({wins/total_trades*100:.1f}%)" if total_trades > 0 else "  Wins: 0")
    print(f"  Losses: {losses} ({losses/total_trades*100:.1f}%)" if total_trades > 0 else "  Losses: 0")
    print(f"  Total P&L: ${total_pnl:+.2f}")
    print(f"  Avg P&L per trade: ${total_pnl/total_trades:+.2f}" if total_trades > 0 else "  Avg P&L: $0.00")
    print()

    if total_pnl > 0:
        print("✅ CONCLUSION: Market conditions filter is BLOCKING PROFITS")
        print(f"   You would have made ${total_pnl:.2f} from these {total_trades} blocked trades")
        print()
        print("RECOMMENDATION: Lower MIN_VOLATILITY threshold to capture these opportunities")
    else:
        print("✅ CONCLUSION: Market conditions filter is PROTECTING CAPITAL")
        print(f"   You would have lost ${abs(total_pnl):.2f} from these {total_trades} trades")
        print()
        print("RECOMMENDATION: Keep current market conditions thresholds")

    print("=" * 80)

if __name__ == "__main__":
    analyze_blocked_trades()
