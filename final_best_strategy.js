const fs = require('fs');
const readline = require('readline');

// CORRECTED FEE STRUCTURE
const TAKER_FEE = 0.012; // 1.2% market order on entry
const MAKER_FEE = 0.006; // 0.6% limit order on exit
const ROUND_TRIP_FEE = TAKER_FEE + MAKER_FEE; // 1.8% total

// REALISTIC MARKET FILTERS - Exclude dying coins
const MIN_PRICE = 0.05; // Exclude very cheap coins under $0.05
const MIN_AVG_VOLUME_USD = 2000; // Minimum $2k average volume
const MIN_VOLATILITY = 0.01; // Minimum 1% daily range
const MAX_SPREAD_PCT = 0.10; // Maximum 10% spread
const MAX_DUMP_CATASTROPHIC = -0.50; // Exclude dumps > 50% (delistings)

// More granular strategy parameters
const DUMP_THRESHOLDS = [-0.03, -0.04, -0.05, -0.06, -0.07, -0.08, -0.10, -0.12, -0.15, -0.20];
const EXIT_TARGETS = [0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12];
const MAX_HOLD_MINUTES = [30, 60, 90, 120, 180, 240];

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
    if (lineCount === 1) continue;

    const parts = line.split(',');
    if (parts.length < 7) continue;

    const ticker = parts[0];
    const volume = parseFloat(parts[1]);
    const open = parseFloat(parts[2]);
    const close = parseFloat(parts[3]);
    const high = parseFloat(parts[4]);
    const low = parseFloat(parts[5]);
    const timestamp = parseInt(parts[6]);

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

  for (const ticker in data) {
    data[ticker].sort((a, b) => a.timestamp - b.timestamp);
  }

  console.log(`   ✅ Parsed ${lineCount.toLocaleString()} lines`);
  console.log(`   ✅ Found ${Object.keys(data).length} unique tickers`);

  return data;
}

function calculateTickerMetrics(candles) {
  if (candles.length < 100) return null;

  let totalVolume = 0;
  let totalRange = 0;
  let validCandles = 0;
  let prices = [];
  let maxDrop = 0;

  for (let i = 1; i < candles.length; i++) {
    const candle = candles[i];
    const prev = candles[i - 1];

    const volumeUSD = candle.volume * candle.close;
    totalVolume += volumeUSD;

    const range = (candle.high - candle.low) / candle.low;
    totalRange += range;

    // Track max single-candle drop to detect dying coins
    const drop = (candle.low - prev.close) / prev.close;
    if (drop < maxDrop) maxDrop = drop;

    prices.push(candle.close);
    validCandles++;
  }

  const avgVolumeUSD = totalVolume / validCandles;
  const avgVolatility = totalRange / validCandles;
  const avgPrice = prices.reduce((a, b) => a + b, 0) / prices.length;

  return {
    avgVolumeUSD,
    avgVolatility,
    avgPrice,
    maxDrop,
    dataPoints: validCandles
  };
}

function passesMarketFilters(metrics) {
  if (!metrics) return false;

  // Exclude coins with catastrophic drops (likely delisted/dying)
  if (metrics.maxDrop < MAX_DUMP_CATASTROPHIC) {
    return false;
  }

  return (
    metrics.avgPrice >= MIN_PRICE &&
    metrics.avgVolumeUSD >= MIN_AVG_VOLUME_USD &&
    metrics.avgVolatility >= MIN_VOLATILITY
  );
}

function detectQualityDumps(candles, dumpThreshold, metrics) {
  const dumps = [];

  for (let i = 10; i < candles.length; i++) {
    const prev = candles[i - 1];
    const curr = candles[i];

    const dumpPercent = (curr.low - prev.close) / prev.close;

    // Skip catastrophic dumps (likely delisting/errors)
    if (dumpPercent < MAX_DUMP_CATASTROPHIC) {
      continue;
    }

    if (dumpPercent <= dumpThreshold) {
      const spread = (curr.high - curr.low) / curr.low;
      const volumeUSD = curr.volume * curr.close;

      if (spread > MAX_SPREAD_PCT || volumeUSD < MIN_AVG_VOLUME_USD / 4) {
        continue;
      }

      dumps.push({
        index: i,
        entryPrice: curr.low,
        timestamp: curr.timestamp,
        dumpPercent: dumpPercent * 100,
        spread: spread * 100,
        volumeUSD
      });
    }
  }

  return dumps;
}

function simulateTrade(candles, dumpIndex, entryPrice, exitTarget, maxHoldMinutes) {
  const maxExitIndex = Math.min(candles.length - 1, dumpIndex + maxHoldMinutes);
  const targetPrice = entryPrice * (1 + exitTarget);

  for (let i = dumpIndex; i <= maxExitIndex; i++) {
    const candle = candles[i];

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
  let filteredOutTickers = 0;

  for (const ticker in data) {
    const candles = data[ticker];
    if (candles.length < 100) continue;

    const metrics = calculateTickerMetrics(candles);

    if (!passesMarketFilters(metrics)) {
      filteredOutTickers++;
      continue;
    }

    const dumps = detectQualityDumps(candles, dumpThreshold, metrics);

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
        exitReason: trade.exitReason,
        volumeUSD: dump.volumeUSD,
        spread: dump.spread,
        avgPrice: metrics.avgPrice,
        avgVolatility: metrics.avgVolatility
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
    filteredOutTickers,
    trades
  };
}

function findBestStrategy(data) {
  console.log('\n🔍 Testing final optimized strategy combinations...\n');
  console.log(`📋 Market Filters:`);
  console.log(`   Min price: $${MIN_PRICE}`);
  console.log(`   Min avg volume: $${MIN_AVG_VOLUME_USD.toLocaleString()}`);
  console.log(`   Min volatility: ${(MIN_VOLATILITY * 100).toFixed(1)}%`);
  console.log(`   Max spread: ${(MAX_SPREAD_PCT * 100).toFixed(1)}%`);
  console.log(`   Exclude dumps > ${(MAX_DUMP_CATASTROPHIC * 100).toFixed(0)}% (delistings)\n`);

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

        // Score prioritizes avg return and win rate
        const score = result.avgReturn * (result.winRate / 100) * Math.sqrt(result.totalTrades);

        if (score > bestScore && result.totalTrades >= 20) {
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

  allResults.sort((a, b) => {
    const scoreA = a.avgReturn * (a.winRate / 100) * Math.sqrt(a.totalTrades);
    const scoreB = b.avgReturn * (b.winRate / 100) * Math.sqrt(b.totalTrades);
    return scoreB - scoreA;
  });

  return { bestStrategy, allResults };
}

async function main() {
  console.log('════════════════════════════════════════════════════════════════');
  console.log('   FINAL OPTIMIZED CRYPTO DUMP STRATEGY');
  console.log('════════════════════════════════════════════════════════════════');
  console.log(`\n💰 Fee Structure:`);
  console.log(`   Entry (taker): ${(TAKER_FEE * 100)}%`);
  console.log(`   Exit (maker): ${(MAKER_FEE * 100)}%`);
  console.log(`   Round trip: ${(ROUND_TRIP_FEE * 100)}%`);

  const files = [
    'C:/Users/Christian Okeke/bot/bot/2025-10-17 Crypto.csv',
    'C:/Users/Christian Okeke/bot/bot/2025-10-18 Crypto.csv',
    'C:/Users/Christian Okeke/bot/bot/2025-10-19 Crypto.csv'
  ];

  let allData = {};
  let totalCandles = 0;

  for (const file of files) {
    const data = await parseCSVFile(file);

    for (const ticker in data) {
      if (!allData[ticker]) {
        allData[ticker] = [];
      }
      allData[ticker] = allData[ticker].concat(data[ticker]);
      totalCandles += data[ticker].length;
    }
  }

  for (const ticker in allData) {
    allData[ticker].sort((a, b) => a.timestamp - b.timestamp);
  }

  console.log(`\n📈 Total dataset:`);
  console.log(`   ${totalCandles.toLocaleString()} candles`);
  console.log(`   ${Object.keys(allData).length} unique tickers`);

  const { bestStrategy, allResults } = findBestStrategy(allData);

  console.log('════════════════════════════════════════════════════════════════');
  console.log('   TOP 20 STRATEGIES');
  console.log('════════════════════════════════════════════════════════════════\n');

  for (let i = 0; i < Math.min(20, allResults.length); i++) {
    const r = allResults[i];
    const score = r.avgReturn * (r.winRate / 100) * Math.sqrt(r.totalTrades);

    console.log(`${(i + 1).toString().padStart(2)}. Dump: ${(r.dumpThreshold * 100).toFixed(1)}% | Exit: ${(r.exitTarget * 100).toFixed(1)}% | Hold: ${r.maxHoldMinutes}min`);
    console.log(`    Trades: ${r.totalTrades} | Win: ${r.winRate.toFixed(1)}% | Avg: ${r.avgReturn.toFixed(3)}% | Total: ${r.totalNetReturn.toFixed(1)}%`);
    console.log(`    Score: ${score.toFixed(2)}\n`);
  }

  console.log('════════════════════════════════════════════════════════════════');
  console.log('   🏆 BEST STRATEGY');
  console.log('════════════════════════════════════════════════════════════════\n');

  if (bestStrategy) {
    console.log(`📊 Parameters:`);
    console.log(`   Dump threshold: ${(bestStrategy.dumpThreshold * 100).toFixed(1)}%`);
    console.log(`   Exit target: ${(bestStrategy.exitTarget * 100).toFixed(1)}%`);
    console.log(`   Max hold time: ${bestStrategy.maxHoldMinutes} minutes\n`);

    console.log(`📈 Performance:`);
    console.log(`   Total trades: ${bestStrategy.totalTrades}`);
    console.log(`   Winners: ${bestStrategy.winners} (${bestStrategy.winRate.toFixed(2)}%)`);
    console.log(`   Losers: ${bestStrategy.losers}`);
    console.log(`   Average return per trade: ${bestStrategy.avgReturn.toFixed(3)}%`);
    console.log(`   Total cumulative return: ${bestStrategy.totalNetReturn.toFixed(2)}%`);
    console.log(`   Filtered tickers: ${bestStrategy.filteredOutTickers}\n`);

    // Top and worst trades
    const sortedTrades = [...bestStrategy.trades].sort((a, b) => b.netReturn - a.netReturn);

    console.log('💎 Top 30 Best Trades:');
    for (let i = 0; i < Math.min(30, sortedTrades.length); i++) {
      const t = sortedTrades[i];
      const date = new Date(t.timestamp / 1000000).toISOString().split('T')[0];
      console.log(`   ${(i + 1).toString().padStart(2)}. ${t.ticker.padEnd(15)} | ${date} | Dump: ${t.dumpPercent.toFixed(2)}% | Net: ${t.netReturn.toFixed(2)}% | Hold: ${t.holdMinutes}min`);
    }

    console.log('\n💩 Worst 20 Trades:');
    const worstTrades = sortedTrades.slice(-20).reverse();
    for (let i = 0; i < worstTrades.length; i++) {
      const t = worstTrades[i];
      const date = new Date(t.timestamp / 1000000).toISOString().split('T')[0];
      console.log(`   ${(i + 1).toString().padStart(2)}. ${t.ticker.padEnd(15)} | ${date} | Dump: ${t.dumpPercent.toFixed(2)}% | Net: ${t.netReturn.toFixed(2)}% | Hold: ${t.holdMinutes}min`);
    }

    // Distribution
    console.log('\n📊 Return Distribution:');
    const bins = [
      { label: '> +5%', count: 0 },
      { label: '+3% to +5%', count: 0 },
      { label: '+1% to +3%', count: 0 },
      { label: '0% to +1%', count: 0 },
      { label: '-1% to 0%', count: 0 },
      { label: '< -1%', count: 0 }
    ];

    for (const t of bestStrategy.trades) {
      if (t.netReturn > 5) bins[0].count++;
      else if (t.netReturn > 3) bins[1].count++;
      else if (t.netReturn > 1) bins[2].count++;
      else if (t.netReturn > 0) bins[3].count++;
      else if (t.netReturn > -1) bins[4].count++;
      else bins[5].count++;
    }

    for (const bin of bins) {
      const pct = (bin.count / bestStrategy.totalTrades * 100).toFixed(1);
      const bar = '█'.repeat(Math.floor(bin.count / bestStrategy.totalTrades * 50));
      console.log(`   ${bin.label.padEnd(15)}: ${bin.count.toString().padStart(4)} (${pct.padStart(5)}%) ${bar}`);
    }

    // Save results
    const outputFile = 'C:/Users/Christian Okeke/bot/bot/final_best_strategy_results.json';
    fs.writeFileSync(outputFile, JSON.stringify({
      feeStructure: {
        takerFee: TAKER_FEE,
        makerFee: MAKER_FEE,
        roundTripFee: ROUND_TRIP_FEE
      },
      marketFilters: {
        minPrice: MIN_PRICE,
        minAvgVolumeUSD: MIN_AVG_VOLUME_USD,
        minVolatility: MIN_VOLATILITY,
        maxSpreadPct: MAX_SPREAD_PCT,
        maxDumpCatastrophic: MAX_DUMP_CATASTROPHIC
      },
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
          totalNetReturn: bestStrategy.totalNetReturn,
          filteredOutTickers: bestStrategy.filteredOutTickers
        }
      },
      top20Strategies: allResults.slice(0, 20).map(r => ({
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

    console.log(`\n📁 Results saved to: ${outputFile}`);
  }

  console.log('\n════════════════════════════════════════════════════════════════');
  console.log('   ANALYSIS COMPLETE');
  console.log('════════════════════════════════════════════════════════════════\n');
}

main().catch(console.error);
