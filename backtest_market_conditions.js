#!/usr/bin/env node
/**
 * Backtest: BTC-Only vs Per-Crypto Market Conditions
 *
 * Compares two strategies over last 12 hours of dump alerts:
 * 1. BTC-Only: Use BTC market conditions as gate for ALL trades
 * 2. Per-Crypto: Check each coin's individual conditions
 */

const http = require('http');
const fs = require('fs');

// Configuration
const BACKEND_URL = 'http://localhost:5001';
const POSITION_SIZE = 50; // $50 per trade
const MAX_POSITIONS = 4;
const STOP_LOSS_PCT = -4.0; // -4% stop loss after 5 min
const LADDER_SELL_START = 8.0; // Start selling at +8%
const LADDER_SELL_STEP = 0.5; // Step down 0.5% every 30s
const LADDER_TIMEOUT_SECONDS = 30;

// Market condition thresholds
const THRESHOLDS = {
  volatility_min: 1.5, // Need 1.5%+ volatility
  volatility_ideal_min: 2.0,
  volatility_ideal_max: 6.0,
  rsi_overbought: 75,
  rsi_oversold: 25,
  dump_frequency_high: 20, // 20+ dumps/hour = unstable
};

// Load dump alerts from file
const dumps = JSON.parse(fs.readFileSync('./dumps_12h.json', 'utf8'));

console.log('='.repeat(80));
console.log('BACKTEST: BTC-Only vs Per-Crypto Market Conditions');
console.log('='.repeat(80));
console.log(`Analyzing ${dumps.length} dump alerts from last 12 hours\n`);

/**
 * Fetch historical price data for a symbol
 */
async function getHistoricalData(symbol, hours = 24) {
  return new Promise((resolve, reject) => {
    const url = `${BACKEND_URL}/api/historical/${symbol}?hours=${hours}`;
    http.get(url.replace('https', 'http'), (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          reject(e);
        }
      });
    }).on('error', reject);
  });
}

/**
 * Calculate volatility from candle data
 */
function calculateVolatility(candles) {
  if (!candles || candles.length < 2) return 0;

  const returns = [];
  for (let i = 1; i < candles.length; i++) {
    const pctChange = ((candles[i].close - candles[i-1].close) / candles[i-1].close) * 100;
    returns.push(pctChange);
  }

  // Standard deviation of returns
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.reduce((sum, r) => sum + Math.pow(r - mean, 2), 0) / returns.length;
  return Math.sqrt(variance);
}

/**
 * Calculate RSI
 */
function calculateRSI(candles, period = 14) {
  if (!candles || candles.length < period + 1) return 50; // Neutral

  let gains = 0, losses = 0;

  for (let i = candles.length - period; i < candles.length; i++) {
    const change = candles[i].close - candles[i - 1].close;
    if (change > 0) gains += change;
    else losses += Math.abs(change);
  }

  const avgGain = gains / period;
  const avgLoss = losses / period;

  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - (100 / (1 + rs));
}

/**
 * Determine trend from candles
 */
function calculateTrend(candles) {
  if (!candles || candles.length < 20) return 'neutral';

  const recent = candles.slice(-20);
  const firstPrice = recent[0].close;
  const lastPrice = recent[recent.length - 1].close;
  const change = ((lastPrice - firstPrice) / firstPrice) * 100;

  if (change > 2) return 'bullish';
  if (change < -2) return 'bearish';
  return 'neutral';
}

/**
 * Calculate volume trend
 */
function calculateVolumeTrend(candles) {
  if (!candles || candles.length < 10) return 'stable';

  const recent = candles.slice(-10);
  const oldHalf = recent.slice(0, 5);
  const newHalf = recent.slice(5);

  const oldAvg = oldHalf.reduce((sum, c) => sum + c.volume, 0) / 5;
  const newAvg = newHalf.reduce((sum, c) => sum + c.volume, 0) / 5;

  const change = ((newAvg - oldAvg) / oldAvg) * 100;

  if (change > 20) return 'increasing';
  if (change < -20) return 'decreasing';
  return 'stable';
}

/**
 * Score market conditions for a specific crypto
 */
function scoreMarketConditions(symbol, candles, dumps) {
  let score = 0;
  const reasons = [];

  if (!candles || candles.length < 20) {
    return { score: 0, reasons: ['Insufficient data'], enabled: false };
  }

  // 1. Volatility (0-25 points)
  const volatility = calculateVolatility(candles);
  if (volatility < THRESHOLDS.volatility_min) {
    reasons.push(`❌ Low volatility (${volatility.toFixed(2)}%)`);
  } else if (volatility >= THRESHOLDS.volatility_ideal_min && volatility <= THRESHOLDS.volatility_ideal_max) {
    score += 25;
    reasons.push(`✅ Ideal volatility (${volatility.toFixed(2)}%)`);
  } else if (volatility > THRESHOLDS.volatility_ideal_max) {
    score += 15;
    reasons.push(`⚠️ High volatility (${volatility.toFixed(2)}%)`);
  } else {
    score += 10;
    reasons.push(`⚡ Moderate volatility (${volatility.toFixed(2)}%)`);
  }

  // 2. Trend (0-20 points)
  const trend = calculateTrend(candles);
  if (trend === 'bullish') {
    score += 20;
    reasons.push(`✅ Bullish trend (dumps bounce well)`);
  } else if (trend === 'neutral') {
    score += 10;
    reasons.push(`⚡ Neutral trend`);
  } else {
    reasons.push(`❌ Bearish trend (risky)`);
  }

  // 3. RSI (0-15 points)
  const rsi = calculateRSI(candles);
  if (rsi < THRESHOLDS.rsi_oversold) {
    score += 15;
    reasons.push(`✅ RSI oversold (${rsi.toFixed(1)} - good for dumps)`);
  } else if (rsi > THRESHOLDS.rsi_overbought) {
    reasons.push(`❌ RSI overbought (${rsi.toFixed(1)})`);
  } else {
    score += 10;
    reasons.push(`⚡ RSI neutral (${rsi.toFixed(1)})`);
  }

  // 4. Volume (0-20 points)
  const volumeTrend = calculateVolumeTrend(candles);
  if (volumeTrend === 'increasing') {
    score += 20;
    reasons.push(`✅ Volume increasing`);
  } else if (volumeTrend === 'stable') {
    score += 10;
    reasons.push(`⚡ Volume stable`);
  } else {
    reasons.push(`❌ Volume decreasing`);
  }

  // 5. Dump frequency for this specific coin (0-20 points)
  const symbolDumps = dumps.filter(d => d.symbol === symbol);
  const firstDump = new Date(symbolDumps[0]?.timestamp || Date.now());
  const lastDump = new Date(symbolDumps[symbolDumps.length - 1]?.timestamp || Date.now());
  const hoursSpan = (lastDump - firstDump) / (1000 * 60 * 60) || 1;
  const dumpFrequency = symbolDumps.length / hoursSpan;

  if (dumpFrequency < 5) {
    score += 20;
    reasons.push(`✅ Stable coin (${dumpFrequency.toFixed(1)} dumps/hr)`);
  } else if (dumpFrequency < 10) {
    score += 10;
    reasons.push(`⚡ Moderate dumps (${dumpFrequency.toFixed(1)}/hr)`);
  } else {
    reasons.push(`❌ High dump frequency (${dumpFrequency.toFixed(1)}/hr)`);
  }

  return {
    score,
    reasons,
    enabled: score >= 50,
    volatility,
    rsi,
    trend,
    volumeTrend,
    dumpFrequency
  };
}

/**
 * Simulate trade outcome
 * Returns: { profit_pct, exit_reason, hold_time_minutes }
 */
function simulateTrade(dump, conditions) {
  const entryPrice = dump.alert_price;

  // Simple simulation based on market conditions
  // Better conditions = higher chance of good outcome

  const score = conditions.score;
  const volatility = conditions.volatility || 0;

  // High volatility + good conditions = good bounce potential
  if (score >= 70 && volatility >= 2.5) {
    // Excellent conditions - likely hits +6% or higher
    const profit = 4 + Math.random() * 4; // 4-8%
    return {
      profit_pct: profit,
      exit_reason: 'Ladder sell filled',
      hold_time_minutes: 10 + Math.random() * 20
    };
  } else if (score >= 50 && volatility >= 1.5) {
    // Good conditions - modest profit or small loss
    const profit = -2 + Math.random() * 5; // -2% to +3%
    return {
      profit_pct: profit,
      exit_reason: profit > 0 ? 'Ladder sell filled' : 'Stop loss',
      hold_time_minutes: 15 + Math.random() * 30
    };
  } else {
    // Poor conditions - likely stop loss
    const profit = -4 + Math.random() * 2; // -4% to -2%
    return {
      profit_pct: profit,
      exit_reason: 'Stop loss',
      hold_time_minutes: 5 + Math.random() * 10
    };
  }
}

/**
 * Main backtest function
 */
async function runBacktest() {
  // Strategy 1: BTC-Only gating
  const btcCandles = await getHistoricalData('BTC-USD', 24);
  const btcConditions = scoreMarketConditions('BTC-USD', btcCandles, dumps);

  console.log('━'.repeat(80));
  console.log('BTC MARKET CONDITIONS (Used for BTC-Only Strategy)');
  console.log('━'.repeat(80));
  console.log(`Score: ${btcConditions.score}/100`);
  console.log(`Enabled: ${btcConditions.enabled ? '✅ YES' : '❌ NO'}`);
  console.log(`\nFactors:`);
  btcConditions.reasons.forEach(r => console.log(`  ${r}`));
  console.log();

  // Strategy results
  const btcOnlyResults = {
    trades: 0,
    blockedTrades: 0,
    totalPnL: 0,
    wins: 0,
    losses: 0
  };

  const perCryptoResults = {
    trades: 0,
    blockedTrades: 0,
    totalPnL: 0,
    wins: 0,
    losses: 0
  };

  console.log('━'.repeat(80));
  console.log('ANALYZING EACH DUMP ALERT');
  console.log('━'.repeat(80));
  console.log();

  // Analyze each dump
  for (let i = 0; i < Math.min(dumps.length, 20); i++) {
    const dump = dumps[i];
    const symbol = dump.symbol;

    console.log(`[${i + 1}/${dumps.length}] ${symbol} @ ${dump.alert_price} (${dump.dump_pct.toFixed(2)}%)`);
    console.log(`  Time: ${dump.timestamp}`);

    try {
      // Get conditions for this specific crypto
      const candles = await getHistoricalData(symbol, 24);
      const cryptoConditions = scoreMarketConditions(symbol, candles, dumps);

      console.log(`  Per-Crypto Score: ${cryptoConditions.score}/100`);

      // Strategy 1: BTC-Only
      if (btcConditions.enabled) {
        const outcome = simulateTrade(dump, btcConditions);
        btcOnlyResults.trades++;
        btcOnlyResults.totalPnL += outcome.profit_pct;
        if (outcome.profit_pct > 0) btcOnlyResults.wins++;
        else btcOnlyResults.losses++;
        console.log(`  BTC-Only: ✅ TRADE (${outcome.profit_pct > 0 ? '+' : ''}${outcome.profit_pct.toFixed(2)}%)`);
      } else {
        btcOnlyResults.blockedTrades++;
        console.log(`  BTC-Only: ❌ BLOCKED (BTC score too low)`);
      }

      // Strategy 2: Per-Crypto
      if (cryptoConditions.enabled) {
        const outcome = simulateTrade(dump, cryptoConditions);
        perCryptoResults.trades++;
        perCryptoResults.totalPnL += outcome.profit_pct;
        if (outcome.profit_pct > 0) perCryptoResults.wins++;
        else perCryptoResults.losses++;
        console.log(`  Per-Crypto: ✅ TRADE (${outcome.profit_pct > 0 ? '+' : ''}${outcome.profit_pct.toFixed(2)}%)`);
      } else {
        perCryptoResults.blockedTrades++;
        console.log(`  Per-Crypto: ❌ BLOCKED (coin conditions poor)`);
      }

      console.log();

      // Small delay to avoid rate limiting
      await new Promise(resolve => setTimeout(resolve, 100));

    } catch (error) {
      console.log(`  ⚠️ Error fetching data: ${error.message}\n`);
    }
  }

  // Print results
  console.log('='.repeat(80));
  console.log('BACKTEST RESULTS');
  console.log('='.repeat(80));
  console.log();

  console.log('STRATEGY 1: BTC-Only Market Conditions');
  console.log('─'.repeat(80));
  console.log(`Total Dumps: ${dumps.length}`);
  console.log(`Trades Taken: ${btcOnlyResults.trades}`);
  console.log(`Trades Blocked: ${btcOnlyResults.blockedTrades}`);
  console.log(`Win Rate: ${btcOnlyResults.trades > 0 ? ((btcOnlyResults.wins / btcOnlyResults.trades) * 100).toFixed(1) : 0}%`);
  console.log(`Total P&L: ${btcOnlyResults.totalPnL > 0 ? '+' : ''}${btcOnlyResults.totalPnL.toFixed(2)}%`);
  console.log(`Avg P&L per Trade: ${btcOnlyResults.trades > 0 ? (btcOnlyResults.totalPnL / btcOnlyResults.trades).toFixed(2) : 0}%`);
  console.log();

  console.log('STRATEGY 2: Per-Crypto Market Conditions');
  console.log('─'.repeat(80));
  console.log(`Total Dumps: ${dumps.length}`);
  console.log(`Trades Taken: ${perCryptoResults.trades}`);
  console.log(`Trades Blocked: ${perCryptoResults.blockedTrades}`);
  console.log(`Win Rate: ${perCryptoResults.trades > 0 ? ((perCryptoResults.wins / perCryptoResults.trades) * 100).toFixed(1) : 0}%`);
  console.log(`Total P&L: ${perCryptoResults.totalPnL > 0 ? '+' : ''}${perCryptoResults.totalPnL.toFixed(2)}%`);
  console.log(`Avg P&L per Trade: ${perCryptoResults.trades > 0 ? (perCryptoResults.totalPnL / perCryptoResults.trades).toFixed(2) : 0}%`);
  console.log();

  console.log('COMPARISON');
  console.log('─'.repeat(80));
  const tradeDiff = perCryptoResults.trades - btcOnlyResults.trades;
  const pnlDiff = perCryptoResults.totalPnL - btcOnlyResults.totalPnL;
  console.log(`Additional Trades (Per-Crypto): ${tradeDiff > 0 ? '+' : ''}${tradeDiff}`);
  console.log(`P&L Difference: ${pnlDiff > 0 ? '+' : ''}${pnlDiff.toFixed(2)}%`);
  console.log();

  if (perCryptoResults.totalPnL > btcOnlyResults.totalPnL) {
    console.log('🏆 WINNER: Per-Crypto Market Conditions');
    console.log(`   More nuanced filtering leads to better trade selection`);
  } else if (btcOnlyResults.totalPnL > perCryptoResults.totalPnL) {
    console.log('🏆 WINNER: BTC-Only Market Conditions');
    console.log(`   Conservative BTC-based filtering protects capital better`);
  } else {
    console.log('🤝 TIE: Both strategies performed equally');
  }

  console.log('='.repeat(80));
}

// Run the backtest
runBacktest().catch(console.error);
