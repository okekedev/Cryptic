#!/usr/bin/env node
/**
 * REALISTIC BACKTEST with Actual Minute Data
 *
 * Uses real price movements after dump alerts to simulate actual trades
 */

const fs = require('fs');

// Load data
const dumps = JSON.parse(fs.readFileSync('./dumps_12h.json', 'utf8'));
const csvData = fs.readFileSync('./crypto_minute_data_12h.csv', 'utf8');

console.log('='.repeat(80));
console.log('REALISTIC BACKTEST: Actual Price Data');
console.log('='.repeat(80));
console.log();

// Parse CSV
const lines = csvData.split('\n').slice(1); // Skip header
const candles = [];

for (const line of lines) {
  if (!line.trim()) continue;

  const [symbol, timestamp, datetime, open, high, low, close, volume] = line.split(',');
  candles.push({
    symbol,
    timestamp: parseInt(timestamp),
    datetime,
    open: parseFloat(open),
    high: parseFloat(high),
    low: parseFloat(low),
    close: parseFloat(close),
    volume: parseFloat(volume)
  });
}

console.log(`Loaded ${candles.length} minute candles`);
console.log(`Analyzing ${dumps.length} dump alerts`);
console.log();

/**
 * Calculate market metrics for a symbol at a specific time
 */
function calculateMetrics(symbol, dumpTime) {
  const dumpTimestamp = new Date(dumpTime).getTime() / 1000;

  // Get 24h of data before dump
  const historicalCandles = candles.filter(c =>
    c.symbol === symbol &&
    c.timestamp < dumpTimestamp &&
    c.timestamp > dumpTimestamp - (24 * 60 * 60)
  ).sort((a, b) => a.timestamp - b.timestamp);

  if (historicalCandles.length < 20) return null;

  // Volatility
  const returns = [];
  for (let i = 1; i < historicalCandles.length; i++) {
    const pct = ((historicalCandles[i].close - historicalCandles[i-1].close) / historicalCandles[i-1].close) * 100;
    returns.push(pct);
  }
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.reduce((sum, r) => sum + Math.pow(r - mean, 2), 0) / returns.length;
  const volatility = Math.sqrt(variance);

  // Trend (last 4 hours before dump)
  const recentCandles = historicalCandles.slice(-240); // 4 hours
  if (recentCandles.length < 10) return null;

  const firstPrice = recentCandles[0].close;
  const lastPrice = recentCandles[recentCandles.length - 1].close;
  const trendPct = ((lastPrice - firstPrice) / firstPrice) * 100;

  // RSI
  let gains = 0, losses = 0;
  const period = Math.min(14, historicalCandles.length - 1);
  for (let i = historicalCandles.length - period - 1; i < historicalCandles.length - 1; i++) {
    const change = historicalCandles[i + 1].close - historicalCandles[i].close;
    if (change > 0) gains += change;
    else losses += Math.abs(change);
  }
  const rsi = losses === 0 ? 100 : 100 - (100 / (1 + (gains / period) / (losses / period)));

  // Volume trend
  const recentVol = historicalCandles.slice(-10);
  const oldHalf = recentVol.slice(0, 5);
  const newHalf = recentVol.slice(5);
  const oldAvg = oldHalf.reduce((sum, c) => sum + c.volume, 0) / 5;
  const newAvg = newHalf.reduce((sum, c) => sum + c.volume, 0) / 5;
  const volumeChangePct = oldAvg === 0 ? 0 : ((newAvg - oldAvg) / oldAvg) * 100;

  // Dump frequency
  const symbolDumps = dumps.filter(d => d.symbol === symbol);
  const dumpFreq = symbolDumps.length / 12; // per hour over 12h

  return {
    volatility,
    trendPct,
    rsi,
    volumeChangePct,
    dumpFreq,
    candleCount: historicalCandles.length
  };
}

/**
 * Score market conditions
 */
function scoreConditions(metrics) {
  if (!metrics) return { score: 0, reasons: ['No data'] };

  let score = 0;
  const reasons = [];

  // Volatility (0-25 points)
  if (metrics.volatility < 1.5) {
    reasons.push(`❌ Low volatility ${metrics.volatility.toFixed(2)}%`);
  } else if (metrics.volatility >= 2.0 && metrics.volatility <= 6.0) {
    score += 25;
    reasons.push(`✅ Ideal volatility ${metrics.volatility.toFixed(2)}%`);
  } else if (metrics.volatility > 6.0) {
    score += 15;
    reasons.push(`⚠️ High volatility ${metrics.volatility.toFixed(2)}%`);
  } else {
    score += 10;
    reasons.push(`⚡ Moderate volatility ${metrics.volatility.toFixed(2)}%`);
  }

  // Trend (0-20 points)
  if (metrics.trendPct > 2) {
    score += 20;
    reasons.push(`✅ Bullish ${metrics.trendPct.toFixed(1)}%`);
  } else if (metrics.trendPct > -2) {
    score += 10;
    reasons.push(`⚡ Neutral ${metrics.trendPct.toFixed(1)}%`);
  } else {
    reasons.push(`❌ Bearish ${metrics.trendPct.toFixed(1)}%`);
  }

  // RSI (0-20 points)
  if (metrics.rsi < 40) {
    score += 20;
    reasons.push(`✅ RSI oversold ${metrics.rsi.toFixed(0)}`);
  } else if (metrics.rsi > 65) {
    reasons.push(`❌ RSI overbought ${metrics.rsi.toFixed(0)}`);
  } else {
    score += 10;
    reasons.push(`⚡ RSI neutral ${metrics.rsi.toFixed(0)}`);
  }

  // Volume (0-15 points)
  if (metrics.volumeChangePct > 20) {
    score += 15;
    reasons.push(`✅ Volume up ${metrics.volumeChangePct.toFixed(0)}%`);
  } else if (metrics.volumeChangePct < -20) {
    reasons.push(`❌ Volume down ${metrics.volumeChangePct.toFixed(0)}%`);
  } else {
    score += 10;
    reasons.push(`⚡ Volume stable`);
  }

  // Dump frequency (0-20 points)
  if (metrics.dumpFreq < 3) {
    score += 20;
    reasons.push(`✅ Stable ${metrics.dumpFreq.toFixed(1)}/hr`);
  } else if (metrics.dumpFreq < 8) {
    score += 10;
    reasons.push(`⚡ Moderate ${metrics.dumpFreq.toFixed(1)}/hr`);
  } else {
    reasons.push(`❌ Choppy ${metrics.dumpFreq.toFixed(1)}/hr`);
  }

  return { score, reasons };
}

/**
 * Simulate trade with ACTUAL price movements
 */
function simulateTrade(dump) {
  const dumpTimestamp = new Date(dump.timestamp).getTime() / 1000;
  const entryPrice = dump.alert_price;

  // Get actual price action after dump (next 60 minutes)
  const futureCandles = candles.filter(c =>
    c.symbol === dump.symbol &&
    c.timestamp > dumpTimestamp &&
    c.timestamp <= dumpTimestamp + (60 * 60) // 60 minutes
  ).sort((a, b) => a.timestamp - b.timestamp);

  if (futureCandles.length === 0) {
    return { outcome: 0, reason: 'No data', minutes: 0 };
  }

  // Simulate ladder buy (starting -3%, stepping up 0.5% every 30s)
  let buyPrice = null;
  let buyTime = null;
  let ladderPct = -3.0;

  for (const candle of futureCandles) {
    const minutesElapsed = (candle.timestamp - dumpTimestamp) / 60;

    // Check if ladder buy would have filled
    const currentLadderPrice = entryPrice * (1 + ladderPct / 100);

    if (candle.low <= currentLadderPrice) {
      buyPrice = currentLadderPrice;
      buyTime = candle.timestamp;
      break;
    }

    // Step up ladder every 30 seconds
    if (minutesElapsed > 0 && minutesElapsed % 0.5 === 0) {
      ladderPct += 0.5;
    }

    // Give up if we reach current price
    if (ladderPct >= 0) {
      buyPrice = entryPrice;
      buyTime = candle.timestamp;
      break;
    }
  }

  if (!buyPrice) {
    return { outcome: 0, reason: 'Buy never filled', minutes: 0 };
  }

  // Now simulate holding and ladder sell
  const buyIndex = futureCandles.findIndex(c => c.timestamp >= buyTime);
  if (buyIndex === -1) return { outcome: 0, reason: 'No data after buy', minutes: 0 };

  const holdingCandles = futureCandles.slice(buyIndex);

  let peak = buyPrice;
  let minutesHeld = 0;

  for (const candle of holdingCandles) {
    minutesHeld = (candle.timestamp - buyTime) / 60;

    // Update peak
    if (candle.high > peak) peak = candle.high;

    // Check stop loss (-4% after 5 minutes)
    if (minutesHeld >= 5) {
      const currentPct = ((candle.low - buyPrice) / buyPrice) * 100;
      if (currentPct <= -4) {
        return {
          outcome: -4,
          reason: 'Stop loss -4%',
          minutes: minutesHeld,
          buyPrice,
          exitPrice: buyPrice * 0.96
        };
      }
    }

    // Simulate ladder sell (start +8%, step down 0.5% every 30s)
    let sellLadderPct = 8.0;
    const sellTime = minutesHeld;

    // Step down based on time held
    sellLadderPct = 8.0 - (Math.floor(sellTime / 0.5) * 0.5);

    const sellPrice = buyPrice * (1 + sellLadderPct / 100);

    // Check if sell would have filled
    if (candle.high >= sellPrice) {
      const profitPct = ((sellPrice - buyPrice) / buyPrice) * 100;
      return {
        outcome: profitPct,
        reason: `Ladder sell @ +${sellLadderPct.toFixed(1)}%`,
        minutes: minutesHeld,
        buyPrice,
        exitPrice: sellPrice
      };
    }

    // Max hold time (60 min)
    if (minutesHeld >= 60) {
      const exitPct = ((candle.close - buyPrice) / buyPrice) * 100;
      return {
        outcome: exitPct,
        reason: 'Max hold time',
        minutes: minutesHeld,
        buyPrice,
        exitPrice: candle.close
      };
    }
  }

  // If we get here, ran out of data
  const lastCandle = holdingCandles[holdingCandles.length - 1];
  const finalPct = ((lastCandle.close - buyPrice) / buyPrice) * 100;

  return {
    outcome: finalPct,
    reason: 'Data ended',
    minutes: minutesHeld,
    buyPrice,
    exitPrice: lastCandle.close
  };
}

/**
 * Run backtest
 */
function runBacktest() {
  const btcOnlyResults = {
    trades: [],
    blocked: []
  };

  const perCryptoResults = {
    trades: [],
    blocked: []
  };

  // Get BTC conditions at start of period
  const btcMetrics = calculateMetrics('BTC-USD', dumps[0].timestamp);
  const btcScore = btcMetrics ? scoreConditions(btcMetrics) : { score: 0 };

  console.log('━'.repeat(80));
  console.log('BTC CONDITIONS (for BTC-Only strategy)');
  console.log('━'.repeat(80));
  console.log(`Score: ${btcScore.score}/100`);
  console.log();

  console.log('━'.repeat(80));
  console.log('ANALYZING DUMPS');
  console.log('━'.repeat(80));
  console.log();

  for (let i = 0; i < dumps.length; i++) {
    const dump = dumps[i];

    console.log(`[${i + 1}/${dumps.length}] ${dump.symbol} @ $${dump.alert_price} (${dump.dump_pct.toFixed(2)}%)`);

    const metrics = calculateMetrics(dump.symbol, dump.timestamp);

    if (!metrics) {
      console.log('  ⚠️ Insufficient data\n');
      continue;
    }

    const cryptoScore = scoreConditions(metrics);
    console.log(`  Score: ${cryptoScore.score}/100`);

    const tradeResult = simulateTrade(dump);

    // BTC-Only Strategy (score >= 50)
    if (btcScore.score >= 50) {
      btcOnlyResults.trades.push({
        symbol: dump.symbol,
        ...tradeResult,
        score: btcScore.score
      });
      console.log(`  BTC-Only: ✅ ${tradeResult.outcome > 0 ? '+' : ''}${tradeResult.outcome.toFixed(2)}% (${tradeResult.reason})`);
    } else {
      btcOnlyResults.blocked.push(dump.symbol);
      console.log(`  BTC-Only: ❌ BLOCKED`);
    }

    // Per-Crypto Strategy (score >= 50)
    if (cryptoScore.score >= 50) {
      perCryptoResults.trades.push({
        symbol: dump.symbol,
        ...tradeResult,
        score: cryptoScore.score
      });
      console.log(`  Per-Crypto: ✅ ${tradeResult.outcome > 0 ? '+' : ''}${tradeResult.outcome.toFixed(2)}% (${tradeResult.reason})`);
    } else {
      perCryptoResults.blocked.push(dump.symbol);
      console.log(`  Per-Crypto: ❌ BLOCKED`);
    }

    console.log();
  }

  // Calculate results
  console.log('='.repeat(80));
  console.log('BACKTEST RESULTS (With Real Price Data)');
  console.log('='.repeat(80));
  console.log();

  const btcWins = btcOnlyResults.trades.filter(t => t.outcome > 0).length;
  const btcLosses = btcOnlyResults.trades.filter(t => t.outcome <= 0).length;
  const btcTotalPnL = btcOnlyResults.trades.reduce((sum, t) => sum + t.outcome, 0);

  console.log('STRATEGY 1: BTC-Only');
  console.log('─'.repeat(80));
  console.log(`Trades: ${btcOnlyResults.trades.length}`);
  console.log(`Blocked: ${btcOnlyResults.blocked.length}`);
  console.log(`Wins: ${btcWins} (${btcOnlyResults.trades.length > 0 ? (btcWins / btcOnlyResults.trades.length * 100).toFixed(1) : 0}%)`);
  console.log(`Losses: ${btcLosses}`);
  console.log(`Total P&L: ${btcTotalPnL > 0 ? '+' : ''}${btcTotalPnL.toFixed(2)}%`);
  console.log(`Avg P&L: ${btcOnlyResults.trades.length > 0 ? (btcTotalPnL / btcOnlyResults.trades.length).toFixed(2) : 0}%`);
  console.log();

  const perWins = perCryptoResults.trades.filter(t => t.outcome > 0).length;
  const perLosses = perCryptoResults.trades.filter(t => t.outcome <= 0).length;
  const perTotalPnL = perCryptoResults.trades.reduce((sum, t) => sum + t.outcome, 0);

  console.log('STRATEGY 2: Per-Crypto');
  console.log('─'.repeat(80));
  console.log(`Trades: ${perCryptoResults.trades.length}`);
  console.log(`Blocked: ${perCryptoResults.blocked.length}`);
  console.log(`Wins: ${perWins} (${perCryptoResults.trades.length > 0 ? (perWins / perCryptoResults.trades.length * 100).toFixed(1) : 0}%)`);
  console.log(`Losses: ${perLosses}`);
  console.log(`Total P&L: ${perTotalPnL > 0 ? '+' : ''}${perTotalPnL.toFixed(2)}%`);
  console.log(`Avg P&L: ${perCryptoResults.trades.length > 0 ? (perTotalPnL / perCryptoResults.trades.length).toFixed(2) : 0}%`);
  console.log();

  console.log('COMPARISON');
  console.log('─'.repeat(80));
  const pnlDiff = perTotalPnL - btcTotalPnL;
  console.log(`P&L Difference: ${pnlDiff > 0 ? '+' : ''}${pnlDiff.toFixed(2)}%`);
  console.log();

  if (perTotalPnL > btcTotalPnL) {
    console.log('🏆 WINNER: Per-Crypto Market Conditions');
  } else if (btcTotalPnL > perTotalPnL) {
    console.log('🏆 WINNER: BTC-Only Market Conditions');
  } else {
    console.log('🤝 TIE');
  }

  console.log('='.repeat(80));
}

runBacktest();
