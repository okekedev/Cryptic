import asyncio
import os
import re
import aiohttp
import logging
from datetime import datetime, timedelta
from collections import defaultdict
import docker

# Configuration
WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "http://telegram-bot:8080/webhook")
MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL", "30"))  # seconds
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Patterns to watch for
ALERT_PATTERNS = {
    'backend': {
        'connection_lost': r'WebSocket connection closed|Disconnected from Coinbase|Connection lost',
        'connection_established': r'Connected to Coinbase|WebSocket connection established|Connected to wss://',
        'error': r'Error:|ERROR|Failed to|Exception',
        'reconnecting': r'Reconnecting|Attempting to reconnect|Retrying connection'
    },
    'spike-detector': {
        'backend_connection_lost': r'Disconnected from backend|Connection failed|Connection refused',
        'backend_connected': r'Connected to backend Socket\.IO',
        'error': r'Error:|ERROR|Failed to'
    }
}

class LogMonitor:
    def __init__(self):
        self.docker_client = docker.from_env()
        self.last_alert_time = defaultdict(lambda: datetime.min)
        self.alert_cooldown = timedelta(seconds=60)  # Don't spam same alert
        self.connection_states = {}
        
    async def send_alert(self, message: str, alert_type: str = "info"):
        """Send alert to Telegram via webhook"""
        emoji = {
            "error": "🚨",
            "warning": "⚠️",
            "success": "✅",
            "info": "ℹ️"
        }.get(alert_type, "📢")
        
        formatted_message = f"{emoji} **LOG ALERT** {emoji}\n\n{message}\n\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"
        
        try:
            async with aiohttp.ClientSession() as session:
                payload = {"text": formatted_message}
                async with session.post(WEBHOOK_URL, json=payload) as response:
                    if response.status == 200:
                        logger.info(f"Alert sent: {alert_type}")
                    else:
                        logger.error(f"Failed to send alert: {response.status}")
        except Exception as e:
            logger.error(f"Error sending alert: {e}")
    
    def should_send_alert(self, container_name: str, alert_key: str) -> bool:
        """Check if we should send this alert (cooldown logic)"""
        key = f"{container_name}:{alert_key}"
        now = datetime.now()
        last_sent = self.last_alert_time[key]
        
        if now - last_sent > self.alert_cooldown:
            self.last_alert_time[key] = now
            return True
        return False
    
    async def monitor_container_logs(self, container_name: str, patterns: dict):
        """Monitor logs for a specific container"""
        try:
            container = self.docker_client.containers.get(container_name)
            if container.status != 'running':
                logger.warning(f"Container {container_name} is not running")
                return
                
            # Get logs from the last monitoring interval
            since = datetime.utcnow() - timedelta(seconds=MONITOR_INTERVAL * 2)
            logs = container.logs(since=since, stream=False, timestamps=True).decode('utf-8')
            
            # Check each pattern
            for alert_key, pattern in patterns.items():
                if re.search(pattern, logs, re.IGNORECASE | re.MULTILINE):
                    # Special handling for connection states
                    if 'connection_lost' in alert_key or 'disconnected' in alert_key:
                        if self.should_send_alert(container_name, alert_key):
                            self.connection_states[container_name] = 'disconnected'
                            await self.send_alert(
                                f"**Container**: {container_name}\n"
                                f"**Event**: Connection Lost\n"
                                f"**Details**: Coinbase WebSocket disconnected",
                                "error"
                            )
                    
                    elif 'connected' in alert_key or 'established' in alert_key:
                        # Only alert on reconnection if we were previously disconnected
                        if self.connection_states.get(container_name) == 'disconnected':
                            if self.should_send_alert(container_name, alert_key):
                                self.connection_states[container_name] = 'connected'
                                await self.send_alert(
                                    f"**Container**: {container_name}\n"
                                    f"**Event**: Connection Restored\n"
                                    f"**Details**: Successfully reconnected to Coinbase",
                                    "success"
                                )
                        else:
                            self.connection_states[container_name] = 'connected'
                    
                    elif 'error' in alert_key:
                        if self.should_send_alert(container_name, alert_key):
                            # Extract the actual error message
                            error_lines = [line for line in logs.split('\n') if re.search(pattern, line, re.IGNORECASE)]
                            if error_lines:
                                error_msg = error_lines[-1].split(']')[-1].strip() if ']' in error_lines[-1] else error_lines[-1]
                                await self.send_alert(
                                    f"**Container**: {container_name}\n"
                                    f"**Event**: Error Detected\n"
                                    f"**Error**: {error_msg[:200]}...",
                                    "error"
                                )
                    
                    elif 'reconnecting' in alert_key:
                        if self.should_send_alert(container_name, alert_key):
                            await self.send_alert(
                                f"**Container**: {container_name}\n"
                                f"**Event**: Reconnection Attempt\n"
                                f"**Details**: Attempting to reconnect to Coinbase",
                                "warning"
                            )
                            
        except docker.errors.NotFound:
            logger.error(f"Container {container_name} not found")
        except Exception as e:
            logger.error(f"Error monitoring {container_name}: {e}")
    
    async def check_container_health(self):
        """Check if critical containers are running"""
        critical_containers = ['backend', 'spike-detector', 'telegram-bot']
        
        for container_name in critical_containers:
            try:
                container = self.docker_client.containers.get(container_name)
                if container.status != 'running':
                    if self.should_send_alert(container_name, 'not_running'):
                        await self.send_alert(
                            f"**Container**: {container_name}\n"
                            f"**Status**: {container.status}\n"
                            f"**Action Required**: Container is not running!",
                            "error"
                        )
            except docker.errors.NotFound:
                if self.should_send_alert(container_name, 'not_found'):
                    await self.send_alert(
                        f"**Container**: {container_name}\n"
                        f"**Status**: NOT FOUND\n"
                        f"**Action Required**: Container does not exist!",
                        "error"
                    )
    
    async def run(self):
        """Main monitoring loop"""
        logger.info("Starting Docker log monitor...")
        
        # Send startup notification
        await self.send_alert("Log Monitor started - Monitoring Docker containers for issues", "info")
        
        while True:
            try:
                # Check container health
                await self.check_container_health()
                
                # Monitor logs for each container
                for container_name, patterns in ALERT_PATTERNS.items():
                    await self.monitor_container_logs(container_name, patterns)
                
                await asyncio.sleep(MONITOR_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(MONITOR_INTERVAL)

async def main():
    """Main entry point"""
    monitor = LogMonitor()
    await monitor.run()

if __name__ == "__main__":
    asyncio.run(main())