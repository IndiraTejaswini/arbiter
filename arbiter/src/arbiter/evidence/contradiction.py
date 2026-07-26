"""Contradiction vocabulary shared by the four detection layers
(temporal.py, numeric.py, identity.py, semantic.py) and the graph
orchestrator (graph.py) that runs them and materializes findings as
first-class graph nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


@dataclass(frozen=True)
class Contradiction:
    kind: str
    severity: str  # LOW | MEDIUM | HIGH | CRITICAL
    description: str
    node_ids: Tuple[str, ...]
    layer: str  # temporal | numeric | identity | semantic
