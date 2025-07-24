FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY consumer.py .
COPY healthcheck.py .

# Create non-root user
RUN useradd --create-home --shell /bin/bash monitor
USER monitor

# Health check - verify RabbitMQ connection and consumer is running
HEALTHCHECK --interval=30s --timeout=15s --start-period=30s --retries=3 \
    CMD python healthcheck.py

CMD ["python", "consumer.py"]