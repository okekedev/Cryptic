import asyncio
import logging
import signal
import sys
import os
from datetime import datetime
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from aiohttp import web
import json

# Import our trading components
from trading_manager import TradingManager
from price_monitor import PriceMonitor
from socket_server import SpikeAlertSocketServer

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

# User preferences for trading
DEFAULT_POSITION_PERCENTAGE = float(os.getenv('DEFAULT_POSITION_PERCENTAGE', '2.0'))

# Store pending custom buy contexts (chat_id -> product_id)
pending_custom_buys = {}


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
        "symbol": "BTC-USD",
        "spike_type": "pump",
        "pct_change": 5.23,
        "old_price": 45230.50,
        "new_price": 47592.75,
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

        # Get product info
        product_info = await trading_manager.get_product_info(product_id)
        if 'error' in product_info:
            await update.message.reply_text(f"❌ Error: {product_info['error']}")
            return

        current_price = product_info['current_price']
        estimated_coins = amount / current_price if current_price > 0 else 0
        remaining_balance = balance_info['available_trading'] - amount

        # Show confirmation
        message = (
            f"💰 *Confirm Market Buy*\n\n"
            f"*Pair:* {product_id}\n"
            f"*Current Price:* ${current_price:,.6f}\n"
            f"*Purchase Amount:* ${amount:,.2f}\n"
            f"*Est. Coins:* {estimated_coins:.8f}\n"
            f"*Available Balance:* ${balance_info['available_trading']:,.2f}\n"
            f"*After Purchase:* ${remaining_balance:,.2f}\n\n"
            f"⚠️ This will execute a *MARKET BUY* at current price"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm Buy", callback_data=f"confirm_buy:{product_id}:{amount}:{percentage_str}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"ignore:{product_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)

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

        elif data.startswith("confirm_buy:"):
            params = data.split(":", 3)
            product_id = params[1]
            amount = params[2]
            percentage = params[3] if len(params) > 3 else "2.0"
            await handle_buy_confirmation(query, product_id, amount, percentage, chat_id)

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
    """Handle buy confirmation and execute order"""
    try:
        # Execute the buy order
        order_result = await trading_manager.create_market_buy_order(
            product_id=product_id,
            quote_size=amount,
            position_percentage=float(percentage)
        )

        if order_result['success']:
            message = trading_manager.format_order_summary(order_result)

            # Start position monitoring
            product_info = await trading_manager.get_product_info(product_id)
            entry_price = product_info.get('current_price', 0)

            # Send position monitoring message
            monitor_message = (
                f"📊 *Position Monitor Started*\n\n"
                f"*Pair:* {product_id}\n"
                f"*Entry Price:* ${entry_price:,.6f}\n"
                f"*Amount:* ${float(amount):,.2f}\n\n"
                f"Live updates will appear below..."
            )

            sent_message = await query.message.reply_text(
                monitor_message,
                parse_mode='Markdown'
            )

            # Start monitoring with the message ID
            await price_monitor.start_position_monitoring(
                product_id=product_id,
                chat_id=chat_id,
                entry_price=entry_price,
                entry_amount=float(amount),
                message_id=sent_message.message_id
            )

            await query.edit_message_text(message, parse_mode='Markdown')

        else:
            await query.edit_message_text(f"❌ {order_result['message']}")

    except Exception as e:
        logger.error(f"Error in buy confirmation: {e}")
        await query.edit_message_text(f"❌ Buy order failed: {str(e)}")


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

    # Add routes
    webhook_app.router.add_post('/webhook', handle_webhook)
    webhook_app.router.add_post('/spike-alert', handle_webhook)

    # Start server
    runner = web.AppRunner(webhook_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', WEBHOOK_PORT)
    await site.start()

    logger.info(f"🌐 Webhook server listening on port {WEBHOOK_PORT}")
    return runner


async def post_init(app: Application) -> None:
    """Initialize bot after startup"""
    global trading_manager, price_monitor, spike_socket_server

    try:
        # Initialize trading manager
        trading_manager = TradingManager()
        logger.info("✅ Trading Manager initialized")

        # Initialize price monitor
        price_monitor = PriceMonitor(app.bot, BACKEND_URL)
        await price_monitor.start()
        logger.info("✅ Price Monitor initialized")

    except Exception as e:
        logger.error(f"Failed to initialize trading components: {e}")
        # Continue without trading functionality
        trading_manager = None
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

    # Add callback query handler for inline buttons
    application.add_handler(CallbackQueryHandler(button_callback))

    # Add message handler for custom buy amount input (must be added last to not interfere with commands)
    from telegram.ext import MessageHandler, filters
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_buy_input))

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