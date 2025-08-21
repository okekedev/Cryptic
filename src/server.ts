// src/server.ts

import express from "express";
import http from "http";
import { Server as SocketIOServer } from "socket.io";
import path from "path";
import dotenv from "dotenv";
import { CoinbaseWebSocket } from "./websocket/CoinbaseWebSocket";
import { TickerMessage } from "./types/coinbase";
import { CoinbaseApiClient } from "./utils/coinbaseApiClient";

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
const apiClient = new CoinbaseApiClient();

// Track connected clients and their subscriptions
const clients = new Map<string, Set<string>>();

// Store all available USD pairs
let allUSDPairs: string[] = [];
let subscribedPairs: Set<string> = new Set();

// Maximum number of simultaneous subscriptions (to avoid overwhelming the connection)
const MAX_SUBSCRIPTIONS = 100;

// Initialize and connect to Coinbase WebSocket
async function initializeCoinbaseWebSocket() {
  console.log("Initializing Coinbase WebSocket connection...");

  // Fetch all USD pairs first
  console.log("Fetching all USD trading pairs...");
  allUSDPairs = await apiClient.getActiveUSDPairs();

  if (allUSDPairs.length === 0) {
    console.log("Failed to fetch pairs, using popular pairs as fallback");
    allUSDPairs = apiClient.getPopularUSDPairs();
  }

  console.log(`Total available USD pairs: ${allUSDPairs.length}`);

  coinbaseWS = new CoinbaseWebSocket({
    channels: ["ticker", "heartbeats"],
    productIds: [], // Start with empty, will subscribe based on client needs
  });

  // Forward ticker updates to all connected clients
  coinbaseWS.on("ticker", (ticker: TickerMessage) => {
    io.emit("ticker", ticker);
  });

  // Log connection events
  coinbaseWS.on("connected", () => {
    console.log("Connected to Coinbase WebSocket");
    io.emit("coinbase_connected");

    // Send available pairs to all clients
    io.emit("available_pairs", allUSDPairs);
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

// Subscribe to a batch of products
function subscribeToProducts(products: string[]) {
  if (!coinbaseWS || coinbaseWS.getConnectionState() !== "CONNECTED") {
    console.log("WebSocket not connected, skipping subscription");
    return;
  }

  // Filter out already subscribed products
  const newProducts = products.filter((p) => !subscribedPairs.has(p));

  if (newProducts.length === 0) {
    return;
  }

  // Check if we're within limits
  const totalAfterSubscribe = subscribedPairs.size + newProducts.length;
  if (totalAfterSubscribe > MAX_SUBSCRIPTIONS) {
    const availableSlots = MAX_SUBSCRIPTIONS - subscribedPairs.size;
    if (availableSlots > 0) {
      newProducts.splice(availableSlots);
      console.log(
        `Limiting subscription to ${availableSlots} products due to MAX_SUBSCRIPTIONS limit`
      );
    } else {
      console.log(`Already at maximum subscriptions (${MAX_SUBSCRIPTIONS})`);
      return;
    }
  }

  console.log(`Subscribing to ${newProducts.length} new products`);
  coinbaseWS.subscribe("ticker", newProducts);

  // Track subscribed pairs
  newProducts.forEach((p) => subscribedPairs.add(p));
}

// Socket.io connection handling
io.on("connection", (socket) => {
  console.log(`Client connected: ${socket.id}`);
  clients.set(socket.id, new Set());

  // Send current connection status and available pairs
  if (coinbaseWS && coinbaseWS.getConnectionState() === "CONNECTED") {
    socket.emit("coinbase_connected");
    socket.emit("available_pairs", allUSDPairs);
  }

  // Handle subscription requests
  socket.on("subscribe", (data: { type: string; products?: string[] }) => {
    const clientProducts = clients.get(socket.id)!;

    if (data.type === "all") {
      // Subscribe to all USD pairs (up to limit)
      console.log(`Client ${socket.id} requesting all USD pairs`);
      const pairsToSubscribe = allUSDPairs.slice(0, MAX_SUBSCRIPTIONS);

      pairsToSubscribe.forEach((product) => clientProducts.add(product));
      subscribeToProducts(pairsToSubscribe);

      socket.emit("subscribed_products", Array.from(subscribedPairs));
    } else if (data.type === "popular") {
      // Subscribe to popular pairs only
      const popularPairs = apiClient.getPopularUSDPairs();
      console.log(
        `Client ${socket.id} subscribing to ${popularPairs.length} popular pairs`
      );

      popularPairs.forEach((product) => clientProducts.add(product));
      subscribeToProducts(popularPairs);

      socket.emit("subscribed_products", Array.from(subscribedPairs));
    } else if (data.type === "custom" && data.products) {
      // Subscribe to specific products
      console.log(`Client ${socket.id} subscribing to:`, data.products);

      data.products.forEach((product) => clientProducts.add(product));
      subscribeToProducts(data.products);

      socket.emit("subscribed_products", Array.from(subscribedPairs));
    }
  });

  // Handle unsubscribe requests
  socket.on("unsubscribe", (data: { products: string[] }) => {
    console.log(`Client ${socket.id} unsubscribing from:`, data.products);

    const clientProducts = clients.get(socket.id)!;
    data.products.forEach((product) => clientProducts.delete(product));

    // Check if any other clients are subscribed to these products
    const stillNeeded = data.products.filter((product) => {
      for (const [clientId, products] of clients) {
        if (clientId !== socket.id && products.has(product)) {
          return true;
        }
      }
      return false;
    });

    // Unsubscribe from products no longer needed
    const toUnsubscribe = data.products.filter((p) => !stillNeeded.includes(p));

    if (toUnsubscribe.length > 0 && coinbaseWS) {
      coinbaseWS.unsubscribe("ticker", toUnsubscribe);
      toUnsubscribe.forEach((p) => subscribedPairs.delete(p));
    }
  });

  // Handle disconnect
  socket.on("disconnect", () => {
    console.log(`Client disconnected: ${socket.id}`);

    const clientProducts = clients.get(socket.id) || new Set();
    clients.delete(socket.id);

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
      coinbaseWS.unsubscribe("ticker", toUnsubscribe);
      toUnsubscribe.forEach((p) => subscribedPairs.delete(p));
      console.log(
        `Unsubscribed from ${toUnsubscribe.length} products after client disconnect`
      );
    }
  });
});

// Health check endpoint
app.get("/health", (req, res) => {
  res.json({
    status: "ok",
    coinbase_connected: coinbaseWS?.getConnectionState() === "CONNECTED",
    active_clients: clients.size,
    subscribed_pairs: subscribedPairs.size,
    available_pairs: allUSDPairs.length,
    timestamp: new Date().toISOString(),
  });
});

// API endpoint to get all available pairs
app.get("/api/pairs", (req, res) => {
  res.json({
    all: allUSDPairs,
    subscribed: Array.from(subscribedPairs),
    popular: apiClient.getPopularUSDPairs(),
  });
});

// Start server
const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`);

  // Initialize Coinbase WebSocket connection immediately when server starts
  initializeCoinbaseWebSocket();
});

// Graceful shutdown
process.on("SIGINT", () => {
  console.log("\nShutting down gracefully...");

  if (coinbaseWS) {
    coinbaseWS.disconnect();
  }

  server.close(() => {
    console.log("Server closed");
    process.exit(0);
  });
});
