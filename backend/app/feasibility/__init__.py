"""Stage ④ FEASIBILITY — when the programme does not fit, say what to change.

CLAUDE.md: *explain, re-plan ×2, STOP with options.* The explaining is the hard part.
"Infeasible" is useless to a plot owner; "a 3BHK with covered parking needs 69 m² and
your ground floor has 55 m² buildable — folding the dining into the hall makes it fit"
is a decision they can take.

**Options are measured, not guessed.** Each candidate change is applied and the solver
actually run against it, so the report says how many topologies become legal rather
than asserting that something ought to help. That is only affordable because Stage A
generates in microseconds and Stage B proves infeasibility in milliseconds — the same
property that made generate-and-rank the right shape for ⑤.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from app.ir.envelope import Envelope
from app.ir.enums import SpaceKind
from app.ir.plan import AdjacencySpec, Program, RoomSpec
from app.ir.layout import Layout
from app.solver import (
    TUNE_SHORTLIST, footprint, improve, shortlist_for, slicing, tuning,
)
from app.solver.score import score

# Enough to tell "some topologies work" from "none do" without paying for precision
# nobody reads. The published number is a ratio, not an estimate of the true rate.
# Legal arrangements in the shortlist above which the floor has real margin. Two is
# not a margin — it is one bad swap from none — and the reference briefs sit at 0, 0,
# 4 and 1, so the line falls between "solvable" and "solvable with room to spare".
_COMFORTABLE = 3

# Topologies generated before ranking, matching `solve`'s DEFAULT_CANDIDATES. The probe
# has to sample from the same pool the real search does, or its rate describes a
# different procedure.
_GENERATED = 900

# A raw hit rate is the wrong thing to threshold. What a user cares about is whether
# stage ⑤ will actually return a plan, and ⑤ tries `TUNE_SHORTLIST` topologies — so a
# 1-in-12 floor succeeds 87% of the time and a 1-in-60 floor only 34%. The verdict is
# that probability, not the rate behind it.
_DEPENDABLE = 0.70


@dataclass(frozen=True, slots=True)
class Option:
    """One change the user could make, and what it measurably buys."""

    change: str
    because: str
    feasible_after: int
    probes: int

    @property
    def helps(self) -> bool:
        return self.feasible_after > 0

    def __str__(self) -> str:
        verdict = (
            f"{self.feasible_after}/{self.probes} layouts become legal"
            if self.helps
            else "still no legal layout"
        )
        return f"{self.change} — {verdict}. {self.because}"


@dataclass(frozen=True, slots=True)
class Verdict:
    """What stage ⑤ will be able to do, and what to do about it."""

    feasible: bool
    floor: int
    reason: str
    options: list[Option]

    def __str__(self) -> str:
        head = f"floor {self.floor}: {'fits' if self.feasible else self.reason}"
        return "\n".join([head, *(f"  · {o}" for o in self.options)])


def assess(program: Program, envelope: Envelope, *, floor: int = 1) -> Verdict:
    """Can this floor be laid out legally, and if not, what would fix it?"""
    rooms = program.on_floor(floor)
    if not rooms:
        return Verdict(True, floor, "nothing on this floor", [])

    legal = _probe(rooms, envelope, program)
    needed = sum(room.min_area_sq_m for room in rooms)
    packed = needed / envelope.max_footprint_sq_m if envelope.max_footprint_sq_m else 0

    # **Not a probability any more, and the change is a correction.** The old verdict
    # sampled a hit rate and compounded it — "5 in 60, and ⑤ tries 24, so 88%". That
    # reasoning needs the 24 to be independent draws, and they stopped being draws when
    # the shortlist became *ranked*: stage ⑤ tries the best 24, and this probe now runs
    # that same ranked shortlist. One legal arrangement among the ones ⑤ will actually
    # try means ⑤ finds it, deterministically, not 88% of the time.
    #
    # Measured against six seeds a brief, the count in the shortlist separates cleanly
    # where the compounded rate did not: 0 / 0 / 4 / 1 legal against 0 / 1 / 6 / 6
    # clean solves on the four reference briefs. The compounded formula called the
    # 50x80 undependable at 71% while it laid out cleanly every single time.
    if legal >= _COMFORTABLE:
        return Verdict(
            True, floor,
            f"{legal} of the {TUNE_SHORTLIST} arrangements stage \u2464 tries come out "
            f"legal ({packed:.0%} packed)",
            [],
        )

    if legal:
        # Solvable, and thin. Said plainly in the reason rather than dressed up with
        # options: nothing is wrong yet, and offering a plot owner rooms to drop when
        # their house fits is noise. One legal arrangement in twenty-four is a margin
        # worth knowing about, not a problem to solve.
        return Verdict(
            True, floor,
            f"only {legal} of the {TUNE_SHORTLIST} arrangements stage \u2464 tries "
            f"comes out legal ({packed:.0%} packed) — solvable, with no margin",
            [],
        )

    # "None found" is not "none exists", and the difference started to matter once
    # minimums were measured inside the walls: the rate on a marginal floor fell far
    # enough that 0 in 60 became a normal result for a plot stage ⑤ still solves. A
    # 30x50 probes 0/60 at one seed and dimensions a clean plan at another. Claiming
    # impossibility on that evidence is a stronger statement than the measurement
    # supports, and it is the kind a plot owner would act on.
    reason = (
        f"needs {needed:.1f} m² of rooms at legal minimums and only "
        f"{envelope.max_footprint_sq_m:.1f} m² is buildable ({packed:.0%} packed) — "
        f"none of the {TUNE_SHORTLIST} arrangements stage \u2464 tries came out legal"
    )
    return Verdict(False, floor, reason, _options(rooms, envelope, program))


def _probe(rooms: list[RoomSpec], envelope: Envelope, program: Program | None = None) -> int:
    """How many of the arrangements stage ⑤ will try come out as legal plans.

    The *rate* is the useful quantity, not just whether it is zero. A floor at 1/60 is
    possible and undependable; one at 40/60 the solver will find first time. Both
    report "feasible" to a boolean and they are not the same product.

    **The probe must run the procedure stage ⑤ runs, or it measures the wrong thing.**
    This tuned *unranked* random trees while `solve` generates several hundred, ranks
    them and tunes the best — and a raw random tree is usually undimensionable, so the
    probe was reporting the feasibility of a search nobody performs. It went unnoticed
    while the envelope was generous enough that random trees dimensioned anyway; the
    moment the tiled rectangle shrank to the programme's own size it began calling
    plots infeasible that stage ⑤ then solved with zero unbuildable rooms.

    One advancing generator, not a fresh `Random(i)` per candidate — seeding afresh
    each time samples a far narrower set of trees, which is how an earlier measurement
    of this same floor came out at 0/150 when the true rate is nearer 1 in 60.
    """
    if not rooms:
        return 0
    # The same rectangle stage ⑤ will actually tile, or the probe measures a floor
    # nobody is going to build — a programme can be undimensionable in its envelope
    # and perfectly comfortable in the smaller footprint it asked for.
    bounds = footprint(envelope, rooms)
    x_min_m, y_min_m, x_max_m, y_max_m = bounds
    weights = slicing.effective_areas(
        rooms, (x_max_m - x_min_m) * (y_max_m - y_min_m)
    )

    # The adjacency graph, restricted to the rooms still present. `_options` probes
    # reduced programmes, and an edge naming a room that was dropped will not
    # construct — but ranking without the edges at all is what made the probe and the
    # solver disagree: it ordered by a different key and reported a 30x50 infeasible
    # that `solve` then dimensioned with zero unbuildable rooms.
    ids = {room.id for room in rooms}
    reduced = Program(
        rooms=rooms,
        adjacencies=[
            edge for edge in (program.adjacencies if program else [])
            if {edge.a, edge.b} <= ids
        ],
    )

    shortlist = shortlist_for(
        reduced, rooms, bounds, weights, envelope, floor=rooms[0].floor, seed=0,
    )
    # **A dimensioned topology is not yet a legal plan.** The probe used to count
    # `tune` returning something, and that was close enough while Stage B's own
    # constraints were the whole of legality. They are not any more: Stage B works on
    # gross rectangles with a conservative wall allowance, while `score` measures the
    # clear floor side by side and also weighs sectors, road access and the shaft. The
    # gap showed up immediately — a 50x80 that `solve` lays out cleanly on six seeds
    # out of six was being called undependable off a raw tune rate.
    #
    # So the probe now runs what `solve` runs: tune, build, hill-climb, and ask whether
    # the result has a room below a minimum. The rate that comes out is the probability
    # a topology yields a *legal plan*, which is the quantity the verdict formula was
    # always assuming it had.
    legal = 0
    for _, _, _, tree in shortlist[:TUNE_SHORTLIST]:
        dimensioned = tuning.tune(tree, bounds, weights, time_limit_s=0.15)
        if dimensioned is None:
            continue
        try:
            layout = Layout(
                rooms=dimensioned, x_min_m=x_min_m, y_min_m=y_min_m,
                x_max_m=x_max_m, y_max_m=y_max_m, floor=rooms[0].floor,
                road_edges=envelope.road_edges,
            )
        except ValueError:
            continue
        if improve(layout, reduced).unbuildable == 0:
            legal += 1
    return legal


def _options(
    rooms: list[RoomSpec], envelope: Envelope, program: Program | None = None
) -> list[Option]:
    """Candidate changes, each measured. Ordered by what a person gives up.

    Nothing here silently edits the programme. A plot owner who wanted a separate
    dining room should be told what it costs, not quietly deprived of it.
    """
    catalogue: list[tuple[str, str, Callable[[list[RoomSpec]], list[RoomSpec]]]] = [
        (
            "move the car porch into the front setback",
            "standard on plots this size, but confirm it is permitted — see VERIFY.md Q1",
            lambda rs: [r for r in rs if r.kind is not SpaceKind.CAR_PARKING],
        ),
        (
            "fold the dining area into the hall",
            "usual on a small plot; the hall absorbs the area rather than losing it",
            _merge_dining,
        ),
        (
            "move another bedroom upstairs",
            "keeps every room, at the cost of stairs",
            lambda rs: _drop_one(rs, SpaceKind.BEDROOM),
        ),
    ]
    found = []
    for change, because, transform in catalogue:
        reduced = transform(rooms)
        if len(reduced) == len(rooms):
            continue  # nothing to remove; the option does not apply here
        found.append(Option(change, because, _probe(reduced, envelope, program), TUNE_SHORTLIST))
    return found


def _merge_dining(rooms: list[RoomSpec]) -> list[RoomSpec]:
    """Drop the dining room and give the hall its area — a combined living-dining,
    which is what most Indian houses of this size actually have."""
    dining = next((r for r in rooms if r.kind is SpaceKind.DINING), None)
    if dining is None:
        return rooms
    out = []
    for room in rooms:
        if room is dining:
            continue
        if room.kind is SpaceKind.HALL:
            room = room.model_copy(
                update={
                    "target_area_sq_m": room.target_area_sq_m + dining.target_area_sq_m,
                }
            )
        out.append(room)
    return out


def _drop_one(rooms: list[RoomSpec], kind: SpaceKind) -> list[RoomSpec]:
    for index, room in enumerate(rooms):
        if room.kind is kind:
            return rooms[:index] + rooms[index + 1 :]
    return rooms
