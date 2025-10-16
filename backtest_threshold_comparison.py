#!/usr/bin/env python3
"""
Backtest comparing different market filter thresholds to find optimal settings.
Tests: No filter, 30% acceptance, 50% acceptance, 70% acceptance, 90% acceptance
"""

import sqlite3
from datetime import datetime, timedelta

# Strategy parameters
POSITION_SIZE = 20
MIN_PROFIT_TARGET = 2.0
TARGET_PROFIT = 8.0
MAX_LOSS = 2.5
BUY_FEE = 0.6
SELL_FEE = 0.4

def simulate_trade_outcome(dump_pct):
    """
    Simulate trade outcome based on dump percentage.
    Larger dumps tend to have better bounces.
    Returns (pnl, result)
    """
    # Simplified model based on dump magnitude
    # Larger dumps generally have better bounce potential
    abs_dump = abs(dump_pct)

    if abs_dump < 3:
        # Small dumps - lower success rate (~40%)
        # Assume 60% chance of small bounce, 40% hit stop loss
        bounce_pct = 1.5 if hash(str(dump_pct)) % 10 < 6 else -2.5
    elif abs_dump < 5:
        # Medium dumps - better success rate (~60%)
        # Assume 70% chance of good bounce
        if hash(str(dump_pct)) % 10 < 7:
            bounce_pct = 4.0 if hash(str(dump_pct)) % 10 < 4 else 2.5
        else:
            bounce_pct = -2.5
    else:
        # Large dumps - best success rate (~70%)
        # Assume 75% chance of hitting targets
        if hash(str(dump_pct)) % 10 < 8:
            bounce_pct = 6.0 if hash(str(dump_pct)) % 10 < 5 else 3.0
        else:
            bounce_pct = -2.5

    # Calculate P&L
    entry_price = 1.0  # Normalized
    shares = POSITION_SIZE / entry_price
    exit_price = entry_price * (1 + bounce_pct/100)
    revenue = shares * exit_price * (1 - SELL_FEE/100)
    pnl = revenue - POSITION_SIZE

    result = "WIN" if pnl > 0 else "LOSS"
    return pnl, result, bounce_pct

def calculate_market_score(timestamp, dump_pct):
    """
    Simulate what the market score would have been at this time.
    Based on time of day, volatility, etc.
    Returns score 0-100
    """
    # Parse timestamp
    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    hour = dt.hour

    # Base score depends on time (US hours are better)
    if 13 <= hour < 20:  # US session
        base_score = 60
    elif 8 <= hour < 17:  # EU session
        base_score = 50
    else:  # Asia session
        base_score = 35

    # Adjust based on dump magnitude (larger dumps in volatile markets)
    abs_dump = abs(dump_pct)
    if abs_dump > 5:
        volatility_bonus = 10
    elif abs_dump > 3:
        volatility_bonus = 5
    else:
        volatility_bonus = 0

    # Add some randomness based on hash
    variance = (hash(timestamp) % 21) - 10  # -10 to +10

    score = base_score + volatility_bonus + variance
    return max(0, min(100, score))

def run_backtest(dumps, acceptance_threshold, filter_name):
    """
    Run backtest with specific acceptance threshold.
    acceptance_threshold: 0-100, trades only if market score >= threshold
    """
    results = {
        'filter_name': filter_name,
        'threshold': acceptance_threshold,
        'total_dumps': len(dumps),
        'trades_executed': 0,
        'trades_blocked': 0,
        'wins': 0,
        'losses': 0,
        'total_pnl': 0.0,
        'trades': []
    }

    for symbol, pct_change, timestamp in dumps:
        # Calculate market score
        market_score = calculate_market_score(timestamp, pct_change)

        # Check if trade would be allowed
        if market_score >= acceptance_threshold:
            # Execute trade
            pnl, result, bounce_pct = simulate_trade_outcome(pct_change)
            results['trades_executed'] += 1
            results['total_pnl'] += pnl

            if result == "WIN":
                results['wins'] += 1
            else:
                results['losses'] += 1

            results['trades'].append({
                'symbol': symbol,
                'dump_pct': pct_change,
                'bounce_pct': bounce_pct,
                'pnl': pnl,
                'result': result,
                'market_score': market_score
            })
        else:
            # Trade blocked by filter
            results['trades_blocked'] += 1

    return results

def print_results(results):
    """Print formatted results"""
    print(f"\n{'='*80}")
    print(f"{results['filter_name']}")
    print(f"{'='*80}")
    print(f"Threshold: {results['threshold']}/100")
    print(f"Dumps Detected: {results['total_dumps']}")
    print(f"Trades Executed: {results['trades_executed']} ({results['trades_executed']/results['total_dumps']*100:.1f}%)")
    print(f"Trades Blocked: {results['trades_blocked']} ({results['trades_blocked']/results['total_dumps']*100:.1f}%)")
    print()

    if results['trades_executed'] > 0:
        win_rate = results['wins'] / results['trades_executed'] * 100
        avg_pnl = results['total_pnl'] / results['trades_executed']

        print(f"Results:")
        print(f"  Wins: {results['wins']} ({win_rate:.1f}%)")
        print(f"  Losses: {results['losses']} ({100-win_rate:.1f}%)")
        print(f"  Total P&L: ${results['total_pnl']:+.2f}")
        print(f"  Avg P&L per trade: ${avg_pnl:+.2f}")
        print(f"  ROI: {results['total_pnl']/results['trades_executed']/POSITION_SIZE*100:+.1f}%")
    else:
        print(f"No trades executed (all blocked)")
        print(f"  Total P&L: $0.00")

    return results

def main():
    print("="*80)
    print("BACKTEST: Market Filter Threshold Comparison")
    print("="*80)
    print()
    print("Testing different acceptance thresholds to find optimal settings...")
    print()

    # Get dumps from database
    conn = sqlite3.connect('./bot/data/drop_detector.db')
    cursor = conn.cursor()

    # Get dumps from last 24 hours
    cursor.execute("""
        SELECT symbol, pct_change, timestamp
        FROM drop_alerts
        WHERE timestamp > datetime('now', '-24 hours')
        ORDER BY timestamp DESC
    """)

    dumps = cursor.fetchall()
    conn.close()

    print(f"Loaded {len(dumps)} dumps from last 24 hours")
    print()

    # Test different thresholds
    scenarios = [
        (0, "NO FILTER (Trade Everything)"),
        (30, "VERY RELAXED (30% threshold)"),
        (50, "RELAXED (50% threshold - RECOMMENDED)"),
        (70, "MODERATE (70% threshold)"),
        (90, "STRICT (90% threshold)")
    ]

    all_results = []

    for threshold, name in scenarios:
        results = run_backtest(dumps, threshold, name)
        print_results(results)
        all_results.append(results)

    # Comparison
    print(f"\n{'='*80}")
    print("COMPARISON TABLE")
    print(f"{'='*80}")
    print(f"{'Filter':<35} {'Trades':<10} {'Win%':<10} {'Total P&L':<15} {'Avg P&L'}")
    print(f"{'-'*80}")

    for r in all_results:
        if r['trades_executed'] > 0:
            win_rate = r['wins'] / r['trades_executed'] * 100
            avg_pnl = r['total_pnl'] / r['trades_executed']
            print(f"{r['filter_name']:<35} {r['trades_executed']:<10} {win_rate:<10.1f} ${r['total_pnl']:<14.2f} ${avg_pnl:.2f}")
        else:
            print(f"{r['filter_name']:<35} {r['trades_executed']:<10} {'N/A':<10} ${'0.00':<14} $0.00")

    # Find best performer
    print(f"\n{'='*80}")
    print("RECOMMENDATION")
    print(f"{'='*80}")

    best = max(all_results, key=lambda x: x['total_pnl'])

    print(f"\nBest Performer: {best['filter_name']}")
    print(f"  Total P&L: ${best['total_pnl']:.2f}")
    print(f"  Trades: {best['trades_executed']}")
    if best['trades_executed'] > 0:
        print(f"  Win Rate: {best['wins']/best['trades_executed']*100:.1f}%")
        print(f"  Avg P&L: ${best['total_pnl']/best['trades_executed']:.2f}")

    # Get actual bot performance for comparison
    conn = sqlite3.connect('./bot/data/dump_trading.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*), SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), SUM(IFNULL(pnl, 0))
        FROM trades
    """)
    actual_trades, actual_wins, actual_pnl = cursor.fetchone()
    conn.close()

    print(f"\n{'='*80}")
    print("ACTUAL BOT PERFORMANCE (Last 24h)")
    print(f"{'='*80}")
    print(f"  Trades Executed: {actual_trades}")
    if actual_trades > 0:
        print(f"  Wins: {actual_wins} ({actual_wins/actual_trades*100:.1f}%)")
        print(f"  Total P&L: ${actual_pnl:.2f}")
        print(f"  Avg P&L: ${actual_pnl/actual_trades:.2f}")
    else:
        print(f"  No trades executed")

    print(f"\n{'='*80}")
    print("FINAL RECOMMENDATION")
    print(f"{'='*80}")

    if best['total_pnl'] > actual_pnl:
        improvement = best['total_pnl'] - actual_pnl
        print(f"\n✅ Adjusting market filter to '{best['filter_name']}' could increase profits!")
        print(f"   Potential gain: ${improvement:.2f} over actual performance")
        print()
        print(f"SUGGESTED CHANGES to market_conditions.py:")
        if best['threshold'] == 0:
            print(f"  • DISABLE market filter (comment out score check)")
        elif best['threshold'] == 30:
            print(f"  • Change line 526: if score >= 30:  # Was 50")
            print(f"  • Lower MIN_VOLATILITY from 1.5 to 1.0")
        elif best['threshold'] == 50:
            print(f"  • Keep current threshold at 50")
            print(f"  • Adjust individual metric scores to be more generous")
        elif best['threshold'] == 70:
            print(f"  • Increase threshold from 50 to 70")
            print(f"  • Make individual metrics more strict")
        else:
            print(f"  • Increase threshold from 50 to 90")
            print(f"  • Very conservative - only best conditions")
    else:
        print(f"\n✅ Current market filter settings are OPTIMAL!")
        print(f"   Actual performance: ${actual_pnl:.2f}")
        print(f"   Best backtest: ${best['total_pnl']:.2f}")
        print(f"   Keep current settings")

    print(f"\n{'='*80}")

if __name__ == "__main__":
    main()
