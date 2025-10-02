# Project Cleanup Guide

## ✅ Essential Services (DO NOT REMOVE)

### Core Trading Stack
```
bots/
├── dump-trading/          # Main trading bot - REQUIRED
│   ├── dump_trading_bot.py
│   ├── coinbase_client.py
│   ├── Dockerfile
│   └── requirements.txt
│
├── spike-detector/        # Detects dumps - REQUIRED
│   ├── spike_bot.py
│   ├── Dockerfile
│   └── requirements.txt
│
├── telegram/              # Sends notifications - REQUIRED
│   ├── telegram_bot.py
│   ├── trading_manager.py
│   ├── socket_server.py
│   ├── Dockerfile
│   └── requirements.txt
│
└── backend/               # WebSocket price feeds - REQUIRED
    ├── src/
    ├── Dockerfile
    └── package.json
```

### Configuration Files
```
.env                       # API keys and settings - REQUIRED
docker-compose.yml         # Service orchestration - REQUIRED
OPTIMIZED_SETTINGS.md      # Strategy parameters - KEEP FOR REFERENCE
SETUP.md                   # Deployment guide - KEEP FOR REFERENCE
DEPLOYMENT_STATUS.md       # Status and checklist - KEEP FOR REFERENCE
```

### Data Directories
```
data/                      # SQLite databases and logs - REQUIRED
├── dump_trading.db        # Trade history
├── spike_alerts.db        # Dump detections
└── telegram_bot.db        # Bot state
```

---

## ❌ Unused Services (SAFE TO REMOVE)

### Deprecated Bots
```bash
# These are commented out in docker-compose.yml and not used

bots/paper-trading/        # Old paper trading bot (unprofitable pump strategy)
bots/trading/              # Old trading bot (replaced by dump-trading)
bots/logs-monitor/         # Optional monitoring (not essential)
```

### Optional Frontend
```
frontend/                  # Web dashboard (optional, not needed for trading)
```

### Cleanup Scripts
```bash
# Remove unused bot directories
rm -rf bots/paper-trading
rm -rf bots/trading
rm -rf bots/logs-monitor

# Remove frontend if not using web dashboard
rm -rf frontend
```

---

## 📦 Files Currently Being Used

### Dump Trading Bot
- `bots/dump-trading/dump_trading_bot.py` - Main bot logic
- `bots/dump-trading/coinbase_client.py` - Coinbase API wrapper
- `bots/dump-trading/Dockerfile` - Container definition
- `bots/dump-trading/requirements.txt` - Python dependencies

### Spike Detector
- `bots/spike-detector/spike_bot.py` - Monitors for 4.5%+ dumps
- `bots/spike-detector/Dockerfile` - Container definition
- `bots/spike-detector/requirements.txt` - Python dependencies

### Telegram Bot
- `bots/telegram/telegram_bot.py` - Main bot handler
- `bots/telegram/trading_manager.py` - Coinbase API integration
- `bots/telegram/socket_server.py` - WebSocket server for alerts
- `bots/telegram/Dockerfile` - Container definition
- `bots/telegram/requirements.txt` - Python dependencies

### Backend
- `backend/src/` - WebSocket server for price feeds
- `backend/Dockerfile` - Container definition
- `backend/package.json` - Node.js dependencies

### Configuration
- `.env` - Environment variables (API keys, strategy params)
- `docker-compose.yml` - Services configuration
- `OPTIMIZED_SETTINGS.md` - Strategy parameters and backtest results
- `SETUP.md` - Deployment instructions
- `DEPLOYMENT_STATUS.md` - Readiness checklist

---

## 🔧 Safe Cleanup Commands

### Option 1: Keep Everything (Recommended)
```bash
# Don't remove anything yet - wait until you're sure
# All unused services are already disabled in docker-compose.yml
```

### Option 2: Remove Unused Bots Only
```bash
cd "C:\Users\Christian Okeke\bot\bot"

# Remove deprecated bots (keep frontend for now)
rm -rf bots/paper-trading
rm -rf bots/trading
rm -rf bots/logs-monitor

# Verify essential services still exist
ls -la bots/dump-trading
ls -la bots/spike-detector
ls -la bots/telegram
ls -la backend
```

### Option 3: Minimal Setup (Remove Everything Unused)
```bash
cd "C:\Users\Christian Okeke\bot\bot"

# Remove all unused directories
rm -rf bots/paper-trading
rm -rf bots/trading
rm -rf bots/logs-monitor
rm -rf frontend

# Remove old scripts and unused files
rm -rf scripts/backtest*.js
rm -rf scripts/reverse_engineer*.py
rm -rf scripts/fetch_historical_data.py

# Keep only essential documentation
# (OPTIMIZED_SETTINGS.md, SETUP.md, DEPLOYMENT_STATUS.md)
```

---

## 📋 Current Services in docker-compose.yml

### ✅ Active Services
```yaml
backend           # WebSocket price feeds
telegram-bot      # Notifications
spike-detector    # Dump detection
dump-trading      # Main trading bot
ngrok             # Public URL (optional)
```

### ❌ Commented Out (Already Disabled)
```yaml
# paper-trading   - Old pump strategy bot (disabled)
# trading-bot     - Replaced by telegram TradingManager (disabled)
# log-monitor     - Optional monitoring (disabled)
```

---

## 🎯 Recommended Action

**Do NOT remove anything yet!** Here's why:

1. **All unused services are already disabled** in `docker-compose.yml`
2. **No resource waste** - Disabled services don't consume CPU/memory
3. **Keep for reference** - Old scripts/bots might have useful code later
4. **Storage is cheap** - A few MB of unused code won't hurt

### When to Clean Up

Only remove files after:
- ✅ Bot has been running successfully for 1+ weeks
- ✅ You've verified you don't need any old code
- ✅ You've backed up the project somewhere safe
- ✅ You understand what each directory does

---

## 🔒 Never Delete These

```
bots/dump-trading/      # YOUR TRADING BOT
bots/spike-detector/    # DUMP DETECTION
bots/telegram/          # NOTIFICATIONS
backend/                # PRICE FEEDS
.env                    # API KEYS
docker-compose.yml      # SERVICE CONFIG
data/                   # TRADE HISTORY
```

If you delete any of these, the bot will stop working!

---

## ✅ Summary

**Current State:** Clean and ready for production
**Unused Services:** Already disabled in docker-compose.yml
**Disk Space:** ~500MB total (mostly Docker images)
**Recommendation:** Keep everything for now, cleanup later after 1+ week of successful trading

**The project is production-ready as-is. No cleanup required before deployment!**
