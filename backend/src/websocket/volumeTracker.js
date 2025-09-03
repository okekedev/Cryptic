const EventEmitter = require("events");
const ccxt = require("ccxt");
const config = require("./config");

class VolumeTracker extends EventEmitter {
  constructor() {
    super();
    this.exchange = new ccxt.coinbase();
    this.volumeWindows = {};
    this.historicalAvgs = {};
    this.windowSeconds = config.WINDOW_MINUTES * 60;

    // Initialize data structures for each crypto
    config.CRYPTOS.forEach((crypto) => {
      this.volumeWindows[crypto] = [];
      this.historicalAvgs[crypto] = 0.0;
    });
  }

  async initialize() {
    console.log("Initializing volume tracker...");
    await Promise.all(
      config.CRYPTOS.map((crypto) => this.fetchHistoricalVolume(crypto))
    );
    console.log("Volume tracker initialized");
  }

  async fetchHistoricalVolume(crypto) {
    try {
      const since = Date.now() - config.HISTORICAL_HOURS * 3600 * 1000;
      const trades = await this.exchange.fetchTrades(crypto, since);

      let windowVol = 0.0;
      let windowStart = Date.now() - config.HISTORICAL_HOURS * 3600 * 1000;
      let numWindows = 0;

      for (const trade of trades) {
        const ts = trade.timestamp;

        if (ts >= windowStart + this.windowSeconds * 1000) {
          if (windowVol > 0) {
            this.historicalAvgs[crypto] += windowVol;
            numWindows += 1;
          }
          windowVol = 0.0;
          windowStart += this.windowSeconds * 1000;
        }

        windowVol += trade.amount || 0;
      }

      if (numWindows > 0) {
        this.historicalAvgs[crypto] /= numWindows;
      } else {
        this.historicalAvgs[crypto] = 1.0; // Default fallback
      }

      console.log(
        `Initial avg volume for ${crypto}: ${this.historicalAvgs[crypto]}`
      );
    } catch (error) {
      console.error(`Error fetching historical for ${crypto}:`, error);
      this.historicalAvgs[crypto] = 1.0;
    }
  }

  processMatch(data) {
    const crypto = data.product_id;
    const size = parseFloat(data.size);
    const ts = Date.now() / 1000;

    // Add to window
    if (!this.volumeWindows[crypto]) return;

    this.volumeWindows[crypto].push({ ts, size });

    // Clean old entries
    this.volumeWindows[crypto] = this.volumeWindows[crypto].filter(
      (entry) => entry.ts >= ts - this.windowSeconds
    );

    // Calculate current volume
    const currentVol = this.volumeWindows[crypto].reduce(
      (sum, entry) => sum + entry.size,
      0
    );

    const avgVol = this.historicalAvgs[crypto];

    // Check for surge
    if (avgVol > 0 && currentVol > config.THRESHOLD * avgVol) {
      const alert = {
        crypto,
        current_vol: currentVol,
        avg_vol: avgVol,
        threshold: config.THRESHOLD,
        surge_ratio: currentVol / avgVol,
        timestamp: new Date().toISOString(),
      };

      // Emit alert event
      this.emit("volumeSurge", alert);

      // Update historical average (simple moving average)
      this.historicalAvgs[crypto] =
        this.historicalAvgs[crypto] * 0.9 + currentVol * 0.1;

      console.log(
        `Volume surge detected for ${crypto}: ${currentVol.toFixed(2)} > ${(
          config.THRESHOLD * avgVol
        ).toFixed(2)}`
      );
    }
  }

  getStats() {
    const stats = {};
    config.CRYPTOS.forEach((crypto) => {
      const currentVol = this.volumeWindows[crypto].reduce(
        (sum, entry) => sum + entry.size,
        0
      );
      stats[crypto] = {
        currentVolume: currentVol,
        averageVolume: this.historicalAvgs[crypto],
        windowSize: this.volumeWindows[crypto].length,
        surgeRatio: currentVol / this.historicalAvgs[crypto],
      };
    });
    return stats;
  }
}

module.exports = VolumeTracker;
