const fs = require('fs');
const readline = require('readline');

// CORRECTED FEE STRUCTURE
const TAKER_FEE = 0.012; // 1.2% market order on entry (fast execution to catch dump)
const MAKER_FEE = 0.006; // 0.6% limit order on exit (patient exit at target)
const ROUND_TRIP_FEE = TAKER_FEE + MAKER_FEE; // 1.8% total

// MARKET CONDITION FILTERS - More reasonable for crypto
const MIN_PRICE = 0.01; // Avoid very low penny stocks under $0.01
const MIN_AVG_VOLUME_USD = 500; // Minimum $500 average volume per candle
const MIN_VOLATILITY = 0.005; // Minimum 0.5% daily range (high-low)
const MAX_SPREAD_PCT = 0.15; // Maximum 15% spread (avoid illiquid markets)

// Strategy parameters to test - MORE SELECTIVE
const DUMP_THRESHOLDS = [-0.03, -0.04, -0.05, -0.06, -0.08, -0.10]; // 3% to 10% dumps
const EXIT_TARGETS = [0.03, 0.04, 0.05, 0.06, 0.08, 0.10]; // 3% to 10% exits
const MAX_HOLD_MINUTES = [15, 30, 60, 120, 240]; // 15min to 4 hours

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

function calculateTickerMetrics(candles) {
  if (candles.length < 100) return null; // Need enough data

  let totalVolume = 0;
  let totalRange = 0;
  let validCandles = 0;
  let prices = [];

  for (const candle of candles) {
    const volumeUSD = candle.volume * candle.close;
    totalVolume += volumeUSD;

    const range = (candle.high - candle.low) / candle.low;
    totalRange += range;

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
    dataPoints: validCandles
  };
}

function passesMarketFilters(metrics) {
  if (!metrics) return false;

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

    // Calculate dump % from previous close to current low
    const dumpPercent = (curr.low - prev.close) / prev.close;

    if (dumpPercent <= dumpThreshold) {
      // Additional quality filters per dump
      const spread = (curr.high - curr.low) / curr.low;
      const volumeUSD = curr.volume * curr.close;

      // Skip if spread too wide (illiquid) or volume too low
      if (spread > MAX_SPREAD_PCT || volumeUSD < MIN_AVG_VOLUME_USD / 10) {
        continue;
      }

      // Calculate recent volatility (last 10 candles)
      const recentVolatility = candles.slice(i - 10, i).reduce((sum, c) => {
        return sum + (c.high - c.low) / c.low;
      }, 0) / 10;

      dumps.push({
        index: i,
        entryPrice: curr.low, // Buy at the low
        timestamp: curr.timestamp,
        dumpPercent: dumpPercent * 100,
        spread: spread * 100,
        volumeUSD,
        recentVolatility
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
  let filteredOutTickers = 0;

  for (const ticker in data) {
    const candles = data[ticker];
    if (candles.length < 100) continue; // Need minimum data

    // Calculate ticker-level metrics
    const metrics = calculateTickerMetrics(candles);

    // Apply market condition filters
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
  console.log('\n🔍 Testing optimized strategy combinations...\n');
  console.log(`📋 Market Filters:`);
  console.log(`   Min price: $${MIN_PRICE}`);
  console.log(`   Min daily volume: $${MIN_AVG_VOLUME_USD.toLocaleString()}`);
  console.log(`   Min volatility: ${(MIN_VOLATILITY * 100).toFixed(1)}%`);
  console.log(`   Max spread: ${(MAX_SPREAD_PCT * 100).toFixed(1)}%\n`);

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

        // NEW SCORING: Prioritize higher average return per trade AND reasonable win rate
        // Score = avg return * win rate * sqrt(trade count)
        // This favors quality trades with good avg returns
        const score = result.avgReturn * (result.winRate / 100) * Math.sqrt(result.totalTrades);

        if (score > bestScore && result.totalTrades >= 10) {
          bestScore = score;
          bestStrategy = result;
        }

        if (testCount % 5 === 0) {
          process.stdout.write(`   Progress: ${testCount}/${totalTests} (${((testCount/totalTests)*100).toFixed(1)}%)\r`);
        }
      }
    }
  }

  console.log(`\n   ✅ Tested ${totalTests} strategy combinations\n`);

  // Sort by score
  allResults.sort((a, b) => {
    const scoreA = a.avgReturn * (a.winRate / 100) * Math.sqrt(a.totalTrades);
    const scoreB = b.avgReturn * (b.winRate / 100) * Math.sqrt(b.totalTrades);
    return scoreB - scoreA;
  });

  return { bestStrategy, allResults };
}

async function main() {
  console.log('════════════════════════════════════════════════════════════════');
  console.log('   OPTIMIZED CRYPTO DUMP STRATEGY - QUALITY OVER QUANTITY');
  console.log('════════════════════════════════════════════════════════════════');
  console.log(`\n💰 Fee Structure (CORRECTED):`);
  console.log(`   Entry (taker/market): ${(TAKER_FEE * 100)}%`);
  console.log(`   Exit (maker/limit): ${(MAKER_FEE * 100)}%`);
  console.log(`   Round trip total: ${(ROUND_TRIP_FEE * 100)}%`);

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
  console.log('   TOP 15 STRATEGIES (Quality Score = AvgReturn * WinRate * √Trades)');
  console.log('════════════════════════════════════════════════════════════════\n');

  for (let i = 0; i < Math.min(15, allResults.length); i++) {
    const r = allResults[i];
    const score = r.avgReturn * (r.winRate / 100) * Math.sqrt(r.totalTrades);

    console.log(`${(i + 1).toString().padStart(2)}. Dump: ${(r.dumpThreshold * 100).toFixed(1)}% | Exit: ${(r.exitTarget * 100).toFixed(1)}% | Hold: ${r.maxHoldMinutes}min`);
    console.log(`    Trades: ${r.totalTrades} | Win: ${r.winRate.toFixed(1)}% | Avg: ${r.avgReturn.toFixed(3)}% | Total: ${r.totalNetReturn.toFixed(1)}%`);
    console.log(`    Quality Score: ${score.toFixed(2)} | Filtered: ${r.filteredOutTickers} tickers\n`);
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
    console.log(`   Total cumulative return: ${bestStrategy.totalNetReturn.toFixed(2)}%`);
    console.log(`   Tickers filtered out: ${bestStrategy.filteredOutTickers}\n`);

    // Best trades
    const sortedTrades = [...bestStrategy.trades].sort((a, b) => b.netReturn - a.netReturn);

    console.log('💎 Top 25 Best Trades:');
    for (let i = 0; i < Math.min(25, sortedTrades.length); i++) {
      const t = sortedTrades[i];
      const date = new Date(t.timestamp / 1000000).toISOString().split('T')[0];
      console.log(`   ${(i + 1).toString().padStart(2)}. ${t.ticker.padEnd(15)} | ${date} | Dump: ${t.dumpPercent.toFixed(2)}% | Net: ${t.netReturn.toFixed(2)}% | Hold: ${t.holdMinutes}min | Vol: $${(t.volumeUSD / 1000).toFixed(1)}k`);
    }

    console.log('\n💩 Top 20 Worst Trades:');
    const worstTrades = sortedTrades.slice(-20).reverse();
    for (let i = 0; i < worstTrades.length; i++) {
      const t = worstTrades[i];
      const date = new Date(t.timestamp / 1000000).toISOString().split('T')[0];
      console.log(`   ${(i + 1).toString().padStart(2)}. ${t.ticker.padEnd(15)} | ${date} | Dump: ${t.dumpPercent.toFixed(2)}% | Net: ${t.netReturn.toFixed(2)}% | Hold: ${t.holdMinutes}min | Vol: $${(t.volumeUSD / 1000).toFixed(1)}k`);
    }

    // Profitability distribution
    console.log('\n📊 Trade Distribution by Return:');
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

    // Save detailed results
    const outputFile = 'C:/Users/Christian Okeke/bot/bot/optimized_strategy_results.json';
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
        maxSpreadPct: MAX_SPREAD_PCT
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
      top15Strategies: allResults.slice(0, 15).map(r => ({
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
