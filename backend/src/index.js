const express = require("express");
const cors = require("cors");
const app = express();
const http = require("http").createServer(app);
const io = require("socket.io")(http, {
  cors: {
    origin: process.env.FRONTEND_URL || "http://localhost:3000",
    methods: ["GET", "POST"],
  },
});
const WebSocketManager = require("./websocket");

const port = process.env.PORT || 3000;

// Initialize WebSocket manager
const wsManager = new WebSocketManager(io);

// Middleware
app.use(cors());
app.use(express.json());

// Routes
app.get("/", (req, res) => {
  res.send("Backend running");
});

// Health check endpoint
app.get("/health", (req, res) => {
  res.json({
    status: "ok",
    timestamp: new Date().toISOString(),
    websocket: wsManager.getStatus(),
  });
});

// Status endpoint for monitoring
app.get("/status", (req, res) => {
  res.json(wsManager.getStatus());
});

// Manual alert endpoint (kept for backward compatibility with monitoring bot)
app.post("/alert", (req, res) => {
  const { crypto, current_vol, avg_vol, threshold } = req.body;
  io.emit("trade_update", { crypto, current_vol, avg_vol, threshold });
  res.sendStatus(200);
});

// Socket.IO connection handling
io.on("connection", (socket) => {
  console.log("Client connected:", socket.id);

  // Send current status to newly connected client
  socket.emit("status", wsManager.getStatus());

  socket.on("disconnect", () => {
    console.log("Client disconnected:", socket.id);
  });

  // Allow clients to request current status
  socket.on("get_status", () => {
    socket.emit("status", wsManager.getStatus());
  });
});

// Start server
async function startServer() {
  try {
    // Start WebSocket manager
    await wsManager.start();

    // Start HTTP server
    http.listen(port, () => {
      console.log(`Backend listening at http://localhost:${port}`);
    });
  } catch (error) {
    console.error("Failed to start server:", error);
    process.exit(1);
  }
}

// Graceful shutdown
process.on("SIGTERM", () => {
  console.log("SIGTERM received, shutting down gracefully...");
  wsManager.stop();
  http.close(() => {
    console.log("Server closed");
    process.exit(0);
  });
});

process.on("SIGINT", () => {
  console.log("SIGINT received, shutting down gracefully...");
  wsManager.stop();
  http.close(() => {
    console.log("Server closed");
    process.exit(0);
  });
});

// Start the server
startServer();
