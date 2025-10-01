#!/usr/bin/env python3
"""
Live Trading Integration Test Suite
Tests the entire workflow from buy to automated exit without real trading
"""
import asyncio
import logging
import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict

# Mock Telegram imports before importing modules
sys.modules['telegram'] = MagicMock()
sys.modules['telegram.ext'] = MagicMock()

from live_trading_manager import LiveTradingManager, LivePosition
from trading_state import TradingStateController

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MockTradingManager:
    """Mock TradingManager for testing"""

    def __init__(self):
        self.orders_created = []
        self.current_prices = {
            'BTC-USD': 65000.0,
            'ETH-USD': 3500.0,
            'DOGE-USD': 0.085
        }

    async def get_product_info(self, product_id: str) -> Dict:
        return {
            'product_id': product_id,
            'current_price': self.current_prices.get(product_id, 100.0),
            'quote_currency': 'USD'
        }

    async def create_market_buy_order(self, product_id: str, quote_size: str, position_percentage: float) -> Dict:
        order_id = f"mock_order_{len(self.orders_created) + 1}"
        self.orders_created.append({
            'type': 'buy',
            'product_id': product_id,
            'quote_size': quote_size,
            'order_id': order_id
        })
        return {
            'success': True,
            'order_id': order_id,
            'message': 'Mock order created'
        }

    async def create_market_sell_order(self, product_id: str, base_size: str) -> Dict:
        order_id = f"mock_sell_{len(self.orders_created) + 1}"
        self.orders_created.append({
            'type': 'sell',
            'product_id': product_id,
            'base_size': base_size,
            'order_id': order_id
        })
        return {
            'success': True,
            'order_id': order_id,
            'message': 'Mock sell order created'
        }

    def set_price(self, product_id: str, price: float):
        """Set current price for testing"""
        self.current_prices[product_id] = price


class LiveTradingTestSuite:
    """Comprehensive test suite for live trading integration"""

    def __init__(self):
        self.mock_trading_manager = MockTradingManager()
        self.live_trading_manager = None
        self.trading_state_controller = None
        # Use proper temp directory for Windows/Linux compatibility
        import tempfile
        self.test_db_path = os.path.join(tempfile.gettempdir(), 'test_live_trading.db')
        self.passed_tests = 0
        self.failed_tests = 0

    async def setup(self):
        """Initialize test environment"""
        logger.info("=" * 80)
        logger.info("LIVE TRADING INTEGRATION TEST SUITE")
        logger.info("=" * 80)

        # Clean up test database
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)

        # Initialize managers
        self.live_trading_manager = LiveTradingManager(
            self.mock_trading_manager,
            db_path=self.test_db_path
        )

        self.trading_state_controller = TradingStateController("http://mock-backend:5000")

        logger.info("✅ Test environment initialized")

    def assert_equal(self, actual, expected, test_name: str):
        """Assert equality and track results"""
        if actual == expected:
            self.passed_tests += 1
            logger.info(f"✅ PASS: {test_name}")
            return True
        else:
            self.failed_tests += 1
            logger.error(f"❌ FAIL: {test_name}")
            logger.error(f"   Expected: {expected}")
            logger.error(f"   Got: {actual}")
            return False

    def assert_true(self, condition, test_name: str):
        """Assert condition is true"""
        if condition:
            self.passed_tests += 1
            logger.info(f"✅ PASS: {test_name}")
            return True
        else:
            self.failed_tests += 1
            logger.error(f"❌ FAIL: {test_name}")
            return False

    async def test_1_buy_order_execution(self):
        """Test 1: Execute buy order and create position"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 1: Buy Order Execution & Position Creation")
        logger.info("=" * 80)

        result = await self.live_trading_manager.execute_buy(
            product_id='DOGE-USD',
            quote_size=10.0,  # $10 purchase
            position_percentage=2.0
        )

        self.assert_true(result['success'], "Buy order executes successfully")
        self.assert_true(result['position'] is not None, "Position object created")
        self.assert_equal(result['position'].product_id, 'DOGE-USD', "Correct product_id")
        self.assert_equal(result['position'].mode, 'automated', "Position starts in automated mode")
        self.assert_true(result['position'].stop_loss_price > 0, "Stop loss price set")
        self.assert_true(result['position'].min_exit_price > result['position'].entry_price,
                         "Min exit price > entry price (profit target)")

        # Verify position is tracked
        self.assert_equal(len(self.live_trading_manager.positions), 1, "One position tracked")
        self.assert_true('DOGE-USD' in self.live_trading_manager.positions, "Position stored in dict")

        logger.info(f"📊 Position Details:")
        logger.info(f"   Entry Price: ${result['position'].entry_price:.6f}")
        logger.info(f"   Quantity: {result['position'].quantity:.8f}")
        logger.info(f"   Cost Basis: ${result['position'].cost_basis:.2f}")
        logger.info(f"   Min Exit: ${result['position'].min_exit_price:.6f}")
        logger.info(f"   Stop Loss: ${result['position'].stop_loss_price:.6f}")

    async def test_2_price_update_no_exit(self):
        """Test 2: Price update that doesn't trigger exit"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 2: Price Update (No Exit Trigger)")
        logger.info("=" * 80)

        # Small price increase (shouldn't trigger exit)
        new_price = 0.087  # +2.35% from 0.085
        self.mock_trading_manager.set_price('DOGE-USD', new_price)

        exit_info = self.live_trading_manager.update_position('DOGE-USD', new_price)

        self.assert_true(exit_info is None, "No exit triggered on small price increase")

        position = self.live_trading_manager.get_position('DOGE-USD')
        self.assert_true(position.peak_price >= new_price, "Peak price updated")
        logger.info(f"📈 Peak Price: ${position.peak_price:.6f}")
        logger.info(f"📊 Trailing Stop: ${position.trailing_exit_price:.6f}")

    async def test_3_peak_price_tracking(self):
        """Test 3: Peak price tracking and trailing stop adjustment"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 3: Peak Price Tracking & Trailing Stop")
        logger.info("=" * 80)

        # Simulate price climbing
        prices = [0.088, 0.090, 0.092, 0.095]

        for price in prices:
            self.mock_trading_manager.set_price('DOGE-USD', price)
            exit_info = self.live_trading_manager.update_position('DOGE-USD', price)
            self.assert_true(exit_info is None, f"No exit at ${price:.6f}")

        position = self.live_trading_manager.get_position('DOGE-USD')
        self.assert_equal(position.peak_price, 0.095, "Peak price tracks highest price")

        # Trailing stop should be 1.5% below peak
        expected_trailing = 0.095 * (1 - 0.015)
        self.assert_true(abs(position.trailing_exit_price - expected_trailing) < 0.0001,
                         "Trailing stop is 1.5% below peak")

        logger.info(f"📈 Price climbed: 0.085 → 0.095 (+11.76%)")
        logger.info(f"📊 Peak: ${position.peak_price:.6f}")
        logger.info(f"📉 Trailing Stop: ${position.trailing_exit_price:.6f}")

    async def test_4_trailing_stop_exit(self):
        """Test 4: Trailing stop triggers exit"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 4: Trailing Stop Exit Trigger")
        logger.info("=" * 80)

        position = self.live_trading_manager.get_position('DOGE-USD')
        trailing_stop = position.trailing_exit_price

        # Drop price below trailing stop
        exit_price = trailing_stop - 0.001
        self.mock_trading_manager.set_price('DOGE-USD', exit_price)

        exit_info = self.live_trading_manager.update_position('DOGE-USD', exit_price)

        self.assert_true(exit_info is not None, "Exit triggered")
        self.assert_true(exit_info['should_exit'], "Should exit flag set")
        self.assert_true('Trailing stop' in exit_info['reason'], "Reason mentions trailing stop")

        logger.info(f"🔴 Exit triggered at ${exit_price:.6f}")
        logger.info(f"📋 Reason: {exit_info['reason']}")

    async def test_5_automated_exit_execution(self):
        """Test 5: Execute automated exit (sell)"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 5: Automated Exit Execution")
        logger.info("=" * 80)

        position = self.live_trading_manager.get_position('DOGE-USD')
        exit_price = 0.093

        result = await self.live_trading_manager.execute_automated_exit(
            product_id='DOGE-USD',
            exit_price=exit_price,
            reason='Trailing stop hit'
        )

        self.assert_true(result['success'], "Automated exit succeeds")
        self.assert_true('pnl_data' in result, "P&L data included")

        pnl_data = result['pnl_data']
        logger.info(f"💰 Exit Results:")
        logger.info(f"   Exit Price: ${exit_price:.6f}")
        logger.info(f"   Gross Proceeds: ${pnl_data['gross_proceeds']:.2f}")
        logger.info(f"   Sell Fee: ${pnl_data['sell_fee']:.2f}")
        logger.info(f"   Net Proceeds: ${pnl_data['net_proceeds']:.2f}")
        logger.info(f"   P&L: ${pnl_data['pnl']:+.2f} ({pnl_data['pnl_percent']:+.2f}%)")

        # Verify position removed
        self.assert_equal(len(self.live_trading_manager.positions), 0, "Position removed after exit")
        self.assert_true('DOGE-USD' not in self.live_trading_manager.positions, "Position not in dict")

    async def test_6_stop_loss_trigger(self):
        """Test 6: Stop loss triggers immediate exit"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 6: Stop Loss Trigger")
        logger.info("=" * 80)

        # Create new position
        await self.live_trading_manager.execute_buy(
            product_id='ETH-USD',
            quote_size=20.0,
            position_percentage=2.0
        )

        position = self.live_trading_manager.get_position('ETH-USD')
        entry_price = position.entry_price
        stop_loss = position.stop_loss_price

        logger.info(f"📊 Entry: ${entry_price:.2f}, Stop Loss: ${stop_loss:.2f}")

        # Drop below stop loss
        exit_price = stop_loss - 10
        self.mock_trading_manager.set_price('ETH-USD', exit_price)

        exit_info = self.live_trading_manager.update_position('ETH-USD', exit_price)

        self.assert_true(exit_info is not None, "Exit triggered by stop loss")
        self.assert_true('Stop loss' in exit_info['reason'], "Reason mentions stop loss")

        logger.info(f"🛑 Stop loss hit at ${exit_price:.2f}")
        logger.info(f"📋 Reason: {exit_info['reason']}")

        # Clean up
        await self.live_trading_manager.execute_automated_exit('ETH-USD', exit_price, exit_info['reason'])

    async def test_7_hibernation_mode(self):
        """Test 7: Limit order hibernation mode"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 7: Limit Order Hibernation Mode")
        logger.info("=" * 80)

        # Create position
        await self.live_trading_manager.execute_buy(
            product_id='BTC-USD',
            quote_size=50.0,
            position_percentage=2.0
        )

        # Set limit order (enter hibernation)
        result = await self.live_trading_manager.set_limit_order('BTC-USD', 68000.0)

        self.assert_true(result['success'], "Limit order set successfully")

        position = self.live_trading_manager.get_position('BTC-USD')
        self.assert_equal(position.mode, 'manual_limit_order', "Mode changed to manual_limit_order")
        self.assert_equal(position.status, 'hibernating', "Status changed to hibernating")

        logger.info(f"💤 Position entered hibernation")
        logger.info(f"   Mode: {position.mode}")
        logger.info(f"   Status: {position.status}")

        # Verify automated logic is skipped
        exit_info = self.live_trading_manager.update_position('BTC-USD', 50000.0)
        self.assert_true(exit_info is None, "Automated logic skipped in hibernation")

        logger.info(f"✅ Automated exits disabled during hibernation")

        # Clean up
        position.mode = 'automated'  # Re-enable to test exit
        await self.live_trading_manager.execute_manual_exit('BTC-USD')

    async def test_8_multi_position_tracking(self):
        """Test 8: Multiple simultaneous positions"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 8: Multi-Position Tracking")
        logger.info("=" * 80)

        # Create 3 positions
        products = [
            ('BTC-USD', 100.0),
            ('ETH-USD', 50.0),
            ('DOGE-USD', 10.0)
        ]

        for product_id, amount in products:
            await self.live_trading_manager.execute_buy(
                product_id=product_id,
                quote_size=amount,
                position_percentage=2.0
            )

        active_products = self.live_trading_manager.get_active_product_ids()
        self.assert_equal(len(active_products), 3, "Three positions tracked")
        self.assert_equal(len(self.live_trading_manager.positions), 3, "All positions in dict")

        logger.info(f"📊 Active Positions: {', '.join(active_products)}")

        # Update each position independently
        for product_id in active_products:
            position = self.live_trading_manager.get_position(product_id)
            new_price = position.entry_price * 1.02  # +2%
            exit_info = self.live_trading_manager.update_position(product_id, new_price)
            self.assert_true(exit_info is None, f"No exit for {product_id} at +2%")

        logger.info(f"✅ All positions updated independently")

        # Clean up
        for product_id in list(active_products):
            await self.live_trading_manager.execute_manual_exit(product_id)

    async def test_9_position_persistence(self):
        """Test 9: Position restoration from database"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 9: Position Persistence & Restoration")
        logger.info("=" * 80)

        # Create position
        result = await self.live_trading_manager.execute_buy(
            product_id='DOGE-USD',
            quote_size=15.0,
            position_percentage=2.0
        )

        original_position = result['position']
        logger.info(f"📊 Created position: {original_position.product_id}")

        # Simulate bot restart - create new manager instance
        new_manager = LiveTradingManager(
            self.mock_trading_manager,
            db_path=self.test_db_path
        )

        # Verify position restored
        self.assert_equal(len(new_manager.positions), 1, "Position restored from DB")
        restored_position = new_manager.get_position('DOGE-USD')
        self.assert_true(restored_position is not None, "Position found")
        self.assert_equal(restored_position.product_id, original_position.product_id, "Product ID matches")
        self.assert_equal(restored_position.entry_price, original_position.entry_price, "Entry price matches")
        self.assert_equal(restored_position.mode, original_position.mode, "Mode matches")

        logger.info(f"✅ Position restored successfully after 'restart'")
        logger.info(f"   Product: {restored_position.product_id}")
        logger.info(f"   Entry: ${restored_position.entry_price:.6f}")
        logger.info(f"   Mode: {restored_position.mode}")

        # Clean up
        self.live_trading_manager = new_manager  # Use new manager
        await self.live_trading_manager.execute_manual_exit('DOGE-USD')

    async def test_10_trading_state_controller(self):
        """Test 10: Trading state and WebSocket feed management"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 10: Trading State Controller")
        logger.info("=" * 80)

        # Mock the HTTP requests
        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={'success': True})
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock()

            mock_session.return_value.__aenter__ = AsyncMock()
            mock_session.return_value.__aexit__ = AsyncMock()
            mock_session.return_value.put = MagicMock(return_value=mock_response)
            mock_session.return_value.delete = MagicMock(return_value=mock_response)

            # Test idle state (no positions)
            await self.trading_state_controller.set_trading_state([])
            self.assert_equal(self.trading_state_controller.trading_state, 'idle', "Idle with 0 positions")

            # Test active state (1 position)
            await self.trading_state_controller.add_position('BTC-USD')
            self.assert_equal(self.trading_state_controller.trading_state, 'active', "Active with 1 position")
            self.assert_true('BTC-USD' in self.trading_state_controller.active_product_ids,
                             "BTC-USD in active list")

            # Test multi-active state (2+ positions)
            await self.trading_state_controller.add_position('ETH-USD')
            self.assert_equal(self.trading_state_controller.trading_state, 'multi_active',
                              "Multi-active with 2 positions")
            self.assert_equal(len(self.trading_state_controller.active_product_ids), 2,
                              "Two products tracked")

            # Test removal
            await self.trading_state_controller.remove_position('BTC-USD')
            self.assert_equal(self.trading_state_controller.trading_state, 'active', "Back to active (1 position)")

            await self.trading_state_controller.remove_position('ETH-USD')
            self.assert_equal(self.trading_state_controller.trading_state, 'idle', "Back to idle (0 positions)")

        logger.info(f"✅ State transitions: idle → active → multi_active → active → idle")

    async def run_all_tests(self):
        """Run all tests"""
        await self.setup()

        tests = [
            self.test_1_buy_order_execution,
            self.test_2_price_update_no_exit,
            self.test_3_peak_price_tracking,
            self.test_4_trailing_stop_exit,
            self.test_5_automated_exit_execution,
            self.test_6_stop_loss_trigger,
            self.test_7_hibernation_mode,
            self.test_8_multi_position_tracking,
            self.test_9_position_persistence,
            self.test_10_trading_state_controller,
        ]

        for test in tests:
            try:
                await test()
            except Exception as e:
                self.failed_tests += 1
                logger.error(f"❌ TEST EXCEPTION: {test.__name__}")
                logger.error(f"   Error: {e}", exc_info=True)

        # Print summary
        logger.info("\n" + "=" * 80)
        logger.info("TEST SUMMARY")
        logger.info("=" * 80)
        logger.info(f"✅ Passed: {self.passed_tests}")
        logger.info(f"❌ Failed: {self.failed_tests}")
        logger.info(f"📊 Total: {self.passed_tests + self.failed_tests}")

        if self.failed_tests == 0:
            logger.info("\n🎉 ALL TESTS PASSED! Live trading integration is working correctly.")
        else:
            logger.error(f"\n⚠️  {self.failed_tests} test(s) failed. Review errors above.")

        # Cleanup (close DB connections first)
        if self.live_trading_manager and hasattr(self.live_trading_manager, 'db'):
            self.live_trading_manager.db.close()

        try:
            if os.path.exists(self.test_db_path):
                os.remove(self.test_db_path)
        except PermissionError:
            # File locked on Windows, skip cleanup
            pass

        return self.failed_tests == 0


async def main():
    """Main test runner"""
    test_suite = LiveTradingTestSuite()
    success = await test_suite.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
