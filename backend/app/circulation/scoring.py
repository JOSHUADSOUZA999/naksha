"""One number for how well a storey is walked, and a verdict no number can buy.

Seven dimensions, each 0-100, weighted by `circulation_v1.scoring`: connectivity (the
access each room actually has, weighted by what the room is for), relationships,
privacy, journeys, efficiency, vertical circulation and arrival. A dimension that does
not apply to the storey drops out and the rest are reweighted.

Where a dimension is measured from the same evidence as its findings — connectivity from
access, relationships and journeys from the routes — those findings are not deducted
again. The others start from their measurement and lose `deductions[grade]` for each
finding `deduct` assigns them.

The score is not the verdict. Any critical finding fails the storey whatever the number
says, and multiplies it by `critical_validity`, so a failed plan cannot read as a good
one while `quality` still tells two failed plans apart.
"""

from __future__ import annotations

from app.circulation import semantics
from app.circulation.analysis import Analysis
from app.circulation.journeys import JourneyAnalysis
from app.circulation.topology import Topology
from app.ir.circulation import CirculationSummary, Dimensions
from app.ir.enums import Grade, Health
from app.ir.validation import Finding


def summarise(
    topo: Topology, walks: JourneyAnalysis, measured: Analysis, findings: list[Finding]
) -> CirculationSummary:
    rules = semantics.data()["scoring"]
    counts = {grade: sum(1 for f in findings if f.grade is grade) for grade in Grade}
    dimensions = _dimensions(topo, walks, measured, findings, rules)
    weights = rules["weights"]
    applied = {
        name: value for name, value in dimensions.model_dump().items() if value is not None
    }
    quality = round(
        sum(weights[name] * value for name, value in applied.items())
        / sum(weights[name] for name in applied),
        1,
    )
    passed = counts[Grade.CRITICAL] == 0
    score = quality if passed else round(quality * rules["critical_validity"], 1)
    return CirculationSummary(
        ruleset=semantics.stamp(),
        passed=passed,
        health=_health(passed, score, counts[Grade.MAJOR], rules["health"]),
        score=score,
        quality=quality,
        dimensions=dimensions,
        circulation_share=round(measured.share, 4),
        critical=counts[Grade.CRITICAL],
        major=counts[Grade.MAJOR],
        minor=counts[Grade.MINOR],
        access=topo.access,
        isolation=topo.isolation,
        journeys=walks.journeys,
        corridors=measured.corridors,
        graph=topo.graph,
    )


def _dimensions(topo, walks, measured, findings, rules) -> Dimensions:
    if topo.blocked:
        # Nothing on a storey nobody can enter can be walked, so nothing measures well.
        return Dimensions(connectivity=0, relationships=0, privacy=0, journeys=0, efficiency=0)
    lost = _deductions(findings, rules)
    stairs = set(semantics.data()["efficiency"]["vertical_kinds"])
    vertical = None
    if any(node.kind in stairs for node in topo.graph.nodes):
        base = sum(walks.vertical) / len(walks.vertical) if walks.vertical else 100.0
        vertical = _clamp(base - lost["vertical"])
    arrival = None if walks.arrival is None else _clamp(walks.arrival - lost["arrival"])
    return Dimensions(
        connectivity=_clamp(_connectivity(topo, rules)),
        relationships=_clamp(walks.relationships),
        privacy=_clamp(100.0 - lost["privacy"]),
        journeys=_clamp(walks.walking),
        efficiency=_clamp(measured.efficiency - lost["efficiency"]),
        vertical=vertical,
        arrival=arrival,
    )


def _connectivity(topo: Topology, rules: dict) -> float:
    """The access credit of every room, weighted by what the room is for.

    A bedroom reached through another bedroom earns nothing; one reached through the
    living room most of its credit; a car bay, entered from the street, does not count.
    """
    importance, credit = rules["importance"], rules["access_credit"]
    weight = total = 0.0
    for row in topo.access:
        share = importance.get(topo.graph.node(row.room).role.value, 0.0)
        weight += share
        total += share * credit[row.grade.value if row.grade is not None else "none"]
    return 100.0 * total / weight if weight else 100.0


def _deductions(findings: list[Finding], rules: dict) -> dict[str, float]:
    per_grade = rules["deductions"]
    lost = {name: 0.0 for name in rules["weights"]}
    for dimension, rule_ids in rules["deduct"].items():
        lost[dimension] = float(
            sum(per_grade[f.grade.value] for f in findings if f.rule in rule_ids)
        )
    return lost


def _health(passed: bool, score: float, major: int, bands: dict) -> Health:
    if not passed:
        return Health.FAIL
    if score >= bands["good"] and major <= bands["good_major_limit"]:
        return Health.GOOD
    if score >= bands["fair"]:
        return Health.FAIR
    return Health.POOR


def _clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)
