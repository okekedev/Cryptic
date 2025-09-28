import os
import logging
import asyncio
from typing import Dict, Optional, List, Tuple
from decimal import Decimal, ROUND_DOWN
from coinbase.rest import RESTClient
from datetime import datetime

logger = logging.getLogger(__name__)


class TradingManager:
    """Manages Coinbase Advanced Trade API interactions for Telegram bot"""

    def __init__(self):
        self.api_key = os.getenv('COINBASE_API_KEY')
        self.signing_key = os.getenv('COINBASE_SIGNING_KEY')

        if not self.api_key or not self.signing_key:
            raise ValueError('COINBASE_API_KEY and COINBASE_SIGNING_KEY must be set')

        self.client = RESTClient(api_key=self.api_key, api_secret=self.signing_key)
        self.active_positions = {}  # product_id -> position_info
        self.pending_orders = {}    # order_id -> order_info

        # Trading settings
        self.default_position_percentage = float(os.getenv('DEFAULT_POSITION_PERCENTAGE', '2.0'))  # 2% of balance
        self.max_position_percentage = float(os.getenv('MAX_POSITION_PERCENTAGE', '10.0'))        # 10% max
        self.min_order_usd = float(os.getenv('MIN_ORDER_USD', '10.0'))                           # $10 minimum

        logger.info(f'Trading Manager initialized - Default position: {self.default_position_percentage}%')

    async def get_account_balance(self, currency: str = 'USD') -> Dict:
        """Get account balance for specified currency"""
        try:
            response = self.client.get_accounts()
            accounts = response.accounts if hasattr(response, 'accounts') else []

            for account in accounts:
                if hasattr(account, 'currency') and account.currency == currency:
                    balance_obj = getattr(account, 'available_balance', None)
                    balance = float(balance_obj.value) if balance_obj and hasattr(balance_obj, 'value') else 0.0

                    return {
                        'currency': currency,
                        'balance': balance,
                        'uuid': getattr(account, 'uuid', None),
                        'formatted': f"${balance:,.2f}" if currency == 'USD' else f"{balance:.8f} {currency}"
                    }

            return {'currency': currency, 'balance': 0.0, 'uuid': None, 'formatted': f"0.00 {currency}"}

        except Exception as e:
            logger.error(f'Error getting {currency} balance: {e}')
            return {'currency': currency, 'balance': 0.0, 'uuid': None, 'formatted': f"Error", 'error': str(e)}

    async def get_product_info(self, product_id: str) -> Dict:
        """Get product information and current price"""
        try:
            product = self.client.get_product(product_id=product_id)

            # Get current price from product ticker if available
            try:
                ticker_response = self.client.get_product_ticker(product_id=product_id)
                current_price = float(getattr(ticker_response, 'price', 0))
            except:
                current_price = 0.0

            return {
                'product_id': getattr(product, 'product_id', product_id),
                'base_currency': getattr(product, 'base_currency_id', ''),
                'quote_currency': getattr(product, 'quote_currency_id', ''),
                'status': getattr(product, 'status', 'unknown'),
                'current_price': current_price,
                'min_market_funds': getattr(product, 'min_market_funds', '0'),
                'max_market_funds': getattr(product, 'max_market_funds', '0'),
                'formatted_price': f"${current_price:,.6f}" if current_price > 0 else "N/A"
            }

        except Exception as e:
            logger.error(f'Error getting product info for {product_id}: {e}')
            return {'product_id': product_id, 'error': str(e)}

    def calculate_position_size(self, available_balance: float, percentage: Optional[float] = None) -> Dict:
        """Calculate position size based on available balance and percentage"""
        try:
            position_pct = percentage or self.default_position_percentage

            # Ensure percentage is within limits
            position_pct = max(0.1, min(position_pct, self.max_position_percentage))

            # Calculate position size
            position_size = available_balance * (position_pct / 100.0)

            # Check minimum order size
            if position_size < self.min_order_usd:
                return {
                    'valid': False,
                    'error': f'Position size ${position_size:.2f} is below minimum ${self.min_order_usd}',
                    'percentage': position_pct,
                    'position_size': position_size
                }

            return {
                'valid': True,
                'percentage': position_pct,
                'position_size': position_size,
                'formatted_size': f"${position_size:,.2f}",
                'remaining_balance': available_balance - position_size
            }

        except Exception as e:
            logger.error(f'Error calculating position size: {e}')
            return {'valid': False, 'error': str(e)}

    async def create_market_buy_order(self, product_id: str, quote_size: str,
                                    position_percentage: Optional[float] = None) -> Dict:
        """Create a market buy order"""
        try:
            client_order_id = f"tg_buy_{product_id}_{int(datetime.now().timestamp())}"

            order = self.client.market_order_buy(
                client_order_id=client_order_id,
                product_id=product_id,
                quote_size=quote_size
            )

            order_id = getattr(order, 'order_id', 'unknown')

            # Track the order
            self.pending_orders[order_id] = {
                'order_id': order_id,
                'client_order_id': client_order_id,
                'product_id': product_id,
                'side': 'BUY',
                'type': 'market',
                'quote_size': float(quote_size),
                'position_percentage': position_percentage or self.default_position_percentage,
                'created_at': datetime.now(),
                'status': 'pending'
            }

            logger.info(f'Market buy order created: {order_id} for {quote_size} USD of {product_id}')

            return {
                'success': True,
                'order_id': order_id,
                'client_order_id': client_order_id,
                'product_id': product_id,
                'quote_size': float(quote_size),
                'type': 'market_buy',
                'message': f'Market buy order placed for ${quote_size} of {product_id}'
            }

        except Exception as e:
            logger.error(f'Error creating market buy order: {e}')
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to place buy order: {str(e)}'
            }

    async def create_market_sell_order(self, product_id: str, base_size: str) -> Dict:
        """Create a market sell order"""
        try:
            client_order_id = f"tg_sell_{product_id}_{int(datetime.now().timestamp())}"

            order = self.client.market_order_sell(
                client_order_id=client_order_id,
                product_id=product_id,
                base_size=base_size
            )

            order_id = getattr(order, 'order_id', 'unknown')

            # Track the order
            self.pending_orders[order_id] = {
                'order_id': order_id,
                'client_order_id': client_order_id,
                'product_id': product_id,
                'side': 'SELL',
                'type': 'market',
                'base_size': float(base_size),
                'created_at': datetime.now(),
                'status': 'pending'
            }

            logger.info(f'Market sell order created: {order_id} for {base_size} of {product_id}')

            return {
                'success': True,
                'order_id': order_id,
                'client_order_id': client_order_id,
                'product_id': product_id,
                'base_size': float(base_size),
                'type': 'market_sell',
                'message': f'Market sell order placed for {base_size} {product_id.split("-")[0]}'
            }

        except Exception as e:
            logger.error(f'Error creating market sell order: {e}')
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to place sell order: {str(e)}'
            }

    async def create_limit_sell_order(self, product_id: str, base_size: str,
                                    limit_price: str, post_only: bool = True) -> Dict:
        """Create a limit sell order"""
        try:
            client_order_id = f"tg_limit_sell_{product_id}_{int(datetime.now().timestamp())}"

            order = self.client.limit_order_gtc_sell(
                client_order_id=client_order_id,
                product_id=product_id,
                base_size=base_size,
                limit_price=limit_price,
                post_only=post_only
            )

            order_id = getattr(order, 'order_id', 'unknown')

            # Track the order
            self.pending_orders[order_id] = {
                'order_id': order_id,
                'client_order_id': client_order_id,
                'product_id': product_id,
                'side': 'SELL',
                'type': 'limit',
                'base_size': float(base_size),
                'limit_price': float(limit_price),
                'created_at': datetime.now(),
                'status': 'pending'
            }

            logger.info(f'Limit sell order created: {order_id} for {base_size} {product_id} at ${limit_price}')

            return {
                'success': True,
                'order_id': order_id,
                'client_order_id': client_order_id,
                'product_id': product_id,
                'base_size': float(base_size),
                'limit_price': float(limit_price),
                'type': 'limit_sell',
                'message': f'Limit sell order placed for {base_size} {product_id.split("-")[0]} at ${limit_price}'
            }

        except Exception as e:
            logger.error(f'Error creating limit sell order: {e}')
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to place limit sell order: {str(e)}'
            }

    async def cancel_order(self, order_id: str) -> Dict:
        """Cancel an order"""
        try:
            result = self.client.cancel_orders(order_ids=[order_id])
            results = getattr(result, 'results', [])

            if results and len(results) > 0:
                cancel_result = results[0]
                success = getattr(cancel_result, 'success', False)

                if success:
                    # Remove from pending orders
                    if order_id in self.pending_orders:
                        del self.pending_orders[order_id]

                    logger.info(f'Order cancelled successfully: {order_id}')
                    return {
                        'success': True,
                        'order_id': order_id,
                        'message': f'Order {order_id[:8]}... cancelled successfully'
                    }
                else:
                    error_msg = getattr(cancel_result, 'failure_reason', 'Unknown error')
                    return {
                        'success': False,
                        'order_id': order_id,
                        'error': error_msg,
                        'message': f'Failed to cancel order: {error_msg}'
                    }
            else:
                return {
                    'success': False,
                    'order_id': order_id,
                    'error': 'No response from API',
                    'message': 'Failed to cancel order: No response from API'
                }

        except Exception as e:
            logger.error(f'Error cancelling order {order_id}: {e}')
            return {
                'success': False,
                'order_id': order_id,
                'error': str(e),
                'message': f'Failed to cancel order: {str(e)}'
            }

    async def get_order_status(self, order_id: str) -> Dict:
        """Get current status of an order"""
        try:
            order = self.client.get_order(order_id=order_id)

            return {
                'order_id': getattr(order, 'order_id', order_id),
                'status': getattr(order, 'status', 'unknown'),
                'side': getattr(order, 'side', 'unknown'),
                'product_id': getattr(order, 'product_id', 'unknown'),
                'filled_size': getattr(order, 'filled_size', '0'),
                'filled_value': getattr(order, 'filled_value', '0'),
                'average_filled_price': getattr(order, 'average_filled_price', '0'),
                'created_time': getattr(order, 'created_time', ''),
                'completion_percentage': getattr(order, 'completion_percentage', '0')
            }

        except Exception as e:
            logger.error(f'Error getting order status for {order_id}: {e}')
            return {
                'order_id': order_id,
                'error': str(e),
                'status': 'error'
            }

    async def get_open_orders(self, product_id: Optional[str] = None) -> List[Dict]:
        """Get all open orders, optionally filtered by product"""
        try:
            orders = self.client.list_orders(
                product_id=product_id,
                order_status=['OPEN', 'PENDING'],
                limit=50
            )

            order_list = getattr(orders, 'orders', [])

            open_orders = []
            for order in order_list:
                order_info = {
                    'order_id': getattr(order, 'order_id', 'unknown'),
                    'product_id': getattr(order, 'product_id', 'unknown'),
                    'side': getattr(order, 'side', 'unknown'),
                    'status': getattr(order, 'status', 'unknown'),
                    'size': getattr(order, 'size', '0'),
                    'filled_size': getattr(order, 'filled_size', '0'),
                    'price': getattr(order, 'price', '0'),
                    'created_time': getattr(order, 'created_time', ''),
                    'order_type': getattr(order, 'order_type', 'unknown')
                }
                open_orders.append(order_info)

            return open_orders

        except Exception as e:
            logger.error(f'Error getting open orders: {e}')
            return []

    async def get_positions(self) -> List[Dict]:
        """Get current positions (non-zero balances)"""
        try:
            response = self.client.get_accounts()
            accounts = response.accounts if hasattr(response, 'accounts') else []

            positions = []
            for account in accounts:
                currency = getattr(account, 'currency', '')
                balance_obj = getattr(account, 'available_balance', None)
                balance = float(balance_obj.value) if balance_obj and hasattr(balance_obj, 'value') else 0.0

                # Only include non-zero, non-USD balances
                if balance > 0 and currency != 'USD':
                    positions.append({
                        'currency': currency,
                        'balance': balance,
                        'uuid': getattr(account, 'uuid', None),
                        'product_id': f"{currency}-USD"  # Assume USD pairs
                    })

            return positions

        except Exception as e:
            logger.error(f'Error getting positions: {e}')
            return []

    def format_order_summary(self, order_info: Dict) -> str:
        """Format order information for display"""
        if not order_info.get('success', False):
            return f"❌ {order_info.get('message', 'Order failed')}"

        order_type = order_info.get('type', 'unknown')
        product_id = order_info.get('product_id', 'unknown')
        order_id_short = order_info.get('order_id', 'unknown')[:8]

        if order_type == 'market_buy':
            quote_size = order_info.get('quote_size', 0)
            return f"✅ Market Buy Order\n🪙 {product_id}\n💰 ${quote_size:,.2f}\n🆔 {order_id_short}..."

        elif order_type == 'market_sell':
            base_size = order_info.get('base_size', 0)
            currency = product_id.split('-')[0]
            return f"✅ Market Sell Order\n🪙 {product_id}\n🔢 {base_size:.8f} {currency}\n🆔 {order_id_short}..."

        elif order_type == 'limit_sell':
            base_size = order_info.get('base_size', 0)
            limit_price = order_info.get('limit_price', 0)
            currency = product_id.split('-')[0]
            return f"✅ Limit Sell Order\n🪙 {product_id}\n🔢 {base_size:.8f} {currency}\n💵 ${limit_price:,.6f}\n🆔 {order_id_short}..."

        else:
            return f"✅ {order_info.get('message', 'Order placed successfully')}"