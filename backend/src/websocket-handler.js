// backend/src/websocket-handler.js
const WebSocket = require("ws");
const EventEmitter = require("events");
const JWTTokenManager = require('./jwt-utils');

class CoinbaseWebSocketHandler extends EventEmitter {
  constructor(config) {
    super();

    // Validate required API credentials
    if (!config.apiKey || !config.signingKey) {
      throw new Error('COINBASE_API_KEY and COINBASE_SIGNING_KEY are required for Advanced Trade WebSocket');
    }

    this.config = {
      wsUrl: config.wsUrl || "wss://advanced-trade-ws.coinbase.com",
      cryptos: config.cryptos || ["BTC-USD", "ETH-USD"],
      volumeThreshold: config.volumeThreshold || 1.5,
      windowMinutes: config.windowMinutes || 5,
      reconnectDelay: config.reconnectDelay || 1000,
      maxReconnectDelay: config.maxReconnectDelay || 60000,
      apiKey: config.apiKey,
      signingKey: config.signingKey,
    };

    this.ws = null;
    this.reconnectAttempts = 0;
    this.volumeWindows = {};
    this.historicalAvgs = {};
    this.currentTickers = {};

    // Initialize JWT token manager for Advanced Trade WebSocket
    this.jwtManager = new JWTTokenManager(this.config.apiKey, this.config.signingKey);
    this.tokenRefreshInterval = null;

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
        console.log("WebSocket connected to Coinbase Advanced Trade");
        this.reconnectAttempts = 0;
        this.subscribe();
        this.startTokenRefresh();
      });

      this.ws.on("message", (data) => {
        this.handleMessage(data);
      });

      this.ws.on("error", (error) => {
        console.error("Advanced Trade WebSocket error:", error);
        if (error.message && error.message.includes('401')) {
          console.error('Authentication failed. Please check your COINBASE_API_KEY and COINBASE_SIGNING_KEY.');
        }
      });

      this.ws.on("close", () => {
        console.log("WebSocket disconnected");
        this.stopTokenRefresh();
        this.handleReconnect();
      });
    } catch (error) {
      console.error("Failed to create WebSocket:", error);
      this.handleReconnect();
    }
  }

  subscribe() {
    const jwt = this.jwtManager.getValidToken();

    // Subscribe to ticker channel
    const tickerSubscribeMsg = {
      type: "subscribe",
      product_ids: this.config.cryptos,
      channel: "ticker",
      jwt: jwt,
    };

    // Subscribe to market_trades channel for volume tracking
    const tradesSubscribeMsg = {
      type: "subscribe",
      product_ids: this.config.cryptos,
      channel: "market_trades",
      jwt: jwt,
    };

    this.ws.send(JSON.stringify(tickerSubscribeMsg));
    this.ws.send(JSON.stringify(tradesSubscribeMsg));

    console.log(
      "Subscribed to Advanced Trade WebSocket ticker and market_trades channels for:",
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
        case "market_trades":
          // Advanced Trade market_trades message
          if (message.events) {
            message.events.forEach(event => {
              this.handleAdvancedTradeMessage(event, message.channel);
            });
          }
          break;
        case "subscriptions":
          console.log("Subscription confirmed:", message);
          break;
        case "error":
          console.error("Coinbase Advanced Trade error:", message);
          if (message.message) {
            if (message.message.includes('jwt') || message.message.includes('token')) {
              console.log('JWT token may be expired, refreshing...');
              this.jwtManager.refreshToken();
              // Resubscribe with new token
              if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                setTimeout(() => this.subscribe(), 1000);
              }
            } else if (message.message.includes('auth') || message.message.includes('unauthorized')) {
              console.error('Authentication failed. Please verify your API credentials.');
            }
          }
          break;
        case "heartbeat":
          // Advanced Trade heartbeat - can be ignored or used for connection monitoring
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

  handleAdvancedTradeMessage(event, channel) {
    if (channel === "market_trades" && event.trades) {
      event.trades.forEach(trade => {
        const crypto = trade.product_id;
        const size = parseFloat(trade.size);
        this.processVolumeData(crypto, size);
      });
    } else if (channel === "ticker" && event.tickers) {
      event.tickers.forEach(ticker => {
        this.handleAdvancedTradeTicker(ticker);
      });
    }
  }

  handleAdvancedTradeTicker(ticker) {
    // Convert Advanced Trade ticker format to standardized format
    const crypto = ticker.product_id;
    const standardFormat = {
      product_id: crypto,
      price: ticker.price,
      best_bid: ticker.best_bid,
      best_ask: ticker.best_ask,
      volume_24h: ticker.volume_24h,
      open_24h: ticker.open_24h,
      low_24h: ticker.low_24h,
      high_24h: ticker.high_24h,
      time: ticker.time,
      sequence: ticker.sequence || Date.now(), // Fallback if sequence not provided
    };

    this.handleTickerMessage(standardFormat);
  }

  processVolumeData(crypto, size) {
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

    // Refresh JWT token before reconnecting
    this.jwtManager.refreshToken();

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

  startTokenRefresh() {
    // Clear any existing interval
    this.stopTokenRefresh();

    // Refresh token every 90 seconds (30 seconds before expiry)
    this.tokenRefreshInterval = setInterval(() => {
      if (this.jwtManager.isTokenExpired()) {
        console.log('JWT token expiring soon, refreshing proactively...');
        this.jwtManager.refreshToken();

        // Resubscribe with new token
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          this.subscribe();
        }
      }
    }, 90000); // 90 seconds
  }

  stopTokenRefresh() {
    if (this.tokenRefreshInterval) {
      clearInterval(this.tokenRefreshInterval);
      this.tokenRefreshInterval = null;
    }
  }

  disconnect() {
    this.stopTokenRefresh();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}

module.exports = CoinbaseWebSocketHandler;
