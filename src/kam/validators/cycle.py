"""V-CYC — cycle detection (graph-deterministic, STR-001).

Iterates `networkx.find_cycle` to surface every cycle in the prereq graph
without paying `simple_cycles`' exponential worst case on dense LLM
extractions. Each found cycle drops one of its edges from a working copy
and re-runs detection until no cycles remain, the cycle-count cap fires,
or the wall-clock timeout fires.

Output: one Finding per cycle. The whole run flags
`cycle_validator_timeout_hit=True` on the upstream metadata when either
limit was reached so consumers know the report is partial.

Spec mappings:
  §22, §33.1.2 — V-CYC, STR-001, graph-deterministic.
  §25.5.2     — severity capped at "medium" in v1 (extracted tier).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

import networkx as nx

from ..models import EvidenceSpan, Finding
from .base import build_finding

logger = logging.getLogger(__name__)

V_CYC_CYCLE_LIMIT = 100
V_CYC_TIMEOUT_SEC = 5.0


@dataclass
class VCycResult:
    findings: List[Finding]
    timeout_hit: bool
    cycles_found: int


def run_v_cyc(
    *,
    segment_id: int,
    prereq_edges: Sequence[Tuple[str, str]],
    cycle_limit: int = V_CYC_CYCLE_LIMIT,
    timeout_sec: float = V_CYC_TIMEOUT_SEC,
    extra_evidence: Iterable[EvidenceSpan] = (),
) -> VCycResult:
    if not prereq_edges:
        return VCycResult(findings=[], timeout_hit=False, cycles_found=0)

    graph = nx.DiGraph()
    graph.add_edges_from(prereq_edges)

    findings: List[Finding] = []
    started_at = time.monotonic()
    timeout_hit = False
    cycles_found = 0

    while True:
        if cycles_found >= cycle_limit:
            logger.warning(
                "V-CYC: cycle limit %d reached on segment %s; remaining cycles unreported.",
                cycle_limit,
                segment_id,
            )
            timeout_hit = True
            break
        if time.monotonic() - started_at > timeout_sec:
            logger.warning(
                "V-CYC: timeout %.1fs exceeded on segment %s; remaining cycles unreported.",
                timeout_sec,
                segment_id,
            )
            timeout_hit = True
            break
        try:
            cycle_edges = nx.find_cycle(graph, orientation="original")
        except nx.NetworkXNoCycle:
            break

        nodes_in_cycle = _cycle_nodes(cycle_edges)
        edges_str = " → ".join([*nodes_in_cycle, nodes_in_cycle[0]])
        findings.append(
            build_finding(
                code="STR-001",
                validator_id="V-CYC",
                severity="medium",
                confidence="high",
                segment_id=segment_id,
                affected_concepts=nodes_in_cycle,
                evidence=list(extra_evidence),
                why=f"Prerequisite cycle detected: {edges_str}.",
                repair_options=[
                    f"Drop or invert one edge in the cycle ({edges_str}).",
                    "Re-extract this section to verify the cycle is not an extraction artifact.",
                ],
            )
        )
        cycles_found += 1

        # Drop the first edge from this cycle so we can find the next independent cycle
        # rather than re-discovering the same one.
        first_edge = cycle_edges[0]
        u, v = first_edge[0], first_edge[1]
        if graph.has_edge(u, v):
            graph.remove_edge(u, v)

    return VCycResult(findings=findings, timeout_hit=timeout_hit, cycles_found=cycles_found)


def _cycle_nodes(cycle_edges) -> List[str]:
    """Return the ordered list of nodes that form the cycle."""
    nodes: List[str] = []
    for edge in cycle_edges:
        u = edge[0]
        if not nodes or nodes[-1] != u:
            nodes.append(u)
    return nodes
