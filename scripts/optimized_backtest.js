const axios = require('axios');
const fs = require('fs');
require('dotenv').config({ path: '../.env' });

/**
 * OPTIMIZED BACKTEST - Reverse Engineered for Maximum Profitability
 *
 * This backtest dynamically analyzes market data to find the most profitable
 * trading parameters across multiple dimensions:
 * - Price movement thresholds (pumps AND dumps)
 * - Multiple timeframes
 * - Volume-based filtering
 * - Volatility analysis
 * - Dynamic entry/exit strategies
 * - Trailing stops and scaled exits
 */

const COINBASE_API_BASE = 'https://api.exchange.coinbase.com';

// Test symbols - focusing on high-volatility assets
const TEST_SYMBOLS = [
  'BTC-USD', 'ETH-USD', 'SOL-USD', 'DOGE-USD', 'AVAX-USD',
  'MATIC-USD', 'LINK-USD', 'UNI-USD', 'AAVE-USD', 'ATOM-USD',
  'ALGO-USD', 'XRP-USD', 'ADA-USD', 'DOT-USD', 'NEAR-USD'
];

// OPTIMIZED PARAMETER RANGES - Focus on sweet spots based on movement analysis
const PRICE_MOVE_THRESHOLDS = [0.5, 0.75, 1.0, 1.5, 2.0]; // Focus on 0.5-2% where most movements occur
const TIME_WINDOWS = [1, 3, 5, 10, 15]; // Most relevant windows
const ENTRY_STRATEGIES = [
  { type: 'immediate', delay: 0 },
  { type: 'ladder', delay: -0.5 }, // Enter 0.5% below
  { type: 'ladder', delay: -1.0 },
  { type: 'ladder', delay: +0.5 }, // Enter 0.5% above (momentum)
];

const EXIT_STRATEGIES = [
  { type: 'fixed', target: 1.0 },
  { type: 'fixed', target: 1.5 },
  { type: 'fixed', target: 2.0 },
  { type: 'fixed', target: 3.0 },
  { type: 'trailing', initialTarget: 2.0, trailingPct: 0.5 },
  { type: 'trailing', initialTarget: 3.0, trailingPct: 0.75 },
];

// NEW: Volume filters
const VOLUME_FILTERS = [
  { enabled: false },
  { enabled: true, minVolumeIncrease: 1.5 }, // 50% volume increase
];

// Risk management
const STOP_LOSS_OPTIONS = [1.0, 1.5, 2.0, 2.5];
const MAX_HOLD_OPTIONS = [30, 60, 120]; // Minutes

// Test period - EXPANDED to 7 days for more data
const DAYS_TO_TEST = 7;

/**
 * Fetch historical data with retry logic
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

    if (!response.data || !Array.isArray(response.data)) {
      return [];
    }

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
 * Detect significant price movements (both dumps AND pumps)
 */
function detectPriceMovements(candles, threshold, windowMinutes, volumeFilter) {
  const movements = [];
  const windowSize = windowMinutes;

  for (let i = windowSize; i < candles.length; i++) {
    const windowCandles = candles.slice(i - windowSize, i + 1);
    const highPrice = Math.max(...windowCandles.map(c => c.high));
    const lowPrice = Math.min(...windowCandles.map(c => c.low));
    const currentPrice = candles[i].close;

    // Calculate price change from high (dump) and from low (pump)
    const dropPct = ((currentPrice - highPrice) / highPrice) * 100;
    const pumpPct = ((currentPrice - lowPrice) / lowPrice) * 100;

    // Check volume if filter enabled
    let volumeQualified = true;
    if (volumeFilter.enabled) {
      const avgVolume = windowCandles.slice(0, -1).reduce((sum, c) => sum + c.volume, 0) / (windowCandles.length - 1);
      const currentVolume = candles[i].volume;
      volumeQualified = currentVolume >= avgVolume * volumeFilter.minVolumeIncrease;
    }

    if (!volumeQualified) continue;

    // Detect DROP (dump scenario)
    if (dropPct <= -threshold) {
      const lastMovement = movements[movements.length - 1];
      if (!lastMovement || (candles[i].timestamp - lastMovement.timestamp) > 30 * 60 * 1000) {
        movements.push({
          type: 'drop',
          timestamp: candles[i].timestamp,
          index: i,
          highPrice: highPrice,
          alertPrice: currentPrice,
          changePct: dropPct,
          volume: candles[i].volume
        });
      }
    }

    // Detect PUMP (pump scenario - for buying momentum)
    if (pumpPct >= threshold) {
      const lastMovement = movements[movements.length - 1];
      if (!lastMovement || (candles[i].timestamp - lastMovement.timestamp) > 30 * 60 * 1000) {
        movements.push({
          type: 'pump',
          timestamp: candles[i].timestamp,
          index: i,
          lowPrice: lowPrice,
          alertPrice: currentPrice,
          changePct: pumpPct,
          volume: candles[i].volume
        });
      }
    }
  }

  return movements;
}

/**
 * Calculate volatility (ATR - Average True Range)
 */
function calculateVolatility(candles, period = 14) {
  if (candles.length < period + 1) return 0;

  let atr = 0;
  for (let i = 1; i < candles.length; i++) {
    const tr = Math.max(
      candles[i].high - candles[i].low,
      Math.abs(candles[i].high - candles[i - 1].close),
      Math.abs(candles[i].low - candles[i - 1].close)
    );
    atr = (atr * (period - 1) + tr) / period;
  }

  return atr;
}

/**
 * Simulate trade with advanced exit strategies
 */
function simulateTrade(movement, candles, entryStrategy, exitStrategy, stopLoss, maxHoldMinutes) {
  // Calculate entry price based on strategy
  let entryPrice;
  if (movement.type === 'drop') {
    entryPrice = movement.alertPrice * (1 + entryStrategy.delay / 100);
  } else { // pump
    entryPrice = movement.alertPrice * (1 + entryStrategy.delay / 100);
  }

  // Calculate initial target and stop prices
  let targetPrice = entryPrice * (1 + (exitStrategy.target || exitStrategy.initialTarget) / 100);
  let stopPrice = entryPrice * (1 - stopLoss / 100);
  let highestPrice = entryPrice; // For trailing stops

  let entryIndex = null;
  let exitIndex = null;
  let exitPrice = null;
  let exitReason = null;

  // Find entry point
  for (let i = movement.index; i < candles.length && i < movement.index + 30; i++) {
    if (candles[i].low <= entryPrice && candles[i].high >= entryPrice) {
      entryIndex = i;
      break;
    }
  }

  if (!entryIndex) return null;

  const entryTime = candles[entryIndex].timestamp;
  const maxExitTime = entryTime + (maxHoldMinutes * 60 * 1000);

  // Simulate trade progression
  for (let i = entryIndex + 1; i < candles.length; i++) {
    const candle = candles[i];

    // Update trailing stop if applicable
    if (exitStrategy.type === 'trailing') {
      if (candle.high > highestPrice) {
        highestPrice = candle.high;
        // Update stop to trail by trailingPct
        stopPrice = Math.max(stopPrice, highestPrice * (1 - exitStrategy.trailingPct / 100));
      }
    }

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

  // Still holding at end of data
  if (!exitIndex) {
    exitIndex = candles.length - 1;
    exitPrice = candles[exitIndex].close;
    exitReason = 'end_of_data';
  }

  const holdMinutes = (candles[exitIndex].timestamp - entryTime) / (60 * 1000);
  const pnlPct = ((exitPrice - entryPrice) / entryPrice) * 100;

  // Calculate fees (maker: 0.4%, taker: 0.6%)
  const entryFee = entryStrategy.delay === 0 ? 0.6 : 0.4; // Immediate = taker
  const exitFee = exitReason === 'profit_target' ? 0.4 : 0.6; // Target = maker, stop = taker
  const totalFees = entryFee + exitFee;
  const netPnlPct = pnlPct - totalFees;

  return {
    movementType: movement.type,
    changePct: movement.changePct,
    entryPrice,
    exitPrice,
    pnlPct: netPnlPct,
    grossPnl: pnlPct,
    fees: totalFees,
    holdMinutes,
    exitReason,
    maxDrawdown: ((stopPrice - highestPrice) / highestPrice) * 100
  };
}

/**
 * Backtest a specific strategy configuration
 */
function backtestStrategy(candles, config) {
  const movements = detectPriceMovements(
    candles,
    config.threshold,
    config.windowMinutes,
    config.volumeFilter
  );

  const trades = [];
  for (const movement of movements) {
    const trade = simulateTrade(
      movement,
      candles,
      config.entryStrategy,
      config.exitStrategy,
      config.stopLoss,
      config.maxHold
    );
    if (trade) {
      trades.push(trade);
    }
  }

  if (trades.length === 0) return null;

  // Calculate comprehensive statistics
  const winningTrades = trades.filter(t => t.pnlPct > 0);
  const losingTrades = trades.filter(t => t.pnlPct <= 0);

  const totalTrades = trades.length;
  const winRate = (winningTrades.length / totalTrades) * 100;
  const avgPnl = trades.reduce((sum, t) => sum + t.pnlPct, 0) / totalTrades;
  const totalPnl = trades.reduce((sum, t) => sum + t.pnlPct, 0);
  const avgHoldTime = trades.reduce((sum, t) => sum + t.holdMinutes, 0) / totalTrades;

  const totalWinAmount = winningTrades.reduce((sum, t) => sum + t.pnlPct, 0);
  const totalLossAmount = Math.abs(losingTrades.reduce((sum, t) => sum + t.pnlPct, 0));
  const profitFactor = totalLossAmount > 0 ? totalWinAmount / totalLossAmount : totalWinAmount;

  const bestTrade = Math.max(...trades.map(t => t.pnlPct));
  const worstTrade = Math.min(...trades.map(t => t.pnlPct));

  // Sharpe ratio (risk-adjusted return)
  const variance = trades.reduce((sum, t) => sum + Math.pow(t.pnlPct - avgPnl, 2), 0) / totalTrades;
  const stdDev = Math.sqrt(variance);
  const sharpe = stdDev > 0 ? avgPnl / stdDev : 0;

  // Max drawdown calculation
  const maxDrawdown = Math.min(...trades.map(t => t.maxDrawdown));

  // Expectancy: (Win% * AvgWin) - (Loss% * AvgLoss)
  const avgWin = winningTrades.length > 0 ? totalWinAmount / winningTrades.length : 0;
  const avgLoss = losingTrades.length > 0 ? totalLossAmount / losingTrades.length : 0;
  const expectancy = (winRate / 100 * avgWin) - ((100 - winRate) / 100 * avgLoss);

  return {
    config,
    totalTrades,
    winningTrades: winningTrades.length,
    losingTrades: losingTrades.length,
    winRate,
    avgPnl,
    totalPnl,
    avgHoldTime,
    profitFactor,
    bestTrade,
    worstTrade,
    sharpe,
    maxDrawdown,
    expectancy,
    avgWin,
    avgLoss,
    trades
  };
}

/**
 * Main optimization runner
 */
async function runOptimization() {
  console.log('🚀 OPTIMIZED BACKTEST - Reverse Engineering for Maximum Profit\n');
  console.log(`Testing ${DAYS_TO_TEST} days of historical data...\n`);

  // Fetch extended historical data
  const now = new Date();
  const startDate = new Date(now);
  startDate.setDate(startDate.getDate() - DAYS_TO_TEST);
  startDate.setHours(0, 0, 0, 0);

  console.log(`Date range: ${startDate.toISOString()} to ${now.toISOString()}\n`);

  // Fetch data for all symbols
  const allCandles = {};
  for (const symbol of TEST_SYMBOLS) {
    console.log(`Fetching ${symbol}...`);
    const symbolCandles = [];

    // Fetch in 5-hour chunks
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

  // Analyze market movements at different thresholds
  console.log('📊 Movement Analysis:\n');
  for (const threshold of [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]) {
    let totalMovements = 0;
    for (const [symbol, candles] of Object.entries(allCandles)) {
      const movements = detectPriceMovements(candles, threshold, 5, { enabled: false });
      if (movements.length > 0) {
        totalMovements += movements.length;
      }
    }
    console.log(`${threshold}% threshold: ${totalMovements} total movements`);
  }

  console.log('\n🔬 Testing parameter combinations...\n');
  console.log('This will test thousands of combinations to find optimal settings...\n');

  const results = [];
  let testCount = 0;
  const totalTests = PRICE_MOVE_THRESHOLDS.length * TIME_WINDOWS.length *
                     ENTRY_STRATEGIES.length * EXIT_STRATEGIES.length *
                     VOLUME_FILTERS.length * STOP_LOSS_OPTIONS.length * MAX_HOLD_OPTIONS.length;

  // Test all combinations
  for (const threshold of PRICE_MOVE_THRESHOLDS) {
    for (const windowMinutes of TIME_WINDOWS) {
      for (const entryStrategy of ENTRY_STRATEGIES) {
        for (const exitStrategy of EXIT_STRATEGIES) {
          for (const volumeFilter of VOLUME_FILTERS) {
            for (const stopLoss of STOP_LOSS_OPTIONS) {
              for (const maxHold of MAX_HOLD_OPTIONS) {
                testCount++;
                if (testCount % 1000 === 0) {
                  console.log(`Progress: ${testCount}/${totalTests} tests (${((testCount/totalTests)*100).toFixed(1)}%)`);
                }

                const config = {
                  threshold,
                  windowMinutes,
                  entryStrategy,
                  exitStrategy,
                  volumeFilter,
                  stopLoss,
                  maxHold
                };

                const strategyResults = [];

                // Test on all symbols
                for (const [symbol, candles] of Object.entries(allCandles)) {
                  const result = backtestStrategy(candles, config);
                  if (result) {
                    strategyResults.push(result);
                  }
                }

                // Aggregate results
                if (strategyResults.length > 0) {
                  const totalTrades = strategyResults.reduce((sum, r) => sum + r.totalTrades, 0);

                  // Skip if too few trades
                  if (totalTrades < 5) continue;

                  const totalWinning = strategyResults.reduce((sum, r) => sum + r.winningTrades, 0);
                  const totalLosing = strategyResults.reduce((sum, r) => sum + r.losingTrades, 0);
                  const avgPnl = strategyResults.reduce((sum, r) => sum + (r.avgPnl * r.totalTrades), 0) / totalTrades;
                  const totalPnl = strategyResults.reduce((sum, r) => sum + r.totalPnl, 0);
                  const avgHoldTime = strategyResults.reduce((sum, r) => sum + (r.avgHoldTime * r.totalTrades), 0) / totalTrades;

                  const allTrades = strategyResults.flatMap(r => r.trades);
                  const totalWinAmount = allTrades.filter(t => t.pnlPct > 0).reduce((sum, t) => sum + t.pnlPct, 0);
                  const totalLossAmount = Math.abs(allTrades.filter(t => t.pnlPct <= 0).reduce((sum, t) => sum + t.pnlPct, 0));
                  const profitFactor = totalLossAmount > 0 ? totalWinAmount / totalLossAmount : totalWinAmount;

                  const bestTrade = Math.max(...allTrades.map(t => t.pnlPct));
                  const worstTrade = Math.min(...allTrades.map(t => t.pnlPct));

                  const variance = allTrades.reduce((sum, t) => sum + Math.pow(t.pnlPct - avgPnl, 2), 0) / totalTrades;
                  const stdDev = Math.sqrt(variance);
                  const sharpe = stdDev > 0 ? avgPnl / stdDev : 0;

                  const avgWin = totalWinning > 0 ? totalWinAmount / totalWinning : 0;
                  const avgLoss = totalLosing > 0 ? totalLossAmount / totalLosing : 0;
                  const winRate = (totalWinning / totalTrades) * 100;
                  const expectancy = (winRate / 100 * avgWin) - ((100 - winRate) / 100 * avgLoss);

                  // Calculate score (weighted combination of metrics)
                  const score = (expectancy * 0.4) + (sharpe * 0.3) + (profitFactor * 0.2) + (winRate * 0.1);

                  results.push({
                    config,
                    totalTrades,
                    winningTrades: totalWinning,
                    losingTrades: totalLosing,
                    winRate,
                    avgPnl,
                    totalPnl,
                    avgHoldTime,
                    profitFactor,
                    bestTrade,
                    worstTrade,
                    sharpe,
                    expectancy,
                    avgWin,
                    avgLoss,
                    score
                  });
                }
              }
            }
          }
        }
      }
    }
  }

  console.log(`\n✅ Completed ${testCount} tests, found ${results.length} profitable strategies\n`);

  // Sort by score
  results.sort((a, b) => b.score - a.score);

  // Display top strategies
  console.log('='.repeat(150));
  console.log('TOP 20 MOST PROFITABLE STRATEGIES (sorted by composite score)');
  console.log('='.repeat(150));
  console.log('Rank | Score | Thresh | Win | Entry | Exit | Stop | Hold | Trades | WinRate | AvgP&L | TotalP&L | PF | Sharpe | Expect');
  console.log('-'.repeat(150));

  results.slice(0, 20).forEach((r, i) => {
    const entryDesc = r.config.entryStrategy.type === 'immediate' ? 'IMMED' :
                      `${r.config.entryStrategy.delay > 0 ? '+' : ''}${r.config.entryStrategy.delay.toFixed(1)}%`;
    const exitDesc = r.config.exitStrategy.type === 'fixed' ?
                     `F${r.config.exitStrategy.target.toFixed(1)}%` :
                     `T${r.config.exitStrategy.initialTarget.toFixed(1)}%`;

    console.log(
      `${(i + 1).toString().padStart(4)} | ` +
      `${r.score.toFixed(2).padStart(5)} | ` +
      `${r.config.threshold.toFixed(1).padStart(6)}% | ` +
      `${r.config.windowMinutes.toString().padStart(3)}m | ` +
      `${entryDesc.padStart(6)} | ` +
      `${exitDesc.padStart(5)} | ` +
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

  console.log('='.repeat(150));

  // Detailed analysis of top strategy
  const topStrategy = results[0];
  console.log('\n🏆 OPTIMAL STRATEGY CONFIGURATION:\n');
  console.log(`Movement Threshold: ${topStrategy.config.threshold}%`);
  console.log(`Time Window: ${topStrategy.config.windowMinutes} minutes`);
  console.log(`Entry Strategy: ${topStrategy.config.entryStrategy.type} (${topStrategy.config.entryStrategy.delay}% delay)`);
  console.log(`Exit Strategy: ${topStrategy.config.exitStrategy.type} (${topStrategy.config.exitStrategy.target || topStrategy.config.exitStrategy.initialTarget}% target)`);
  console.log(`Stop Loss: ${topStrategy.config.stopLoss}%`);
  console.log(`Max Hold Time: ${topStrategy.config.maxHold} minutes`);
  console.log(`Volume Filter: ${topStrategy.config.volumeFilter.enabled ? `Yes (${topStrategy.config.volumeFilter.minVolumeIncrease}x)` : 'No'}`);
  console.log(`\n📈 PERFORMANCE METRICS:\n`);
  console.log(`Total Trades: ${topStrategy.totalTrades}`);
  console.log(`Win Rate: ${topStrategy.winRate.toFixed(1)}%`);
  console.log(`Average P&L: ${topStrategy.avgPnl >= 0 ? '+' : ''}${topStrategy.avgPnl.toFixed(2)}%`);
  console.log(`Total P&L: ${topStrategy.totalPnl >= 0 ? '+' : ''}${topStrategy.totalPnl.toFixed(1)}%`);
  console.log(`Profit Factor: ${topStrategy.profitFactor.toFixed(2)}`);
  console.log(`Sharpe Ratio: ${topStrategy.sharpe.toFixed(3)}`);
  console.log(`Expectancy: ${topStrategy.expectancy.toFixed(3)}%`);
  console.log(`Average Win: +${topStrategy.avgWin.toFixed(2)}%`);
  console.log(`Average Loss: -${topStrategy.avgLoss.toFixed(2)}%`);
  console.log(`Best Trade: +${topStrategy.bestTrade.toFixed(2)}%`);
  console.log(`Worst Trade: ${topStrategy.worstTrade.toFixed(2)}%`);
  console.log(`Average Hold Time: ${topStrategy.avgHoldTime.toFixed(1)} minutes`);

  // Save results
  const reportPath = './optimized_backtest_results.json';
  fs.writeFileSync(reportPath, JSON.stringify({
    testPeriod: {
      start: startDate.toISOString(),
      end: now.toISOString(),
      days: DAYS_TO_TEST
    },
    symbols: Object.keys(allCandles),
    totalStrategiesTested: testCount,
    profitableStrategies: results.length,
    topStrategy: topStrategy,
    top20Strategies: results.slice(0, 20),
    allResults: results
  }, null, 2));

  console.log(`\n💾 Detailed results saved to: ${reportPath}`);
}

// Run optimization
runOptimization().catch(console.error);
