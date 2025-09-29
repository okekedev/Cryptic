const express = require("express");
const cors = require("cors");
require('dotenv').config();
const app = express();
const http = require("http").createServer(app);
const io = require("socket.io")(http, {
  cors: {
    origin: process.env.FRONTEND_URL || "http://localhost:5000",
    methods: ["GET", "POST"],
  },
});
const CoinbaseWebSocketHandler = require("./websocket-handler");
const port = process.env.PORT || 5000;

// Middleware
app.use(cors());
app.use(express.json());

// Note: USD pairs are now fetched dynamically from Coinbase API

// Get cryptos to monitor from environment - now uses dynamic fetching
function getCryptosToMonitor() {
  // Check if specific cryptos are defined
  if (process.env.MONITORING_CRYPTOS) {
    const customPairs = process.env.MONITORING_CRYPTOS.split(",").map(pair => pair.trim());
    console.log(`Monitoring custom pairs: ${customPairs.join(", ")}`);
    return customPairs;
  }

  // Check if we want top N pairs
  if (process.env.TOP_USD_PAIRS) {
    const topN = parseInt(process.env.TOP_USD_PAIRS);
    console.log(`Will monitor top ${topN} USD pairs (determined dynamically)`);
    return { topN: topN }; // Return object to indicate we want top N pairs
  }

  // Default: Monitor all USD pairs (will be fetched dynamically)
  console.log('Will monitor all USD pairs (fetched dynamically from Coinbase API)');
  return null; // null indicates use dynamic fetching
}

// Validate required Coinbase API credentials
if (!process.env.COINBASE_API_KEY || !process.env.COINBASE_SIGNING_KEY) {
  console.error('❌ COINBASE_API_KEY and COINBASE_SIGNING_KEY environment variables are required.');
  console.error('   Get your API credentials from: https://portal.cloud.coinbase.com/access/api');
  console.error('   See .env.example for configuration details.');
  process.exit(1);
}

// Initialize Coinbase Advanced Trade WebSocket handler
const cryptoConfig = getCryptosToMonitor();
const wsHandler = new CoinbaseWebSocketHandler({
  wsUrl: process.env.WS_URL || "wss://advanced-trade-ws.coinbase.com",
  cryptoConfig: cryptoConfig, // Pass the config object instead of cryptos array
  volumeThreshold: parseFloat(process.env.VOLUME_THRESHOLD) || 1.5,
  windowMinutes: parseInt(process.env.WINDOW_MINUTES) || 5,
  apiKey: process.env.COINBASE_API_KEY,
  signingKey: process.env.COINBASE_SIGNING_KEY,
});

// Initialize WebSocket handler (fetches USD pairs and connects)
wsHandler.initialize();

// Handle ticker updates - stream to all connected clients
wsHandler.on("ticker_update", (tickerData) => {
  io.emit("ticker_update", tickerData);
});

// // Handle volume alerts - can be used by bots or logged
// wsHandler.on("volume_alert", (alertData) => {
//   console.log("Volume Alert:", alertData);
//   // Optionally emit to a specific channel for bots
//   io.emit("volume_alert", alertData);

//   // Also emit as trade_update for backward compatibility
//   io.emit("trade_update", {
//     crypto: alertData.crypto,
//     current_vol: alertData.current_vol,
//     avg_vol: alertData.avg_vol,
//     threshold: alertData.threshold,
//   });
// });

// Routes
app.post("/alert", (req, res) => {
  // Keep this endpoint for backward compatibility
  const { crypto, current_vol, avg_vol, threshold } = req.body;
  io.emit("trade_update", { crypto, current_vol, avg_vol, threshold });
  res.sendStatus(200);
});

// New endpoint to get current ticker data
app.get("/tickers", (req, res) => {
  res.json(wsHandler.getAllTickers());
});

app.get("/tickers/:crypto", (req, res) => {
  const ticker = wsHandler.getCurrentTicker(req.params.crypto);
  if (ticker) {
    res.json(ticker);
  } else {
    res.status(404).json({ error: "Ticker not found" });
  }
});

// Priority pair management endpoints for trading system
app.post("/priority-pairs", (req, res) => {
  try {
    const { product_id, action } = req.body;

    if (!product_id || !action) {
      return res.status(400).json({
        error: "Missing required fields: product_id and action"
      });
    }

    if (action === "add") {
      wsHandler.addPriorityPair(product_id);
      res.json({
        success: true,
        message: `Added ${product_id} to priority monitoring`,
        priority_pairs: wsHandler.getPriorityPairs()
      });
    } else if (action === "remove") {
      wsHandler.removePriorityPair(product_id);
      res.json({
        success: true,
        message: `Removed ${product_id} from priority monitoring`,
        priority_pairs: wsHandler.getPriorityPairs()
      });
    } else {
      res.status(400).json({
        error: "Invalid action. Use 'add' or 'remove'"
      });
    }
  } catch (error) {
    console.error("Error managing priority pairs:", error);
    res.status(500).json({
      error: "Internal server error",
      message: error.message
    });
  }
});

app.get("/priority-pairs", (req, res) => {
  try {
    const stats = wsHandler.getPriorityStats();
    res.json(stats);
  } catch (error) {
    console.error("Error getting priority pairs:", error);
    res.status(500).json({
      error: "Internal server error",
      message: error.message
    });
  }
});

app.delete("/priority-pairs", (req, res) => {
  try {
    wsHandler.clearPriorityPairs();
    res.json({
      success: true,
      message: "Cleared all priority pairs"
    });
  } catch (error) {
    console.error("Error clearing priority pairs:", error);
    res.status(500).json({
      error: "Internal server error",
      message: error.message
    });
  }
});

app.get("/", (req, res) => {
  res.send("Backend running with ticker streaming and priority pair management");
});

// Socket.IO connection handling
io.on("connection", (socket) => {
  console.log("Client connected:", socket.id);

  // Send current ticker data immediately upon connection
  const currentTickers = wsHandler.getAllTickers();
  Object.values(currentTickers).forEach((ticker) => {
    if (ticker) {
      socket.emit("ticker_update", ticker);
    }
  });

  socket.on("disconnect", () => {
    console.log("Client disconnected:", socket.id);
  });
});

// Graceful shutdown
process.on("SIGTERM", () => {
  console.log("SIGTERM received, closing connections...");
  wsHandler.disconnect();
  http.close(() => {
    console.log("Server closed");
  });
});

// Start server
http.listen(port, () => {
  console.log(`Backend listening at http://localhost:${port}`);
  console.log("Advanced Trade WebSocket with dynamic USD pair discovery enabled");
});
