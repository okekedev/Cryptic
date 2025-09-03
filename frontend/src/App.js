import React, { useState, useEffect } from "react";
import io from "socket.io-client";
import "./App.css";

function App() {
  const [socket, setSocket] = useState(null);
  const [connected, setConnected] = useState(false);
  const [exchangeConnected, setExchangeConnected] = useState(false);
  const [cryptoAlerts, setCryptoAlerts] = useState({});
  const [volumeStats, setVolumeStats] = useState({});

  useEffect(() => {
    // Connect to the backend WebSocket
    const newSocket = io("/", {
      path: "/socket.io/",
      transports: ["websocket", "polling"],
    });

    newSocket.on("connect", () => {
      console.log("Connected to WebSocket");
      setConnected(true);
      // Request current status when connected
      newSocket.emit("get_status");
    });

    newSocket.on("disconnect", () => {
      console.log("Disconnected from WebSocket");
      setConnected(false);
      setExchangeConnected(false);
    });

    // Listen for exchange connection status
    newSocket.on("exchange_connected", (data) => {
      console.log("Exchange connected:", data);
      setExchangeConnected(true);
    });

    newSocket.on("exchange_disconnected", (data) => {
      console.log("Exchange disconnected:", data);
      setExchangeConnected(false);
    });

    // Listen for status updates
    newSocket.on("status", (data) => {
      console.log("Status update:", data);
      if (data.volumeStats) {
        setVolumeStats(data.volumeStats);
      }
    });

    // Listen for trade updates from the backend
    newSocket.on("trade_update", (data) => {
      console.log("Received trade update:", data);

      // Update the specific crypto card
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
    return b.lastUpdate - a.lastUpdate;
  });

  // Get volume stats for a crypto
  const getVolumeInfo = (crypto) => {
    const stats = volumeStats[crypto];
    if (!stats) return null;

    return (
      <div className="volume-info">
        <span className="stat">
          Current: {stats.currentVolume?.toFixed(2) || "0"}
        </span>
        <span className="stat">
          Avg: {stats.averageVolume?.toFixed(2) || "0"}
        </span>
        <span className="stat">
          Ratio: {stats.surgeRatio?.toFixed(2) || "0"}x
        </span>
      </div>
    );
  };

  return (
    <div className="App">
      <header className="App-header">
        <h1>Crypto Trading Alerts</h1>

        <div className="connection-status">
          <div>
            Backend:{" "}
            <span className={connected ? "connected" : "disconnected"}>
              {connected ? "🟢 Connected" : "🔴 Disconnected"}
            </span>
          </div>
          <div>
            Exchange:{" "}
            <span className={exchangeConnected ? "connected" : "disconnected"}>
              {exchangeConnected ? "🟢 Connected" : "🔴 Disconnected"}
            </span>
          </div>
        </div>

        <div className="alerts-container">
          <h2>Live Volume Alerts</h2>

          {alertsArray.length === 0 ? (
            <div className="no-alerts">
              <p>Monitoring for volume surges...</p>
              <p className="monitoring-info">
                Watching: {Object.keys(volumeStats).join(", ") || "Loading..."}
              </p>
            </div>
          ) : (
            <div className="alerts-grid">
              {alertsArray.map((alert) => (
                <div key={alert.crypto} className="alert-card">
                  <div className="alert-header">
                    <h3>{alert.crypto}</h3>
                    <span className="alert-time">{alert.timestamp}</span>
                  </div>

                  <div className="alert-body">
                    <div className="surge-indicator">
                      <span className="surge-label">
                        Volume Surge Detected!
                      </span>
                      <span className="surge-ratio">
                        {alert.surge_ratio?.toFixed(2) ||
                          (alert.current_vol / alert.avg_vol).toFixed(2)}
                        x
                      </span>
                    </div>

                    <div className="volume-details">
                      <div className="volume-item">
                        <span className="label">Current Volume:</span>
                        <span className="value">
                          {alert.current_vol.toFixed(2)}
                        </span>
                      </div>
                      <div className="volume-item">
                        <span className="label">Average Volume:</span>
                        <span className="value">
                          {alert.avg_vol.toFixed(2)}
                        </span>
                      </div>
                      <div className="volume-item">
                        <span className="label">Threshold:</span>
                        <span className="value">{alert.threshold}x</span>
                      </div>
                    </div>
                  </div>

                  {/* Show real-time volume stats if available */}
                  {getVolumeInfo(alert.crypto)}
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
