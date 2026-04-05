from processor.congestion import classify_congestion, normalize


def test_normalize_basic():
    assert normalize(0, 100) == 0.0
    assert normalize(50, 100) == 0.5
    assert normalize(100, 100) == 1.0


def test_normalize_clamps():
    assert normalize(150, 100) == 1.0
    assert normalize(-10, 100) == 0.0


def test_normalize_zero_max():
    assert normalize(50, 0) == 0.0


def test_low_congestion():
    # Few vehicles, high speed, no stopped
    level, score = classify_congestion(vehicle_count=5, avg_speed_kmh=55, stopped_ratio=0.0)
    assert level == "LOW"
    assert score < 0.30


def test_high_congestion():
    # Many vehicles, low speed, many stopped
    level, score = classify_congestion(vehicle_count=45, avg_speed_kmh=5, stopped_ratio=0.8)
    assert level in ("HIGH", "SEVERE")
    assert score > 0.55


def test_moderate_congestion():
    level, score = classify_congestion(vehicle_count=20, avg_speed_kmh=30, stopped_ratio=0.2)
    assert level in ("LOW", "MODERATE")
