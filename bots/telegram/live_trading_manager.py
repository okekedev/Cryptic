"""
Live Trading Manager
Integrates paper trading bot's automated logic with real Coinbase API execution
"""
import os
import logging
import sqlite3
import time
import json
from datetime import datetime
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, asdict
from trading_manager import TradingManager

logger = logging.getLogger(__name__)

# Trading configuration from environment
MIN_PROFIT_TARGET = float(os.getenv("MIN_PROFIT_TARGET", "3.0"))  # 3% minimum profit
TRAILING_THRESHOLD = float(os.getenv("TRAILING_THRESHOLD", "1.5"))  # Drop 1.5% from peak to exit
MIN_HOLD_TIME_MINUTES = float(os.getenv("MIN_HOLD_TIME_MINUTES", "30.0"))  # Minimum 30 min hold time
STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "5.0"))  # 5% stop loss
BUY_FEE_PERCENT = float(os.getenv("BUY_FEE_PERCENT", "0.6"))  # Coinbase Advanced Trade taker fee
SELL_FEE_PERCENT = float(os.getenv("SELL_FEE_PERCENT", "0.4"))  # Coinbase Advanced Trade maker fee


@dataclass
class LivePosition:
    """
    Represents a live trading position with automated exit logic
    Based on paper trading Position class but with real order tracking
    """
    product_id: str
    order_id: str  # Coinbase order ID from buy
    entry_price: float
    entry_time: str  # ISO format
    quantity: float
    cost_basis: float  # Including fees
    min_exit_price: float  # Minimum price to exit (3% + fees)
    peak_price: float  # Highest price seen
    trailing_exit_price: float  # Current trailing stop
    stop_loss_price: float  # Hard stop loss
    status: str = "active"  # active, hibernating, closed
    mode: str = "automated"  # automated, manual_limit_order
    limit_order_id: Optional[str] = None  # If in manual mode
    last_sync_timestamp: str = None  # Last API sync time

    def update_peak(self, current_price: float) -> bool:
        """Update peak and trailing exit. Returns True if peak was updated."""
        if current_price > self.peak_price:
            self.peak_price = current_price
            # Trailing exit: drop TRAILING_THRESHOLD% from peak
            self.trailing_exit_price = self.peak_price * (1 - TRAILING_THRESHOLD / 100)
            # Never let trailing exit drop below minimum exit price
            self.trailing_exit_price = max(self.trailing_exit_price, self.min_exit_price)
            return True
        return False

    def should_exit(self, current_price: float) -> Tuple[bool, str]:
        """
        Check if position should be exited (automated mode only)
        Returns (should_exit, reason)
        """
        # Calculate time held
        entry_time = datetime.fromisoformat(self.entry_time)
        time_held_minutes = (datetime.now() - entry_time).total_seconds() / 60

        # Calculate current P&L percentage
        current_value = current_price * self.quantity
        pnl_percent = ((current_value - self.cost_basis) / self.cost_basis) * 100

        # STOP LOSS: Exit immediately if down 5% or more
        if current_price <= self.stop_loss_price:
            return True, f"Stop loss hit at ${current_price:.6f} ({pnl_percent:.2f}%)"

        # PROFIT TARGET MET: Use trailing stop if price reached min profit target
        if current_price >= self.min_exit_price:
            if current_price <= self.trailing_exit_price:
                return True, f"Trailing stop hit (profit secured at ${current_price:.6f})"

        # MINIMUM HOLD TIME: Don't exit before 30 minutes unless stop loss
        if time_held_minutes < MIN_HOLD_TIME_MINUTES:
            return False, ""

        # AFTER MIN HOLD TIME: Exit if below trailing stop OR at any profit
        if current_price <= self.trailing_exit_price or pnl_percent > 0:
            return True, f"Min hold time reached ({time_held_minutes:.1f} min), exiting with P&L: {pnl_percent:+.2f}%"

        return False, ""

    def calculate_pnl(self, exit_price: float) -> Dict:
        """Calculate profit/loss for this position"""
        gross_proceeds = self.quantity * exit_price
        sell_fee = gross_proceeds * (SELL_FEE_PERCENT / 100)
        net_proceeds = gross_proceeds - sell_fee

        pnl = net_proceeds - self.cost_basis
        pnl_percent = (pnl / self.cost_basis) * 100

        return {
            "gross_proceeds": gross_proceeds,
            "sell_fee": sell_fee,
            "net_proceeds": net_proceeds,
            "pnl": pnl,
            "pnl_percent": pnl_percent
        }

    def get_unrealized_pnl(self, current_price: float) -> Dict:
        """Calculate unrealized P&L (accounts for projected sell fee)"""
        gross_value = current_price * self.quantity
        # Project sell fee that would be charged if we sold now
        projected_sell_fee = gross_value * (SELL_FEE_PERCENT / 100)
        net_value = gross_value - projected_sell_fee

        unrealized_pnl = net_value - self.cost_basis
        unrealized_pnl_percent = (unrealized_pnl / self.cost_basis) * 100

        return {
            "current_value": gross_value,
            "projected_sell_fee": projected_sell_fee,
            "net_value": net_value,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_percent": unrealized_pnl_percent
        }


class LiveTradingManager:
    """
    Manages live trading positions with automated exit logic
    Coordinates between TradingManager (Coinbase API) and Position logic
    """

    def __init__(self, trading_manager: TradingManager, db_path: str = '/app/data/telegram_bot.db'):
        self.trading_manager = trading_manager
        self.db_path = db_path
        self.positions: Dict[str, LivePosition] = {}  # product_id -> LivePosition

        # Initialize database
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()

        # Restore positions from database
        self._restore_positions()

        logger.info(f"LiveTradingManager initialized with {len(self.positions)} active position(s)")

    def _init_db(self):
        """Initialize database tables for live positions"""
        cursor = self.db.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS live_positions (
                product_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                entry_price REAL NOT NULL,
                entry_time TEXT NOT NULL,
                quantity REAL NOT NULL,
                cost_basis REAL NOT NULL,
                min_exit_price REAL NOT NULL,
                peak_price REAL NOT NULL,
                trailing_exit_price REAL NOT NULL,
                stop_loss_price REAL NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                limit_order_id TEXT,
                last_sync_timestamp TEXT
            )
        """)

        # Trade history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS live_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                entry_price REAL NOT NULL,
                exit_price REAL,
                quantity REAL NOT NULL,
                cost_basis REAL NOT NULL,
                gross_proceeds REAL,
                net_proceeds REAL,
                buy_fee REAL NOT NULL,
                sell_fee REAL,
                pnl REAL,
                pnl_percent REAL,
                peak_price REAL,
                status TEXT NOT NULL,
                exit_reason TEXT,
                mode TEXT NOT NULL
            )
        """)

        self.db.commit()
        logger.info(f"Database initialized: {self.db_path}")

    def _restore_positions(self):
        """Restore active positions from database"""
        cursor = self.db.cursor()
        cursor.execute("SELECT * FROM live_positions WHERE status IN ('active', 'hibernating')")
        rows = cursor.fetchall()

        for row in rows:
            product_id = row[0]
            position = LivePosition(
                product_id=product_id,
                order_id=row[1],
                entry_price=row[2],
                entry_time=row[3],
                quantity=row[4],
                cost_basis=row[5],
                min_exit_price=row[6],
                peak_price=row[7],
                trailing_exit_price=row[8],
                stop_loss_price=row[9],
                status=row[10],
                mode=row[11],
                limit_order_id=row[12],
                last_sync_timestamp=row[13]
            )
            self.positions[product_id] = position
            logger.info(f"Restored position: {product_id} (mode: {position.mode}, status: {position.status})")

    def _persist_position(self, position: LivePosition):
        """Save position to database"""
        cursor = self.db.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO live_positions
            (product_id, order_id, entry_price, entry_time, quantity, cost_basis,
             min_exit_price, peak_price, trailing_exit_price, stop_loss_price,
             status, mode, limit_order_id, last_sync_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            position.product_id, position.order_id, position.entry_price,
            position.entry_time, position.quantity, position.cost_basis,
            position.min_exit_price, position.peak_price, position.trailing_exit_price,
            position.stop_loss_price, position.status, position.mode,
            position.limit_order_id, position.last_sync_timestamp
        ))
        self.db.commit()

    def _remove_position(self, product_id: str):
        """Remove position from database"""
        cursor = self.db.cursor()
        cursor.execute("DELETE FROM live_positions WHERE product_id = ?", (product_id,))
        self.db.commit()

    async def execute_buy(self, product_id: str, quote_size: float, position_percentage: float) -> Dict:
        """
        Execute buy order and create live position with automated tracking

        Returns: {
            'success': bool,
            'message': str,
            'position': LivePosition or None,
            'order_result': dict
        }
        """
        # Round quote_size to 2 decimal places for Coinbase precision requirements
        quote_size = round(quote_size, 2)

        print(f"\n{'='*60}")
        print(f"🔵 EXECUTE_BUY CALLED")
        print(f"🔵 product_id: {product_id}")
        print(f"🔵 quote_size: {quote_size}")
        print(f"🔵 position_percentage: {position_percentage}")
        print(f"{'='*60}\n")

        try:
            # Check if we already have a position
            if product_id in self.positions:
                print(f"⚠️ Already have position in {product_id}")
                return {
                    'success': False,
                    'message': f"Already have active position in {product_id}",
                    'position': None,
                    'order_result': None
                }

            # Execute market buy via TradingManager
            print(f"📞 Calling trading_manager.create_market_buy_order...")
            order_result = await self.trading_manager.create_market_buy_order(
                product_id=product_id,
                quote_size=str(quote_size),
                position_percentage=position_percentage
            )

            print(f"\n{'='*60}")
            print(f"📥 ORDER_RESULT FROM create_market_buy_order:")
            print(json.dumps(order_result, indent=2, default=str))
            print(f"{'='*60}\n")

            if not order_result.get('success'):
                print(f"❌ Order failed: {order_result.get('message')}")
                return {
                    'success': False,
                    'message': order_result.get('message', 'Order Failed'),
                    'position': None,
                    'order_result': order_result
                }
                
            
            
            # Get order details - Already extracted to root level by trading_manager
            order_id = order_result.get('order_id', 'unknown')

            # Additional validation
            if not order_id or order_id == 'unknown':
                logger.error(f"❌ Failed to get order_id from response")
                logger.error(f"Response keys: {list(order_result.keys())}")
                return {
                    'success': False,
                    'message': 'Order created but failed to get order ID from response',
                    'position': None,
                    'order_result': order_result
                }

            # Wait for fill and get actual fill price from the order (no timeout)
            logger.info(f"🔎 Waiting for order {order_id} to fill...")
            fill_success = await self._wait_for_fill(order_id)

            if not fill_success:
                # Status polling failed/gave up, check fills endpoint as fallback
                logger.warning(f"⚠️ Status polling failed for order {order_id}, checking fills endpoint as fallback...")
                import asyncio
                await asyncio.sleep(2)

                fill_details = await self.trading_manager.get_order_fills(order_id)
                logger.info(f"📋 Fills endpoint result: {fill_details}")

                if fill_details.get('success'):
                    logger.info(f"✅ Order {order_id[:8]}... did fill! Continuing with position creation.")
                else:
                    logger.error(f"❌ Order {order_id[:8]}... could not be confirmed. Check Coinbase manually.")
                    return {
                        'success': False,
                        'message': f'Could not confirm order fill. Check Coinbase for order {order_id[:8]}...',
                        'position': None,
                        'order_result': order_result,
                        'order_id': order_id
                    }

            # Give the fills endpoint a moment to update (fills may lag behind order status)
            import asyncio
            await asyncio.sleep(1)

            # Get fill details from order
            fill_details = await self.trading_manager.get_order_fills(order_id)

            if not fill_details.get('success'):
                # Fallback: try to get price from ticker
                logger.warning(f"Could not get fills for {order_id}, trying ticker price as fallback")
                product_info = await self.trading_manager.get_product_info(product_id)
                entry_price = product_info.get('current_price', 0)
                quantity = quote_size / entry_price if entry_price > 0 else 0

                if entry_price <= 0 or quantity <= 0:
                    return {
                        'success': False,
                        'message': f'Could not get valid fill price for {product_id}. Check order {order_id[:8]}... manually in Coinbase.',
                        'position': None,
                        'order_result': order_result
                    }

                logger.warning(f"Using ticker price as fallback: ${entry_price:.6f}")
            else:
                # Use actual fill price and quantity from order
                entry_price = fill_details['average_fill_price']
                quantity = fill_details['filled_size']
                logger.info(f"✅ Using actual fill data: {quantity:.8f} @ ${entry_price:.6f}")

            # Calculate position parameters (using actual fill values)
            buy_fee = quote_size * (BUY_FEE_PERCENT / 100)
            cost_basis = quote_size + buy_fee

            # Calculate minimum exit price (3% profit + fees)
            target_proceeds = cost_basis * (1 + MIN_PROFIT_TARGET / 100)
            min_exit_price = target_proceeds / (quantity * (1 - SELL_FEE_PERCENT / 100))

            # Calculate stop loss (5% below entry)
            stop_loss_price = entry_price * (1 - STOP_LOSS_PERCENT / 100)

            # Initial peak is entry price
            peak_price = entry_price
            trailing_exit_price = max(min_exit_price, peak_price * (1 - TRAILING_THRESHOLD / 100))

            # Create live position
            position = LivePosition(
                product_id=product_id,
                order_id=order_id,
                entry_price=entry_price,
                entry_time=datetime.now().isoformat(),
                quantity=quantity,
                cost_basis=cost_basis,
                min_exit_price=min_exit_price,
                peak_price=peak_price,
                trailing_exit_price=trailing_exit_price,
                stop_loss_price=stop_loss_price,
                status="active",
                mode="automated",
                last_sync_timestamp=datetime.now().isoformat()
            )

            # Store position
            self.positions[product_id] = position
            self._persist_position(position)

            # Record trade entry
            self._record_trade_entry(position, quote_size * (BUY_FEE_PERCENT / 100))

            logger.info(f"✅ Live position opened: {product_id} @ ${entry_price:.6f}, "
                       f"Quantity: {quantity:.8f}, Stop Loss: ${stop_loss_price:.6f}, "
                       f"Min Exit: ${min_exit_price:.6f}")

            return {
                'success': True,
                'message': f'Position opened in {product_id}',
                'position': position,
                'order_result': order_result
            }

        except Exception as e:
            logger.error(f"Error executing buy: {e}")
            return {
                'success': False,
                'message': f'Error: {str(e)}',
                'position': None,
                'order_result': None
            }

    async def _wait_for_fill(self, order_id: str, max_wait_seconds: int = None):
        """Wait for order to fill by polling order status (no timeout if max_wait_seconds is None)"""
        import asyncio

        # Validate order_id before polling
        if not order_id or order_id == 'unknown':
            logger.error(f"❌ Invalid order_id '{order_id}' - cannot check fill status")
            return False

        start_time = time.time()
        poll_interval = 0.5  # Poll every 500ms
        consecutive_errors = 0
        max_consecutive_errors = 10  # Increased from 5 to 10
        poll_count = 0

        logger.info(f"🔍 Starting order fill monitoring for {order_id[:8]}... (no timeout)")

        while True:
            poll_count += 1
            elapsed = time.time() - start_time

            try:
                # Check order status
                logger.info(f"📡 Poll #{poll_count} (elapsed: {elapsed:.1f}s) - Checking order status for {order_id[:8]}...")
                status_result = await self.trading_manager.get_order_status(order_id)

                # Log the full status result for debugging
                logger.info(f"📊 Status API Response: {status_result}")

                # Check if there's an error in the response
                if 'error' in status_result:
                    consecutive_errors += 1
                    logger.warning(f"⚠️ Error getting order status (attempt {consecutive_errors}/{max_consecutive_errors}): {status_result.get('error')}")

                    # If too many consecutive errors, give up on status polling
                    if consecutive_errors >= max_consecutive_errors:
                        logger.error(f"❌ Too many consecutive errors ({consecutive_errors}) checking order status, will rely on fills endpoint")
                        return False

                    await asyncio.sleep(poll_interval)
                    continue

                # Reset error counter on successful response
                if consecutive_errors > 0:
                    logger.info(f"✅ Status check recovered after {consecutive_errors} errors")
                consecutive_errors = 0

                order_status = status_result.get('status', '').upper()
                product_id = status_result.get('product_id', 'unknown')
                side = status_result.get('side', 'unknown')

                logger.info(f"📈 Order {order_id[:8]}... status: {order_status} | Product: {product_id} | Side: {side}")

                # Order is filled
                if order_status in ['FILLED', 'DONE']:
                    logger.info(f"✅ Order {order_id[:8]}... FILLED successfully after {elapsed:.1f}s ({poll_count} polls)")
                    return True

                # Order failed
                if order_status in ['CANCELLED', 'EXPIRED', 'FAILED', 'REJECTED']:
                    logger.error(f"❌ Order {order_id[:8]}... FAILED with status: {order_status} after {elapsed:.1f}s")
                    return False

                # Still pending
                if order_status in ['PENDING', 'OPEN', 'QUEUED']:
                    logger.info(f"⏳ Order {order_id[:8]}... still {order_status}, waiting...")
                else:
                    logger.info(f"🔄 Order {order_id[:8]}... status: {order_status} (continuing to monitor)")

                await asyncio.sleep(poll_interval)

            except Exception as e:
                consecutive_errors += 1
                logger.warning(f"⚠️ Exception checking order status (attempt {consecutive_errors}/{max_consecutive_errors}): {e}", exc_info=True)

                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"❌ Too many exceptions ({consecutive_errors}), will rely on fills endpoint")
                    return False

                await asyncio.sleep(poll_interval)

    def update_position(self, product_id: str, current_price: float) -> Optional[Dict]:
        """
        Update position with current price
        Returns dict with exit info if position should be closed, None otherwise
        """
        if product_id not in self.positions:
            return None

        position = self.positions[product_id]

        # Skip automated logic if in manual mode
        if position.mode != "automated":
            return None

        # Update peak and check for exit
        peak_updated = position.update_peak(current_price)

        if peak_updated:
            unrealized = position.get_unrealized_pnl(current_price)
            logger.info(f"🔼 {product_id}: New peak ${current_price:.6f}, "
                       f"Unrealized P&L: {unrealized['unrealized_pnl_percent']:+.2f}%, "
                       f"Trailing exit: ${position.trailing_exit_price:.6f}")
            # Update persisted position
            self._persist_position(position)

        # Check if we should exit
        should_exit, exit_reason = position.should_exit(current_price)

        if should_exit:
            return {
                'should_exit': True,
                'reason': exit_reason,
                'current_price': current_price,
                'position': position
            }

        # Just update timestamp
        position.last_sync_timestamp = datetime.now().isoformat()
        self._persist_position(position)

        return None

    async def execute_automated_exit(self, product_id: str, exit_price: float, reason: str) -> Dict:
        """Execute automated exit (sell at market)"""
        if product_id not in self.positions:
            return {'success': False, 'message': 'Position not found'}

        position = self.positions[product_id]

        try:
            # Execute market sell
            sell_result = await self.trading_manager.create_market_sell_order(
                product_id=product_id,
                base_size=str(position.quantity)
            )

            if not sell_result.get('success'):
                logger.error(f"Automated exit failed for {product_id}: {sell_result.get('message')}")
                return sell_result

            # Get actual fill price from the order
            order_id = sell_result.get('order_id')
            if order_id:
                # Wait briefly for order to fill
                import asyncio
                await asyncio.sleep(1)

                # Get fill details
                fill_result = await self.trading_manager.get_order_fills(order_id)

                if fill_result.get('success'):
                    actual_exit_price = fill_result.get('average_fill_price')
                    logger.info(f"📊 Actual fill price: ${actual_exit_price:.6f} (estimated: ${exit_price:.6f})")
                    exit_price = actual_exit_price
                else:
                    logger.warning(f"Could not get fill price, using estimated: ${exit_price:.6f}")

            # Calculate P&L with actual exit price
            pnl_data = position.calculate_pnl(exit_price)

            # Calculate holding time
            entry_time = datetime.fromisoformat(position.entry_time)
            exit_time = datetime.now()
            holding_seconds = (exit_time - entry_time).total_seconds()

            logger.info(f"🔴 Automated exit: {product_id} @ ${exit_price:.6f}, "
                       f"P&L: {pnl_data['pnl_percent']:+.2f}%, "
                       f"Reason: {reason}, "
                       f"Held: {holding_seconds/60:.1f} min")

            # Record trade exit
            self._record_trade_exit(position, exit_price, pnl_data, reason)

            # Remove position
            del self.positions[product_id]
            self._remove_position(product_id)

            return {
                'success': True,
                'message': f'Position closed: {reason}',
                'pnl_data': pnl_data,
                'sell_result': sell_result,
                'actual_exit_price': exit_price
            }

        except Exception as e:
            logger.error(f"Error executing automated exit: {e}")
            return {'success': False, 'message': str(e)}

    async def execute_manual_exit(self, product_id: str) -> Dict:
        """Execute manual market sell"""
        if product_id not in self.positions:
            return {'success': False, 'message': 'Position not found'}

        position = self.positions[product_id]

        # Get current price
        product_info = await self.trading_manager.get_product_info(product_id)
        current_price = product_info.get('current_price', position.entry_price)

        return await self.execute_automated_exit(product_id, current_price, "Manual market sell")

    async def set_limit_order(self, product_id: str, limit_price: float) -> Dict:
        """
        Set custom limit order and enter hibernation mode
        """
        if product_id not in self.positions:
            return {'success': False, 'message': 'Position not found'}

        position = self.positions[product_id]

        try:
            # Cancel any existing orders
            # TODO: Implement order cancellation

            # Place limit order
            # TODO: Implement limit order via TradingManager
            # For now, just update the mode

            position.mode = "manual_limit_order"
            position.status = "hibernating"
            position.limit_order_id = "pending_implementation"
            self._persist_position(position)

            logger.info(f"💤 {product_id} entering hibernation mode with limit order @ ${limit_price:.6f}")

            return {
                'success': True,
                'message': f'Limit order set at ${limit_price:.6f}',
                'position': position
            }

        except Exception as e:
            logger.error(f"Error setting limit order: {e}")
            return {'success': False, 'message': str(e)}

    async def cancel_limit_order(self, product_id: str) -> Dict:
        """
        Cancel limit order and resume automated trading
        """
        if product_id not in self.positions:
            return {'success': False, 'message': 'Position not found'}

        position = self.positions[product_id]

        try:
            # Cancel any pending orders on Coinbase
            # TODO: Implement actual order cancellation via TradingManager
            if position.limit_order_id and position.limit_order_id != "pending_implementation":
                # await self.trading_manager.cancel_order(position.limit_order_id)
                pass

            # Resume automated mode
            position.mode = "automated"
            position.status = "active"
            position.limit_order_id = None
            self._persist_position(position)

            logger.info(f"🤖 {product_id} resumed automated trading (limit order cancelled)")

            return {
                'success': True,
                'message': 'Limit order cancelled, automated trading resumed',
                'position': position
            }

        except Exception as e:
            logger.error(f"Error cancelling limit order: {e}")
            return {'success': False, 'message': str(e)}

    def get_active_product_ids(self) -> list:
        """Get list of all active position product IDs"""
        return [pid for pid, pos in self.positions.items() if pos.status in ['active', 'hibernating']]

    def get_position(self, product_id: str) -> Optional[LivePosition]:
        """Get position by product ID"""
        return self.positions.get(product_id)

    def _record_trade_entry(self, position: LivePosition, buy_fee: float):
        """Record trade entry in database"""
        cursor = self.db.cursor()
        cursor.execute("""
            INSERT INTO live_trades
            (product_id, order_id, entry_time, entry_price, quantity, cost_basis,
             buy_fee, peak_price, status, mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            position.product_id, position.order_id, position.entry_time,
            position.entry_price, position.quantity, position.cost_basis,
            buy_fee, position.peak_price, 'open', position.mode
        ))
        self.db.commit()

    def _record_trade_exit(self, position: LivePosition, exit_price: float,
                           pnl_data: Dict, reason: str):
        """Record trade exit in database"""
        cursor = self.db.cursor()
        cursor.execute("""
            UPDATE live_trades
            SET exit_time = ?, exit_price = ?, gross_proceeds = ?, net_proceeds = ?,
                sell_fee = ?, pnl = ?, pnl_percent = ?, peak_price = ?,
                status = ?, exit_reason = ?
            WHERE product_id = ? AND status = 'open'
        """, (
            datetime.now().isoformat(), exit_price, pnl_data['gross_proceeds'],
            pnl_data['net_proceeds'], pnl_data['sell_fee'], pnl_data['pnl'],
            pnl_data['pnl_percent'], position.peak_price, 'closed', reason,
            position.product_id
        ))
        self.db.commit()
