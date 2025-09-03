const WebSocket = require("ws");
const EventEmitter = require("events");
const config = require("./config");

class CoinbaseWebSocketClient extends EventEmitter {
  constructor() {
    super();
    this.ws = null;
    this.reconnectDelay = config.RECONNECT_DELAY;
    this.isConnected = false;
    this.shouldReconnect = true;
  }

  connect() {
    if (this.ws) {
      return;
    }

    console.log("Connecting to Coinbase WebSocket...");

    this.ws = new WebSocket(config.WS_URL);

    this.ws.on("open", () => {
      console.log("Connected to Coinbase WebSocket");
      this.isConnected = true;
      this.reconnectDelay = config.RECONNECT_DELAY; // Reset delay on successful connection
      this.subscribe();
      this.emit("connected");
    });

    this.ws.on("message", (data) => {
      try {
        const message = JSON.parse(data);
        this.handleMessage(message);
      } catch (error) {
        console.error("Error parsing WebSocket message:", error);
      }
    });

    this.ws.on("error", (error) => {
      console.error("WebSocket error:", error);
      this.emit("error", error);
    });

    this.ws.on("close", (code, reason) => {
      console.log(`WebSocket closed: ${code} ${reason}`);
      this.isConnected = false;
      this.ws = null;
      this.emit("disconnected");

      if (this.shouldReconnect) {
        this.scheduleReconnect();
      }
    });

    // Heartbeat to keep connection alive
    this.setupHeartbeat();
  }

  subscribe() {
    const subscribeMsg = {
      type: "subscribe",
      product_ids: config.CRYPTOS,
      channels: ["matches"],
    };

    this.send(subscribeMsg);
    console.log(
      "Subscribed to matches channel for:",
      config.CRYPTOS.join(", ")
    );
  }

  send(data) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    } else {
      console.error("WebSocket not connected, cannot send message");
    }
  }

  handleMessage(message) {
    switch (message.type) {
      case "subscriptions":
        console.log("Subscription confirmed:", message);
        break;
      case "match":
        this.emit("match", message);
        break;
      case "error":
        console.error("Coinbase error:", message.message);
        this.emit("error", new Error(message.message));
        break;
      default:
        // Ignore other message types
        break;
    }
  }

  setupHeartbeat() {
    // Coinbase doesn't require client-side heartbeat, but we'll set up a ping interval
    // to detect connection issues early
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
    }

    this.heartbeatInterval = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.ping();
      }
    }, 30000); // Ping every 30 seconds
  }

  scheduleReconnect() {
    console.log(`Reconnecting in ${this.reconnectDelay}ms...`);

    setTimeout(() => {
      this.connect();
    }, this.reconnectDelay);

    // Exponential backoff
    this.reconnectDelay = Math.min(
      this.reconnectDelay * 2,
      config.MAX_RECONNECT_DELAY
    );
  }

  disconnect() {
    this.shouldReconnect = false;

    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
    }

    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  getConnectionStatus() {
    return {
      connected: this.isConnected,
      url: config.WS_URL,
      cryptos: config.CRYPTOS,
    };
  }
}

module.exports = CoinbaseWebSocketClient;
