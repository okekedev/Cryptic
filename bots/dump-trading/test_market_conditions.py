#!/usr/bin/env python3
"""
Test Market Conditions - Standalone script to test market conditions analyzer

Usage:
    python test_market_conditions.py
"""

import os
import sys
import logging
from market_conditions import get_market_conditions

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Test market conditions analyzer"""
    print("=" * 80)
    print("MARKET CONDITIONS TEST")
    print("=" * 80)
    print()

    # Configuration
    backend_url = os.getenv("BACKEND_URL", "http://localhost:5000")
    db_path = os.getenv("DUMP_DB_PATH", "./data/dump_trading.db")

    print(f"Backend URL: {backend_url}")
    print(f"Database: {db_path}")
    print()

    # Get market conditions instance
    print("Initializing market conditions analyzer...")
    mc = get_market_conditions(backend_url, db_path)
    print()

    # Check if trading should be enabled
    print("Analyzing market conditions...")
    print()

    result = mc.should_trade()

    # Print results
    print("=" * 80)
    print("📊 MARKET CONDITIONS ANALYSIS RESULT")
    print("=" * 80)
    print()
    print(f"Trading Enabled: {'🟢 YES' if result['enabled'] else '🔴 NO'}")
    print(f"Score: {result['score']}/100")
    print(f"Decision: {result['reason']}")
    print()

    # Print metrics
    metrics = result.get('metrics', {})
    print("📈 RAW METRICS:")
    print(f"  Volatility: {metrics.get('volatility', 'N/A')}")
    print(f"  Trend: {metrics.get('trend', 'N/A')}")
    print(f"  RSI: {metrics.get('rsi', 'N/A')}")
    print(f"  Volume Trend: {metrics.get('volume_trend', 'N/A')}")
    print(f"  Session: {metrics.get('session', 'N/A')}")
    print(f"  Dump Frequency: {metrics.get('dump_frequency', 'N/A')}")

    perf = metrics.get('performance')
    if perf and perf.get('total_trades', 0) > 0:
        print(f"  Recent Performance: {perf['winning_trades']}/{perf['total_trades']} wins ({perf['win_rate']:.1f}%)")
    else:
        print(f"  Recent Performance: No trades")
    print()

    # Print favorable factors
    if result.get('details'):
        print("✅ FAVORABLE FACTORS:")
        for detail in result['details']:
            print(f"  {detail}")
        print()

    # Print warnings
    if result.get('warnings'):
        print("⚠️ RISK FACTORS:")
        for warning in result['warnings']:
            print(f"  {warning}")
        print()

    # Print blockers
    if result.get('blockers'):
        print("🚨 CRITICAL BLOCKERS:")
        for blocker in result['blockers']:
            print(f"  {blocker}")
        print()

    # Print detailed status (for Telegram alerts)
    print("=" * 80)
    print("DETAILED STATUS (for alerts):")
    print("=" * 80)
    print()
    print(mc.get_detailed_status())
    print()

    # Print compact status
    print("=" * 80)
    print("COMPACT STATUS (for logging):")
    print("=" * 80)
    print()
    print(mc.get_status())
    print()

    print("=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        sys.exit(1)
