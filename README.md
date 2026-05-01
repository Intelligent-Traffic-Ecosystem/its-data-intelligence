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
cp .env.example .env
docker compose up -d

# Forcing a clean rebuild (skips BuildKit layer cache)
# `--build --force-recreate` still uses the layer cache — use this instead:
docker compose build --no-cache --pull
docker compose up -d --force-recreate

# Send mock traffic events (for development without B1)
pip install kafka-python
python tools/mock_producer.py --cameras 4 --rate 10

# Check the API
curl http://localhost:8000/health
curl http://localhost:8000/cameras
curl http://localhost:8000/congestion/current
```

## Demo Runbook

A step-by-step walkthrough that exercises every capability the team built. Run from the **repo root** unless noted.

### Prerequisites
- Docker Desktop running
- Python 3.11+ on the host (for `tools/mock_producer.py`)
- Optional: `jq`, `websocat` for nicer output

### 1 — Bring up the stack
```bash
cp .env.example .env
docker compose up -d
docker compose ps                              # all services Healthy
docker compose exec b2-stream-processor alembic upgrade head
```

> Need a clean rebuild? `docker compose up -d --build --force-recreate` still hits the BuildKit layer cache. Run `docker compose build --no-cache --pull` first, then `docker compose up -d --force-recreate`.

### 2 — Health probe (Kafka + Postgres aware)
```bash
curl -s http://localhost:8000/health | jq
# {"status":"ok","kafka":"ok","postgres":"ok"}
```

Stop Kafka briefly to see the degraded path:
```bash
docker compose stop kafka
curl -s http://localhost:8000/health | jq
# {"status":"degraded","kafka":"unreachable","postgres":"ok"}   ← still HTTP 200
docker compose start kafka
```

### 3 — Structured JSON logs
```bash
docker compose logs --tail=20 b2-stream-processor | jq -c .
```
Every line is one JSON object with `time`, `level`, `logger`, `message`. Library logs (kafka-python, sqlalchemy) come through the same formatter.

### 4 — Send B1-shaped events
```bash
cd services/b2-data
pip install kafka-python
python tools/mock_producer.py --cameras 4 --rate 10 --brokers localhost:9092
```

In another terminal, watch windows close:
```bash
docker compose logs -f b2-stream-processor | jq -c 'select(.message | startswith("window_flushed"))'
```

### 5 — Verify all B1 fields are persisted
```bash
docker compose exec postgres psql -U user -d traffic -c "
SELECT camera_id, ts, vehicle_id, class, frame_id, confidence,
       bbox_x, bbox_y, bbox_w, bbox_h, lane_id, speed_kmh
FROM traffic_events ORDER BY ts DESC LIMIT 5;"
```
`frame_id`, `confidence`, full `bbox`, and `lane_id` were dropped before. They're now in the row.

### 6 — Inspect aggregated camera-wide metrics
```bash
docker compose exec postgres psql -U user -d traffic -c "
SELECT camera_id, window_start, vehicle_count,
       round(avg_speed_kmh::numeric, 1) AS avg_speed,
       congestion_level, round(congestion_score::numeric, 2) AS score
FROM traffic_metrics
WHERE lane_id IS NULL
ORDER BY window_start DESC LIMIT 5;"
```

### 7 — Per-lane breakdown
Restart the producer with lanes:
```bash
python tools/mock_producer.py --cameras 4 --rate 10 --with-lanes
```

After ~15s:
```bash
docker compose exec postgres psql -U user -d traffic -c "
SELECT camera_id, lane_id, window_start, vehicle_count, congestion_level
FROM traffic_metrics
WHERE lane_id IS NOT NULL
ORDER BY window_start DESC, camera_id, lane_id LIMIT 12;"
```
One camera now produces both a camera-wide row and per-lane rows for the same 5s window.

### 8 — Speed fallback when B1 omits speed_estimate
```bash
python tools/mock_producer.py --cameras 4 --rate 15 --no-speed
```

After ~3 windows:
```bash
docker compose exec postgres psql -U user -d traffic -c "
SELECT camera_id, window_start, round(avg_speed_kmh::numeric, 1) AS avg_speed
FROM traffic_metrics WHERE lane_id IS NULL
ORDER BY window_start DESC LIMIT 5;"
```
`avg_speed` is non-zero — `SpeedTracker` computed it from inter-frame centroid displacement.

### 9 — REST endpoints (the contract for B3)
```bash
curl -s localhost:8000/cameras | jq
curl -s "localhost:8000/metrics/current?camera_id=cam_01" | jq
curl -s "localhost:8000/metrics/history?camera_id=cam_01&from=2026-05-01T00:00:00Z&to=2026-05-02T00:00:00Z" | jq
curl -s localhost:8000/congestion/current | jq
```

### 10 — WebSocket live stream
```bash
# all cameras (default — camera-wide rows only)
websocat ws://localhost:8000/ws/metrics

# single camera
websocat 'ws://localhost:8000/ws/metrics?camera_id=cam_01'
```

### 11 — Prometheus metrics (two endpoints)
```bash
# API process: 8000
curl -s localhost:8000/metrics | grep -E '^b2_api_requests_total|^b2_api_request_latency'

# Processor process: 9100
curl -s localhost:9100/metrics | grep -E '^b2_events_processed_total|^b2_window_flushes_total|^b2_kafka_consumer_lag'
```

### 12 — Manual retention sweep
```bash
docker compose exec b2-stream-processor python -c "
from shared.db import SessionLocal
from processor.retention import sweep
events_deleted, metrics_deleted = sweep(SessionLocal)
print(f'events_deleted={events_deleted} metrics_deleted={metrics_deleted}')"
```

### 13 — Integration tests against real Kafka + Postgres
```bash
cd services/b2-data
pip install -r requirements.txt
make test-integration                          # ~60–90s on first run
```

Coverage:
- `test_kafka_schema_compat` — 100 mock events round-trip through validator
- `test_end_to_end_flow` — produce → consume → DB row + per-lane row
- `test_health_with_real_kafka` — `/health` returns `kafka=ok`
- `test_retention` — sweeper deletes old rows

## Capability Matrix (what to highlight)

| Capability | Where to look | Talking point |
|---|---|---|
| Wire-compatible with B1 verbatim | `traffic_events` table | every B1 field persisted |
| Per-lane analytics | `traffic_metrics WHERE lane_id IS NOT NULL` | single table, one query path, one writer |
| Speed fallback | `--no-speed` run | tracking-based estimate when B1 omits it |
| Kafka-aware health | `/health` while Kafka is stopped | reports `degraded`, still HTTP 200 |
| Structured logs | `docker compose logs ... \| jq` | one JSON object per line, lib logs included |
| Two Prometheus endpoints | `:8000/metrics`, `:9100/metrics` | API + processor instrumented |
| WebSocket camera filter | `?camera_id=X` | B3 can subscribe to one camera |
| Retention enforced | manual `sweep()` invocation | events 24h, metrics 30d (SRS §7) |
| Integration tests | `make test-integration` | testcontainers Kafka + Postgres in CI |

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

## Known caveats / Backlog

These are tracked on the project board so we don't lose them:

- **Pixel-to-meter calibration is a placeholder** (`SPEED_TRACKER_PIXEL_TO_METER=0.05`). Absolute km/h numbers will be off until B1 hands over per-camera survey data. Tracked in [#19](https://github.com/Intelligent-Traffic-Ecosystem/its-data-intelligence/issues/19).
- **In-memory windowing, not PyFlink.** Fine for the demo and the load we expect; PyFlink migration is on the backlog ([#20](https://github.com/Intelligent-Traffic-Ecosystem/its-data-intelligence/issues/20)) for after the demo.
- **Auth is delegated to Kong / Keycloak (B4).** B2 endpoints are unauthenticated when hit directly — fine inside the cluster, defense-in-depth tracked in [#23](https://github.com/Intelligent-Traffic-Ecosystem/its-data-intelligence/issues/23).
- **Wall-clock vs event-time flushing** ([#21](https://github.com/Intelligent-Traffic-Ecosystem/its-data-intelligence/issues/21)) — late events from a clock-skewed B1 camera could be lost; not blocking since cameras are NTP-synced (<100ms drift).

## Project Structure

```
src/
  shared/     — Config, DB models, Pydantic schemas (shared by both services)
  processor/  — Stream processor (Kafka consumer, windowed aggregation, Postgres writer)
  api/        — FastAPI REST + WebSocket service
tools/        — Mock event producer for development
migrations/   — Alembic database migrations
tests/        — Unit tests + tests/integration/ (testcontainers)
docker/       — Dockerfiles for each service
```
