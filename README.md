# HyperTrade AI Backend

AI-Powered Trading Platform with Hyperliquid Integration

## Features

- **AI Trading Signals**: Multi-LLM consensus-based signal generation
- **Hyperliquid Integration**: Direct trading on Hyperliquid perpetuals
- **Wallet Management**: Support for master and API wallets
- **Subscription System**: NOWPayments crypto payment integration
- **Real-time Notifications**: Telegram bot and email alerts
- **Affiliate Program**: Referral tracking and commission payouts
- **Analytics Dashboard**: Trade performance and signal statistics

## Tech Stack

- **Framework**: FastAPI with async support
- **Database**: PostgreSQL with SQLAlchemy 2.0 async
- **Cache/Queue**: Redis
- **Task Queue**: Celery with Redis broker
- **Authentication**: JWT with refresh tokens
- **Encryption**: Fernet (AES-128) for wallet keys

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 15+
- Redis 7+
- Docker & Docker Compose (optional)

### Development Setup

1. Clone the repository and navigate to backend:
```bash
cd backend
```

2. Install dependencies with Poetry:
```bash
pip install poetry
poetry install
```

3. Copy environment file:
```bash
cp .env.example .env
```

4. Configure your `.env` file with required values:
- Database credentials
- Redis URL
- JWT secrets
- Hyperliquid API settings
- OpenRouter API key
- NOWPayments API key
- Telegram bot token
- SMTP settings

5. Generate encryption key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

6. Run database migrations:
```bash
alembic upgrade head
```

7. Start the development server:
```bash
uvicorn app.main:app --reload
```

### Docker Setup

1. Configure `.env` file

2. Start all services:
```bash
docker-compose up -d
```

3. Run migrations:
```bash
docker-compose exec api alembic upgrade head
```

## API Documentation

When running in development mode, API documentation is available at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Project Structure

```
backend/
├── alembic/              # Database migrations
├── app/
│   ├── api/v1/          # API endpoints
│   ├── core/            # Security, exceptions, middleware
│   ├── models/          # SQLAlchemy models
│   ├── schemas/         # Pydantic schemas
│   ├── services/        # Business logic
│   │   ├── hyperliquid/ # Hyperliquid API client
│   │   ├── llm/         # LLM integration
│   │   └── trading/     # Signal & trade execution
│   ├── workers/         # Celery tasks
│   └── utils/           # Helper utilities
├── docker/              # Docker configurations
└── tests/               # Test suite
```

## Celery Tasks

Start Celery worker:
```bash
celery -A app.workers.celery_app worker --loglevel=info
```

Start Celery beat (scheduler):
```bash
celery -A app.workers.celery_app beat --loglevel=info
```

## Testing

Run tests:
```bash
pytest tests/ -v
```

With coverage:
```bash
pytest tests/ -v --cov=app --cov-report=html
```

## Environment Variables

See `.env.example` for all configuration options.

### Required Variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Application secret key |
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `JWT_SECRET_KEY` | JWT signing key |
| `ENCRYPTION_KEY` | Fernet encryption key |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `NOWPAYMENTS_API_KEY` | NOWPayments API key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |

## License

Proprietary - All rights reserved
