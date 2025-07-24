#!/bin/bash
# Test script to verify health check functionality

echo "🔍 Testing wbor-endec health check..."
echo "Environment variables:"
echo "  RABBITMQ_URL: ${RABBITMQ_URL:-amqp://guest:guest@rabbitmq:5672/}"
echo "  HEALTH_CHECK_QUEUE: ${HEALTH_CHECK_QUEUE:-health_checks}"
echo "  RABBITMQ_EXCHANGE_NAME: ${RABBITMQ_EXCHANGE_NAME:-healthcheck}"
echo ""

echo "Running health check script..."
python3 healthcheck.py

echo ""
echo "Health check test completed."