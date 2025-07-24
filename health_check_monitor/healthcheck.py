"""Health check script for the wbor-endec health check monitor container.

Verifies that the container can connect to RabbitMQ and access the required
exchange and queue for health check monitoring.
"""

import logging
import os
import sys
from typing import NoReturn

import pika

# Suppress pika logging noise
logging.getLogger("pika").setLevel(logging.WARNING)


def health_check() -> None:
    """Perform health check by verifying RabbitMQ connection and resources."""
    try:
        # Get configuration from environment variables
        rabbitmq_url = os.environ.get(
            "RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/"
        )
        queue_name = os.environ.get("HEALTH_CHECK_QUEUE", "health_checks")
        exchange_name = os.environ.get("RABBITMQ_EXCHANGE_NAME", "healthcheck")
        routing_key = os.environ.get(
            "RABBITMQ_HEALTHCHECK_ROUTING_KEY", "health.wbor-endec"
        )

        # Connect to RabbitMQ
        connection = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
        channel = connection.channel()

        # Verify exchange exists (passive=True means don't create, just check)
        try:
            channel.exchange_declare(
                exchange=exchange_name,
                exchange_type="topic",
                durable=True,
                passive=True,
            )
        except Exception as e:
            msg = f"Exchange '{exchange_name}' not accessible: {e}"
            raise RuntimeError(msg) from e

        # Verify queue exists and is accessible
        try:
            channel.queue_declare(queue=queue_name, durable=True, passive=True)
        except Exception as e:
            msg = f"Queue '{queue_name}' not accessible: {e}"
            raise RuntimeError(msg) from e

        # Verify queue binding (try to bind - if already bound, this is idempotent)
        try:
            channel.queue_bind(
                exchange=exchange_name, queue=queue_name, routing_key=routing_key
            )
        except Exception as e:
            msg = (
                f"Cannot bind queue '{queue_name}' to exchange '{exchange_name}' with "
                f"routing key '{routing_key}': {e}"
            )
            raise RuntimeError(msg) from e

        # Clean up
        connection.close()

        print("✅ Health check passed:")
        print(f"   - Connected to RabbitMQ: {rabbitmq_url}")
        print(f"   - Exchange '{exchange_name}' accessible")
        print(f"   - Queue '{queue_name}' accessible")
        print(f"   - Binding verified with routing key '{routing_key}'")

    except Exception as e:
        print(f"❌ Health check failed: {e}")
        fail_with_error()


def fail_with_error() -> NoReturn:
    """Exit with error status."""
    sys.exit(1)


if __name__ == "__main__":
    health_check()
