#!/usr/bin/env python3
"""
Daily P&L Email Reporter

Sends a daily performance summary to your Gmail at 8 PM CST.
"""

import os
import smtplib
import sqlite3
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import asyncio
import logging

logger = logging.getLogger(__name__)

# Email configuration
GMAIL_ADDRESS = os.getenv('GMAIL_ADDRESS')  # Your Gmail address
GMAIL_APP_PASSWORD = os.getenv('GMAIL_APP_PASSWORD')  # Gmail app password
SEND_TIME_HOUR = 20  # 8 PM CST

DB_PATH = 'data/traderdb.db'


def get_daily_stats():
    """Get today's trading statistics from database"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Get today's date range
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = datetime.now()

    # Get all trades from today
    c.execute('''
        SELECT * FROM proven_trades
        WHERE entry_time >= ? AND entry_time <= ?
        ORDER BY entry_time DESC
    ''', (today_start.isoformat(), today_end.isoformat()))

    rows = c.fetchall()
    conn.close()

    if not rows:
        return None

    # Calculate stats
    total_trades = len(rows)
    winning_trades = sum(1 for row in rows if row[10] and row[10] == 'target')  # exit_reason = 'target'
    total_pnl = sum(row[14] for row in rows if row[14])  # net_pnl_usd

    # Get open positions
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM proven_trades WHERE status = 'OPEN'")
    open_positions = c.fetchone()[0]
    conn.close()

    return {
        'date': today_start.strftime('%Y-%m-%d'),
        'total_trades': total_trades,
        'winning_trades': winning_trades,
        'win_rate': (winning_trades / total_trades * 100) if total_trades > 0 else 0,
        'total_pnl': total_pnl,
        'open_positions': open_positions,
        'trades': rows
    }


def format_email_body(stats):
    """Format the daily report as HTML email"""
    if not stats:
        return "<h2>No trades today</h2><p>The bot found no valid entry signals today.</p>"

    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #2c3e50;">📊 Daily Trading Report - {stats['date']}</h2>

        <div style="background-color: #ecf0f1; padding: 20px; border-radius: 8px; margin: 20px 0;">
            <h3 style="margin-top: 0;">Performance Summary</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 8px;"><strong>Total Trades:</strong></td>
                    <td style="padding: 8px; text-align: right;">{stats['total_trades']}</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Winning Trades:</strong></td>
                    <td style="padding: 8px; text-align: right;">{stats['winning_trades']}</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Win Rate:</strong></td>
                    <td style="padding: 8px; text-align: right;">{stats['win_rate']:.1f}%</td>
                </tr>
                <tr style="background-color: {'#d4edda' if stats['total_pnl'] > 0 else '#f8d7da'};">
                    <td style="padding: 8px;"><strong>Total P&L:</strong></td>
                    <td style="padding: 8px; text-align: right; font-size: 18px; font-weight: bold;">
                        ${stats['total_pnl']:.2f}
                    </td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Open Positions:</strong></td>
                    <td style="padding: 8px; text-align: right;">{stats['open_positions']}</td>
                </tr>
            </table>
        </div>

        <div style="margin: 20px 0;">
            <h3>Recent Trades</h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 12px;">
                <thead>
                    <tr style="background-color: #34495e; color: white;">
                        <th style="padding: 8px; text-align: left;">Ticker</th>
                        <th style="padding: 8px; text-align: right;">Entry</th>
                        <th style="padding: 8px; text-align: right;">Exit</th>
                        <th style="padding: 8px; text-align: right;">P&L</th>
                    </tr>
                </thead>
                <tbody>
    """

    # Add recent trades (last 10)
    for i, trade in enumerate(stats['trades'][:10]):
        ticker = trade[1]
        entry_price = trade[3]
        exit_price = trade[8] if trade[8] else 'Open'
        pnl = trade[14] if trade[14] else 0
        pnl_color = '#27ae60' if pnl > 0 else '#e74c3c'

        html += f"""
                    <tr style="border-bottom: 1px solid #ecf0f1;">
                        <td style="padding: 8px;">{ticker}</td>
                        <td style="padding: 8px; text-align: right;">${entry_price:.4f}</td>
                        <td style="padding: 8px; text-align: right;">{f'${exit_price:.4f}' if isinstance(exit_price, float) else exit_price}</td>
                        <td style="padding: 8px; text-align: right; color: {pnl_color}; font-weight: bold;">
                            ${pnl:.2f}
                        </td>
                    </tr>
        """

    html += """
                </tbody>
            </table>
        </div>

        <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #ecf0f1; font-size: 12px; color: #7f8c8d;">
            <p><strong>Strategy:</strong> 3-Candle sum -6% + RSI&lt;35 → +5% target</p>
            <p><strong>Generated:</strong> """ + datetime.now().strftime('%Y-%m-%d %I:%M %p CST') + """</p>
        </div>
    </body>
    </html>
    """

    return html


def send_email(to_address, subject, html_body):
    """Send email via Gmail SMTP"""
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = GMAIL_ADDRESS
    msg['To'] = to_address

    html_part = MIMEText(html_body, 'html')
    msg.attach(html_part)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        logger.info(f"✅ Daily report sent to {to_address}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to send email: {e}")
        return False


async def daily_report_loop():
    """Main loop - sends report at 8 PM CST every day"""
    logger.info("📧 Daily email reporter started (sends at 8 PM CST)")

    while True:
        now = datetime.now()

        # Check if it's 8 PM
        if now.hour == SEND_TIME_HOUR and now.minute < 5:  # Send within first 5 minutes of 8 PM
            logger.info("📊 Generating daily report...")

            stats = get_daily_stats()
            html_body = format_email_body(stats)

            subject = f"📊 Trading Bot Daily Report - {now.strftime('%Y-%m-%d')}"
            if stats:
                subject += f" | P&L: ${stats['total_pnl']:.2f}"

            send_email(GMAIL_ADDRESS, subject, html_body)

            # Sleep for 1 hour to avoid sending multiple times
            await asyncio.sleep(3600)
        else:
            # Check every 5 minutes
            await asyncio.sleep(300)


def start_daily_reporter():
    """Start the daily email reporter"""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        logger.warning("⚠️  Daily email reporter disabled - GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set")
        return None

    return asyncio.create_task(daily_report_loop())


if __name__ == '__main__':
    # Test report
    logging.basicConfig(level=logging.INFO)
    print("Testing daily report generation...")
    stats = get_daily_stats()
    if stats:
        print(f"\nStats for {stats['date']}:")
        print(f"  Trades: {stats['total_trades']}")
        print(f"  Win Rate: {stats['win_rate']:.1f}%")
        print(f"  P&L: ${stats['total_pnl']:.2f}")
        print(f"\nHTML body preview:")
        print(format_email_body(stats)[:500] + "...")
    else:
        print("No trades today")
