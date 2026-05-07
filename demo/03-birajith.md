# 03 — Speed fallback + retention sweeper
**Owner:** @birajithk (Birajith Kiritharajah) · **PR:** #8 · **Time:** ~4 min

## What you're showing
- When B1 omits `speed_estimate` (calibration not yet configured for a camera), B2 estimates speed itself from inter-frame centroid displacement.
- A periodic sweeper keeps the database honest — `traffic_events` ≤ 24h, `traffic_metrics` ≤ 30d, per SRS §7.

## Why it matters
B1 SRS marks `speed_estimate` as optional — only emitted when a camera has been pixel-to-meter calibrated. Without a fallback, `avg_speed_kmh` would be 0 for every uncalibrated camera, which feeds straight into the congestion classifier and gives wrong answers. SRS §7 also mandates retention — without enforcement the metrics table grows unbounded.

---

## Demo steps

### 1. Show the speed-tracking code (briefly)

```bash
sed -n '32,55p' src/processor/speed_tracker.py
```

> **Talking point:** *"It's intentionally simple: distance = sqrt(dx² + dy²) * pixel_to_meter; speed = distance / dt × 3.6. We keep a per-vehicle TTL-evicted map of last-seen centroid + timestamp."*

### 2. Stop any running producer, start one **without** speed

```bash
# Ctrl+C the previous producer if running
python tools/mock_producer.py --cameras 4 --rate 15 --brokers localhost:29092 --no-speed --malformed-rate 0
```

> **Talking point:** *"--no-speed strips speed_estimate from every event — the same shape B1 emits when a camera isn't calibrated."*

### 3. Wait ~15 seconds, then check that B2 still produced non-zero avg_speed

```bash
docker compose exec postgres psql -U user -d traffic -c "
SELECT camera_id, window_start,
       round(avg_speed_kmh::numeric, 1) AS avg_speed,
       congestion_level
FROM traffic_metrics
WHERE lane_id IS NULL
ORDER BY window_start DESC LIMIT 5;"
```

> **Talking point:** *"avg_speed is non-zero — and varying — because SpeedTracker is computing it from each vehicle's centroid moving between frames. The first window for a vehicle still has 0 (no prior position to compare against), but after that we have real numbers."*

### 4. Show the retention sweeper code

```bash
sed -n '20,45p' src/processor/retention.py
```

> **Talking point:** *"Two parameterised DELETEs. Runs from the processor's poll loop on a 1-hour cadence — no extra cron container, no pg_cron extension. Cheap. The 24h / 30d windows come from env vars so SRE can tune without redeploying."*

### 5. Trigger the sweeper manually

```bash
docker compose exec b2-stream-processor python -c "
from shared.db import SessionLocal
from processor.retention import sweep
events_deleted, metrics_deleted = sweep(SessionLocal)
print(f'events_deleted={events_deleted} metrics_deleted={metrics_deleted}')"
```

> **Talking point:** *"Zero deleted because nothing in our DB is older than the retention windows yet. In production, this returns rowcounts and logs them as JSON — B4 can chart deletion rates over time to make sure the sweep is actually doing work."*

### 6. Force-delete by injecting an old row to prove it works

```bash
docker compose exec postgres psql -U user -d traffic -c "
INSERT INTO traffic_events (camera_id, ts, vehicle_id, class)
VALUES ('cam_demo', now() - interval '48 hours', 'veh_old', 'car');
SELECT count(*) FROM traffic_events WHERE camera_id = 'cam_demo';"

docker compose exec b2-stream-processor python -c "
from shared.db import SessionLocal
from processor.retention import sweep
print(sweep(SessionLocal))"

docker compose exec postgres psql -U user -d traffic -c "
SELECT count(*) FROM traffic_events WHERE camera_id = 'cam_demo';"
```

> **Talking point:** *"Inserted a 48-hour-old row, ran the sweep, row is gone. Same for `traffic_metrics` with the 30-day cutoff."*

---

## What I changed
- **`src/processor/speed_tracker.py`** — new `SpeedTracker` class. Per-vehicle centroid + timestamp dict, TTL eviction (default 30s) so memory stays bounded.
- **`src/processor/retention.py`** — `sweep(session_factory, events_retention_hours, metrics_retention_days)`. Parameterised cutoff so SQLite tests work too.
- **`shared/config.py`** — six new settings: `speed_tracker_ttl_seconds`, `speed_tracker_pixel_to_meter`, `raw_writer_batch_size`, `raw_writer_flush_interval_seconds`, `retention_*_hours/days`, `retention_sweep_interval_seconds`.

## Open follow-up
- **Pixel-to-meter calibration is a placeholder** (default 0.05 m/px). Absolute km/h numbers will be off until B1 hands over per-camera survey data. Tracked as issue #19. Mention this honestly during demo — *"This shows the path is wired; the absolute numbers are placeholders."*

## Q&A prep
- **Q: Why not use Kafka time + B1's frame_id for delta time?** Network delay would corrupt dt. We use B1's `timestamp` field which is NTP-synced.
- **Q: Why not a separate cleanup container?** One less moving part. The sweeper is < 50 lines and runs in the same poll loop where we already check the clock.
