import os
import logging
import asyncio
import time
import json
import jwt
import requests
from typing import Dict, Optional, List
from cryptography.hazmat.primitives import serialization
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

        # Replace escaped newlines
        self.signing_key = self.signing_key.replace('\\n', '\n')
        
        self.base_url = "https://api.coinbase.com"
        self.active_positions = {}
        self.pending_orders = {}

        # Enhanced trading settings with safety features
        self.default_position_percentage = float(os.getenv('DEFAULT_POSITION_PERCENTAGE', '2.0'))
        self.max_position_percentage = float(os.getenv('MAX_POSITION_PERCENTAGE', '5.0'))
        self.min_order_usd = float(os.getenv('MIN_TRADE_USD', '10.0'))
        self.reserve_percentage = float(os.getenv('RESERVE_PERCENTAGE', '5.0'))
        self.max_daily_trades = int(os.getenv('MAX_DAILY_TRADES', '10'))
        self.daily_trade_count = 0
        self.last_trade_date = None
        self.emergency_stop = False

        # Rate limiting
        self.last_api_call = 0
        self.min_api_interval = 0.1

        # Trade tracking and statistics
        self.trades_file = Path("trade_log.json")
        self.load_trade_statistics()

        # Emergency stop check
        if os.getenv('EMERGENCY_STOP', 'false').lower() == 'true':
            self.emergency_stop = True
            logger.warning('🚨 EMERGENCY STOP ACTIVATED - All trading disabled')

        logger.info(f'Enhanced Trading Manager initialized - Default: {self.default_position_percentage}%, Max: {self.max_position_percentage}%, Reserve: {self.reserve_percentage}%')

    def _generate_jwt(self, method: str, path: str) -> str:
        """Generate JWT token for authentication"""
        try:
            # Load private key
            private_key = serialization.load_pem_private_key(
                self.signing_key.encode(),
                password=None
            )
            
            # Create JWT URI (method + host + path)
            uri = f"{method} api.coinbase.com{path}"
            
            # Create JWT payload
            current_time = int(time.time())
            payload = {
                'sub': self.api_key,
                'iss': 'coinbase-cloud',
                'nbf': current_time,
                'exp': current_time + 120,
                'uri': uri
            }
            
            # Generate JWT token
            token = jwt.encode(
                payload,
                private_key,
                algorithm='ES256',
                headers={'kid': self.api_key, 'nonce': str(current_time)}
            )
            
            return token
            
        except Exception as e:
            raise Exception(f"Failed to generate JWT: {e}")

    def _make_request(self, method: str, path: str, json_data: Optional[Dict] = None) -> Dict:
        """Make authenticated request to Coinbase API"""
        # Generate JWT for this specific request
        token = self._generate_jwt(method, path)
        
        # Prepare headers
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        # Make request
        url = f"{self.base_url}{path}"
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=10)
            elif method == 'POST':
                response = requests.post(url, headers=headers, json=json_data, timeout=10)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, json=json_data, timeout=10)
            elif method == 'PUT':
                response = requests.put(url, headers=headers, json=json_data, timeout=10)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            # Handle response
            if response.status_code == 200:
                return response.json()
            else:
                error_msg = f"API request failed ({response.status_code}): {response.text}"
                logger.error(error_msg)
                return {'error': error_msg, 'status_code': response.status_code}
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Request exception: {e}")
            return {'error': str(e)}

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
        if self.emergency_stop:
            return {'valid': False, 'error': '🚨 Emergency stop is active. Trading disabled.'}

        if os.getenv('EMERGENCY_STOP', 'false').lower() == 'true':
            self.emergency_stop = True
            return {'valid': False, 'error': '🚨 Emergency stop activated via environment. Trading disabled.'}

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
        """Get account balance for specified currency"""
        try:
            await self.rate_limit_check()
            response = self._make_request('GET', '/api/v3/brokerage/accounts')
            
            if 'error' in response:
                return {'currency': currency, 'error': response['error']}
            
            accounts = response.get('accounts', [])

            for account in accounts:
                if account.get('currency') == currency:
                    total_balance = float(account.get('available_balance', {}).get('value', 0))

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
                        'uuid': account.get('uuid'),
                        'formatted': f"${total_balance:,.2f}" if currency == 'USD' else f"{total_balance:.8f} {currency}",
                        'available_formatted': f"${available_balance:,.2f}" if currency == 'USD' else f"{available_balance:.8f} {currency}"
                    }

            return {
                'currency': currency, 
                'balance': 0.0, 
                'available_trading': 0.0, 
                'reserve': 0.0, 
                'formatted': f"0.00 {currency}"
            }

        except Exception as e:
            logger.error(f'Error getting {currency} balance: {e}')
            return {'currency': currency, 'error': str(e)}

    async def get_product_info(self, product_id: str) -> Dict:
        """Get product information and current price"""
        try:
            response = self._make_request('GET', f'/api/v3/brokerage/products/{product_id}')
            
            if 'error' in response:
                return {'product_id': product_id, 'error': response['error']}

            # Get current price from ticker
            ticker_response = self._make_request('GET', f'/api/v3/brokerage/products/{product_id}/ticker')
            current_price = float(ticker_response.get('price', 0)) if 'error' not in ticker_response else 0

            return {
                'product_id': response.get('product_id', product_id),
                'base_currency': response.get('base_currency_id', ''),
                'quote_currency': response.get('quote_currency_id', ''),
                'status': response.get('status', 'unknown'),
                'current_price': current_price,
                'formatted_price': f"${current_price:,.6f}" if current_price > 0 else "N/A"
            }

        except Exception as e:
            logger.error(f'Error getting product info for {product_id}: {e}')
            return {'product_id': product_id, 'error': str(e)}

    def calculate_position_size(self, available_trading_balance: float, percentage: Optional[float] = None) -> Dict:
        """Calculate position size based on available trading balance and percentage"""
        try:
            position_pct = percentage or self.default_position_percentage
            position_pct = max(0.1, min(position_pct, self.max_position_percentage))
            position_size = available_trading_balance * (position_pct / 100.0)

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
        """Create a market buy order"""
        try:
            validation = self.validate_trading_conditions()
            if not validation['valid']:
                return {'success': False, 'error': validation['error'], 'message': validation['error']}

            await self.rate_limit_check()

            client_order_id = f"tg_buy_{product_id}_{int(datetime.now().timestamp())}"

            order_data = {
                "client_order_id": client_order_id,
                "product_id": product_id,
                "side": "BUY",
                "order_configuration": {
                    "market_market_ioc": {
                        "quote_size": quote_size
                    }
                }
            }

            response = self._make_request('POST', '/api/v3/brokerage/orders', json_data=order_data)

            if 'error' in response:
                return {'success': False, 'error': response['error'], 'message': f"Failed: {response['error']}"}

            order_id = response.get('order_id', 'unknown')

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

            # Update trade count
            today = str(date.today())
            if self.last_trade_date != today:
                self.daily_trade_count = 0
                self.last_trade_date = today

            self.daily_trade_count += 1
            self.save_trade_statistics()
            self.log_trade(order_info)

            logger.info(f'Market buy order created: {order_id} for {quote_size} USD of {product_id}')

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
            return {'success': False, 'error': str(e), 'message': f'Failed: {str(e)}'}

    async def create_market_sell_order(self, product_id: str, base_size: str) -> Dict:
        """Create a market sell order"""
        try:
            validation = self.validate_trading_conditions()
            if not validation['valid']:
                return {'success': False, 'error': validation['error'], 'message': validation['error']}

            await self.rate_limit_check()

            client_order_id = f"tg_sell_{product_id}_{int(datetime.now().timestamp())}"

            order_data = {
                "client_order_id": client_order_id,
                "product_id": product_id,
                "side": "SELL",
                "order_configuration": {
                    "market_market_ioc": {
                        "base_size": base_size
                    }
                }
            }

            response = self._make_request('POST', '/api/v3/brokerage/orders', json_data=order_data)

            if 'error' in response:
                return {'success': False, 'error': response['error'], 'message': f"Failed: {response['error']}"}

            order_id = response.get('order_id', 'unknown')

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

            today = str(date.today())
            if self.last_trade_date != today:
                self.daily_trade_count = 0
                self.last_trade_date = today

            self.daily_trade_count += 1
            self.save_trade_statistics()
            self.log_trade(order_info)

            logger.info(f'Market sell order created: {order_id}')

            return {
                'success': True,
                'order_id': order_id,
                'product_id': product_id,
                'base_size': float(base_size),
                'type': 'market_sell',
                'side': 'SELL',
                'message': f'Market sell order placed',
                'daily_trades': f'{self.daily_trade_count}/{self.max_daily_trades}'
            }

        except Exception as e:
            logger.error(f'Error creating market sell order: {e}')
            return {'success': False, 'error': str(e), 'message': f'Failed: {str(e)}'}

    async def create_limit_sell_order(self, product_id: str, base_size: str,
                                    limit_price: str, post_only: bool = True) -> Dict:
        """Create a limit sell order"""
        try:
            client_order_id = f"tg_limit_sell_{product_id}_{int(datetime.now().timestamp())}"

            order_data = {
                "client_order_id": client_order_id,
                "product_id": product_id,
                "side": "SELL",
                "order_configuration": {
                    "limit_limit_gtc": {
                        "base_size": base_size,
                        "limit_price": limit_price,
                        "post_only": post_only
                    }
                }
            }

            response = self._make_request('POST', '/api/v3/brokerage/orders', json_data=order_data)

            if 'error' in response:
                return {'success': False, 'error': response['error']}

            order_id = response.get('order_id', 'unknown')
            self.pending_orders[order_id] = {
                'order_id': order_id,
                'product_id': product_id,
                'side': 'SELL',
                'type': 'limit',
                'base_size': float(base_size),
                'limit_price': float(limit_price),
                'created_at': datetime.now()
            }

            return {
                'success': True,
                'order_id': order_id,
                'product_id': product_id,
                'base_size': float(base_size),
                'limit_price': float(limit_price),
                'type': 'limit_sell',
                'message': f'Limit sell order placed at ${limit_price}'
            }

        except Exception as e:
            logger.error(f'Error creating limit sell order: {e}')
            return {'success': False, 'error': str(e)}

    async def cancel_order(self, order_id: str) -> Dict:
        """Cancel an order"""
        try:
            response = self._make_request('POST', '/api/v3/brokerage/orders/batch_cancel', 
                                         json_data={"order_ids": [order_id]})

            if 'error' in response:
                return {'success': False, 'order_id': order_id, 'error': response['error']}

            results = response.get('results', [])
            if results and results[0].get('success'):
                if order_id in self.pending_orders:
                    del self.pending_orders[order_id]
                return {'success': True, 'order_id': order_id, 'message': 'Order cancelled'}
            else:
                return {'success': False, 'order_id': order_id, 'error': 'Cancel failed'}

        except Exception as e:
            logger.error(f'Error cancelling order: {e}')
            return {'success': False, 'order_id': order_id, 'error': str(e)}

    async def get_open_orders(self, product_id: Optional[str] = None) -> List[Dict]:
        """Get all open orders"""
        try:
            path = '/api/v3/brokerage/orders/historical/batch?limit=50'
            if product_id:
                path += f'&product_id={product_id}'
            path += '&order_status=OPEN&order_status=PENDING'
            
            response = self._make_request('GET', path)
            
            if 'error' in response:
                return []

            return response.get('orders', [])

        except Exception as e:
            logger.error(f'Error getting open orders: {e}')
            return []

    async def get_positions(self) -> List[Dict]:
        """Get current positions"""
        try:
            response = self._make_request('GET', '/api/v3/brokerage/accounts')
            
            if 'error' in response:
                return []

            accounts = response.get('accounts', [])
            positions = []
            
            for account in accounts:
                currency = account.get('currency', '')
                balance = float(account.get('available_balance', {}).get('value', 0))

                if balance > 0 and currency != 'USD':
                    positions.append({
                        'currency': currency,
                        'balance': balance,
                        'uuid': account.get('uuid'),
                        'product_id': f"{currency}-USD"
                    })

            return positions

        except Exception as e:
            logger.error(f'Error getting positions: {e}')
            return []

    async def get_fills(self, order_id: Optional[str] = None, product_id: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """Get fill history"""
        try:
            await self.rate_limit_check()
            
            path = f'/api/v3/brokerage/orders/historical/fills?limit={limit}'
            if order_id:
                path += f'&order_id={order_id}'
            if product_id:
                path += f'&product_id={product_id}'
            
            response = self._make_request('GET', path)
            
            if 'error' in response:
                return []

            return response.get('fills', [])

        except Exception as e:
            logger.error(f'Error getting fills: {e}')
            return []

    async def edit_order(self, order_id: str, new_price: Optional[str] = None, new_size: Optional[str] = None) -> Dict:
        """Edit an existing order"""
        try:
            validation = self.validate_trading_conditions()
            if not validation['valid']:
                return {'success': False, 'error': validation['error']}

            await self.rate_limit_check()

            data = {'order_id': order_id}
            if new_price:
                data['price'] = new_price
            if new_size:
                data['size'] = new_size

            response = self._make_request('POST', '/api/v3/brokerage/orders/edit', json_data=data)

            if 'error' in response:
                return {'success': False, 'error': response['error']}

            return {'success': True, 'order_id': order_id, 'message': 'Order edited'}

        except Exception as e:
            logger.error(f'Error editing order: {e}')
            return {'success': False, 'error': str(e)}

    async def get_order_status(self, order_id: str) -> Dict:
        """Get order status"""
        try:
            response = self._make_request('GET', f'/api/v3/brokerage/orders/historical/{order_id}')
            
            if 'error' in response:
                return {'order_id': order_id, 'error': response['error'], 'status': 'error'}

            return {
                'order_id': response.get('order_id', order_id),
                'status': response.get('status', 'unknown'),
                'side': response.get('side', 'unknown'),
                'product_id': response.get('product_id', 'unknown')
            }

        except Exception as e:
            logger.error(f'Error getting order status: {e}')
            return {'order_id': order_id, 'error': str(e), 'status': 'error'}

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