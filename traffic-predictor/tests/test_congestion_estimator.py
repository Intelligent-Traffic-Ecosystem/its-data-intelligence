"""
Unit tests for congestion_estimator.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.config import Config
from src.inference.congestion_estimator import CongestionEstimator, CongestionWindow


def _make_estimator(start_thr=0.55, end_thr=0.30, min_dur=3, window_sec=5) -> CongestionEstimator:
    cfg = Config()
    cfg.inference.congestion.start_threshold = start_thr
    cfg.inference.congestion.end_threshold = end_thr
    cfg.inference.congestion.min_duration_steps = min_dur
    cfg.features.window_size_seconds = window_sec
    return CongestionEstimator(cfg)


_ORIGIN = datetime(2026, 5, 5, 8, 0, 0, tzinfo=timezone.utc)


class TestFindCrossing:
    def test_detects_rise(self):
        est = _make_estimator(start_thr=0.5, min_dur=2)
        series = np.array([0.1, 0.2, 0.6, 0.7, 0.8])
        idx = est._find_crossing(series, threshold=0.5, above=True, min_dur=2)
        assert idx == 2

    def test_detects_fall(self):
        est = _make_estimator(end_thr=0.3, min_dur=2)
        series = np.array([0.8, 0.7, 0.2, 0.1, 0.1])
        idx = est._find_crossing(series, threshold=0.3, above=False, min_dur=2)
        assert idx == 2

    def test_no_crossing_returns_none(self):
        est = _make_estimator(start_thr=0.9, min_dur=3)
        series = np.zeros(10)
        assert est._find_crossing(series, 0.9, above=True, min_dur=3) is None

    def test_min_duration_enforced(self):
        est = _make_estimator()
        # Only 2 consecutive steps above threshold, min_dur=3 → None
        series = np.array([0.1, 0.6, 0.6, 0.1, 0.6, 0.6, 0.6])
        idx = est._find_crossing(series, threshold=0.55, above=True, min_dur=3)
        assert idx == 4  # starts at position 4 (3 consecutive there)


class TestEstimate:
    def test_congestion_start_detected(self):
        est = _make_estimator(start_thr=0.55, min_dur=3)
        # Congestion starts at step 5
        series = np.array([0.1] * 5 + [0.7] * 10, dtype=np.float32)
        cw = est.estimate("cam-001", 1, series, _ORIGIN)
        assert cw.start_step == 5
        assert cw.congestion_start is not None
        expected_dt = _ORIGIN + timedelta(seconds=5 * 5)
        assert cw.congestion_start == expected_dt

    def test_congestion_end_detected(self):
        est = _make_estimator(start_thr=0.55, end_thr=0.30, min_dur=3)
        series = np.array([0.7] * 8 + [0.1] * 5, dtype=np.float32)
        cw = est.estimate("cam-001", 1, series, _ORIGIN)
        assert cw.end_step == 8
        assert cw.congestion_end is not None

    def test_no_start_returns_none_for_both(self):
        est = _make_estimator(start_thr=0.9, min_dur=3)
        series = np.zeros(20, dtype=np.float32)
        cw = est.estimate("cam-001", 1, series, _ORIGIN)
        assert cw.congestion_start is None
        assert cw.congestion_end is None

    def test_start_no_end_within_horizon(self):
        est = _make_estimator(start_thr=0.55, end_thr=0.30, min_dur=3)
        # Congestion starts but never clears within the window
        series = np.array([0.1] * 3 + [0.8] * 20, dtype=np.float32)
        cw = est.estimate("cam-001", 1, series, _ORIGIN)
        assert cw.congestion_start is not None
        assert cw.congestion_end is None

    def test_peak_probability(self):
        est = _make_estimator(start_thr=0.55, min_dur=3)
        series = np.array([0.6, 0.7, 0.9, 0.8, 0.6], dtype=np.float32)
        cw = est.estimate("cam-001", 1, series, _ORIGIN)
        assert cw.peak_probability == pytest.approx(0.9, abs=0.01)

    def test_returns_correct_camera_lane(self):
        est = _make_estimator()
        series = np.zeros(10, dtype=np.float32)
        cw = est.estimate("cam-999", 42, series, _ORIGIN)
        assert cw.camera_id == "cam-999"
        assert cw.lane_id == 42


class TestEstimateBatch:
    def test_batch_length_matches_nodes(self):
        est = _make_estimator()
        index_to_node = [("cam-001", 1), ("cam-001", 2), ("cam-002", 1)]
        prob = np.zeros((20, 3), dtype=np.float32)
        results = est.estimate_batch(prob, index_to_node, _ORIGIN)
        assert len(results) == 3

    def test_batch_node_ids_correct(self):
        est = _make_estimator()
        index_to_node = [("cam-A", 10), ("cam-B", 20)]
        prob = np.zeros((10, 2), dtype=np.float32)
        results = est.estimate_batch(prob, index_to_node, _ORIGIN)
        assert results[0].camera_id == "cam-A"
        assert results[0].lane_id == 10
        assert results[1].camera_id == "cam-B"
        assert results[1].lane_id == 20
