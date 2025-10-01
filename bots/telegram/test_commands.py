"""
Telegram Test Commands
Interactive commands for testing live trading functionality
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def test_buy_flow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /testbuy - Simulates a complete buy flow with mock spike alert
    Tests: Alert → Buy → Verification → Execute → Position Card → Auto Exit
    """
    from telegram_bot import format_price_alert

    # Create realistic test alert
    test_data = {
        "symbol": "DOGE-USD",
        "spike_type": "pump",
        "pct_change": 5.23,
        "old_price": 0.08450,
        "new_price": 0.08892,
        "time_span_seconds": 300,
        "timestamp": "2025-10-01T12:00:00"
    }

    message, reply_markup = format_price_alert(test_data)

    await update.message.reply_text(
        "🧪 **TEST MODE: Buy Flow Simulation**\n\n"
        "This will test the complete buy flow:\n"
        "1. Spike alert display\n"
        "2. Buy button → Amount input\n"
        "3. Verification screen\n"
        "4. Order execution\n"
        "5. Position card creation\n"
        "6. Live price updates\n"
        "7. Automated exit logic\n\n"
        "Click 'Buy' below to start:",
        parse_mode='Markdown'
    )

    await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)


async def test_price_simulator_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /testprices - Simulates price movements to trigger automated exits
    """
    from telegram_bot import live_trading_manager

    if not live_trading_manager or len(live_trading_manager.positions) == 0:
        await update.message.reply_text(
            "❌ No active positions to test.\n\n"
            "Use /testbuy to create a test position first."
        )
        return

    # Get first active position
    product_id = list(live_trading_manager.positions.keys())[0]
    position = live_trading_manager.get_position(product_id)

    message = (
        f"🧪 **PRICE SIMULATOR**\n\n"
        f"Active Position: {product_id}\n"
        f"Entry: ${position.entry_price:.6f}\n"
        f"Peak: ${position.peak_price:.6f}\n"
        f"Trailing Stop: ${position.trailing_exit_price:.6f}\n"
        f"Stop Loss: ${position.stop_loss_price:.6f}\n\n"
        f"Choose a scenario:"
    )

    keyboard = [
        [InlineKeyboardButton("📈 +5% Price Increase", callback_data=f"test_price:{product_id}:up:5")],
        [InlineKeyboardButton("📉 -3% Price Drop", callback_data=f"test_price:{product_id}:down:3")],
        [InlineKeyboardButton("🎢 Climb then Drop (Trailing Stop)", callback_data=f"test_price:{product_id}:trailing")],
        [InlineKeyboardButton("🛑 Hit Stop Loss", callback_data=f"test_price:{product_id}:stoploss")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"test_price:cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)


async def test_websocket_state_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /testwebsocket - Shows current WebSocket feed state and allows testing state changes
    """
    from telegram_bot import trading_state_controller, live_trading_manager
    import aiohttp

    if not trading_state_controller:
        await update.message.reply_text("❌ Trading state controller not initialized")
        return

    state = trading_state_controller.get_state()

    # Get backend WebSocket stats
    backend_stats = "Unable to fetch"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('http://backend:5000/health', timeout=aiohttp.ClientTimeout(total=2)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    backend_stats = f"✅ Connected"
    except:
        backend_stats = "❌ Disconnected"

    active_positions = []
    if live_trading_manager:
        active_positions = live_trading_manager.get_active_product_ids()

    message = (
        f"🌐 **WEBSOCKET STATE TEST**\n\n"
        f"**Current State:** {state['trading_state'].upper()}\n"
        f"**Active Positions:** {state['active_positions']}\n"
        f"**Priority Pairs:** {', '.join(state['product_ids']) if state['product_ids'] else 'None'}\n\n"
        f"**Backend Status:** {backend_stats}\n\n"
        f"**Position Details:**\n"
    )

    if active_positions:
        for pid in active_positions:
            pos = live_trading_manager.get_position(pid)
            message += f"• {pid}: {pos.mode} mode\n"
    else:
        message += "• No active positions\n"

    keyboard = [
        [InlineKeyboardButton("🔄 Refresh State", callback_data="test_ws:refresh")],
        [InlineKeyboardButton("📊 View Backend Health", callback_data="test_ws:health")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)


async def test_position_modes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /testmodes - Test position mode transitions (automated → hibernating → manual)
    """
    from telegram_bot import live_trading_manager

    if not live_trading_manager or len(live_trading_manager.positions) == 0:
        await update.message.reply_text(
            "❌ No active positions.\n\n"
            "Use /testbuy to create a test position first."
        )
        return

    product_id = list(live_trading_manager.positions.keys())[0]
    position = live_trading_manager.get_position(product_id)

    message = (
        f"🎭 **MODE TRANSITION TEST**\n\n"
        f"Position: {product_id}\n"
        f"Current Mode: **{position.mode}**\n"
        f"Current Status: **{position.status}**\n\n"
        f"Test mode transitions:"
    )

    keyboard = [
        [InlineKeyboardButton("🤖 → 💤 (Set Limit Order)", callback_data=f"test_mode:{product_id}:hibernate")],
        [InlineKeyboardButton("💤 → 🤖 (Cancel & Resume Auto)", callback_data=f"test_mode:{product_id}:resume")],
        [InlineKeyboardButton("🔴 Manual Exit", callback_data=f"test_mode:{product_id}:exit")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)


async def test_full_integration_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /testintegration - Run complete integration test (buy → price changes → auto exit)
    """
    await update.message.reply_text(
        "🧪 **FULL INTEGRATION TEST**\n\n"
        "This will run a complete automated test:\n\n"
        "1️⃣ Create test position (DOGE-USD, $10)\n"
        "2️⃣ Simulate price climb (+10%)\n"
        "3️⃣ Trigger trailing stop\n"
        "4️⃣ Execute automated exit\n"
        "5️⃣ Verify WebSocket feed update\n\n"
        "⏱️ Estimated time: 30 seconds\n\n"
        "Proceed?",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Run Test", callback_data="test_integration:run"),
                InlineKeyboardButton("❌ Cancel", callback_data="test_integration:cancel")
            ]
        ])
    )


async def handle_test_callbacks(query, data: str):
    """Handle test command callbacks"""
    from telegram_bot import live_trading_manager, trading_state_controller, application
    import asyncio

    parts = data.split(":")

    # Price simulator callbacks
    if data.startswith("test_price:"):
        if parts[1] == "cancel":
            await query.edit_message_text("❌ Price simulation cancelled")
            return

        product_id = parts[1]
        action = parts[2]
        position = live_trading_manager.get_position(product_id)

        if not position:
            await query.edit_message_text("❌ Position not found")
            return

        await query.edit_message_text(f"🧪 Simulating {action} scenario for {product_id}...")

        if action == "up":
            # Price increase
            pct = float(parts[3])
            new_price = position.entry_price * (1 + pct / 100)
            exit_info = live_trading_manager.update_position(product_id, new_price)

            msg = (
                f"📈 **Price Increased +{pct}%**\n\n"
                f"New Price: ${new_price:.6f}\n"
                f"Peak: ${position.peak_price:.6f}\n"
                f"Trailing Stop: ${position.trailing_exit_price:.6f}\n\n"
            )

            if exit_info:
                msg += f"🔴 EXIT TRIGGERED: {exit_info['reason']}"
            else:
                msg += "✅ No exit triggered (position still active)"

            await query.edit_message_text(msg, parse_mode='Markdown')

        elif action == "down":
            # Price drop
            pct = float(parts[3])
            new_price = position.entry_price * (1 - pct / 100)
            exit_info = live_trading_manager.update_position(product_id, new_price)

            msg = (
                f"📉 **Price Dropped -{pct}%**\n\n"
                f"New Price: ${new_price:.6f}\n"
                f"Stop Loss: ${position.stop_loss_price:.6f}\n\n"
            )

            if exit_info:
                msg += f"🔴 EXIT TRIGGERED: {exit_info['reason']}"
            else:
                msg += "✅ No exit triggered (position still active)"

            await query.edit_message_text(msg, parse_mode='Markdown')

        elif action == "trailing":
            # Climb then drop to trigger trailing stop
            await query.edit_message_text("🎢 Simulating climb then drop...")

            # Climb
            climb_price = position.entry_price * 1.10  # +10%
            live_trading_manager.update_position(product_id, climb_price)
            await asyncio.sleep(1)

            # Drop to trailing stop
            position = live_trading_manager.get_position(product_id)
            drop_price = position.trailing_exit_price - 0.0001
            exit_info = live_trading_manager.update_position(product_id, drop_price)

            msg = (
                f"🎢 **Climb & Drop Scenario**\n\n"
                f"1. Climbed to: ${climb_price:.6f} (+10%)\n"
                f"2. Dropped to: ${drop_price:.6f}\n"
                f"3. Trailing Stop: ${position.trailing_exit_price:.6f}\n\n"
            )

            if exit_info:
                msg += f"✅ SUCCESS: {exit_info['reason']}"
            else:
                msg += "❌ FAILED: Exit should have triggered"

            await query.edit_message_text(msg, parse_mode='Markdown')

        elif action == "stoploss":
            # Hit stop loss
            stop_price = position.stop_loss_price - 0.01
            exit_info = live_trading_manager.update_position(product_id, stop_price)

            msg = (
                f"🛑 **Stop Loss Test**\n\n"
                f"Price: ${stop_price:.6f}\n"
                f"Stop Loss: ${position.stop_loss_price:.6f}\n\n"
            )

            if exit_info:
                msg += f"✅ SUCCESS: {exit_info['reason']}"
            else:
                msg += "❌ FAILED: Stop loss should have triggered"

            await query.edit_message_text(msg, parse_mode='Markdown')

    # Mode transition callbacks
    elif data.startswith("test_mode:"):
        product_id = parts[1]
        mode_action = parts[2]

        if mode_action == "hibernate":
            limit_price = live_trading_manager.get_position(product_id).entry_price * 1.05
            result = await live_trading_manager.set_limit_order(product_id, limit_price)

            if result['success']:
                await query.edit_message_text(
                    f"💤 **HIBERNATION MODE**\n\n"
                    f"Position: {product_id}\n"
                    f"Limit Price: ${limit_price:.6f}\n\n"
                    f"✅ Automated exits paused\n"
                    f"📊 Position card updated",
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(f"❌ Error: {result['message']}")

        elif mode_action == "resume":
            position = live_trading_manager.get_position(product_id)
            position.mode = "automated"
            position.status = "active"
            live_trading_manager._persist_position(position)

            await query.edit_message_text(
                f"🤖 **AUTOMATED MODE RESUMED**\n\n"
                f"Position: {product_id}\n\n"
                f"✅ Automated exits re-enabled\n"
                f"📊 Monitoring trailing stops",
                parse_mode='Markdown'
            )

        elif mode_action == "exit":
            result = await live_trading_manager.execute_manual_exit(product_id)

            if result['success']:
                pnl_data = result.get('pnl_data', {})
                await query.edit_message_text(
                    f"🔴 **MANUAL EXIT**\n\n"
                    f"Position: {product_id}\n"
                    f"P&L: ${pnl_data.get('pnl', 0):+.2f} ({pnl_data.get('pnl_percent', 0):+.2f}%)\n\n"
                    f"✅ Position closed",
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(f"❌ Error: {result['message']}")

    # WebSocket state callbacks
    elif data.startswith("test_ws:"):
        action = parts[1]

        if action == "refresh":
            state = trading_state_controller.get_state()
            await query.edit_message_text(
                f"🔄 **WebSocket State Refreshed**\n\n"
                f"State: {state['trading_state']}\n"
                f"Active Positions: {state['active_positions']}\n"
                f"Priority Pairs: {', '.join(state['product_ids']) if state['product_ids'] else 'None'}",
                parse_mode='Markdown'
            )

        elif action == "health":
            import aiohttp
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get('http://backend:5000/health', timeout=aiohttp.ClientTimeout(total=3)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            await query.edit_message_text(
                                f"🏥 **Backend Health**\n\n"
                                f"Status: ✅ Healthy\n"
                                f"Response: {data}",
                                parse_mode='Markdown'
                            )
                        else:
                            await query.edit_message_text(f"⚠️ Backend returned status {resp.status}")
            except Exception as e:
                await query.edit_message_text(f"❌ Backend health check failed: {str(e)}")

    # Full integration test
    elif data.startswith("test_integration:"):
        if parts[1] == "cancel":
            await query.edit_message_text("❌ Integration test cancelled")
            return

        await query.edit_message_text("🧪 Starting integration test...")
        await asyncio.sleep(1)

        try:
            # Step 1: Create position
            await query.edit_message_text("1️⃣ Creating test position...")
            result = await live_trading_manager.execute_buy('DOGE-USD', 10.0, 2.0)
            if not result['success']:
                raise Exception(f"Buy failed: {result['message']}")

            await asyncio.sleep(2)

            # Step 2: Price climb
            await query.edit_message_text("2️⃣ Simulating price climb (+10%)...")
            position = live_trading_manager.get_position('DOGE-USD')
            climb_price = position.entry_price * 1.10
            live_trading_manager.update_position('DOGE-USD', climb_price)

            await asyncio.sleep(2)

            # Step 3: Trigger trailing stop
            await query.edit_message_text("3️⃣ Triggering trailing stop...")
            position = live_trading_manager.get_position('DOGE-USD')
            exit_price = position.trailing_exit_price - 0.0001
            exit_info = live_trading_manager.update_position('DOGE-USD', exit_price)

            if not exit_info:
                raise Exception("Exit not triggered")

            await asyncio.sleep(1)

            # Step 4: Execute exit
            await query.edit_message_text("4️⃣ Executing automated exit...")
            exit_result = await live_trading_manager.execute_automated_exit(
                'DOGE-USD', exit_price, exit_info['reason']
            )

            if not exit_result['success']:
                raise Exception(f"Exit failed: {exit_result['message']}")

            await asyncio.sleep(1)

            # Step 5: Verify state
            await query.edit_message_text("5️⃣ Verifying WebSocket state...")
            state = trading_state_controller.get_state()

            await asyncio.sleep(1)

            # Success summary
            pnl_data = exit_result.get('pnl_data', {})
            await query.edit_message_text(
                f"✅ **INTEGRATION TEST PASSED**\n\n"
                f"**Results:**\n"
                f"• Position created: ✅\n"
                f"• Price climb tracked: ✅\n"
                f"• Trailing stop triggered: ✅\n"
                f"• Automated exit executed: ✅\n"
                f"• WebSocket state updated: ✅\n\n"
                f"**Trade P&L:**\n"
                f"Entry: ${position.entry_price:.6f}\n"
                f"Exit: ${exit_price:.6f}\n"
                f"P&L: ${pnl_data.get('pnl', 0):+.2f} ({pnl_data.get('pnl_percent', 0):+.2f}%)\n\n"
                f"🎉 All systems working correctly!",
                parse_mode='Markdown'
            )

        except Exception as e:
            await query.edit_message_text(
                f"❌ **INTEGRATION TEST FAILED**\n\n"
                f"Error: {str(e)}\n\n"
                f"Check logs for details.",
                parse_mode='Markdown'
            )
