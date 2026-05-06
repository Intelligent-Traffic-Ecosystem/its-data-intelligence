# B2 → B3 API Contract

API contract for the endpoints B3 (dashboard) consumes from B2 (data &
intelligence). All endpoints are JSON unless noted. Base URL is the B2 API host
(typically `http://b2-api:8000` inside the cluster, or whatever B3 is configured
with).

OpenAPI schema is also served live at `GET /docs` (Swagger UI) and `GET /openapi.json`.

---

## Auth

Admin-only endpoints (Section 2 + alert acknowledge + camera CRUD) require:

| Header | Required | Notes |
|---|---|---|
| `X-Admin-Token` | yes | The admin API token. Provisioned out-of-band. Alternative form: `Authorization: Bearer <token>`. |
| `X-Admin-User` | recommended | The administrator's user ID — recorded in the audit log. Defaults to `"admin"` if omitted. |

Public endpoints (history queries, dashboard, map, analytics reads) currently
have no auth — this is temporary. Coordinate with B4 on whether to add auth here
before shipping to production.

Errors:
- `401 Unauthorized` — bad / missing token
- `400 Bad Request` — invalid query params (e.g. unknown severity)
- `404 Not Found` — entity (alert, zone, camera) does not exist
- `409 Conflict` — duplicate (camera_id), or alert already acknowledged
- `500 Internal Server Error` — admin token not configured server-side

---

## Severity & vocabulary

We unified the vocabulary as follows. Please flag if B3 uses different terms.

- **`congestion_level`** (per-window metric) ∈ `LOW | MODERATE | HIGH | SEVERE`
- **`severity`** (per-alert) ∈ `WARNING | CRITICAL | EMERGENCY`

Mapping rule used by B2's auto-generator:
| Sustained congestion_level | Generated alert severity |
|---|---|
| `HIGH` | `WARNING` |
| `SEVERE` | `CRITICAL` |
| (any) | `EMERGENCY` reserved — manual or future B1 incident events |

Other taxonomies:
- **`alert_type`** ∈ `congestion | stopped_traffic | incident | manual`
- **`road_segment`** is a free-text label stored per camera. Filter alerts /
  analytics by it. Empty/null is allowed.

---

## 2. Administrator Controls

> All require admin auth. All write actions append a row to `audit_logs`.

### `GET /api/admin/thresholds`

Returns the current congestion thresholds.

**Response 200**
```json
{
  "congestion_threshold_low": 0.30,
  "congestion_threshold_moderate": 0.55,
  "congestion_threshold_high": 0.80
}
```

### `PUT /api/admin/thresholds`

Updates all three thresholds. Must be strictly increasing
(`low < moderate < high`).

**Request body** — same shape as the response. **Response** — the saved row.

### `GET /api/admin/zones`

List all monitoring zones.

**Response 200**
```json
[
  {
    "id": 1,
    "name": "City Centre",
    "description": "Downtown core",
    "coordinates": [
      {"lat": 6.9271, "lon": 79.8612},
      {"lat": 6.9300, "lon": 79.8700},
      {"lat": 6.9250, "lon": 79.8650},
      {"lat": 6.9271, "lon": 79.8612}
    ],
    "created_at": "2026-05-06T12:00:00+00:00",
    "updated_at": "2026-05-06T12:00:00+00:00"
  }
]
```

### `POST /api/admin/zones`

Create a zone. Server auto-closes the polygon if the last point ≠ the first.

**Request body**
```json
{
  "name": "City Centre",
  "description": "optional, max 1000 chars",
  "coordinates": [
    {"lat": 6.9271, "lon": 79.8612},
    {"lat": 6.9300, "lon": 79.8700},
    {"lat": 6.9250, "lon": 79.8650}
  ]
}
```
- `coordinates`: at least 3 points; each `lat ∈ [-90, 90]`, `lon ∈ [-180, 180]`.

**Response 201** — same shape as `GET /api/admin/zones` items.

### `PUT /api/admin/zones/{zone_id}`

Replace name/description/coordinates of an existing zone. Same body as POST.
**Response 200** — updated zone.

### `DELETE /api/admin/zones/{zone_id}`

**Response 204** — no body.

### Bonus: Camera registry (`/api/admin/cameras`)

Needed to populate latitude/longitude (for map endpoints) and `road_segment`
(for filtering). Same auth.

#### `GET /api/admin/cameras`

```json
[
  {
    "id": 1,
    "camera_id": "cam-galle-01",
    "name": "Galle Rd / Liberty",
    "latitude": 6.9145,
    "longitude": 79.8624,
    "road_segment": "Galle Rd",
    "description": null,
    "created_at": "2026-05-06T12:00:00+00:00",
    "updated_at": "2026-05-06T12:00:00+00:00"
  }
]
```

#### `POST /api/admin/cameras`

```json
{
  "camera_id": "cam-galle-01",
  "name": "Galle Rd / Liberty",
  "latitude": 6.9145,
  "longitude": 79.8624,
  "road_segment": "Galle Rd",
  "description": null
}
```
Returns **201** with the saved row. **409** if `camera_id` already exists.

#### `PUT /api/admin/cameras/{camera_id}`

Patch-style — only fields included in the body are updated.
```json
{ "latitude": 6.92, "longitude": 79.87 }
```

#### `DELETE /api/admin/cameras/{camera_id}` → **204**

---

## 3. Real-Time Alerting

### `POST /api/alerts/{alert_id}/acknowledge`

Admin-authed. Acknowledges a `CRITICAL` or `EMERGENCY` alert. `WARNING` alerts
do not require acknowledgement (returns 400). Already-acknowledged alerts
return 409.

**Response 200**
```json
{
  "id": 42,
  "acknowledged_by": "alice@its.gov",
  "acknowledged_at": "2026-05-06T13:14:15+00:00"
}
```

The administrator's user ID is taken from the `X-Admin-User` header and also
written to the `audit_logs` table.

### `GET /api/alerts/history`

Public read. List historical alerts, newest first.

**Query parameters** (all optional)
| Param | Type | Notes |
|---|---|---|
| `severity` | `WARNING\|CRITICAL\|EMERGENCY` | exact match |
| `road_segment` | string | exact match |
| `alert_type` | `congestion\|stopped_traffic\|incident\|manual` | exact match |
| `camera_id` | string | exact match |
| `from` | ISO 8601 datetime | `triggered_at >= from` |
| `to` | ISO 8601 datetime | `triggered_at <= to` |
| `limit` | int 1–5000 | default 500 |

**Response 200** — array of `AlertOut`:
```json
[
  {
    "id": 42,
    "severity": "CRITICAL",
    "alert_type": "congestion",
    "camera_id": "cam-galle-01",
    "road_segment": "Galle Rd",
    "title": "CRITICAL: congestion SEVERE on cam-galle-01",
    "message": "Camera cam-galle-01 congestion=SEVERE score=0.87",
    "congestion_level": "SEVERE",
    "congestion_score": 0.87,
    "triggered_at": "2026-05-06T13:10:00+00:00",
    "resolved_at": null,
    "acknowledged_by": "alice@its.gov",
    "acknowledged_at": "2026-05-06T13:14:15+00:00"
  }
]
```

### `GET /api/alerts/export`

Same query params as `/history` (plus `limit` up to 500_000). Returns a
streaming CSV with `Content-Disposition: attachment; filename="alerts_<ts>.csv"`.

CSV columns (in order):
```
id, severity, alert_type, camera_id, road_segment, title, message,
congestion_level, congestion_score,
triggered_at, resolved_at, acknowledged_by, acknowledged_at
```

---

## 4. Historical Analytics

All public reads. Aggregations operate on **camera-wide rows** (`lane_id IS NULL`)
from `traffic_metrics`.

### `GET /api/analytics/metrics?from=…&to=…`

`from` < `to`, both ISO 8601.

**Response 200** — `AnalyticsMetrics`:
```json
{
  "range_start": "2026-05-01T00:00:00+00:00",
  "range_end": "2026-05-06T00:00:00+00:00",
  "avg_congestion_score": 0.42,
  "peak_hour_distribution": [
    {"hour": 0, "avg_vehicle_count": 3.1, "avg_congestion_score": 0.18},
    {"hour": 1, "avg_vehicle_count": 2.0, "avg_congestion_score": 0.10},
    "..."
  ],
  "top_segments": [
    {
      "camera_id": "cam-galle-01",
      "road_segment": "Galle Rd",
      "avg_congestion_score": 0.74,
      "severe_minutes": 142.5
    }
  ],
  "incident_pie": [
    {"severity": "WARNING", "count": 120},
    {"severity": "CRITICAL", "count": 18},
    {"severity": "EMERGENCY", "count": 0}
  ]
}
```

Notes:
- `peak_hour_distribution` is bucketed by **hour-of-day in UTC** (0–23). One row per hour with data.
- `top_segments` is the top 10 cameras ranked by `avg_congestion_score`,
  tie-broken by `severe_minutes`. `road_segment` is best-effort — null if the
  camera has no segment configured.
- `incident_pie` counts alerts by severity in the range.

### `GET /api/analytics/compare?aFrom=…&aTo=…&bFrom=…&bTo=…`

Two `AnalyticsMetrics` blocks — one per range — for side-by-side comparison.

**Response 200**
```json
{
  "range_a": { "...": "AnalyticsMetrics" },
  "range_b": { "...": "AnalyticsMetrics" }
}
```

### `GET /api/analytics/report/pdf?from=…&to=…`

Returns `application/pdf` with `Content-Disposition: attachment; filename="its_analytics_<from>_<to>.pdf"`.
The PDF contains the same sections as `/metrics` rendered as tables (no
charts — happy to add chart images if B3 wants them).

---

## 5. Dashboard & Map

All public reads.

### `GET /api/dashboard/summary`

**Response 200** — `DashboardSummary`:
```json
{
  "total_incidents_24h": 18,
  "avg_speed_kmh": 34.2,
  "overall_congestion_level": "MODERATE",
  "overall_congestion_score": 0.41,
  "active_alerts": 3,
  "last_updated": "2026-05-06T13:15:00+00:00"
}
```

Definitions:
- `total_incidents_24h` — count of alerts triggered in the last 24h.
- `avg_speed_kmh` — average over camera-wide rows from the last 5 minutes.
- `overall_congestion_level` / `_score` — average congestion_score over the last
  5 minutes, mapped back to the level enum using the configured thresholds.
- `active_alerts` — alerts with `resolved_at IS NULL`.

### `GET /api/dashboard/events?limit=10`

Most recent traffic events, newest first. `limit` 1–100, default 10.

**Response 200**
```json
[
  {
    "camera_id": "cam-galle-01",
    "timestamp": "2026-05-06T13:14:58+00:00",
    "vehicle_class": "car",
    "speed_kmh": 38.4,
    "lane_id": 2
  }
]
```

### `GET /api/map/heatmap`

Vehicle-density points for the Mapbox heatmap layer. Drawn from the latest
camera-wide metric per camera within the last 5 minutes. Cameras without
`latitude`/`longitude` configured are omitted.

**Response 200**
```json
[
  {
    "camera_id": "cam-galle-01",
    "latitude": 6.9145,
    "longitude": 79.8624,
    "weight": 0.62,
    "vehicle_count": 31
  }
]
```
- `weight ∈ [0, 1]` — `vehicle_count / max_vehicle_count` clamped. Suitable as a
  Mapbox `heatmap-weight` property.

### `GET /api/map/incidents`

Active (unresolved) alerts as map markers. Includes lat/lng from the camera
registry; falls back to `null` if the camera lacks coordinates.

**Response 200**
```json
[
  {
    "alert_id": 42,
    "camera_id": "cam-galle-01",
    "latitude": 6.9145,
    "longitude": 79.8624,
    "severity": "CRITICAL",
    "alert_type": "congestion",
    "title": "CRITICAL: congestion SEVERE on cam-galle-01",
    "triggered_at": "2026-05-06T13:10:00+00:00"
  }
]
```

---

## Data shapes (reference)

### `AlertOut`
```ts
{
  id: number;
  severity: "WARNING" | "CRITICAL" | "EMERGENCY";
  alert_type: "congestion" | "stopped_traffic" | "incident" | "manual";
  camera_id: string | null;
  road_segment: string | null;
  title: string;
  message: string | null;
  congestion_level: "LOW" | "MODERATE" | "HIGH" | "SEVERE" | null;
  congestion_score: number | null;
  triggered_at: string;        // ISO 8601
  resolved_at: string | null;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
}
```

### `CameraOut`
```ts
{
  id: number;
  camera_id: string;
  name: string | null;
  latitude: number | null;
  longitude: number | null;
  road_segment: string | null;
  description: string | null;
  created_at: string;
  updated_at: string;
}
```

### `DashboardSummary`
```ts
{
  total_incidents_24h: number;
  avg_speed_kmh: number;
  overall_congestion_level: "LOW" | "MODERATE" | "HIGH" | "SEVERE";
  overall_congestion_score: number;
  active_alerts: number;
  last_updated: string;
}
```

### `HeatmapPoint`
```ts
{ camera_id: string; latitude: number; longitude: number; weight: number; vehicle_count: number; }
```

### `IncidentMarker`
```ts
{
  alert_id: number;
  camera_id: string | null;
  latitude: number | null;
  longitude: number | null;
  severity: "WARNING" | "CRITICAL" | "EMERGENCY";
  alert_type: "congestion" | "stopped_traffic" | "incident" | "manual";
  title: string;
  triggered_at: string;
}
```

### `AnalyticsMetrics`
```ts
{
  range_start: string;
  range_end: string;
  avg_congestion_score: number;
  peak_hour_distribution: Array<{ hour: number; avg_vehicle_count: number; avg_congestion_score: number; }>;
  top_segments: Array<{ camera_id: string; road_segment: string | null; avg_congestion_score: number; severe_minutes: number; }>;
  incident_pie: Array<{ severity: string; count: number; }>;
}
```

---

## Open questions for B3 (please confirm)

1. Are `WARNING / CRITICAL / EMERGENCY` the right vocabulary? We use those for
   the alert table. We use `LOW / MODERATE / HIGH / SEVERE` for the per-window
   congestion enum.
2. `road_segment` is a free-text string per camera. Acceptable? Or should we
   add a separate `road_segments` table with structured ids?
3. Map endpoints depend on `cameras.latitude/longitude`. Who populates the
   camera registry — admin operator via `/api/admin/cameras`, or do we expect
   some external source-of-truth?
4. Should `/api/alerts/history`, `/dashboard/*`, `/map/*`, `/analytics/*` be
   admin-authed? Right now they're public reads. (Auth-gating is one-line per
   route once decided.)
5. PDF report — server-side ReportLab is in. Want chart images embedded in the
   PDF, or are tables enough for v1?
6. WebSocket: B2 already broadcasts metric updates on the existing WS endpoint.
   Should we also push alert events (created / acknowledged / resolved) over
   the same channel? Easy to add — just confirm the message envelope you want.
