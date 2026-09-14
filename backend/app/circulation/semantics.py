"""The circulation rules, read once, with the few operations every analysis needs.

Everything the engine believes about a room kind comes through here from
`circulation_v1.json` — its role, its zone, what walking through it costs — so no module
carries its own list of which rooms are private. Two copies of that judgment are how
stage ⑥ and stage ⑦ once disagreed about the same plan.
"""

from __future__ import annotations

from app.ir.enums import CirculationRole, Grade, Zone
from app.rules import load_ruleset

RULES = "circulation_v1"

_RANK: dict[Grade | None, int] = {
    None: 0, Grade.MINOR: 1, Grade.MAJOR: 2, Grade.CRITICAL: 3,
}
_BY_RANK = {value: key for key, value in _RANK.items()}

# Where each zone sits on the gradient from the street inwards. Service runs beside the
# gradient and is ranked with the semi-private rooms: a guest should no more walk through
# the kitchen than down the bedroom corridor.
ZONE_RANK: dict[Zone, int] = {
    Zone.EXTERNAL: -1,
    Zone.PUBLIC: 0,
    Zone.SEMI_PUBLIC: 1,
    Zone.SEMI_PRIVATE: 2,
    Zone.SERVICE: 2,
    Zone.PRIVATE: 3,
}


def data() -> dict:
    return load_ruleset(RULES).data


def stamp() -> str:
    return load_ruleset(RULES).stamp


def grade(value: str | None) -> Grade | None:
    """A grade as the ruleset spells it; `none` means no finding at all."""
    return None if value in (None, "none") else Grade(value)


def rank(value: Grade | None) -> int:
    return _RANK[value]


def worst(*values: Grade | None) -> Grade | None:
    return _BY_RANK[max((_RANK[value] for value in values), default=0)]


def weaker(value: Grade | None) -> Grade | None:
    """One step lower — how a relationship below the strong weight is graded."""
    return _BY_RANK[max(0, _RANK[value] - 1)]


def role_of(kind: str) -> CirculationRole:
    entry = data()["kinds"].get(kind)
    return CirculationRole(entry["role"]) if entry else CirculationRole.OUTSIDE


def zone_of(kind: str, *, shared: bool = False) -> Zone | None:
    """The kind's zone, or None where it is derived from what opens off it.

    `shared` is a bathroom the house uses rather than one a bedroom owns.
    """
    entry = data()["kinds"].get(kind)
    if entry is None:
        return Zone.EXTERNAL
    value = entry.get("shared_zone", entry["zone"]) if shared else entry["zone"]
    return None if value == "derived" else Zone(value)


def pass_through(host: CirculationRole, target: CirculationRole) -> Grade | None:
    """What walking through a room of role `host` costs on the way to `target`.

    Outside nodes cost nothing here; which walks may use them at all is the router's
    rule, not a price.
    """
    row = data()["pass_through"].get(host.value)
    if row is None:
        return None
    return grade(row.get(target.value, row["*"]))
