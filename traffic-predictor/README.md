```bash
cd traffic-predictor
pip install -r requirements.txt

# Train (needs traffic_metrics rows with lane_id set)
python scripts/train.py --lookback-days 30

# Start REST API (port 8001)
python scripts/predict.py api

# Start Kafka-driven stream mode
python scripts/predict.py stream

# Single prediction to stdout
python scripts/predict.py once --camera cam-001 --lane 2
```