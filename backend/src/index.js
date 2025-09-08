const express = require("express");
const cors = require("cors");
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

// USD trading pairs list
const USD_PAIRS = require("./usd-pairs");

// Get cryptos to monitor from environment or use defaults
function getCryptosToMonitor() {
  // Check if we should use ALL USD pairs
  if (process.env.USE_ALL_USD_PAIRS === "true") {
    console.log(`Monitoring ALL ${USD_PAIRS.length} USD pairs`);
    return USD_PAIRS;
  }

  // Check if specific cryptos are defined
  if (process.env.MONITORING_CRYPTOS) {
    return process.env.MONITORING_CRYPTOS.split(",");
  }

  // Check if we want top N pairs
  if (process.env.TOP_USD_PAIRS) {
    const topN = parseInt(process.env.TOP_USD_PAIRS);
    // Prioritize major pairs first
    const priorityPairs = [
      "BTC-USD",
      "ETH-USD",
      "SOL-USD",
      "DOGE-USD",
      "XRP-USD",
      "ADA-USD",
      "AVAX-USD",
      "LINK-USD",
      "DOT-USD",
      "MATIC-USD",
    ];
    const otherPairs = USD_PAIRS.filter(
      (pair) => !priorityPairs.includes(pair)
    );
    return [...priorityPairs, ...otherPairs].slice(0, topN);
  }

  // Default to major pairs
  return ["BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "XRP-USD"];
}

// Initialize Coinbase WebSocket handler
const wsHandler = new CoinbaseWebSocketHandler({
  wsUrl: process.env.WS_URL || "wss://ws-feed.exchange.coinbase.com",
  cryptos: getCryptosToMonitor(),
  volumeThreshold: parseFloat(process.env.VOLUME_THRESHOLD) || 1.5,
  windowMinutes: parseInt(process.env.WINDOW_MINUTES) || 5,
});

// Connect to Coinbase WebSocket
wsHandler.connect();

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

app.get("/", (req, res) => {
  res.send("Backend running with ticker streaming");
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
  console.log(
    "WebSocket streaming ticker data for:",
    process.env.MONITORING_CRYPTOS || "BTC-USD,ETH-USD"
  );
});
