# Dump Trading Bot - Setup Guide

**Last Updated:** October 2, 2025

---

## 🎯 Quick Start

### 1. Prerequisites
- Docker and Docker Compose installed
- Coinbase Advanced Trade account with API keys
- Telegram bot token and chat ID
- Funding in your Coinbase USD wallet

### 2. Configuration

Edit the `.env` file:

```bash
# Coinbase API Credentials
COINBASE_API_KEY=your_api_key_here
COINBASE_SIGNING_KEY=-----BEGIN EC PRIVATE KEY-----\n...\n-----END EC PRIVATE KEY-----\n

# Telegram Notifications
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Trading Settings
AUTO_TRADE=no  # Set to "yes" when ready to trade live
PRICE_SPIKE_THRESHOLD=4.5
PRICE_WINDOW_MINUTES=5
```

### 3. Deploy

```bash
# Start all services
docker-compose up --build -d

# View logs
docker-compose logs -f dump-trading

# Stop all services
docker-compose down
```

---

## 📊 Bot Configuration

### Position Sizing
- **Position Size:** 25% of total capital per trade
- **Max Concurrent Positions:** 4
- **Strategy:** Mean reversion on dumps

### Entry Rules
- **Dump Threshold:** 4.5% drop in 5 minutes
- **WebSocket:** Real-time price monitoring

### Exit Rules
- **Min Profit:** +2.0%
- **Target Profit:** +4.0%
- **Stop Loss:** -3.0%
- **Trailing Stop:** 0.7% from peak
- **Min Hold Time:** 5 minutes
- **Max Hold Time:** 15 minutes

### Notifications
- ✅ Buy notification when position opened
- ✅ Sell notification when position closed (with P&L)
- ✅ Daily summary at 10:30 PM

---

## 🔒 Safety Features

### AUTO_TRADE Flag
The bot has a safety mechanism with the `AUTO_TRADE` environment variable:

- **AUTO_TRADE=no** (default): Bot sends Telegram alerts but does NOT execute trades
- **AUTO_TRADE=yes**: Bot automatically executes trades on Coinbase

**Important:** Test with `AUTO_TRADE=no` first to see alerts before enabling live trading!

### Testing Before Live Trading

1. **Start with AUTO_TRADE=no**
   ```bash
   # In .env file
   AUTO_TRADE=no
   ```

2. **Watch for alerts in Telegram**
   - You'll receive dump alerts
   - No actual trades will be executed
   - Verify the bot is detecting good opportunities

3. **Enable live trading when ready**
   ```bash
   # In .env file
   AUTO_TRADE=yes
   ```

4. **Rebuild and restart**
   ```bash
   docker-compose down
   docker-compose up --build -d
   ```

---

## 💰 Capital Management

### Balance Tracking
- Bot fetches live USD balance from Coinbase at startup
- Uses 25% of total capital per trade
- Supports up to 4 concurrent positions
- Balance automatically syncs with Coinbase

### Example with $10,000
- **Position 1:** $2,500 (25%)
- **Position 2:** $2,500 (25%)
- **Position 3:** $2,500 (25%)
- **Position 4:** $2,500 (25%)
- **Total Deployed:** $10,000 max

---

## 📈 Backtest Results

**Date:** October 2, 2025
**Strategy:** Refined DUMP (Mean Reversion)
**Test Data:** 56,315 candles across 300 cryptocurrencies

| Metric | Value |
|--------|-------|
| **Initial Capital** | $10,000 |
| **Final Capital** | $11,453 |
| **Total P&L** | +$1,453 |
| **ROI** | **+14.53%** |
| **Total Trades** | 12 |
| **Win Rate** | 58.3% |
| **Avg P&L per Trade** | +1.19% |

---

## 🔧 Monitoring

### View Logs
```bash
# All services
docker-compose logs -f

# Just dump-trading bot
docker-compose logs -f dump-trading

# Just Telegram bot
docker-compose logs -f telegram-bot
```

### Check Running Services
```bash
docker-compose ps
```

### Restart Specific Service
```bash
docker-compose restart dump-trading
```

---

## ⚠️ Important Notes

1. **Start Small:** Begin with a small amount to test the system
2. **Monitor Daily:** Check Telegram for the 10:30 PM summary
3. **Review Trades:** Each trade notification shows entry, exit, and P&L
4. **Coinbase Fees:** Real trades incur Coinbase fees (~0.6% taker, ~0.4% maker)
5. **24/7 Operation:** Bot runs continuously - ensure stable internet/power

---

## 🆘 Troubleshooting

### Bot Not Trading
- Check `AUTO_TRADE=yes` in `.env`
- Verify Coinbase API keys are correct
- Check USD balance in Coinbase
- View logs: `docker-compose logs dump-trading`

### No Telegram Notifications
- Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
- Check telegram-bot service is running
- Test webhook: `docker-compose logs telegram-bot`

### WebSocket Issues
- Backend service must be running
- Check `docker-compose logs backend`
- Verify spike-detector is running

---

## 📞 Support

- **Logs:** `docker-compose logs -f`
- **Status:** `docker-compose ps`
- **Database:** Located in `./data/dump_trading.db`
- **Trade Log:** Check Telegram or database

---

**Status:** ✅ Ready for Live Trading (when AUTO_TRADE=yes)
