"""The 14-plan benchmark: what every solver, search or circulation change is measured on.

Two questions, kept apart on purpose. **Did the generated plans change?** Answered by
a hash of each plan's geometry — room rectangles, walls, openings, fixtures — and never
of its reports, so adding a field to a report or rewording a finding is not a change to
the house. **Did they get worse?** Answered by counts a person can argue with: errors and
warnings by check, Vastu zones met, rooms with air from two sides, bathrooms ventilated.

A change that is meant to alter plans will fail the first question by design. That is
the point at which someone reads the second, and re-baselines deliberately:

    .venv/bin/python tests/benchmark/benchmark_plans.py            # compare with baseline
    .venv/bin/python tests/benchmark/benchmark_plans.py --write    # accept as the new baseline
    .venv/bin/pytest -m benchmark                                   # the same, as tests

Runs offline, with no model and no key. About 40 seconds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.envelope import build_envelope  # noqa: E402
from app.ir.enums import SpaceKind  # noqa: E402
from app.ir.envelope import Envelope  # noqa: E402
from app.ir.plan import Program, ProgramDraft  # noqa: E402
from app.llm import fallback  # noqa: E402
from app.llm.program import build_program  # noqa: E402
from app.program import expand, spec_for  # noqa: E402
from app.refine import draw  # noqa: E402
from app.rules import load_ruleset  # noqa: E402
from app.solver import plan  # noqa: E402
from app.validator import check, judge  # noqa: E402

CASES = HERE / "cases.json"
BASELINE = HERE / "baseline.json"
GOLDEN = ROOT / "tests" / "golden"
_BATHS = {SpaceKind.BATHROOM, SpaceKind.WC}


@dataclass
class CaseResult:
    id: str
    floors: int
    errors: int
    warnings: int
    by_check: dict[str, int] = field(default_factory=dict)
    zones_met: int = 0
    zones_wanted: int = 0
    two_sided_air: int = 0
    air_rooms: int = 0
    no_air: int = 0
    baths_vented: int = 0
    baths: int = 0
    geometry: str = ""
    seconds: float = 0.0


class _Recorded:
    """Hands back a model answer recorded earlier, so the replay needs no network."""

    name = "recorded"

    def __init__(self, draft: ProgramDraft):
        self.draft = draft

    def parse(self, **_kwargs):
        return self.draft


def load_cases() -> tuple[int, list[dict]]:
    data = json.loads(CASES.read_text())
    return data["seed"], data["cases"]


def inputs(case: dict):
    """The brief, envelope and programme a case is solved from."""
    if case["source"] == "offline":
        brief = fallback.parse(case["brief"])
        envelope = build_envelope(brief, allow_unverified=True)
        return brief, envelope, expand(brief, envelope, stilt=case.get("stilt", False))
    if case["source"] == "recorded":
        golden = json.loads((GOLDEN / "program_drafts.json").read_text())
        brief = fallback.parse(case["brief"])
        envelope = Envelope.model_validate(golden["envelopes"][case["brief"]])
        draft = ProgramDraft.model_validate(golden["drafts"][case["brief"]])
        program, _ = build_program(brief, envelope, provider=_Recorded(draft))
        return brief, envelope, program
    if case["source"] == "live":
        live = json.loads((GOLDEN / "live_programs.json").read_text())["programmes"][case["id"]]
        stored = Program.model_validate(live["program"])
        # Rebuilt from the current rules, keeping only what the model decided — the same
        # construction `llm/program.py::_merge` uses, so a rule change reaches these too.
        rooms = [
            spec_for(room.kind, room.id, floor=room.floor).model_copy(update={
                "sector": room.sector,
                "target_area_sq_m": room.target_area_sq_m,
                "max_target_sq_m": room.max_target_sq_m,
            })
            for room in stored.rooms
        ]
        program = Program(rooms=rooms, adjacencies=stored.adjacencies)
        return fallback.parse(live["brief_text"]), Envelope.model_validate(live["envelope"]), program
    raise ValueError(f"{case['id']}: unknown source {case['source']!r}")


def geometry_hash(bundle) -> str:
    """What the house is, not what was said about it: rooms, walls, openings, fixtures.

    Scores and violation strings are left out with the reports — rewording a message or
    adding a report field must not read as a different plan.
    """
    dumped = bundle.model_dump(mode="json")
    keep = {"rooms", "x_min_m", "y_min_m", "x_max_m", "y_max_m", "floor", "shafts", "shaft_zone", "road_edges"}
    shape = {
        "layouts": [{k: v for k, v in layout.items() if k in keep} for layout in dumped["layouts"]],
        "floors": dumped["floors"],
    }
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()


def measure(case: dict, seed: int) -> tuple[CaseResult, object]:
    brief, envelope, program = inputs(case)
    started = time.perf_counter()
    bundle = check(draw(plan(brief, envelope, program, seed=seed, judge=judge(program, envelope)), envelope))
    seconds = time.perf_counter() - started

    specs = {room.id: room for room in program.rooms}
    result = CaseResult(id=case["id"], floors=len(bundle.layouts), errors=0, warnings=0)
    for layout, floor, report in zip(bundle.layouts, bundle.floors, bundle.reports):
        result.errors += report.errors
        result.warnings += len(report.findings) - report.errors
        for finding in report.findings:
            result.by_check[finding.check] = result.by_check.get(finding.check, 0) + 1
        for placed in layout.rooms:
            spec = specs[placed.room_id]
            if spec.sector is not None:
                result.zones_wanted += 1
                result.zones_met += layout.sector_of(placed) is spec.sector
            if spec.kind in _BATHS:
                result.baths += 1
                result.baths_vented += bool(floor.air_sides(placed.room_id))
        result.two_sided_air += len(report.cross_ventilated)
        result.air_rooms += len(report.cross_ventilated) + len(report.single_sided)
        result.no_air += sum(1 for room in report.single_sided if not floor.air_sides(room))
    result.by_check = dict(sorted(result.by_check.items()))
    result.geometry = geometry_hash(bundle)
    result.seconds = round(seconds, 2)
    return result, bundle


def run_all() -> list[CaseResult]:
    seed, cases = load_cases()
    return [measure(case, seed)[0] for case in cases]


# Counts where more is worse, and counts where fewer is worse.
_LOWER_IS_BETTER = ("errors", "warnings", "no_air")
_HIGHER_IS_BETTER = ("zones_met", "two_sided_air", "baths_vented")


def regressions(result: CaseResult, baseline: dict) -> list[str]:
    """How this case is worse than its baseline, in words. Empty when it is not."""
    worse = []
    for name in _LOWER_IS_BETTER:
        if getattr(result, name) > baseline[name]:
            worse.append(f"{name} {baseline[name]} → {getattr(result, name)}")
    for name in _HIGHER_IS_BETTER:
        if getattr(result, name) < baseline[name]:
            worse.append(f"{name} {baseline[name]} → {getattr(result, name)}")
    return worse


def load_baseline() -> dict:
    data = json.loads(BASELINE.read_text())
    return {row["id"]: row for row in data["cases"]}


def write_baseline(results: list[CaseResult]) -> None:
    payload = {
        "_note": (
            "Accepted results of tests/benchmark/cases.json. Rewrite only deliberately, "
            "after reading what changed: `.venv/bin/python tests/benchmark/benchmark_plans.py --write`. "
            "`seconds` is recorded for information and never compared."
        ),
        "rulesets": {
            name: load_ruleset(name).stamp for name in ("spaces_v1", "refine_v1", "setbacks_v1")
        },
        "cases": [asdict(r) for r in results],
    }
    BASELINE.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _table(results: list[CaseResult], baseline: dict | None) -> str:
    lines = [f"{'case':28} {'err':>3} {'warn':>4} {'zones':>7} {'air':>5} {'baths':>5} {'time':>6}  change"]
    for r in results:
        base = (baseline or {}).get(r.id)
        if base is None:
            note = "new case"
        elif r.geometry != base["geometry"]:
            worse = regressions(r, base)
            note = "PLANS CHANGED" + (f" — worse: {'; '.join(worse)}" if worse else " — no metric worse")
        else:
            note = "same plans" if not regressions(r, base) else f"same plans, worse: {'; '.join(regressions(r, base))}"
        lines.append(
            f"{r.id:28} {r.errors:>3} {r.warnings:>4} {r.zones_met:>3}/{r.zones_wanted:<3} "
            f"{r.two_sided_air:>2}/{r.air_rooms:<2} {r.baths_vented:>2}/{r.baths:<2} {r.seconds:>5.1f}s  {note}"
        )
    total = lambda name: sum(getattr(r, name) for r in results)  # noqa: E731
    lines.append(
        f"{'TOTAL':28} {total('errors'):>3} {total('warnings'):>4} {total('zones_met'):>3}/{total('zones_wanted'):<3} "
        f"{total('two_sided_air'):>2}/{total('air_rooms'):<2} {total('baths_vented'):>2}/{total('baths'):<2} "
        f"{sum(r.seconds for r in results):>5.1f}s"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="accept these results as the baseline")
    args = parser.parse_args(argv)
    import warnings

    warnings.simplefilter("ignore")
    results = run_all()
    baseline = load_baseline() if BASELINE.exists() else None
    print(_table(results, baseline))
    if args.write:
        write_baseline(results)
        print(f"baseline written: {BASELINE.relative_to(ROOT)}")
        return 0
    if baseline is None:
        print("no baseline yet — run with --write to accept these results")
        return 1
    changed = [r.id for r in results if r.id in baseline and r.geometry != baseline[r.id]["geometry"]]
    worse = [r.id for r in results if r.id in baseline and regressions(r, baseline[r.id])]
    return 1 if changed or worse else 0


if __name__ == "__main__":
    raise SystemExit(main())
