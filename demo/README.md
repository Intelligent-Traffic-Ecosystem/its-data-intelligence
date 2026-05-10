# B2 — Demo session

Five 3-5 minute walkthroughs, one per teammate, that together exercise every capability we shipped in the SRS gap-closure sprint.

## Order
| # | File | Owner | Topic |
|---|---|---|---|
| 1 | `01-tenath.md` | @TenathDilusha | Structured JSON logging + dev requirements |
| 2 | `02-gimsara.md` | @gimsara94 | Full B1 schema fidelity + per-lane metrics |
| 3 | `03-birajith.md` | @birajithk | Speed fallback + retention sweeper |
| 4 | `04-bhagya.md` | @Bhagyatgn | Kafka-aware health + Prometheus + WebSocket filter |
| 5 | `05-charindith.md` | @charindithjaindu | Testcontainers integration suite |

The stack stays up the whole time; each person runs from their own terminal tab.

## One-time setup (everyone does this once before starting)

```bash
cd services/b2-data
docker compose up -d kafka postgres b2-stream-processor b2-api
sleep 25                                    # wait for Kafka to fully boot
curl -s localhost:18001/health | jq          # expect kafka=ok postgres=ok
```

If you have a Python venv ready for the host-side mock producer:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install kafka-python
```

(The dev compose at `services/b2-data/docker-compose.yml` exposes Kafka on `localhost:29092` for host clients. The production-like root compose exposes only `9092` to in-network services.)

## Tear down at the end

```bash
docker compose down -v          # -v also wipes the Postgres volume for a clean re-demo
```

## Tips while presenting

- Keep one terminal tailing the processor with structured JSON output:
  ```bash
  docker compose logs -f b2-stream-processor | jq -c .
  ```
- Keep `psql` open in another tab:
  ```bash
  docker compose exec postgres psql -U user -d traffic
  ```
- Most demos take ~30 seconds of producer traffic to populate enough rows to look at.

## Live data side terminal — keep this open the whole demo

This is the single most visual thing we can show: **the dashboard-facing WebSocket pushing fresh metrics every 5 seconds, in real time.** Open it on a projected screen and leave it running for the whole session — every demo's traffic shows up here automatically.

### Install the WebSocket client (one-time, choose one)

```bash
# Option A — websocat (Rust, single binary)
brew install websocat

# Option B — wscat (npm)
npm install -g wscat
```

### All cameras (default — what B3's main dashboard subscribes to)

```bash
# websocat
websocat ws://localhost:18001/ws/metrics | jq -c .

# wscat
wscat -c ws://localhost:18001/ws/metrics
```

You'll see one JSON array per camera, every 5 seconds, with `vehicle_count`, `avg_speed_kmh`, `congestion_level`, `congestion_score`, `counts_by_class`, etc.

### Single camera (what B3's drill-down view subscribes to)

```bash
websocat 'ws://localhost:18001/ws/metrics?camera_id=cam_01' | jq -c .
```

Only `cam_01` arrives now — the SQL push-down means the server doesn't even fetch other cameras' rows.

### Per-lane breakdowns (separate channel)

```bash
websocat 'ws://localhost:18001/ws/metrics/lanes?camera_id=cam_01' | jq -c .
```

Same cadence, same metric shape — but one row per lane.

### What to point at while it scrolls

- **Cadence:** "One push every 5 seconds — that's our window size."
- **Schema:** "Same shape as `/metrics/current` — B3 can use one Pydantic model for both."
- **Live updates during phase #2:** when @gimsara94 starts the producer with `--with-lanes`, `vehicle_count` and `congestion_level` change in real time.
- **Live updates during phase #3:** when @birajithk runs `--no-speed`, `avg_speed_kmh` stays non-zero — proves the centroid fallback is working live.
- **Live degradation during phase #4:** when @Bhagyatgn stops Kafka, the WebSocket keeps pushing the *last-known* metrics — so the dashboard doesn't go blank during a partial outage.

### One-shot peek without keeping it open

If you don't want to install a client, the same data is available via the REST endpoint:
```bash
watch -n 5 'curl -s localhost:18001/congestion/current | jq'
```
