import os
import logging
import asyncio
import time
import json
from typing import Dict, Optional, List, Tuple
from decimal import Decimal, ROUND_DOWN
from coinbase.rest import RESTClient
from datetime import datetime, date
from pathlib import Path

logger = logging.getLogger(__name__)


class TradingManager:
    """Manages Coinbase Advanced Trade API interactions for Telegram bot"""

    def __init__(self):
        # Use dedicated trading API credentials if available, otherwise fall back to main credentials
        self.api_key = os.getenv('COINBASE_TRADING_API_KEY') or os.getenv('COINBASE_API_KEY')
        self.signing_key = os.getenv('COINBASE_TRADING_SIGNING_KEY') or os.getenv('COINBASE_SIGNING_KEY')

        if not self.api_key or not self.signing_key:
            raise ValueError('COINBASE_TRADING_API_KEY and COINBASE_TRADING_SIGNING_KEY must be set')

        self.client = RESTClient(api_key=self.api_key, api_secret=self.signing_key)
        self.active_positions = {}  # product_id -> position_info
        self.pending_orders = {}    # order_id -> order_info

        # Enhanced trading settings with safety features
        self.default_position_percentage = float(os.getenv('DEFAULT_POSITION_PERCENTAGE', '2.0'))  # 2% of balance
        self.max_position_percentage = float(os.getenv('MAX_POSITION_PERCENTAGE', '5.0'))          # 5% max (reduced for safety)
        self.min_order_usd = float(os.getenv('MIN_TRADE_USD', '10.0'))                            # $10 minimum
        self.reserve_percentage = float(os.getenv('RESERVE_PERCENTAGE', '5.0'))                   # Keep 5% USD as reserve
        self.max_daily_trades = int(os.getenv('MAX_DAILY_TRADES', '10'))                          # Daily trade limit
        self.daily_trade_count = 0
        self.last_trade_date = None
        self.emergency_stop = False

        # Rate limiting
        self.last_api_call = 0
        self.min_api_interval = 0.1  # 100ms between calls (10 calls/second limit)

        # Trade tracking and statistics
        self.trades_file = Path("trade_log.json")
        self.load_trade_statistics()

        # Emergency stop check
        if os.getenv('EMERGENCY_STOP', 'false').lower() == 'true':
            self.emergency_stop = True
            logger.warning('🚨 EMERGENCY STOP ACTIVATED - All trading disabled')

        logger.info(f'Enhanced Trading Manager initialized - Default: {self.default_position_percentage}%, Max: {self.max_position_percentage}%, Reserve: {self.reserve_percentage}%')

    def load_trade_statistics(self):
        """Load trade statistics from file"""
        try:
            if self.trades_file.exists():
                with open(self.trades_file, 'r') as f:
                    data = json.load(f)
                    today = str(date.today())
                    if today in data:
                        self.daily_trade_count = data[today].get('count', 0)
                        self.last_trade_date = today
                    else:
                        self.daily_trade_count = 0
                        self.last_trade_date = None
            else:
                self.daily_trade_count = 0
                self.last_trade_date = None
        except Exception as e:
            logger.error(f'Error loading trade statistics: {e}')
            self.daily_trade_count = 0
            self.last_trade_date = None

    def save_trade_statistics(self):
        """Save trade statistics to file"""
        try:
            data = {}
            if self.trades_file.exists():
                with open(self.trades_file, 'r') as f:
                    data = json.load(f)

            today = str(date.today())
            if today not in data:
                data[today] = {'count': 0, 'trades': []}

            data[today]['count'] = self.daily_trade_count

            with open(self.trades_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f'Error saving trade statistics: {e}')

    def log_trade(self, trade_info: Dict):
        """Log a completed trade"""
        try:
            data = {}
            if self.trades_file.exists():
                with open(self.trades_file, 'r') as f:
                    data = json.load(f)

            today = str(date.today())
            if today not in data:
                data[today] = {'count': 0, 'trades': []}

            trade_log = {
                'timestamp': datetime.now().isoformat(),
                'order_id': trade_info.get('order_id'),
                'product_id': trade_info.get('product_id'),
                'side': trade_info.get('side', 'unknown'),
                'type': trade_info.get('type'),
                'amount': trade_info.get('quote_size') or trade_info.get('base_size'),
                'price': trade_info.get('limit_price', 'market')
            }

            data[today]['trades'].append(trade_log)

            with open(self.trades_file, 'w') as f:
                json.dump(data, f, indent=2)

            logger.info(f'Trade logged: {trade_info.get("product_id")} {trade_info.get("side")}')
        except Exception as e:
            logger.error(f'Error logging trade: {e}')

    async def rate_limit_check(self):
        """Enforce API rate limiting"""
        current_time = time.time()
        time_since_last = current_time - self.last_api_call

        if time_since_last < self.min_api_interval:
            sleep_time = self.min_api_interval - time_since_last
            await asyncio.sleep(sleep_time)

        self.last_api_call = time.time()

    def validate_trading_conditions(self) -> Dict:
        """Validate if trading is allowed"""
        # Check emergency stop
        if self.emergency_stop:
            return {'valid': False, 'error': '🚨 Emergency stop is active. Trading disabled.'}

        # Check emergency stop environment variable
        if os.getenv('EMERGENCY_STOP', 'false').lower() == 'true':
            self.emergency_stop = True
            return {'valid': False, 'error': '🚨 Emergency stop activated via environment. Trading disabled.'}

        # Check daily trade limits
        today = str(date.today())
        if self.last_trade_date != today:
            self.daily_trade_count = 0
            self.last_trade_date = today

        if self.daily_trade_count >= self.max_daily_trades:
            return {'valid': False, 'error': f'Daily trade limit reached ({self.daily_trade_count}/{self.max_daily_trades})'}

        return {'valid': True}

    def calculate_available_trading_balance(self, total_balance: float) -> float:
        """Calculate available balance for trading after reserve"""
        reserve_amount = total_balance * (self.reserve_percentage / 100.0)
        available = total_balance - reserve_amount
        return max(0, available)

    async def emergency_stop_toggle(self, enable: bool = True) -> Dict:
        """Toggle emergency stop"""
        self.emergency_stop = enable
        if enable:
            logger.warning('🚨 Emergency stop ENABLED - All trading disabled')
            return {'success': True, 'message': '🚨 Emergency stop ENABLED. All trading disabled.'}
        else:
            logger.info('✅ Emergency stop DISABLED - Trading re-enabled')
            return {'success': True, 'message': '✅ Emergency stop DISABLED. Trading re-enabled.'}

    async def get_trading_statistics(self) -> Dict:
        """Get current trading statistics"""
        today = str(date.today())
        if self.last_trade_date != today:
            self.daily_trade_count = 0

        return {
            'daily_trades': self.daily_trade_count,
            'daily_limit': self.max_daily_trades,
            'trades_remaining': max(0, self.max_daily_trades - self.daily_trade_count),
            'emergency_stop': self.emergency_stop,
            'reserve_percentage': self.reserve_percentage,
            'max_position_percentage': self.max_position_percentage,
            'min_trade_usd': self.min_order_usd
        }

    async def get_account_balance(self, currency: str = 'USD') -> Dict:
        """Get account balance for specified currency with rate limiting"""
        try:
            await self.rate_limit_check()
            response = self.client.get_accounts()
            accounts = response.accounts if hasattr(response, 'accounts') else []

            for account in accounts:
                if hasattr(account, 'currency') and account.currency == currency:
                    balance_obj = getattr(account, 'available_balance', None)
                    total_balance = float(balance_obj.value) if balance_obj and hasattr(balance_obj, 'value') else 0.0

                    # Calculate available trading balance (after reserve)
                    if currency == 'USD':
                        available_balance = self.calculate_available_trading_balance(total_balance)
                        reserve_amount = total_balance - available_balance
                    else:
                        available_balance = total_balance
                        reserve_amount = 0

                    return {
                        'currency': currency,
                        'balance': total_balance,
                        'available_trading': available_balance,
                        'reserve': reserve_amount,
                        'uuid': getattr(account, 'uuid', None),
                        'formatted': f"${total_balance:,.2f}" if currency == 'USD' else f"{total_balance:.8f} {currency}",
                        'available_formatted': f"${available_balance:,.2f}" if currency == 'USD' else f"{available_balance:.8f} {currency}"
                    }

            return {'currency': currency, 'balance': 0.0, 'available_trading': 0.0, 'reserve': 0.0, 'uuid': None, 'formatted': f"0.00 {currency}", 'available_formatted': f"0.00 {currency}"}

        except Exception as e:
            logger.error(f'Error getting {currency} balance: {e}')
            return {'currency': currency, 'balance': 0.0, 'available_trading': 0.0, 'reserve': 0.0, 'uuid': None, 'formatted': f"Error", 'available_formatted': f"Error", 'error': str(e)}

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

    def calculate_position_size(self, available_trading_balance: float, percentage: Optional[float] = None) -> Dict:
        """Calculate position size based on available trading balance (after reserve) and percentage"""
        try:
            position_pct = percentage or self.default_position_percentage

            # Ensure percentage is within limits
            position_pct = max(0.1, min(position_pct, self.max_position_percentage))

            # Calculate position size based on available trading balance (already excludes reserve)
            position_size = available_trading_balance * (position_pct / 100.0)

            # Check minimum order size
            if position_size < self.min_order_usd:
                return {
                    'valid': False,
                    'error': f'Position size ${position_size:.2f} is below minimum ${self.min_order_usd}',
                    'percentage': position_pct,
                    'position_size': position_size,
                    'available_balance': available_trading_balance
                }

            return {
                'valid': True,
                'percentage': position_pct,
                'position_size': position_size,
                'formatted_size': f"${position_size:,.2f}",
                'remaining_balance': available_trading_balance - position_size,
                'available_balance': available_trading_balance
            }

        except Exception as e:
            logger.error(f'Error calculating position size: {e}')
            return {'valid': False, 'error': str(e)}

    async def create_market_buy_order(self, product_id: str, quote_size: str,
                                    position_percentage: Optional[float] = None) -> Dict:
        """Create a market buy order with safety validation"""
        try:
            # Validate trading conditions first
            validation = self.validate_trading_conditions()
            if not validation['valid']:
                return {'success': False, 'error': validation['error'], 'message': validation['error']}

            # Rate limiting
            await self.rate_limit_check()

            # Check for duplicate orders (basic protection)
            duplicate_check_window = 30  # seconds
            current_time = datetime.now()
            for order_info in self.pending_orders.values():
                if (order_info['product_id'] == product_id and
                    order_info['side'] == 'BUY' and
                    order_info['quote_size'] == float(quote_size) and
                    (current_time - order_info['created_at']).total_seconds() < duplicate_check_window):
                    return {
                        'success': False,
                        'error': 'Duplicate order detected',
                        'message': f'Similar order placed within {duplicate_check_window}s. Please wait.'
                    }

            client_order_id = f"tg_buy_{product_id}_{int(datetime.now().timestamp())}"

            order = self.client.market_order_buy(
                client_order_id=client_order_id,
                product_id=product_id,
                quote_size=quote_size
            )

            order_id = getattr(order, 'order_id', 'unknown')

            # Track the order
            order_info = {
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
            self.pending_orders[order_id] = order_info

            # Update daily trade count
            today = str(date.today())
            if self.last_trade_date != today:
                self.daily_trade_count = 0
                self.last_trade_date = today

            self.daily_trade_count += 1
            self.save_trade_statistics()

            # Log the trade
            self.log_trade(order_info)

            logger.info(f'Market buy order created: {order_id} for {quote_size} USD of {product_id} (Trade {self.daily_trade_count}/{self.max_daily_trades})')

            return {
                'success': True,
                'order_id': order_id,
                'client_order_id': client_order_id,
                'product_id': product_id,
                'quote_size': float(quote_size),
                'type': 'market_buy',
                'side': 'BUY',
                'message': f'Market buy order placed for ${quote_size} of {product_id}',
                'daily_trades': f'{self.daily_trade_count}/{self.max_daily_trades}'
            }

        except Exception as e:
            logger.error(f'Error creating market buy order: {e}')
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to place buy order: {str(e)}'
            }

    async def create_market_sell_order(self, product_id: str, base_size: str) -> Dict:
        """Create a market sell order with safety validation"""
        try:
            # Validate trading conditions first
            validation = self.validate_trading_conditions()
            if not validation['valid']:
                return {'success': False, 'error': validation['error'], 'message': validation['error']}

            # Rate limiting
            await self.rate_limit_check()

            # Check for duplicate orders (basic protection)
            duplicate_check_window = 30  # seconds
            current_time = datetime.now()
            for order_info in self.pending_orders.values():
                if (order_info['product_id'] == product_id and
                    order_info['side'] == 'SELL' and
                    order_info.get('base_size') == float(base_size) and
                    (current_time - order_info['created_at']).total_seconds() < duplicate_check_window):
                    return {
                        'success': False,
                        'error': 'Duplicate order detected',
                        'message': f'Similar order placed within {duplicate_check_window}s. Please wait.'
                    }

            client_order_id = f"tg_sell_{product_id}_{int(datetime.now().timestamp())}"

            order = self.client.market_order_sell(
                client_order_id=client_order_id,
                product_id=product_id,
                base_size=base_size
            )

            order_id = getattr(order, 'order_id', 'unknown')

            # Track the order
            order_info = {
                'order_id': order_id,
                'client_order_id': client_order_id,
                'product_id': product_id,
                'side': 'SELL',
                'type': 'market',
                'base_size': float(base_size),
                'created_at': datetime.now(),
                'status': 'pending'
            }
            self.pending_orders[order_id] = order_info

            # Update daily trade count
            today = str(date.today())
            if self.last_trade_date != today:
                self.daily_trade_count = 0
                self.last_trade_date = today

            self.daily_trade_count += 1
            self.save_trade_statistics()

            # Log the trade
            self.log_trade(order_info)

            logger.info(f'Market sell order created: {order_id} for {base_size} of {product_id} (Trade {self.daily_trade_count}/{self.max_daily_trades})')

            return {
                'success': True,
                'order_id': order_id,
                'client_order_id': client_order_id,
                'product_id': product_id,
                'base_size': float(base_size),
                'type': 'market_sell',
                'side': 'SELL',
                'message': f'Market sell order placed for {base_size} {product_id.split("-")[0]}',
                'daily_trades': f'{self.daily_trade_count}/{self.max_daily_trades}'
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

    async def edit_order(self, order_id: str, new_price: Optional[str] = None, new_size: Optional[str] = None) -> Dict:
        """Edit an existing order (price and/or size)"""
        try:
            # Validate trading conditions first
            validation = self.validate_trading_conditions()
            if not validation['valid']:
                return {'success': False, 'error': validation['error'], 'message': validation['error']}

            # Rate limiting
            await self.rate_limit_check()

            # Get current order details first
            current_order = await self.get_order_status(order_id)
            if 'error' in current_order:
                return {
                    'success': False,
                    'order_id': order_id,
                    'error': current_order['error'],
                    'message': f'Cannot edit order: {current_order["error"]}'
                }

            # Check if order is still editable
            if current_order['status'] not in ['OPEN', 'PENDING']:
                return {
                    'success': False,
                    'order_id': order_id,
                    'error': f'Order status is {current_order["status"]}',
                    'message': f'Cannot edit order: status is {current_order["status"]}'
                }

            # Prepare edit parameters
            edit_params = {'order_id': order_id}
            if new_price:
                edit_params['price'] = new_price
            if new_size:
                edit_params['size'] = new_size

            # Use the edit order method from Coinbase SDK
            result = self.client.edit_order(**edit_params)

            # Check if edit was successful
            success = getattr(result, 'success', False)
            if success:
                # Update our tracking if we have this order
                if order_id in self.pending_orders:
                    if new_price:
                        self.pending_orders[order_id]['limit_price'] = float(new_price)
                    if new_size:
                        if 'quote_size' in self.pending_orders[order_id]:
                            self.pending_orders[order_id]['quote_size'] = float(new_size)
                        if 'base_size' in self.pending_orders[order_id]:
                            self.pending_orders[order_id]['base_size'] = float(new_size)

                logger.info(f'Order edited successfully: {order_id}')
                return {
                    'success': True,
                    'order_id': order_id,
                    'message': f'Order {order_id[:8]}... edited successfully',
                    'new_price': new_price,
                    'new_size': new_size
                }
            else:
                error_msg = getattr(result, 'failure_reason', 'Unknown error')
                return {
                    'success': False,
                    'order_id': order_id,
                    'error': error_msg,
                    'message': f'Failed to edit order: {error_msg}'
                }

        except Exception as e:
            logger.error(f'Error editing order {order_id}: {e}')
            return {
                'success': False,
                'order_id': order_id,
                'error': str(e),
                'message': f'Failed to edit order: {str(e)}'
            }

    async def get_fills(self, order_id: Optional[str] = None, product_id: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """Get fill (execution) history for orders"""
        try:
            await self.rate_limit_check()

            # Build parameters for the fills request
            params = {'limit': limit}
            if order_id:
                params['order_id'] = order_id
            if product_id:
                params['product_id'] = product_id

            fills_response = self.client.get_fills(**params)
            fills = getattr(fills_response, 'fills', [])

            fill_list = []
            for fill in fills:
                fill_info = {
                    'trade_id': getattr(fill, 'trade_id', 'unknown'),
                    'order_id': getattr(fill, 'order_id', 'unknown'),
                    'product_id': getattr(fill, 'product_id', 'unknown'),
                    'side': getattr(fill, 'side', 'unknown'),
                    'size': getattr(fill, 'size', '0'),
                    'price': getattr(fill, 'price', '0'),
                    'fee': getattr(fill, 'fee', '0'),
                    'trade_time': getattr(fill, 'trade_time', ''),
                    'user_id': getattr(fill, 'user_id', ''),
                    'sequence_timestamp': getattr(fill, 'sequence_timestamp', '')
                }
                fill_list.append(fill_info)

            return fill_list

        except Exception as e:
            logger.error(f'Error getting fills: {e}')
            return []

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