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
curl -s localhost:8000/health | jq          # expect kafka=ok postgres=ok
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
