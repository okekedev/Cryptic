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

    // Priority system for active trades
    this.priorityPairs = new Set(); // Set of product_ids that need priority updates
    this.priorityUpdateInterval = 1000; // Priority updates every 1 second
    this.lastPriorityUpdate = {}; // Track last update time for priority pairs

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

      // Handle messages based on channel (Advanced Trade API uses 'channel' not 'type' at root)
      switch (message.channel) {
        case "ticker":
          // Single ticker update
          if (message.events) {
            message.events.forEach(event => {
              if (event.tickers) {
                event.tickers.forEach(ticker => this.handleTickerMessage(ticker));
              }
            });
          }
          break;
        case "ticker_batch":
          // Batch ticker updates
          if (message.events) {
            message.events.forEach(event => {
              if (event.tickers) {
                event.tickers.forEach(ticker => this.handleTickerMessage(ticker));
              }
            });
          }
          break;
        case "market_trades":
          // Market trades for volume tracking
          if (message.events) {
            message.events.forEach(event => {
              if (event.trades) {
                event.trades.forEach(trade => {
                  const crypto = trade.product_id;
                  const size = parseFloat(trade.size);
                  this.processVolumeData(crypto, size);
                });
              }
            });
          }
          break;
        case "subscriptions":
          console.log("✅ Subscription confirmed:", message.events ? JSON.stringify(message.events[0]).substring(0, 200) : 'OK');
          break;
        case "heartbeats":
          // Heartbeat - connection is alive
          break;
        default:
          // Check if this is an error message
          if (message.type === "error") {
            console.error("❌ Coinbase Advanced Trade error:", message);
            if (message.message) {
              const errorMsg = message.message.toLowerCase();
              if (errorMsg.includes('jwt') || errorMsg.includes('token') || errorMsg.includes('auth')) {
                console.log('JWT authentication failed, refreshing token and resubscribing...');
                this.jwtManager.refreshToken();
                setTimeout(() => {
                  if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.subscribe();
                  }
                }, 1000);
              } else if (errorMsg.includes('unauthorized')) {
                console.error('Authentication failed. Please verify your API credentials.');
              }
            }
          }
          break;
      }
    } catch (error) {
      console.error("Error parsing message:", error);
    }
  }

  handleTickerMessage(ticker) {
    // Update current ticker data - handle Advanced Trade API format
    const crypto = ticker.product_id;
    const price = parseFloat(ticker.price);
    const volume_24h = parseFloat(ticker.volume_24_h || ticker.volume_24h || 0);
    const low_24h = parseFloat(ticker.low_24_h || ticker.low_24h || 0);
    const high_24h = parseFloat(ticker.high_24_h || ticker.high_24h || 0);

    // Calculate open_24h from price_percent_chg_24_h if available
    const price_percent_chg = parseFloat(ticker.price_percent_chg_24_h || 0);
    const open_24h = price_percent_chg !== 0 ? price / (1 + price_percent_chg / 100) : price;

    this.currentTickers[crypto] = {
      crypto: crypto,
      price: price,
      bid: parseFloat(ticker.best_bid || ticker.bid || 0),
      ask: parseFloat(ticker.best_ask || ticker.ask || 0),
      volume_24h: volume_24h,
      price_24h: open_24h,
      low_24h: low_24h,
      high_24h: high_24h,
      price_change_24h: price - open_24h,
      price_change_percent_24h: price_percent_chg,
      time: ticker.time || ticker.timestamp || new Date().toISOString(),
      sequence: ticker.sequence || Date.now(),
    };

    // Emit ticker update for frontend
    this.emit("ticker_update", this.currentTickers[crypto]);

    // Emit priority update if this is a priority pair
    if (this.priorityPairs.has(crypto)) {
      const now = Date.now();
      const lastUpdate = this.lastPriorityUpdate[crypto] || 0;

      // Emit priority updates more frequently
      if (now - lastUpdate >= this.priorityUpdateInterval) {
        this.emit("priority_ticker_update", this.currentTickers[crypto]);
        this.lastPriorityUpdate[crypto] = now;
      }
    }
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

  // Priority system methods for active trading pairs
  addPriorityPair(productId) {
    this.priorityPairs.add(productId);
    this.lastPriorityUpdate[productId] = 0; // Reset update timer
    console.log(`Added ${productId} to priority monitoring. Total priority pairs: ${this.priorityPairs.size}`);
  }

  removePriorityPair(productId) {
    this.priorityPairs.delete(productId);
    delete this.lastPriorityUpdate[productId];
    console.log(`Removed ${productId} from priority monitoring. Total priority pairs: ${this.priorityPairs.size}`);
  }

  getPriorityPairs() {
    return Array.from(this.priorityPairs);
  }

  isPriorityPair(productId) {
    return this.priorityPairs.has(productId);
  }

  clearPriorityPairs() {
    this.priorityPairs.clear();
    this.lastPriorityUpdate = {};
    console.log('Cleared all priority pairs');
  }

  // Get priority statistics
  getPriorityStats() {
    return {
      totalPriorityPairs: this.priorityPairs.size,
      priorityPairs: Array.from(this.priorityPairs),
      updateInterval: this.priorityUpdateInterval,
      lastUpdates: { ...this.lastPriorityUpdate }
    };
  }
}

module.exports = CoinbaseWebSocketHandler;
