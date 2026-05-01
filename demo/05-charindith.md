# 05 — Integration tests with testcontainers + processor runner extraction
**Owner:** @charindithjaindu (Jaindu Charindith) · **PR:** #10 · **Time:** ~4 min

## What you're showing
- A test suite that spins up **real Kafka + Postgres** in containers, produces a B1-shaped event, runs the processor, and asserts the row landed in Postgres with all fields populated.
- These run on every PR via GitHub Actions — so any change that breaks the B1↔B2 wire contract fails CI before merging.

## Why it matters
The unit tests (validator, metrics, congestion) ran against mocks. They could pass even if the wire format with B1 was broken — and we already caught one such issue (B1 sends fractional floats for bbox; our schema required int). Integration tests against real Kafka prove the contract end-to-end.

---

## Demo steps

### 1. Show the test suite

```bash
ls tests/integration/
cat tests/integration/conftest.py | head -45
```

> **Talking point:** *"Session-scoped Kafka and Postgres containers — testcontainers-python spins them up, sets the env vars our settings module reads, and tears them down at the end of the run. Cost is ~20s on first run, then warm."*

### 2. Show the runner extraction (so tests can drive deterministically)

```bash
sed -n '14,55p' src/processor/runner.py
```

> **Talking point:** *"The processor's poll loop is now `run_iteration(consumer, aggregator, raw_writer, tracker)` — one tick per call, returns the count of events processed. main() just calls it in a while-loop. Tests can call it once, twice, ten times, with deterministic outcomes."*

### 3. Show the end-to-end test

```bash
sed -n '17,55p' tests/integration/test_end_to_end_flow.py
```

> **Talking point:** *"Produce one B1-shaped event with KafkaProducer, call run_iteration until we see it processed, assert the traffic_events row has all fields populated, assert both a camera-wide and a per-lane traffic_metrics row exist."*

### 4. Run the suite

```bash
make test-integration
```

Expect ~60-90 seconds (Kafka container takes ~20s to be healthy, the rest is fast).

> **Talking point:** *"Four tests pass: schema compatibility (100 mock events round-trip through validator), end-to-end flow, /health with real Kafka, retention sweep. If any of these fails, CI fails the PR."*

### 5. Show the schema-compatibility test

```bash
sed -n '17,50p' tests/integration/test_kafka_schema_compat.py
```

> **Talking point:** *"This is the most paranoid test: 100 events from the mock producer get JSON-serialised, sent to a real Kafka topic, consumed by a real KafkaConsumer, and validated by `validate_event()`. If the wire format ever drifts from what B1 produces, this catches it."*

### 6. Show CI integration

```bash
sed -n '60,80p' ../../.github/workflows/ci.yaml
```

> **Talking point:** *"This step runs only when matrix.service == 'b2-data', uses --timeout=120 per test so a flaky Kafka boot doesn't deadlock the runner. Other services are unaffected."*

---

## What I changed
- **`src/processor/runner.py`** — extracted `run_iteration()` from `processor/main.py` so tests can drive single ticks deterministically.
- **`src/processor/metrics_prom.py`** — declared the processor-side counters; `main.py` exposes them on port 9100 via `prometheus_client.start_http_server`.
- **`tests/integration/`** — four tests using `testcontainers-python`:
  - `test_kafka_schema_compat` — wire-contract guard with the mock producer
  - `test_end_to_end_flow` — full producer→consumer→DB chain with all B1 fields and per-lane breakdown
  - `test_health_with_real_kafka` — `/health` returns `kafka=ok` against a live broker
  - `test_retention` — sweeper actually deletes rows past the cutoff
- **`Makefile`** — `test-integration`, `test-all` targets.
- **`requirements/dev.txt`** — pulls in `testcontainers[kafka,postgres]` and `pytest-timeout`.
- **`.github/workflows/ci.yaml` (its-main)** — added the integration step gated on `matrix.service == 'b2-data'`.

## Q&A prep
- **Q: Slow CI?** ~90s for the integration suite, runs in parallel with B1/B3 matrix entries. Still well under the 5-min target.
- **Q: Why testcontainers and not a separate docker-compose?** testcontainers manages lifecycle automatically — no leaked containers between test runs, no port conflicts.
- **Q: Can we run integration tests locally?** Yes — `make test-integration` from `services/b2-data/`. Needs Docker Desktop running.
