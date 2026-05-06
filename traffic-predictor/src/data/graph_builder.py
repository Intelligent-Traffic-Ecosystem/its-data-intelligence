"""
Graph builder — converts the lane/camera topology config into the PyTorch
Geometric ``edge_index`` (COO format) and a stable node-index mapping.

Graph definition
----------------
* **Nodes**: each unique ``(camera_id, lane_id)`` pair is a node.
* **Edges**:
    1. *Intra-camera*: all adjacent lane pairs on the same camera
       (lane k ↔ lane k+1, bidirectional).
    2. *Inter-camera*: explicit cross-camera connections from ``config.yaml``.
* Self-loops are added so every node aggregates its own features.

The ``node_index`` dict maps ``(camera_id, lane_id) → int`` and is used
everywhere that raw data must be aligned to the graph.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import torch
from torch import Tensor

from src.config import Config, TopologyConfig, get_config

logger = logging.getLogger(__name__)


@dataclass
class LaneGraph:
    """Immutable graph descriptor produced by :func:`build_graph`."""

    num_nodes: int
    edge_index: Tensor          # shape [2, num_edges], dtype=torch.long
    node_index: dict[tuple[str, int], int]  # (camera_id, lane_id) → node idx
    index_to_node: list[tuple[str, int]]    # node idx → (camera_id, lane_id)


def build_graph(config: Config | None = None) -> LaneGraph:
    """
    Build the lane-level spatio-temporal graph from the topology config.

    Returns
    -------
    LaneGraph
        Contains the PyG ``edge_index`` tensor and the node mapping dicts.
    """
    cfg = config or get_config()
    topology: TopologyConfig = cfg.topology

    # ---------------------------------------------------------------
    # 1. Assign stable node indices
    # ---------------------------------------------------------------
    node_index: dict[tuple[str, int], int] = {}
    index_to_node: list[tuple[str, int]] = []

    for cam in topology.cameras:
        for lane_id in cam.lane_ids:
            key = (cam.camera_id, lane_id)
            if key not in node_index:
                node_index[key] = len(index_to_node)
                index_to_node.append(key)

    num_nodes = len(index_to_node)
    logger.info("Graph: %d nodes (lanes)", num_nodes)

    # ---------------------------------------------------------------
    # 2. Collect edges
    # ---------------------------------------------------------------
    edges: list[tuple[int, int]] = []

    # Self-loops
    for i in range(num_nodes):
        edges.append((i, i))

    # Intra-camera: adjacent lane pairs (bidirectional)
    for cam in topology.cameras:
        lane_ids = cam.lane_ids
        for a, b in zip(lane_ids[:-1], lane_ids[1:]):
            i = node_index[(cam.camera_id, a)]
            j = node_index[(cam.camera_id, b)]
            edges.append((i, j))
            edges.append((j, i))

    # Inter-camera: explicit cross-camera edges (bidirectional)
    for edge in topology.cross_camera_edges:
        src_key = (edge.from_camera, edge.from_lane)
        dst_key = (edge.to_camera, edge.to_lane)
        if src_key not in node_index:
            logger.warning("Cross-camera edge source %s not in graph, skipping", src_key)
            continue
        if dst_key not in node_index:
            logger.warning("Cross-camera edge target %s not in graph, skipping", dst_key)
            continue
        i = node_index[src_key]
        j = node_index[dst_key]
        edges.append((i, j))
        edges.append((j, i))

    # Deduplicate while preserving order
    seen: set[tuple[int, int]] = set()
    unique_edges: list[tuple[int, int]] = []
    for e in edges:
        if e not in seen:
            seen.add(e)
            unique_edges.append(e)

    if not unique_edges:
        raise ValueError("Graph has no edges — check topology config.")

    edge_index = torch.tensor(unique_edges, dtype=torch.long).t().contiguous()
    logger.info("Graph: %d edges (after dedup + self-loops)", edge_index.shape[1])

    return LaneGraph(
        num_nodes=num_nodes,
        edge_index=edge_index,
        node_index=node_index,
        index_to_node=index_to_node,
    )
