// src/server.ts

import express from "express";
import http from "http";
import { Server as SocketIOServer } from "socket.io";
import path from "path";
import dotenv from "dotenv";
import { CoinbaseWebSocket } from "./websocket/CoinbaseWebSocket";
import { TickerMessage } from "./types/coinbase";

// Load environment variables
dotenv.config();

const app = express();
const server = http.createServer(app);
const io = new SocketIOServer(server, {
  cors: {
    origin: "*",
    methods: ["GET", "POST"],
  },
});

// Serve static files
app.use(express.static(path.join(__dirname, "../public")));

// Coinbase WebSocket instance
let coinbaseWS: CoinbaseWebSocket | null = null;

// Track connected clients and their subscriptions
const clients = new Map<string, Set<string>>();

// Initialize Coinbase WebSocket
function initializeCoinbaseWebSocket() {
  const config: any = {
    channels: ["ticker", "heartbeat"],
    productIds: [], // Will be populated by client subscriptions
  };

  // Add authentication if environment variables are set
  if (process.env.COINBASE_API_KEY && process.env.COINBASE_API_SECRET) {
    config.auth = {
      apiKey: process.env.COINBASE_API_KEY,
      apiSecret: process.env.COINBASE_API_SECRET,
    };
    console.log("Using authenticated Coinbase WebSocket connection");
  } else {
    console.log("Using unauthenticated Coinbase WebSocket connection");
  }

  coinbaseWS = new CoinbaseWebSocket(config);

  // Forward ticker updates to all connected clients
  coinbaseWS.on("ticker", (ticker: TickerMessage) => {
    io.emit("ticker", ticker);
  });

  // Log connection events
  coinbaseWS.on("connected", () => {
    console.log("Connected to Coinbase WebSocket");
    io.emit("coinbase_connected");
  });

  coinbaseWS.on("disconnected", (data) => {
    console.log("Disconnected from Coinbase:", data);
    io.emit("coinbase_disconnected");
  });

  coinbaseWS.on("error", (error) => {
    console.error("Coinbase WebSocket error:", error);
    io.emit("error", { message: error.message });
  });

  coinbaseWS.on("reconnecting", (data) => {
    console.log("Reconnecting to Coinbase:", data);
    io.emit("coinbase_reconnecting", data);
  });

  // Start connection
  coinbaseWS.connect();
}

// Socket.io connection handling
io.on("connection", (socket) => {
  console.log(`Client connected: ${socket.id}`);
  clients.set(socket.id, new Set());

  // Initialize Coinbase connection if this is the first client
  if (clients.size === 1 && !coinbaseWS) {
    initializeCoinbaseWebSocket();
  }

  // Handle subscription requests
  socket.on(
    "subscribe",
    (data: { channels: string[]; product_ids: string[] }) => {
      console.log(`Client ${socket.id} subscribing to:`, data.product_ids);

      const clientProducts = clients.get(socket.id)!;
      data.product_ids.forEach((product) => clientProducts.add(product));

      // Subscribe to each channel separately as per Coinbase docs
      if (coinbaseWS) {
        for (const channel of data.channels) {
          coinbaseWS.subscribe(channel, data.product_ids);
        }
      }
    }
  );

  // Handle unsubscribe requests
  socket.on(
    "unsubscribe",
    (data: { channels: string[]; product_ids: string[] }) => {
      console.log(`Client ${socket.id} unsubscribing from:`, data.product_ids);

      const clientProducts = clients.get(socket.id)!;
      data.product_ids.forEach((product) => clientProducts.delete(product));

      // Check if any other clients are subscribed to these products
      const stillNeeded = data.product_ids.filter((product) => {
        for (const [clientId, products] of clients) {
          if (clientId !== socket.id && products.has(product)) {
            return true;
          }
        }
        return false;
      });

      // Unsubscribe from products no longer needed
      const toUnsubscribe = data.product_ids.filter(
        (p) => !stillNeeded.includes(p)
      );
      if (toUnsubscribe.length > 0 && coinbaseWS) {
        for (const channel of data.channels) {
          coinbaseWS.unsubscribe(channel, toUnsubscribe);
        }
      }
    }
  );

  // Handle disconnect
  socket.on("disconnect", () => {
    console.log(`Client disconnected: ${socket.id}`);

    // Get products this client was subscribed to
    const clientProducts = clients.get(socket.id) || new Set();
    clients.delete(socket.id);

    // If no clients left, disconnect from Coinbase
    if (clients.size === 0 && coinbaseWS) {
      console.log("No clients connected, disconnecting from Coinbase");
      coinbaseWS.disconnect();
      coinbaseWS = null;
    } else {
      // Check if we need to unsubscribe from any products
      const productsToCheck = Array.from(clientProducts);
      const stillNeeded = productsToCheck.filter((product) => {
        for (const products of clients.values()) {
          if (products.has(product)) return true;
        }
        return false;
      });

      const toUnsubscribe = productsToCheck.filter(
        (p) => !stillNeeded.includes(p)
      );
      if (toUnsubscribe.length > 0 && coinbaseWS) {
        // Unsubscribe from all active channels for these products
        const channels = coinbaseWS.getSubscribedChannels();
        for (const channel of channels) {
          coinbaseWS.unsubscribe(channel, toUnsubscribe);
        }
      }
    }
  });
});

// Health check endpoint
app.get("/health", (req, res) => {
  res.json({
    status: "ok",
    coinbase_connected: coinbaseWS?.getConnectionState() === "CONNECTED",
    active_clients: clients.size,
    timestamp: new Date().toISOString(),
  });
});

// Start server
const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`);
});
