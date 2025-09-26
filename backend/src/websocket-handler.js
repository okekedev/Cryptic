// backend/src/websocket-handler.js
const WebSocket = require("ws");
const EventEmitter = require("events");
const JWTTokenManager = require('./jwt-utils');
const fetch = require('node-fetch');

class CoinbaseWebSocketHandler extends EventEmitter {
  constructor(config) {
    super();

    // Validate required API credentials
    if (!config.apiKey || !config.signingKey) {
      throw new Error('COINBASE_API_KEY and COINBASE_SIGNING_KEY are required for Advanced Trade WebSocket');
    }

    this.config = {
      wsUrl: config.wsUrl || "wss://advanced-trade-ws.coinbase.com",
      cryptoConfig: config.cryptoConfig || null, // null means fetch all pairs dynamically
      cryptos: [], // Will be populated dynamically
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

  async fetchAvailableUSDPairs() {
    try {
      console.log('Fetching available USD trading pairs from Coinbase...');
      // Use Coinbase public endpoint to get all products
      const response = await fetch('https://api.coinbase.com/api/v3/brokerage/market/products');

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();

      // Filter for USD pairs only and active products
      const usdPairs = data.products
        .filter(product =>
          product.quote_currency_id === 'USD' &&
          product.status === 'online' &&
          !product.trading_disabled
        )
        .map(product => product.product_id);

      console.log(`Found ${usdPairs.length} active USD trading pairs`);
      return usdPairs;
    } catch (error) {
      console.error('Failed to fetch USD pairs:', error);
      // Return empty array if API call fails - no hardcoded fallbacks
      console.log('No fallback pairs configured - will retry fetching on next connection');
      return [];
    }
  }

  async initialize() {
    try {
      let finalPairs = [];

      // Handle different crypto configuration types
      if (Array.isArray(this.config.cryptoConfig)) {
        // Custom pairs specified
        finalPairs = this.config.cryptoConfig;
        console.log(`Using custom pairs: ${finalPairs.join(', ')}`);
      } else if (this.config.cryptoConfig && this.config.cryptoConfig.topN) {
        // Top N pairs requested
        const allPairs = await this.fetchAvailableUSDPairs();
        if (allPairs.length > 0) {
          finalPairs = allPairs.slice(0, this.config.cryptoConfig.topN);
          console.log(`Using top ${this.config.cryptoConfig.topN} pairs from ${allPairs.length} available pairs`);
        }
      } else {
        // Default: fetch all available USD pairs
        finalPairs = await this.fetchAvailableUSDPairs();
        console.log(`Using all ${finalPairs.length} available USD pairs`);
      }

      // Update config with final pairs
      this.config.cryptos = finalPairs;

      // Initialize volume tracking for all pairs
      finalPairs.forEach((pair) => {
        this.volumeWindows[pair] = [];
        this.historicalAvgs[pair] = 0;
        this.currentTickers[pair] = null;
      });

      console.log(`Initialized tracking for ${finalPairs.length} USD pairs`);

      // Only connect if we have pairs to monitor
      if (finalPairs.length > 0) {
        this.connect();
      } else {
        console.warn('No USD pairs to monitor - skipping WebSocket connection');
      }
    } catch (error) {
      console.error('Initialization failed:', error);
      // Don't fallback to connect if we have no pairs to monitor
    }
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

  async subscribe() {
    try {
      console.log('Starting subscription process...');

      // First, subscribe to heartbeats to keep connection alive
      try {
        const heartbeatJWT = this.jwtManager.getValidToken();
        if (!heartbeatJWT) {
          throw new Error('Failed to generate JWT token for heartbeats');
        }

        const heartbeatMsg = {
          type: "subscribe",
          channel: "heartbeats",
          jwt: heartbeatJWT
        };

        console.log('Subscribing to heartbeats channel...');
        this.ws.send(JSON.stringify(heartbeatMsg));
        console.log('Heartbeats subscription sent successfully');
      } catch (error) {
        console.error('Failed to subscribe to heartbeats:', error.message);
        return; // Don't proceed if JWT generation fails
      }

      // Wait a bit before other subscriptions
      await new Promise(resolve => setTimeout(resolve, 500));

      // Batch products - use smaller batch size (50) for better reliability
      const batchSize = 50;
      const productBatches = [];

      for (let i = 0; i < this.config.cryptos.length; i += batchSize) {
        productBatches.push(this.config.cryptos.slice(i, i + batchSize));
      }

      console.log(`Subscribing to ${this.config.cryptos.length} USD pairs across ${productBatches.length} batches (${batchSize} products per batch)`);

      // Subscribe to each batch
      for (let batchIndex = 0; batchIndex < productBatches.length; batchIndex++) {
        const batch = productBatches[batchIndex];
        console.log(`Processing batch ${batchIndex + 1}/${productBatches.length} with ${batch.length} products`);

        try {
          // Subscribe to ticker_batch (updates every 5 seconds, better for many products)
          const tickerJWT = this.jwtManager.getValidToken();
          if (!tickerJWT) {
            throw new Error('Failed to generate JWT token for ticker_batch');
          }

          const tickerMsg = {
            type: "subscribe",
            product_ids: batch,
            channel: "ticker_batch",
            jwt: tickerJWT
          };

          console.log(`Subscribing to ticker_batch for batch ${batchIndex + 1}...`);
          this.ws.send(JSON.stringify(tickerMsg));
          console.log(`Ticker_batch subscription sent for batch ${batchIndex + 1}`);

          // Increased delay between subscriptions for rate limiting
          await new Promise(resolve => setTimeout(resolve, 300));

          // Subscribe to market_trades for volume tracking
          const tradesJWT = this.jwtManager.getValidToken();
          if (!tradesJWT) {
            throw new Error('Failed to generate JWT token for market_trades');
          }

          const tradesMsg = {
            type: "subscribe",
            product_ids: batch,
            channel: "market_trades",
            jwt: tradesJWT
          };

          console.log(`Subscribing to market_trades for batch ${batchIndex + 1}...`);
          this.ws.send(JSON.stringify(tradesMsg));
          console.log(`Market_trades subscription sent for batch ${batchIndex + 1}`);

          // Longer delay before next batch to respect rate limits
          if (batchIndex < productBatches.length - 1) {
            console.log(`Waiting before next batch (${batchIndex + 2}/${productBatches.length})...`);
            await new Promise(resolve => setTimeout(resolve, 500));
          }

        } catch (error) {
          console.error(`Failed to subscribe to batch ${batchIndex + 1}:`, error.message);
          // Continue with next batch even if this one fails
        }
      }

      console.log("Subscription process completed for all batches");
    } catch (error) {
      console.error('Subscription error:', error);
    }
  }

  handleMessage(data) {
    try {
      const message = JSON.parse(data);

      switch (message.type) {
        case "ticker":
          this.handleTickerMessage(message);
          break;
        case "ticker_batch":
          // Handle ticker_batch events
          if (message.events) {
            message.events.forEach(event => {
              this.handleTickerMessage(event);
            });
          }
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
            const errorMsg = message.message.toLowerCase();
            if (errorMsg.includes('jwt') || errorMsg.includes('token') || errorMsg.includes('auth')) {
              console.log('JWT authentication failed, refreshing token and resubscribing...');
              this.jwtManager.refreshToken();
              // Reconnect with fresh token
              setTimeout(() => {
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                  this.subscribe();
                }
              }, 1000);
            } else if (errorMsg.includes('unauthorized')) {
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
