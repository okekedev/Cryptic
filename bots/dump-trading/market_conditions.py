#!/usr/bin/env python3
"""
Market Conditions Analyzer - Determines if market is suitable for dump trading

Analyzes:
- Overall market volatility (using BTC as proxy)
- Market trend (bullish vs bearish)
- Trading volume trends
- Market momentum (RSI)
- Recent dump success patterns
- Trading session activity (US/EU/ASIA hours)

Only enables trading during favorable conditions to maximize profitability.
"""

import logging
import requests
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Optional, List
import statistics
import os

logger = logging.getLogger(__name__)

# Configuration
MIN_VOLATILITY = float(os.getenv("MIN_VOLATILITY", "1.5"))  # Minimum 1.5% volatility
IDEAL_VOLATILITY_MIN = float(os.getenv("IDEAL_VOLATILITY_MIN", "2.0"))  # 2%
IDEAL_VOLATILITY_MAX = float(os.getenv("IDEAL_VOLATILITY_MAX", "6.0"))  # 6%
EXTREME_VOLATILITY = float(os.getenv("EXTREME_VOLATILITY", "8.0"))  # >8% is too risky
MIN_TRADE_SUCCESS_RATE = float(os.getenv("MIN_TRADE_SUCCESS_RATE", "40.0"))  # 40% win rate
RECENT_TRADES_LOOKBACK_HOURS = int(os.getenv("RECENT_TRADES_LOOKBACK_HOURS", "24"))  # Last 24h

class MarketConditions:
    """Analyzes market conditions to determine if trading should be enabled"""

    def __init__(self, backend_url: str = "http://backend:5000", db_path: str = "/app/data/dump_trading.db"):
        self.backend_url = backend_url
        self.db_path = db_path
        self.last_check = None
        self.cache_minutes = 5  # Cache results for 5 minutes
        self.cached_result = None
        self.last_condition_state = None  # Track state changes

    def get_btc_volatility(self) -> Optional[float]:
        """
        Calculate BTC volatility over last 24h using price data

        Returns:
            float: Volatility percentage (higher = more volatile)
        """
        try:
            # Fetch BTC price history from backend
            response = requests.get(
                f"{self.backend_url}/api/historical/BTC-USD",
                params={"hours": 24},
                timeout=10
            )

            if response.status_code != 200:
                logger.warning(f"Failed to fetch BTC data: {response.status_code}")
                return None

            data = response.json()
            if not data or len(data) < 20:
                return None

            # Calculate price changes
            prices = [float(candle['close']) for candle in data]
            returns = [(prices[i] - prices[i-1]) / prices[i-1] * 100 for i in range(1, len(prices))]

            # Volatility = standard deviation of returns
            volatility = statistics.stdev(returns)

            return volatility

        except Exception as e:
            logger.error(f"Error calculating BTC volatility: {e}")
            return None

    def get_market_trend(self) -> Optional[str]:
        """
        Determine market trend (bullish/bearish) using BTC

        Returns:
            str: 'bullish', 'bearish', or 'neutral'
        """
        try:
            # Fetch BTC price for last 4 hours
            response = requests.get(
                f"{self.backend_url}/api/historical/BTC-USD",
                params={"hours": 4},
                timeout=10
            )

            if response.status_code != 200:
                return None

            data = response.json()
            if not data or len(data) < 10:
                return None

            prices = [float(candle['close']) for candle in data]

            # Calculate trend: compare first half vs second half
            mid = len(prices) // 2
            first_half_avg = sum(prices[:mid]) / mid
            second_half_avg = sum(prices[mid:]) / (len(prices) - mid)

            change_pct = ((second_half_avg - first_half_avg) / first_half_avg) * 100

            if change_pct > 0.5:
                return 'bullish'
            elif change_pct < -0.5:
                return 'bearish'
            else:
                return 'neutral'

        except Exception as e:
            logger.error(f"Error determining market trend: {e}")
            return None

    def get_btc_rsi(self, period: int = 14) -> Optional[float]:
        """
        Calculate BTC RSI (Relative Strength Index)

        RSI > 70 = overbought (risky for dumps)
        RSI < 30 = oversold (good for bounce plays)
        40-60 = neutral

        Returns:
            float: RSI value (0-100)
        """
        try:
            # Fetch BTC price history for RSI calculation
            response = requests.get(
                f"{self.backend_url}/api/historical/BTC-USD",
                params={"hours": 2},  # Need enough data for RSI
                timeout=10
            )

            if response.status_code != 200:
                return None

            data = response.json()
            if not data or len(data) < period + 1:
                return None

            # Get closing prices
            prices = [float(candle['close']) for candle in data]

            # Calculate price changes
            deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]

            # Separate gains and losses
            gains = [d if d > 0 else 0 for d in deltas]
            losses = [-d if d < 0 else 0 for d in deltas]

            # Calculate average gains and losses
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period

            if avg_loss == 0:
                return 100.0  # No losses = maximum RSI

            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

            return rsi

        except Exception as e:
            logger.error(f"Error calculating BTC RSI: {e}")
            return None

    def get_volume_trend(self) -> Optional[str]:
        """
        Determine if trading volume is increasing or decreasing

        Returns:
            str: 'increasing', 'decreasing', 'stable'
        """
        try:
            # Fetch BTC volume data
            response = requests.get(
                f"{self.backend_url}/api/historical/BTC-USD",
                params={"hours": 4},
                timeout=10
            )

            if response.status_code != 200:
                return None

            data = response.json()
            if not data or len(data) < 10:
                return None

            # Extract volumes
            volumes = [float(candle.get('volume', 0)) for candle in data]

            # Compare first half vs second half
            mid = len(volumes) // 2
            first_half_avg = sum(volumes[:mid]) / mid
            second_half_avg = sum(volumes[mid:]) / (len(volumes) - mid)

            change_pct = ((second_half_avg - first_half_avg) / first_half_avg) * 100

            if change_pct > 10:
                return 'increasing'
            elif change_pct < -10:
                return 'decreasing'
            else:
                return 'stable'

        except Exception as e:
            logger.error(f"Error determining volume trend: {e}")
            return None

    def get_recent_trade_performance(self) -> Optional[Dict]:
        """
        Analyze recent dump trading performance to see if strategy is working

        Returns:
            dict: {
                'total_trades': int,
                'winning_trades': int,
                'win_rate': float,
                'avg_pnl_pct': float
            }
        """
        try:
            # Check if database exists
            if not os.path.exists(self.db_path):
                return None

            # Connect to dump trading database
            db = sqlite3.connect(self.db_path)
            cursor = db.cursor()

            # Get trades from last N hours
            cutoff_time = (datetime.now() - timedelta(hours=RECENT_TRADES_LOOKBACK_HOURS)).isoformat()

            cursor.execute("""
                SELECT COUNT(*),
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                       AVG(pnl_percent)
                FROM trades
                WHERE status = 'closed'
                AND exit_time > ?
            """, (cutoff_time,))

            result = cursor.fetchone()
            db.close()

            if not result or result[0] == 0:
                return {
                    'total_trades': 0,
                    'winning_trades': 0,
                    'win_rate': 0.0,
                    'avg_pnl_pct': 0.0
                }

            total_trades = result[0]
            winning_trades = result[1] or 0
            avg_pnl = result[2] or 0.0

            return {
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'win_rate': (winning_trades / total_trades * 100) if total_trades > 0 else 0.0,
                'avg_pnl_pct': avg_pnl
            }

        except Exception as e:
            logger.error(f"Error getting recent trade performance: {e}")
            return None

    def get_trading_session(self) -> str:
        """
        Determine current trading session (US/EU/ASIA)
        Different sessions have different liquidity

        Returns:
            str: 'us', 'eu', 'asia', 'overlap'
        """
        try:
            now_utc = datetime.utcnow()
            hour = now_utc.hour

            # US session: 13:30 - 20:00 UTC (NYSE open hours)
            # EU session: 08:00 - 16:30 UTC (London open hours)
            # ASIA session: 00:00 - 08:00 UTC (Tokyo open hours)

            if 13 <= hour < 20:
                return 'us'
            elif 8 <= hour < 17:
                if hour < 14:
                    return 'overlap'  # EU/US overlap (highest liquidity)
                return 'eu'
            else:
                return 'asia'

        except Exception as e:
            logger.error(f"Error determining trading session: {e}")
            return 'unknown'

    def get_dump_frequency(self) -> Optional[int]:
        """
        Count number of dumps detected in last hour
        Helps determine if market is unstable

        Returns:
            int: Number of dumps in last hour
        """
        try:
            # Check drop detector database
            drop_db_path = os.path.dirname(self.db_path) + "/drop_detector.db"

            if not os.path.exists(drop_db_path):
                return None

            db = sqlite3.connect(drop_db_path)
            cursor = db.cursor()

            # Count dumps in last hour
            cutoff_time = (datetime.now() - timedelta(hours=1)).isoformat()

            cursor.execute("""
                SELECT COUNT(*)
                FROM drop_alerts
                WHERE spike_type = 'dump'
                AND timestamp > ?
            """, (cutoff_time,))

            result = cursor.fetchone()
            db.close()

            return result[0] if result else 0

        except Exception as e:
            logger.error(f"Error getting dump frequency: {e}")
            return None

    def should_trade(self) -> Dict:
        """
        Determine if current market conditions are suitable for dump trading

        Comprehensive scoring system (0-100 points):
        - Volatility: 0-30 points
        - Trend: 0-30 points
        - RSI: 0-15 points
        - Volume: 0-10 points
        - Session: 0-10 points
        - Performance: 0-5 points

        Trading enabled if score >= 50

        Returns:
            dict: {
                'enabled': bool,
                'reason': str,
                'score': float (0-100),
                'details': list of reasons,
                'metrics': dict of all indicators,
                'timestamp': str
            }
        """
        # Check cache
        if self.cached_result and self.last_check:
            age_minutes = (datetime.now() - self.last_check).total_seconds() / 60
            if age_minutes < self.cache_minutes:
                logger.debug(f"Using cached market conditions ({age_minutes:.1f} min old)")
                return self.cached_result

        logger.info("🔍 Analyzing comprehensive market conditions...")

        # Get all market metrics
        volatility = self.get_btc_volatility()
        trend = self.get_market_trend()
        rsi = self.get_btc_rsi()
        volume_trend = self.get_volume_trend()
        session = self.get_trading_session()
        performance = self.get_recent_trade_performance()
        dump_freq = self.get_dump_frequency()

        # Calculate trading score (0-100)
        score = 0
        reasons = []
        warnings = []

        # === VOLATILITY SCORE (0-30 points) ===
        if volatility is not None:
            if volatility < MIN_VOLATILITY:
                vol_score = 0
                reasons.append(f"❌ Very low volatility ({volatility:.2f}% < {MIN_VOLATILITY}%)")
            elif volatility < IDEAL_VOLATILITY_MIN:
                vol_score = 10
                reasons.append(f"⚡ Low volatility ({volatility:.2f}%)")
            elif volatility <= IDEAL_VOLATILITY_MAX:
                vol_score = 30
                reasons.append(f"✅ IDEAL volatility ({volatility:.2f}%)")
            elif volatility <= EXTREME_VOLATILITY:
                vol_score = 20
                reasons.append(f"⚠️ High volatility ({volatility:.2f}%) - caution")
            else:
                vol_score = 5
                warnings.append(f"🚨 EXTREME volatility ({volatility:.2f}%) - very risky")

            score += vol_score
        else:
            warnings.append("❌ Cannot determine volatility")

        # === TREND SCORE (0-30 points) ===
        if trend == 'bullish':
            trend_score = 30
            reasons.append("✅ Strong bullish trend - dumps bounce well")
        elif trend == 'neutral':
            trend_score = 15
            reasons.append("⚡ Neutral trend - moderate bounces")
        elif trend == 'bearish':
            trend_score = 0
            warnings.append("🚨 Bearish trend - dumps may continue falling")
        else:
            trend_score = 0
            warnings.append("❌ Cannot determine trend")

        score += trend_score

        # === RSI SCORE (0-15 points) ===
        if rsi is not None:
            if rsi < 30:
                rsi_score = 15
                reasons.append(f"✅ RSI oversold ({rsi:.1f}) - prime for bounces")
            elif rsi < 50:
                rsi_score = 10
                reasons.append(f"✅ RSI neutral-low ({rsi:.1f}) - good for dumps")
            elif rsi < 70:
                rsi_score = 5
                reasons.append(f"⚡ RSI neutral-high ({rsi:.1f})")
            else:
                rsi_score = 0
                warnings.append(f"⚠️ RSI overbought ({rsi:.1f}) - risky for dumps")

            score += rsi_score
        else:
            warnings.append("❌ Cannot calculate RSI")

        # === VOLUME SCORE (0-10 points) ===
        if volume_trend == 'increasing':
            vol_trend_score = 10
            reasons.append("✅ Volume increasing - strong momentum")
        elif volume_trend == 'stable':
            vol_trend_score = 5
            reasons.append("⚡ Volume stable - normal activity")
        elif volume_trend == 'decreasing':
            vol_trend_score = 0
            warnings.append("⚠️ Volume decreasing - weak liquidity")
        else:
            vol_trend_score = 0

        score += vol_trend_score

        # === TRADING SESSION SCORE (0-10 points) ===
        if session == 'overlap':
            session_score = 10
            reasons.append("✅ EU/US overlap - peak liquidity")
        elif session == 'us':
            session_score = 8
            reasons.append("✅ US session - high liquidity")
        elif session == 'eu':
            session_score = 7
            reasons.append("✅ EU session - good liquidity")
        elif session == 'asia':
            session_score = 3
            reasons.append("⚡ Asia session - lower liquidity")
        else:
            session_score = 0

        score += session_score

        # === PERFORMANCE SCORE (0-5 points) ===
        if performance and performance['total_trades'] >= 5:
            win_rate = performance['win_rate']
            if win_rate >= 60:
                perf_score = 5
                reasons.append(f"✅ Recent performance excellent ({win_rate:.1f}% wins)")
            elif win_rate >= MIN_TRADE_SUCCESS_RATE:
                perf_score = 3
                reasons.append(f"✅ Recent performance good ({win_rate:.1f}% wins)")
            else:
                perf_score = 0
                warnings.append(f"⚠️ Recent performance poor ({win_rate:.1f}% < {MIN_TRADE_SUCCESS_RATE}%)")

            score += perf_score
        elif performance and performance['total_trades'] > 0:
            reasons.append(f"⚡ Only {performance['total_trades']} recent trades - limited data")
        else:
            reasons.append("⚡ No recent trades - no performance data")

        # === DUMP FREQUENCY CHECK ===
        if dump_freq is not None:
            if dump_freq > 10:
                warnings.append(f"⚠️ High dump frequency ({dump_freq}/hour) - unstable market")
            elif dump_freq > 5:
                reasons.append(f"⚡ Moderate dump activity ({dump_freq}/hour)")
            else:
                reasons.append(f"✅ Normal dump activity ({dump_freq}/hour)")

        # === DECISION LOGIC ===
        # Require minimum score of 50/100 to enable trading
        # Also check critical blockers

        blockers = []

        # Critical blockers (auto-disable trading)
        if volatility and volatility > EXTREME_VOLATILITY:
            blockers.append("Extreme volatility")
        if trend == 'bearish' and (rsi is None or rsi > 50):
            blockers.append("Strong bearish trend")
        if performance and performance['total_trades'] >= 10 and performance['win_rate'] < 20:
            blockers.append("Very poor recent performance")

        if blockers:
            enabled = False
            decision_reason = f"🚨 TRADING BLOCKED: {', '.join(blockers)}"
        elif score >= 70:
            enabled = True
            decision_reason = "🟢 EXCELLENT conditions - highly favorable"
        elif score >= 50:
            enabled = True
            decision_reason = "✅ GOOD conditions - trading enabled"
        else:
            enabled = False
            decision_reason = f"❌ POOR conditions - score {score}/100 (need 50+)"

        # Prepare result
        result = {
            'enabled': enabled,
            'reason': decision_reason,
            'score': score,
            'details': reasons,
            'warnings': warnings,
            'blockers': blockers,
            'metrics': {
                'volatility': volatility,
                'trend': trend,
                'rsi': rsi,
                'volume_trend': volume_trend,
                'session': session,
                'performance': performance,
                'dump_frequency': dump_freq
            },
            'timestamp': datetime.now().isoformat()
        }

        # Log comprehensive analysis
        logger.info("=" * 80)
        logger.info("📊 COMPREHENSIVE MARKET CONDITIONS ANALYSIS")
        logger.info("=" * 80)
        logger.info(f"🎯 SCORE: {score}/100 (Need 50+ to trade)")
        logger.info("")

        if reasons:
            logger.info("✅ FAVORABLE FACTORS:")
            for reason in reasons:
                logger.info(f"   {reason}")
            logger.info("")

        if warnings:
            logger.info("⚠️ RISK FACTORS:")
            for warning in warnings:
                logger.info(f"   {warning}")
            logger.info("")

        if blockers:
            logger.info("🚨 CRITICAL BLOCKERS:")
            for blocker in blockers:
                logger.info(f"   {blocker}")
            logger.info("")

        logger.info(f"📈 DECISION: {decision_reason}")
        logger.info(f"🎲 TRADING: {'ENABLED ✅' if enabled else 'DISABLED ❌'}")
        logger.info("")

        logger.info("📊 RAW METRICS:")
        logger.info(f"   Volatility: {volatility:.2f}% (ideal: {IDEAL_VOLATILITY_MIN}-{IDEAL_VOLATILITY_MAX}%)" if volatility else "   Volatility: N/A")
        logger.info(f"   Trend: {trend or 'N/A'}")
        logger.info(f"   RSI: {rsi:.1f}" if rsi else "   RSI: N/A")
        logger.info(f"   Volume: {volume_trend or 'N/A'}")
        logger.info(f"   Session: {session}")
        if performance and performance['total_trades'] > 0:
            logger.info(f"   Recent: {performance['winning_trades']}/{performance['total_trades']} wins ({performance['win_rate']:.1f}%)")
        logger.info(f"   Dumps/hr: {dump_freq}" if dump_freq is not None else "   Dumps/hr: N/A")
        logger.info("=" * 80)

        # Check for state change (trading enabled -> disabled or vice versa)
        if self.last_condition_state is not None and self.last_condition_state != enabled:
            state_change_msg = "🟢 TRADING ENABLED" if enabled else "🔴 TRADING DISABLED"
            logger.warning(f"\n{'=' * 80}\n⚡ STATE CHANGE: {state_change_msg}\n{'=' * 80}\n")
            result['state_changed'] = True
        else:
            result['state_changed'] = False

        # Cache result
        self.cached_result = result
        self.last_check = datetime.now()
        self.last_condition_state = enabled

        return result

    def get_status(self) -> str:
        """Get human-readable status string"""
        result = self.should_trade()
        metrics = result.get('metrics', {})

        # Build compact status
        parts = []
        parts.append(f"Score: {result['score']}/100")

        if metrics.get('volatility'):
            parts.append(f"Vol: {metrics['volatility']:.1f}%")

        if metrics.get('trend'):
            parts.append(f"Trend: {metrics['trend']}")

        if metrics.get('rsi'):
            parts.append(f"RSI: {metrics['rsi']:.0f}")

        parts.append(f"Trading: {'✅ ON' if result['enabled'] else '❌ OFF'}")

        return " | ".join(parts)

    def get_detailed_status(self) -> str:
        """Get detailed multi-line status string for alerts"""
        result = self.should_trade()
        metrics = result.get('metrics', {})

        status = "📊 MARKET CONDITIONS\n\n"
        status += f"Score: {result['score']}/100\n"
        status += f"Status: {'🟢 ENABLED' if result['enabled'] else '🔴 DISABLED'}\n\n"

        if metrics.get('volatility'):
            status += f"Volatility: {metrics['volatility']:.2f}%\n"
        if metrics.get('trend'):
            status += f"Trend: {metrics['trend'].title()}\n"
        if metrics.get('rsi'):
            status += f"RSI: {metrics['rsi']:.1f}\n"
        if metrics.get('volume_trend'):
            status += f"Volume: {metrics['volume_trend'].title()}\n"
        if metrics.get('session'):
            status += f"Session: {metrics['session'].upper()}\n"

        perf = metrics.get('performance')
        if perf and perf.get('total_trades', 0) > 0:
            status += f"\nRecent: {perf['winning_trades']}/{perf['total_trades']} wins "
            status += f"({perf['win_rate']:.1f}%)\n"

        status += f"\n{result['reason']}"

        return status


# Singleton instance
_market_conditions = None

def get_market_conditions(backend_url: str = "http://backend:5000", db_path: str = "/app/data/dump_trading.db") -> MarketConditions:
    """Get or create singleton MarketConditions instance"""
    global _market_conditions
    if _market_conditions is None:
        _market_conditions = MarketConditions(backend_url, db_path)
    return _market_conditions
