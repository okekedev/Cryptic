#!/usr/bin/env python3
"""
Simplified Trading Bot Service
Just runs the dump trading bot with WebSocket price feeds - simple P&L alerts only
"""

import asyncio
import os
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from dotenv import load_dotenv
import socketio

# Import bot modules
from websocket_handler import EnhancedWebSocketHandler
from drop_detector import DropDetectorBot
from dump_trader import DumpTradingBot

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Global state
websocket_handler = None
drop_detector = None
dump_trader = None

# Create Socket.IO server for internal communication
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*',
    logger=False,
    engineio_logger=False
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup and shutdown lifecycle for the trading service
    """
    global websocket_handler, drop_detector, dump_trader

    logger.info("🚀 Starting Trading Bot Service...")

    try:
        # 1. Initialize WebSocket handler for Coinbase streaming
        logger.info("Initializing WebSocket handler...")
        ws_config = {
            'wsUrl': os.getenv('WS_URL', 'wss://advanced-trade-ws.coinbase.com'),
            'cryptoConfig': None,  # Will fetch all USD pairs
            'volumeThreshold': float(os.getenv('VOLUME_THRESHOLD', '1.5')),
            'windowMinutes': int(os.getenv('WINDOW_MINUTES', '5')),
            'productsPerConnection': 15,
            'apiKey': os.getenv('COINBASE_API_KEY'),
            'signingKey': os.getenv('COINBASE_SIGNING_KEY')
        }

        websocket_handler = EnhancedWebSocketHandler(ws_config)
        await websocket_handler.initialize()

        # Get the main event loop
        main_loop = asyncio.get_event_loop()

        # Set up ticker broadcast callback
        def broadcast_ticker(ticker_data):
            """Broadcast ticker updates to connected Socket.IO clients"""
            try:
                # Schedule the coroutine on the main event loop from this thread
                asyncio.run_coroutine_threadsafe(
                    sio.emit('ticker_update', ticker_data),
                    main_loop
                )
            except Exception as e:
                logger.debug(f"Error broadcasting ticker: {e}")

        # Register broadcast as event listener
        websocket_handler.on('ticker_update', broadcast_ticker)

        logger.info("✅ WebSocket handler initialized with Socket.IO broadcasting")

        # 2. Initialize Drop Detector
        logger.info("Initializing Drop Detector...")
        drop_detector = DropDetectorBot()
        # Start drop detector in background (runs synchronously in thread)
        asyncio.create_task(asyncio.to_thread(drop_detector.run))
        logger.info("✅ Drop Detector started")

        # 3. Initialize Dump Trading Bot
        auto_trade = os.getenv('AUTO_TRADE', 'no').lower() in ['yes', 'true', '1']
        logger.info(f"Initializing Dump Trading Bot (AUTO_TRADE: {auto_trade})...")
        dump_trader = DumpTradingBot()
        # Start dump trader in background
        asyncio.create_task(asyncio.to_thread(dump_trader.run))
        logger.info("✅ Dump Trading Bot started")

        logger.info("🎉 All services started successfully!")
        logger.info("📱 Simple P&L alerts will be sent to Telegram when trades complete")

        yield  # Server is running

    except Exception as e:
        logger.error(f"❌ Error during startup: {e}", exc_info=True)
        raise

    finally:
        # Shutdown
        logger.info("🛑 Shutting down services...")

        if websocket_handler:
            websocket_handler.disconnect()

        if drop_detector:
            drop_detector.stop()

        if dump_trader:
            dump_trader.stop()

        logger.info("✅ Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Simple Trading Bot Service",
    description="Automated crypto trading with simple P&L alerts",
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Socket.IO app
socket_app = socketio.ASGIApp(sio, app)


# Socket.IO event handlers
@sio.event
async def connect(sid, environ):
    """Handle client connection"""
    logger.info(f"Socket.IO client connected: {sid}")


@sio.event
async def disconnect(sid):
    """Handle client disconnection"""
    logger.info(f"Socket.IO client disconnected: {sid}")


@sio.event
async def spike_alert(sid, data):
    """Forward spike alerts to all clients (for dump trader to receive from drop detector)"""
    await sio.emit('spike_alert', data, skip_sid=sid)


# REST API Endpoints
@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "running",
        "service": "Simple Trading Bot",
        "version": "2.0.0"
    }


@app.get("/health")
async def health():
    """Detailed health check"""
    health_status = {
        "websocket_handler": "running" if websocket_handler else "not_initialized",
        "drop_detector": "running" if drop_detector else "not_initialized",
        "dump_trader": "running" if dump_trader else "not_initialized"
    }

    if websocket_handler:
        health_status["websocket_details"] = websocket_handler.getHealthSummary()

    if dump_trader:
        stats = dump_trader.get_statistics()
        health_status["trading_stats"] = {
            "total_trades": stats.get("total_trades", 0),
            "win_rate": stats.get("win_rate", 0),
            "total_pnl": stats.get("total_pnl", 0),
            "current_capital": stats.get("current_capital", 0)
        }

    return health_status


@app.get("/tickers")
async def get_all_tickers():
    """Get all current ticker data"""
    if not websocket_handler:
        return {"error": "WebSocket handler not initialized"}

    return websocket_handler.getAllTickers()


@app.get("/tickers/{symbol}")
async def get_ticker(symbol: str):
    """Get ticker data for a specific symbol"""
    if not websocket_handler:
        return {"error": "WebSocket handler not initialized"}

    ticker = websocket_handler.getCurrentTicker(symbol)
    if not ticker:
        return {"error": f"Ticker {symbol} not found"}

    return ticker


@app.get("/stats")
async def get_trading_stats():
    """Get trading statistics"""
    if not dump_trader:
        return {"error": "Dump trader not initialized"}

    return dump_trader.get_statistics()


@app.get("/api/historical/{symbol}")
async def get_historical_data(symbol: str, hours: int = 24):
    """
    Get historical price data for market conditions analysis

    Returns candle data with: timestamp, open, high, low, close, volume
    """
    try:
        import requests
        from datetime import datetime, timedelta

        # Fetch historical candles from Coinbase API
        # Calculate time range
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=hours)

        # Determine granularity based on hours requested
        # Granularity in seconds: 60, 300, 900, 3600, 21600, 86400
        if hours <= 2:
            granularity = 60  # 1 minute candles
        elif hours <= 6:
            granularity = 300  # 5 minute candles
        elif hours <= 24:
            granularity = 900  # 15 minute candles
        else:
            granularity = 3600  # 1 hour candles

        # Coinbase API endpoint
        url = f"https://api.exchange.coinbase.com/products/{symbol}/candles"
        params = {
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "granularity": granularity
        }

        response = requests.get(url, params=params, timeout=10)

        if response.status_code != 200:
            logger.warning(f"Failed to fetch historical data for {symbol}: {response.status_code}")
            return {"error": f"Failed to fetch data: {response.status_code}"}

        candles_raw = response.json()

        # Coinbase returns: [timestamp, low, high, open, close, volume]
        # We need to convert to: {timestamp, open, high, low, close, volume}
        candles = []
        for candle in candles_raw:
            if len(candle) >= 6:
                candles.append({
                    "timestamp": candle[0],
                    "low": float(candle[1]),
                    "high": float(candle[2]),
                    "open": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[5])
                })

        # Sort by timestamp ascending (oldest first)
        candles.sort(key=lambda x: x["timestamp"])

        logger.info(f"Returned {len(candles)} historical candles for {symbol} ({hours}h)")
        return candles

    except Exception as e:
        logger.error(f"Error fetching historical data: {e}")
        return {"error": str(e)}


def main():
    """Main entry point"""
    # Configuration
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))

    logger.info(f"Starting server on {host}:{port}")

    # Run the server with Socket.IO support
    uvicorn.run(
        "main:socket_app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )


if __name__ == "__main__":
    main()
