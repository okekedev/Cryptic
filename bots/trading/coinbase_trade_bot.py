import os
import time
import logging
from typing import Dict, Optional, List
from coinbase.rest import RESTClient
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CoinbaseTradeBot:

    def __init__(self, api_key: Optional[str] = None, signing_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('COINBASE_API_KEY')
        self.signing_key = signing_key or os.getenv('COINBASE_SIGNING_KEY')

        if not self.api_key or not self.signing_key:
            raise ValueError('COINBASE_API_KEY and COINBASE_SIGNING_KEY must be provided')

        self.client = RESTClient(api_key=self.api_key, api_secret=self.signing_key)
        logger.info('Coinbase Trade Bot initialized successfully')

    def get_accounts(self) -> Dict:
        try:
            response = self.client.get_accounts()
            accounts = response.accounts if hasattr(response, 'accounts') else []
            logger.info(f'Retrieved {len(accounts)} accounts')
            return {'accounts': accounts, 'has_next': getattr(response, 'has_next', False)}
        except Exception as e:
            logger.error(f'Error getting accounts: {e}')
            raise

    def get_account_balance(self, currency: str) -> Dict:
        try:
            accounts_response = self.get_accounts()
            for account in accounts_response.get('accounts', []):
                if hasattr(account, 'currency') and account.currency == currency:
                    balance_obj = getattr(account, 'available_balance', None)
                    balance = float(balance_obj.value) if balance_obj and hasattr(balance_obj, 'value') else 0.0
                    logger.info(f'{currency} balance: {balance}')
                    return {
                        'currency': currency,
                        'balance': balance,
                        'uuid': getattr(account, 'uuid', None)
                    }
            logger.warning(f'No account found for currency: {currency}')
            return {'currency': currency, 'balance': 0.0, 'uuid': None}
        except Exception as e:
            logger.error(f'Error getting balance for {currency}: {e}')
            raise

    def create_market_order(self, product_id: str, side: str, size: Optional[str] = None,
                           quote_size: Optional[str] = None, client_order_id: Optional[str] = None) -> Dict:
        try:
            if side.upper() not in ['BUY', 'SELL']:
                raise ValueError('Side must be BUY or SELL')

            logger.info(f'Creating market {side.lower()} order for {product_id}')

            if side.upper() == 'BUY':
                if not quote_size:
                    raise ValueError('quote_size is required for market buy orders')
                order = self.client.market_order_buy(
                    client_order_id=client_order_id or f'buy_{int(time.time())}',
                    product_id=product_id,
                    quote_size=quote_size
                )
            else:
                if not size:
                    raise ValueError('size is required for market sell orders')
                order = self.client.market_order_sell(
                    client_order_id=client_order_id or f'sell_{int(time.time())}',
                    product_id=product_id,
                    base_size=size
                )

            order_id = getattr(order, 'order_id', 'unknown')
            logger.info(f'Market order created successfully: {order_id}')
            return order
        except Exception as e:
            logger.error(f'Error creating market order: {e}')
            raise

    def create_limit_order(self, product_id: str, side: str, base_size: str,
                          limit_price: str, post_only: bool = False,
                          client_order_id: Optional[str] = None) -> Dict:
        try:
            if side.upper() not in ['BUY', 'SELL']:
                raise ValueError('Side must be BUY or SELL')

            logger.info(f'Creating limit {side.lower()} order for {product_id} at {limit_price}')

            if side.upper() == 'BUY':
                order = self.client.limit_order_gtc_buy(
                    client_order_id=client_order_id or f'limit_buy_{int(time.time())}',
                    product_id=product_id,
                    base_size=base_size,
                    limit_price=limit_price,
                    post_only=post_only
                )
            else:
                order = self.client.limit_order_gtc_sell(
                    client_order_id=client_order_id or f'limit_sell_{int(time.time())}',
                    product_id=product_id,
                    base_size=base_size,
                    limit_price=limit_price,
                    post_only=post_only
                )

            order_id = getattr(order, 'order_id', 'unknown')
            logger.info(f'Limit order created successfully: {order_id}')
            return order
        except Exception as e:
            logger.error(f'Error creating limit order: {e}')
            raise

    def cancel_orders(self, order_ids: List[str]) -> Dict:
        try:
            if not order_ids:
                raise ValueError('order_ids list cannot be empty')

            if len(order_ids) > 100:
                raise ValueError('Maximum 100 orders can be cancelled at once')

            logger.info(f'Cancelling {len(order_ids)} order(s)')
            result = self.client.cancel_orders(order_ids=order_ids)

            results = getattr(result, 'results', [])
            success_count = len([r for r in results if getattr(r, 'success', False)])
            logger.info(f'Successfully cancelled {success_count}/{len(order_ids)} orders')

            return result
        except Exception as e:
            logger.error(f'Error cancelling orders: {e}')
            raise

    def edit_order(self, order_id: str, price: Optional[str] = None,
                   size: Optional[str] = None) -> Dict:
        try:
            if not price and not size:
                raise ValueError('Either price or size must be provided')

            logger.info(f'Editing order {order_id}')
            result = self.client.edit_order(order_id=order_id, price=price, size=size)

            if getattr(result, 'success', False):
                logger.info(f'Order edited successfully')
            else:
                logger.warning(f'Order edit may have failed: {result}')

            return result
        except Exception as e:
            logger.error(f'Error editing order: {e}')
            raise

    def get_order(self, order_id: str) -> Dict:
        try:
            order = self.client.get_order(order_id=order_id)
            logger.info(f'Retrieved order {order_id}')
            return order
        except Exception as e:
            logger.error(f'Error getting order {order_id}: {e}')
            raise

    def list_orders(self, product_id: Optional[str] = None,
                   order_status: Optional[List[str]] = None, limit: int = 100) -> Dict:
        try:
            orders = self.client.list_orders(
                product_id=product_id,
                order_status=order_status,
                limit=limit
            )
            order_list = getattr(orders, 'orders', [])
            order_count = len(order_list)
            logger.info(f'Retrieved {order_count} orders')
            return orders
        except Exception as e:
            logger.error(f'Error listing orders: {e}')
            raise

    def get_product(self, product_id: str) -> Dict:
        try:
            product = self.client.get_product(product_id=product_id)
            logger.info(f'Retrieved product info for {product_id}')
            return product
        except Exception as e:
            logger.error(f'Error getting product {product_id}: {e}')
            raise


def main():
    logger.info('=== Coinbase Trading Bot Demo ===')

    try:
        bot = CoinbaseTradeBot()
        logger.info('Bot initialized successfully')

        logger.info('\n--- Getting Account Balances ---')
        accounts = bot.get_accounts()
        for account in accounts.get('accounts', [])[:5]:
            currency = getattr(account, 'currency', 'unknown')
            balance_obj = getattr(account, 'available_balance', None)
            balance_value = getattr(balance_obj, 'value', '0') if balance_obj else '0'
            if float(balance_value) > 0:
                logger.info(f'{currency}: {balance_value}')

        logger.info('\n--- Getting BTC-USD Product Info ---')
        product = bot.get_product('BTC-USD')
        logger.info(f'Product: {getattr(product, "product_id", "unknown")}')
        logger.info(f'Base Currency: {getattr(product, "base_currency_id", "unknown")}')
        logger.info(f'Quote Currency: {getattr(product, "quote_currency_id", "unknown")}')
        logger.info(f'Status: {getattr(product, "status", "unknown")}')

        logger.info('\n--- Listing Recent Orders ---')
        orders = bot.list_orders(limit=5)
        order_list = getattr(orders, 'orders', [])
        logger.info(f'Found {len(order_list)} recent orders')
        for order in order_list[:3]:
            order_id = getattr(order, 'order_id', 'unknown')
            side = getattr(order, 'side', 'unknown')
            product_id = getattr(order, 'product_id', 'unknown')
            status = getattr(order, 'status', 'unknown')
            logger.info(f'  Order {order_id}: {side} {product_id} - {status}')

        logger.info('\n=== Demo Complete ===')
        logger.info('Bot is ready to execute trades via API calls')
        logger.info('Available methods:')
        logger.info('  - create_market_order(product_id, side, size/quote_size)')
        logger.info('  - create_limit_order(product_id, side, base_size, limit_price)')
        logger.info('  - cancel_orders(order_ids)')
        logger.info('  - edit_order(order_id, price, size)')
        logger.info('  - get_order(order_id)')
        logger.info('  - list_orders()')

    except Exception as e:
        logger.error(f'Error in main: {e}')
        raise


if __name__ == '__main__':
    main()