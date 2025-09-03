// backend/src/websocket-handler.js
const WebSocket = require("ws");
const EventEmitter = require("events");

class CoinbaseWebSocketHandler extends EventEmitter {
  constructor(config) {
    super();
    this.config = {
      wsUrl: config.wsUrl || "wss://ws-feed.exchange.coinbase.com",
      cryptos: config.cryptos || ["BTC-USD", "ETH-USD"],
      volumeThreshold: config.volumeThreshold || 1.5,
      windowMinutes: config.windowMinutes || 5,
      reconnectDelay: config.reconnectDelay || 1000,
      maxReconnectDelay: config.maxReconnectDelay || 60000,
    };

    this.ws = null;
    this.reconnectAttempts = 0;
    this.volumeWindows = {};
    this.historicalAvgs = {};
    this.currentTickers = {};

    // Initialize data structures for each crypto
    this.config.cryptos.forEach((crypto) => {
      this.volumeWindows[crypto] = [];
      this.historicalAvgs[crypto] = 0;
      this.currentTickers[crypto] = null;
    });
  }

  connect() {
    try {
      this.ws = new WebSocket(this.config.wsUrl);

      this.ws.on("open", () => {
        console.log("WebSocket connected to Coinbase");
        this.reconnectAttempts = 0;
        this.subscribe();
      });

      this.ws.on("message", (data) => {
        this.handleMessage(data);
      });

      this.ws.on("error", (error) => {
        console.error("WebSocket error:", error);
      });

      this.ws.on("close", () => {
        console.log("WebSocket disconnected");
        this.handleReconnect();
      });
    } catch (error) {
      console.error("Failed to create WebSocket:", error);
      this.handleReconnect();
    }
  }

  subscribe() {
    const subscribeMsg = {
      type: "subscribe",
      product_ids: this.config.cryptos,
      channels: [
        "ticker", // For real-time price data
        "matches", // For volume tracking
      ],
    };

    this.ws.send(JSON.stringify(subscribeMsg));
    console.log(
      "Subscribed to ticker and matches channels for:",
      this.config.cryptos
    );
  }

  handleMessage(data) {
    try {
      const message = JSON.parse(data);

      switch (message.type) {
        case "ticker":
          this.handleTickerMessage(message);
          break;
        case "match":
          this.handleMatchMessage(message);
          break;
        case "subscriptions":
          console.log("Subscription confirmed:", message);
          break;
        case "error":
          console.error("Coinbase error:", message);
          break;
      }
    } catch (error) {
      console.error("Error parsing message:", error);
    }
  }

  handleTickerMessage(ticker) {
    // Update current ticker data
    const crypto = ticker.product_id;
    this.currentTickers[crypto] = {
      crypto: crypto,
      price: parseFloat(ticker.price),
      bid: parseFloat(ticker.best_bid),
      ask: parseFloat(ticker.best_ask),
      volume_24h: parseFloat(ticker.volume_24h),
      price_24h: parseFloat(ticker.open_24h),
      low_24h: parseFloat(ticker.low_24h),
      high_24h: parseFloat(ticker.high_24h),
      price_change_24h: parseFloat(ticker.price) - parseFloat(ticker.open_24h),
      price_change_percent_24h:
        ((parseFloat(ticker.price) - parseFloat(ticker.open_24h)) /
          parseFloat(ticker.open_24h)) *
        100,
      time: ticker.time,
      sequence: ticker.sequence,
    };

    // Emit ticker update for frontend
    this.emit("ticker_update", this.currentTickers[crypto]);
  }

  handleMatchMessage(match) {
    const crypto = match.product_id;
    const size = parseFloat(match.size);
    const now = Date.now();
    const windowMs = this.config.windowMinutes * 60 * 1000;

    // Add to volume window
    this.volumeWindows[crypto].push({
      time: now,
      size: size,
    });

    // Remove old entries outside the window
    this.volumeWindows[crypto] = this.volumeWindows[crypto].filter(
      (entry) => entry.time > now - windowMs
    );

    // Calculate current window volume
    const currentVolume = this.volumeWindows[crypto].reduce(
      (sum, entry) => sum + entry.size,
      0
    );

    // Check for volume surge
    if (this.historicalAvgs[crypto] > 0) {
      const ratio = currentVolume / this.historicalAvgs[crypto];

      if (ratio > this.config.volumeThreshold) {
        const alert = {
          crypto: crypto,
          current_vol: currentVolume,
          avg_vol: this.historicalAvgs[crypto],
          threshold: this.config.volumeThreshold,
          ratio: ratio,
          ticker: this.currentTickers[crypto], // Include current ticker data
        };

        this.emit("volume_alert", alert);

        // Update historical average (simple moving average)
        this.historicalAvgs[crypto] =
          this.historicalAvgs[crypto] * 0.9 + currentVolume * 0.1;
      }
    } else {
      // Initialize historical average if not set
      this.historicalAvgs[crypto] = currentVolume;
    }
  }

  handleReconnect() {
    const delay = Math.min(
      this.config.reconnectDelay * Math.pow(2, this.reconnectAttempts),
      this.config.maxReconnectDelay
    );

    console.log(`Reconnecting in ${delay}ms...`);
    this.reconnectAttempts++;

    setTimeout(() => {
      this.connect();
    }, delay);
  }

  getCurrentTicker(crypto) {
    return this.currentTickers[crypto];
  }

  getAllTickers() {
    return this.currentTickers;
  }

  disconnect() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}

module.exports = CoinbaseWebSocketHandler;
