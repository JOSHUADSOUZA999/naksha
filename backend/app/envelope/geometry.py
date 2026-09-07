"""Road-relative plot to compass-aligned rectangle. No rules, no lookups — pure
arithmetic, so every claim here is checkable by hypothesis.

The one conversion that matters: `PlotSpec.width_m` is frontage and `depth_m` runs
away from the road, so which compass axis each occupies depends on the facing. An
east-facing 30x40 plot is 40 ft east-west and 30 ft north-south, the opposite of a
north-facing one. Getting this backwards rotates every plan ninety degrees.
"""

from __future__ import annotations

from app.ir.enums import Facing
from app.ir.envelope import CARDINALS
from app.ir.models import PlotSpec

_BY_DEGREES = {f.degrees: f for f in Facing}


def snap_to_cardinal(facing: Facing) -> Facing:
    """Nearest cardinal edge.

    A rectangle has no north-east side, so a diagonal facing has to land on one of
    four edges before a setback can attach to it. Ties resolve clockwise — north-east
    becomes east — which is arbitrary but fixed. It rarely fires: a diagonal facing
    almost always accompanies "corner", and stage ① already resolves *those* into two
    cardinals, so what reaches here is the loose-speech remainder.
    """
    return _BY_DEGREES[((facing.degrees + 45) // 90 * 90) % 360]


def opposite(facing: Facing) -> Facing:
    return _BY_DEGREES[(facing.degrees + 180) % 360]


def plot_extent(plot: PlotSpec) -> tuple[float, float]:
    """(east-west, north-south) extent in metres.

    Frontage runs *along* the road, so it spans the axis perpendicular to the facing.
    """
    frontage_runs_east_west = snap_to_cardinal(plot.facing) in (Facing.NORTH, Facing.SOUTH)
    if frontage_runs_east_west:
        return plot.width_m, plot.depth_m
    return plot.depth_m, plot.width_m


def assign_setbacks(
    road_edges: list[Facing], *, front: float, rear: float, sides: list[float]
) -> dict[Facing, float]:
    """One margin per compass edge.

    Every road edge takes the front setback — that is the whole reason `road_edges`
    is a list, and the difference between a correct corner-plot envelope and one that
    quietly under-sets-back the second road. The edge opposite the primary frontage
    takes the rear margin unless a road is already on it, and whatever is left takes
    the side margins in bearing order.
    """
    roads = {snap_to_cardinal(edge) for edge in road_edges}
    back = opposite(snap_to_cardinal(road_edges[0]))

    applied: dict[Facing, float] = {}
    remaining = list(sides)
    for edge in CARDINALS:
        if edge in roads:
            applied[edge] = front
        elif edge == back:
            applied[edge] = rear
        else:
            # Fewer side values than side edges is a malformed band, not a crash:
            # reuse the last rather than lose an edge.
            applied[edge] = remaining.pop(0) if remaining else (sides[-1] if sides else 0.0)
    return applied


def buildable_rect(
    plot: PlotSpec, setbacks: dict[Facing, float]
) -> tuple[float, float, float, float]:
    """(x_min, y_min, x_max, y_max), metres from the plot's south-west corner.

    May return an empty or inverted rectangle — a small plot under large margins
    genuinely has nowhere to build. Reporting that is the caller's job; silently
    clamping it to zero would hand stage ③ a plot it cannot fill and no reason why.
    """
    east_west, north_south = plot_extent(plot)
    return (
        setbacks[Facing.WEST],
        setbacks[Facing.SOUTH],
        east_west - setbacks[Facing.EAST],
        north_south - setbacks[Facing.NORTH],
    )
