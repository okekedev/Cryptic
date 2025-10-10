#!/bin/bash
# Test Dump Trading Bot Setup with Market Conditions

echo "========================================"
echo "DUMP TRADING BOT - SYSTEM TEST"
echo "========================================"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test 1: Check if backend is running
echo "Test 1: Backend Health Check"
if curl -s http://localhost:5000/health > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} Backend is running"
else
    echo -e "${RED}✗${NC} Backend is not running"
    echo "   Run: docker-compose up backend"
    exit 1
fi
echo ""

# Test 2: Check BTC historical data
echo "Test 2: Historical Data Check"
BTC_DATA=$(curl -s http://localhost:5000/api/historical/BTC-USD?hours=24)
if [ ! -z "$BTC_DATA" ] && [ "$BTC_DATA" != "[]" ]; then
    echo -e "${GREEN}✓${NC} BTC historical data available"
else
    echo -e "${RED}✗${NC} No BTC historical data"
    echo "   Backend needs time to collect data"
fi
echo ""

# Test 3: Check if drop-detector is running
echo "Test 3: Drop Detector Status"
if docker-compose ps | grep -q "bot-drop-detector.*Up"; then
    echo -e "${GREEN}✓${NC} Drop detector is running"
else
    echo -e "${RED}✗${NC} Drop detector is not running"
    echo "   Run: docker-compose up -d drop-detector"
fi
echo ""

# Test 4: Check if dump-trading is running
echo "Test 4: Dump Trading Bot Status"
if docker-compose ps | grep -q "bot-dump-trading.*Up"; then
    echo -e "${GREEN}✓${NC} Dump trading bot is running"
else
    echo -e "${RED}✗${NC} Dump trading bot is not running"
    echo "   Run: docker-compose up -d dump-trading"
fi
echo ""

# Test 5: Check market conditions module
echo "Test 5: Market Conditions Module"
if docker-compose exec -T dump-trading ls /app/market_conditions.py > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} market_conditions.py is present"
else
    echo -e "${RED}✗${NC} market_conditions.py not found"
    echo "   Rebuild container: docker-compose build dump-trading"
fi
echo ""

# Test 6: Check recent logs for market conditions
echo "Test 6: Market Conditions Integration"
if docker-compose logs dump-trading 2>/dev/null | grep -q "Market Conditions: ENABLED"; then
    echo -e "${GREEN}✓${NC} Market conditions analyzer is active"
else
    echo -e "${YELLOW}⚠${NC} Market conditions may not be initialized yet"
    echo "   Check logs: docker-compose logs dump-trading"
fi
echo ""

# Test 7: Check databases exist
echo "Test 7: Database Check"
if [ -f "./data/dump_trading.db" ]; then
    echo -e "${GREEN}✓${NC} dump_trading.db exists"
else
    echo -e "${YELLOW}⚠${NC} dump_trading.db not found (will be created on first run)"
fi

if [ -f "./data/drop_detector.db" ]; then
    echo -e "${GREEN}✓${NC} drop_detector.db exists"
else
    echo -e "${YELLOW}⚠${NC} drop_detector.db not found (will be created on first run)"
fi
echo ""

echo "========================================"
echo "RECOMMENDATIONS"
echo "========================================"
echo ""

# Check .env settings
echo "Environment Configuration:"
if grep -q "AUTO_TRADE=yes" .env 2>/dev/null; then
    echo -e "${YELLOW}⚠${NC} AUTO_TRADE is ENABLED - real trading active!"
else
    echo -e "${GREEN}✓${NC} AUTO_TRADE is disabled - safe mode"
fi

if grep -q "COINBASE_API_KEY" .env 2>/dev/null; then
    echo -e "${GREEN}✓${NC} Coinbase API keys configured"
else
    echo -e "${YELLOW}⚠${NC} No Coinbase API keys (needed for live trading)"
fi

if grep -q "TELEGRAM_BOT_TOKEN" .env 2>/dev/null; then
    echo -e "${GREEN}✓${NC} Telegram bot configured"
else
    echo -e "${YELLOW}⚠${NC} No Telegram bot (alerts disabled)"
fi
echo ""

echo "Next Steps:"
echo "1. Run market conditions test:"
echo "   docker-compose exec dump-trading python test_market_conditions.py"
echo ""
echo "2. Watch live logs:"
echo "   docker-compose logs -f dump-trading"
echo ""
echo "3. Monitor market conditions:"
echo "   docker-compose logs -f dump-trading | grep 'MARKET CONDITIONS'"
echo ""
echo "4. Test with a simulated dump:"
echo "   curl -X POST http://localhost:8080/webhook -H 'Content-Type: application/json' -d '{\"symbol\":\"BTC-USD\",\"spike_type\":\"dump\",\"pct_change\":-5.0,\"new_price\":60000.0}'"
echo ""

echo "========================================"
echo "TEST COMPLETE"
echo "========================================"
