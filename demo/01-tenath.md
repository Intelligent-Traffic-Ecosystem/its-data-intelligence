# 01 — Foundations: structured JSON logging
**Owner:** @TenathDilusha (Dilusha Chandrasiri) · **PR:** #6 · **Time:** ~3 min

## What you're showing
- Every log line from b2-stream-processor and b2-api is **a single valid JSON object**, not a free-form string.
- Library logs (kafka-python, sqlalchemy, uvicorn) flow through the same JSON formatter, not their own ad-hoc formats.
- B4 / ELK can parse this directly — no regex pain.

## Why it matters
Before this, `processor/main.py` had a hand-rolled f-string format that didn't escape strings and silently failed on logs from third-party libraries. Operations couldn't grep `level=ERROR` reliably. Now every log line is parseable.

---

## Demo steps

### 1. Show the raw stream

```bash
docker compose logs --tail=10 b2-stream-processor
```

> **Talking point:** *"Look — every line is one JSON object. No half-printed exceptions, no multi-line stack traces breaking the parser."*

### 2. Pretty-print with `jq`

```bash
docker compose logs --tail=10 b2-stream-processor | jq -c .
```

> **Talking point:** *"Because they're real JSON, jq parses every line. Try the same trick on a 'classic' log file — you can't."*

### 3. Filter by level

```bash
docker compose logs b2-stream-processor | jq -c 'select(.level == "WARNING")'
```

> **Talking point:** *"This is what B4's monitoring will use to alert on errors — it's a one-liner."*

### 4. Show a library log going through the same formatter

```bash
docker compose logs b2-stream-processor | jq -c 'select(.logger | startswith("kafka."))'
```

> **Talking point:** *"This is from `kafka-python`, not our code. It still comes out as JSON because we configured the root logger, not just our own loggers."*

### 5. Show the API side

```bash
docker compose logs --tail=5 b2-api | jq -c .
```

> **Talking point:** *"Same deal in the API process — uvicorn access logs, our middleware logs, all unified."*

---

## What I changed
- Added `python-json-logger` to base requirements; removed the unused `apache-flink` from processor deps (it was pulling in JVM weight for no reason).
- New `src/shared/logging_setup.py` — idempotent JSON formatter that captures library logs too.
- Wired `configure_logging("b2-processor")` and `configure_logging("b2-api")` into both entry points.
- Added `requirements/dev.txt` and a top-level `requirements.txt` shim — this also unblocked `its-main`'s CI which was silently skipping all of b2-data because the guard `[ -f requirements.txt ]` was always false.

## Q&A prep
- **Q: Why python-json-logger and not structlog?** Lighter dependency, doesn't change the call sites — existing `logger.info("foo")` calls just work.
- **Q: What about log rotation?** Container stdout, B4 / Docker logging driver handles rotation. We don't write to disk.
