import React, { useState, useEffect } from "react";
import io from "socket.io-client";
import "./App.css";

function App() {
  const [socket, setSocket] = useState(null);
  const [connected, setConnected] = useState(false);
  const [cryptoTickers, setCryptoTickers] = useState({});
  const [volumeAlerts, setVolumeAlerts] = useState([]);

  useEffect(() => {
    // Connect to the backend WebSocket
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

    // Listen for ticker updates from the backend
    newSocket.on("ticker_update", (data) => {
      console.log("Received ticker update:", data);

      setCryptoTickers((prev) => ({
        ...prev,
        [data.crypto]: {
          ...data,
          lastUpdate: Date.now(),
        },
      }));
    });

    // Optional: Still listen for volume alerts for monitoring
    newSocket.on("volume_alert", (data) => {
      console.log("Volume alert:", data);
      setVolumeAlerts((prev) => [...prev.slice(-9), data]);
    });

    setSocket(newSocket);

    // Cleanup on unmount
    return () => {
      newSocket.close();
    };
  }, []);

  // Convert object to array for rendering
  const tickersArray = Object.values(cryptoTickers).sort((a, b) => {
    // Sort by crypto name for consistent ordering
    return a.crypto.localeCompare(b.crypto);
  });

  // Format price with appropriate decimal places
  const formatPrice = (price) => {
    if (price >= 1000) return price.toFixed(2);
    if (price >= 1) return price.toFixed(4);
    return price.toFixed(6);
  };

  // Format percentage
  const formatPercent = (percent) => {
    const formatted = percent.toFixed(2);
    return percent >= 0 ? `+${formatted}%` : `${formatted}%`;
  };

  return (
    <div className="App">
      <header className="App-header">
        <div className="connection-status">
          Status:{" "}
          <span className={connected ? "connected" : "disconnected"}>
            {connected ? "🟢 Connected" : "🔴 Disconnected"}
          </span>
        </div>

        <div className="alerts-container">
          <h2>Live Market Data</h2>

          {tickersArray.length === 0 ? (
            <p className="no-alerts">Connecting to market data...</p>
          ) : (
            <div className="alerts-grid">
              {tickersArray.map((ticker) => (
                <div key={ticker.crypto} className="ticker-card">
                  <div className="ticker-header">
                    <span className="crypto-name">{ticker.crypto}</span>
                    <span className="timestamp">{ticker.timestamp}</span>
                  </div>

                  <div className="price-display">
                    <div className="current-price">
                      ${formatPrice(ticker.price)}
                    </div>
                    <div
                      className={`price-change ${
                        ticker.price_change_24h >= 0 ? "positive" : "negative"
                      }`}
                    >
                      <span className="change-amount">
                        {ticker.price_change_24h >= 0 ? "▲" : "▼"} $
                        {Math.abs(ticker.price_change_24h).toFixed(2)}
                      </span>
                      <span className="change-percent">
                        ({formatPercent(ticker.price_change_percent_24h)})
                      </span>
                    </div>
                  </div>

                  <div className="ticker-body">
                    <div className="ticker-row">
                      <div className="ticker-item">
                        <span className="label">Bid</span>
                        <span className="value">
                          ${formatPrice(ticker.bid)}
                        </span>
                      </div>
                      <div className="ticker-item">
                        <span className="label">Ask</span>
                        <span className="value">
                          ${formatPrice(ticker.ask)}
                        </span>
                      </div>
                    </div>

                    <div className="ticker-row">
                      <div className="ticker-item">
                        <span className="label">24h Low</span>
                        <span className="value">
                          ${formatPrice(ticker.low_24h)}
                        </span>
                      </div>
                      <div className="ticker-item">
                        <span className="label">24h High</span>
                        <span className="value">
                          ${formatPrice(ticker.high_24h)}
                        </span>
                      </div>
                    </div>

                    <div className="ticker-item full-width">
                      <span className="label">24h Volume</span>
                      <span className="value">
                        {ticker.volume_24h?.toLocaleString()}{" "}
                        {ticker.crypto.split("-")[0]}
                      </span>
                    </div>
                  </div>

                  <div className="update-indicator"></div>
                </div>
              ))}
            </div>
          )}

          {volumeAlerts.length > 0 && (
            <div className="volume-alerts-section">
              <h3>volume alerts</h3>
              <div className="volume-alerts-list">
                {volumeAlerts.slice(-3).map((alert, index) => (
                  <div key={index} className="volume-alert-mini">
                    <span>{alert.crypto}</span>
                    <span className="alert-ratio">
                      {alert.ratio?.toFixed(2)}x surge
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </header>
    </div>
  );
}

export default App;
