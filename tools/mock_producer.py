"""Mock event producer — generates realistic traffic events to Kafka.

Use this for developing B2 without B1. Simulates multiple cameras with
varying traffic patterns.

Usage:
    python tools/mock_producer.py --cameras 4 --rate 10
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone

from kafka import KafkaProducer

VEHICLE_CLASSES = ["car", "truck", "bus", "motorcycle", "bicycle"]
CAMERA_LOCATIONS = {
    "cam1": (6.931777, 79.846805),
    "cam2": (6.927517, 79.849893),
    "cam3": (6.934078, 79.866102),
    "cam4": (6.912149, 79.855979),
    "cam5": (6.911370, 79.877183),
    "cam6": (6.931370, 79.878204),
    "cam7": (6.897231, 79.860079),
    "cam8": (6.896660, 79.877218),
}
# Typical town-area camera-specific base speeds (km/h).
# Vehicles in a camera zone move at similar speeds with small jitter.
CAMERA_BASE_SPEED_KMPH = {
    "cam1": 42.0,
    "cam2": 38.0,
    "cam3": 50.0,
    "cam4": 34.0,
    "cam5": 46.0,
    "cam6": 55.0,
    "cam7": 32.0,
    "cam8": 40.0,
}
KAFKA_CONNECT_RETRIES = 10
KAFKA_CONNECT_DELAY_SECONDS = 2


def generate_event(
    camera_id: str,
    vehicle_counter: int,
    with_lane: bool = False,
    include_speed: bool = True,
) -> dict:
    """Generate a single realistic traffic event matching the B1 schema."""
    vehicle_class = random.choice(VEHICLE_CLASSES)
    lat, lng = CAMERA_LOCATIONS[camera_id]
    base_speed = CAMERA_BASE_SPEED_KMPH[camera_id]
    speed = min(max(random.gauss(base_speed, 2.5), 30.0), 70.0)

    event: dict = {
        "camera_id": camera_id,
        "latitude": lat,
        "longitude": lng,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "frame_id": vehicle_counter,
        "vehicle_id": f"veh_{vehicle_counter:06d}",
        "class": vehicle_class,
        "confidence": round(random.uniform(0.70, 0.99), 2),
        "bbox": {
            "x": random.randint(0, 1920),
            "y": random.randint(0, 1080),
            "w": random.randint(40, 120),
            "h": random.randint(30, 80),
        },
        "centroid": {
            "x": random.randint(100, 1820),
            "y": random.randint(100, 980),
        },
    }
    if include_speed:
        event["speed_estimate"] = round(speed, 1)
    if with_lane and random.random() < 0.8:
        event["lane_id"] = random.choice([1, 2, 3])
    return event


def generate_malformed_event() -> str:
    """Occasionally generate a malformed event to test validator resilience."""
    bad_events = [
        '{"camera_id":"cam1","timestamp":"2026-01-01T00:00:00Z","frame_id":1}',  # Missing fields
        '{"camera_id":"cam2","timestamp":"bad-ts","frame_id":"NaN","vehicle_id":"v1","class":"car","confidence":0.9,"bbox":{"x":0,"y":0,"w":1,"h":1},"centroid":{"x":0,"y":0}}',
        '{"camera_id":"cam3","timestamp":"2026-01-01T00:00:00Z","frame_id":2,"vehicle_id":"v2","class":"car","confidence":1.4,"bbox":{"x":0,"y":0,"w":1,"h":1},"centroid":{"x":0,"y":0}}',
    ]
    return random.choice(bad_events)


def main():
    parser = argparse.ArgumentParser(description="Mock traffic event producer")
    parser.add_argument("--cameras", type=int, default=8, help="Number of cameras to simulate (max 8)")
    parser.add_argument("--rate", type=float, default=10, help="Events per second (total across cameras)")
    parser.add_argument("--brokers", type=str, default="localhost:29094", help="Kafka broker(s)")
    parser.add_argument("--topic", type=str, default="traffic.events.raw", help="Kafka topic")
    parser.add_argument("--malformed-rate", type=float, default=0.0, help="Fraction of malformed events (0-1)")
    parser.add_argument("--with-lanes", action="store_true", help="Include lane_id on ~80%% of events")
    parser.add_argument("--no-speed", action="store_true", help="Omit speed_estimate so B2 must compute it")
    args = parser.parse_args()

    if args.cameras < 1 or args.cameras > len(CAMERA_LOCATIONS):
        print(f"--cameras must be between 1 and {len(CAMERA_LOCATIONS)}")
        sys.exit(2)
    cameras = list(CAMERA_LOCATIONS.keys())[: args.cameras]
    delay = 1.0 / args.rate

    print(f"Connecting to Kafka at {args.brokers}...")
    producer = None
    last_error = None
    for attempt in range(1, KAFKA_CONNECT_RETRIES + 1):
        try:
            producer = KafkaProducer(
                bootstrap_servers=args.brokers.split(","),
                value_serializer=lambda v: v.encode("utf-8") if isinstance(v, str) else json.dumps(v).encode("utf-8"),
            )
            break
        except Exception as e:
            last_error = e
            if attempt < KAFKA_CONNECT_RETRIES:
                print(
                    f"Kafka not ready yet ({attempt}/{KAFKA_CONNECT_RETRIES}): {e}"
                )
                time.sleep(KAFKA_CONNECT_DELAY_SECONDS)
            else:
                print(f"Failed to connect to Kafka: {e}")
                print("Make sure Kafka is running: docker compose up -d")
                sys.exit(1)

    print(f"Producing events to topic '{args.topic}' from {len(cameras)} cameras at {args.rate} events/sec")
    print("Press Ctrl+C to stop\n")

    counter = 0
    try:
        while True:
            camera = random.choice(cameras)

            if random.random() < args.malformed_rate:
                # Send a malformed event
                value = generate_malformed_event()
                producer.send(args.topic, value=value, key=camera.encode("utf-8"))
                print(f"  [MALFORMED] sent to {camera}")
            else:
                event = generate_event(
                    camera,
                    counter,
                    with_lane=args.with_lanes,
                    include_speed=not args.no_speed,
                )
                producer.send(args.topic, value=event, key=camera.encode("utf-8"))
                counter += 1
                if counter % 50 == 0:
                    print(
                        f"  Sent {counter} events | last: {camera} "
                        f"class={event['class']} speed={event.get('speed_estimate', '?')} "
                        f"lane={event.get('lane_id', '-')}"
                    )

            time.sleep(delay)

    except KeyboardInterrupt:
        print(f"\nStopped. Sent {counter} events total.")
    finally:
        producer.flush()
        producer.close()


if __name__ == "__main__":
    main()
