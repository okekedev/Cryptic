import asyncio
import logging
import signal
import sys
import os
import sqlite3
from datetime import datetime
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from aiohttp import web
import json

# Import our trading components
from trading_manager import TradingManager
from price_monitor import PriceMonitor
from socket_server import SpikeAlertSocketServer
from live_trading_manager import LiveTradingManager
from trading_state import TradingStateController

# Custom filter to block getUpdates logs
class IgnoreGetUpdatesFilter(logging.Filter):
    def filter(self, record):
        return "getUpdates" not in record.getMessage()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Suppress all the noisy loggers
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.INFO)
httpx_logger.addFilter(IgnoreGetUpdatesFilter())

logging.getLogger("httpcore").setLevel(logging.ERROR)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

# Get our logger
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))
SPIKE_SOCKET_PORT = int(os.getenv("SPIKE_SOCKET_PORT", "8081"))
ALERTS_CHANNEL_ID = os.getenv("ALERTS_CHANNEL_ID", "")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000")

# Global variables
application = None
alerts_enabled = True
trading_manager = None
price_monitor = None
spike_socket_server = None
live_trading_manager = None  # NEW: Manages live positions with automated logic
trading_state_controller = None  # NEW: Controls WebSocket feed switching

# User preferences for trading
DEFAULT_POSITION_PERCENTAGE = float(os.getenv('DEFAULT_POSITION_PERCENTAGE', '2.0'))

# Store pending custom buy contexts (chat_id -> product_id)
pending_custom_buys = {}

# Store active position cards (product_id -> card_info)
active_position_cards = {}

# Store pending limit order inputs (chat_id -> order_context)
pending_limit_orders = {}

# Database for position card persistence
DB_PATH = '/app/data/telegram_bot.db'
db_conn = None

# TODO: IMPLEMENT PERSISTENT POSITION CARD FEATURE
# =================================================
#
# FEATURE REQUIREMENTS:
# 1. When bot enters a trade, send a "Position Card" message to Telegram
# 2. Card stays at bottom of chat (pin it or edit existing message)
# 3. Update card in real-time with current price and P&L
# 4. Include action buttons: "Set Limit Order", "Sell at Market"
# 5. Add confirmation step for all trades to prevent accidents
# 6. Cancel any existing orders when new order is placed
#
# IMPLEMENTATION PLAN:
# ====================
#
# A. Data Structure:
# ------------------
# active_position_cards = {
#     'BTC-USD': {
#         'message_id': 12345,
#         'chat_id': '8005083771',
#         'entry_price': 64250.50,
#         'entry_time': '2025-09-30T16:00:00',
#         'quantity': 0.001,
#         'cost_basis': 64.25,
#         'current_price': 65100.00,
#         'pnl_usd': 0.85,
#         'pnl_pct': 1.32,
#         'last_update': time.time(),
#         'pending_action': None  # or {'type': 'limit_order', 'price': 66000, 'awaiting_confirm': True}
#     }
# }
#
# B. Position Card Message Format:
# --------------------------------
# 📊 ACTIVE POSITION: BTC-USD
#
# Entry: $64,250.50 @ 16:00:00
# Quantity: 0.001 BTC
# Cost Basis: $64.25
#
# Current Price: $65,100.00 📈
# Unrealized P&L: +$0.85 (+1.32%)
#
# [Set Limit Order] [Sell at Market]
#
# C. Real-time Price Updates:
# ---------------------------
# - Listen to ticker_update events from backend
# - Update card every 5 seconds (rate limit to avoid API spam)
# - Use bot.edit_message_text() to update in-place
# - Calculate P&L: (current_price - entry_price) * quantity - fees
#
# D. Button Handlers:
# -------------------
# Callback data format: "pos_action:{product_id}:{action}:{step}"
#
# Actions:
# - "pos_action:BTC-USD:limit:prompt" → Ask user to enter limit price
# - "pos_action:BTC-USD:limit:confirm:66000" → Show confirmation
# - "pos_action:BTC-USD:limit:execute:66000" → Place limit order
# - "pos_action:BTC-USD:market:confirm" → Show market sell confirmation
# - "pos_action:BTC-USD:market:execute" → Execute market sell
# - "pos_action:BTC-USD:cancel" → Cancel pending action
#
# E. Confirmation Flow:
# ---------------------
# Step 1: User clicks "Set Limit Order"
#   → Edit card to show: "Enter limit price (current: $65,100)"
#   → Wait for user message with price
#
# Step 2: User types "66000"
#   → Show confirmation:
#     "Confirm Limit Sell Order
#      Product: BTC-USD
#      Quantity: 0.001 BTC
#      Limit Price: $66,000.00
#      Expected Proceeds: $66.00
#      [✅ Confirm] [❌ Cancel]"
#
# Step 3: User clicks "✅ Confirm"
#   → Cancel any existing open orders for this product
#   → Place new limit order
#   → Update position card with order status
#
# F. Order Management:
# --------------------
# async def cancel_existing_orders(product_id: str):
#     """Cancel all open orders for a product"""
#     orders = await trading_manager.list_orders(product_id=product_id, status='OPEN')
#     for order in orders:
#         await trading_manager.cancel_order(order['order_id'])
#         logger.info(f"Cancelled order {order['order_id']} for {product_id}")
#
# G. Position Tracking Integration:
# ---------------------------------
# WEBHOOK ENDPOINTS (implemented below):
# - POST /position-opened: Create position card when trade enters
#   Payload: {product_id, entry_price, quantity, cost_basis}
# - POST /position-updated: Update card with current price
#   Payload: {product_id, current_price}
# - POST /position-closed: Archive card when position exits
#   Payload: {product_id, exit_price, exit_reason}
#
# PAPER TRADING BOT INTEGRATION:
# In paper_trading_bot.py, add webhook calls:
#
# 1. After opening position (in open_position method):
#    requests.post('http://telegram-bot:8080/position-opened', json={
#        'product_id': symbol,
#        'entry_price': entry_price,
#        'quantity': position.quantity,
#        'cost_basis': position.cost_basis
#    })
#
# 2. On ticker updates for open positions (in handle_ticker method):
#    if symbol in self.positions:
#        requests.post('http://telegram-bot:8080/position-updated', json={
#            'product_id': symbol,
#            'current_price': price
#        })
#
# 3. After closing position (in close_position method):
#    requests.post('http://telegram-bot:8080/position-closed', json={
#        'product_id': symbol,
#        'exit_price': exit_price,
#        'exit_reason': reason  # e.g., 'Trailing stop triggered', 'Min profit reached'
#    })
#
# - Store position cards in SQLite for persistence across restarts
#
# H. Message Handler for Limit Price Input:
# -----------------------------------------
# pending_limit_orders = {
#     'chat_id': {
#         'product_id': 'BTC-USD',
#         'message_id': 12345,  # Position card to update
#         'awaiting_price': True
#     }
# }
#
# async def handle_limit_price_input(update, context):
#     if chat_id in pending_limit_orders:
#         try:
#             price = float(update.message.text.strip())
#             # Show confirmation
#             await show_limit_order_confirmation(chat_id, product_id, price)
#         except ValueError:
#             await update.message.reply_text("Invalid price. Please enter a number.")
#
# I. Database Schema for Position Cards:
# --------------------------------------
# CREATE TABLE active_position_cards (
#     product_id TEXT PRIMARY KEY,
#     message_id INTEGER NOT NULL,
#     chat_id TEXT NOT NULL,
#     entry_price REAL NOT NULL,
#     entry_time TEXT NOT NULL,
#     quantity REAL NOT NULL,
#     cost_basis REAL NOT NULL,
#     last_update_time REAL NOT NULL,
#     status TEXT DEFAULT 'active'  -- 'active', 'pending_action', 'closed'
# );
#
# J. Safety Features:
# ------------------
# 1. Double confirmation for market sells (high risk)
# 2. Price validation for limit orders (warn if >10% from current)
# 3. Minimum order size validation ($10 minimum)
# 4. Daily trade limit enforcement
# 5. Rate limiting on card updates (max 1 update per 5 seconds)
# 6. Emergency stop button (accessible from position card)
#
# K. Implementation Files to Create/Modify:
# -----------------------------------------
# - telegram_bot.py: Add position card handlers
# - trading_manager.py: Add cancel_orders(), list_orders() methods
# - position_card_manager.py: NEW - Dedicated class for card lifecycle
# - confirmation_flow.py: NEW - Handle multi-step confirmations
#
# L. Testing Checklist:
# --------------------
# [ ] Create position card on trade entry
# [ ] Update card with real-time prices
# [ ] Set limit order with confirmation
# [ ] Sell at market with double confirmation
# [ ] Cancel existing orders before new order
# [ ] Handle network failures gracefully
# [ ] Persist cards across bot restarts
# [ ] Archive card on position close
# [ ] Multiple simultaneous positions (multiple cards)
# [ ] Button spam protection
#
# PRIORITY: HIGH - This is a critical UX feature for active trading
# COMPLEXITY: HIGH - Requires state management, real-time updates, and order orchestration
# ESTIMATED EFFORT: 8-12 hours for full implementation
# DEPENDENCIES: trading_manager methods, WebSocket price feed integration


def format_price_alert(data):
    """Format price alert with trading buttons"""
    emoji = "🚀" if data['spike_type'] == 'pump' else "📉"

    timestamp_str = ""
    if 'timestamp' in data:
        try:
            dt = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
            timestamp_str = f"*time (UTC):* {dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
        except:
            timestamp_str = f"*time:* {data['timestamp']}\n"

    if data.get('event_type') == 'momentum_end':
        # Momentum ended - no trading buttons needed
        return (
            f"{emoji} *MOMENTUM ENDED* {emoji}\n\n"
            f"*Symbol:* {data['symbol']}\n"
            f"*Peak gain:* {data['peak_change']:.2f}%\n"
            f"*Exit at:* {data['exit_change']:.2f}%\n"
            f"*Peak price:* ${data['peak_price']:.6f}\n"
            f"*Exit price:* ${data['new_price']:.6f}\n"
            f"*Duration:* {data['time_span_seconds']/60:.1f} min\n"
            f"{timestamp_str}"
        ), None
    else:
        # All other spike alerts (pump/dump) - include trading buttons
        message = (
            f"{emoji} *PRICE {data['spike_type'].upper()} ALERT* {emoji}\n\n"
            f"*Symbol:* {data['symbol']}\n"
            f"*Change:* {data['pct_change']:.2f}%\n"
            f"*Price:* ${data['old_price']:.6f} → ${data['new_price']:.6f}\n"
            f"*Time span:* {data['time_span_seconds']:.0f}s\n"
            f"{timestamp_str}"
        )

        # Create inline keyboard for all pump/dump alerts
        chart_url = f"https://www.coinbase.com/advanced-trade/spot/{data['symbol']}"
        keyboard = [
            [
                InlineKeyboardButton("🚀 Buy", callback_data=f"buy:{data['symbol']}"),
                InlineKeyboardButton("📊 Chart", url=chart_url)
            ],
            [
                InlineKeyboardButton("👁️ Ignore", callback_data=f"ignore:{data['symbol']}")
            ]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        return message, reply_markup


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    await update.message.reply_text(
        "🚀 *Crypto Trading Bot Started!*\n\n"
        "🔥 *Features:*\n"
        "• Real-time spike alerts with trading buttons\n"
        "• Direct Coinbase trading integration\n"
        "• Live position monitoring\n"
        "• Risk management & position sizing\n\n"
        "📋 *Basic Commands:*\n"
        "/balance - Check USD balance & reserves\n"
        "/positions - View open positions\n"
        "/orders - View open orders\n"
        "/status - Check bot status\n\n"
        "🔧 *Trading Management:*\n"
        "/limits - View daily trade limits\n"
        "/emergency_stop - Halt all trading\n"
        "/edit_order <id> <price> - Edit order\n"
        "/fills - View recent executions\n"
        "/trading_stats - Trading statistics\n\n"
        "⚙️ *System:*\n"
        "/enable - Enable alerts\n"
        "/disable - Disable alerts\n"
        "/test - Send test alert\n"
        "/help - Show this message",
        parse_mode='Markdown'
    )


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /balance command with enhanced reserve information"""
    try:
        balance_info = await trading_manager.get_account_balance('USD')

        if 'error' in balance_info:
            await update.message.reply_text(
                f"❌ Error getting balance: {balance_info['error']}",
                parse_mode='Markdown'
            )
            return

        message = (
            f"💰 *Account Balance*\n\n"
            f"*Total USD:* {balance_info['formatted']}\n"
            f"*Available for trading:* {balance_info['available_formatted']}\n"
            f"*Reserve ({trading_manager.reserve_percentage}%):* ${balance_info['reserve']:.2f}\n\n"
            f"💡 *Position Sizing:*\n"
            f"*Default:* {trading_manager.default_position_percentage}% of available\n"
            f"*Max:* {trading_manager.max_position_percentage}% of available"
        )

        await update.message.reply_text(message, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in balance command: {e}")
        await update.message.reply_text(
            "❌ Failed to fetch balance. Please check API credentials.",
            parse_mode='Markdown'
        )


async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /positions command"""
    try:
        positions = await trading_manager.get_positions()

        if not positions:
            await update.message.reply_text(
                "📊 *Open Positions*\n\nNo open positions found.",
                parse_mode='Markdown'
            )
            return

        message = "📊 *Open Positions*\n\n"

        for position in positions[:10]:  # Limit to 10 positions
            currency = position['currency']
            balance = position['balance']
            product_id = position['product_id']

            # Get current price if available
            current_price_data = await price_monitor.get_current_price(product_id)
            if current_price_data:
                current_price = current_price_data['price']
                value_usd = balance * current_price
                message += f"*{currency}:* {balance:.8f} (~${value_usd:.2f})\n"
            else:
                message += f"*{currency}:* {balance:.8f}\n"

        keyboard = [
            [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_positions")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error in positions command: {e}")
        await update.message.reply_text(
            "❌ Failed to fetch positions. Please try again.",
            parse_mode='Markdown'
        )


async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /orders command"""
    try:
        orders = await trading_manager.get_open_orders()

        if not orders:
            await update.message.reply_text(
                "📋 *Open Orders*\n\nNo open orders found.",
                parse_mode='Markdown'
            )
            return

        message = "📋 *Open Orders*\n\n"

        for order in orders[:5]:  # Limit to 5 orders
            order_id_short = order['order_id'][:8]
            product_id = order['product_id']
            side = order['side']
            size = float(order['size'])
            filled = float(order['filled_size'])
            price = order.get('price', 'Market')

            if price != 'Market':
                price = f"${float(price):,.6f}"

            message += (
                f"*{product_id}* - {side}\n"
                f"Size: {size:.8f} | Filled: {filled:.8f}\n"
                f"Price: {price} | ID: {order_id_short}...\n\n"
            )

        keyboard = [
            [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_orders")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error in orders command: {e}")
        await update.message.reply_text(
            "❌ Failed to fetch orders. Please try again.",
            parse_mode='Markdown'
        )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command"""
    global alerts_enabled
    status = "✅ Enabled" if alerts_enabled else "🔕 Disabled"

    # Get active monitoring stats
    active_positions = await price_monitor.get_active_positions()
    priority_pairs_count = len(active_positions)

    await update.message.reply_text(
        f"🤖 *Bot Status*\n\n"
        f"*Alerts:* {status}\n"
        f"*Webhook Port:* {WEBHOOK_PORT}\n"
        f"*Active Positions:* {priority_pairs_count}\n"
        f"*Trading:* ✅ Enabled\n"
        f"*Price Monitor:* ✅ Connected",
        parse_mode='Markdown'
    )


async def enable_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /enable command"""
    global alerts_enabled
    alerts_enabled = True
    await update.message.reply_text("✅ Alerts enabled!")


async def disable_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /disable command"""
    global alerts_enabled
    alerts_enabled = False
    await update.message.reply_text("🔕 Alerts disabled!")


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /test command"""
    test_data = {
        "symbol": "DOGE-USD",  # Cheap coin for safe testing
        "spike_type": "pump",
        "pct_change": 5.23,
        "old_price": 0.08450,
        "new_price": 0.08892,
        "time_span_seconds": 300,
        "timestamp": datetime.now().isoformat()
    }

    message, reply_markup = format_price_alert(test_data)
    await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)


async def emergency_stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /emergency_stop command"""
    try:
        # Check if this is enabling or disabling
        args = context.args
        if args and args[0].lower() in ['off', 'disable', 'false']:
            result = await trading_manager.emergency_stop_toggle(enable=False)
        else:
            result = await trading_manager.emergency_stop_toggle(enable=True)

        await update.message.reply_text(
            f"{result['message']}\n\n"
            f"Use `/emergency_stop off` to re-enable trading.",
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f"Error in emergency_stop command: {e}")
        await update.message.reply_text(
            "❌ Failed to toggle emergency stop.",
            parse_mode='Markdown'
        )


async def limits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /limits command - show trading limits and usage"""
    try:
        stats = await trading_manager.get_trading_statistics()

        message = (
            f"📊 *Trading Limits & Usage*\n\n"
            f"*Daily Trades:* {stats['daily_trades']}/{stats['daily_limit']}\n"
            f"*Remaining:* {stats['trades_remaining']}\n"
            f"*Emergency Stop:* {'🚨 ACTIVE' if stats['emergency_stop'] else '✅ Off'}\n\n"
            f"*Safety Limits:*\n"
            f"*Reserve:* {stats['reserve_percentage']}% of USD balance\n"
            f"*Max Position:* {stats['max_position_percentage']}% per trade\n"
            f"*Min Trade:* ${stats['min_trade_usd']}\n\n"
            f"💡 Use `/emergency_stop` to halt all trading"
        )

        await update.message.reply_text(message, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in limits command: {e}")
        await update.message.reply_text(
            "❌ Failed to fetch trading limits.",
            parse_mode='Markdown'
        )


async def edit_order_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /edit_order command"""
    try:
        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                "❌ Usage: `/edit_order <order_id> <new_price> [new_size]`\n\n"
                "Example: `/edit_order abc123 45000.50`",
                parse_mode='Markdown'
            )
            return

        order_id = args[0]
        new_price = args[1] if len(args) > 1 else None
        new_size = args[2] if len(args) > 2 else None

        # Validate price format
        try:
            if new_price:
                float(new_price)
            if new_size:
                float(new_size)
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid price or size format. Use numbers only.",
                parse_mode='Markdown'
            )
            return

        result = await trading_manager.edit_order(order_id, new_price, new_size)

        if result['success']:
            message = (
                f"✅ *Order Edited*\n\n"
                f"*Order ID:* {order_id[:8]}...\n"
                f"*New Price:* ${float(new_price):,.6f}" + (f"\n*New Size:* {new_size}" if new_size else "")
            )
        else:
            message = f"❌ {result['message']}"

        await update.message.reply_text(message, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in edit_order command: {e}")
        await update.message.reply_text(
            "❌ Failed to edit order.",
            parse_mode='Markdown'
        )


async def fills_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /fills command - show recent trade executions"""
    try:
        args = context.args
        order_id = args[0] if args else None

        fills = await trading_manager.get_fills(order_id=order_id, limit=10)

        if not fills:
            await update.message.reply_text(
                "📊 *Recent Fills*\n\nNo recent trade executions found.",
                parse_mode='Markdown'
            )
            return

        message = "📊 *Recent Fills*\n\n"
        for fill in fills[:5]:  # Show last 5 fills
            trade_time = fill['trade_time'][:19].replace('T', ' ') if fill['trade_time'] else 'Unknown'
            message += (
                f"*{fill['product_id']}* - {fill['side']}\n"
                f"Size: {float(fill['size']):.8f}\n"
                f"Price: ${float(fill['price']):,.6f}\n"
                f"Fee: ${float(fill['fee']):.6f}\n"
                f"Time: {trade_time}\n\n"
            )

        keyboard = [
            [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_fills")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error in fills command: {e}")
        await update.message.reply_text(
            "❌ Failed to fetch trade fills.",
            parse_mode='Markdown'
        )


async def trading_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /trading_stats command"""
    try:
        stats = await trading_manager.get_trading_statistics()

        # Get recent fills for additional stats
        fills = await trading_manager.get_fills(limit=20)

        total_volume = sum(float(fill['size']) * float(fill['price']) for fill in fills if fill['side'] == 'BUY')
        total_fees = sum(float(fill['fee']) for fill in fills)

        message = (
            f"📈 *Trading Statistics*\n\n"
            f"*Today:*\n"
            f"Trades: {stats['daily_trades']}/{stats['daily_limit']}\n"
            f"Remaining: {stats['trades_remaining']}\n\n"
            f"*Recent Activity (last 20 fills):*\n"
            f"Total Volume: ${total_volume:,.2f}\n"
            f"Total Fees: ${total_fees:,.6f}\n"
            f"Executions: {len(fills)}\n\n"
            f"*Safety Status:*\n"
            f"Emergency Stop: {'🚨 ACTIVE' if stats['emergency_stop'] else '✅ Off'}\n"
            f"Reserve: {stats['reserve_percentage']}%\n"
            f"Max Position: {stats['max_position_percentage']}%"
        )

        await update.message.reply_text(message, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in trading_stats command: {e}")
        await update.message.reply_text(
            "❌ Failed to fetch trading statistics.",
            parse_mode='Markdown'
        )


# Callback query handlers for inline buttons
async def handle_custom_buy_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user's custom amount input for buy orders"""
    global pending_custom_buys

    chat_id = str(update.message.chat_id)
    user_input = update.message.text.strip()

    # Check if this user has a pending custom buy
    if chat_id not in pending_custom_buys:
        return  # Not a custom buy input, ignore

    product_id = pending_custom_buys[chat_id]
    del pending_custom_buys[chat_id]  # Clear the pending state

    try:
        # Parse the input - could be "$50", "50", or "7.5%"
        if '%' in user_input:
            # Percentage format
            percentage = float(user_input.replace('%', '').strip())
            # Use the existing percentage handler logic
            balance_info = await trading_manager.get_account_balance('USD')
            if 'error' in balance_info:
                await update.message.reply_text(f"❌ Error: {balance_info['error']}")
                return

            position_calc = trading_manager.calculate_position_size(
                balance_info['available_trading'],
                percentage=percentage
            )

            if not position_calc['valid']:
                await update.message.reply_text(f"❌ {position_calc['error']}")
                return

            amount = position_calc['position_size']
            percentage_str = str(percentage)

        else:
            # Dollar amount format
            amount = float(user_input.replace('$', '').replace(',', '').strip())

            # Validate minimum
            if amount < trading_manager.min_order_usd:
                await update.message.reply_text(
                    f"❌ Amount ${amount:.2f} is below minimum ${trading_manager.min_order_usd}"
                )
                return

            balance_info = await trading_manager.get_account_balance('USD')
            if 'error' in balance_info:
                await update.message.reply_text(f"❌ Error: {balance_info['error']}")
                return

            # Calculate what percentage this represents
            available = balance_info['available_trading']
            if amount > available:
                await update.message.reply_text(
                    f"❌ Amount ${amount:.2f} exceeds available balance ${available:.2f}"
                )
                return

            percentage_str = str((amount / available) * 100)

        # Get product info for verification screen
        product_info = await trading_manager.get_product_info(product_id)
        if 'error' in product_info:
            await update.message.reply_text(f"❌ Error: {product_info['error']}")
            return

        current_price = product_info['current_price']
        estimated_quantity = amount / current_price if current_price > 0 else 0

        # Skip first confirmation, go straight to verification screen
        verification_message = (
            f"⚠️ *VERIFY PURCHASE*\n\n"
            f"*Product:* {product_id}\n"
            f"*Amount:* ${amount:,.2f}\n"
            f"*Current Price:* ${current_price:,.6f}\n"
            f"*Est. Quantity:* {estimated_quantity:.8f}\n"
            f"*Est. Fees:* ${amount * 0.006:,.2f} (0.6%)\n\n"
            f"🤖 *Trading Mode:* Automated\n"
            f"• Profit Target: 3%+\n"
            f"• Trailing Stop: 1.5%\n"
            f"• Stop Loss: -5%\n"
            f"• Min Hold: 30 min\n\n"
            f"⚡ After purchase, bot will manage exits automatically.\n\n"
            f"Proceed with purchase?"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ CONFIRM PURCHASE", callback_data=f"execute_buy:{product_id}:{amount}:{percentage_str}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"ignore:{product_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(verification_message, parse_mode='Markdown', reply_markup=reply_markup)

    except ValueError:
        await update.message.reply_text(
            f"❌ Invalid amount format. Please enter a number like `50` or `7.5%`"
        )
        pending_custom_buys[chat_id] = product_id  # Restore pending state
    except Exception as e:
        logger.error(f"Error processing custom buy input: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks"""
    query = update.callback_query
    await query.answer()

    data = query.data
    chat_id = str(query.message.chat_id)

    try:
        if data.startswith("buy:"):
            product_id = data.split(":", 1)[1]
            await handle_buy_request(query, product_id, chat_id)

        elif data.startswith("ignore:"):
            product_id = data.split(":", 1)[1]
            await handle_ignore_request(query, product_id)

        # Removed: confirm_buy callback - now going straight to execute_buy

        elif data.startswith("execute_buy:"):
            params = data.split(":", 3)
            product_id = params[1]
            amount = params[2]
            percentage = params[3] if len(params) > 3 else "2.0"
            await handle_execute_buy(query, product_id, amount, percentage, chat_id)

        elif data.startswith("market_sell:"):
            product_id = data.split(":", 1)[1]
            await handle_market_sell_request(query, product_id, chat_id)

        elif data.startswith("limit_sell:"):
            product_id = data.split(":", 1)[1]
            await handle_limit_sell_request(query, product_id, chat_id)

        elif data.startswith("stop_monitor:"):
            product_id = data.split(":", 1)[1]
            await handle_stop_monitoring(query, product_id, chat_id)

        elif data.startswith("refresh_position:"):
            product_id = data.split(":", 1)[1]
            await handle_refresh_position(query, product_id)

        elif data == "refresh_positions":
            await positions_command(update, context)

        elif data == "refresh_orders":
            await orders_command(update, context)

        elif data == "refresh_fills":
            await fills_command(update, context)

        elif data.startswith("pos_action:"):
            # Position card actions
            parts = data.split(":")
            if len(parts) >= 4:
                product_id = parts[1]
                action = parts[2]
                step = parts[3]

                if action == "limit" and step == "prompt":
                    await handle_position_limit_prompt(query, product_id, chat_id)
                elif action == "limit" and step == "confirm":
                    price = float(parts[4]) if len(parts) > 4 else 0
                    await handle_position_limit_confirm(query, product_id, price, chat_id)
                elif action == "limit" and step == "execute":
                    price = float(parts[4]) if len(parts) > 4 else 0
                    await handle_position_limit_execute(query, product_id, price, chat_id)
                elif action == "cancel_limit" and step == "confirm":
                    await handle_cancel_limit_confirm(query, product_id, chat_id)
                elif action == "cancel_limit" and step == "execute":
                    await handle_cancel_limit_execute(query, product_id, chat_id)
                elif action == "market" and step == "confirm":
                    await handle_position_market_confirm(query, product_id, chat_id)
                elif action == "market" and step == "execute":
                    await handle_position_market_execute(query, product_id, chat_id)
                elif action == "refresh":
                    await handle_position_card_refresh(query, product_id)
                elif action == "cancel":
                    await handle_position_action_cancel(query, product_id, chat_id)

        # Test command callbacks
        elif data.startswith("test_"):
            from test_commands import handle_test_callbacks
            await handle_test_callbacks(query, data)

        else:
            await query.edit_message_text("❌ Unknown action")

    except Exception as e:
        logger.error(f"Error handling callback {data}: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)}")


async def handle_buy_request(query, product_id: str, chat_id: str):
    """Handle buy button click - prompt for custom amount"""
    try:
        # Get balance and product info
        balance_info = await trading_manager.get_account_balance('USD')
        product_info = await trading_manager.get_product_info(product_id)

        if 'error' in balance_info:
            await query.edit_message_text(f"❌ Error getting balance: {balance_info['error']}")
            return

        if 'error' in product_info:
            await query.edit_message_text(f"❌ Error getting product info: {product_info['error']}")
            return

        current_price = product_info['current_price']
        available_balance = balance_info['available_trading']

        # Store pending buy context
        global pending_custom_buys
        pending_custom_buys[chat_id] = product_id

        message = (
            f"💵 *Enter Purchase Amount*\n\n"
            f"*Pair:* {product_id}\n"
            f"*Current Price:* ${current_price:,.6f}\n"
            f"*Available Balance:* ${available_balance:,.2f}\n\n"
            f"Reply to this message with:\n"
            f"• A dollar amount (e.g., `50` for $50)\n"
            f"• A percentage (e.g., `7.5%` for 7.5%)\n\n"
            f"Example: Type `100` to buy $100 worth\n"
            f"Example: Type `12.5%` to buy 12.5% of balance"
        )

        keyboard = [
            [InlineKeyboardButton("❌ Cancel", callback_data=f"ignore:{product_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error in buy request: {e}")
        await query.edit_message_text(f"❌ Error preparing buy order: {str(e)}")


async def handle_buy_confirmation(query, product_id: str, amount: str, percentage: str, chat_id: str):
    """
    Handle buy confirmation with verification and automated trading setup
    NEW: Integrates with LiveTradingManager for automated exit logic
    """
    try:
        # VERIFICATION STEP: Get current price to verify it hasn't changed dramatically
        product_info = await trading_manager.get_product_info(product_id)

        if 'error' in product_info:
            await query.edit_message_text(f"❌ Error verifying product: {product_info['error']}")
            return

        current_price = product_info.get('current_price', 0)
        amount_float = float(amount)
        estimated_quantity = amount_float / current_price if current_price > 0 else 0

        # Show verification prompt
        verification_message = (
            f"⚠️ *VERIFY PURCHASE*\n\n"
            f"*Product:* {product_id}\n"
            f"*Amount:* ${amount_float:,.2f}\n"
            f"*Current Price:* ${current_price:,.6f}\n"
            f"*Est. Quantity:* {estimated_quantity:.8f}\n"
            f"*Est. Fees:* ${amount_float * 0.006:,.2f} (0.6%)\n\n"
            f"🤖 *Trading Mode:* Automated\n"
            f"• Profit Target: 3%+\n"
            f"• Trailing Stop: 1.5%\n"
            f"• Stop Loss: -5%\n"
            f"• Min Hold: 30 min\n\n"
            f"⚡ After purchase, bot will manage exits automatically.\n"
            f"Position card will show live P&L.\n\n"
            f"Proceed with purchase?"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ CONFIRM PURCHASE", callback_data=f"execute_buy:{product_id}:{amount}:{percentage}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"ignore:{product_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(verification_message, parse_mode='Markdown', reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error in buy confirmation: {e}")
        await query.edit_message_text(f"❌ Verification failed: {str(e)}")


async def handle_execute_buy(query, product_id: str, amount: str, percentage: str, chat_id: str):
    """
    Execute verified buy order and setup automated trading
    NEW: Uses LiveTradingManager for automated position management
    """
    try:
        await query.edit_message_text(f"⏳ Executing buy order for {product_id}...")

        # Execute buy through LiveTradingManager (includes automated tracking)
        result = await live_trading_manager.execute_buy(
            product_id=product_id,
            quote_size=float(amount),
            position_percentage=float(percentage)
        )

        if not result['success']:
            await query.edit_message_text(f"❌ {result['message']}")
            return

        position = result['position']

        # Create position card
        await create_position_card(
            bot=application.bot,
            product_id=product_id,
            entry_price=position.entry_price,
            quantity=position.quantity,
            cost_basis=position.cost_basis
        )

        # Update trading state and switch WebSocket to priority mode
        await trading_state_controller.add_position(product_id)

        # Show success message
        success_message = (
            f"✅ *Purchase Complete*\n\n"
            f"*Product:* {product_id}\n"
            f"*Entry Price:* ${position.entry_price:,.6f}\n"
            f"*Quantity:* {position.quantity:.8f}\n"
            f"*Cost:* ${position.cost_basis:,.2f}\n\n"
            f"🤖 *Automated Trading Active*\n"
            f"• Min Exit: ${position.min_exit_price:,.6f} (+3%)\n"
            f"• Stop Loss: ${position.stop_loss_price:,.6f} (-5%)\n"
            f"• Trailing Stop: ${position.trailing_exit_price:,.6f}\n\n"
            f"📊 Position card created below.\n"
            f"Bot will manage exits automatically."
        )

        await query.edit_message_text(success_message, parse_mode='Markdown')

        logger.info(f"✅ Live trading activated for {product_id} - WebSocket switched to priority mode")

    except Exception as e:
        logger.error(f"Error executing buy: {e}")
        await query.edit_message_text(f"❌ Purchase failed: {str(e)}")


async def handle_chart_request(query, product_id: str):
    """Handle chart button click"""
    chart_url = f"https://www.coinbase.com/advanced-trade/spot/{product_id}"

    keyboard = [
        [InlineKeyboardButton("📊 View Chart", url=chart_url)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"📈 *Chart for {product_id}*\n\nClick the button below to view the chart on Coinbase.",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )


async def handle_ignore_request(query, product_id: str):
    """Handle ignore button click"""
    await query.edit_message_text(f"👁️ Alert for {product_id} ignored.")


async def handle_market_sell_request(query, product_id: str, chat_id: str):
    """Handle market sell from position monitoring"""
    try:
        # Get current position
        positions = await trading_manager.get_positions()
        position = next((p for p in positions if p['product_id'] == product_id), None)

        if not position:
            await query.edit_message_text(f"❌ No position found for {product_id}")
            return

        # Execute market sell
        base_size = str(position['balance'])
        sell_result = await trading_manager.create_market_sell_order(
            product_id=product_id,
            base_size=base_size
        )

        if sell_result['success']:
            # Stop monitoring
            await price_monitor.stop_position_monitoring(product_id)

            message = trading_manager.format_order_summary(sell_result)
            await query.edit_message_text(message, parse_mode='Markdown')
        else:
            await query.edit_message_text(f"❌ {sell_result['message']}")

    except Exception as e:
        logger.error(f"Error in market sell: {e}")
        await query.edit_message_text(f"❌ Market sell failed: {str(e)}")


async def handle_limit_sell_request(query, product_id: str, chat_id: str):
    """Handle limit sell from position monitoring"""
    # For now, just show a message. This could be expanded to ask for limit price
    await query.edit_message_text(
        "🔧 Limit sell functionality coming soon!\n"
        "Use market sell for immediate execution."
    )


async def handle_stop_monitoring(query, product_id: str, chat_id: str):
    """Handle stop monitoring request"""
    try:
        await price_monitor.stop_position_monitoring(product_id)
        await query.edit_message_text(f"🛑 Stopped monitoring {product_id}")
    except Exception as e:
        logger.error(f"Error stopping monitoring: {e}")
        await query.edit_message_text(f"❌ Error stopping monitoring: {str(e)}")


async def handle_refresh_position(query, product_id: str):
    """Handle position refresh request"""
    await query.answer("🔄 Refreshing position data...")


async def cancel_existing_orders(product_id: str):
    """Cancel all open orders for a given product"""
    try:
        # Get all open orders
        orders = await trading_manager.get_open_orders(product_id=product_id)

        if not orders:
            logger.info(f"No open orders to cancel for {product_id}")
            return {'success': True, 'cancelled_count': 0}

        cancelled_count = 0
        failed_cancels = []

        for order in orders:
            order_id = order.get('order_id')
            if order_id:
                result = await trading_manager.cancel_order(order_id)
                if result.get('success'):
                    cancelled_count += 1
                    logger.info(f"Cancelled order {order_id} for {product_id}")
                else:
                    failed_cancels.append(order_id)
                    logger.warning(f"Failed to cancel order {order_id}: {result.get('message')}")

        if failed_cancels:
            return {
                'success': False,
                'cancelled_count': cancelled_count,
                'failed': failed_cancels,
                'message': f"Cancelled {cancelled_count} orders, failed to cancel {len(failed_cancels)}"
            }

        return {'success': True, 'cancelled_count': cancelled_count}

    except Exception as e:
        logger.error(f"Error cancelling orders for {product_id}: {e}")
        return {'success': False, 'error': str(e), 'message': f"Error: {str(e)}"}


def init_position_cards_db():
    """Initialize database for position card persistence"""
    global db_conn
    try:
        db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = db_conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_position_cards (
                product_id TEXT PRIMARY KEY,
                message_id INTEGER NOT NULL,
                chat_id TEXT NOT NULL,
                entry_price REAL NOT NULL,
                entry_time TEXT NOT NULL,
                quantity REAL NOT NULL,
                cost_basis REAL NOT NULL,
                current_price REAL NOT NULL,
                pnl_usd REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                last_update REAL NOT NULL,
                status TEXT DEFAULT 'active'
            )
        """)

        db_conn.commit()
        logger.info(f"✅ Position cards database initialized at {DB_PATH}")
    except Exception as e:
        logger.error(f"Error initializing position cards database: {e}")


def save_position_card_to_db(product_id: str, card_data: dict):
    """Save position card to database"""
    try:
        cursor = db_conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO active_position_cards
            (product_id, message_id, chat_id, entry_price, entry_time, quantity,
             cost_basis, current_price, pnl_usd, pnl_pct, last_update, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """, (
            product_id,
            card_data['message_id'],
            card_data['chat_id'],
            card_data['entry_price'],
            card_data['entry_time'],
            card_data['quantity'],
            card_data['cost_basis'],
            card_data['current_price'],
            card_data['pnl_usd'],
            card_data['pnl_pct'],
            card_data['last_update']
        ))
        db_conn.commit()
    except Exception as e:
        logger.error(f"Error saving position card to DB: {e}")


def restore_position_cards_from_db():
    """Restore position cards from database on startup"""
    global active_position_cards
    try:
        cursor = db_conn.cursor()
        cursor.execute("SELECT * FROM active_position_cards WHERE status = 'active'")
        rows = cursor.fetchall()

        for row in rows:
            product_id = row[0]
            active_position_cards[product_id] = {
                'message_id': row[1],
                'chat_id': row[2],
                'entry_price': row[3],
                'entry_time': row[4],
                'quantity': row[5],
                'cost_basis': row[6],
                'current_price': row[7],
                'pnl_usd': row[8],
                'pnl_pct': row[9],
                'last_update': row[10],
                'pending_action': None
            }

        if rows:
            logger.info(f"✅ Restored {len(rows)} position card(s) from database")
    except Exception as e:
        logger.error(f"Error restoring position cards from DB: {e}")


def remove_position_card_from_db(product_id: str):
    """Remove position card from database when closed"""
    try:
        cursor = db_conn.cursor()
        cursor.execute("UPDATE active_position_cards SET status = 'closed' WHERE product_id = ?", (product_id,))
        db_conn.commit()
    except Exception as e:
        logger.error(f"Error removing position card from DB: {e}")


async def handle_position_limit_prompt(query, product_id: str, chat_id: str):
    """Prompt user to enter limit price"""
    global pending_limit_orders

    try:
        if product_id not in active_position_cards:
            await query.edit_message_text("❌ Position not found")
            return

        card = active_position_cards[product_id]
        current_price = card['current_price']

        # Store context
        pending_limit_orders[chat_id] = {
            'product_id': product_id,
            'message_id': card['message_id'],
            'awaiting_price': True
        }

        message = (
            f"📊 *Set Limit Order: {product_id}*\n\n"
            f"Current Price: ${current_price:,.6f}\n"
            f"Position Size: {card['quantity']:.8f}\n\n"
            f"Reply with your limit price:\n"
            f"• Dollar amount: `{current_price * 1.05:.2f}`\n"
            f"• Percentage: `+5%` (5% above current)\n"
            f"• Percentage: `-2%` (2% below current)\n\n"
            f"ℹ️ Order will execute when price reaches your limit"
        )

        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data=f"pos_action:{product_id}:cancel:limit")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error in limit prompt: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)}")


async def handle_position_limit_confirm(query, product_id: str, price: float, chat_id: str):
    """Show confirmation for limit order"""
    try:
        if product_id not in active_position_cards:
            await query.edit_message_text("❌ Position not found")
            return

        card = active_position_cards[product_id]
        current_price = card['current_price']
        quantity = card['quantity']

        # Calculate expected proceeds
        gross_proceeds = price * quantity
        estimated_fee = gross_proceeds * 0.004  # 0.4% maker fee
        net_proceeds = gross_proceeds - estimated_fee

        # Price validation warning
        price_diff_pct = ((price - current_price) / current_price) * 100
        warning = ""
        if abs(price_diff_pct) > 10:
            warning = f"\n⚠️ Warning: Limit price is {abs(price_diff_pct):.1f}% from current price\n"

        message = (
            f"⚠️ *Confirm Limit Sell Order*\n\n"
            f"Product: {product_id}\n"
            f"Quantity: {quantity:.8f}\n"
            f"Limit Price: ${price:,.6f}\n"
            f"Current Price: ${current_price:,.6f}\n"
            f"Expected Proceeds: ${net_proceeds:.2f} (after fees)\n"
            f"{warning}\n"
            f"This order will:\n"
            f"• Cancel any existing open orders for {product_id}\n"
            f"• Execute when price reaches ${price:,.6f}\n"
            f"• May take time to fill\n"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"pos_action:{product_id}:limit:execute:{price}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"pos_action:{product_id}:cancel:limit")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error in limit confirm: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)}")


async def handle_position_limit_execute(query, product_id: str, price: float, chat_id: str):
    """
    Execute limit sell order and enter hibernation mode
    NEW: Uses LiveTradingManager to set hibernation state
    """
    try:
        if product_id not in active_position_cards:
            await query.edit_message_text("❌ Position not found")
            return

        card = active_position_cards[product_id]
        await query.edit_message_text(f"⏳ Placing limit order for {product_id}...")

        # Cancel existing orders first
        cancel_result = await cancel_existing_orders(product_id)
        if cancel_result['cancelled_count'] > 0:
            logger.info(f"Cancelled {cancel_result['cancelled_count']} existing order(s) for {product_id}")

        # Set limit order via LiveTradingManager (enters hibernation mode)
        if live_trading_manager:
            result = await live_trading_manager.set_limit_order(product_id, price)

            if not result['success']:
                await query.edit_message_text(f"❌ Error: {result['message']}")
                return

            message = (
                f"💤 *HIBERNATION MODE ACTIVATED*\n\n"
                f"Product: {product_id}\n"
                f"Limit Price: ${price:,.6f}\n"
                f"Quantity: {card['quantity']:.8f}\n\n"
                f"🤖 Bot has paused automated exits.\n"
                f"📊 Position card will show hibernating status.\n"
                f"⏳ Waiting for limit order fill at ${price:,.6f}"
            )
        else:
            # Fallback if LiveTradingManager not available
            message = (
                f"✅ *Limit Order Placed*\n\n"
                f"Product: {product_id}\n"
                f"Quantity: {card['quantity']:.8f}\n"
                f"Limit Price: ${price:,.6f}\n\n"
                f"ℹ️ Order will execute when price reaches ${price:,.6f}"
            )

        keyboard = [[InlineKeyboardButton("🔙 Back to Position", callback_data=f"pos_action:{product_id}:refresh:0")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(message, parse_mode='Markdown', reply_markup=reply_markup)
        logger.info(f"💤 Limit order set - {product_id} entering hibernation @ ${price}")

    except Exception as e:
        logger.error(f"Error executing limit order: {e}")
        await query.edit_message_text(f"❌ Error placing order: {str(e)}")


async def handle_cancel_limit_confirm(query, product_id: str, chat_id: str):
    """Show confirmation for cancelling limit order"""
    try:
        if product_id not in active_position_cards:
            await query.edit_message_text("❌ Position not found")
            return

        card = active_position_cards[product_id]

        message = (
            f"⚠️ *CONFIRM CANCEL LIMIT ORDER*\n\n"
            f"Product: {product_id}\n"
            f"Quantity: {card['quantity']:.8f}\n\n"
            f"This will:\n"
            f"• Cancel any pending limit orders\n"
            f"• Resume automated trading logic\n"
            f"• Bot will manage exits automatically\n\n"
            f"Are you sure?"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Yes, Resume Auto", callback_data=f"pos_action:{product_id}:cancel_limit:execute"),
                InlineKeyboardButton("❌ No, Keep Order", callback_data=f"pos_action:{product_id}:refresh")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error in cancel limit confirm: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)}")


async def handle_cancel_limit_execute(query, product_id: str, chat_id: str):
    """Execute limit order cancellation and resume automated trading"""
    try:
        if product_id not in active_position_cards:
            await query.edit_message_text("❌ Position not found")
            return

        await query.edit_message_text(f"⏳ Cancelling limit order for {product_id}...")

        # Cancel limit order via LiveTradingManager
        if live_trading_manager:
            result = await live_trading_manager.cancel_limit_order(product_id)

            if not result['success']:
                await query.edit_message_text(f"❌ Error: {result['message']}")
                return

            position = result['position']

            message = (
                f"✅ *AUTOMATED TRADING RESUMED*\n\n"
                f"Product: {product_id}\n\n"
                f"🤖 Bot has resumed automated exits:\n"
                f"• Min Exit: ${position.min_exit_price:,.6f} (+3%)\n"
                f"• Stop Loss: ${position.stop_loss_price:,.6f} (-5%)\n"
                f"• Trailing Stop: Active\n\n"
                f"📊 Position card updated.\n"
                f"Bot will monitor and exit automatically."
            )
        else:
            message = (
                f"✅ *Limit Order Cancelled*\n\n"
                f"Product: {product_id}\n\n"
                f"ℹ️ Automated trading resumed"
            )

        keyboard = [[InlineKeyboardButton("🔙 Back to Position", callback_data=f"pos_action:{product_id}:refresh:0")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(message, parse_mode='Markdown', reply_markup=reply_markup)
        logger.info(f"🤖 Automated trading resumed for {product_id}")

    except Exception as e:
        logger.error(f"Error cancelling limit order: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)}")


async def handle_position_market_confirm(query, product_id: str, chat_id: str):
    """Show confirmation for market sell (double confirmation for safety)"""
    try:
        if product_id not in active_position_cards:
            await query.edit_message_text("❌ Position not found")
            return

        card = active_position_cards[product_id]
        current_price = card['current_price']
        quantity = card['quantity']
        pnl_usd = card['pnl_usd']
        pnl_pct = card['pnl_pct']

        # Calculate expected proceeds
        gross_proceeds = current_price * quantity
        estimated_fee = gross_proceeds * 0.006  # 0.6% taker fee
        net_proceeds = gross_proceeds - estimated_fee

        pnl_emoji = "📈" if pnl_usd >= 0 else "📉"
        pnl_sign = "+" if pnl_usd >= 0 else ""

        message = (
            f"⚠️ *CONFIRM MARKET SELL*\n\n"
            f"Product: {product_id}\n"
            f"Quantity: {quantity:.8f}\n"
            f"Current Price: ${current_price:,.6f}\n"
            f"Expected Proceeds: ${net_proceeds:.2f}\n"
            f"Current P&L: {pnl_sign}${abs(pnl_usd):.2f} ({pnl_sign}{pnl_pct:.2f}%) {pnl_emoji}\n\n"
            f"⚠️ *WARNING*:\n"
            f"• This will sell at MARKET PRICE immediately\n"
            f"• Price may vary due to slippage\n"
            f"• This action CANNOT be undone\n"
            f"• All open orders for {product_id} will be cancelled\n\n"
            f"Are you sure you want to proceed?"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ YES, SELL NOW", callback_data=f"pos_action:{product_id}:market:execute:0"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"pos_action:{product_id}:cancel:market")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error in market confirm: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)}")


async def handle_position_market_execute(query, product_id: str, chat_id: str):
    """
    Execute manual market sell order
    NEW: Uses LiveTradingManager and updates trading state
    """
    try:
        if product_id not in active_position_cards:
            await query.edit_message_text("❌ Position not found")
            return

        card = active_position_cards[product_id]
        await query.edit_message_text(f"⏳ Executing market sell for {product_id}...")

        # Cancel existing orders first
        cancel_result = await cancel_existing_orders(product_id)
        if cancel_result['cancelled_count'] > 0:
            logger.info(f"Cancelled {cancel_result['cancelled_count']} existing order(s) for {product_id}")

        # Execute via LiveTradingManager
        if live_trading_manager:
            result = await live_trading_manager.execute_manual_exit(product_id)

            if not result['success']:
                await query.edit_message_text(f"❌ Error: {result['message']}")
                return

            # Archive the card
            await archive_position_card(application.bot, product_id, card['current_price'], "Manual market sell")

            # Update trading state (remove from priority feed)
            await trading_state_controller.remove_position(product_id)

            pnl_data = result.get('pnl_data', {})
            pnl = pnl_data.get('pnl', 0)
            pnl_pct = pnl_data.get('pnl_percent', 0)
            pnl_sign = "+" if pnl >= 0 else ""
            pnl_emoji = "📈" if pnl >= 0 else "📉"

            message = (
                f"✅ *Market Sell Executed*\n\n"
                f"Product: {product_id}\n"
                f"Quantity: {card['quantity']:.8f}\n"
                f"Exit Price: ${card['current_price']:,.6f}\n"
                f"Net Proceeds: ${pnl_data.get('net_proceeds', 0):,.2f}\n\n"
                f"Final P&L: {pnl_sign}${abs(pnl):.2f} ({pnl_sign}{pnl_pct:.2f}%) {pnl_emoji}\n\n"
                f"📊 Position closed\n"
                f"🔄 WebSocket feed updated"
            )
        else:
            # Fallback if LiveTradingManager not available
            await archive_position_card(application.bot, product_id, card['current_price'], "Manual market sell")

            message = (
                f"✅ *Market Sell Executed*\n\n"
                f"Product: {product_id}\n"
                f"Quantity: {card['quantity']:.8f}\n"
                f"Executed at: ~${card['current_price']:,.6f}\n\n"
                f"ℹ️ Check your fills for exact execution price"
            )

        await query.edit_message_text(message, parse_mode='Markdown')
        logger.info(f"Market sell executed: {product_id}")

    except Exception as e:
        logger.error(f"Error executing market sell: {e}")
        await query.edit_message_text(f"❌ Error executing sell: {str(e)}")


async def handle_position_card_refresh(query, product_id: str):
    """Manually refresh position card"""
    global active_position_cards

    if product_id not in active_position_cards:
        await query.answer("❌ Position not found")
        return

    card = active_position_cards[product_id]

    # Get latest price
    product_info = await trading_manager.get_product_info(product_id)
    if 'error' not in product_info:
        current_price = product_info['current_price']
        await update_position_card(application.bot, product_id, current_price)
        await query.answer(f"🔄 Refreshed: ${current_price:,.6f}")
    else:
        await query.answer("❌ Failed to refresh")


async def handle_position_action_cancel(query, product_id: str, chat_id: str):
    """Cancel pending position action and return to card"""
    global pending_limit_orders

    # Clear any pending limit order context
    if chat_id in pending_limit_orders:
        del pending_limit_orders[chat_id]

    # Refresh the position card
    if product_id in active_position_cards:
        await handle_position_card_refresh(query, product_id)
        await query.answer("❌ Action cancelled")
    else:
        await query.edit_message_text("❌ Position not found")


async def handle_limit_price_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user's limit price input for position limit orders"""
    global pending_limit_orders

    chat_id = str(update.message.chat_id)
    user_input = update.message.text.strip()

    # Check if this user has a pending limit order
    if chat_id not in pending_limit_orders:
        return  # Not a limit price input, ignore

    order_context = pending_limit_orders[chat_id]
    product_id = order_context['product_id']
    del pending_limit_orders[chat_id]  # Clear the pending state

    try:
        # Get current position card data first (needed for percentage calculations)
        if product_id not in active_position_cards:
            await update.message.reply_text(f"❌ Position card not found for {product_id}")
            return

        card = active_position_cards[product_id]
        current_price = card['current_price']

        # Parse the input - support both dollar amounts and percentages
        if '%' in user_input:
            # Percentage format (e.g., "+5%" or "-2%")
            percent_str = user_input.replace('%', '').replace('+', '').strip()
            try:
                percent = float(percent_str)
                limit_price = current_price * (1 + percent / 100)
            except ValueError:
                await update.message.reply_text("❌ Invalid percentage format. Use +5% or -2%")
                return
        else:
            # Dollar amount format (e.g., "65000" or "$65,000")
            try:
                limit_price = float(user_input.replace('$', '').replace(',', '').strip())
            except ValueError:
                await update.message.reply_text("❌ Invalid price format. Use a number or percentage (e.g., 65000 or +5%)")
                return

        # Validate minimum price (must be > 0)
        if limit_price <= 0:
            await update.message.reply_text("❌ Limit price must be greater than $0")
            return

        # Validate limit price is above current price
        if limit_price <= current_price:
            await update.message.reply_text(
                f"❌ Limit price ${limit_price:,.6f} must be above current price ${current_price:,.6f}"
            )
            return

        # Calculate potential profit
        quantity = card['quantity']
        gross_proceeds = limit_price * quantity
        current_value = current_price * quantity
        profit_usd = gross_proceeds - card['cost_basis']
        profit_pct = (profit_usd / card['cost_basis']) * 100
        gain_from_current = ((limit_price - current_price) / current_price) * 100

        # Show confirmation
        message = (
            f"📊 *Confirm Limit Order*\n\n"
            f"Product: {product_id}\n"
            f"Limit Price: ${limit_price:,.6f}\n"
            f"Current Price: ${current_price:,.6f}\n"
            f"Target Gain: +{gain_from_current:.2f}%\n\n"
            f"*If Executed at Limit:*\n"
            f"Quantity: {quantity:.8f}\n"
            f"Gross Proceeds: ${gross_proceeds:,.2f}\n"
            f"Est. Fees (0.4%): ${gross_proceeds * 0.004:,.2f}\n"
            f"Net Profit: ${profit_usd:,.2f} ({profit_pct:+.2f}%)\n\n"
            f"⚠️ This will place a limit sell order at ${limit_price:,.6f}\n"
            f"Order executes automatically when price reaches limit.\n"
            f"All existing orders for {product_id} will be cancelled.\n\n"
            f"Proceed?"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Place Limit Order", callback_data=f"pos_action:{product_id}:limit:execute:{limit_price}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"pos_action:{product_id}:cancel:limit")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    except ValueError:
        await update.message.reply_text(
            f"❌ Invalid price format. Please enter a number (e.g., 1.25 or $1.25)"
        )
    except Exception as e:
        logger.error(f"Error handling limit price input: {e}")
        await update.message.reply_text(f"❌ Error processing limit price: {str(e)}")


async def create_position_card(bot: Bot, product_id: str, entry_price: float, quantity: float, cost_basis: float):
    """Create a persistent position card for active trade"""
    global active_position_cards

    try:
        entry_time = datetime.now().strftime('%H:%M:%S')

        message = format_position_card_message(
            product_id=product_id,
            entry_price=entry_price,
            entry_time=entry_time,
            quantity=quantity,
            cost_basis=cost_basis,
            current_price=entry_price,  # Start with entry price
            pnl_usd=0,
            pnl_pct=0
        )

        keyboard = [
            [
                InlineKeyboardButton("📊 Set Limit Order", callback_data=f"pos_action:{product_id}:limit:prompt"),
                InlineKeyboardButton("💰 Sell at Market", callback_data=f"pos_action:{product_id}:market:confirm")
            ],
            [
                InlineKeyboardButton("🔄 Refresh", callback_data=f"pos_action:{product_id}:refresh")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        sent_message = await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

        # Store card info
        import time
        active_position_cards[product_id] = {
            'message_id': sent_message.message_id,
            'chat_id': TELEGRAM_CHAT_ID,
            'entry_price': entry_price,
            'entry_time': entry_time,
            'quantity': quantity,
            'cost_basis': cost_basis,
            'current_price': entry_price,
            'pnl_usd': 0,
            'pnl_pct': 0,
            'last_update': time.time(),
            'pending_action': None
        }

        # Save to database
        save_position_card_to_db(product_id, active_position_cards[product_id])

        logger.info(f"✅ Created position card for {product_id}")

    except Exception as e:
        logger.error(f"Error creating position card: {e}")


def format_position_card_message(product_id: str, entry_price: float, entry_time: str,
                                  quantity: float, cost_basis: float, current_price: float,
                                  pnl_usd: float, pnl_pct: float, mode: str = "automated",
                                  trailing_stop: float = None, stop_loss: float = None,
                                  peak_price: float = None) -> str:
    """
    Format the position card message with automated trading status
    NEW: Shows bot mode, trailing stop, stop loss, and peak price
    """
    pnl_emoji = "📈" if pnl_usd >= 0 else "📉"
    pnl_sign = "+" if pnl_usd >= 0 else ""

    # Mode indicator
    if mode == "automated":
        mode_indicator = "🤖 *Automated Trading*"
    elif mode == "manual_limit_order":
        mode_indicator = "💤 *Hibernating* (Limit Order)"
    else:
        mode_indicator = "👁️ *Manual Mode*"

    message = (
        f"📊 *ACTIVE POSITION: {product_id}*\n"
        f"{mode_indicator}\n\n"
        f"Entry: ${entry_price:,.6f} @ {entry_time}\n"
        f"Quantity: {quantity:.8f}\n"
        f"Cost Basis: ${cost_basis:.2f}\n\n"
        f"Current: ${current_price:,.6f} {pnl_emoji}\n"
        f"Unrealized P&L: {pnl_sign}${abs(pnl_usd):.2f} ({pnl_sign}{pnl_pct:.2f}%)\n"
    )

    # Add automated trading details if available
    if mode == "automated" and (trailing_stop or stop_loss or peak_price):
        message += "\n🎯 *Trading Thresholds:*\n"
        if peak_price:
            message += f"Peak: ${peak_price:,.6f}\n"
        if trailing_stop:
            message += f"Trailing Stop: ${trailing_stop:,.6f}\n"
        if stop_loss:
            message += f"Stop Loss: ${stop_loss:,.6f}\n"

    return message


async def update_position_card(bot: Bot, product_id: str, current_price: float):
    """
    Update position card with current price and live trading status
    NEW: Fetches live position data from LiveTradingManager
    """
    global active_position_cards, pending_limit_orders

    if product_id not in active_position_cards:
        return

    card = active_position_cards[product_id]

    # Skip update if user is in the middle of a limit order flow
    chat_id = str(card['chat_id'])
    if chat_id in pending_limit_orders:
        pending_context = pending_limit_orders[chat_id]
        if pending_context.get('product_id') == product_id:
            logger.debug(f"Skipping position card update for {product_id} - user entering limit order")
            return

    # Rate limit: Update max once per 5 seconds
    import time
    current_time = time.time()
    if current_time - card['last_update'] < 5:
        return

    try:
        # Get live position data if available
        live_position = None
        if live_trading_manager:
            live_position = live_trading_manager.get_position(product_id)

        # Calculate P&L (fee-aware if live position available)
        card['current_price'] = current_price

        if live_position:
            # Use fee-aware calculation from LivePosition
            unrealized_pnl_data = live_position.get_unrealized_pnl(current_price)
            card['pnl_usd'] = unrealized_pnl_data['unrealized_pnl']
            card['pnl_pct'] = unrealized_pnl_data['unrealized_pnl_percent']
        else:
            # Fallback for positions without LiveTradingManager (shouldn't happen)
            gross_value = current_price * card['quantity']
            card['pnl_usd'] = gross_value - card['cost_basis']
            card['pnl_pct'] = (card['pnl_usd'] / card['cost_basis']) * 100

        card['last_update'] = current_time

        # Format message with live position data
        message = format_position_card_message(
            product_id=product_id,
            entry_price=card['entry_price'],
            entry_time=card['entry_time'],
            quantity=card['quantity'],
            cost_basis=card['cost_basis'],
            current_price=current_price,
            pnl_usd=card['pnl_usd'],
            pnl_pct=card['pnl_pct'],
            mode=live_position.mode if live_position else "manual",
            trailing_stop=live_position.trailing_exit_price if live_position else None,
            stop_loss=live_position.stop_loss_price if live_position else None,
            peak_price=live_position.peak_price if live_position else None
        )

        # Adjust buttons based on mode
        if live_position and live_position.mode == "manual_limit_order":
            # Hibernating mode - show cancel limit order button
            keyboard = [
                [
                    InlineKeyboardButton("❌ Cancel Limit Order", callback_data=f"pos_action:{product_id}:cancel_limit:confirm"),
                    InlineKeyboardButton("💰 Sell Now", callback_data=f"pos_action:{product_id}:market:confirm")
                ],
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data=f"pos_action:{product_id}:refresh")
                ]
            ]
        else:
            # Automated or manual mode - standard buttons
            keyboard = [
                [
                    InlineKeyboardButton("📊 Set Limit Order", callback_data=f"pos_action:{product_id}:limit:prompt"),
                    InlineKeyboardButton("💰 Sell at Market", callback_data=f"pos_action:{product_id}:market:confirm")
                ],
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data=f"pos_action:{product_id}:refresh")
                ]
            ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await bot.edit_message_text(
            chat_id=card['chat_id'],
            message_id=card['message_id'],
            text=message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

        # Update database
        save_position_card_to_db(product_id, card)

    except Exception as e:
        logger.error(f"Error updating position card: {e}")


async def archive_position_card(bot: Bot, product_id: str, exit_price: float, reason: str):
    """Archive position card when position is closed"""
    global active_position_cards

    if product_id not in active_position_cards:
        return

    card = active_position_cards[product_id]

    try:
        # Calculate final P&L
        gross_value = exit_price * card['quantity']
        final_pnl_usd = gross_value - card['cost_basis']
        final_pnl_pct = (final_pnl_usd / card['cost_basis']) * 100

        pnl_emoji = "✅" if final_pnl_usd >= 0 else "❌"
        pnl_sign = "+" if final_pnl_usd >= 0 else ""

        message = (
            f"🏁 *CLOSED POSITION: {product_id}*\n\n"
            f"Entry: ${card['entry_price']:,.6f} @ {card['entry_time']}\n"
            f"Exit: ${exit_price:,.6f}\n"
            f"Quantity: {card['quantity']:.8f}\n"
            f"Cost Basis: ${card['cost_basis']:.2f}\n\n"
            f"Final P&L: {pnl_sign}${abs(final_pnl_usd):.2f} ({pnl_sign}{final_pnl_pct:.2f}%) {pnl_emoji}\n"
            f"Reason: {reason}\n"
        )

        await bot.edit_message_text(
            chat_id=card['chat_id'],
            message_id=card['message_id'],
            text=message,
            parse_mode='Markdown'
        )

        # Remove from active cards and database
        del active_position_cards[product_id]
        remove_position_card_from_db(product_id)
        logger.info(f"Archived position card for {product_id}")

    except Exception as e:
        logger.error(f"Error archiving position card: {e}")


async def send_alert(bot: Bot, alert_data: dict):
    """Send alert with trading buttons"""
    if not alerts_enabled:
        logger.info("Alerts disabled, skipping notification")
        return

    # Handle different data formats
    if 'text' in alert_data and 'spike_type' not in alert_data:
        # It's a text-only format from webhook, send as plain text
        message = alert_data['text']
        reply_markup = None
        parse_mode = None
        logger.info("Received text-format alert")
    else:
        # It's full data format with trading buttons
        message, reply_markup = format_price_alert(alert_data)
        parse_mode = 'Markdown'
        logger.info(f"Received data-format alert: {alert_data.get('symbol', 'unknown')}")

    # Send to personal chat
    if TELEGRAM_CHAT_ID:
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=message,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
            logger.info("📤 Alert sent successfully")
        except Exception as e:
            logger.error(f"Failed to send alert to user: {e}")

    # Send to channel if configured (without buttons for channel)
    if ALERTS_CHANNEL_ID:
        try:
            await bot.send_message(
                chat_id=ALERTS_CHANNEL_ID,
                text=message,
                parse_mode=parse_mode
            )
        except Exception as e:
            logger.error(f"Failed to send to channel: {e}")


async def start_webhook_server(app: Application):
    """Start a simple webhook server to receive alerts"""
    webhook_app = web.Application()

    async def handle_webhook(request):
        """Handle incoming webhooks from spike-detector"""
        try:
            data = await request.json()

            # Send the alert via Telegram
            await send_alert(app.bot, data)

            return web.Response(text="OK", status=200)
        except Exception as e:
            logger.error(f"Error handling webhook: {e}", exc_info=True)
            return web.Response(text="Error", status=500)

    async def handle_position_opened(request):
        """Handle position opened event from paper trading bot"""
        try:
            data = await request.json()

            product_id = data.get('product_id')
            entry_price = data.get('entry_price')
            quantity = data.get('quantity')
            cost_basis = data.get('cost_basis')

            if not all([product_id, entry_price, quantity, cost_basis]):
                return web.Response(text="Missing required fields", status=400)

            # Create position card
            await create_position_card(app.bot, product_id, entry_price, quantity, cost_basis)

            logger.info(f"Position card created for {product_id}")
            return web.Response(text="OK", status=200)
        except Exception as e:
            logger.error(f"Error handling position opened: {e}", exc_info=True)
            return web.Response(text="Error", status=500)

    async def handle_position_updated(request):
        """Handle position price update from backend ticker"""
        try:
            data = await request.json()

            product_id = data.get('product_id')
            current_price = data.get('current_price')

            if not all([product_id, current_price]):
                return web.Response(text="Missing required fields", status=400)

            # Update position card
            await update_position_card(app.bot, product_id, current_price)

            return web.Response(text="OK", status=200)
        except Exception as e:
            logger.error(f"Error handling position update: {e}", exc_info=True)
            return web.Response(text="Error", status=500)

    async def handle_position_closed(request):
        """Handle position closed event from paper trading bot"""
        try:
            data = await request.json()

            product_id = data.get('product_id')
            exit_price = data.get('exit_price')
            exit_reason = data.get('exit_reason', 'Position closed')

            if not all([product_id, exit_price]):
                return web.Response(text="Missing required fields", status=400)

            # Archive position card
            await archive_position_card(app.bot, product_id, exit_price, exit_reason)

            logger.info(f"Position card archived for {product_id}")
            return web.Response(text="OK", status=200)
        except Exception as e:
            logger.error(f"Error handling position closed: {e}", exc_info=True)
            return web.Response(text="Error", status=500)

    # Add routes
    webhook_app.router.add_post('/webhook', handle_webhook)
    webhook_app.router.add_post('/spike-alert', handle_webhook)
    webhook_app.router.add_post('/position-opened', handle_position_opened)
    webhook_app.router.add_post('/position-updated', handle_position_updated)
    webhook_app.router.add_post('/position-closed', handle_position_closed)

    # Start server
    runner = web.AppRunner(webhook_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', WEBHOOK_PORT)
    await site.start()

    logger.info(f"🌐 Webhook server listening on port {WEBHOOK_PORT}")
    return runner


async def live_position_updater(bot: Bot):
    """
    Background task to update position cards AND execute automated exits
    NEW: Integrates with LiveTradingManager for automated trading logic
    """
    import aiohttp

    logger.info("🔄 Live position updater started (with automated exit logic)")

    await asyncio.sleep(10)  # Wait for bot to fully initialize

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # Check if we have any live positions to track
                if not live_trading_manager or len(live_trading_manager.positions) == 0:
                    await asyncio.sleep(5)
                    continue

                # Fetch current prices from backend
                try:
                    active_product_ids = live_trading_manager.get_active_product_ids()
                    logger.debug(f"📊 Fetching prices for {len(active_product_ids)} active position(s)")

                    async with session.get(f"{BACKEND_URL}/tickers", timeout=aiohttp.ClientTimeout(total=5)) as response:
                        if response.status == 200:
                            data = await response.json()
                            prices = {crypto: ticker['price'] for crypto, ticker in data.items()}

                            # Update each active position with automated logic
                            for product_id in active_product_ids:
                                if product_id not in prices:
                                    continue

                                current_price = prices[product_id]
                                position = live_trading_manager.get_position(product_id)

                                if not position:
                                    continue

                                # Update position card UI
                                await update_position_card(bot, product_id, current_price)

                                # AUTOMATED EXIT LOGIC (only in automated mode)
                                if position.mode == "automated":
                                    exit_info = live_trading_manager.update_position(product_id, current_price)

                                    if exit_info and exit_info.get('should_exit'):
                                        # Execute automated exit
                                        logger.info(f"🤖 Automated exit triggered for {product_id}: {exit_info['reason']}")

                                        exit_result = await live_trading_manager.execute_automated_exit(
                                            product_id=product_id,
                                            exit_price=current_price,
                                            reason=exit_info['reason']
                                        )

                                        if exit_result['success']:
                                            # Archive position card
                                            await archive_position_card(
                                                bot=bot,
                                                product_id=product_id,
                                                exit_price=current_price,
                                                reason=exit_info['reason']
                                            )

                                            # Update trading state (remove from priority feed)
                                            await trading_state_controller.remove_position(product_id)

                                            logger.info(f"✅ Automated exit complete for {product_id}")
                                        else:
                                            logger.error(f"Failed automated exit for {product_id}: {exit_result.get('message')}")

                        else:
                            logger.warning(f"Backend returned status {response.status}")

                except Exception as e:
                    logger.error(f"Error in position update cycle: {e}", exc_info=True)

                # Update every 2 seconds for faster response to exit conditions
                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Error in live position updater: {e}")
                await asyncio.sleep(5)


async def post_init(app: Application) -> None:
    """Initialize bot after startup"""
    global trading_manager, price_monitor, spike_socket_server, live_trading_manager, trading_state_controller

    # Initialize position cards database
    init_position_cards_db()
    restore_position_cards_from_db()

    try:
        # Initialize trading manager
        trading_manager = TradingManager()
        logger.info("✅ Trading Manager initialized")

        # Initialize live trading manager (with automated exit logic)
        live_trading_manager = LiveTradingManager(trading_manager)
        logger.info(f"✅ Live Trading Manager initialized with {len(live_trading_manager.positions)} active position(s)")

        # Initialize trading state controller (WebSocket feed management)
        trading_state_controller = TradingStateController(BACKEND_URL)

        # Set initial trading state based on restored positions
        active_products = live_trading_manager.get_active_product_ids()
        await trading_state_controller.set_trading_state(active_products)
        logger.info(f"✅ Trading State Controller initialized (state: {trading_state_controller.trading_state})")

        # Initialize price monitor
        price_monitor = PriceMonitor(app.bot, BACKEND_URL)
        await price_monitor.start()
        logger.info("✅ Price Monitor initialized")

    except Exception as e:
        logger.error(f"Failed to initialize trading components: {e}")
        # Continue without trading functionality
        trading_manager = None
        live_trading_manager = None
        trading_state_controller = None
        price_monitor = None

    # Initialize direct spike alert Socket.IO server
    try:
        async def spike_alert_callback(data):
            """Callback for spike alerts"""
            await send_alert(app.bot, data)

        spike_socket_server = SpikeAlertSocketServer(spike_alert_callback)
        app.bot_data['spike_server'] = await spike_socket_server.start(port=SPIKE_SOCKET_PORT)
        logger.info(f"✅ Direct Spike Alert Socket Server initialized on port {SPIKE_SOCKET_PORT}")
    except Exception as e:
        logger.error(f"Failed to initialize spike alert socket server: {e}")
        spike_socket_server = None

    # Start webhook server
    app.bot_data['webhook_runner'] = await start_webhook_server(app)

    # Start live position updater
    asyncio.create_task(live_position_updater(app.bot))

    logger.info("✅ Telegram Trading Bot is running")

    # Send startup notification
    if TELEGRAM_CHAT_ID:
        try:
            status_msg = "🤖 *Trading Bot Started*\n\n"
            if trading_manager and price_monitor:
                status_msg += "✅ Trading: Enabled\n✅ Price Monitor: Connected\n"
            else:
                status_msg += "❌ Trading: Disabled (Check API credentials)\n"

            if spike_socket_server:
                status_msg += f"✅ Spike Alerts: Direct server on port {SPIKE_SOCKET_PORT}"
            else:
                status_msg += "❌ Spike Alerts: Server not started"

            await app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=status_msg,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Failed to send startup message: {e}")


async def shutdown(app: Application) -> None:
    """Cleanup on shutdown"""
    if 'webhook_runner' in app.bot_data:
        await app.bot_data['webhook_runner'].cleanup()

    if 'spike_server' in app.bot_data:
        await app.bot_data['spike_server'].cleanup()

    if price_monitor:
        await price_monitor.stop()

    logger.info("Bot shutdown complete")


def main() -> None:
    """Main entry point"""
    global application

    # Validate configuration
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        sys.exit(1)

    if not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set! Bot will only work in channel mode.")

    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", start_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("positions", positions_command))
    application.add_handler(CommandHandler("orders", orders_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("enable", enable_command))
    application.add_handler(CommandHandler("disable", disable_command))
    application.add_handler(CommandHandler("test", test_command))

    # Enhanced trading commands
    application.add_handler(CommandHandler("emergency_stop", emergency_stop_command))
    application.add_handler(CommandHandler("limits", limits_command))
    application.add_handler(CommandHandler("edit_order", edit_order_command))
    application.add_handler(CommandHandler("fills", fills_command))
    application.add_handler(CommandHandler("trading_stats", trading_stats_command))

    # Testing commands
    from test_commands import (
        test_buy_flow_command,
        test_price_simulator_command,
        test_websocket_state_command,
        test_position_modes_command,
        test_full_integration_command,
        handle_test_callbacks
    )
    application.add_handler(CommandHandler("testbuy", test_buy_flow_command))
    application.add_handler(CommandHandler("testprices", test_price_simulator_command))
    application.add_handler(CommandHandler("testwebsocket", test_websocket_state_command))
    application.add_handler(CommandHandler("testmodes", test_position_modes_command))
    application.add_handler(CommandHandler("testintegration", test_full_integration_command))

    # Add callback query handler for inline buttons
    application.add_handler(CallbackQueryHandler(button_callback))

    # Add message handler for text inputs (must be added last to not interfere with commands)
    from telegram.ext import MessageHandler, filters

    async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Route text input to appropriate handler"""
        # Try limit price input first
        await handle_limit_price_input(update, context)
        # Then try custom buy input
        await handle_custom_buy_input(update, context)

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    # Setup initialization and shutdown
    application.post_init = post_init
    application.post_shutdown = shutdown

    # Handle signals
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        asyncio.create_task(shutdown(application))
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run the bot
    logger.info("Starting Telegram Trading Bot...")
    logger.info(f"Bot will send alerts to chat ID: {TELEGRAM_CHAT_ID}")
    if ALERTS_CHANNEL_ID:
        logger.info(f"Also broadcasting to channel: {ALERTS_CHANNEL_ID}")

    application.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()