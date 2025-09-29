import asyncio
import logging
from aiohttp import web
import socketio

logger = logging.getLogger(__name__)

class SpikeAlertSocketServer:
    """Direct Socket.IO server for receiving spike alerts from spike-detector bot"""

    def __init__(self, alert_callback):
        self.alert_callback = alert_callback
        self.connected_clients = set()

        # Create Socket.IO server
        self.sio = socketio.AsyncServer(
            async_mode='aiohttp',
            cors_allowed_origins='*',  # Allow connections from any origin
            logger=False,
            engineio_logger=False
        )

        # Create aiohttp application
        self.app = web.Application()
        self.sio.attach(self.app)

        # Register event handlers
        self.sio.on('connect', self._on_connect)
        self.sio.on('disconnect', self._on_disconnect)
        self.sio.on('spike_alert', self._on_spike_alert)
        self.sio.on('ping', self._on_ping)  # For connection testing

        self.stats = {
            'total_connections': 0,
            'total_alerts': 0,
            'last_alert_time': None,
            'last_alert_symbol': None
        }

    async def _on_connect(self, sid, environ):
        """Handle client connection"""
        self.connected_clients.add(sid)
        self.stats['total_connections'] += 1

        client_info = {
            'sid': sid,
            'remote_addr': environ.get('REMOTE_ADDR', 'unknown'),
            'user_agent': environ.get('HTTP_USER_AGENT', 'unknown')
        }

        logger.info(f"🔌 Spike detector connected: {sid} from {client_info['remote_addr']}")

        # Send acknowledgment
        await self.sio.emit('connection_ack', {
            'status': 'connected',
            'message': 'Connected to Telegram Bot direct socket server'
        }, to=sid)

    async def _on_disconnect(self, sid):
        """Handle client disconnection"""
        if sid in self.connected_clients:
            self.connected_clients.remove(sid)
        logger.info(f"🔌 Spike detector disconnected: {sid}")

    async def _on_spike_alert(self, sid, data):
        """Handle incoming spike alert"""
        try:
            symbol = data.get('symbol', 'unknown')
            event_type = data.get('event_type', 'spike')
            pct_change = data.get('pct_change', 0)

            # Update stats
            self.stats['total_alerts'] += 1
            self.stats['last_alert_time'] = data.get('timestamp')
            self.stats['last_alert_symbol'] = symbol

            logger.info(f"⚡ Direct spike alert received: {symbol} {event_type} ({pct_change:.2f}%)")

            # Call the alert callback (send to Telegram)
            await self.alert_callback(data)

            # Send acknowledgment back to spike detector
            await self.sio.emit('alert_received', {
                'status': 'success',
                'symbol': symbol,
                'event_type': event_type
            }, to=sid)

        except Exception as e:
            logger.error(f"Error handling spike alert: {e}")
            # Send error acknowledgment
            await self.sio.emit('alert_received', {
                'status': 'error',
                'message': str(e)
            }, to=sid)

    async def _on_ping(self, sid, data):
        """Handle ping for connection testing"""
        await self.sio.emit('pong', {
            'timestamp': data.get('timestamp'),
            'connected_clients': len(self.connected_clients),
            'stats': self.stats
        }, to=sid)

    async def start(self, port=8081):
        """Start the Socket.IO server"""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()

        logger.info(f"🚀 Direct Socket.IO server listening on port {port}")
        logger.info(f"📡 Ready to receive spike alerts from spike-detector")

        return runner

    def get_stats(self):
        """Get server statistics"""
        return {
            'connected_clients': len(self.connected_clients),
            'total_connections': self.stats['total_connections'],
            'total_alerts': self.stats['total_alerts'],
            'last_alert_time': self.stats['last_alert_time'],
            'last_alert_symbol': self.stats['last_alert_symbol']
        }