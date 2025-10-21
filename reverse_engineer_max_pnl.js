const fs = require('fs');
const readline = require('readline');

// Fee structure - assuming market orders (taker fees)
const TAKER_FEE = 0.012; // 1.2% per trade
const ROUND_TRIP_FEE = TAKER_FEE * 2; // 2.4% total (buy + sell)

// Strategy parameters to test
const DUMP_THRESHOLDS = [-0.01, -0.015, -0.02, -0.025, -0.03, -0.04, -0.05]; // -1% to -5%
const EXIT_TARGETS = [0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05]; // 0.5% to 5%
const MAX_HOLD_MINUTES = [5, 10, 15, 30, 60, 120, 240]; // 5min to 4 hours

// Results tracking
let allTrades = [];
let dailyResults = {};

async function parseCSVFile(filePath) {
  console.log(`\n📊 Parsing ${filePath}...`);
  const fileStream = fs.createReadStream(filePath);
  const rl = readline.createInterface({
    input: fileStream,
    crlfDelay: Infinity
  });

  const data = {};
  let lineCount = 0;

  for await (const line of rl) {
    lineCount++;
    if (lineCount === 1) continue; // Skip header

    const parts = line.split(',');
    if (parts.length < 7) continue;

    const ticker = parts[0];
    const volume = parseFloat(parts[1]);
    const open = parseFloat(parts[2]);
    const close = parseFloat(parts[3]);
    const high = parseFloat(parts[4]);
    const low = parseFloat(parts[5]);
    const timestamp = parseInt(parts[6]);

    // Skip invalid data
    if (isNaN(open) || isNaN(close) || isNaN(high) || isNaN(low) || open === 0) {
      continue;
    }

    if (!data[ticker]) {
      data[ticker] = [];
    }

    data[ticker].push({
      ticker,
      timestamp,
      open,
      close,
      high,
      low,
      volume
    });
  }

  // Sort each ticker's data by timestamp
  for (const ticker in data) {
    data[ticker].sort((a, b) => a.timestamp - b.timestamp);
  }

  console.log(`   ✅ Parsed ${lineCount.toLocaleString()} lines`);
  console.log(`   ✅ Found ${Object.keys(data).length} unique tickers`);

  return data;
}

function detectDumps(candles, dumpThreshold) {
  const dumps = [];

  for (let i = 1; i < candles.length; i++) {
    const prev = candles[i - 1];
    const curr = candles[i];

    // Calculate dump % from previous close to current low
    const dumpPercent = (curr.low - prev.close) / prev.close;

    if (dumpPercent <= dumpThreshold) {
      dumps.push({
        index: i,
        entryPrice: curr.low, // Buy at the low
        timestamp: curr.timestamp,
        dumpPercent: dumpPercent * 100
      });
    }
  }

  return dumps;
}

function simulateTrade(candles, dumpIndex, entryPrice, exitTarget, maxHoldMinutes) {
  const entryCandle = candles[dumpIndex];
  const maxExitIndex = Math.min(candles.length - 1, dumpIndex + maxHoldMinutes);

  const targetPrice = entryPrice * (1 + exitTarget);

  // Look for exit in subsequent candles
  for (let i = dumpIndex; i <= maxExitIndex; i++) {
    const candle = candles[i];

    // Check if target was hit (high reached our target)
    if (candle.high >= targetPrice) {
      const exitPrice = targetPrice;
      const grossReturn = (exitPrice - entryPrice) / entryPrice;
      const netReturn = grossReturn - ROUND_TRIP_FEE;
      const holdMinutes = i - dumpIndex;

      return {
        success: true,
        exitPrice,
        grossReturn: grossReturn * 100,
        netReturn: netReturn * 100,
        holdMinutes,
        exitReason: 'target'
      };
    }
  }

  // Exit at max hold time
  const exitCandle = candles[maxExitIndex];
  const exitPrice = exitCandle.close;
  const grossReturn = (exitPrice - entryPrice) / entryPrice;
  const netReturn = grossReturn - ROUND_TRIP_FEE;
  const holdMinutes = maxExitIndex - dumpIndex;

  return {
    success: netReturn > 0,
    exitPrice,
    grossReturn: grossReturn * 100,
    netReturn: netReturn * 100,
    holdMinutes,
    exitReason: 'timeout'
  };
}

function analyzeStrategy(data, dumpThreshold, exitTarget, maxHoldMinutes) {
  let trades = [];
  let totalTrades = 0;
  let winners = 0;
  let losers = 0;
  let totalNetReturn = 0;

  for (const ticker in data) {
    const candles = data[ticker];
    if (candles.length < 10) continue; // Need minimum data

    const dumps = detectDumps(candles, dumpThreshold);

    for (const dump of dumps) {
      const trade = simulateTrade(candles, dump.index, dump.entryPrice, exitTarget, maxHoldMinutes);

      totalTrades++;
      if (trade.netReturn > 0) {
        winners++;
      } else {
        losers++;
      }
      totalNetReturn += trade.netReturn;

      trades.push({
        ticker,
        timestamp: dump.timestamp,
        dumpPercent: dump.dumpPercent,
        entryPrice: dump.entryPrice,
        exitPrice: trade.exitPrice,
        grossReturn: trade.grossReturn,
        netReturn: trade.netReturn,
        holdMinutes: trade.holdMinutes,
        exitReason: trade.exitReason
      });
    }
  }

  const winRate = totalTrades > 0 ? (winners / totalTrades) * 100 : 0;
  const avgReturn = totalTrades > 0 ? totalNetReturn / totalTrades : 0;

  return {
    dumpThreshold,
    exitTarget,
    maxHoldMinutes,
    totalTrades,
    winners,
    losers,
    winRate,
    avgReturn,
    totalNetReturn,
    trades
  };
}

function findBestStrategy(data) {
  console.log('\n🔍 Testing all strategy combinations...\n');

  let bestStrategy = null;
  let bestScore = -Infinity;
  const allResults = [];

  let testCount = 0;
  const totalTests = DUMP_THRESHOLDS.length * EXIT_TARGETS.length * MAX_HOLD_MINUTES.length;

  for (const dumpThreshold of DUMP_THRESHOLDS) {
    for (const exitTarget of EXIT_TARGETS) {
      for (const maxHoldMinutes of MAX_HOLD_MINUTES) {
        testCount++;

        const result = analyzeStrategy(data, dumpThreshold, exitTarget, maxHoldMinutes);
        allResults.push(result);

        // Score = total net return * win rate (prefer strategies that work consistently)
        const score = result.totalNetReturn * (result.winRate / 100);

        if (score > bestScore && result.totalTrades >= 10) {
          bestScore = score;
          bestStrategy = result;
        }

        if (testCount % 10 === 0) {
          process.stdout.write(`   Progress: ${testCount}/${totalTests} (${((testCount/totalTests)*100).toFixed(1)}%)\r`);
        }
      }
    }
  }

  console.log(`\n   ✅ Tested ${totalTests} strategy combinations\n`);

  // Sort by score
  allResults.sort((a, b) => {
    const scoreA = a.totalNetReturn * (a.winRate / 100);
    const scoreB = b.totalNetReturn * (b.winRate / 100);
    return scoreB - scoreA;
  });

  return { bestStrategy, allResults };
}

async function main() {
  console.log('════════════════════════════════════════════════════════════════');
  console.log('   CRYPTO DUMP STRATEGY - REVERSE ENGINEERING MAXIMUM P&L');
  console.log('════════════════════════════════════════════════════════════════');
  console.log(`\n💰 Fee Structure: ${(TAKER_FEE * 100)}% per trade (${(ROUND_TRIP_FEE * 100)}% round trip)`);

  const files = [
    'C:/Users/Christian Okeke/bot/bot/2025-10-17 Crypto.csv',
    'C:/Users/Christian Okeke/bot/bot/2025-10-18 Crypto.csv',
    'C:/Users/Christian Okeke/bot/bot/2025-10-19 Crypto.csv'
  ];

  let allData = {};
  let totalCandles = 0;

  // Parse all files
  for (const file of files) {
    const data = await parseCSVFile(file);

    // Merge data
    for (const ticker in data) {
      if (!allData[ticker]) {
        allData[ticker] = [];
      }
      allData[ticker] = allData[ticker].concat(data[ticker]);
      totalCandles += data[ticker].length;
    }
  }

  // Sort merged data
  for (const ticker in allData) {
    allData[ticker].sort((a, b) => a.timestamp - b.timestamp);
  }

  console.log(`\n📈 Total dataset:`);
  console.log(`   ${totalCandles.toLocaleString()} candles`);
  console.log(`   ${Object.keys(allData).length} unique tickers`);

  // Find best strategy
  const { bestStrategy, allResults } = findBestStrategy(allData);

  // Display results
  console.log('════════════════════════════════════════════════════════════════');
  console.log('   TOP 10 STRATEGIES BY SCORE');
  console.log('════════════════════════════════════════════════════════════════\n');

  for (let i = 0; i < Math.min(10, allResults.length); i++) {
    const r = allResults[i];
    const score = r.totalNetReturn * (r.winRate / 100);

    console.log(`${i + 1}. Dump: ${(r.dumpThreshold * 100).toFixed(1)}% | Exit: ${(r.exitTarget * 100).toFixed(1)}% | Hold: ${r.maxHoldMinutes}min`);
    console.log(`   Trades: ${r.totalTrades} | Win Rate: ${r.winRate.toFixed(1)}% | Avg: ${r.avgReturn.toFixed(3)}%`);
    console.log(`   Total P&L: ${r.totalNetReturn.toFixed(2)}% | Score: ${score.toFixed(2)}\n`);
  }

  console.log('════════════════════════════════════════════════════════════════');
  console.log('   BEST STRATEGY DETAILS');
  console.log('════════════════════════════════════════════════════════════════\n');

  if (bestStrategy) {
    console.log(`📊 Strategy Parameters:`);
    console.log(`   Dump threshold: ${(bestStrategy.dumpThreshold * 100).toFixed(1)}%`);
    console.log(`   Exit target: ${(bestStrategy.exitTarget * 100).toFixed(1)}%`);
    console.log(`   Max hold time: ${bestStrategy.maxHoldMinutes} minutes\n`);

    console.log(`📈 Performance Metrics:`);
    console.log(`   Total trades: ${bestStrategy.totalTrades}`);
    console.log(`   Winners: ${bestStrategy.winners} (${bestStrategy.winRate.toFixed(2)}%)`);
    console.log(`   Losers: ${bestStrategy.losers}`);
    console.log(`   Average return per trade: ${bestStrategy.avgReturn.toFixed(3)}%`);
    console.log(`   Total cumulative return: ${bestStrategy.totalNetReturn.toFixed(2)}%\n`);

    // Best trades
    const sortedTrades = [...bestStrategy.trades].sort((a, b) => b.netReturn - a.netReturn);

    console.log('💎 Top 20 Best Trades:');
    for (let i = 0; i < Math.min(20, sortedTrades.length); i++) {
      const t = sortedTrades[i];
      const date = new Date(t.timestamp / 1000000).toISOString().split('T')[0];
      console.log(`   ${i + 1}. ${t.ticker.padEnd(15)} | ${date} | Dump: ${t.dumpPercent.toFixed(2)}% | Return: ${t.netReturn.toFixed(2)}% | Hold: ${t.holdMinutes}min`);
    }

    console.log('\n💩 Top 20 Worst Trades:');
    const worstTrades = sortedTrades.slice(-20).reverse();
    for (let i = 0; i < worstTrades.length; i++) {
      const t = worstTrades[i];
      const date = new Date(t.timestamp / 1000000).toISOString().split('T')[0];
      console.log(`   ${i + 1}. ${t.ticker.padEnd(15)} | ${date} | Dump: ${t.dumpPercent.toFixed(2)}% | Return: ${t.netReturn.toFixed(2)}% | Hold: ${t.holdMinutes}min`);
    }

    // Save detailed results
    const outputFile = 'C:/Users/Christian Okeke/bot/bot/strategy_analysis_results.json';
    fs.writeFileSync(outputFile, JSON.stringify({
      bestStrategy: {
        parameters: {
          dumpThreshold: bestStrategy.dumpThreshold,
          exitTarget: bestStrategy.exitTarget,
          maxHoldMinutes: bestStrategy.maxHoldMinutes
        },
        metrics: {
          totalTrades: bestStrategy.totalTrades,
          winners: bestStrategy.winners,
          losers: bestStrategy.losers,
          winRate: bestStrategy.winRate,
          avgReturn: bestStrategy.avgReturn,
          totalNetReturn: bestStrategy.totalNetReturn
        }
      },
      top10Strategies: allResults.slice(0, 10).map(r => ({
        parameters: {
          dumpThreshold: r.dumpThreshold,
          exitTarget: r.exitTarget,
          maxHoldMinutes: r.maxHoldMinutes
        },
        metrics: {
          totalTrades: r.totalTrades,
          winRate: r.winRate,
          avgReturn: r.avgReturn,
          totalNetReturn: r.totalNetReturn
        }
      })),
      allTrades: bestStrategy.trades
    }, null, 2));

    console.log(`\n📁 Detailed results saved to: ${outputFile}`);
  }

  console.log('\n════════════════════════════════════════════════════════════════');
  console.log('   ANALYSIS COMPLETE');
  console.log('════════════════════════════════════════════════════════════════\n');
}

main().catch(console.error);
