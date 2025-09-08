"""
Simplified Telegram Bot for Single User
"""
import asyncio
import logging
import signal
import sys
import os
from datetime import datetime
import socketio
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # Your personal chat ID
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Optional: Channel for broadcasting
ALERTS_CHANNEL_ID = os.getenv("ALERTS_CHANNEL_ID", "")

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global variables
sio: socketio.AsyncClient = None
bot_app: Application = None
alerts_enabled = True  # Simple on/off switch

def format_price_alert(data):
    """Format price spike alert for Telegram"""
    emoji = "🚀" if data['spike_type'] == 'pump' else "📉"
    
    if data.get('event_type') == 'momentum_end':
        return (
            f"{emoji} *MOMENTUM ENDED* {emoji}\n\n"
            f"*Symbol:* {data['symbol']}\n"
            f"*Peak gain:* {data['peak_change']:.2f}%\n"
            f"*Exit at:* {data['exit_change']:.2f}%\n"
            f"*Peak price:* ${data['peak_price']:.6f}\n"
            f"*Exit price:* ${data['new_price']:.6f}\n"
            f"*Duration:* {data['time_span_seconds']/60:.1f} min"
        )
    else:
        return (
            f"{emoji} *PRICE {data['spike_type'].upper()} ALERT* {emoji}\n\n"
            f"*Symbol:* {data['symbol']}\n"
            f"*Change:* {data['pct_change']:.2f}%\n"
            f"*Price:* ${data['old_price']:.6f} → ${data['new_price']:.6f}\n"
            f"*Time span:* {data['time_span_seconds']:.0f}s"
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
        f"*Backend:* {BACKEND_URL}\n"
        f"*Socket.IO:* {'Connected' if sio and sio.connected else 'Disconnected'}",
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
    # Test alert
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
    
    # Also test Socket.IO connection
    if sio and sio.connected:
        await update.message.reply_text("✅ Socket.IO is connected", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Socket.IO is NOT connected", parse_mode='Markdown')

async def send_alert(bot: Bot, alert_data: dict):
    """Send alert to user and optional channel"""
    if not alerts_enabled:
        logger.info("Alerts disabled, skipping notification")
        return
    
    message = format_price_alert(alert_data)
    
    # Send to personal chat
    if TELEGRAM_CHAT_ID:
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=message,
                parse_mode='Markdown'
            )
            logger.info(f"📤 Alert sent: {alert_data['symbol']} {alert_data['spike_type']} {alert_data['pct_change']:.2f}%")
        except Exception as e:
            logger.error(f"Failed to send alert to user: {e}")
    
    # Send to channel if configured
    if ALERTS_CHANNEL_ID:
        try:
            await bot.send_message(
                chat_id=ALERTS_CHANNEL_ID,
                text=message,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Failed to send to channel: {e}")

async def setup_socketio(app: Application):
    """Setup Socket.IO connection to backend"""
    global sio
    
    sio = socketio.AsyncClient(logger=False, engineio_logger=False)
    
    @sio.on('spike_alert')
    async def on_spike_alert(data):
        """Handle spike alerts from backend"""
        try:
            await send_alert(app.bot, data)
        except Exception as e:
            logger.error(f"Error processing spike alert: {e}")
    
    @sio.on('connect')
    async def on_connect():
        logger.info("Connected to backend Socket.IO")
    
    @sio.on('disconnect')
    async def on_disconnect():
        logger.warning("Disconnected from backend Socket.IO")
    
    # Keep trying to connect
    while True:
        try:
            await sio.connect(BACKEND_URL)
            logger.info(f"Connected to backend at {BACKEND_URL}")
            break
        except Exception as e:
            logger.error(f"Failed to connect to backend: {e}. Retrying in 5s...")
            await asyncio.sleep(5)

async def post_init(app: Application):
    """Initialize bot after startup"""
    await setup_socketio(app)
    
    # Send startup notification
    if TELEGRAM_CHAT_ID:
        try:
            await app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text="🤖 Bot started and monitoring for alerts!",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Failed to send startup message: {e}")

async def shutdown(app: Application):
    """Cleanup on shutdown"""
    global sio
    
    if sio and sio.connected:
        await sio.disconnect()
    
    logger.info("Bot shutdown complete")

def main():
    """Main entry point"""
    global bot_app
    
    # Validate configuration
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        sys.exit(1)
    
    if not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set! Bot will only work in channel mode.")
    
    # Create application
    bot_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add command handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("help", start_command))
    bot_app.add_handler(CommandHandler("status", status_command))
    bot_app.add_handler(CommandHandler("enable", enable_command))
    bot_app.add_handler(CommandHandler("disable", disable_command))
    bot_app.add_handler(CommandHandler("test", test_command))
    
    # Setup initialization and shutdown
    bot_app.post_init = post_init
    bot_app.post_shutdown = shutdown
    
    # Handle signals
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        asyncio.create_task(shutdown(bot_app))
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run the bot
    logger.info("Starting Telegram bot...")
    bot_app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()