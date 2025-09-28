import asyncio
import json
import logging
import os
import aiohttp
from typing import Dict, Optional, Callable, Set
from datetime import datetime, timedelta
from telegram import Bot

logger = logging.getLogger(__name__)


class PriceMonitor:
    """Monitors real-time prices via WebSocket and manages live price updates for active trades"""

    def __init__(self, bot: Bot, backend_url: Optional[str] = None):
        self.bot = bot
        self.backend_url = backend_url or os.getenv('BACKEND_URL', 'http://backend:5000')

        # WebSocket connection
        self.ws_session = None
        self.ws_connection = None
        self.is_connected = False

        # Monitoring state
        self.monitored_pairs: Set[str] = set()  # Product IDs being monitored
        self.price_callbacks: Dict[str, Callable] = {}  # product_id -> callback function
        self.last_prices: Dict[str, Dict] = {}  # product_id -> latest price data

        # Position monitoring
        self.active_positions: Dict[str, Dict] = {}  # product_id -> position info
        self.position_messages: Dict[str, Dict] = {}  # product_id -> {chat_id, message_id, entry_price}

        # Reconnection settings
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 5

        logger.info('Price Monitor initialized')

    async def start(self):
        """Start the price monitoring service"""
        try:
            await self._connect_websocket()
            asyncio.create_task(self._maintain_connection())
            logger.info('Price Monitor started successfully')
        except Exception as e:
            logger.error(f'Failed to start Price Monitor: {e}')

    async def stop(self):
        """Stop the price monitoring service"""
        self.is_connected = False
        if self.ws_connection:
            await self.ws_connection.close()
        if self.ws_session:
            await self.ws_session.close()
        logger.info('Price Monitor stopped')

    async def _connect_websocket(self):
        """Establish WebSocket connection to backend using HTTP polling for now"""
        try:
            # For now, use HTTP polling instead of WebSocket due to Socket.IO compatibility
            # This will be updated to proper Socket.IO client in future iteration
            self.is_connected = True
            self.reconnect_attempts = 0

            logger.info(f'Price monitor initialized (polling mode) - Backend: {self.backend_url}')

            # Start polling for price data instead of WebSocket
            asyncio.create_task(self._poll_for_prices())

        except Exception as e:
            logger.error(f'Failed to initialize price monitor: {e}')
            self.is_connected = False
            await self._schedule_reconnect()

    async def _poll_for_prices(self):
        """Poll backend for price updates instead of WebSocket"""
        while self.is_connected:
            try:
                # Poll for priority pairs if any are monitored
                if self.monitored_pairs:
                    for product_id in list(self.monitored_pairs):
                        try:
                            # Get current price from backend ticker endpoint
                            url = f"{self.backend_url}/tickers/{product_id}"
                            async with aiohttp.ClientSession() as session:
                                async with session.get(url) as response:
                                    if response.status == 200:
                                        ticker_data = await response.json()
                                        await self._process_price_update(product_id, ticker_data, is_priority=True)
                        except Exception as e:
                            logger.warning(f'Failed to get price for {product_id}: {e}')

                # Wait before next poll - faster for priority pairs
                await asyncio.sleep(2 if self.monitored_pairs else 10)

            except Exception as e:
                logger.error(f'Error in price polling: {e}')
                await asyncio.sleep(5)

    async def _listen_to_messages(self):
        """Placeholder for WebSocket messages - using polling instead"""
        pass

    async def _handle_ticker_update(self, ticker_data: Dict):
        """Handle regular ticker updates"""
        product_id = ticker_data.get('product_id')
        if product_id and product_id in self.monitored_pairs:
            await self._process_price_update(product_id, ticker_data)

    async def _handle_priority_ticker_update(self, ticker_data: Dict):
        """Handle priority ticker updates for active trades"""
        product_id = ticker_data.get('product_id')
        if product_id:
            await self._process_price_update(product_id, ticker_data, is_priority=True)

    async def _process_price_update(self, product_id: str, ticker_data: Dict, is_priority: bool = False):
        """Process price update and trigger callbacks"""
        try:
            # Update last known price
            self.last_prices[product_id] = {
                'price': float(ticker_data.get('price', 0)),
                'volume_24h': float(ticker_data.get('volume_24h', 0)),
                'low_24h': float(ticker_data.get('low_24h', 0)),
                'high_24h': float(ticker_data.get('high_24h', 0)),
                'timestamp': datetime.now(),
                'is_priority': is_priority
            }

            # Trigger callback if registered
            if product_id in self.price_callbacks:
                callback = self.price_callbacks[product_id]
                await callback(product_id, self.last_prices[product_id])

            # Update position messages if this is an active position
            if product_id in self.active_positions:
                await self._update_position_message(product_id, self.last_prices[product_id])

        except Exception as e:
            logger.error(f'Error processing price update for {product_id}: {e}')

    async def _maintain_connection(self):
        """Maintain WebSocket connection with automatic reconnection"""
        while True:
            await asyncio.sleep(30)  # Check every 30 seconds

            if not self.is_connected and self.reconnect_attempts < self.max_reconnect_attempts:
                logger.info('WebSocket disconnected, attempting to reconnect...')
                await self._connect_websocket()

            # Send ping to keep connection alive
            if self.is_connected and self.ws_connection:
                try:
                    await self.ws_connection.send_str('2')  # Socket.io ping
                except Exception as e:
                    logger.error(f'Failed to send ping: {e}')
                    self.is_connected = False

    async def _schedule_reconnect(self):
        """Schedule a reconnection attempt"""
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            delay = min(self.reconnect_delay * self.reconnect_attempts, 60)
            logger.info(f'Scheduling reconnect attempt {self.reconnect_attempts} in {delay}s')
            await asyncio.sleep(delay)

    async def add_priority_pair(self, product_id: str):
        """Add a trading pair to priority monitoring"""
        try:
            # Tell backend to prioritize this pair
            url = f"{self.backend_url}/priority-pairs"
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={'product_id': product_id, 'action': 'add'}) as response:
                    if response.status == 200:
                        logger.info(f'Added {product_id} to priority monitoring')
                        return True
                    else:
                        logger.error(f'Failed to add priority pair: {response.status}')
                        return False
        except Exception as e:
            logger.error(f'Error adding priority pair {product_id}: {e}')
            return False

    async def remove_priority_pair(self, product_id: str):
        """Remove a trading pair from priority monitoring"""
        try:
            # Tell backend to remove priority for this pair
            url = f"{self.backend_url}/priority-pairs"
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={'product_id': product_id, 'action': 'remove'}) as response:
                    if response.status == 200:
                        logger.info(f'Removed {product_id} from priority monitoring')
                        return True
                    else:
                        logger.error(f'Failed to remove priority pair: {response.status}')
                        return False
        except Exception as e:
            logger.error(f'Error removing priority pair {product_id}: {e}')
            return False

    async def start_position_monitoring(self, product_id: str, chat_id: str, entry_price: float,
                                      entry_amount: float, message_id: Optional[int] = None):
        """Start monitoring a position with live price updates"""
        try:
            # Add to priority monitoring
            await self.add_priority_pair(product_id)

            # Store position info
            self.active_positions[product_id] = {
                'entry_price': entry_price,
                'entry_amount': entry_amount,
                'chat_id': chat_id,
                'start_time': datetime.now()
            }

            # Store message info for live updates
            if message_id:
                self.position_messages[product_id] = {
                    'chat_id': chat_id,
                    'message_id': message_id,
                    'entry_price': entry_price,
                    'entry_amount': entry_amount
                }

            # Add to monitored pairs
            self.monitored_pairs.add(product_id)

            logger.info(f'Started position monitoring for {product_id} at ${entry_price}')
            return True

        except Exception as e:
            logger.error(f'Error starting position monitoring for {product_id}: {e}')
            return False

    async def stop_position_monitoring(self, product_id: str):
        """Stop monitoring a position"""
        try:
            # Remove from priority monitoring
            await self.remove_priority_pair(product_id)

            # Clean up tracking data
            if product_id in self.active_positions:
                del self.active_positions[product_id]

            if product_id in self.position_messages:
                del self.position_messages[product_id]

            if product_id in self.monitored_pairs:
                self.monitored_pairs.remove(product_id)

            if product_id in self.price_callbacks:
                del self.price_callbacks[product_id]

            logger.info(f'Stopped position monitoring for {product_id}')
            return True

        except Exception as e:
            logger.error(f'Error stopping position monitoring for {product_id}: {e}')
            return False

    async def _update_position_message(self, product_id: str, price_data: Dict):
        """Update live position message with current price and P&L"""
        try:
            if product_id not in self.position_messages:
                return

            msg_info = self.position_messages[product_id]
            position_info = self.active_positions[product_id]

            current_price = price_data['price']
            entry_price = position_info['entry_price']
            entry_amount = position_info['entry_amount']

            # Calculate P&L
            pnl_percentage = ((current_price - entry_price) / entry_price) * 100
            current_value = entry_amount * (current_price / entry_price)
            pnl_usd = current_value - entry_amount

            # Color indicator
            if pnl_percentage > 0:
                indicator = "🟢"
            elif pnl_percentage < -5:
                indicator = "🔴"
            else:
                indicator = "🟡"

            # Format message
            currency = product_id.split('-')[0]
            message = (
                f"{indicator} *Live Position: {product_id}*\n\n"
                f"💰 *Entry:* ${entry_price:,.6f}\n"
                f"📈 *Current:* ${current_price:,.6f}\n"
                f"📊 *P&L:* {pnl_percentage:+.2f}% (${pnl_usd:+,.2f})\n"
                f"💵 *Value:* ${current_value:,.2f}\n"
                f"🕐 *Updated:* {datetime.now().strftime('%H:%M:%S')}"
            )

            # Update message
            await self.bot.edit_message_text(
                chat_id=msg_info['chat_id'],
                message_id=msg_info['message_id'],
                text=message,
                parse_mode='Markdown',
                reply_markup=self._get_position_keyboard(product_id)
            )

        except Exception as e:
            # If message can't be updated (deleted, etc.), stop monitoring
            logger.warning(f'Failed to update position message for {product_id}: {e}')
            await self.stop_position_monitoring(product_id)

    def _get_position_keyboard(self, product_id: str):
        """Get inline keyboard for position management"""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton("🔥 Market Sell", callback_data=f"market_sell:{product_id}"),
                InlineKeyboardButton("📉 Limit Sell", callback_data=f"limit_sell:{product_id}")
            ],
            [
                InlineKeyboardButton("❌ Stop Monitoring", callback_data=f"stop_monitor:{product_id}"),
                InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_position:{product_id}")
            ]
        ]

        return InlineKeyboardMarkup(keyboard)

    async def register_price_callback(self, product_id: str, callback: Callable):
        """Register a callback function for price updates"""
        self.price_callbacks[product_id] = callback
        self.monitored_pairs.add(product_id)
        logger.info(f'Registered price callback for {product_id}')

    async def get_current_price(self, product_id: str) -> Optional[Dict]:
        """Get the most recent price data for a product"""
        return self.last_prices.get(product_id)

    async def get_active_positions(self) -> Dict[str, Dict]:
        """Get all active positions being monitored"""
        return self.active_positions.copy()

    def format_price_update(self, product_id: str, price_data: Dict) -> str:
        """Format price data for display"""
        price = price_data['price']
        timestamp = price_data['timestamp'].strftime('%H:%M:%S')
        is_priority = price_data.get('is_priority', False)

        priority_indicator = "⚡" if is_priority else "📊"

        return (
            f"{priority_indicator} *{product_id}*\n"
            f"💰 ${price:,.6f}\n"
            f"🕐 {timestamp}"
        )