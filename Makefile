# Makefile for wbor-endec development

.PHONY: help install format lint check clean test run

# Default target
help: ## Show this help message
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# Development setup
install: ## Install development dependencies using uv
	uv sync --dev

# Code formatting and linting
format: ## Format code using ruff
	uv run ruff format .

lint: ## Run linting checks with ruff
	uv run ruff check .

lint-fix: ## Run linting checks and auto-fix issues where possible
	uv run ruff check --fix .

lint-unsafe-fix: ## Run linting checks and auto-fix issues with unsafe fixes
	uv run ruff check --fix --unsafe-fixes .

typecheck: ## Run type checking with mypy
	uv run mypy .

check: format lint typecheck ## Run formatting, linting, and type checks

# Development workflow
run: ## Run the application in development mode
	uv run python endec.py --config config.json

run-debug: ## Run the application with debug logging
	uv run python endec.py --config config.json

# Cleanup
clean: ## Clean up temporary files and caches
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -name ".ruff_cache" -exec rm -rf {} +

# Service management (requires sudo)
service-install: ## Install systemd service (requires sudo)
	sudo cp wbor-endec.service /etc/systemd/system/
	sudo systemctl daemon-reload

service-enable: ## Enable systemd service (requires sudo)
	sudo systemctl enable wbor-endec.service

service-start: ## Start systemd service (requires sudo)
	sudo systemctl start wbor-endec.service

service-stop: ## Stop systemd service (requires sudo)
	sudo systemctl stop wbor-endec.service

service-restart: ## Restart systemd service (requires sudo)
	sudo systemctl restart wbor-endec.service

service-status: ## Check systemd service status
	sudo systemctl status wbor-endec.service

service-logs: ## View systemd service logs
	sudo journalctl -u wbor-endec.service -f

# Environment setup
config-copy: ## Copy config.json.example to config.json
	@if [ -f config.json.example ]; then \
		cp config.json.example config.json; \
		echo "Copied config.json.example to config.json - please edit with your configuration"; \
	else \
		echo "config.json.example not found - create it first"; \
	fi

# Quick development workflow
dev-setup: install config-copy ## Complete development setup
	@echo "Development setup complete!"
	@echo "Next steps:"
	@echo "1. Edit config.json and secrets.json with your configuration"
	@echo "2. Run 'make run' to start the application"

# Continuous integration helpers
ci-check: ## Run all checks for CI (format check + lint + typecheck)
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy .

ci-install: ## Install dependencies for CI
	uv sync --frozen

# Show project info
info: ## Show project information
	@echo "WBOR ENDEC Development"
	@echo "====================="
	@echo "Python version: $(shell python --version)"
	@echo "UV version: $(shell uv --version 2>/dev/null || echo 'uv not installed')"
	@echo "Project directory: $(PWD)"
	@echo "Virtual environment: $(shell echo $VIRTUAL_ENV || echo 'Not in virtual environment')"
	@echo ""
	@echo "Run 'make help' to see available commands"

# Health check monitor targets
health-monitor-build: ## Build the health check monitor Docker image
	cd health_check_monitor && make build

health-monitor-run: ## Run the health check monitor
	cd health_check_monitor && make run

health-monitor-logs: ## View health check monitor logs
	cd health_check_monitor && make logsf

health-monitor-stop: ## Stop the health check monitor
	cd health_check_monitor && make stop

health-monitor-clean: ## Clean up health check monitor
	cd health_check_monitor && make clean