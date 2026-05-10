# Traffic Predictor (ST-GCN)

This directory contains the ST-GCN training and inference pipeline for lane-level
traffic forecasting.

## Quick start: train on the PEMS04 freeway dataset

The fastest way to get a real trained model — no Docker, no Postgres, no live
Kafka feed. Total run time on an M-series Mac: **~10 min setup + ~30–60 min
training on CPU**.

### 1) Set up the training virtual environment

```bash
cd traffic-predictor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Download the PEMS04 dataset (~31 MB)

PEMS04 is the standard benchmark for ST-GCN forecasting — 307 California
freeway loop detectors, 5-minute cadence, 59 days of flow + occupancy + speed.

```bash
mkdir -p data
curl -sL -o data/PEMS04.npz \
  https://raw.githubusercontent.com/guoshnBJTU/ASTGNN/main/data/PEMS04/PEMS04.npz
curl -sL -o data/PEMS04_distance.csv \
  https://raw.githubusercontent.com/guoshnBJTU/ASTGNN/main/data/PEMS04/PEMS04.csv
```

### 3) Import a 12-sensor subset into a SQLite training DB

```bash
python scripts/import_pems04.py --truncate
# imported 203904 rows (16992 timesteps × 3 cameras × 4 lanes)
```

This selects 12 connected sensors (via BFS over the real PEMS04 adjacency)
and rewrites them as a 3-camera × 4-lane topology, populating the same
`traffic_metrics` schema the live B2 aggregator uses.

### 4) Train

```bash
python scripts/train.py --lookback-days 70
```

The trainer reads the rows back via `TrafficMetricsLoader`, builds an
`STGCN(nodes=12, features=8, hidden=64, blocks=3, T_in=12, T_out=6)` model
(~963k params), and runs the configured 25 epochs with cosine LR. The best
checkpoint is written to `checkpoints/best_model.pt`.

### 5) Smoke-test inference

```bash
python scripts/predict.py --checkpoint checkpoints/best_model.pt
```

### Reference run — results from one training pass

Run on a 2024 M-series Mac (`--device mps`), 25 epochs, ~23 minutes wall:

| Phase | Metric | Value |
|---|---|---|
| Train | final loss | 0.2620 |
| Val   | final loss | 0.2607 |
| Test  | vehicle-count MAE (normalised) | 0.038 |
| Test  | vehicle-count RMSE (normalised) | 0.060 |
| Test  | binary congestion probability accuracy | **75.2 %** |
| Test  | 3-class congestion level accuracy | **91.5 %** |

Validation loss curve (selected epochs):

```
epoch  1   val=0.2908   train=0.3542
epoch  5   val=0.2750   train=0.2801
epoch 10   val=0.2705   train=0.2727
epoch 15   val=0.2645   train=0.2672
epoch 20   val=0.2618   train=0.2637
epoch 25   val=0.2607   train=0.2620   ← best, restored before testing
```

Notes:

- `count_mape` is logged but is not reported above. Many normalised
  vehicle-count targets are close to zero (free-flow lanes), which blows
  up percentage error; MAE/RMSE on the normalised counts are the meaningful
  numbers for this dataset.
- The committed checkpoint at `checkpoints/best_model.pt` (~3.7 MB) is the
  artifact produced by this exact run. Re-running `scripts/train.py` will
  overwrite it.

### Reusing the trained model for live serving

The B2 API currently serves an EWMA + linear-trend baseline at
`GET /api/predict/congestion`. To switch it to the trained ST-GCN:

1. Copy `checkpoints/best_model.pt` to a path the API container can read
   (e.g. mount as a volume in `docker-compose.yml`).
2. In `services/b2-data/src/api/routes/predict.py`, replace the call to
   `processor.forecaster.forecast(...)` with `TrafficPredictor.predict(...)`
   from `traffic-predictor/src/inference/predictor.py`.
3. Add `torch` and `torch-geometric` to `services/b2-data/requirements/api.txt`.

This wiring is tracked in issue #52.

## Important note about `sensors-23-00841.xml`

`sensors-23-00841.xml` is the text of a research article (JATS XML), not a
time-series training dataset.  
The training code in this project reads **numeric traffic metrics** from
PostgreSQL table `traffic_metrics` (with `lane_id IS NOT NULL`).

## Manual training (step by step)

### 1) Start PostgreSQL and data producer stack

From the repository root:

```bash
cp .env.example .env
docker compose up -d
```

### 2) Generate traffic data into Kafka (so B2 writes metrics to PostgreSQL)

From repository root, run a producer that includes lane data:

```bash
python tools/mock_producer.py --cameras 4 --rate 10 --with-lanes --brokers localhost:29094
```

Keep it running for a few minutes so enough rows are created.

### 3) Verify training data exists in PostgreSQL

```bash
docker compose exec postgres psql -U user -d traffic -c "
SELECT COUNT(*) AS lane_metric_rows
FROM traffic_metrics
WHERE lane_id IS NOT NULL;"
```

If the count is very low, keep the producer running longer.

### 4) Install predictor dependencies

Open a new terminal:

```bash
cd traffic-predictor
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 5) Configure database connection for training

The default in `config.yaml` is:

`postgresql://user:pass@localhost:5432/traffic`

In this project, Docker maps PostgreSQL to port `5434` on host.  
Use one of these options:

- Edit `config.yaml` `database.url` to `postgresql://user:pass@localhost:5434/traffic`
- OR run with env override:

```bash
export STGCN_DATABASE__URL=postgresql://user:pass@localhost:5434/traffic
```

### 6) Train the model

```bash
python scripts/train.py --lookback-days 30
```

Optional quick test run:

```bash
python scripts/train.py --lookback-days 3 --epochs 5
```

### 7) Check output artifacts

Best checkpoint is saved at:

`checkpoints/best_model.pt`

### 8) Run inference after training

Start API:

```bash
python scripts/predict.py api
```

Single prediction:

```bash
python scripts/predict.py once --camera cam-001 --lane 2
```

Stream mode:

```bash
python scripts/predict.py stream
```

## Troubleshooting

- **No data found in traffic_metrics**: keep mock producer running longer and
  confirm `lane_id` values are present.
- **DB connection refused**: ensure Docker PostgreSQL is up and port is `5434`
  for host access.
- **Dataset too small for split**: collect more history or reduce
  `features.output_horizon` temporarily in `config.yaml`.