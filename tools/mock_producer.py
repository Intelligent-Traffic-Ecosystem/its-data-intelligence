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

VEHICLE_CLASSES = ["car", "car", "car", "car", "truck", "bus", "motorcycle", "bicycle"]
CAMERA_IDS = ["cam_{:02d}".format(i) for i in range(1, 21)]


def generate_event(
    camera_id: str,
    vehicle_counter: int,
    with_lane: bool = False,
    include_speed: bool = True,
) -> dict:
    """Generate a single realistic traffic event matching the B1 schema."""
    vehicle_class = random.choice(VEHICLE_CLASSES)

    hour = datetime.now().second % 60
    if hour < 20:
        speed = random.uniform(0, 25)
    elif hour < 40:
        speed = random.uniform(15, 55)
    else:
        speed = random.uniform(30, 65)

    event: dict = {
        "camera_id": camera_id,
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
        '{"camera_id": "cam_01"}',  # Missing required fields
        "not json at all",
        '{"camera_id": "cam_01", "class": "spaceship", "timestamp": "2026-01-01T00:00:00Z", "frame_id": 1, "vehicle_id": "v1", "confidence": 0.9, "bbox": {"x":0,"y":0,"w":1,"h":1}, "centroid": {"x":0,"y":0}}',
        "",
    ]
    return random.choice(bad_events)


def main():
    parser = argparse.ArgumentParser(description="Mock traffic event producer")
    parser.add_argument("--cameras", type=int, default=4, help="Number of cameras to simulate")
    parser.add_argument("--rate", type=float, default=10, help="Events per second (total across cameras)")
    parser.add_argument("--brokers", type=str, default="localhost:9092", help="Kafka broker(s)")
    parser.add_argument("--topic", type=str, default="traffic.events.raw", help="Kafka topic")
    parser.add_argument("--malformed-rate", type=float, default=0.02, help="Fraction of malformed events (0-1)")
    parser.add_argument("--with-lanes", action="store_true", help="Include lane_id on ~80%% of events")
    parser.add_argument("--no-speed", action="store_true", help="Omit speed_estimate so B2 must compute it")
    args = parser.parse_args()

    cameras = CAMERA_IDS[: args.cameras]
    delay = 1.0 / args.rate

    print(f"Connecting to Kafka at {args.brokers}...")
    try:
        producer = KafkaProducer(
            bootstrap_servers=args.brokers.split(","),
            value_serializer=lambda v: v.encode("utf-8") if isinstance(v, str) else json.dumps(v).encode("utf-8"),
        )
    except Exception as e:
        print(f"Failed to connect to Kafka: {e}")
        print("Make sure Kafka is running: docker compose up kafka zookeeper")
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
