# Traffic Predictor (ST-GCN)

This directory contains the ST-GCN training and inference pipeline for lane-level
traffic forecasting.

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