"""The circulation engine: can a person move through the house the way it is meant to be used?

Stage ⑦ asked whether every room could be reached, and a plan could answer yes while a
bedroom was the only way into another. This package asks the architectural question
instead, of one drawn storey at a time: the graph of every way through it
(`graph`), whether each room has the access its use demands (`topology`), how the walks
a house is used for actually go (`journeys`), what the privacy gradient and the
circulation area look like (`analysis`), and a score no critical failure can pass
(`scoring`), with every finding explained and given a correction (`diagnose`).

Everything it believes about a room kind is data, in `rules/circulation_v1.json`.
Nothing here imports the solver or stage ⑥: it reads their output.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.circulation import analysis, journeys, scoring, semantics, topology
from app.circulation.graph import build
from app.ir.circulation import CirculationSummary
from app.ir.layout import Layout
from app.ir.plan import Program
from app.ir.refined import RefinedFloor
from app.ir.validation import Finding


@dataclass(frozen=True)
class Evaluation:
    """A storey's circulation verdict, and the findings behind it, most severe first."""

    summary: CirculationSummary
    findings: list[Finding]


def evaluate(layout: Layout, program: Program, floor: RefinedFloor) -> Evaluation:
    """Judge one drawn storey. Reads stage ⑥'s drawing and changes nothing in it."""
    graph = build(layout, program, floor)
    topo = topology.analyse(graph, layout)
    walks = journeys.analyse(topo)
    measured = analysis.analyse(topo, walks)
    findings = sorted(
        [*topo.findings, *walks.findings, *measured.findings],
        key=lambda f: -semantics.rank(f.grade),
    )
    return Evaluation(scoring.summarise(topo, walks, measured, findings), findings)
