const CoinbaseWebSocketClient = require("./coinbaseClient");
const VolumeTracker = require("./volumeTracker");

class WebSocketManager {
  constructor(io) {
    this.io = io;
    this.client = new CoinbaseWebSocketClient();
    this.volumeTracker = new VolumeTracker();

    this.setupEventHandlers();
  }

  async start() {
    try {
      // Initialize volume tracker with historical data
      await this.volumeTracker.initialize();

      // Connect to Coinbase WebSocket
      this.client.connect();

      console.log("WebSocket manager started");
    } catch (error) {
      console.error("Error starting WebSocket manager:", error);
      throw error;
    }
  }

  setupEventHandlers() {
    this.client.on("connected", () => {
      this.io.emit("exchange_connected", {
        status: "connected",
        exchange: "coinbase",
      });
    });

    this.client.on("disconnected", () => {
      this.io.emit("exchange_disconnected", {
        status: "disconnected",
        exchange: "coinbase",
      });
    });

    this.client.on("error", (error) => {
      console.error("WebSocket client error:", error);
      this.io.emit("exchange_error", {
        error: error.message,
        exchange: "coinbase",
      });
    });

    // Handle match events
    this.client.on("match", (data) => {
      // Process match through volume tracker
      this.volumeTracker.processMatch(data);

      // Optionally emit raw trade data to frontend
      if (process.env.EMIT_RAW_TRADES === "true") {
        this.io.emit("trade", {
          crypto: data.product_id,
          price: parseFloat(data.price),
          size: parseFloat(data.size),
          side: data.side,
          time: data.time,
        });
      }
    });

    // Handle volume surge alerts
    this.volumeTracker.on("volumeSurge", (alert) => {
      // Emit to all connected Socket.IO clients
      this.io.emit("trade_update", alert);

      // Log the alert
      console.log(
        `Alert sent for ${alert.crypto}: ${alert.current_vol.toFixed(2)} > ${(
          alert.threshold * alert.avg_vol
        ).toFixed(2)}`
      );
    });
  }

  stop() {
    this.client.disconnect();
    console.log("WebSocket manager stopped");
  }

  getStatus() {
    return {
      connection: this.client.getConnectionStatus(),
      volumeStats: this.volumeTracker.getStats(),
    };
  }
}

module.exports = WebSocketManager;
