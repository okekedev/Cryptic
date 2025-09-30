"""
Background service to continuously update position cards with live prices from backend
"""
import asyncio
import aiohttp
import logging
from typing import Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BACKEND_URL = "http://backend:5000"
TELEGRAM_WEBHOOK_URL = "http://localhost:8080"

async def fetch_current_prices(session: aiohttp.ClientSession) -> Dict[str, float]:
    """Fetch current prices from backend WebSocket data"""
    try:
        async with session.get(f"{BACKEND_URL}/api/prices") as response:
            if response.status == 200:
                data = await response.json()
                prices = {}
                for ticker in data:
                    prices[ticker['crypto']] = ticker['price']
                return prices
    except Exception as e:
        logger.error(f"Error fetching prices: {e}")
    return {}

async def update_position_card(session: aiohttp.ClientSession, product_id: str, price: float):
    """Send position update to telegram bot"""
    try:
        async with session.post(
            f"{TELEGRAM_WEBHOOK_URL}/position-updated",
            json={"product_id": product_id, "current_price": price}
        ) as response:
            if response.status == 200:
                logger.debug(f"Updated {product_id}: ${price:,.2f}")
    except Exception as e:
        logger.error(f"Error updating position card: {e}")

async def main():
    """Continuously update active position cards with live prices"""
    logger.info("🔄 Live Position Updater started")

    # Track which positions are active (product_id -> last_price)
    active_positions = {
        "ETH-USD": None
    }

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # Fetch current prices
                prices = await fetch_current_prices(session)

                # Update each active position
                for product_id in list(active_positions.keys()):
                    if product_id in prices:
                        current_price = prices[product_id]
                        last_price = active_positions[product_id]

                        # Always update (rate limiting is handled by telegram bot)
                        await update_position_card(session, product_id, current_price)
                        active_positions[product_id] = current_price

                # Wait 3 seconds between updates (bot rate limits to 5s internally)
                await asyncio.sleep(3)

            except Exception as e:
                logger.error(f"Error in update loop: {e}")
                await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
