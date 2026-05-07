# 02 — Schema fidelity + per-lane metrics
**Owner:** @gimsara94 (Gimsara Bulagala) · **PR:** #7 · **Time:** ~5 min

## What you're showing
- Every field B1 publishes (`frame_id`, `confidence`, `bbox_x/y/w/h`, `lane_id`) now lands in the database.
- A single `traffic_metrics` table holds **camera-wide** rows and **per-lane** rows for the same window — no schema duplication.

## Why it matters
B2 SRS §3.2 + §7 require persisting raw events for 24h and supporting per-lane metrics. The original writer dropped `bbox`, `frame_id`, `confidence` and `lane_id` on the floor. We were lossy. We're not lossy any more.

---

## Demo steps

### 1. Show the schema (proves migrations 002 + 003 applied)

```bash
docker compose exec postgres psql -U user -d traffic -c '\d traffic_events'
```

> **Talking point:** *"Five new columns: `frame_id`, `confidence`, `bbox_x/y/w/h`, `lane_id`. Plus a partial index on `(camera_id, lane_id, ts DESC) WHERE lane_id IS NOT NULL` so per-lane queries are fast without bloating the camera-wide path."*

```bash
docker compose exec postgres psql -U user -d traffic -c '\d traffic_metrics'
```

> **Talking point:** *"`lane_id` here too. Notice the unique constraint is now `(camera_id, lane_id, window_start)` — so one window can have one camera-wide row AND one row per lane, all coexisting."*

### 2. Pump events with lane info

In a fresh terminal (with venv active):
```bash
python tools/mock_producer.py --cameras 4 --rate 10 --brokers localhost:29092 --with-lanes --malformed-rate 0
```

Leave it running. Wait ~15 seconds.

### 3. Show all B1 fields persisted in raw events

```bash
docker compose exec postgres psql -U user -d traffic -c "
SELECT camera_id, vehicle_id, class, frame_id,
       round(confidence::numeric, 2) AS conf,
       bbox_x, bbox_y, bbox_w, bbox_h, lane_id
FROM traffic_events ORDER BY ts DESC LIMIT 5;"
```

> **Talking point:** *"Before our fix, `frame_id`, `confidence`, the bbox coordinates and `lane_id` were all NULL or missing. Now every column is populated. If B1 ever needs us to replay events for forensics, we have the full data."*

### 4. Per-lane breakdown

```bash
docker compose exec postgres psql -U user -d traffic -c "
SELECT camera_id, lane_id, window_start, vehicle_count, congestion_level
FROM traffic_metrics
WHERE lane_id IS NOT NULL
ORDER BY window_start DESC, camera_id, lane_id LIMIT 12;"
```

> **Talking point:** *"For the same 5-second window, one camera produces a `lane_id IS NULL` aggregate AND one row per active lane. B3 dashboards can show overall congestion or drill into a specific lane — same query, different `WHERE` clause."*

### 5. Camera-wide vs per-lane side by side

```bash
docker compose exec postgres psql -U user -d traffic -c "
SELECT camera_id, COALESCE(lane_id::text, 'ALL') AS lane,
       window_start, vehicle_count, congestion_level
FROM traffic_metrics
WHERE camera_id = 'cam_01'
ORDER BY window_start DESC, lane LIMIT 10;"
```

> **Talking point:** *"Look how the row with `lane=ALL` has `vehicle_count` equal to the sum of the per-lane rows for the same window. Same data, two views, one table."*

---

## What I changed
- **Migration 002** — added `frame_id`, `confidence`, `bbox_x/y/w/h`, `lane_id` columns to `traffic_events` (nullable, so it's a non-breaking change) plus the partial index.
- **Migration 003** — added nullable `lane_id` to `traffic_metrics`. Replaced the `(camera_id, window_start)` unique constraint with a `COALESCE(lane_id, -1)`-based unique expression index on Postgres so NULL doesn't break `ON CONFLICT` semantics.
- **Writer** — new `BatchedRawWriter` (buffers 200 / flushes every 1s) bulk-inserts validated events. Drop-on-failure semantics so a DB blip doesn't crash the consumer.
- **Aggregator** — added `_lane_windows` dict alongside the camera-wide `_windows`. On flush, both produce metric rows.

## Q&A prep
- **Q: Why one table instead of `traffic_metrics_by_lane`?** One query path, one writer, one Pydantic model. Cheap to filter `WHERE lane_id IS NULL` vs joining two tables.
- **Q: bbox as four columns vs JSONB?** Cheaper inserts, never queried by shape.
- **Q: What if B1 sends partial bbox?** Pydantic validation rejects, validator drops the event, processor logs a `WARNING` but keeps consuming.
