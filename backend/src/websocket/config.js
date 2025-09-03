// WebSocket configuration
module.exports = {
  // Coinbase WebSocket URL
  WS_URL: process.env.WS_URL || "wss://ws-feed.exchange.coinbase.com",

  // Cryptocurrencies to monitor
  CRYPTOS: (process.env.MONITORING_CRYPTOS || "BTC-USD,ETH-USD").split(","),

  // Volume surge threshold multiplier
  THRESHOLD: parseFloat(process.env.VOLUME_THRESHOLD || "1.5"),

  // Time window for volume calculation (in minutes)
  WINDOW_MINUTES: parseInt(process.env.WINDOW_MINUTES || "5"),

  // Reconnection settings
  RECONNECT_DELAY: parseInt(process.env.RECONNECT_DELAY || "1000"), // ms
  MAX_RECONNECT_DELAY: parseInt(process.env.MAX_RECONNECT_DELAY || "60000"), // ms

  // Historical data settings
  HISTORICAL_HOURS: parseInt(process.env.HISTORICAL_HOURS || "1"),
};
