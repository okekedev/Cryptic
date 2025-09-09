import asyncio
import logging
import signal
import sys
import os
from datetime import datetime
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from aiohttp import web
import json

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

# Global variables
application = None
alerts_enabled = True

def format_price_alert(data):
    emoji = "🚀" if data['spike_type'] == 'pump' else "📉"
    
    timestamp_str = ""
    if 'timestamp' in data:
        try:
            dt = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
            timestamp_str = f"*time (UTC):* {dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
        except:
            timestamp_str = f"*time:* {data['timestamp']}\n"
    
    if data.get('event_type') == 'momentum_end':
        return (
            f"{emoji} *MOMENTUM ENDED* {emoji}\n\n"
            f"*Symbol:* {data['symbol']}\n"
            f"*Peak gain:* {data['peak_change']:.2f}%\n"
            f"*Exit at:* {data['exit_change']:.2f}%\n"
            f"*Peak price:* ${data['peak_price']:.6f}\n"
            f"*Exit price:* ${data['new_price']:.6f}\n"
            f"*Duration:* {data['time_span_seconds']/60:.1f} min"
            f"{timestamp_str}"
        )
    else:
        return (
            f"{emoji} *PRICE {data['spike_type'].upper()} ALERT* {emoji}\n\n"
            f"*Symbol:* {data['symbol']}\n"
            f"*Change:* {data['pct_change']:.2f}%\n"
            f"*Price:* ${data['old_price']:.6f} → ${data['new_price']:.6f}\n"
            f"*Time span:* {data['time_span_seconds']:.0f}s"
            f"{timestamp_str}"
        )

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    await update.message.reply_text(
        "🚀 *Crypto Alert Bot Started!*\n\n"
        "Commands:\n"
        "/status - Check alert status\n"
        "/enable - Enable alerts\n"
        "/disable - Disable alerts\n"
        "/test - Send test alert\n"
        "/help - Show this message",
        parse_mode='Markdown'
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command"""
    global alerts_enabled
    status = "✅ Enabled" if alerts_enabled else "🔕 Disabled"
    
    await update.message.reply_text(
        f"*Alert Status:* {status}\n"
        f"*Webhook Port:* {WEBHOOK_PORT}",
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
    
    message = format_price_alert(test_data)
    await update.message.reply_text(message, parse_mode='Markdown')

async def send_alert(bot: Bot, alert_data: dict):
    if not alerts_enabled:
        logger.info("Alerts disabled, skipping notification")
        return
    
    # Handle different data formats
    if 'text' in alert_data and 'spike_type' not in alert_data:
        # It's a text-only format from webhook, send as plain text
        message = alert_data['text']
        parse_mode = None  # Send as plain text, no parsing
        logger.info("Received text-format alert")
    else:
        # It's full data format
        message = format_price_alert(alert_data)
        parse_mode = 'Markdown'
        logger.info(f"Received data-format alert: {alert_data.get('symbol', 'unknown')}")
    
    # Send to personal chat
    if TELEGRAM_CHAT_ID:
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=message,
                parse_mode=parse_mode
            )
            logger.info("📤 Alert sent successfully")
        except Exception as e:
            logger.error(f"Failed to send alert to user: {e}")
    
    # Send to channel if configured
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
    # Start webhook server
    app.bot_data['webhook_runner'] = await start_webhook_server(app)
    
    logger.info("✅ Telegram bot is running and monitoring for alerts via webhook")
    
    # Send startup notification
    if TELEGRAM_CHAT_ID:
        try:
            await app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"🤖 Bot started",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Failed to send startup message: {e}")

async def shutdown(app: Application) -> None:
    """Cleanup on shutdown"""
    if 'webhook_runner' in app.bot_data:
        await app.bot_data['webhook_runner'].cleanup()
    
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
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("enable", enable_command))
    application.add_handler(CommandHandler("disable", disable_command))
    application.add_handler(CommandHandler("test", test_command))
    
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
    logger.info("Starting Telegram bot...")
    logger.info(f"Bot will send alerts to chat ID: {TELEGRAM_CHAT_ID}")
    if ALERTS_CHANNEL_ID:
        logger.info(f"Also broadcasting to channel: {ALERTS_CHANNEL_ID}")
    
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
