import React, { useState, useEffect } from "react";
import io from "socket.io-client";
import "./App.css";

function App() {
  const [socket, setSocket] = useState(null);
  const [connected, setConnected] = useState(false);
  const [tradeAlerts, setTradeAlerts] = useState([]);

  useEffect(() => {
    // Connect to the backend WebSocket
    // Using relative path so it works with the nginx proxy
    const newSocket = io("/", {
      path: "/socket.io/",
      transports: ["websocket", "polling"],
    });

    newSocket.on("connect", () => {
      console.log("Connected to WebSocket");
      setConnected(true);
    });

    newSocket.on("disconnect", () => {
      console.log("Disconnected from WebSocket");
      setConnected(false);
    });

    // Listen for trade updates from the backend
    newSocket.on("trade_update", (data) => {
      console.log("Received trade update:", data);

      // Add timestamp to the alert
      const alertWithTime = {
        ...data,
        timestamp: new Date().toLocaleTimeString(),
        id: Date.now(), // Simple ID for React key
      };

      // Add new alert to the beginning of the array
      setTradeAlerts((prev) => [alertWithTime, ...prev].slice(0, 50)); // Keep last 50 alerts
    });

    setSocket(newSocket);

    // Cleanup on unmount
    return () => {
      newSocket.close();
    };
  }, []);

  return (
    <div className="App">
      <header className="App-header">
        <h1>Crypto Trading Alerts</h1>

        <div className="connection-status">
          Status:{" "}
          <span className={connected ? "connected" : "disconnected"}>
            {connected ? "🟢 Connected" : "🔴 Disconnected"}
          </span>
        </div>

        <div className="alerts-container">
          <h2>Volume Alerts</h2>

          {tradeAlerts.length === 0 ? (
            <p className="no-alerts">Waiting for alerts...</p>
          ) : (
            <div className="alerts-list">
              {tradeAlerts.map((alert) => (
                <div key={alert.id} className="alert-item">
                  <div className="alert-header">
                    <span className="crypto-name">{alert.crypto}</span>
                    <span className="timestamp">{alert.timestamp}</span>
                  </div>
                  <div className="alert-body">
                    <div className="volume-info">
                      <span className="label">Current Volume:</span>
                      <span className="value">
                        {alert.current_vol?.toLocaleString()}
                      </span>
                    </div>
                    <div className="volume-info">
                      <span className="label">Average Volume:</span>
                      <span className="value">
                        {alert.avg_vol?.toLocaleString()}
                      </span>
                    </div>
                    <div className="volume-info">
                      <span className="label">Threshold:</span>
                      <span className="value">{alert.threshold}x</span>
                    </div>
                    <div className="volume-ratio">
                      <span className="label">Ratio:</span>
                      <span className="value ratio">
                        {(alert.current_vol / alert.avg_vol).toFixed(2)}x
                        average
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </header>
    </div>
  );
}

export default App;
