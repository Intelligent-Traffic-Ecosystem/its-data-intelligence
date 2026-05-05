# B2 — Data & Intelligence
#its-data-intelligence
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
# Install dependencies (full dev stack incl. testcontainers)
pip install -r requirements.txt

# Run unit tests (skips integration)
make test

# Run integration tests (requires Docker — spins up Kafka + Postgres)
make test-integration

# Run everything
make test-all

# Lint
make lint

# Run mock producer
make mock                                      # plain B1-shaped events
python tools/mock_producer.py --with-lanes     # include lane_id ~80% of events
python tools/mock_producer.py --no-speed       # exercise B2 speed fallback

# View logs
make logs

# Tear down
make down
```

## Retention

Per SRS §7, the processor sweeps old rows on a configurable interval
(default 1 hour): `traffic_events` are kept for 24h, `traffic_metrics` for
30 days. Override via `RETENTION_EVENTS_HOURS` / `RETENTION_METRICS_DAYS`.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/cameras` | List active cameras |
| GET | `/metrics/current?camera_id=X` | Latest metrics for a camera |
| GET | `/metrics/history?camera_id=X&from=T1&to=T2` | Historical metrics |
| GET | `/congestion/current` | Current congestion for all cameras |
| GET | `/health` | Liveness probe (checks Kafka + Postgres) |
| GET | `/metrics` | Prometheus scrape endpoint (b2-api on :8000) |
| WS | `/ws/metrics[?camera_id=X]` | Live metric updates (every 5s); optional camera filter |

The processor exposes its own Prometheus endpoint on port `9100`:
`b2_events_processed_total`, `b2_events_dropped_total{reason}`,
`b2_window_flushes_total`, `b2_kafka_consumer_lag`.

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
