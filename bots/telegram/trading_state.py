"""
Trading State Controller
Manages trading state and coordinates WebSocket feed switching for live positions
"""
import logging
import aiohttp
from typing import List, Optional

logger = logging.getLogger(__name__)


class TradingStateController:
    """
    Manages trading state and WebSocket feed priority based on active positions
    """

    def __init__(self, backend_url: str = "http://backend:5000"):
        self.backend_url = backend_url
        self.active_product_ids: List[str] = []
        self.trading_state = "idle"  # idle, active, multi_active

    async def set_trading_state(self, product_ids: List[str]):
        """
        Set trading state based on active positions

        Args:
            product_ids: List of product IDs with active positions
        """
        self.active_product_ids = product_ids

        if len(product_ids) == 0:
            self.trading_state = "idle"
            await self.switch_to_all_pairs_feed()
        elif len(product_ids) == 1:
            self.trading_state = "active"
            await self.switch_to_priority_feed(product_ids)
        else:
            self.trading_state = "multi_active"
            await self.switch_to_priority_feed(product_ids)

        logger.info(f"Trading state: {self.trading_state}, Active positions: {len(product_ids)}")

    async def add_position(self, product_id: str):
        """
        Add a new active position

        Args:
            product_id: Product ID to add to priority monitoring
        """
        if product_id not in self.active_product_ids:
            self.active_product_ids.append(product_id)
            await self.set_trading_state(self.active_product_ids)

    async def remove_position(self, product_id: str):
        """
        Remove a closed position

        Args:
            product_id: Product ID to remove from priority monitoring
        """
        if product_id in self.active_product_ids:
            self.active_product_ids.remove(product_id)
            await self.set_trading_state(self.active_product_ids)

    async def switch_to_priority_feed(self, product_ids: List[str]):
        """
        Switch WebSocket feed to only monitor specific product IDs

        Args:
            product_ids: List of product IDs to monitor exclusively
        """
        try:
            url = f"{self.backend_url}/priority-pairs"
            async with aiohttp.ClientSession() as session:
                async with session.put(url, json={'product_ids': product_ids}) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"✅ Switched to priority feed: {product_ids}")
                        return result
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to set priority feed: {error_text}")
                        return None
        except Exception as e:
            logger.error(f"Error switching to priority feed: {e}")
            return None

    async def switch_to_all_pairs_feed(self):
        """
        Switch WebSocket feed back to monitoring all pairs (idle state)
        """
        try:
            url = f"{self.backend_url}/priority-pairs"
            async with aiohttp.ClientSession() as session:
                async with session.delete(url) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info("✅ Switched to all-pairs feed (idle state)")
                        return result
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to clear priority feed: {error_text}")
                        return None
        except Exception as e:
            logger.error(f"Error switching to all-pairs feed: {e}")
            return None

    def get_state(self) -> dict:
        """Get current trading state"""
        return {
            'trading_state': self.trading_state,
            'active_positions': len(self.active_product_ids),
            'product_ids': self.active_product_ids
        }

    def is_idle(self) -> bool:
        """Check if in idle state"""
        return self.trading_state == "idle"

    def is_active(self) -> bool:
        """Check if actively trading"""
        return self.trading_state in ['active', 'multi_active']
