// src/server.ts

import express from "express";
import http from "http";
import { Server as SocketIOServer } from "socket.io";
import path from "path";
import dotenv from "dotenv";
import { CoinbaseWebSocket } from "./websocket/CoinbaseWebSocket";
import { TickerMessage } from "./types/coinbase";
import {
  ACTIVE_USD_PAIRS,
  POPULAR_USD_PAIRS,
  getUSDPairInfo,
} from "./constants/UsdPairs";
import { pairUpdaterService } from "./services/pairUpdaterService";

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

// Store subscribed pairs
let subscribedPairs: Set<string> = new Set();

// Maximum number of simultaneous subscriptions (to avoid overwhelming the connection)
const MAX_SUBSCRIPTIONS = 100;

// Initialize and connect to Coinbase WebSocket
function initializeCoinbaseWebSocket() {
  console.log("=== Starting Coinbase initialization ===");

  const pairInfo = getUSDPairInfo();
  console.log(
    `Using hardcoded pairs: ${pairInfo.active} active USD pairs (last updated: ${pairInfo.lastUpdated})`
  );

  coinbaseWS = new CoinbaseWebSocket({
    channels: ["ticker", "heartbeats"],
    productIds: [], // Start with empty, will subscribe based on client needs
  });

  setupWebSocketHandlers();
  coinbaseWS.connect();
  console.log("WebSocket connection initiated");
}

// Separate WebSocket handlers setup
function setupWebSocketHandlers() {
  if (!coinbaseWS) {
    console.error("Coinbase WebSocket is not initialized.");
    return;
  }

  // Forward ticker updates to all connected clients
  coinbaseWS.on("ticker", (ticker: TickerMessage) => {
    io.emit("ticker", ticker);
  });

  // Log connection events
  coinbaseWS.on("connected", () => {
    console.log("=== WebSocket connected to Coinbase ===");
    io.emit("coinbase_connected");

    // Send available pairs to all clients immediately
    io.emit("available_pairs", ACTIVE_USD_PAIRS);
  });

  coinbaseWS.on("disconnected", (data) => {
    console.log("=== WebSocket disconnected from Coinbase ===", data);
    io.emit("coinbase_disconnected");
  });

  coinbaseWS.on("error", (error) => {
    console.error("=== Coinbase WebSocket error ===", error);
    io.emit("error", { message: error.message });
  });

  coinbaseWS.on("reconnecting", (data) => {
    console.log("=== WebSocket reconnecting to Coinbase ===", data);
    io.emit("coinbase_reconnecting", data);
  });
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

  // Send current connection status and available pairs immediately
  if (coinbaseWS && coinbaseWS.getConnectionState() === "CONNECTED") {
    socket.emit("coinbase_connected");
  }

  // Always send available pairs (they're hardcoded)
  socket.emit("available_pairs", ACTIVE_USD_PAIRS);

  // Handle subscription requests
  socket.on("subscribe", (data: { type: string; products?: string[] }) => {
    const clientProducts = clients.get(socket.id)!;

    if (data.type === "all") {
      // Subscribe to all USD pairs (up to limit)
      console.log(`Client ${socket.id} requesting all USD pairs`);
      const pairsToSubscribe = ACTIVE_USD_PAIRS.slice(0, MAX_SUBSCRIPTIONS);

      pairsToSubscribe.forEach((product) => clientProducts.add(product));
      subscribeToProducts(pairsToSubscribe);

      socket.emit("subscribed_products", Array.from(subscribedPairs));
    } else if (data.type === "popular") {
      // Subscribe to popular pairs only
      console.log(
        `Client ${socket.id} subscribing to ${POPULAR_USD_PAIRS.length} popular pairs`
      );

      POPULAR_USD_PAIRS.forEach((product) => clientProducts.add(product));
      subscribeToProducts([...POPULAR_USD_PAIRS]);

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
  const pairInfo = getUSDPairInfo();
  res.json({
    status: "ok",
    coinbase_connected: coinbaseWS?.getConnectionState() === "CONNECTED",
    active_clients: clients.size,
    subscribed_pairs: subscribedPairs.size,
    available_pairs: pairInfo,
    timestamp: new Date().toISOString(),
  });
});

// API endpoint to get all available pairs
app.get("/api/pairs", (req, res) => {
  res.json({
    all: ACTIVE_USD_PAIRS,
    subscribed: Array.from(subscribedPairs),
    popular: POPULAR_USD_PAIRS,
    info: getUSDPairInfo(),
  });
});

// API endpoint to manually trigger pair update (for testing/admin use)
app.post("/api/pairs/update", async (req, res) => {
  // Optional: Add authentication here for production
  const adminKey = req.headers["x-admin-key"];
  if (process.env.ADMIN_KEY && adminKey !== process.env.ADMIN_KEY) {
    return res.status(401).json({ error: "Unauthorized" });
  }

  console.log("🔄 Manual pair update requested");

  try {
    // Force update by clearing last update info
    const fs = require("fs/promises");
    const lastUpdateFile = path.join(__dirname, "../.last-pair-update.json");
    await fs.unlink(lastUpdateFile).catch(() => {}); // Ignore if file doesn't exist

    // Trigger update
    await pairUpdaterService["checkAndUpdate"](); // Access private method for manual trigger

    res.json({
      success: true,
      message: "Update process triggered",
      currentPairs: getUSDPairInfo(),
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: "Failed to trigger update",
      message: (error as Error).message,
    });
  }
});

// API endpoint to check updater status
app.get("/api/pairs/update-status", async (req, res) => {
  try {
    const fs = require("fs/promises");
    const lastUpdateFile = path.join(__dirname, "../.last-pair-update.json");

    let lastUpdate = null;
    try {
      const data = await fs.readFile(lastUpdateFile, "utf-8");
      lastUpdate = JSON.parse(data);
    } catch {
      // No update history yet
    }

    const now = new Date();
    const currentWeek = Math.ceil(
      (now.getTime() - new Date(now.getFullYear(), 0, 1).getTime()) /
        86400000 /
        7
    );

    res.json({
      currentWeek,
      currentYear: now.getFullYear(),
      lastUpdate,
      nextCheckIn: "Checks every 24 hours",
      pairInfo: getUSDPairInfo(),
    });
  } catch (error) {
    res.status(500).json({ error: "Failed to get status" });
  }
});

// Start server
const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`);

  // Initialize Coinbase WebSocket connection immediately when server starts
  initializeCoinbaseWebSocket();

  // Start the pair updater service
  pairUpdaterService.start();
  console.log(
    "✓ Pair updater service started (checks every 24 hours for weekly updates)"
  );
});

// Graceful shutdown
process.on("SIGINT", () => {
  console.log("\nShutting down gracefully...");

  // Stop the updater service
  pairUpdaterService.stop();

  if (coinbaseWS) {
    coinbaseWS.disconnect();
  }

  server.close(() => {
    console.log("Server closed");
    process.exit(0);
  });
});
