"""Workspace topology: which rooms, panes and agent sessions Orca and Git report, read-only.

Separate from ``snapshot@1`` (tasks, verification, review, recovery). Nothing in
this package sends input, starts or stops anything, or is used by the console.
"""

from .document import CAPABILITIES, topology_document
from .model import (
    TOPOLOGY_SCHEMA,
    Activity,
    Availability,
    Connection,
    EndEvidence,
    IdentityBasis,
    Intent,
    Lifecycle,
    Provider,
    Topology,
    WorkerLink,
)
from .parse import RawObservation, Reply, parse_observation
from .tracker import TopologyTracker, decide_intent

__all__ = [
    "CAPABILITIES", "TOPOLOGY_SCHEMA", "Activity", "Availability", "Connection", "EndEvidence", "IdentityBasis",
    "Intent", "Lifecycle", "Provider", "RawObservation", "Reply", "Topology", "TopologyTracker", "WorkerLink",
    "decide_intent", "parse_observation", "topology_document",
]
