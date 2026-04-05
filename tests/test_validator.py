import json

from processor.validator import validate_event


def _make_event(**overrides) -> str:
    base = {
        "camera_id": "cam_01",
        "timestamp": "2026-04-16T10:15:23.456Z",
        "frame_id": 100,
        "vehicle_id": "veh_001",
        "class": "car",
        "confidence": 0.93,
        "bbox": {"x": 412, "y": 178, "w": 82, "h": 46},
        "centroid": {"x": 453, "y": 201},
    }
    base.update(overrides)
    return json.dumps(base)


def test_valid_event():
    result = validate_event(_make_event())
    assert result is not None
    assert result.camera_id == "cam_01"
    assert result.vehicle_class == "car"


def test_invalid_json():
    assert validate_event("not json") is None


def test_empty_string():
    assert validate_event("") is None


def test_missing_required_field():
    assert validate_event('{"camera_id": "cam_01"}') is None


def test_invalid_vehicle_class():
    assert validate_event(_make_event(**{"class": "spaceship"})) is None


def test_confidence_out_of_range():
    assert validate_event(_make_event(confidence=1.5)) is None


def test_all_valid_classes():
    for cls in ["car", "bus", "truck", "motorcycle", "bicycle", "pedestrian"]:
        result = validate_event(_make_event(**{"class": cls}))
        assert result is not None, f"Class '{cls}' should be valid"
