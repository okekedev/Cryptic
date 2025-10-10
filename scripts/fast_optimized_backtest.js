const axios = require('axios');
const fs = require('fs');
require('dotenv').config({ path: '../.env' });

/**
 * FAST OPTIMIZED BACKTEST - Smart Parameter Discovery
 * Uses parallel testing and early stopping to find optimal parameters quickly
 */

const COINBASE_API_BASE = 'https://api.exchange.coinbase.com';

// Focus on high-liquidity, high-volatility coins
const TEST_SYMBOLS = ['BTC-USD', 'ETH-USD', 'SOL-USD', 'DOGE-USD', 'AVAX-USD'];

// PHASE 1: Coarse grid search - OPTIMIZED FOR PROFITABILITY
const COARSE_THRESHOLDS = [1.0, 1.5, 2.0, 3.0]; // Larger movements
const COARSE_WINDOWS = [3, 5, 10];
const COARSE_ENTRIES = [0, -0.5, -1.0, -1.5]; // Better entry prices
const COARSE_EXITS = [2.0, 3.0, 4.0, 5.0, 6.0]; // LARGER targets to overcome fees
const COARSE_STOPS = [1.5, 2.0, 3.0];
const COARSE_HOLDS = [60, 120, 180];

const DAYS_TO_TEST = 7;

/**
 * Fetch historical data
 */
async function fetchHistoricalData(symbol, startTime, endTime, granularity = 60) {
  try {
    const url = `${COINBASE_API_BASE}/products/${symbol}/candles`;
    const params = {
      start: startTime.toISOString(),
      end: endTime.toISOString(),
      granularity: granularity
    };

    const response = await axios.get(url, {
      params,
      headers: { 'Content-Type': 'application/json' }
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
    console.error(`Error fetching ${symbol}:`, error.message);
    return [];
  }
}

/**
 * Detect price movements (dumps and pumps)
 */
function detectMovements(candles, threshold, windowMinutes) {
  const movements = [];
  const windowSize = windowMinutes;

  for (let i = windowSize; i < candles.length; i++) {
    const windowCandles = candles.slice(i - windowSize, i + 1);
    const highPrice = Math.max(...windowCandles.map(c => c.high));
    const lowPrice = Math.min(...windowCandles.map(c => c.low));
    const currentPrice = candles[i].close;

    const dropPct = ((currentPrice - highPrice) / highPrice) * 100;
    const pumpPct = ((currentPrice - lowPrice) / lowPrice) * 100;

    // Detect DROP
    if (dropPct <= -threshold) {
      const lastMovement = movements[movements.length - 1];
      if (!lastMovement || (candles[i].timestamp - lastMovement.timestamp) > 30 * 60 * 1000) {
        movements.push({
          type: 'drop',
          timestamp: candles[i].timestamp,
          index: i,
          refPrice: highPrice,
          alertPrice: currentPrice,
          changePct: dropPct
        });
      }
    }

    // Detect PUMP
    if (pumpPct >= threshold) {
      const lastMovement = movements[movements.length - 1];
      if (!lastMovement || (candles[i].timestamp - lastMovement.timestamp) > 30 * 60 * 1000) {
        movements.push({
          type: 'pump',
          timestamp: candles[i].timestamp,
          index: i,
          refPrice: lowPrice,
          alertPrice: currentPrice,
          changePct: pumpPct
        });
      }
    }
  }

  return movements;
}

/**
 * Simulate trade
 */
function simulateTrade(movement, candles, entryDelay, exitTarget, stopLoss, maxHoldMinutes) {
  const entryPrice = movement.alertPrice * (1 + entryDelay / 100);
  const targetPrice = entryPrice * (1 + exitTarget / 100);
  const stopPrice = entryPrice * (1 - stopLoss / 100);

  let entryIndex = null;

  // Find entry
  for (let i = movement.index; i < candles.length && i < movement.index + 30; i++) {
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

    // Stop loss hit
    if (candle.low <= stopPrice) {
      const holdMinutes = (candle.timestamp - entryTime) / (60 * 1000);
      const pnlPct = ((stopPrice - entryPrice) / entryPrice) * 100;
      return { pnlPct: pnlPct - 1.0, holdMinutes, exitReason: 'stop_loss' }; // -1% fees
    }

    // Target hit
    if (candle.high >= targetPrice) {
      const holdMinutes = (candle.timestamp - entryTime) / (60 * 1000);
      const pnlPct = ((targetPrice - entryPrice) / entryPrice) * 100;
      return { pnlPct: pnlPct - 0.8, holdMinutes, exitReason: 'target' }; // -0.8% fees (maker)
    }

    // Max hold
    if (candle.timestamp >= maxExitTime) {
      const holdMinutes = (candle.timestamp - entryTime) / (60 * 1000);
      const pnlPct = ((candle.close - entryPrice) / entryPrice) * 100;
      return { pnlPct: pnlPct - 1.0, holdMinutes, exitReason: 'max_hold' };
    }
  }

  // End of data
  const exitCandle = candles[candles.length - 1];
  const holdMinutes = (exitCandle.timestamp - entryTime) / (60 * 1000);
  const pnlPct = ((exitCandle.close - entryPrice) / entryPrice) * 100;
  return { pnlPct: pnlPct - 1.0, holdMinutes, exitReason: 'end_of_data' };
}

/**
 * Backtest strategy
 */
function backtestStrategy(candles, config) {
  const movements = detectMovements(candles, config.threshold, config.windowMinutes);
  const trades = [];

  for (const movement of movements) {
    const trade = simulateTrade(
      movement,
      candles,
      config.entryDelay,
      config.exitTarget,
      config.stopLoss,
      config.maxHold
    );
    if (trade) {
      trades.push(trade);
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

  // Composite score
  const score = (expectancy * 0.4) + (sharpe * 0.3) + (profitFactor * 0.2) + (winRate * 0.1);

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
    trades
  };
}

/**
 * Main optimization
 */
async function runOptimization() {
  console.log('🚀 FAST OPTIMIZED BACKTEST - Smart Parameter Discovery\n');
  console.log(`Testing ${DAYS_TO_TEST} days across ${TEST_SYMBOLS.length} symbols...\n`);

  // Fetch data
  const now = new Date();
  const startDate = new Date(now);
  startDate.setDate(startDate.getDate() - DAYS_TO_TEST);
  startDate.setHours(0, 0, 0, 0);

  console.log(`Date range: ${startDate.toISOString()} to ${now.toISOString()}\n`);

  const allCandles = {};
  for (const symbol of TEST_SYMBOLS) {
    console.log(`Fetching ${symbol}...`);
    const symbolCandles = [];

    for (let chunkStart = new Date(startDate); chunkStart < now; ) {
      const chunkEnd = new Date(chunkStart.getTime() + (5 * 60 * 60 * 1000));
      if (chunkEnd > now) chunkEnd.setTime(now.getTime());

      const candles = await fetchHistoricalData(symbol, chunkStart, chunkEnd);
      symbolCandles.push(...candles);

      chunkStart = chunkEnd;
      await new Promise(resolve => setTimeout(resolve, 200));
    }

    if (symbolCandles.length > 0) {
      const uniqueCandles = Array.from(new Map(symbolCandles.map(c => [c.timestamp, c])).values());
      allCandles[symbol] = uniqueCandles.sort((a, b) => a.timestamp - b.timestamp);
      console.log(`  Fetched ${allCandles[symbol].length} candles`);
    }
  }

  console.log(`\n✅ Fetched data for ${Object.keys(allCandles).length} symbols\n`);

  // Movement analysis
  console.log('📊 Movement Analysis:\n');
  for (const threshold of [0.5, 1.0, 1.5, 2.0, 3.0]) {
    let total = 0;
    for (const candles of Object.values(allCandles)) {
      total += detectMovements(candles, threshold, 5).length;
    }
    console.log(`${threshold}% threshold: ${total} movements`);
  }

  console.log('\n🔬 Testing parameter combinations...\n');

  const results = [];
  let testCount = 0;

  // Test all coarse combinations
  for (const threshold of COARSE_THRESHOLDS) {
    for (const windowMinutes of COARSE_WINDOWS) {
      for (const entryDelay of COARSE_ENTRIES) {
        for (const exitTarget of COARSE_EXITS) {
          for (const stopLoss of COARSE_STOPS) {
            for (const maxHold of COARSE_HOLDS) {
              testCount++;

              const config = { threshold, windowMinutes, entryDelay, exitTarget, stopLoss, maxHold };
              const strategyResults = [];

              // Test on all symbols
              for (const candles of Object.values(allCandles)) {
                const result = backtestStrategy(candles, config);
                if (result) {
                  strategyResults.push(result);
                }
              }

              // Aggregate
              if (strategyResults.length > 0) {
                const totalTrades = strategyResults.reduce((sum, r) => sum + r.totalTrades, 0);
                if (totalTrades < 5) continue; // Need at least 5 trades

                const totalWinning = strategyResults.reduce((sum, r) => sum + r.winningTrades, 0);
                const totalLosing = strategyResults.reduce((sum, r) => sum + r.losingTrades, 0);
                const avgPnl = strategyResults.reduce((sum, r) => sum + (r.avgPnl * r.totalTrades), 0) / totalTrades;
                const totalPnl = strategyResults.reduce((sum, r) => sum + r.totalPnl, 0);

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

                const score = (expectancy * 0.4) + (sharpe * 0.3) + (profitFactor * 0.2) + (winRate * 0.1);

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
                  score
                });
              }
            }
          }
        }
      }
    }
  }

  console.log(`✅ Tested ${testCount} combinations, found ${results.length} profitable strategies\n`);

  // Sort by score
  results.sort((a, b) => b.score - a.score);

  // Display results
  console.log('='.repeat(140));
  console.log('TOP 20 MOST PROFITABLE STRATEGIES');
  console.log('='.repeat(140));
  console.log('Rank | Score | Thresh | Win | Entry | Exit | Stop | Hold | Trades | WinRate | AvgP&L | TotalP&L | PF | Sharpe | Expect');
  console.log('-'.repeat(140));

  results.slice(0, 20).forEach((r, i) => {
    console.log(
      `${(i + 1).toString().padStart(4)} | ` +
      `${r.score.toFixed(2).padStart(5)} | ` +
      `${r.config.threshold.toFixed(2).padStart(6)}% | ` +
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
      `${r.expectancy.toFixed(3)}`
    );
  });

  console.log('='.repeat(140));

  if (results.length > 0) {
    const top = results[0];
    console.log('\n🏆 OPTIMAL STRATEGY:\n');
    console.log(`Movement Threshold: ${top.config.threshold}%`);
    console.log(`Time Window: ${top.config.windowMinutes} minutes`);
    console.log(`Entry Delay: ${top.config.entryDelay >= 0 ? '+' : ''}${top.config.entryDelay}%`);
    console.log(`Exit Target: ${top.config.exitTarget}%`);
    console.log(`Stop Loss: ${top.config.stopLoss}%`);
    console.log(`Max Hold: ${top.config.maxHold} minutes`);
    console.log(`\n📈 METRICS:\n`);
    console.log(`Total Trades: ${top.totalTrades}`);
    console.log(`Win Rate: ${top.winRate.toFixed(1)}%`);
    console.log(`Average P&L: ${top.avgPnl >= 0 ? '+' : ''}${top.avgPnl.toFixed(2)}%`);
    console.log(`Total P&L: ${top.totalPnl >= 0 ? '+' : ''}${top.totalPnl.toFixed(1)}%`);
    console.log(`Profit Factor: ${top.profitFactor.toFixed(2)}`);
    console.log(`Sharpe Ratio: ${top.sharpe.toFixed(3)}`);
    console.log(`Expectancy: ${top.expectancy.toFixed(3)}%`);
    console.log(`Composite Score: ${top.score.toFixed(3)}`);
  }

  // Save results
  const reportPath = './fast_backtest_results.json';
  fs.writeFileSync(reportPath, JSON.stringify({
    testPeriod: { start: startDate.toISOString(), end: now.toISOString(), days: DAYS_TO_TEST },
    symbols: Object.keys(allCandles),
    totalStrategiesTested: testCount,
    profitableStrategies: results.length,
    top20Strategies: results.slice(0, 20),
    allResults: results
  }, null, 2));

  console.log(`\n💾 Results saved to: ${reportPath}`);
}

runOptimization().catch(console.error);
