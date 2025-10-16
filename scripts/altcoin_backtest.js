const axios = require('axios');
const fs = require('fs');
require('dotenv').config({ path: '../.env' });

/**
 * ALTCOIN-SPECIFIC BACKTEST
 * Tests YOUR actual trading coins (NEON, ALICE, ARPA, etc.)
 * to find optimal parameters for small-cap, high-volatility altcoins
 */

const COINBASE_API_BASE = 'https://api.exchange.coinbase.com';

// YOUR ACTUAL TRADING COINS (from the stuck positions + recent activity)
const ALTCOINS = [
  'NEON-USD', 'ALICE-USD', 'ARPA-USD', 'SAPIEN-USD', 'USELESS-USD',
  'TRAC-USD', 'OMNI-USD', 'PLU-USD', 'ZEC-USD', 'INV-USD',
  'AERO-USD', 'COOKIE-USD', 'GTC-USD', 'ETC-USD', 'KARRAT-USD',
  'TAO-USD', 'ZEN-USD', 'ATH-USD', 'CVX-USD', 'TIA-USD',
  'WLD-USD', 'PNUT-USD', 'FARTCOIN-USD', 'WCT-USD', 'AST-USD',
  'INDEX-USD', 'DASH-USD', 'RSC-USD', 'HOPR-USD', 'EDGE-USD',
  'ZORA-USD'
];

// Comprehensive parameter grid for altcoins
const DROP_THRESHOLDS = [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0];
const TIME_WINDOWS = [3, 5, 10];
const ENTRY_DELAYS = [-2.0, -1.5, -1.0, -0.5, 0];
const EXIT_TARGETS = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0];
const STOP_LOSSES = [2.0, 2.5, 3.0, 3.5];
const MAX_HOLDS = [30, 60, 90, 120];

const DAYS_TO_TEST = 7;
const FEES_TAKER = 1.0;  // 1% total (0.6% buy + 0.4% sell)
const FEES_MAKER = 0.8;  // 0.8% for limit orders

/**
 * Fetch historical data with retry logic
 */
async function fetchHistoricalData(symbol, startTime, endTime, granularity = 60) {
  const maxRetries = 3;

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      const url = `${COINBASE_API_BASE}/products/${symbol}/candles`;
      const params = {
        start: startTime.toISOString(),
        end: endTime.toISOString(),
        granularity: granularity
      };

      const response = await axios.get(url, {
        params,
        headers: { 'Content-Type': 'application/json' },
        timeout: 10000
      });

      if (!response.data || !Array.isArray(response.data)) return [];

      const candles = response.data.map(candle => ({
        timestamp: candle[0] * 1000,
        low: parseFloat(candle[1]),
        high: parseFloat(candle[2]),
        open: parseFloat(candle[3]),
        close: parseFloat(candle[4]),
        volume: parseFloat(candle[5])
      }));

      return candles.sort((a, b) => a.timestamp - b.timestamp);
    } catch (error) {
      if (attempt === maxRetries) {
        console.error(`  Failed after ${maxRetries} attempts: ${error.message}`);
        return [];
      }
      await new Promise(resolve => setTimeout(resolve, 1000 * attempt));
    }
  }
  return [];
}

/**
 * Detect drops
 */
function detectDrops(candles, threshold, windowMinutes) {
  const drops = [];
  const windowSize = windowMinutes;

  for (let i = windowSize; i < candles.length; i++) {
    const windowCandles = candles.slice(i - windowSize, i + 1);
    const highPrice = Math.max(...windowCandles.map(c => c.high));
    const currentPrice = candles[i].close;
    const dropPct = ((currentPrice - highPrice) / highPrice) * 100;

    if (dropPct <= -threshold) {
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
 * Simulate trade
 */
function simulateTrade(drop, candles, entryDelay, exitTarget, stopLoss, maxHoldMinutes) {
  const entryPrice = drop.alertPrice * (1 + entryDelay / 100);
  const targetPrice = entryPrice * (1 + exitTarget / 100);
  const stopPrice = entryPrice * (1 - stopLoss / 100);

  let entryIndex = null;

  // Find entry
  for (let i = drop.index; i < candles.length && i < drop.index + 30; i++) {
    if (candles[i].low <= entryPrice && candles[i].high >= entryPrice) {
      entryIndex = i;
      break;
    }
  }

  if (!entryIndex) return null;

  const entryTime = candles[entryIndex].timestamp;
  const maxExitTime = entryTime + (maxHoldMinutes * 60 * 1000);

  // Find exit
  for (let i = entryIndex + 1; i < candles.length; i++) {
    const candle = candles[i];

    // Stop loss
    if (candle.low <= stopPrice) {
      const holdMinutes = (candle.timestamp - entryTime) / (60 * 1000);
      const pnlPct = ((stopPrice - entryPrice) / entryPrice) * 100;
      return { pnlPct: pnlPct - FEES_TAKER, holdMinutes, exitReason: 'stop_loss', exitPrice: stopPrice };
    }

    // Target
    if (candle.high >= targetPrice) {
      const holdMinutes = (candle.timestamp - entryTime) / (60 * 1000);
      const pnlPct = ((targetPrice - entryPrice) / entryPrice) * 100;
      return { pnlPct: pnlPct - FEES_MAKER, holdMinutes, exitReason: 'target', exitPrice: targetPrice };
    }

    // Max hold
    if (candle.timestamp >= maxExitTime) {
      const holdMinutes = (candle.timestamp - entryTime) / (60 * 1000);
      const pnlPct = ((candle.close - entryPrice) / entryPrice) * 100;
      return { pnlPct: pnlPct - FEES_TAKER, holdMinutes, exitReason: 'max_hold', exitPrice: candle.close };
    }
  }

  // End of data
  const exitCandle = candles[candles.length - 1];
  const holdMinutes = (exitCandle.timestamp - entryTime) / (60 * 1000);
  const pnlPct = ((exitCandle.close - entryPrice) / entryPrice) * 100;
  return { pnlPct: pnlPct - FEES_TAKER, holdMinutes, exitReason: 'end_of_data', exitPrice: exitCandle.close };
}

/**
 * Backtest strategy
 */
function backtestStrategy(candles, config) {
  const drops = detectDrops(candles, config.threshold, config.windowMinutes);
  const trades = [];

  for (const drop of drops) {
    const trade = simulateTrade(
      drop,
      candles,
      config.entryDelay,
      config.exitTarget,
      config.stopLoss,
      config.maxHold
    );
    if (trade) {
      trades.push({ ...trade, dropPct: drop.dropPct, entryPrice: drop.alertPrice * (1 + config.entryDelay / 100) });
    }
  }

  if (trades.length === 0) return null;

  const winningTrades = trades.filter(t => t.pnlPct > 0);
  const losingTrades = trades.filter(t => t.pnlPct <= 0);

  const totalTrades = trades.length;
  const winRate = (winningTrades.length / totalTrades) * 100;
  const avgPnl = trades.reduce((sum, t) => sum + t.pnlPct, 0) / totalTrades;
  const totalPnl = trades.reduce((sum, t) => sum + t.pnlPct, 0);

  const totalWinAmount = winningTrades.reduce((sum, t) => sum + t.pnlPct, 0);
  const totalLossAmount = Math.abs(losingTrades.reduce((sum, t) => sum + t.pnlPct, 0));
  const profitFactor = totalLossAmount > 0 ? totalWinAmount / totalLossAmount : totalWinAmount;

  const variance = trades.reduce((sum, t) => sum + Math.pow(t.pnlPct - avgPnl, 2), 0) / totalTrades;
  const stdDev = Math.sqrt(variance);
  const sharpe = stdDev > 0 ? avgPnl / stdDev : 0;

  const avgWin = winningTrades.length > 0 ? totalWinAmount / winningTrades.length : 0;
  const avgLoss = losingTrades.length > 0 ? totalLossAmount / losingTrades.length : 0;
  const expectancy = (winRate / 100 * avgWin) - ((100 - winRate) / 100 * avgLoss);

  const avgHold = trades.reduce((sum, t) => sum + t.holdMinutes, 0) / totalTrades;

  // Composite score (weighted for profitability)
  const score = (expectancy * 0.5) + (sharpe * 0.2) + (profitFactor * 0.2) + (winRate * 0.1);

  return {
    config,
    totalTrades,
    winningTrades: winningTrades.length,
    losingTrades: losingTrades.length,
    winRate,
    avgPnl,
    totalPnl,
    profitFactor,
    sharpe,
    expectancy,
    score,
    avgHold,
    trades
  };
}

/**
 * Main optimization
 */
async function runOptimization() {
  console.log('='.repeat(100));
  console.log('🎯 ALTCOIN-SPECIFIC BACKTEST - YOUR ACTUAL TRADING COINS');
  console.log('='.repeat(100));
  console.log();
  console.log(`Testing ${DAYS_TO_TEST} days across ${ALTCOINS.length} altcoins...`);
  console.log();

  const now = new Date();
  const startDate = new Date(now);
  startDate.setDate(startDate.getDate() - DAYS_TO_TEST);
  startDate.setHours(0, 0, 0, 0);

  console.log(`Date range: ${startDate.toISOString()} to ${now.toISOString()}`);
  console.log();

  // Fetch data
  const allCandles = {};
  let successCount = 0;

  for (const symbol of ALTCOINS) {
    process.stdout.write(`Fetching ${symbol}...`);
    const symbolCandles = [];

    for (let chunkStart = new Date(startDate); chunkStart < now; ) {
      const chunkEnd = new Date(chunkStart.getTime() + (5 * 60 * 60 * 1000));
      if (chunkEnd > now) chunkEnd.setTime(now.getTime());

      const candles = await fetchHistoricalData(symbol, chunkStart, chunkEnd);
      symbolCandles.push(...candles);

      chunkStart = chunkEnd;
      await new Promise(resolve => setTimeout(resolve, 300));
    }

    if (symbolCandles.length > 0) {
      const uniqueCandles = Array.from(new Map(symbolCandles.map(c => [c.timestamp, c])).values());
      allCandles[symbol] = uniqueCandles.sort((a, b) => a.timestamp - b.timestamp);
      console.log(` ✅ ${allCandles[symbol].length} candles`);
      successCount++;
    } else {
      console.log(` ❌ No data`);
    }
  }

  console.log();
  console.log(`✅ Fetched data for ${successCount}/${ALTCOINS.length} symbols`);
  console.log();

  // Movement analysis
  console.log('📊 Drop Frequency Analysis:');
  console.log();
  for (const threshold of [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]) {
    let total = 0;
    for (const candles of Object.values(allCandles)) {
      total += detectDrops(candles, threshold, 5).length;
    }
    console.log(`  ${threshold}% threshold: ${total} drops`);
  }

  console.log();
  console.log('🔬 Testing parameter combinations (this will take a while)...');
  console.log();

  const totalCombinations = DROP_THRESHOLDS.length * TIME_WINDOWS.length * ENTRY_DELAYS.length *
                           EXIT_TARGETS.length * STOP_LOSSES.length * MAX_HOLDS.length;
  console.log(`Total combinations: ${totalCombinations}`);
  console.log();

  const results = [];
  let tested = 0;

  for (const threshold of DROP_THRESHOLDS) {
    for (const windowMinutes of TIME_WINDOWS) {
      for (const entryDelay of ENTRY_DELAYS) {
        for (const exitTarget of EXIT_TARGETS) {
          for (const stopLoss of STOP_LOSSES) {
            for (const maxHold of MAX_HOLDS) {
              tested++;

              if (tested % 100 === 0) {
                process.stdout.write(`\rProgress: ${tested}/${totalCombinations} (${(tested/totalCombinations*100).toFixed(1)}%)`);
              }

              const config = { threshold, windowMinutes, entryDelay, exitTarget, stopLoss, maxHold };
              const strategyResults = [];

              for (const candles of Object.values(allCandles)) {
                const result = backtestStrategy(candles, config);
                if (result) {
                  strategyResults.push(result);
                }
              }

              if (strategyResults.length > 0) {
                const totalTrades = strategyResults.reduce((sum, r) => sum + r.totalTrades, 0);
                if (totalTrades < 10) continue;

                const totalWinning = strategyResults.reduce((sum, r) => sum + r.winningTrades, 0);
                const totalLosing = strategyResults.reduce((sum, r) => sum + r.losingTrades, 0);
                const avgPnl = strategyResults.reduce((sum, r) => sum + (r.avgPnl * r.totalTrades), 0) / totalTrades;
                const totalPnl = strategyResults.reduce((sum, r) => sum + r.totalPnl, 0);
                const avgHold = strategyResults.reduce((sum, r) => sum + (r.avgHold * r.totalTrades), 0) / totalTrades;

                const allTrades = strategyResults.flatMap(r => r.trades);
                const totalWinAmount = allTrades.filter(t => t.pnlPct > 0).reduce((sum, t) => sum + t.pnlPct, 0);
                const totalLossAmount = Math.abs(allTrades.filter(t => t.pnlPct <= 0).reduce((sum, t) => sum + t.pnlPct, 0));
                const profitFactor = totalLossAmount > 0 ? totalWinAmount / totalLossAmount : totalWinAmount;

                const variance = allTrades.reduce((sum, t) => sum + Math.pow(t.pnlPct - avgPnl, 2), 0) / totalTrades;
                const stdDev = Math.sqrt(variance);
                const sharpe = stdDev > 0 ? avgPnl / stdDev : 0;

                const avgWin = totalWinning > 0 ? totalWinAmount / totalWinning : 0;
                const avgLoss = totalLosing > 0 ? totalLossAmount / totalLosing : 0;
                const winRate = (totalWinning / totalTrades) * 100;
                const expectancy = (winRate / 100 * avgWin) - ((100 - winRate) / 100 * avgLoss);

                const score = (expectancy * 0.5) + (sharpe * 0.2) + (profitFactor * 0.2) + (winRate * 0.1);

                results.push({
                  config,
                  totalTrades,
                  winningTrades: totalWinning,
                  losingTrades: totalLosing,
                  winRate,
                  avgPnl,
                  totalPnl,
                  profitFactor,
                  sharpe,
                  expectancy,
                  score,
                  avgHold
                });
              }
            }
          }
        }
      }
    }
  }

  console.log(`\n\n✅ Tested ${tested} combinations, found ${results.length} profitable strategies\n`);

  results.sort((a, b) => b.score - a.score);

  console.log('='.repeat(150));
  console.log('TOP 20 MOST PROFITABLE STRATEGIES FOR ALTCOINS');
  console.log('='.repeat(150));
  console.log('Rank | Score | Thresh | Win | Entry | Exit | Stop | Hold | Trades | WinRate | AvgP&L | TotalP&L | PF   | Sharpe | Expect | AvgHold');
  console.log('-'.repeat(150));

  results.slice(0, 20).forEach((r, i) => {
    console.log(
      `${(i + 1).toString().padStart(4)} | ` +
      `${r.score.toFixed(2).padStart(5)} | ` +
      `${r.config.threshold.toFixed(1).padStart(6)}% | ` +
      `${r.config.windowMinutes.toString().padStart(3)}m | ` +
      `${r.config.entryDelay >= 0 ? '+' : ''}${r.config.entryDelay.toFixed(1).padStart(5)}% | ` +
      `${r.config.exitTarget.toFixed(1).padStart(4)}% | ` +
      `${r.config.stopLoss.toFixed(1).padStart(4)}% | ` +
      `${r.config.maxHold.toString().padStart(4)}m | ` +
      `${r.totalTrades.toString().padStart(6)} | ` +
      `${r.winRate.toFixed(1).padStart(7)}% | ` +
      `${r.avgPnl >= 0 ? '+' : ''}${r.avgPnl.toFixed(2).padStart(6)}% | ` +
      `${r.totalPnl >= 0 ? '+' : ''}${r.totalPnl.toFixed(1).padStart(8)}% | ` +
      `${r.profitFactor.toFixed(2).padStart(4)} | ` +
      `${r.sharpe.toFixed(2).padStart(6)} | ` +
      `${r.expectancy.toFixed(3).padStart(6)} | ` +
      `${r.avgHold.toFixed(0).padStart(7)}m`
    );
  });

  console.log('='.repeat(150));

  if (results.length > 0) {
    const top = results[0];
    console.log('\n🏆 OPTIMAL STRATEGY FOR YOUR ALTCOINS:\n');
    console.log(`Drop Threshold: ${top.config.threshold}%`);
    console.log(`Time Window: ${top.config.windowMinutes} minutes`);
    console.log(`Entry Delay: ${top.config.entryDelay >= 0 ? '+' : ''}${top.config.entryDelay}%`);
    console.log(`Exit Target: ${top.config.exitTarget}%`);
    console.log(`Stop Loss: ${top.config.stopLoss}%`);
    console.log(`Max Hold: ${top.config.maxHold} minutes`);
    console.log(`\n📈 EXPECTED PERFORMANCE:\n`);
    console.log(`Total Trades: ${top.totalTrades}`);
    console.log(`Win Rate: ${top.winRate.toFixed(1)}%`);
    console.log(`Average P&L: ${top.avgPnl >= 0 ? '+' : ''}${top.avgPnl.toFixed(2)}%`);
    console.log(`Total P&L: ${top.totalPnl >= 0 ? '+' : ''}${top.totalPnl.toFixed(1)}%`);
    console.log(`Profit Factor: ${top.profitFactor.toFixed(2)}`);
    console.log(`Sharpe Ratio: ${top.sharpe.toFixed(3)}`);
    console.log(`Expectancy: ${top.expectancy.toFixed(3)}%`);
    console.log(`Average Hold Time: ${top.avgHold.toFixed(0)} minutes`);
    console.log(`Composite Score: ${top.score.toFixed(3)}`);
  }

  const reportPath = './altcoin_backtest_results.json';
  fs.writeFileSync(reportPath, JSON.stringify({
    testPeriod: { start: startDate.toISOString(), end: now.toISOString(), days: DAYS_TO_TEST },
    symbols: Object.keys(allCandles),
    totalSymbols: successCount,
    totalStrategiesTested: tested,
    profitableStrategies: results.length,
    top20Strategies: results.slice(0, 20),
    allResults: results
  }, null, 2));

  console.log(`\n💾 Results saved to: ${reportPath}`);
  console.log('='.repeat(150));
}

runOptimization().catch(console.error);
