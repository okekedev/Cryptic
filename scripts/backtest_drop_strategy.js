const axios = require('axios');
const fs = require('fs');
const jwt = require('jsonwebtoken');
const crypto = require('crypto');
require('dotenv').config({ path: '../.env' });

/**
 * Backtest Drop Trading Strategy
 *
 * Tests various drop detection thresholds and time windows against historical data
 * to find optimal parameters for the dump trading bot.
 */

// Configuration
// Use public Coinbase Exchange API (no auth needed for market data)
const COINBASE_API_BASE = 'https://api.exchange.coinbase.com';
const COINBASE_API_KEY = process.env.COINBASE_API_KEY;
const COINBASE_SIGNING_KEY = process.env.COINBASE_SIGNING_KEY?.replace(/\\n/g, '\n');
const TEST_SYMBOLS = [
  'BTC-USD', 'ETH-USD', 'SOL-USD', 'DOGE-USD', 'AVAX-USD',
  'MATIC-USD', 'LINK-USD', 'UNI-USD', 'AAVE-USD', 'ATOM-USD',
  'ALGO-USD', 'XRP-USD', 'ADA-USD', 'DOT-USD', 'NEAR-USD'
];

// Strategy parameters to test (reduced for faster testing)
const DROP_THRESHOLDS = [2.0, 3.0, 4.0, 5.0];  // % drop thresholds (lowered to find more signals)
const TIME_WINDOWS = [3, 5, 10];  // minutes
const ENTRY_DELAYS = [-3.0, -2.0, -1.0];  // % below alert price for entry
const EXIT_TARGETS = [3.0, 4.0, 6.0, 8.0];  // % profit targets

// Risk management
const MAX_HOLD_MINUTES = 120;  // 2 hours max hold
const STOP_LOSS_PCT = 2.5;  // 2.5% stop loss

/**
 * Generate JWT token for Coinbase API authentication
 */
function generateJWT(requestMethod, requestPath) {
  const algorithm = 'ES256';
  const uri = `${requestMethod} ${requestPath}`;

  const payload = {
    iss: 'coinbase-cloud',
    nbf: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 120,
    sub: COINBASE_API_KEY,
    uri
  };

  return jwt.sign(payload, COINBASE_SIGNING_KEY, {
    algorithm,
    header: {
      kid: COINBASE_API_KEY,
      nonce: crypto.randomBytes(16).toString('hex')
    }
  });
}

/**
 * Fetch historical candle data from Coinbase Advanced Trade API
 */
async function fetchHistoricalData(symbol, startTime, endTime, granularity = 60) {
  try {
    const url = `${COINBASE_API_BASE}/products/${symbol}/candles`;
    const params = {
      start: startTime.toISOString(),
      end: endTime.toISOString(),
      granularity: granularity  // 60 seconds = 1 minute
    };

    const response = await axios.get(url, {
      params,
      headers: {
        'Content-Type': 'application/json'
      }
    });

    // Coinbase Exchange API returns: [[timestamp, low, high, open, close, volume]]
    if (!response.data || !Array.isArray(response.data)) {
      return [];
    }

    const candles = response.data.map(candle => ({
      timestamp: candle[0] * 1000,  // Convert to ms
      low: parseFloat(candle[1]),
      high: parseFloat(candle[2]),
      open: parseFloat(candle[3]),
      close: parseFloat(candle[4]),
      volume: parseFloat(candle[5])
    }));

    // Sort by timestamp ascending
    return candles.sort((a, b) => a.timestamp - b.timestamp);
  } catch (error) {
    console.error(`Error fetching data for ${symbol}:`, error.response?.data || error.message);
    return [];
  }
}

/**
 * Detect drops in price data
 */
function detectDrops(candles, dropThreshold, windowMinutes) {
  const drops = [];
  const windowSize = windowMinutes;

  for (let i = windowSize; i < candles.length; i++) {
    const windowCandles = candles.slice(i - windowSize, i + 1);
    const highPrice = Math.max(...windowCandles.map(c => c.high));
    const currentPrice = candles[i].close;

    const dropPct = ((currentPrice - highPrice) / highPrice) * 100;

    if (dropPct <= -dropThreshold) {
      // Check if this is a new drop (not within 30 minutes of last drop)
      const lastDrop = drops[drops.length - 1];
      if (!lastDrop || (candles[i].timestamp - lastDrop.timestamp) > 30 * 60 * 1000) {
        drops.push({
          timestamp: candles[i].timestamp,
          index: i,
          highPrice: highPrice,
          alertPrice: currentPrice,
          dropPct: dropPct
        });
      }
    }
  }

  return drops;
}

/**
 * Simulate trade from a drop alert
 */
function simulateTrade(drop, candles, entryDelay, exitTarget, stopLoss, maxHoldMinutes) {
  const entryPrice = drop.alertPrice * (1 + entryDelay / 100);
  const targetPrice = entryPrice * (1 + exitTarget / 100);
  const stopPrice = entryPrice * (1 - stopLoss / 100);

  let entryIndex = null;
  let exitIndex = null;
  let exitPrice = null;
  let exitReason = null;

  // Find entry point (price must drop to our entry price)
  for (let i = drop.index; i < candles.length && i < drop.index + 30; i++) {
    if (candles[i].low <= entryPrice) {
      entryIndex = i;
      break;
    }
  }

  // If we couldn't get in within 30 minutes, skip this trade
  if (!entryIndex) {
    return null;
  }

  const entryTime = candles[entryIndex].timestamp;
  const maxExitTime = entryTime + (maxHoldMinutes * 60 * 1000);

  // Find exit point
  for (let i = entryIndex + 1; i < candles.length; i++) {
    const candle = candles[i];

    // Check stop loss
    if (candle.low <= stopPrice) {
      exitIndex = i;
      exitPrice = stopPrice;
      exitReason = 'stop_loss';
      break;
    }

    // Check profit target
    if (candle.high >= targetPrice) {
      exitIndex = i;
      exitPrice = targetPrice;
      exitReason = 'profit_target';
      break;
    }

    // Check max hold time
    if (candle.timestamp >= maxExitTime) {
      exitIndex = i;
      exitPrice = candle.close;
      exitReason = 'max_hold';
      break;
    }
  }

  // If still holding at end of data
  if (!exitIndex) {
    exitIndex = candles.length - 1;
    exitPrice = candles[exitIndex].close;
    exitReason = 'end_of_data';
  }

  const holdMinutes = (candles[exitIndex].timestamp - entryTime) / (60 * 1000);
  const pnlPct = ((exitPrice - entryPrice) / entryPrice) * 100;

  return {
    dropPct: drop.dropPct,
    entryPrice,
    exitPrice,
    pnlPct,
    holdMinutes,
    exitReason
  };
}

/**
 * Backtest strategy with given parameters
 */
function backtestStrategy(candles, dropThreshold, windowMinutes, entryDelay, exitTarget) {
  const drops = detectDrops(candles, dropThreshold, windowMinutes);
  const trades = [];

  for (const drop of drops) {
    const trade = simulateTrade(drop, candles, entryDelay, exitTarget, STOP_LOSS_PCT, MAX_HOLD_MINUTES);
    if (trade) {
      trades.push(trade);
    }
  }

  // Calculate statistics
  const totalTrades = trades.length;
  if (totalTrades === 0) {
    return null;
  }

  const winningTrades = trades.filter(t => t.pnlPct > 0);
  const losingTrades = trades.filter(t => t.pnlPct <= 0);
  const winRate = (winningTrades.length / totalTrades) * 100;
  const avgPnl = trades.reduce((sum, t) => sum + t.pnlPct, 0) / totalTrades;
  const totalPnl = trades.reduce((sum, t) => sum + t.pnlPct, 0);
  const avgHoldTime = trades.reduce((sum, t) => sum + t.holdMinutes, 0) / totalTrades;
  const bestTrade = Math.max(...trades.map(t => t.pnlPct));
  const worstTrade = Math.min(...trades.map(t => t.pnlPct));

  // Calculate Sharpe-like ratio (avg return / stddev)
  const variance = trades.reduce((sum, t) => sum + Math.pow(t.pnlPct - avgPnl, 2), 0) / totalTrades;
  const stdDev = Math.sqrt(variance);
  const sharpe = stdDev > 0 ? avgPnl / stdDev : 0;

  return {
    dropThreshold,
    windowMinutes,
    entryDelay,
    exitTarget,
    totalTrades,
    winningTrades: winningTrades.length,
    losingTrades: losingTrades.length,
    winRate,
    avgPnl,
    totalPnl,
    avgHoldTime,
    bestTrade,
    worstTrade,
    sharpe,
    trades
  };
}

/**
 * Main backtest runner
 */
async function runBacktest() {
  console.log('🔬 Drop Trading Strategy Backtest\n');
  console.log('Fetching historical data from yesterday...\n');

  // Get yesterday's data (full 24 hours) - fetch in chunks to avoid 300 candle limit
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  yesterday.setHours(0, 0, 0, 0);

  const endOfYesterday = new Date(yesterday);
  endOfYesterday.setHours(23, 59, 59, 999);

  console.log(`Date range: ${yesterday.toISOString()} to ${endOfYesterday.toISOString()}\n`);

  // Fetch data for all test symbols in 5-hour chunks (300 minutes = 300 candles)
  const allCandles = {};
  for (const symbol of TEST_SYMBOLS) {
    console.log(`Fetching ${symbol}...`);
    const symbolCandles = [];

    // Fetch in 5-hour chunks
    for (let chunkStart = new Date(yesterday); chunkStart < endOfYesterday; ) {
      const chunkEnd = new Date(chunkStart.getTime() + (5 * 60 * 60 * 1000)); // 5 hours
      if (chunkEnd > endOfYesterday) {
        chunkEnd.setTime(endOfYesterday.getTime());
      }

      const candles = await fetchHistoricalData(symbol, chunkStart, chunkEnd);
      symbolCandles.push(...candles);

      chunkStart = chunkEnd;
      await new Promise(resolve => setTimeout(resolve, 300)); // Rate limit
    }

    if (symbolCandles.length > 0) {
      // Remove duplicates and sort
      const uniqueCandles = Array.from(new Map(symbolCandles.map(c => [c.timestamp, c])).values());
      allCandles[symbol] = uniqueCandles.sort((a, b) => a.timestamp - b.timestamp);
      console.log(`  Fetched ${allCandles[symbol].length} candles`);
    }
  }

  console.log(`\nFetched data for ${Object.keys(allCandles).length} symbols\n`);

  // First, let's see how many drops we'd detect with our current threshold
  console.log('Analyzing drop frequency...\n');
  for (const threshold of DROP_THRESHOLDS) {
    let totalDrops = 0;
    for (const [symbol, candles] of Object.entries(allCandles)) {
      const drops = detectDrops(candles, threshold, 5);
      if (drops.length > 0) {
        console.log(`${symbol}: ${drops.length} drops at -${threshold}% threshold`);
        totalDrops += drops.length;
      }
    }
    console.log(`Total -${threshold}% drops across all symbols: ${totalDrops}\n`);
  }

  console.log('Running backtest with different parameter combinations...\n');

  // Run backtest for all parameter combinations
  const results = [];

  for (const dropThreshold of DROP_THRESHOLDS) {
    for (const windowMinutes of TIME_WINDOWS) {
      for (const entryDelay of ENTRY_DELAYS) {
        for (const exitTarget of EXIT_TARGETS) {
          const strategyResults = [];

          // Test on all symbols
          for (const [symbol, candles] of Object.entries(allCandles)) {
            const result = backtestStrategy(candles, dropThreshold, windowMinutes, entryDelay, exitTarget);
            if (result) {
              strategyResults.push(result);
            }
          }

          // Aggregate results across all symbols
          if (strategyResults.length > 0) {
            const totalTrades = strategyResults.reduce((sum, r) => sum + r.totalTrades, 0);
            const totalWinning = strategyResults.reduce((sum, r) => sum + r.winningTrades, 0);
            const totalLosing = strategyResults.reduce((sum, r) => sum + r.losingTrades, 0);
            const avgPnl = strategyResults.reduce((sum, r) => sum + (r.avgPnl * r.totalTrades), 0) / totalTrades;
            const totalPnl = strategyResults.reduce((sum, r) => sum + r.totalPnl, 0);
            const avgHoldTime = strategyResults.reduce((sum, r) => sum + (r.avgHoldTime * r.totalTrades), 0) / totalTrades;
            const allTrades = strategyResults.flatMap(r => r.trades);
            const bestTrade = Math.max(...allTrades.map(t => t.pnlPct));
            const worstTrade = Math.min(...allTrades.map(t => t.pnlPct));

            // Calculate overall Sharpe
            const variance = allTrades.reduce((sum, t) => sum + Math.pow(t.pnlPct - avgPnl, 2), 0) / totalTrades;
            const stdDev = Math.sqrt(variance);
            const sharpe = stdDev > 0 ? avgPnl / stdDev : 0;

            results.push({
              dropThreshold,
              windowMinutes,
              entryDelay,
              exitTarget,
              totalTrades,
              winningTrades: totalWinning,
              losingTrades: totalLosing,
              winRate: (totalWinning / totalTrades) * 100,
              avgPnl,
              totalPnl,
              avgHoldTime,
              bestTrade,
              worstTrade,
              sharpe,
              profitFactor: totalWinning > 0 ? Math.abs(totalPnl) : 0
            });
          }
        }
      }
    }
  }

  // Sort by Sharpe ratio (risk-adjusted returns)
  results.sort((a, b) => b.sharpe - a.sharpe);

  console.log('=' .repeat(120));
  console.log('TOP 10 STRATEGIES (sorted by Sharpe ratio - risk-adjusted returns)');
  console.log('='.repeat(120));
  console.log('Rank | Drop% | Window | Entry% | Target% | Trades | Win% | Avg P&L | Total P&L | Hold(min) | Sharpe');
  console.log('-'.repeat(120));

  results.slice(0, 10).forEach((r, i) => {
    console.log(
      `${(i + 1).toString().padStart(4)} | ` +
      `${r.dropThreshold.toFixed(1).padStart(5)}% | ` +
      `${r.windowMinutes.toString().padStart(6)}m | ` +
      `${r.entryDelay.toFixed(1).padStart(6)}% | ` +
      `${r.exitTarget.toFixed(1).padStart(7)}% | ` +
      `${r.totalTrades.toString().padStart(6)} | ` +
      `${r.winRate.toFixed(1).padStart(4)}% | ` +
      `${r.avgPnl >= 0 ? '+' : ''}${r.avgPnl.toFixed(2).padStart(6)}% | ` +
      `${r.totalPnl >= 0 ? '+' : ''}${r.totalPnl.toFixed(1).padStart(8)}% | ` +
      `${r.avgHoldTime.toFixed(1).padStart(9)} | ` +
      `${r.sharpe.toFixed(3)}`
    );
  });

  console.log('='.repeat(120));
  console.log('\nCURRENT STRATEGY PERFORMANCE:');
  const currentStrategy = results.find(r =>
    r.dropThreshold === 4.0 &&
    r.windowMinutes === 5 &&
    r.entryDelay === -3.0 &&
    r.exitTarget === 8.0
  );

  if (currentStrategy) {
    console.log(`Drop Threshold: ${currentStrategy.dropThreshold}%`);
    console.log(`Time Window: ${currentStrategy.windowMinutes} minutes`);
    console.log(`Entry Delay: ${currentStrategy.entryDelay}% (ladder start)`);
    console.log(`Exit Target: ${currentStrategy.exitTarget}% (ladder start)`);
    console.log(`Total Trades: ${currentStrategy.totalTrades}`);
    console.log(`Win Rate: ${currentStrategy.winRate.toFixed(1)}%`);
    console.log(`Average P&L: ${currentStrategy.avgPnl >= 0 ? '+' : ''}${currentStrategy.avgPnl.toFixed(2)}%`);
    console.log(`Total P&L: ${currentStrategy.totalPnl >= 0 ? '+' : ''}${currentStrategy.totalPnl.toFixed(1)}%`);
    console.log(`Average Hold Time: ${currentStrategy.avgHoldTime.toFixed(1)} minutes`);
    console.log(`Sharpe Ratio: ${currentStrategy.sharpe.toFixed(3)}`);
    console.log(`Best Trade: +${currentStrategy.bestTrade.toFixed(2)}%`);
    console.log(`Worst Trade: ${currentStrategy.worstTrade.toFixed(2)}%`);

    const rank = results.findIndex(r => r === currentStrategy) + 1;
    console.log(`\nRank: ${rank} out of ${results.length} strategies tested`);
  } else {
    console.log('Current strategy not found in results (possibly no trades)');
  }

  console.log('\n' + '='.repeat(120));
  console.log('ANALYSIS & RECOMMENDATIONS:');
  console.log('='.repeat(120));

  const topStrategy = results[0];
  console.log('\n✨ OPTIMAL STRATEGY:');
  console.log(`   Drop Threshold: ${topStrategy.dropThreshold}%`);
  console.log(`   Time Window: ${topStrategy.windowMinutes} minutes`);
  console.log(`   Entry Delay: ${topStrategy.entryDelay}% (ladder start)`);
  console.log(`   Exit Target: ${topStrategy.exitTarget}% (ladder start)`);
  console.log(`   Expected Win Rate: ${topStrategy.winRate.toFixed(1)}%`);
  console.log(`   Expected Avg P&L: ${topStrategy.avgPnl >= 0 ? '+' : ''}${topStrategy.avgPnl.toFixed(2)}%`);
  console.log(`   Risk-Adjusted Return (Sharpe): ${topStrategy.sharpe.toFixed(3)}`);

  // Save detailed results to file
  const reportPath = './backtest_results.json';
  fs.writeFileSync(reportPath, JSON.stringify({
    testPeriod: {
      start: yesterday.toISOString(),
      end: endOfYesterday.toISOString()
    },
    symbols: Object.keys(allCandles),
    topStrategies: results.slice(0, 20),
    currentStrategy,
    allResults: results
  }, null, 2));

  console.log(`\n📊 Detailed results saved to: ${reportPath}`);
}

// Run the backtest
runBacktest().catch(console.error);
