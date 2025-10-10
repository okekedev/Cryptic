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
const { spawn } = require('child_process');
const path = require('path');
const port = process.env.PORT || 5000;

// Middleware
app.use(cors());
app.use(express.json());

// Get cryptos to monitor from environment
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
    return { topN: topN };
  }

  // Default: Monitor all USD pairs (will be fetched dynamically)
  console.log('Will monitor all USD pairs (fetched dynamically from Coinbase API)');
  return null;
}

// Validate required environment for Python handler
console.log('🚀 Starting Enhanced WebSocket Backend with Multi-Connection Support');

let wsHandlerProcess = null;
let wsHandlerReady = false;
const currentTickers = {};

// Historical price data storage (in-memory circular buffer)
// Store last 48 hours of 1-minute candles for each symbol
const MAX_HISTORY_MINUTES = 48 * 60; // 48 hours
const historicalData = {}; // symbol -> [{timestamp, open, high, low, close, volume}]

// Helper to add price data to history
function addToHistory(symbol, price, volume) {
  if (!historicalData[symbol]) {
    historicalData[symbol] = [];
  }

  const now = Date.now();
  const currentMinute = Math.floor(now / 60000) * 60000; // Round to minute

  // Check if we already have data for this minute
  const lastCandle = historicalData[symbol][historicalData[symbol].length - 1];

  if (lastCandle && lastCandle.timestamp === currentMinute) {
    // Update existing candle
    lastCandle.high = Math.max(lastCandle.high, price);
    lastCandle.low = Math.min(lastCandle.low, price);
    lastCandle.close = price;
    lastCandle.volume += volume || 0;
  } else {
    // Create new candle
    historicalData[symbol].push({
      timestamp: currentMinute,
      open: price,
      high: price,
      low: price,
      close: price,
      volume: volume || 0
    });

    // Trim old data (keep only MAX_HISTORY_MINUTES)
    if (historicalData[symbol].length > MAX_HISTORY_MINUTES) {
      historicalData[symbol].shift();
    }
  }
}

// Start Python WebSocket handler process
function startPythonWebSocketHandler() {
  const pythonScript = path.join(__dirname, 'websocket-python', 'python_ws_bridge.py');

  const env = {
    ...process.env,
    CRYPTO_CONFIG: JSON.stringify(getCryptosToMonitor()),
    PRODUCTS_PER_CONNECTION: process.env.PRODUCTS_PER_CONNECTION || '15',
    VOLUME_THRESHOLD: process.env.VOLUME_THRESHOLD || '1.5',
    WINDOW_MINUTES: process.env.WINDOW_MINUTES || '5'
  };

  wsHandlerProcess = spawn('python', [pythonScript], {
    env: env,
    stdio: ['pipe', 'pipe', 'pipe']
  });

  wsHandlerProcess.stdout.on('data', (data) => {
    const lines = data.toString().split('\n').filter(line => line.trim());

    for (const line of lines) {
      try {
        if (line.startsWith('TICKER:')) {
          const tickerData = JSON.parse(line.substring(7));
          currentTickers[tickerData.crypto] = tickerData;

          // Store in historical data
          addToHistory(tickerData.crypto, tickerData.price, tickerData.volume_24h);

          // Emit to all connected Socket.IO clients
          io.emit("ticker_update", tickerData);

        } else if (line.startsWith('VOLUME_ALERT:')) {
          const alertData = JSON.parse(line.substring(13));
          io.emit("volume_alert", alertData);

        } else if (line.startsWith('STATUS:')) {
          const statusData = JSON.parse(line.substring(7));
          if (statusData.ready) {
            wsHandlerReady = true;
            console.log('✅ Enhanced WebSocket handler is ready');
          }

        } else if (line.startsWith('HEALTH:')) {
          const healthData = JSON.parse(line.substring(7));
          console.log(`📊 Health: ${healthData.connected_connections}/${healthData.total_connections} connections, ${healthData.active_products}/${healthData.total_products} products active`);

        } else {
          console.log(`WebSocket Handler: ${line}`);
        }
      } catch (e) {
        // Regular log line, just print it
        console.log(`WebSocket Handler: ${line}`);
      }
    }
  });

  wsHandlerProcess.stderr.on('data', (data) => {
    console.error(`WebSocket Handler Error: ${data}`);
  });

  wsHandlerProcess.on('close', (code) => {
    console.log(`WebSocket handler process exited with code ${code}`);
    wsHandlerReady = false;

    // Restart after delay if not intentional shutdown
    if (code !== 0) {
      console.log('Restarting WebSocket handler in 5 seconds...');
      setTimeout(startPythonWebSocketHandler, 5000);
    }
  });

  wsHandlerProcess.on('error', (error) => {
    console.error('Failed to start WebSocket handler process:', error);
  });
}

// Routes
app.post("/alert", (req, res) => {
  const { crypto, current_vol, avg_vol, threshold } = req.body;
  io.emit("trade_update", { crypto, current_vol, avg_vol, threshold });
  res.sendStatus(200);
});

app.get("/tickers", (req, res) => {
  res.json(currentTickers);
});

app.get("/tickers/:crypto", (req, res) => {
  const ticker = currentTickers[req.params.crypto];
  if (ticker) {
    res.json(ticker);
  } else {
    res.status(404).json({ error: "Ticker not found" });
  }
});

// Historical data endpoint
app.get("/api/historical/:symbol", (req, res) => {
  const symbol = req.params.symbol;
  const hours = parseInt(req.query.hours) || 24;

  if (!historicalData[symbol] || historicalData[symbol].length === 0) {
    return res.status(404).json({
      error: "No historical data available for this symbol",
      symbol: symbol,
      available_symbols: Object.keys(historicalData).filter(s => historicalData[s].length > 0)
    });
  }

  // Calculate how many minutes of data to return
  const minutesToReturn = hours * 60;
  const allData = historicalData[symbol];

  // Get the most recent data
  const startIndex = Math.max(0, allData.length - minutesToReturn);
  const data = allData.slice(startIndex);

  res.json(data);
});

// Priority pair management endpoints
app.post("/priority-pairs", (req, res) => {
  try {
    const { product_id, action } = req.body;

    if (!product_id || !action) {
      return res.status(400).json({
        error: "Missing required fields: product_id and action"
      });
    }

    // Send command to Python handler
    if (wsHandlerProcess && wsHandlerReady) {
      const command = {
        type: 'priority_pair',
        action: action,
        product_id: product_id
      };

      wsHandlerProcess.stdin.write(JSON.stringify(command) + '\n');

      res.json({
        success: true,
        message: `${action === 'add' ? 'Added' : 'Removed'} ${product_id} ${action === 'add' ? 'to' : 'from'} priority monitoring`
      });
    } else {
      res.status(503).json({
        error: "WebSocket handler not ready"
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
    if (wsHandlerProcess && wsHandlerReady) {
      // Request stats from Python handler
      const command = { type: 'get_priority_stats' };
      wsHandlerProcess.stdin.write(JSON.stringify(command) + '\n');

      // For now, return basic response (you might want to implement proper async response)
      res.json({
        message: "Priority stats requested from handler"
      });
    } else {
      res.status(503).json({
        error: "WebSocket handler not ready"
      });
    }
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
    if (wsHandlerProcess && wsHandlerReady) {
      const command = { type: 'clear_priority_pairs' };
      wsHandlerProcess.stdin.write(JSON.stringify(command) + '\n');

      res.json({
        success: true,
        message: "Cleared all priority pairs"
      });
    } else {
      res.status(503).json({
        error: "WebSocket handler not ready"
      });
    }
  } catch (error) {
    console.error("Error clearing priority pairs:", error);
    res.status(500).json({
      error: "Internal server error",
      message: error.message
    });
  }
});

// Batch set priority pairs (for live trading - replaces all priority pairs)
app.put("/priority-pairs", (req, res) => {
  try {
    const { product_ids } = req.body;

    if (!Array.isArray(product_ids)) {
      return res.status(400).json({
        error: "product_ids must be an array"
      });
    }

    if (wsHandlerProcess && wsHandlerReady) {
      // Clear existing priority pairs first
      wsHandlerProcess.stdin.write(JSON.stringify({ type: 'clear_priority_pairs' }) + '\n');

      // Add new priority pairs
      if (product_ids.length > 0) {
        product_ids.forEach(product_id => {
          const command = {
            type: 'priority_pair',
            action: 'add',
            product_id: product_id
          };
          wsHandlerProcess.stdin.write(JSON.stringify(command) + '\n');
        });
      }

      res.json({
        success: true,
        message: `Set ${product_ids.length} priority pair(s)`,
        product_ids: product_ids
      });
    } else {
      res.status(503).json({
        error: "WebSocket handler not ready"
      });
    }
  } catch (error) {
    console.error("Error setting priority pairs:", error);
    res.status(500).json({
      error: "Internal server error",
      message: error.message
    });
  }
});

// Health check endpoint
app.get("/health", (req, res) => {
  if (wsHandlerProcess && wsHandlerReady) {
    const command = { type: 'get_health' };
    wsHandlerProcess.stdin.write(JSON.stringify(command) + '\n');
  }

  res.json({
    status: wsHandlerReady ? "ready" : "not_ready",
    websocket_handler: wsHandlerReady,
    active_tickers: Object.keys(currentTickers).length
  });
});

app.get("/", (req, res) => {
  res.send("Enhanced Backend with Multi-Connection WebSocket Support");
});

// Socket.IO connection handling
io.on("connection", (socket) => {
  console.log("Client connected:", socket.id);

  // Send current ticker data immediately upon connection
  Object.values(currentTickers).forEach((ticker) => {
    if (ticker) {
      socket.emit("ticker_update", ticker);
    }
  });

  // Handle spike_alert events from spike-detector and broadcast to all clients
  socket.on("spike_alert", (data) => {
    console.log(`📢 Received spike alert from ${socket.id}:`, data.symbol, data.event_type);
    // Broadcast to all connected clients (including telegram-bot and paper-trading)
    io.emit("spike_alert", data);
  });

  socket.on("disconnect", () => {
    console.log("Client disconnected:", socket.id);
  });
});

// Graceful shutdown
process.on("SIGTERM", () => {
  console.log("SIGTERM received, closing connections...");
  if (wsHandlerProcess) {
    wsHandlerProcess.kill('SIGTERM');
  }
  http.close(() => {
    console.log("Server closed");
  });
});

// Start WebSocket handler
startPythonWebSocketHandler();

// Start server
http.listen(port, () => {
  console.log(`Enhanced Backend listening at http://localhost:${port}`);
  console.log("Multi-Connection WebSocket handler starting...");
});