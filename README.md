# Optimized Crypto Trading Bot

Automated trading bot using a proven, data-driven strategy optimized through grid search on 1.4M candles.

## 📊 Strategy Performance

**Validated on 7 days (Oct 17-23, 2025):**
- ✅ **Return:** +30.42%
- ✅ **Win Rate:** 82.1%
- ✅ **Trades:** 329 (47/day)
- ✅ **Capital:** $200 → $260.83

## 🎯 Strategy Parameters

- **Entry:** 3-Candle accumulation ≤ -6% + RSI < 35
- **Exit:** +5% target or 300 min timeout
- **Position Size:** $20 per trade
- **Max Concurrent:** 20 positions
- **Fees:** 1.8% total (1.2% entry + 0.6% exit)

## 🚀 Quick Start

### Prerequisites
- Docker & Docker Compose
- Coinbase Advanced Trade API keys
- Polygon.io API key (for market data)

### Environment Setup

Create `.env` file:
```bash
# Coinbase Advanced Trade
COINBASE_API_KEY=your_key
COINBASE_API_SECRET=your_secret

# Polygon.io
POLYGON_API_KEY=your_polygon_key

# Trading Config
PROVEN_AUTO_TRADE=yes
PROVEN_INITIAL_CAPITAL=200

# Email Notifications (Optional)
GMAIL_ADDRESS=your_email@gmail.com
GMAIL_APP_PASSWORD=your_16_char_app_password
```

**Gmail Setup (Optional - for daily P&L reports at 8 PM CST):**
1. Go to your Google Account → Security
2. Enable 2-Step Verification
3. Generate App Password for "Mail"
4. Add credentials to `.env` file

### Run

```bash
docker-compose -f docker-compose.proven.yml up -d --build
```

### Monitor

```bash
# View logs
docker logs proven-trading-bot -f

# Check health
curl http://localhost:8000/health

# View positions
curl http://localhost:8000/positions

# View stats
curl http://localhost:8000/stats
```

## 📁 Project Structure

```
.
├── docker-compose.proven.yml    # Production deployment config
├── OPTIMIZED_STRATEGY_DEPLOYED.md  # Strategy documentation
├── websocket-service/
│   ├── main_proven_polygon.py   # FastAPI app entry point
│   ├── proven_dump_trader.py    # Core trading strategy
│   ├── polygon_rest_client.py   # Market data (Polygon REST API)
│   ├── coinbase_client.py       # Trade execution (Coinbase)
│   ├── Dockerfile               # Container definition
│   └── requirements.txt         # Python dependencies
├── data/                        # SQLite databases (trades, alerts)
└── archive/                     # Historical tests & old code
```

## 🔧 Configuration

All strategy parameters are in `websocket-service/proven_dump_trader.py`:

```python
# Entry conditions
DUMP_THRESHOLD = -0.06    # -6% accumulated over 3 candles
CANDLE_LOOKBACK = 3       # Number of candles to accumulate
RSI_THRESHOLD = 35        # RSI must be below 35

# Exit conditions
EXIT_TARGET = 0.05        # +5% profit target
MAX_HOLD_MINUTES = 300    # 5 hours max hold
```

## 📈 API Endpoints

- `GET /` - Health check
- `GET /health` - Detailed health status
- `GET /positions` - Open positions
- `GET /stats` - Trading statistics

## ⚠️ Risk Disclaimer

- **Past performance does not guarantee future results**
- Strategy validated on historical data only
- Live trading involves real money risk
- Always start with capital you can afford to lose
- Monitor bot performance closely

## 🧪 Development & Testing

All test scripts and historical analysis are archived in `archive/`:
- `archive/test_scripts/` - Backtesting scripts
- `archive/backtest_data/` - Historical CSV data
- `archive/old_reports/` - Analysis reports

## 📝 License

Private project - All rights reserved
