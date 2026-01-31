.PHONY: help install dev run test lint format migrate migrate-create migrate-down docker-up docker-down docker-build clean celery celery-beat shell db-shell redis-cli

# Default target
help:
	@echo ""
	@echo "USEALPHA Backend - Available Commands"
	@echo "========================================"
	@echo ""
	@echo "Setup & Installation:"
	@echo "  make install          Install dependencies with uv"
	@echo "  make install-dev      Install with dev dependencies"
	@echo "  make setup            Full setup (install + migrate)"
	@echo ""
	@echo "Development:"
	@echo "  make dev              Run development server with auto-reload"
	@echo "  make run              Run production server"
	@echo "  make shell            Open Python shell with app context"
	@echo ""
	@echo "Database:"
	@echo "  make migrate          Run database migrations"
	@echo "  make migrate-create   Create new migration (NAME=migration_name)"
	@echo "  make migrate-down     Rollback last migration"
	@echo "  make migrate-reset    Reset all migrations"
	@echo "  make db-shell         Open PostgreSQL shell"
	@echo ""
	@echo "Background Workers:"
	@echo "  make celery           Start Celery worker"
	@echo "  make celery-beat      Start Celery beat scheduler"
	@echo "  make celery-flower    Start Flower monitoring (port 5555)"
	@echo ""
	@echo "Testing & Quality:"
	@echo "  make test             Run all tests"
	@echo "  make test-cov         Run tests with coverage report"
	@echo "  make lint             Run linters (ruff, mypy)"
	@echo "  make format           Format code with ruff"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-up        Start all services with Docker Compose"
	@echo "  make docker-down      Stop all Docker services"
	@echo "  make docker-build     Build Docker images"
	@echo "  make docker-logs      View Docker logs"
	@echo ""
	@echo "Utilities:"
	@echo "  make clean            Remove cache and build files"
	@echo "  make redis-cli        Open Redis CLI"
	@echo "  make generate-key     Generate new secret key"
	@echo ""

# ============================================
# Setup & Installation (using uv)
# ============================================

install:
	uv sync --no-dev

install-dev:
	uv sync

setup: install-dev migrate
	@echo "Setup complete!"

# Generate requirements.txt from pyproject.toml
requirements:
	uv pip compile pyproject.toml -o requirements.txt

requirements-dev:
	uv pip compile pyproject.toml --all-extras -o requirements-dev.txt

# ============================================
# Development
# ============================================

dev:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Note: Use 'make run-prod' for multi-worker production (Linux/Docker only)
run:
	uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# Production with multiple workers (Linux/Docker only - not Windows)
run-prod:
	uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

shell:
	uv run python -i -c "from app.database import *; from app.models import *; print('USEALPHA Shell Ready')"

# ============================================
# Database Migrations
# ============================================

migrate:
	uv run alembic upgrade head

migrate-create:
	uv run alembic revision --autogenerate -m "$(NAME)"

migrate-down:
	uv run alembic downgrade -1

migrate-reset:
	uv run alembic downgrade base
	uv run alembic upgrade head

migrate-history:
	uv run alembic history

seed-admin:
	uv run python -m app.scripts.seed_admin

db-shell:
	docker exec -it usealpha-postgres psql -U postgres -d usealpha

# ============================================
# Celery Workers
# ============================================

worker:
	uv run celery -A app.workers.celery_app worker --pool=solo --loglevel=info --concurrency=4

beat:
	uv run celery -A app.workers.celery_app beat --loglevel=info

celery-flower:
	uv run celery -A app.workers.celery_app flower --port=5555

celery-purge:
	uv run celery -A app.workers.celery_app purge -f

# ============================================
# Testing
# ============================================

test:
	uv run pytest tests/ -v

test-cov:
	uv run pytest tests/ -v --cov=app --cov-report=html --cov-report=term-missing

test-fast:
	uv run pytest tests/ -v -n auto

test-watch:
	uv run ptw tests/ -- -v

# ============================================
# Code Quality
# ============================================

lint:
	uv run ruff check app/ tests/
	uv run mypy app/

format:
	uv run ruff format app/ tests/
	uv run ruff check --fix app/ tests/

# ============================================
# Docker
# ============================================

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

docker-build:
	docker-compose build

docker-logs:
	docker-compose logs -f

docker-shell:
	docker exec -it usealpha-api /bin/bash

docker-restart:
	docker-compose restart

docker-clean:
	docker-compose down -v --remove-orphans

# ============================================
# Utilities
# ============================================

clean:
	@if exist __pycache__ rd /s /q __pycache__
	@if exist .pytest_cache rd /s /q .pytest_cache
	@if exist .mypy_cache rd /s /q .mypy_cache
	@if exist .ruff_cache rd /s /q .ruff_cache
	@if exist htmlcov rd /s /q htmlcov
	@if exist .coverage del .coverage
	@echo "Cleaned!"

# Unix clean (for Git Bash / WSL)
clean-unix:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true

redis-cli:
	docker exec -it usealpha-redis redis-cli

generate-key:
	@uv run python -c "import secrets; print(secrets.token_urlsafe(32))"

check-env:
	@uv run python -c "from app.config import settings; print('Configuration loaded successfully')"

# ============================================
# Production Deployment
# ============================================

prod-deploy:
	git pull origin main
	uv sync --no-dev
	uv run alembic upgrade head
	@echo "Deployment complete!"

# ============================================
# Hyperliquid Specific
# ============================================

hl-test-connection:
	uv run python -c "from app.services.hyperliquid.client import HyperliquidClient; import asyncio; asyncio.run(HyperliquidClient().test_connection())"

# ============================================
# Backup & Maintenance
# ============================================

backup-db:
	@if not exist backups mkdir backups
	docker exec usealpha-postgres pg_dump -U postgres usealpha > backups/db_backup.sql

restore-db:
	docker exec -i usealpha-postgres psql -U postgres usealpha < $(FILE)

# ============================================
# Quick Commands
# ============================================

# Start Docker services
start-services: docker-up
	@echo "Docker services started"

# Stop everything
stop: docker-down
	@echo "All services stopped"

# Initialize project (first time setup)
init: install-dev
	@echo "Creating .env from .env.example..."
	@if not exist .env copy .env.example .env
	@echo "Starting Docker services..."
	docker-compose up -d postgres redis rabbitmq
	@echo "Waiting for services to be ready..."
	timeout /t 10
	@echo "Running migrations..."
	uv run alembic upgrade head
	@echo ""
	@echo "=========================================="
	@echo "Setup complete! Run 'make dev' to start"
	@echo "=========================================="
