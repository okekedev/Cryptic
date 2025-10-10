# Market Conditions System - Complete Guide

## Overview

The Market Conditions System is a comprehensive filter that determines whether market conditions are favorable for dump trading. It analyzes multiple indicators and only enables trading when conditions meet specific criteria.

## How It Works

### Scoring System (0-100 points)

Trading is **ENABLED** when score >= 50 points.

**Point Breakdown:**
- **Volatility** (0-30 points): BTC 24h volatility
- **Trend** (0-30 points): Market direction (bullish/neutral/bearish)
- **RSI** (0-15 points): Momentum indicator
- **Volume** (0-10 points): Trading volume trend
- **Session** (0-10 points): Trading hours (US/EU/ASIA)
- **Performance** (0-5 points): Recent trade success rate

### Indicators Analyzed

#### 1. Volatility (30 points max)
- **Ideal**: 2-6% volatility → 30 points
- **Low**: 1.5-2% → 10 points
- **Very Low**: <1.5% → 0 points
- **High**: 6-8% → 20 points (caution)
- **Extreme**: >8% → 5 points (very risky)

#### 2. Trend (30 points max)
- **Bullish**: +30 points (dumps bounce well)
- **Neutral**: +15 points (moderate bounces)
- **Bearish**: 0 points (dumps may continue)

#### 3. RSI (15 points max)
- **Oversold** (<30): +15 points (prime for bounces)
- **Neutral-Low** (30-50): +10 points (good)
- **Neutral-High** (50-70): +5 points (ok)
- **Overbought** (>70): 0 points (risky)

#### 4. Volume Trend (10 points max)
- **Increasing**: +10 points (strong momentum)
- **Stable**: +5 points (normal)
- **Decreasing**: 0 points (weak liquidity)

#### 5. Trading Session (10 points max)
- **EU/US Overlap**: +10 points (peak liquidity)
- **US Session**: +8 points (high liquidity)
- **EU Session**: +7 points (good liquidity)
- **Asia Session**: +3 points (lower liquidity)

#### 6. Recent Performance (5 points max)
- **>60% win rate**: +5 points (excellent)
- **>40% win rate**: +3 points (good)
- **<40% win rate**: 0 points (poor)
- **<5 trades**: No score (insufficient data)

### Critical Blockers

Even if score >= 50, trading is **BLOCKED** if:
1. Volatility > 8% (extreme volatility)
2. Bearish trend + RSI > 50 (strong bearish momentum)
3. Win rate < 20% over 10+ recent trades (strategy not working)

## Configuration

### Environment Variables

Add to your `.env` file:

```bash
# Market Conditions Thresholds
MIN_VOLATILITY=1.5                  # Minimum required volatility (%)
IDEAL_VOLATILITY_MIN=2.0            # Ideal volatility lower bound (%)
IDEAL_VOLATILITY_MAX=6.0            # Ideal volatility upper bound (%)
EXTREME_VOLATILITY=8.0              # Extreme volatility threshold (%)
MIN_TRADE_SUCCESS_RATE=40.0         # Minimum acceptable win rate (%)
RECENT_TRADES_LOOKBACK_HOURS=24     # Hours to look back for performance
```

### Default Values

If not configured, the system uses sensible defaults:
- Minimum volatility: 1.5%
- Ideal range: 2-6%
- Extreme threshold: 8%
- Min win rate: 40%
- Lookback: 24 hours

## Testing

### 1. Test Market Conditions Analyzer

```bash
# From the dump-trading directory
cd bots/dump-trading

# Run test script
python test_market_conditions.py
```

This will:
- Connect to backend
- Fetch BTC price/volume data
- Analyze recent trades
- Display comprehensive analysis
- Show exact score and decision

### 2. Check Backend Connection

Ensure your backend is running and has historical data:

```bash
# Test backend API
curl http://localhost:5000/api/historical/BTC-USD?hours=24
```

### 3. Test with Docker

```bash
# Rebuild dump-trading container
docker-compose build dump-trading

# Restart with logs
docker-compose up dump-trading

# Watch for market conditions logs
docker-compose logs -f dump-trading | grep "MARKET CONDITIONS"
```

## How Trading is Affected

### When Conditions Are Favorable (Score >= 50)
1. Bot receives dump alert
2. ✅ Market conditions check passes
3. Position opened normally
4. Trade executes

### When Conditions Are Unfavorable (Score < 50)
1. Bot receives dump alert
2. ❌ Market conditions check fails
3. Alert logged: "Market conditions unfavorable - skipping"
4. **NO trade executed** (money saved!)
5. Telegram alert sent (only on state change)

### State Change Alerts

You'll receive Telegram alerts when:
- **Trading becomes ENABLED**: "🟢 TRADING ENABLED"
- **Trading becomes DISABLED**: "🔴 TRADING DISABLED"

These alerts include:
- Current score
- All metrics (volatility, trend, RSI, etc.)
- Reason for decision
- Recent performance stats

## Monitoring

### Real-Time Monitoring

The system checks conditions every 5 minutes and:
1. Logs analysis to console
2. Sends Telegram alerts on state changes
3. Blocks unfavorable trades automatically

### Daily Summary

Includes market conditions status:
```
📊 DAILY TRADING SUMMARY
...
──────────────────────────────
📊 MARKET CONDITIONS

Score: 65/100
Status: 🟢 ENABLED

Volatility: 3.45%
Trend: Bullish
RSI: 42.3
Volume: Increasing
Session: US

Recent: 8/12 wins (66.7%)

✅ GOOD conditions - trading enabled
```

### Logs to Watch

```bash
# Market conditions analysis
docker-compose logs -f dump-trading | grep "COMPREHENSIVE MARKET CONDITIONS"

# State changes
docker-compose logs -f dump-trading | grep "STATE CHANGE"

# Skipped trades
docker-compose logs -f dump-trading | grep "Market conditions unfavorable"
```

## Example Scenarios

### Scenario 1: Ideal Conditions ✅
- Volatility: 3.2% → 30 points
- Trend: Bullish → 30 points
- RSI: 45 → 10 points
- Volume: Increasing → 10 points
- Session: US → 8 points
- Performance: 10/15 wins (66.7%) → 5 points
- **Total: 93/100 → ENABLED**

### Scenario 2: Poor Conditions ❌
- Volatility: 0.8% → 0 points
- Trend: Bearish → 0 points
- RSI: 65 → 5 points
- Volume: Decreasing → 0 points
- Session: Asia → 3 points
- Performance: 3/10 wins (30%) → 0 points
- **Total: 8/100 → DISABLED**

### Scenario 3: Critical Blocker 🚫
- Volatility: 9.5% → 5 points (EXTREME!)
- Trend: Bullish → 30 points
- RSI: 35 → 10 points
- Others: 20 points
- **Total: 65/100 but BLOCKED due to extreme volatility**

## Benefits

1. **Protects Capital**: Avoids trading in unfavorable markets
2. **Improves Win Rate**: Only trades when conditions support bounces
3. **Reduces Losses**: Blocks trades during bearish/unstable periods
4. **Adaptive**: Learns from recent performance
5. **Transparent**: Clear logging of all decisions
6. **Automated**: No manual intervention needed

## Troubleshooting

### Issue: Always shows "Cannot determine volatility"

**Solution**: Check backend connection and historical data
```bash
curl http://localhost:5000/api/historical/BTC-USD?hours=24
```

### Issue: Always DISABLED even in good conditions

**Solution**: Check thresholds in `.env` - may be too strict
```bash
# Lower requirements temporarily for testing
MIN_VOLATILITY=1.0
MIN_TRADE_SUCCESS_RATE=30.0
```

### Issue: No recent performance data

**Solution**: This is normal initially. System needs at least 5 completed trades for performance scoring.

### Issue: Market conditions not being checked

**Solution**: Ensure `market_conditions.py` is present and imported correctly
```bash
# Check if file exists
docker-compose exec dump-trading ls -la /app/market_conditions.py

# Check logs for import errors
docker-compose logs dump-trading | grep -i "import"
```

## Advanced Customization

### Create Custom Indicator

Edit `market_conditions.py` and add your indicator:

```python
def get_my_custom_indicator(self) -> Optional[float]:
    """
    Calculate custom indicator
    Returns: float value
    """
    # Your logic here
    pass
```

Then add to scoring in `should_trade()`:

```python
# Get custom indicator
custom = self.get_my_custom_indicator()

# Add to score (0-10 points)
if custom and custom > threshold:
    score += 10
    reasons.append(f"✅ Custom indicator favorable ({custom})")
```

### Adjust Scoring Weights

Modify point allocations in `should_trade()`:

```python
# Example: Make trend more important
if trend == 'bullish':
    trend_score = 40  # Instead of 30
    ...
```

### Add New Blockers

Add to critical blockers section:

```python
# Example: Block during weekends
import datetime
if datetime.datetime.now().weekday() >= 5:
    blockers.append("Weekend - low liquidity")
```

## Summary

The Market Conditions System is a sophisticated, multi-factor filter that:
- Analyzes 6+ market indicators
- Scores conditions 0-100
- Enables trading only when score >= 50
- Blocks trades with critical issues
- Sends alerts on state changes
- Learns from recent performance

This ensures you only trade when the market truly favors your dump-trading strategy!
