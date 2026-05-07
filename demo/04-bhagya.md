# 04 — Kafka-aware health, Prometheus, WebSocket camera filter
**Owner:** @Bhagyatgn · **PR:** #9 · **Time:** ~5 min

## What you're showing
- `/health` actually checks both Kafka and Postgres, and reports `degraded` (still HTTP 200) when one fails — so B4's monitor can see partial outages.
- The Prometheus counters `b2_api_requests_total` and `b2_api_request_latency_seconds` get incremented on every request (they were declared but never bumped before).
- WebSocket clients can subscribe to one camera with `?camera_id=X`, and per-lane rows don't leak into the default stream.

## Why it matters
SRS §11 acceptance criteria #6 says health must report status, not 503 — so it stays scrapable when partially degraded. The Prometheus counters being unwired was an existing dead-code path. The WebSocket filter is what B3 needs so dashboards can subscribe to a single junction without filtering client-side.

---

## Demo steps

### 1. Health probe — both subsystems healthy

```bash
curl -s localhost:8000/health | jq
# {"status":"ok","kafka":"ok","postgres":"ok"}
```

> **Talking point:** *"Both fields are 'ok'. Top-level status only flips to 'ok' when both pass."*

### 2. Stop Kafka — show graceful degradation

```bash
docker compose stop kafka
sleep 4
curl -s -w "\nHTTP %{http_code}\n" localhost:8000/health | jq
# {"status":"degraded","kafka":"unreachable","postgres":"ok"}
# HTTP 200
```

> **Talking point:** *"Notice it's still HTTP 200, not 503. If we returned 503, B4's scraper would treat us as 'down' and stop pulling metrics — exactly when we'd want them most. Instead, the body says 'degraded' and B4's alerting matches on the JSON."*

```bash
docker compose start kafka
sleep 12
curl -s localhost:8000/health | jq
# back to "ok"
```

### 3. Prometheus metrics — request counts actually increment

Hit the API a few times:
```bash
for i in {1..10}; do curl -s localhost:8000/cameras > /dev/null; done
```

Now scrape:
```bash
curl -s localhost:8000/metrics | grep -E '^b2_api_requests_total|^b2_api_request_latency_seconds_count'
```

> **Talking point:** *"Counter increments per call, latency histogram observes actual times. Before our fix these were declared in `prometheus.py` but no code ever called `.inc()` — Grafana would have shown a flat 0."*

### 4. Two Prometheus endpoints (API + processor)

```bash
echo '--- API ---'
curl -s localhost:8000/metrics | grep -c '^b2_api_'
echo '--- Processor (port 9100) ---'
curl -s localhost:9100/metrics | grep -E '^b2_events_processed_total|^b2_window_flushes_total|^b2_kafka_consumer_lag'
```

> **Talking point:** *"Two separate processes, two separate endpoints. The processor doesn't have FastAPI so it uses prometheus_client's standalone HTTP server on port 9100."*

### 5. WebSocket — all cameras vs filtered

You'll need a producer running in another tab first.

```bash
# Tab A: pumping events
python tools/mock_producer.py --cameras 4 --rate 10 --brokers localhost:29092 --with-lanes --malformed-rate 0

# Tab B: default WebSocket — all cameras
websocat ws://localhost:8000/ws/metrics
# (see all 4 cameras every 5 seconds)
```

> **Talking point:** *"All 4 cameras, every 5 seconds, camera-wide rows only — per-lane rows don't leak in by default."*

```bash
# Tab B (Ctrl+C, reconnect filtered)
websocat 'ws://localhost:8000/ws/metrics?camera_id=cam_01'
# (only cam_01)
```

> **Talking point:** *"Same connection, just adds `?camera_id=`. The filter happens at the SQL level so we don't ship rows we'll just throw away."*

---

## What I changed
- **`src/api/routes/health.py`** — caches a `KafkaAdminClient` (request_timeout_ms=2000), probes with `list_topics()` in try/except, returns three-status response. `degraded` keeps HTTP 200 so it stays scrapable.
- **`src/api/main.py`** — added a FastAPI middleware that times every request and increments `REQUEST_COUNT.labels(...)` + `REQUEST_LATENCY.labels(endpoint).observe(...)`. Catches unhandled exceptions to bump `PROCESSING_ERRORS`.
- **`src/api/websocket.py`** — endpoint now accepts `?camera_id` Query param; broadcast SQL filters camera-wide rows (`lane_id IS NULL`) so per-lane subscription stays cleanly separable for future work.
- **`tools/mock_producer.py`** — added `--with-lanes` and `--no-speed` flags to support phases #2 and #3 in this demo session.

## Q&A prep
- **Q: Why not 503 when degraded?** Many monitors stop scraping endpoints that return 5xx. We want continuous visibility into partial outages — the body carries the truth.
- **Q: Per-lane WebSocket subscription?** Open issue #22 — discussing with B3 whether they want a separate `/ws/metrics/lanes` channel or an `include_lanes=true` flag on the existing endpoint.
