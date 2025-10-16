#!/usr/bin/env python3
"""
Enhanced backtest comparing:
1. Performance WITHOUT market filter (all dumps traded)
2. Performance WITH market filter (only favorable conditions)
"""

import sqlite3
import requests
from datetime import datetime, timedelta
import sys

BACKEND_URL = "http://localhost:5000"

# Strategy parameters
POSITION_SIZE = 20  # $20 per trade
MIN_PROFIT_TARGET = 2.0  # 2%
TARGET_PROFIT = 8.0  # 8%
MAX_LOSS = 2.5  # 2.5% stop loss
BUY_FEE = 0.6  # 0.6%
SELL_FEE = 0.4  # 0.4%

# Market filter settings (from market_conditions.py)
MIN_VOLATILITY = 1.5
IDEAL_VOLATILITY_MIN = 2.0
IDEAL_VOLATILITY_MAX = 6.0

def get_current_price(symbol):
    """Get current price for a symbol"""
    try:
        response = requests.get(f"{BACKEND_URL}/tickers/{symbol}", timeout=5)
        if response.status_code == 200:
            data = response.json()
            return data.get('price')
        return None
    except Exception:
        return None

def get_market_score_at_time(timestamp):
    """
    Simplified market score calculation (0-100)
    In reality, we'd need historical market data
    For now, we'll use a simplified approach
    """
    # For this backtest, we'll assume market conditions were:
    # - Moderate (score ~40-60) most of the time
    # - This causes the filter to block most trades

    # You can enhance this to use actual historical BTC data
    return 45  # Below 50 threshold = trading disabled

def simulate_trade(symbol, entry_price, current_price):
    """Simulate a trade outcome"""
    if current_price is None:
        return None, None

    # Calculate fees
    entry_cost = POSITION_SIZE * (1 + BUY_FEE/100)
    shares = POSITION_SIZE / entry_price

    # Calculate bounce percentage
    bounce_pct = ((current_price - entry_price) / entry_price) * 100

    # Determine outcome based on strategy
    if bounce_pct >= TARGET_PROFIT:
        # Hit target profit - assume ladder sell at avg 6%
        exit_pct = 6.0
        result = "WIN"
    elif bounce_pct >= MIN_PROFIT_TARGET:
        # Hit min profit
        exit_pct = MIN_PROFIT_TARGET
        result = "WIN"
    elif bounce_pct <= -MAX_LOSS:
        # Hit stop loss
        exit_pct = -MAX_LOSS
        result = "LOSS"
    else:
        # Current state (still holding)
        exit_pct = bounce_pct
        result = "WIN" if bounce_pct > 0 else "LOSS"

    exit_price = entry_price * (1 + exit_pct/100)
    revenue = shares * exit_price * (1 - SELL_FEE/100)
    pnl = revenue - POSITION_SIZE

    return pnl, result

def main():
    print("=" * 100)
    print("COMPREHENSIVE BACKTEST: Market Filter Impact Analysis")
    print("=" * 100)
    print()

    # Connect to database
    conn = sqlite3.connect('./bot/data/drop_detector.db')
    cursor = conn.cursor()

    # Get dumps from last 24 hours
    cursor.execute("""
        SELECT symbol, pct_change, new_price, timestamp
        FROM drop_alerts
        WHERE timestamp > datetime('now', '-24 hours')
        ORDER BY timestamp DESC
    """)

    dumps = cursor.fetchall()
    conn.close()

    print(f"📊 Found {len(dumps)} dumps in last 24 hours")
    print()

    # Scenario 1: WITHOUT market filter (trade everything)
    print("=" * 100)
    print("SCENARIO 1: WITHOUT Market Filter (Trade ALL dumps)")
    print("=" * 100)
    print()

    no_filter_results = {
        'total_trades': 0,
        'wins': 0,
        'losses': 0,
        'total_pnl': 0,
        'trades': []
    }

    print("Simulating trades for top 30 dumps...")
    print("-" * 100)

    for i, (symbol, pct_change, entry_price, timestamp) in enumerate(dumps[:30]):
        # Adjust entry price (buy 0.5% below dump price)
        entry_price = entry_price * 0.995

        # Get current price
        current_price = get_current_price(symbol)

        if current_price is None:
            print(f"⚠️  {symbol:12} {pct_change:6.2f}% - No price data available")
            continue

        # Simulate trade
        pnl, result = simulate_trade(symbol, entry_price, current_price)

        if pnl is None:
            continue

        no_filter_results['total_trades'] += 1
        no_filter_results['total_pnl'] += pnl

        if result == "WIN":
            no_filter_results['wins'] += 1
            emoji = "✅"
        else:
            no_filter_results['losses'] += 1
            emoji = "❌"

        bounce_pct = ((current_price - entry_price) / entry_price) * 100
        print(f"{emoji} {symbol:12} {pct_change:6.2f}% dump → {bounce_pct:+6.2f}% bounce = ${pnl:+7.2f}")

        no_filter_results['trades'].append({
            'symbol': symbol,
            'pnl': pnl,
            'result': result
        })

        # Progress indicator
        if (i + 1) % 10 == 0:
            print(f"   ... processed {i + 1} dumps")

    print("-" * 100)
    print()
    print("SCENARIO 1 RESULTS:")
    total = no_filter_results['total_trades']
    if total > 0:
        win_rate = no_filter_results['wins'] / total * 100
        avg_pnl = no_filter_results['total_pnl'] / total
        print(f"  Total Trades: {total}")
        print(f"  Wins: {no_filter_results['wins']} ({win_rate:.1f}%)")
        print(f"  Losses: {no_filter_results['losses']} ({100-win_rate:.1f}%)")
        print(f"  Total P&L: ${no_filter_results['total_pnl']:+.2f}")
        print(f"  Avg P&L per trade: ${avg_pnl:+.2f}")
    else:
        print("  No trades executed")
    print()

    # Scenario 2: WITH market filter (only trade when score >= 50)
    print("=" * 100)
    print("SCENARIO 2: WITH Market Filter (Only favorable conditions)")
    print("=" * 100)
    print()

    with_filter_results = {
        'total_trades': 0,
        'blocked_trades': 0,
        'wins': 0,
        'losses': 0,
        'total_pnl': 0,
        'trades': []
    }

    print("Checking which trades would pass market filter...")
    print("-" * 100)

    # For this simulation, we'll assume market score was low (40-45)
    # so most trades would be blocked
    # In a real implementation, you'd check historical market conditions

    market_score = 45  # Below 50 threshold
    trades_allowed = market_score >= 50

    print(f"Market Score (simulated): {market_score}/100")
    print(f"Trading Status: {'ENABLED ✅' if trades_allowed else 'DISABLED ❌'}")
    print()

    if trades_allowed:
        # Simulate the same trades as scenario 1
        for trade in no_filter_results['trades']:
            with_filter_results['total_trades'] += 1
            with_filter_results['total_pnl'] += trade['pnl']
            if trade['result'] == 'WIN':
                with_filter_results['wins'] += 1
            else:
                with_filter_results['losses'] += 1
    else:
        print("❌ ALL TRADES BLOCKED by market filter")
        with_filter_results['blocked_trades'] = no_filter_results['total_trades']

    print("-" * 100)
    print()
    print("SCENARIO 2 RESULTS:")
    if with_filter_results['total_trades'] > 0:
        win_rate = with_filter_results['wins'] / with_filter_results['total_trades'] * 100
        avg_pnl = with_filter_results['total_pnl'] / with_filter_results['total_trades']
        print(f"  Total Trades: {with_filter_results['total_trades']}")
        print(f"  Wins: {with_filter_results['wins']} ({win_rate:.1f}%)")
        print(f"  Losses: {with_filter_results['losses']} ({100-win_rate:.1f}%)")
        print(f"  Total P&L: ${with_filter_results['total_pnl']:+.2f}")
        print(f"  Avg P&L per trade: ${avg_pnl:+.2f}")
    else:
        print(f"  Trades Executed: 0")
        print(f"  Trades Blocked: {with_filter_results['blocked_trades']}")
        print(f"  Total P&L: $0.00")
    print()

    # Comparison and recommendation
    print("=" * 100)
    print("COMPARISON & RECOMMENDATION")
    print("=" * 100)
    print()

    pnl_diff = no_filter_results['total_pnl'] - with_filter_results['total_pnl']

    print(f"Without Filter: ${no_filter_results['total_pnl']:+.2f} ({no_filter_results['total_trades']} trades)")
    print(f"With Filter:    ${with_filter_results['total_pnl']:+.2f} ({with_filter_results['total_trades']} trades)")
    print(f"Difference:     ${pnl_diff:+.2f}")
    print()

    if pnl_diff > 0:
        print("🚨 CONCLUSION: Market filter is BLOCKING PROFITS")
        print(f"   You LOST ${pnl_diff:.2f} by having the filter enabled")
        print()
        print("RECOMMENDATIONS:")
        print("  1. Lower MIN_VOLATILITY threshold (try 1.0% instead of 1.5%)")
        print("  2. Lower minimum score requirement (try 40 instead of 50)")
        print("  3. Make trend requirements less strict")
        print("  4. Consider disabling market filter during tested hours")
    elif pnl_diff < 0:
        print("✅ CONCLUSION: Market filter is PROTECTING CAPITAL")
        print(f"   You SAVED ${abs(pnl_diff):.2f} by having the filter enabled")
        print()
        print("RECOMMENDATIONS:")
        print("  1. Keep current market filter settings")
        print("  2. Filter is successfully avoiding bad market conditions")
    else:
        print("⚪ CONCLUSION: Market filter has NEUTRAL impact")
        print()
        print("RECOMMENDATIONS:")
        print("  1. Need more data to make conclusive recommendation")
        print("  2. Monitor for longer period (48-72 hours)")

    print()
    print("=" * 100)

if __name__ == "__main__":
    main()
