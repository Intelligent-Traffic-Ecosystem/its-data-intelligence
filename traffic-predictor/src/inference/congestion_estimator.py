"""
Congestion start/end time estimator.

Given a per-lane congestion-probability time series (produced by the ST-GCN
model), this module detects:

* **Congestion start** — the first future time step where the rolling
  probability **rises above** ``start_threshold`` for at least
  ``min_duration_steps`` consecutive steps.
* **Congestion end** — the first future time step (after congestion has
  started) where the rolling probability **drops below** ``end_threshold``
  for at least ``min_duration_steps`` consecutive steps.

If no start or end is detected within the forecast horizon the corresponding
field is ``None``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from src.config import Config, get_config

logger = logging.getLogger(__name__)


@dataclass
class CongestionWindow:
    """Estimated congestion episode for one (camera, lane) over the horizon."""

    camera_id: str
    lane_id: int
    congestion_start: datetime | None   # UTC datetime of predicted start
    congestion_end: datetime | None     # UTC datetime of predicted end
    # Indices into the forecast horizon array (0-based)
    start_step: int | None
    end_step: int | None
    # Highest congestion probability seen in the detected window
    peak_probability: float


class CongestionEstimator:
    """
    Detects congestion start and end times from a probability time series.

    Parameters
    ----------
    config:
        Application config. Defaults to global singleton.
    """

    def __init__(self, config: Config | None = None) -> None:
        self._cfg = (config or get_config()).inference.congestion
        self._window_seconds = (config or get_config()).features.window_size_seconds

    def estimate(
        self,
        camera_id: str,
        lane_id: int,
        prob_series: np.ndarray,
        forecast_origin: datetime,
    ) -> CongestionWindow:
        """
        Estimate congestion start/end from a 1-D probability array.

        Parameters
        ----------
        camera_id / lane_id:
            Identifiers for this lane (used in the returned dataclass).
        prob_series:
            Numpy array of shape ``[T_out]`` with values in ``[0, 1]``.
        forecast_origin:
            The timestamp of the **first** predicted step (i.e., now + 1 window).

        Returns
        -------
        CongestionWindow
        """
        T = len(prob_series)
        min_dur = self._cfg.min_duration_steps
        start_thr = self._cfg.start_threshold
        end_thr = self._cfg.end_threshold
        step_sec = self._window_seconds

        start_step = self._find_crossing(
            prob_series, threshold=start_thr, above=True, min_dur=min_dur
        )
        end_step: int | None = None
        if start_step is not None:
            tail = prob_series[start_step:]
            rel = self._find_crossing(tail, threshold=end_thr, above=False, min_dur=min_dur)
            if rel is not None:
                end_step = start_step + rel

        def _step_to_dt(step: int | None) -> datetime | None:
            if step is None:
                return None
            return forecast_origin + timedelta(seconds=step * step_sec)

        if start_step is not None:
            window_slice = prob_series[start_step: end_step]
            peak = float(window_slice.max()) if len(window_slice) > 0 else float(prob_series[start_step])
        else:
            peak = float(prob_series.max())

        return CongestionWindow(
            camera_id=camera_id,
            lane_id=lane_id,
            congestion_start=_step_to_dt(start_step),
            congestion_end=_step_to_dt(end_step),
            start_step=start_step,
            end_step=end_step,
            peak_probability=peak,
        )

    def estimate_batch(
        self,
        prob_tensor: np.ndarray,
        index_to_node: list[tuple[str, int]],
        forecast_origin: datetime,
    ) -> list[CongestionWindow]:
        """
        Estimate for all nodes in the graph in one call.

        Parameters
        ----------
        prob_tensor:
            ``[T_out, N]`` array of congestion probabilities.
        index_to_node:
            Ordered list of ``(camera_id, lane_id)`` tuples from the graph.
        forecast_origin:
            Timestamp of the first predicted step.

        Returns
        -------
        list[CongestionWindow]
            One entry per graph node.
        """
        T_out, N = prob_tensor.shape
        assert N == len(index_to_node), "prob_tensor.shape[1] must equal len(index_to_node)"

        results: list[CongestionWindow] = []
        for n_idx, (cam, lane) in enumerate(index_to_node):
            cw = self.estimate(cam, lane, prob_tensor[:, n_idx], forecast_origin)
            results.append(cw)
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_crossing(
        series: np.ndarray,
        threshold: float,
        above: bool,
        min_dur: int,
    ) -> int | None:
        """
        Return the first index where *series* stays on the target side of
        *threshold* for at least *min_dur* consecutive steps.

        Parameters
        ----------
        above:
            If True, detect where series > threshold; otherwise < threshold.
        """
        T = len(series)
        consecutive = 0
        start: int | None = None

        for i in range(T):
            v = series[i]
            cond = (v >= threshold) if above else (v <= threshold)
            if cond:
                if consecutive == 0:
                    start = i
                consecutive += 1
                if consecutive >= min_dur:
                    return start  # type: ignore[return-value]
            else:
                consecutive = 0
                start = None

        return None
