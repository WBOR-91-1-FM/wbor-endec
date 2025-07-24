# WBOR ENDEC Development

This document describes the development setup and design patterns implemented in the WBOR ENDEC project.

## 🚀 Quick Start

```bash
# Install dependencies
make install

# Run linting and formatting
make check

# Run the application
make run

# View available commands
make help
```

## 🔧 Development Tools

### Package Management

- **uv**: Modern Python package manager for fast dependency management - [installation instructions](https://docs.astral.sh/uv/getting-started/installation/)
- **pyproject.toml**: Comprehensive project configuration with dependencies and dev tools

### Code Quality

- **ruff**: Fast Python linter and formatter with comprehensive rules
- **mypy**: Static type checking for Python code
- **GitHub Actions**: Automated CI/CD for code quality checks

### Available Commands

```bash
make install       # Install development dependencies
make format        # Format code with ruff
make lint          # Run linting checks
make typecheck     # Run type checking
make check         # Run all quality checks
make clean         # Clean temporary files
make ci-check      # Run CI-style checks
```

## 🏥 Health Check System

The project implements a comprehensive health check system with two components:

### Health Check Sender (in `endec.py`)

- Sends hourly health check pings via RabbitMQ
- Includes system status, serial port info, and version details
- Automatic retry logic with exponential backoff
- Integrated into the main serial processing loop

### Health Check Monitor (`health_check_monitor/`)

- Docker-based monitoring system
- Consumes health check messages from RabbitMQ
- Sends Discord alerts when health checks are missed
- Configurable timeout thresholds and check intervals

#### Configuration

Add to your secrets file:

```json
{
  "rabbitmq_healthcheck_exchange": "healthcheck",
  "rabbitmq_healthcheck_routing_key": "health.wbor-endec"
}
```

#### Running the Monitor

```bash
cd health_check_monitor
make help          # View available commands
make build         # Build Docker image
make run           # Run the monitor
make logsf         # Follow logs
```

## 📁 Project Structure

```txt
wbor-endec/
├── endec.py                      # Main application with health check integration
├── pyproject.toml               # Project configuration with uv and ruff
├── Makefile                     # Development commands
├── .github/workflows/lint.yml   # CI/CD pipeline
└── health_check_monitor/        # Health monitoring system
    ├── consumer.py              # Health check message consumer
    ├── healthcheck.py           # Container health check script
    ├── Dockerfile               # Container configuration
    ├── Makefile                 # Container management commands
    ├── docker-compose.yml       # Multi-service setup
    └── README.md                # Monitoring system docs
```

## 🔄 Message Flow

```txt
ENDEC Serial Data → endec.py → RabbitMQ Exchanges
                               ├── notifications (EAS alerts)
                               └── healthcheck (system health)
                              ↓
                              health_check_monitor → Discord alerts
```

## 🐛 Development Workflow

1. **Setup**: `make install` to install dependencies
2. **Code**: Make your changes to the codebase
3. **Check**: `make check` to run all quality checks
4. **Test**: `make run` to test the application
5. **Commit**: Git commit your changes
6. **CI**: GitHub Actions will run automated checks

## 📊 Health Check Message Format

```json
{
  "source_application": "wbor-endec",
  "event_type": "health_check", 
  "timestamp_utc": "2025-01-24T10:30:00Z",
  "status": "alive",
  "serial_port": "/dev/ttyUSB0",
  "system_info": {
    "listening_port": "/dev/ttyUSB0",
    "application": "wbor-endec",
    "version": "4.1.1"
  }
}
```

## 🔧 Configuration Files

- **`pyproject.toml`**: Python project configuration with comprehensive linting rules
- **`.github/workflows/lint.yml`**: CI/CD pipeline for automated quality checks
- **`health_check_monitor/.env.example`**: Environment template for monitoring system
- **`Makefile`**: Development commands for the main project
- **`health_check_monitor/Makefile`**: Container management commands
