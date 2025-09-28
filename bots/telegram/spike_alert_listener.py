import asyncio
import logging
from typing import Optional, Callable
import socketio

logger = logging.getLogger(__name__)


class SpikeAlertListener:
    """Listens for spike alerts from backend via Socket.IO"""

    def __init__(self, backend_url: str, alert_callback: Callable):
        self.backend_url = backend_url
        self.alert_callback = alert_callback
        self.sio = socketio.AsyncClient(logger=False, engineio_logger=False)
        self.is_connected = False
        self.reconnect_task = None

        # Register event handlers
        self.sio.on('connect', self._on_connect)
        self.sio.on('disconnect', self._on_disconnect)
        self.sio.on('spike_alert', self._on_spike_alert)

    async def start(self):
        """Start listening for alerts"""
        try:
            await self._connect()
        except Exception as e:
            logger.error(f"Failed to start spike alert listener: {e}")
            # Schedule reconnection attempt
            self.reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def stop(self):
        """Stop listening and disconnect"""
        self.is_connected = False
        if self.reconnect_task:
            self.reconnect_task.cancel()
        if self.sio.connected:
            await self.sio.disconnect()
        logger.info("Spike alert listener stopped")

    async def _connect(self):
        """Connect to backend Socket.IO"""
        try:
            logger.info(f"Connecting to backend Socket.IO at {self.backend_url}")
            await self.sio.connect(
                self.backend_url,
                transports=['websocket', 'polling'],
                wait_timeout=10
            )
            logger.info("✅ Connected to backend for spike alerts")
            self.is_connected = True
        except Exception as e:
            logger.error(f"Failed to connect to backend: {e}")
            raise

    async def _reconnect_loop(self):
        """Automatically reconnect if disconnected"""
        while True:
            try:
                await asyncio.sleep(5)
                if not self.is_connected and not self.sio.connected:
                    logger.info("Attempting to reconnect to backend...")
                    await self._connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Reconnection attempt failed: {e}")

    async def _on_connect(self):
        """Handle Socket.IO connection"""
        self.is_connected = True
        logger.info("🔗 Socket.IO connected to backend")

    async def _on_disconnect(self):
        """Handle Socket.IO disconnection"""
        self.is_connected = False
        logger.warning("❌ Socket.IO disconnected from backend")
        # Start reconnection loop if not already running
        if not self.reconnect_task or self.reconnect_task.done():
            self.reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _on_spike_alert(self, data):
        """Handle incoming spike alert"""
        try:
            logger.info(f"📨 Received spike alert: {data.get('symbol', 'unknown')} - {data.get('event_type', 'unknown')}")

            # Call the callback function with the alert data
            if self.alert_callback:
                await self.alert_callback(data)
        except Exception as e:
            logger.error(f"Error handling spike alert: {e}")