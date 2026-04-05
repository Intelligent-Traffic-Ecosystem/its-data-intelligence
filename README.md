# B2 — Data & Intelligence

Stream processing and analytics API for the Intelligent Traffic System.

## Architecture

```
B1 → Kafka → Stream Processor → PostgreSQL → FastAPI API → B3
```

Two services:
- **b2-stream-processor** — Consumes events from Kafka, aggregates in 5-second windows, computes metrics, classifies congestion, writes to Postgres
- **b2-api** — REST + WebSocket API serving traffic metrics to B3 dashboard

## Quick Start

```bash
# Start everything (Kafka, Postgres, both B2 services)
docker compose up -d

# Send mock traffic events (for development without B1)
pip install kafka-python
python tools/mock_producer.py --cameras 4 --rate 10

# Check the API
curl http://localhost:8000/health
curl http://localhost:8000/cameras
curl http://localhost:8000/congestion/current
```

## Development

```bash
# Install dependencies
pip install -r requirements/base.txt -r requirements/api.txt
pip install kafka-python pytest ruff

# Run tests
make test

# Lint
make lint

# Run mock producer
make mock

# View logs
make logs

# Tear down
make down
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/cameras` | List active cameras |
| GET | `/metrics/current?camera_id=X` | Latest metrics for a camera |
| GET | `/metrics/history?camera_id=X&from=T1&to=T2` | Historical metrics |
| GET | `/congestion/current` | Current congestion for all cameras |
| GET | `/health` | Liveness probe |
| GET | `/metrics` | Prometheus scrape endpoint |
| WS | `/ws/metrics` | Live metric updates (every 5s) |

## Configuration

All configuration via environment variables. See `.env.example` for the full list.

## Project Structure

```
src/
  shared/     — Config, DB models, Pydantic schemas (shared by both services)
  processor/  — Stream processor (Kafka consumer, windowed aggregation, Postgres writer)
  api/        — FastAPI REST + WebSocket service
tools/        — Mock event producer for development
migrations/   — Alembic database migrations
tests/        — Unit tests
docker/       — Dockerfiles for each service
```
