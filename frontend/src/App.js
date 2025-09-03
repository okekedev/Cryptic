import React, { useState, useEffect, useMemo } from "react";
import io from "socket.io-client";
import "./App.css";

function App() {
  const [socket, setSocket] = useState(null);
  const [connected, setConnected] = useState(false);
  const [cryptoTickers, setCryptoTickers] = useState({});
  const [viewMode, setViewMode] = useState("grid"); // grid or list
  const [sortBy, setSortBy] = useState("change"); // Default to sorting by change
  const [filterText, setFilterText] = useState("");

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
      setCryptoTickers((prev) => ({
        ...prev,
        [data.crypto]: {
          ...data,
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

  // Convert object to array and apply sorting/filtering
  const tickersArray = useMemo(() => {
    let tickers = Object.values(cryptoTickers);

    // Apply filter
    if (filterText) {
      tickers = tickers.filter((ticker) =>
        ticker.crypto.toLowerCase().includes(filterText.toLowerCase())
      );
    }

    // Apply sort
    tickers.sort((a, b) => {
      switch (sortBy) {
        case "price":
          return b.price - a.price;
        case "change":
          // Sort by percentage change - highest to lowest
          return b.price_change_percent_24h - a.price_change_percent_24h;
        case "volume":
          return b.volume_24h - a.volume_24h;
        case "name":
        default:
          return a.crypto.localeCompare(b.crypto);
      }
    });

    return tickers;
  }, [cryptoTickers, sortBy, filterText]);

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
        <h1>crypto ticker ({Object.keys(cryptoTickers).length} pairs)</h1>

        <div className="connection-status">
          status:{" "}
          <span className={connected ? "connected" : "disconnected"}>
            {connected ? "connected" : "disconnected"}
          </span>
          {connected && Object.keys(cryptoTickers).length === 0 && (
            <span className="loading-indicator"> - loading tickers...</span>
          )}
        </div>

        <div className="controls">
          <div className="search-box">
            <input
              type="text"
              placeholder="search pairs..."
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              className="search-input"
            />
          </div>

          <div className="sort-controls">
            <span>sort by: </span>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
              className="sort-select"
            >
              <option value="change">24h change</option>
              <option value="name">name</option>
              <option value="price">price</option>
              <option value="volume">volume</option>
            </select>
          </div>

          <div className="view-controls">
            <span>view: </span>
            <button
              className={viewMode === "grid" ? "active" : ""}
              onClick={() => setViewMode("grid")}
            >
              grid
            </button>
            <button
              className={viewMode === "list" ? "active" : ""}
              onClick={() => setViewMode("list")}
            >
              list
            </button>
          </div>
        </div>

        <div className="alerts-container">
          <h2>
            market data ({tickersArray.length}{" "}
            {filterText ? "filtered" : "total"})
          </h2>

          {/* Market overview summary */}
          {tickersArray.length > 0 && sortBy === "change" && !filterText && (
            <div className="market-overview">
              <span className="overview-item gainers">
                gainers:{" "}
                {
                  tickersArray.filter((t) => t.price_change_percent_24h > 0)
                    .length
                }
              </span>
              <span className="overview-item losers">
                losers:{" "}
                {
                  tickersArray.filter((t) => t.price_change_percent_24h < 0)
                    .length
                }
              </span>
              <span className="overview-item unchanged">
                unchanged:{" "}
                {
                  tickersArray.filter((t) => t.price_change_percent_24h === 0)
                    .length
                }
              </span>
            </div>
          )}

          {tickersArray.length === 0 &&
          Object.keys(cryptoTickers).length > 0 ? (
            <p className="no-alerts">no pairs match "{filterText}"</p>
          ) : tickersArray.length === 0 ? (
            <p className="no-alerts">loading...</p>
          ) : viewMode === "grid" ? (
            <div
              className={`alerts-grid ${
                tickersArray.length > 20 ? "dense-grid" : ""
              }`}
            >
              {tickersArray.map((ticker, index) => {
                // Add special classes for top gainers/losers when sorted by change
                let cardClass = "ticker-card";
                if (sortBy === "change") {
                  if (index < 3 && ticker.price_change_percent_24h > 5) {
                    cardClass += " top-gainer";
                  } else if (
                    index >= tickersArray.length - 3 &&
                    ticker.price_change_percent_24h < -5
                  ) {
                    cardClass += " top-loser";
                  }
                }

                return (
                  <div key={ticker.crypto} className={cardClass}>
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
                          {ticker.price_change_24h >= 0 ? "+" : "-"}$
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
                          <span className="label">bid:</span>
                          <span className="value">
                            ${formatPrice(ticker.bid)}
                          </span>
                        </div>
                        <div className="ticker-item">
                          <span className="label">ask:</span>
                          <span className="value">
                            ${formatPrice(ticker.ask)}
                          </span>
                        </div>
                      </div>

                      <div className="ticker-row">
                        <div className="ticker-item">
                          <span className="label">24h low:</span>
                          <span className="value">
                            ${formatPrice(ticker.low_24h)}
                          </span>
                        </div>
                        <div className="ticker-item">
                          <span className="label">24h high:</span>
                          <span className="value">
                            ${formatPrice(ticker.high_24h)}
                          </span>
                        </div>
                      </div>

                      <div className="ticker-item full-width">
                        <span className="label">24h volume:</span>
                        <span className="value">
                          {ticker.volume_24h?.toLocaleString()}{" "}
                          {ticker.crypto.split("-")[0]}
                        </span>
                      </div>
                    </div>

                    <div className="update-indicator"></div>
                  </div>
                );
              })}
            </div>
          ) : (
            <table className="ticker-list">
              <thead>
                <tr>
                  <th>pair</th>
                  <th>price</th>
                  <th>24h change</th>
                  <th>bid</th>
                  <th>ask</th>
                  <th>24h volume</th>
                </tr>
              </thead>
              <tbody>
                {tickersArray.map((ticker) => (
                  <tr key={ticker.crypto} className="ticker-row-item">
                    <td className="crypto-name">{ticker.crypto}</td>
                    <td>${formatPrice(ticker.price)}</td>
                    <td
                      className={
                        ticker.price_change_24h >= 0 ? "positive" : "negative"
                      }
                    >
                      {ticker.price_change_24h >= 0 ? "+" : ""}
                      {ticker.price_change_percent_24h.toFixed(2)}%
                    </td>
                    <td>${formatPrice(ticker.bid)}</td>
                    <td>${formatPrice(ticker.ask)}</td>
                    <td>
                      {ticker.volume_24h?.toLocaleString(undefined, {
                        maximumFractionDigits: 0,
                      })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </header>
    </div>
  );
}

export default App;
