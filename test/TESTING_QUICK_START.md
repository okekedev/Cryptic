# 🧪 Live Trading - Quick Test Guide

## 🚀 30-Second Test

```bash
# Terminal
cd bots/telegram && python test_live_trading.py
```

**Expected:** ✅ ALL TESTS PASSED (45/45)

---

## 📱 Telegram Test Commands

| Command | What it Does | Time |
|---------|--------------|------|
| `/testintegration` | Full automated test | 30s |
| `/testbuy` | Test buy flow with mock alert | 2min |
| `/testprices` | Simulate price movements | 1min |
| `/testwebsocket` | Check WebSocket state | 10s |
| `/testmodes` | Test mode transitions | 1min |

---

## ✅ Quick Verification (5 Minutes)

### 1. Run Automated Tests
```bash
python bots/telegram/test_live_trading.py
```
✅ Should show: `🎉 ALL TESTS PASSED!`

### 2. Run Telegram Integration Test
```
/testintegration
```
✅ Should show: `✅ INTEGRATION TEST PASSED`

### 3. Test Buy Flow
```
/testbuy
→ Click "🚀 Buy"
→ Enter "10"
→ Click "✅ CONFIRM PURCHASE"
```
✅ Should show: Position card with live updates

### 4. Test Automated Exit
```
/testprices
→ Choose "🎢 Climb then Drop"
```
✅ Should show: Exit triggered with P&L

### 5. Test WebSocket Switch
```
/testwebsocket
```
✅ Should show: State = "active", Priority pairs = [your position]

---

## 🎯 What Each Test Validates

### `/testintegration` validates:
- ✅ Buy order execution
- ✅ Position creation
- ✅ Peak price tracking
- ✅ Trailing stop trigger
- ✅ Automated exit
- ✅ WebSocket feed management

### `/testbuy` validates:
- ✅ Spike alert format
- ✅ Buy button flow
- ✅ Verification screen
- ✅ Amount input
- ✅ Position card creation
- ✅ Live updates

### `/testprices` validates:
- ✅ Price update logic
- ✅ Peak tracking
- ✅ Trailing stop calculation
- ✅ Stop loss trigger
- ✅ Exit conditions

### `/testwebsocket` validates:
- ✅ Trading state tracking
- ✅ Priority feed switching
- ✅ Multi-position support
- ✅ Backend connectivity

### `/testmodes` validates:
- ✅ Automated mode
- ✅ Hibernation mode
- ✅ Mode transitions
- ✅ Manual overrides

---

## 🔍 Visual Test Checklist

**Position Card Should Show:**
```
📊 ACTIVE POSITION: DOGE-USD
🤖 Automated Trading

Entry: $0.085000 @ 12:00:00
Quantity: 117.64705882
Cost Basis: $10.06

Current: $0.087000 📈
Unrealized P&L: +$0.18 (+1.79%)

🎯 Trading Thresholds:
Peak: $0.087000
Trailing Stop: $0.085695
Stop Loss: $0.080750

[📊 Set Limit Order] [💰 Sell at Market]
[🔄 Refresh]
```

**After Exit:**
```
📊 ARCHIVED POSITION: DOGE-USD
Closed: Trailing stop hit

Entry: $0.085000
Exit: $0.093465
Peak: $0.095000
Held: 45.2 minutes

Final P&L: +$0.82 (+8.24%) 📈
```

---

## ❌ Common Issues & Fixes

### Issue: Tests fail with "module not found"
```bash
# Fix: Install dependencies
pip install -r requirements.txt
```

### Issue: Telegram commands not working
```bash
# Fix: Restart bot
docker-compose restart telegram-bot
```

### Issue: WebSocket not switching
```bash
# Fix: Check backend
docker-compose ps
docker-compose logs backend | grep priority
```

### Issue: Position card not updating
```bash
# Fix: Check backend tickers
curl http://localhost:5000/tickers/DOGE-USD
```

---

## 🎬 Demo Flow (Show Someone)

**1. Show spike alert simulation:**
```
/testbuy
```
*"This simulates a spike alert with buy button"*

**2. Execute buy:**
*Click Buy → Enter 10 → Confirm*
*"Bot creates position and starts automated tracking"*

**3. Show live updates:**
*Wait 5 seconds*
*"Position card updates with live price every 5 seconds"*

**4. Trigger automated exit:**
```
/testprices
→ Choose "Climb then Drop"
```
*"Bot detects trailing stop and auto-sells"*

**5. Show WebSocket switch:**
```
/testwebsocket
```
*"Feed switches to priority mode when trading, back to all-pairs when idle"*

---

## 📊 Success Metrics

| Metric | Target | Check |
|--------|--------|-------|
| Unit tests passed | 45/45 | ✅ |
| Integration test | PASSED | ✅ |
| Buy flow time | < 5s | ✅ |
| Position card update | < 1s | ✅ |
| Exit trigger time | < 2s | ✅ |
| WebSocket switch | < 500ms | ✅ |
| Bot restart recovery | < 2s | ✅ |

---

## 🚦 Ready for Production?

**Run this final check:**

```bash
# 1. All automated tests pass
python bots/telegram/test_live_trading.py
# Expected: ✅ ALL TESTS PASSED

# 2. Integration test passes
/testintegration
# Expected: ✅ INTEGRATION TEST PASSED

# 3. Buy flow works
/testbuy → Buy → 10 → Confirm
# Expected: Position card appears

# 4. Exit logic works
/testprices → Climb then Drop
# Expected: Automated exit triggers

# 5. State management works
/testwebsocket
# Expected: Shows correct state
```

**If all ✅ → You're ready! 🎉**

---

## 🔗 Full Documentation

For detailed testing procedures, see:
- **[TESTING_GUIDE.md](TESTING_GUIDE.md)** - Complete testing documentation
- **[test_live_trading.py](bots/telegram/test_live_trading.py)** - Automated test suite
- **[test_commands.py](bots/telegram/test_commands.py)** - Interactive test commands

---

## 💡 Pro Tips

1. **Test on cheap coins first** (DOGE, SHIB) - costs pennies
2. **Use /emergency_stop** before production testing
3. **Set MIN_TRADE_USD=1.0** for minimal risk
4. **Watch logs during tests** - `docker-compose logs -f telegram-bot`
5. **Test with multiple positions** to verify WebSocket management
6. **Restart bot after major changes** to test persistence

---

## 📞 Need Help?

**Check logs:**
```bash
docker-compose logs -f telegram-bot | grep -E "ERROR|WARN|automated exit"
```

**Debug specific component:**
```bash
# LiveTradingManager
docker-compose logs telegram-bot | grep "LiveTradingManager"

# WebSocket state
docker-compose logs telegram-bot | grep "Trading state"

# Position updates
docker-compose logs telegram-bot | grep "Updating.*with price"
```

**Reset everything:**
```bash
docker-compose down
rm data/telegram_bot.db data/spike_alerts.db
docker-compose up -d
```

---

## 🎯 Final Checklist

Before going live:

- [ ] All automated tests pass (45/45)
- [ ] /testintegration shows PASSED
- [ ] Tested with $1 real trade successfully
- [ ] Automated exit triggered correctly
- [ ] WebSocket switches to priority feed
- [ ] Position survived bot restart
- [ ] Manual override controls work
- [ ] Multi-position tracking works
- [ ] Emergency stop tested and works
- [ ] Logs show no errors

**All checked? → GO LIVE! 🚀**
