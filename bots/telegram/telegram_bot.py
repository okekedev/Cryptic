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
from spike_alert_listener import SpikeAlertListener

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
ALERTS_CHANNEL_ID = os.getenv("ALERTS_CHANNEL_ID", "")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000")

# Global variables
application = None
alerts_enabled = True
trading_manager = None
price_monitor = None
spike_listener = None

# User preferences for trading
DEFAULT_POSITION_PERCENTAGE = float(os.getenv('DEFAULT_POSITION_PERCENTAGE', '2.0'))


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
        "📋 *Commands:*\n"
        "/balance - Check USD balance\n"
        "/positions - View open positions\n"
        "/orders - View open orders\n"
        "/status - Check alert status\n"
        "/enable - Enable alerts\n"
        "/disable - Disable alerts\n"
        "/test - Send test alert\n"
        "/help - Show this message",
        parse_mode='Markdown'
    )


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /balance command"""
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
            f"*USD Balance:* {balance_info['formatted']}\n"
            f"*Available for trading:* ${balance_info['balance'] * 0.99:.2f} (1% reserve)"
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


# Callback query handlers for inline buttons
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
            params = data.split(":", 2)
            product_id = params[1]
            amount = params[2]
            await handle_buy_confirmation(query, product_id, amount, chat_id)

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

        else:
            await query.edit_message_text("❌ Unknown action")

    except Exception as e:
        logger.error(f"Error handling callback {data}: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)}")


async def handle_buy_request(query, product_id: str, chat_id: str):
    """Handle buy button click"""
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

        # Calculate position size
        position_calc = trading_manager.calculate_position_size(balance_info['balance'])

        if not position_calc['valid']:
            await query.edit_message_text(f"❌ {position_calc['error']}")
            return

        current_price = product_info['current_price']
        position_size = position_calc['position_size']

        message = (
            f"💰 *Buy Confirmation*\n\n"
            f"*Pair:* {product_id}\n"
            f"*Current Price:* ${current_price:,.6f}\n"
            f"*Position Size:* {position_calc['formatted_size']} ({position_calc['percentage']:.1f}%)\n"
            f"*Available Balance:* {balance_info['formatted']}\n"
            f"*Order Type:* Market Buy"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm Buy", callback_data=f"confirm_buy:{product_id}:{position_size}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"ignore:{product_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error in buy request: {e}")
        await query.edit_message_text(f"❌ Error preparing buy order: {str(e)}")


async def handle_buy_confirmation(query, product_id: str, amount: str, chat_id: str):
    """Handle buy confirmation"""
    try:
        # Execute the buy order
        order_result = await trading_manager.create_market_buy_order(
            product_id=product_id,
            quote_size=amount
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
    global trading_manager, price_monitor, spike_listener

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

    # Initialize spike alert listener
    try:
        async def spike_alert_callback(data):
            """Callback for spike alerts"""
            await send_alert(app.bot, data)

        spike_listener = SpikeAlertListener(BACKEND_URL, spike_alert_callback)
        await spike_listener.start()
        logger.info("✅ Spike Alert Listener initialized")
    except Exception as e:
        logger.error(f"Failed to initialize spike alert listener: {e}")
        spike_listener = None

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

            if spike_listener:
                status_msg += "✅ Spike Alerts: Listening"
            else:
                status_msg += "❌ Spike Alerts: Disconnected"

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

    if price_monitor:
        await price_monitor.stop()

    if spike_listener:
        await spike_listener.stop()

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

    # Add callback query handler for inline buttons
    application.add_handler(CallbackQueryHandler(button_callback))

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