import React, { useState, useEffect } from "react";
import io from "socket.io-client";
import "./App.css";

function App() {
  const [socket, setSocket] = useState(null);
  const [connected, setConnected] = useState(false);
  const [cryptoAlerts, setCryptoAlerts] = useState({});

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

      // Update the specific crypto card instead of adding to a list
      setCryptoAlerts((prev) => ({
        ...prev,
        [data.crypto]: {
          ...data,
          timestamp: new Date().toLocaleTimeString(),
          lastUpdate: Date.now(),
        },
      }));
    });

    setSocket(newSocket);

    // Cleanup on unmount
    return () => {
      newSocket.close();
    };
  }, []);

  // Convert object to array for rendering
  const alertsArray = Object.values(cryptoAlerts).sort((a, b) => {
    // Sort by most recent update first
    return b.lastUpdate - a.lastUpdate;
  });

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
          <h2>Live Volume Alerts</h2>

          {alertsArray.length === 0 ? (
            <p className="no-alerts">Waiting for alerts...</p>
          ) : (
            <div className="alerts-grid">
              {alertsArray.map((alert) => (
                <div key={alert.crypto} className="alert-card">
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
                  <div className="update-indicator"></div>
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
