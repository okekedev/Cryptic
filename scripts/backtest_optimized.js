#!/usr/bin/env node
/**
 * Optimized Backtest Engine in JavaScript
 *
 * Backtests with reverse-engineered optimal parameters
 * Shows detailed debugging for why trades happen/don't happen
 */

const fs = require('fs');
const path = require('path');
const csv = require('csv-parser');

const CSV_DIR = path.join(__dirname, '..', 'data', 'historical_candles');
const RESULTS_DIR = path.join(__dirname, '..', 'data', 'backtest_results');

// Ensure results directory exists
if (!fs.existsSync(RESULTS_DIR)) {
  fs.mkdirSync(RESULTS_DIR, { recursive: true });
}

// OPTIMIZED PARAMETERS
const OPTIMIZED_PUMP = {
  spike_threshold: 3.3,
  spike_window: 5,
  position_size_pct: 10.0,
  min_profit_target: 7.5,
  trailing_threshold: 0.2,
  min_hold_time: 3.0,
  stop_loss_pct: 2.0,
  buy_fee_pct: 0.6,
  sell_fee_pct: 0.4,
  initial_capital: 10000.0
};

const OPTIMIZED_DUMP = {
  spike_threshold: 3.4,
  spike_window: 5,
  min_profit_target: 4.6,
  target_profit: 4.6,
  trailing_threshold: 0.5,
  min_hold_time: 7.0,
  max_hold_time: 59.0,
  stop_loss_pct: 2.0,
  buy_fee_pct: 0.6,
  sell_fee_pct: 0.4,
  initial_capital: 10000.0
};

// REFINED DUMP - Based on winning trade analysis
const REFINED_DUMP = {
  spike_threshold: 4.5,  // Only enter on larger dumps (better rebound rate)
  spike_window: 5,
  min_profit_target: 2.0,  // Lower target to exit winners faster
  target_profit: 4.0,      // Realistic target from data
  trailing_threshold: 0.7, // Wider trailing to avoid early exits
  min_hold_time: 5.0,      // Shorter min hold (winners exited 5-13 min)
  max_hold_time: 15.0,     // Tighter max hold (most winners < 15 min)
  stop_loss_pct: 3.0,      // Wider stop loss (avoid premature stops)
  buy_fee_pct: 0.6,
  sell_fee_pct: 0.4,
  initial_capital: 10000.0
};

class BacktestEngine {
  constructor(strategyName, params) {
    this.strategyName = strategyName;
    this.params = params;
    this.initial_capital = params.initial_capital;
    this.capital = params.initial_capital;
    this.positions = new Map();
    this.closed_trades = [];
    this.price_history = new Map();
    this.spike_detections = 0;
    this.entry_attempts = 0;
  }

  detectSpike(symbol, currentTime, currentPrice) {
    if (!this.price_history.has(symbol)) {
      return null;
    }

    const history = this.price_history.get(symbol);
    const windowMs = this.params.spike_window * 60 * 1000;
    const windowStart = currentTime.getTime() - windowMs;

    const recentPrices = history.filter(([t, p]) => t.getTime() >= windowStart && t.getTime() <= currentTime.getTime());

    if (recentPrices.length < 2) {
      return null;
    }

    const oldestPrice = recentPrices[0][1];
    const pctChange = ((currentPrice - oldestPrice) / oldestPrice) * 100;

    return Math.abs(pctChange) >= this.params.spike_threshold ? pctChange : null;
  }

  openPosition(symbol, entryPrice, entryTime, spikePct, entryType) {
    if (this.positions.has(symbol) || entryPrice <= 0) {
      return false;
    }

    this.entry_attempts++;

    let quantity, costBasis, minExitPrice, stopLossPrice, breakEvenPrice;

    if (entryType === 'pump') {
      const positionValue = this.capital * (this.params.position_size_pct / 100);
      const buyFee = positionValue * (this.params.buy_fee_pct / 100);
      costBasis = positionValue + buyFee;

      if (costBasis > this.capital) {
        return false;
      }

      quantity = positionValue / entryPrice;
      if (quantity <= 0) {
        return false;
      }

      const totalFeePct = this.params.buy_fee_pct + this.params.sell_fee_pct;
      minExitPrice = entryPrice * (1 + (totalFeePct + this.params.min_profit_target) / 100);
      stopLossPrice = entryPrice * (1 - this.params.stop_loss_pct / 100);
      breakEvenPrice = entryPrice;

    } else { // dump
      const buyFeeRate = this.params.buy_fee_pct / 100;
      const spendAmount = this.capital / (1 + buyFeeRate);
      const buyFee = spendAmount * buyFeeRate;
      costBasis = spendAmount + buyFee;

      if (costBasis > this.capital) {
        return false;
      }

      quantity = spendAmount / entryPrice;
      if (quantity <= 0) {
        return false;
      }

      const sellFeeRate = this.params.sell_fee_pct / 100;
      const breakEvenValue = costBasis / (1 - sellFeeRate);
      breakEvenPrice = breakEvenValue / quantity;
      minExitPrice = entryPrice * (1 + this.params.min_profit_target / 100);
      stopLossPrice = entryPrice * (1 - this.params.stop_loss_pct / 100);
    }

    const position = {
      symbol,
      entry_price: entryPrice,
      entry_time: entryTime,
      quantity,
      cost_basis: costBasis,
      min_exit_price: minExitPrice,
      peak_price: entryPrice,
      trailing_exit_price: entryType === 'pump' ? minExitPrice : breakEvenPrice,
      stop_loss_price: stopLossPrice,
      entry_type: entryType,
      spike_pct: spikePct,
      break_even_price: breakEvenPrice
    };

    this.positions.set(symbol, position);
    this.capital -= costBasis;

    console.log(`  ✅ OPENED ${entryType.toUpperCase()}: ${symbol} @ $${entryPrice.toFixed(2)} (spike: ${spikePct > 0 ? '+' : ''}${spikePct.toFixed(2)}%)`);

    return true;
  }

  checkExit(position, currentPrice, currentTime) {
    const timeHeldMinutes = (currentTime.getTime() - position.entry_time.getTime()) / 60000;

    const currentValue = currentPrice * position.quantity;
    const sellFee = currentValue * (this.params.sell_fee_pct / 100);
    const netProceeds = currentValue - sellFee;
    const pnlPercent = ((netProceeds - position.cost_basis) / position.cost_basis) * 100;

    // Stop loss
    if (currentPrice <= position.stop_loss_price) {
      return [true, `Stop loss (${pnlPercent.toFixed(2)}%)`];
    }

    if (position.entry_type === 'pump') {
      // Update peak/trailing
      if (currentPrice > position.peak_price) {
        position.peak_price = currentPrice;
        position.trailing_exit_price = position.peak_price * (1 - this.params.trailing_threshold / 100);
        position.trailing_exit_price = Math.max(position.trailing_exit_price, position.min_exit_price);
      }

      if (timeHeldMinutes < this.params.min_hold_time) {
        return [false, ''];
      }

      if (currentPrice <= position.trailing_exit_price) {
        return [true, `Trailing stop (${pnlPercent.toFixed(2)}%)`];
      }

    } else { // dump
      const targetPrice = position.entry_price * (1 + this.params.target_profit / 100);
      if (currentPrice >= targetPrice) {
        return [true, `Target profit (${pnlPercent.toFixed(2)}%)`];
      }

      // Update peak/trailing
      if (currentPrice > position.peak_price) {
        position.peak_price = currentPrice;
        position.trailing_exit_price = position.peak_price * (1 - this.params.trailing_threshold / 100);
        position.trailing_exit_price = Math.max(position.trailing_exit_price, position.break_even_price);
      }

      if (position.peak_price >= position.min_exit_price && currentPrice < position.break_even_price) {
        return [true, `Break-even exit (${pnlPercent.toFixed(2)}%)`];
      }

      if (timeHeldMinutes < this.params.min_hold_time) {
        return [false, ''];
      }

      if (currentPrice <= position.trailing_exit_price) {
        return [true, `Trailing stop (${pnlPercent.toFixed(2)}%)`];
      }

      if (this.params.max_hold_time && timeHeldMinutes >= this.params.max_hold_time) {
        return [true, `Max hold time (${pnlPercent.toFixed(2)}%)`];
      }
    }

    return [false, ''];
  }

  closePosition(symbol, exitPrice, exitTime, reason) {
    const position = this.positions.get(symbol);

    const exitValue = exitPrice * position.quantity;
    const sellFee = exitValue * (this.params.sell_fee_pct / 100);
    const netProceeds = exitValue - sellFee;
    const pnl = netProceeds - position.cost_basis;
    const pnlPercent = (pnl / position.cost_basis) * 100;

    this.capital += netProceeds;

    const timeHeld = (exitTime.getTime() - position.entry_time.getTime()) / 60000;

    this.closed_trades.push({
      symbol,
      entry_type: position.entry_type,
      entry_price: position.entry_price,
      exit_price: exitPrice,
      pnl,
      pnl_percent: pnlPercent,
      time_held_minutes: timeHeld,
      spike_pct: position.spike_pct,
      exit_reason: reason
    });

    console.log(`  ❌ CLOSED ${position.entry_type.toUpperCase()}: ${symbol} @ $${exitPrice.toFixed(2)} | P&L: ${pnl >= 0 ? '+' : ''}${pnlPercent.toFixed(2)}% | ${reason}`);

    this.positions.delete(symbol);
  }

  processCandle(symbol, candle) {
    const currentTime = new Date(candle.datetime);
    const currentPrice = candle.close;

    // Update price history
    if (!this.price_history.has(symbol)) {
      this.price_history.set(symbol, []);
    }
    this.price_history.get(symbol).push([currentTime, currentPrice]);

    // Keep only last 15 minutes
    const cutoff = currentTime.getTime() - (15 * 60 * 1000);
    const filtered = this.price_history.get(symbol).filter(([t, p]) => t.getTime() >= cutoff);
    this.price_history.set(symbol, filtered);

    // Check existing position
    if (this.positions.has(symbol)) {
      const position = this.positions.get(symbol);
      const [shouldExit, reason] = this.checkExit(position, currentPrice, currentTime);
      if (shouldExit) {
        this.closePosition(symbol, currentPrice, currentTime, reason);
      }
    } else {
      // Check for entry
      const spikePct = this.detectSpike(symbol, currentTime, currentPrice);

      if (spikePct !== null) {
        this.spike_detections++;

        if (this.strategyName.includes('pump') && spikePct >= this.params.spike_threshold) {
          this.openPosition(symbol, currentPrice, currentTime, spikePct, 'pump');
        } else if (this.strategyName.includes('dump') && spikePct <= -this.params.spike_threshold) {
          this.openPosition(symbol, currentPrice, currentTime, Math.abs(spikePct), 'dump');
        }
      }
    }
  }

  getReport() {
    const totalTrades = this.closed_trades.length;
    const totalPnl = this.closed_trades.reduce((sum, t) => sum + t.pnl, 0);
    const winningTrades = this.closed_trades.filter(t => t.pnl > 0);
    const losingTrades = this.closed_trades.filter(t => t.pnl <= 0);

    return {
      strategy: this.strategyName,
      params: this.params,
      initial_capital: this.initial_capital,
      final_capital: this.capital,
      total_pnl: totalPnl,
      total_pnl_pct: (totalPnl / this.initial_capital) * 100,
      total_trades: totalTrades,
      winning_trades: winningTrades.length,
      losing_trades: losingTrades.length,
      win_rate: totalTrades > 0 ? (winningTrades.length / totalTrades) * 100 : 0,
      avg_pnl_pct: totalTrades > 0 ? this.closed_trades.reduce((sum, t) => sum + t.pnl_percent, 0) / totalTrades : 0,
      spike_detections: this.spike_detections,
      entry_attempts: this.entry_attempts,
      trades: this.closed_trades
    };
  }
}

async function loadCandles(csvFile) {
  return new Promise((resolve, reject) => {
    const candles = [];
    fs.createReadStream(csvFile)
      .pipe(csv())
      .on('data', (row) => {
        candles.push({
          timestamp: parseInt(row.timestamp),
          datetime: row.datetime,
          open: parseFloat(row.open),
          high: parseFloat(row.high),
          low: parseFloat(row.low),
          close: parseFloat(row.close),
          volume: parseFloat(row.volume)
        });
      })
      .on('end', () => resolve(candles.sort((a, b) => a.timestamp - b.timestamp)))
      .on('error', reject);
  });
}

async function runBacktest(strategyName, params) {
  console.log(`\n${'='.repeat(60)}`);
  console.log(`Running ${strategyName.toUpperCase()} Backtest`);
  console.log(`${'='.repeat(60)}`);

  const engine = new BacktestEngine(strategyName, params);

  // Load all CSV files
  const files = fs.readdirSync(CSV_DIR).filter(f => f.endsWith('.csv'));
  console.log(`\nLoading ${files.length} CSV files...`);

  const allCandles = [];
  for (const file of files) {
    const symbol = file.replace('_2025-10-02.csv', '').replace(/_/g, '-');
    const candles = await loadCandles(path.join(CSV_DIR, file));
    candles.forEach(c => allCandles.push({ symbol, candle: c }));
  }

  allCandles.sort((a, b) => a.candle.timestamp - b.candle.timestamp);
  console.log(`✓ Loaded ${allCandles.length.toLocaleString()} candles\n`);

  // Process candles
  for (const { symbol, candle } of allCandles) {
    engine.processCandle(symbol, candle);
  }

  return engine.getReport();
}

async function main() {
  console.log('\n' + '='.repeat(60));
  console.log('DUMP STRATEGY REFINEMENT TEST');
  console.log('='.repeat(60));

  const optimizedReport = await runBacktest('dump_optimized', OPTIMIZED_DUMP);
  const refinedReport = await runBacktest('dump_refined', REFINED_DUMP);

  console.log(`\n${'='.repeat(60)}`);
  console.log('DUMP STRATEGY COMPARISON');
  console.log(`${'='.repeat(60)}\n`);

  console.log('OPTIMIZED DUMP (Original):');
  console.log(`  Total Trades: ${optimizedReport.total_trades}`);
  console.log(`  P&L: $${optimizedReport.total_pnl.toFixed(2)} (${optimizedReport.total_pnl_pct >= 0 ? '+' : ''}${optimizedReport.total_pnl_pct.toFixed(2)}%)`);
  console.log(`  Win Rate: ${optimizedReport.win_rate.toFixed(1)}%`);
  console.log(`  Avg P&L per Trade: ${optimizedReport.avg_pnl_pct.toFixed(2)}%`);

  console.log('\nREFINED DUMP (Enhanced):');
  console.log(`  Total Trades: ${refinedReport.total_trades}`);
  console.log(`  P&L: $${refinedReport.total_pnl.toFixed(2)} (${refinedReport.total_pnl_pct >= 0 ? '+' : ''}${refinedReport.total_pnl_pct.toFixed(2)}%)`);
  console.log(`  Win Rate: ${refinedReport.win_rate.toFixed(1)}%`);
  console.log(`  Avg P&L per Trade: ${refinedReport.avg_pnl_pct.toFixed(2)}%`);

  const improvement = refinedReport.total_pnl_pct - optimizedReport.total_pnl_pct;
  console.log(`\n  📈 IMPROVEMENT: ${improvement >= 0 ? '+' : ''}${improvement.toFixed(2)}%`);

  console.log('\nKEY REFINEMENTS:');
  console.log('  • Spike threshold: 3.4% → 4.5% (larger dumps only)');
  console.log('  • Min profit: 4.6% → 2.0% (capture winners earlier)');
  console.log('  • Target profit: 4.6% → 4.0% (realistic)');
  console.log('  • Trailing stop: 0.5% → 0.7% (avoid early exits)');
  console.log('  • Max hold: 59 min → 15 min (exit faster)');
  console.log('  • Stop loss: 2% → 3% (avoid premature stops)');

  // Save results
  const resultsFile = path.join(RESULTS_DIR, 'refined_dump_comparison.json');
  fs.writeFileSync(resultsFile, JSON.stringify({
    optimized: optimizedReport,
    refined: refinedReport,
    improvement_pct: improvement
  }, null, 2));
  console.log(`\n✓ Results saved to: ${resultsFile}`);
  console.log('='.repeat(60) + '\n');
}

main().catch(console.error);
