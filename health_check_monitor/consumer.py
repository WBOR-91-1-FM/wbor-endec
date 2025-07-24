# type ignores since VSCode struggles to see pika types correctly

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import threading
import time
from typing import NoReturn

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

import pika
import requests

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class HealthCheckMonitor:
    """Monitors RabbitMQ health check messages and sends Discord alerts on timeout."""

    def __init__(self) -> None:
        """Initialize the health check monitor with configuration from env variables."""
        self.rabbitmq_url = os.getenv(
            "RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"
        )
        self.queue_name = os.getenv("HEALTH_CHECK_QUEUE", "health_checks")
        self.exchange_name = os.getenv("RABBITMQ_EXCHANGE_NAME", "healthcheck")
        self.routing_key = os.getenv(
            "RABBITMQ_HEALTHCHECK_ROUTING_KEY", "health.wbor-endec"
        )
        self.discord_webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        self.check_interval = int(
            os.getenv("CHECK_INTERVAL_SECONDS", "300")
        )  # 5 minutes default
        self.timeout_threshold = int(
            os.getenv("TIMEOUT_THRESHOLD_SECONDS", "600")
        )  # 10 minutes default
        self.timezone_str = os.getenv("TIMEZONE", "America/New_York")
        self.timezone = ZoneInfo(self.timezone_str)

        self.last_health_check: datetime | None = None
        self.connection: pika.BlockingConnection | None = None
        self.channel: pika.channel.Channel | None = None  # type: ignore[attr-defined]
        self.is_running = True

        if not self.discord_webhook_url:
            logger.error("DISCORD_WEBHOOK_URL environment variable is required")
            msg = "Discord webhook URL not configured"
            raise ValueError(msg)

        logger.info(
            "Health check monitor configured with timezone: %s", self.timezone_str
        )

    def connect_rabbitmq(self) -> None:
        """Connect to RabbitMQ and declare the exchange, queue, and binding."""
        try:
            self.connection = pika.BlockingConnection(
                pika.URLParameters(self.rabbitmq_url)
            )
            self.channel = self.connection.channel()

            # Declare the exchange
            self.channel.exchange_declare(  # type: ignore[attr-defined]
                exchange=self.exchange_name, exchange_type="topic", durable=True
            )

            # Declare the queue
            self.channel.queue_declare(queue=self.queue_name, durable=True)  # type: ignore[attr-defined]

            # Bind the queue to the exchange with the routing key
            self.channel.queue_bind(  # type: ignore[attr-defined]
                exchange=self.exchange_name,
                queue=self.queue_name,
                routing_key=self.routing_key,
            )

            logger.info(
                "Connected to RabbitMQ - queue '%s' bound to exchange '%s' with routing"
                " key '%s'",
                self.queue_name,
                self.exchange_name,
                self.routing_key,
            )
        except Exception:
            logger.exception("Failed to connect to RabbitMQ")
            raise

    def send_discord_alert(self, message: str) -> None:
        """Send an alert message to Discord via webhook.

        Args:
            message: The alert message to send.
        """
        if not self.discord_webhook_url:
            logger.error("Discord webhook URL not configured")
            return

        try:
            payload = {"content": message, "username": "wbor-endec Health Monitor"}
            response = requests.post(self.discord_webhook_url, json=payload, timeout=30)
            response.raise_for_status()
            logger.info("Discord alert sent successfully")
        except Exception:
            logger.exception("Failed to send Discord alert")

    def process_health_check(
        self,
        ch: pika.channel.Channel,  # type: ignore[attr-defined]
        method: pika.spec.Basic.Deliver,  # type: ignore[attr-defined]
        _properties: pika.spec.BasicProperties,  # type: ignore[attr-defined]
        body: bytes,
    ) -> None:
        """Process incoming health check messages from RabbitMQ.

        Args:
            ch: The channel object.
            method: Delivery method containing delivery information.
            _properties: Message properties (unused).
            body: The message body as bytes.
        """
        try:
            message = json.loads(body.decode("utf-8"))
            self.last_health_check = datetime.now(timezone.utc)
            # Log with local timezone for readability
            local_time = self.last_health_check.astimezone(self.timezone)
            logger.info(
                "Received health check at %s (%s): %s",
                local_time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                self.timezone_str,
                message,
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except Exception:
            logger.exception("Error processing health check message")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    def monitor_timeout(self) -> None:
        """Monitor for health check timeouts and send alerts when detected."""
        while self.is_running:
            time.sleep(self.check_interval)

            if not self.is_running:
                break

            current_time = datetime.now(timezone.utc)

            if self.last_health_check is None:
                logger.warning("No health check messages received yet")
                continue

            time_since_last_check = current_time - self.last_health_check

            if time_since_last_check.total_seconds() > self.timeout_threshold:
                seconds_since = int(time_since_last_check.total_seconds())
                # Convert UTC timestamp to local timezone for display
                last_check_local = self.last_health_check.astimezone(self.timezone)
                last_check_str = last_check_local.strftime("%Y-%m-%d %H:%M:%S %Z")
                alert_message = (
                    "🚨 **WBOR ENDEC Health Check Alert** 🚨\n"
                    f"No health check received from `wbor-endec` for {seconds_since} "
                    "seconds\n"
                    f"Last health check: `{last_check_str}`\n"
                    f"Threshold: `{self.timeout_threshold}` seconds\n\n"
                    "Please investigate that the ENDEC system is powered on and the "
                    "serial connection is working properly."
                )
                logger.warning(
                    "Health check timeout detected: %s seconds",
                    time_since_last_check.total_seconds(),
                )
                self.send_discord_alert(alert_message)

                # Reset to avoid spam alerts
                self.last_health_check = current_time

    def _ensure_channel(self) -> None:
        """Ensure RabbitMQ channel is established.

        Raises:
            RuntimeError: If channel is not available.
        """
        if not self.channel:
            msg = "Failed to establish RabbitMQ channel"
            raise RuntimeError(msg)

    def _raise_channel_error(self) -> NoReturn:
        """Raise an error when RabbitMQ channel is not initialized.

        Raises:
            ValueError: Always raised with channel error message.
        """
        msg = "RabbitMQ channel is not initialized"
        raise ValueError(msg)

    def start_consuming(self) -> None:
        """Start consuming health check messages from RabbitMQ."""
        try:
            self.connect_rabbitmq()
            self._ensure_channel()

            # Start timeout monitor in separate thread
            timeout_thread = threading.Thread(target=self.monitor_timeout, daemon=True)
            timeout_thread.start()

            # mypy knows channel is not None after _ensure_channel() call
            if self.channel is None:
                self._raise_channel_error()

            self.channel.basic_consume(
                queue=self.queue_name,
                on_message_callback=self.process_health_check,
            )

            logger.info("Starting to consume health check messages...")
            self.channel.start_consuming()

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            self.is_running = False
            if self.channel:
                self.channel.stop_consuming()
        except Exception:
            logger.exception("Error in consumer")
            raise
        finally:
            if self.connection and not self.connection.is_closed:
                self.connection.close()


if __name__ == "__main__":
    monitor = HealthCheckMonitor()
    monitor.start_consuming()
