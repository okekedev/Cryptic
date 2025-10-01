# Live Trading Integration - Testing Guide

## 🧪 Comprehensive Testing Suite

This guide covers all methods to test the live trading integration without risking real money.

---

## 📋 Table of Contents

1. [Automated Unit Tests](#1-automated-unit-tests)
2. [Interactive Telegram Commands](#2-interactive-telegram-commands)
3. [Manual Test Flow](#3-manual-test-flow)
4. [Verification Checklist](#4-verification-checklist)

---

## 1. Automated Unit Tests

### Run the complete test suite:

```bash
# From the project root
cd bots/telegram
python test_live_trading.py
```

### What it tests:
- ✅ Buy order execution & position creation
- ✅ Price updates without exit trigger
- ✅ Peak price tracking & trailing stop adjustment
- ✅ Trailing stop exit trigger
- ✅ Automated exit execution
- ✅ Stop loss trigger
- ✅ Hibernation mode (limit orders)
- ✅ Multi-position tracking
- ✅ Position persistence (database)
- ✅ Trading state controller & WebSocket management

### Expected output:
```
================================================================================
LIVE TRADING INTEGRATION TEST SUITE
================================================================================
✅ Test environment initialized
...
✅ PASS: Buy order executes successfully
✅ PASS: Position object created
...
================================================================================
TEST SUMMARY
================================================================================
✅ Passed: 45
❌ Failed: 0
📊 Total: 45

🎉 ALL TESTS PASSED! Live trading integration is working correctly.
```

---

## 2. Interactive Telegram Commands

### Available Test Commands:

#### `/testbuy` - Complete Buy Flow Simulation
Tests the entire buy workflow with a mock spike alert.

**Flow:**
1. Shows simulated spike alert for DOGE-USD
2. Click "🚀 Buy" button
3. Enter amount (e.g., "10" for $10)
4. See verification screen
5. Click "✅ CONFIRM PURCHASE"
6. Position card created with live updates
7. Automated exit logic activates

**What to verify:**
- ✅ Verification shows correct price, quantity, fees
- ✅ Position card displays with mode indicator (🤖 Automated)
- ✅ Trailing stop, stop loss, peak price visible
- ✅ Live price updates work

---

#### `/testprices` - Price Movement Simulator
Simulates various price scenarios to test exit conditions.

**Scenarios:**
- 📈 **+5% Price Increase** - Tests peak tracking
- 📉 **-3% Price Drop** - Tests no premature exit
- 🎢 **Climb then Drop** - Tests trailing stop trigger
- 🛑 **Hit Stop Loss** - Tests stop loss exit

**How to use:**
1. Create a position first (use `/testbuy`)
2. Run `/testprices`
3. Choose scenario from buttons
4. Watch exit logic in action

**Expected results:**
- Peak tracking: Price increases should update peak & trailing stop
- Trailing stop: Drop below trailing stop triggers exit
- Stop loss: Drop below -5% triggers immediate exit

---

#### `/testwebsocket` - WebSocket State Monitor
Shows current WebSocket feed state and priority pairs.

**What it displays:**
- Trading state (idle/active/multi_active)
- Active positions count
- Priority pairs being monitored
- Backend connection status

**Use cases:**
- Verify feed switches when position opens
- Confirm feed returns to all-pairs when position closes
- Check multi-position priority management

---

#### `/testmodes` - Mode Transition Testing
Tests position mode changes.

**Modes:**
- 🤖 **Automated** - Bot manages exits
- 💤 **Hibernating** - Limit order active, auto-exits paused
- 🔴 **Manual Exit** - User-triggered sell

**Test flow:**
1. Create position (automated mode)
2. Set limit order → enters hibernation
3. Verify automated exits paused
4. Resume or manually exit

---

#### `/testintegration` - Full Integration Test
Runs a complete automated test sequence.

**Test sequence (30 seconds):**
1. Creates test position (DOGE-USD, $10)
2. Simulates price climb (+10%)
3. Triggers trailing stop
4. Executes automated exit
5. Verifies WebSocket feed update

**Expected output:**
```
✅ INTEGRATION TEST PASSED

Results:
• Position created: ✅
• Price climb tracked: ✅
• Trailing stop triggered: ✅
• Automated exit executed: ✅
• WebSocket state updated: ✅

Trade P&L:
Entry: $0.085000
Exit: $0.093465
P&L: +$0.82 (+8.24%)

🎉 All systems working correctly!
```

---

## 3. Manual Test Flow

### Step-by-Step Testing with Minimal Risk

#### **Test 1: Buy Flow with Real API (Minimal Amount)**

```bash
# Set minimal amounts in .env
DEFAULT_POSITION_PERCENTAGE=0.1
MIN_TRADE_USD=1.0
```

1. **In Telegram:** `/test` (or wait for real spike alert)
2. **Click:** 🚀 Buy
3. **Enter:** `1` (for $1 purchase)
4. **Verify:** Check verification screen shows:
   - Product ID matches alert
   - Price is current (not stale)
   - Quantity is reasonable
   - Fees calculated correctly
5. **Confirm:** Click ✅ CONFIRM PURCHASE
6. **Observe:**
   - Position card appears
   - Shows 🤖 Automated Trading
   - Displays trailing stop, stop loss, peak

#### **Test 2: Automated Exit Logic**

**Option A: Wait for natural exit**
- Monitor position card
- Wait for price to trigger exit (could take hours)

**Option B: Use test simulator**
```
/testprices
→ Choose "🎢 Climb then Drop (Trailing Stop)"
→ Verify automated exit triggers
```

#### **Test 3: Manual Override**

1. **Create position** (use /testbuy)
2. **Click:** 📊 Set Limit Order
3. **Enter price:** (e.g., 5% above current)
4. **Confirm**
5. **Verify:**
   - Card shows 💤 Hibernating
   - Automated exits disabled
   - Button changes to "Cancel Limit Order"

#### **Test 4: Multi-Position Management**

1. Create 3 positions on different cryptos
2. Run `/testwebsocket`
3. Verify:
   - State shows "multi_active"
   - All 3 products in priority pairs
4. Close one position
5. Verify priority pairs updated (2 remaining)
6. Close all
7. Verify returns to "idle" state

---

## 4. Verification Checklist

### ✅ Core Functionality

**Buy Flow:**
- [ ] Spike alert displays correctly
- [ ] Buy button creates verification screen
- [ ] Verification shows accurate price, quantity, fees
- [ ] Confirmation executes real Coinbase API call
- [ ] Position created in database
- [ ] Position card appears in Telegram

**Automated Trading:**
- [ ] Position starts in automated mode (🤖)
- [ ] Peak price tracks correctly
- [ ] Trailing stop adjusts with price increases
- [ ] Stop loss set at -5% from entry
- [ ] Min hold time enforced (30 min)
- [ ] Trailing stop exit triggers correctly
- [ ] Stop loss exit triggers at -5%
- [ ] Exit executes real sell order
- [ ] P&L calculated correctly (with fees)

**Position Cards:**
- [ ] Card shows live price updates (every 5s)
- [ ] Unrealized P&L updates correctly
- [ ] Mode indicator displays (🤖/💤/👁️)
- [ ] Trading thresholds visible (peak, trailing, stop loss)
- [ ] Buttons change based on mode
- [ ] Archived cards show final P&L

**Manual Controls:**
- [ ] Set Limit Order enters hibernation
- [ ] Automated exits pause in hibernation
- [ ] Cancel & Resume re-enables automation
- [ ] Market Sell executes immediately
- [ ] All orders cancelled before new orders

**WebSocket Management:**
- [ ] Feed switches to priority on position open
- [ ] Only active products monitored
- [ ] Multi-position tracks all active products
- [ ] Feed returns to all-pairs on position close
- [ ] State persists across bot restarts

**Position Persistence:**
- [ ] Positions saved to database
- [ ] Positions restored on bot restart
- [ ] Mode/status preserved
- [ ] Position cards recreated
- [ ] Automated tracking resumes

---

## 🐛 Troubleshooting

### Issue: Automated exit not triggering

**Check:**
1. Position mode is "automated" (not hibernating)
2. Price actually meets exit conditions
3. Bot is running (check logs)
4. `live_position_updater` task is active

**Debug:**
```bash
docker-compose logs -f telegram-bot | grep "automated exit"
```

---

### Issue: WebSocket not switching feeds

**Check:**
1. Backend is running: `docker-compose ps`
2. Priority endpoint responding: `curl http://localhost:5000/health`
3. Trading state controller initialized

**Debug:**
```bash
# In Telegram
/testwebsocket
→ Shows current state

# Check backend logs
docker-compose logs -f backend | grep "priority"
```

---

### Issue: Position card not updating

**Check:**
1. Backend ticker endpoint: `curl http://localhost:5000/tickers/DOGE-USD`
2. Rate limiting (updates max once per 5s)
3. Position exists in `active_position_cards`

**Debug:**
```python
# In telegram_bot logs
"📊 Fetching prices for X active position(s)"
"📈 Updating {product_id} with price ${price}"
```

---

### Issue: Database errors

**Check:**
1. Database file exists: `ls -la data/telegram_bot.db`
2. Permissions correct
3. SQLite version compatible

**Fix:**
```bash
# Reset database (WARNING: loses positions)
rm data/telegram_bot.db
docker-compose restart telegram-bot
```

---

## 📊 Performance Metrics

### Expected Response Times:
- **Buy order execution:** 2-5 seconds
- **Position card update:** < 1 second
- **Automated exit trigger:** < 2 seconds (from price change)
- **WebSocket feed switch:** < 500ms
- **Position restoration:** < 2 seconds (on restart)

### Resource Usage:
- **Memory:** ~150MB per active position
- **CPU:** < 5% idle, < 15% active trading
- **Database:** ~10KB per position
- **Network:** Minimal (only priority pairs monitored)

---

## 🎯 Test Coverage Summary

| Component | Unit Tests | Integration Tests | Manual Tests |
|-----------|-----------|-------------------|--------------|
| LiveTradingManager | ✅ | ✅ | ✅ |
| TradingStateController | ✅ | ✅ | ✅ |
| Buy Flow | ✅ | ✅ | ✅ |
| Automated Exits | ✅ | ✅ | ✅ |
| Position Cards | ✅ | ✅ | ✅ |
| WebSocket Control | ✅ | ✅ | ✅ |
| Position Persistence | ✅ | ✅ | ✅ |
| Mode Transitions | ✅ | ✅ | ✅ |
| Multi-Position | ✅ | ✅ | ✅ |

**Total Test Cases:** 45+ automated, 10+ interactive, 4 manual flows

---

## 🚀 Quick Start Testing

**5-Minute Test:**
```bash
# 1. Run automated tests
cd bots/telegram && python test_live_trading.py

# 2. In Telegram, run integration test
/testintegration

# 3. Test buy flow with mock
/testbuy
→ Click Buy → Enter 10 → Confirm

# 4. Test price simulation
/testprices
→ Choose "Climb then Drop"

# Done! ✅
```

**Full Test Suite (30 minutes):**
1. Run all automated tests
2. Test each Telegram command
3. Create real $1 position (if API enabled)
4. Wait for automated exit (or simulate)
5. Test multi-position with 3 positions
6. Test hibernation mode
7. Restart bot and verify restoration

---

## 📝 Test Log Template

```markdown
## Test Session: [Date]

### Environment:
- Bot Version: [commit hash]
- Backend: Running ✅ / Failed ❌
- Coinbase API: Sandbox / Production

### Tests Run:
- [ ] Automated unit tests (45 cases)
- [ ] /testbuy flow
- [ ] /testprices scenarios
- [ ] /testintegration
- [ ] Manual $1 trade
- [ ] Multi-position test
- [ ] Hibernation mode
- [ ] Bot restart persistence

### Results:
- Passed: __/45
- Failed: __/45
- Notes: _______________

### Issues Found:
1. _______________
2. _______________

### Conclusion:
✅ Ready for production / ❌ Needs fixes
```

---

## ⚠️ Safety Reminders

1. **Never test with large amounts on production API**
2. **Use /emergency_stop before testing if nervous**
3. **Set MIN_TRADE_USD=1.0 for minimal risk**
4. **Test on cheap coins (DOGE, SHIB) first**
5. **Monitor logs during testing**
6. **Have backup access to Coinbase dashboard**
7. **Test order cancellation works before trusting automated exits**

---

## 🎉 Success Criteria

Your integration is working correctly if:

✅ All automated tests pass (45/45)
✅ /testintegration shows "ALL TESTS PASSED"
✅ Buy flow creates position with live updates
✅ Trailing stop triggers automated exit
✅ WebSocket switches to priority feed
✅ Position restores after bot restart
✅ Manual override controls work
✅ Multi-position tracking works

**If all above pass → Ready for production! 🚀**
