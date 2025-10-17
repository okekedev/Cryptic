#!/usr/bin/env node
/**
 * Download minute-level price data for all coins that dumped in last 12 hours
 * Saves to CSV for accurate backtesting
 */

const http = require('http');
const fs = require('fs');

const dumps = JSON.parse(fs.readFileSync('./dumps_12h.json', 'utf8'));
const BACKEND_URL = 'http://localhost:5001';

// Get unique symbols from dumps
const symbols = [...new Set(dumps.map(d => d.symbol))];

console.log('='.repeat(80));
console.log('DOWNLOADING MINUTE DATA FOR BACKTESTING');
console.log('='.repeat(80));
console.log(`Found ${symbols.length} unique symbols with dumps in last 12 hours`);
console.log();

/**
 * Fetch minute candles from Coinbase
 */
async function getMinuteData(symbol, hours = 12) {
  return new Promise((resolve, reject) => {
    const url = `${BACKEND_URL}/api/historical/${symbol}?hours=${hours}`;

    http.get(url, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const candles = JSON.parse(data);
          if (candles.error) {
            reject(new Error(candles.error));
          } else {
            resolve(candles);
          }
        } catch (e) {
          reject(e);
        }
      });
    }).on('error', reject);
  });
}

/**
 * Convert candles to CSV format
 */
function candlesToCSV(symbol, candles) {
  if (!candles || candles.length === 0) return null;

  let csv = 'symbol,timestamp,datetime,open,high,low,close,volume\n';

  for (const candle of candles) {
    const datetime = new Date(candle.timestamp * 1000).toISOString();
    csv += `${symbol},${candle.timestamp},${datetime},${candle.open},${candle.high},${candle.low},${candle.close},${candle.volume}\n`;
  }

  return csv;
}

/**
 * Main download function
 */
async function downloadAllData() {
  const allData = [];
  let successCount = 0;
  let failCount = 0;

  console.log('Downloading data...\n');

  for (let i = 0; i < symbols.length; i++) {
    const symbol = symbols[i];
    process.stdout.write(`[${i + 1}/${symbols.length}] ${symbol.padEnd(15)} ... `);

    try {
      const candles = await getMinuteData(symbol, 12);

      if (!candles || candles.length === 0) {
        console.log('❌ No data');
        failCount++;
      } else {
        // Add to master dataset
        for (const candle of candles) {
          const datetime = new Date(candle.timestamp * 1000).toISOString();
          allData.push({
            symbol,
            timestamp: candle.timestamp,
            datetime,
            open: candle.open,
            high: candle.high,
            low: candle.low,
            close: candle.close,
            volume: candle.volume
          });
        }

        console.log(`✅ ${candles.length} candles`);
        successCount++;
      }

      // Small delay to avoid rate limiting
      await new Promise(resolve => setTimeout(resolve, 50));

    } catch (error) {
      console.log(`❌ Error: ${error.message}`);
      failCount++;
    }
  }

  console.log();
  console.log('='.repeat(80));
  console.log(`Download complete: ${successCount} success, ${failCount} failed`);
  console.log(`Total candles: ${allData.length}`);
  console.log('='.repeat(80));
  console.log();

  // Sort by timestamp
  allData.sort((a, b) => a.timestamp - b.timestamp);

  // Write master CSV file
  console.log('Writing CSV file...');
  let csv = 'symbol,timestamp,datetime,open,high,low,close,volume\n';

  for (const row of allData) {
    csv += `${row.symbol},${row.timestamp},${row.datetime},${row.open},${row.high},${row.low},${row.close},${row.volume}\n`;
  }

  fs.writeFileSync('./crypto_minute_data_12h.csv', csv);
  console.log('✅ Saved to: crypto_minute_data_12h.csv');
  console.log();

  // Also create a summary file with metadata
  const summary = {
    generated_at: new Date().toISOString(),
    hours_of_data: 12,
    symbols_count: symbols.length,
    symbols_success: successCount,
    symbols_failed: failCount,
    total_candles: allData.length,
    symbols: symbols,
    time_range: {
      start: allData[0]?.datetime || 'N/A',
      end: allData[allData.length - 1]?.datetime || 'N/A'
    }
  };

  fs.writeFileSync('./crypto_data_summary.json', JSON.stringify(summary, null, 2));
  console.log('✅ Saved summary to: crypto_data_summary.json');
  console.log();

  console.log('='.repeat(80));
  console.log('DATA SUMMARY');
  console.log('='.repeat(80));
  console.log(`Time Range: ${summary.time_range.start} → ${summary.time_range.end}`);
  console.log(`Symbols: ${summary.symbols_success}/${summary.symbols_count}`);
  console.log(`Total Candles: ${summary.total_candles.toLocaleString()}`);
  console.log();
  console.log('Files created:');
  console.log('  • crypto_minute_data_12h.csv (main dataset)');
  console.log('  • crypto_data_summary.json (metadata)');
  console.log('  • dumps_12h.json (dump alerts)');
  console.log();
  console.log('Ready for backtesting!');
  console.log('='.repeat(80));
}

// Run the download
downloadAllData().catch(console.error);
