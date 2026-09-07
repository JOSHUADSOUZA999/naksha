"""Deterministic brief parser — the floor under stage ①.

CLAUDE.md: *the LLM being down must never break generation entirely.* This is what
runs when the model is unreachable or never returns a valid Brief. It is a real
parser, not a stub: it handles the phrasings that actually appear in Indian site
descriptions, and every value it guesses is pushed into `assumptions` so the
degradation is visible to the user rather than silent.

No network, no API key, no imports beyond the standard library and the IR.
"""

from __future__ import annotations

import math
import re

from app.ir.enums import Facing, RoomKind, VastuStance
from app.ir.models import (
    Assumption,
    Brief,
    BriefDraft,
    Locale,
    PlotSpec,
    ProgramHints,
)
from app.ir.units import AreaUnit, LengthUnit, area_to_sq_m, to_metres

# Typical Indian residential frontage-to-depth ratio. Used only when the text gives
# an area with no dimensions — 1200 sqft becomes 8.6 m x 12.9 m rather than a square.
_FRONTAGE_TO_DEPTH = 2 / 3

# Used when the text gives no size at all. 30x40 ft is the single most common site
# size in south-Indian layouts, which makes it the least surprising stand-in.
_DEFAULT_WIDTH_M = to_metres(30, LengthUnit.FOOT)
_DEFAULT_DEPTH_M = to_metres(40, LengthUnit.FOOT)

_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
}

# "30x40", "30 by 40", "30*40", "40'x60'", "12m x 15m"
_DIMENSION_RE = re.compile(
    r"(?P<a>\d+(?:\.\d+)?)\s*(?P<ua>ft|feet|foot|'|m|mt|mtr|meters?|metres?|yd|yards?)?"
    r"\s*(?:x|\*|by|/|×)\s*"
    r"(?P<b>\d+(?:\.\d+)?)\s*(?P<ub>ft|feet|foot|'|m|mt|mtr|meters?|metres?|yd|yards?)?",
    re.IGNORECASE,
)

_AREA_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>sq\.?\s*ft|sqft|sft|square\s+feet|sq\.?\s*m|sqm|square\s+met(?:er|re)s?"
    r"|sq\.?\s*yd|sqyd|square\s+yards?|gaj|cents?)",
    re.IGNORECASE,
)

_BHK_RE = re.compile(
    r"\b(?P<n>\d|one|two|three|four|five|six|seven|eight)\s*"
    r"(?:b\s*h\s*k|bhk|bed\s*room|bedroom|bed)s?\b",
    re.IGNORECASE,
)

_BATHROOM_RE = re.compile(
    r"\b(?P<n>\d|one|two|three|four|five|six|seven|eight)\s*"
    r"(?:bath\s*rooms?|bathrooms?|baths?|toilets?|washrooms?)\b",
    re.IGNORECASE,
)

_ROAD_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ft|feet|foot|m|mt|mtr|met(?:er|re)s?)?\s*"
    r"(?:wide\s+)?road",
    re.IGNORECASE,
)

_FLOORS_RE = re.compile(
    r"\b(?:g\s*\+\s*(?P<gplus>\d)|(?P<n>\d|one|two|three|four)\s*(?:floors?|storey?s?|stories))\b",
    re.IGNORECASE,
)

# Longest-first so "north east" wins over "north".
_FACING_PATTERNS: list[tuple[re.Pattern[str], Facing]] = [
    (re.compile(p, re.IGNORECASE), facing)
    for p, facing in [
        (r"\bnorth[\s\-]?east\b|\bne\b", Facing.NORTH_EAST),
        (r"\bnorth[\s\-]?west\b|\bnw\b", Facing.NORTH_WEST),
        (r"\bsouth[\s\-]?east\b|\bse\b", Facing.SOUTH_EAST),
        (r"\bsouth[\s\-]?west\b|\bsw\b", Facing.SOUTH_WEST),
        (r"\bnorth\b", Facing.NORTH),
        (r"\bsouth\b", Facing.SOUTH),
        (r"\beast\b", Facing.EAST),
        (r"\bwest\b", Facing.WEST),
    ]
]

_ROOM_PATTERNS: list[tuple[re.Pattern[str], RoomKind]] = [
    (re.compile(p, re.IGNORECASE), kind)
    for p, kind in [
        (r"\bpo?oja\b|\bpuja\b|\bprayer\s+room\b|\bmandir\b", RoomKind.POOJA),
        (r"\bstudy\b|\breading\s+room\b", RoomKind.STUDY),
        (r"\bcar\s*(?:park|parking|porch|shed)\b|\bparking\b|\bgarage\b", RoomKind.CAR_PARKING),
        (r"\bstore\s*room\b|\bstorage\b", RoomKind.STORE),
        (r"\butility\b|\bwash\s+area\b", RoomKind.UTILITY),
        (r"\bguest\s+(?:room|bed)\b", RoomKind.GUEST_ROOM),
        (r"\bservant\b|\bmaid'?s?\s+room\b", RoomKind.SERVANT_ROOM),
        (r"\bbalcon(?:y|ies)\b", RoomKind.BALCONY),
        # "porch" is deliberately absent: in Indian listings it is nearly always
        # "car porch", which the car-parking pattern above already claims. Matching
        # it here too tagged the same phrase as both rooms.
        (r"\bveranda(?:h)?\b|\bsit\s*out\b", RoomKind.VERANDA),
        (r"\bhome\s+office\b|\bwork\s+from\s+home\b", RoomKind.OFFICE),
    ]
]

_CITIES = [
    "Bengaluru", "Bangalore", "Mysuru", "Mysore", "Mangaluru", "Mangalore", "Hubli",
    "Chennai", "Coimbatore", "Madurai", "Salem", "Tiruchirappalli",
    "Hyderabad", "Secunderabad", "Warangal", "Vijayawada", "Visakhapatnam", "Guntur",
    "Kochi", "Cochin", "Thiruvananthapuram", "Trivandrum", "Kozhikode", "Calicut", "Thrissur",
    "Mumbai", "Pune", "Nagpur", "Nashik", "Thane", "Navi Mumbai", "Aurangabad",
    "Ahmedabad", "Surat", "Vadodara", "Rajkot", "Gandhinagar",
    "Delhi", "New Delhi", "Gurugram", "Gurgaon", "Noida", "Ghaziabad", "Faridabad",
    "Jaipur", "Jodhpur", "Udaipur", "Kota",
    "Lucknow", "Kanpur", "Varanasi", "Agra", "Prayagraj",
    "Kolkata", "Howrah", "Siliguri",
    "Bhopal", "Indore", "Jabalpur", "Gwalior",
    "Chandigarh", "Ludhiana", "Amritsar", "Jalandhar",
    "Patna", "Ranchi", "Raipur", "Bhubaneswar", "Cuttack", "Guwahati", "Dehradun",
]
# Longest first so "New Delhi" and "Navi Mumbai" beat "Delhi" and "Mumbai".
_CITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\b{re.escape(city)}\b", re.IGNORECASE), city)
    for city in sorted(_CITIES, key=len, reverse=True)
]

# Localities distinctive enough to name their own city. Deliberately not exhaustive:
# every entry here is a municipal ruleset chosen on the user's behalf, so a name that
# could plausibly belong to two cities is worth less than the null it replaces.
# Ordinary words that happen to be locality names ("Satellite", "Sector 56") are out
# for the same reason.
_LOCALITIES: dict[str, str] = {
    # Bengaluru
    "Whitefield": "Bengaluru", "Koramangala": "Bengaluru", "Indiranagar": "Bengaluru",
    "Jayanagar": "Bengaluru", "HSR Layout": "Bengaluru", "Electronic City": "Bengaluru",
    "Yelahanka": "Bengaluru", "Marathahalli": "Bengaluru", "Sarjapur": "Bengaluru",
    "Hebbal": "Bengaluru", "Banashankari": "Bengaluru", "Rajajinagar": "Bengaluru",
    "Malleshwaram": "Bengaluru", "JP Nagar": "Bengaluru", "Bellandur": "Bengaluru",
    "Kengeri": "Bengaluru", "Devanahalli": "Bengaluru", "Bommanahalli": "Bengaluru",
    # Hyderabad
    "Kukatpally": "Hyderabad", "Gachibowli": "Hyderabad", "Madhapur": "Hyderabad",
    "Kondapur": "Hyderabad", "Miyapur": "Hyderabad", "Banjara Hills": "Hyderabad",
    "Jubilee Hills": "Hyderabad", "Shamshabad": "Hyderabad",
    # Pune
    "Kharadi": "Pune", "Hinjewadi": "Pune", "Baner": "Pune", "Wakad": "Pune",
    "Kothrud": "Pune", "Viman Nagar": "Pune", "Hadapsar": "Pune", "Aundh": "Pune",
    # Chennai
    "Velachery": "Chennai", "Anna Nagar": "Chennai", "Porur": "Chennai",
    "Sholinganallur": "Chennai", "Perungudi": "Chennai", "Thoraipakkam": "Chennai",
    # Mumbai
    "Andheri": "Mumbai", "Borivali": "Mumbai", "Powai": "Mumbai", "Chembur": "Mumbai",
    "Malad": "Mumbai", "Goregaon": "Mumbai", "Kandivali": "Mumbai",
    # Delhi
    "Dwarka": "Delhi", "Rohini": "Delhi", "Vasant Kunj": "Delhi",
    "Greater Kailash": "Delhi", "Pitampura": "Delhi",
    # Kochi
    "Kakkanad": "Kochi", "Edappally": "Kochi",
    # Kolkata
    "Salt Lake": "Kolkata", "New Town": "Kolkata", "Behala": "Kolkata",
}
_LOCALITY_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE), name, _LOCALITIES[name])
    for name in sorted(_LOCALITIES, key=len, reverse=True)
]


def parse(text: str) -> Brief:
    """Best-effort Brief from raw text. Never raises on ordinary input."""
    assumptions: list[Assumption] = []

    plot = _parse_plot(text, assumptions)
    locale = _parse_locale(text, assumptions)
    program = _parse_program(text, assumptions, plot=plot, locale=locale)
    vastu = _parse_vastu(text, assumptions)

    assumptions.append(
        _assume(
            "parser",
            "offline",
            "the model was unreachable",
            0.5,
        )
    )

    draft = BriefDraft(
        plot=plot,
        program=program,
        locale=locale,
        vastu=vastu,
        constraints=[],
        assumptions=assumptions,
    )
    return Brief.from_draft(draft, raw_text=text)


def _assume(field: str, value: str, reason: str, confidence: float) -> Assumption:
    """Short constructor — this module builds a dozen of these and the noise showed."""
    return Assumption(
        field=field, value=value, reason=reason[:120], confidence=confidence
    )


def _parse_plot(text: str, assumptions: list[Assumption]) -> PlotSpec:
    width_m, depth_m = _parse_dimensions(text, assumptions)
    return PlotSpec(
        width_m=width_m,
        depth_m=depth_m,
        road_edges=_parse_road_edges(text, assumptions),
        road_width_m=_road_width(text, assumptions),
    )


# Karnataka layout roads are typically formed at 30 or 40 ft. 9 m is the common 30 ft
# road, and it sits just under the 9.5 m threshold that caps a building at GF+1 — so
# defaulting here is not neutral, and the assumption says so.
_DEFAULT_ROAD_WIDTH_M = 9.0


def _road_width(text: str, assumptions: list[Assumption]) -> float:
    stated = _parse_road_width(text)
    if stated is not None:
        return stated
    assumptions.append(
        _assume(
            "road width",
            f"{_DEFAULT_ROAD_WIDTH_M:g} m (30 ft)",
            "not stated; the common layout road, and it caps height",
            0.5,
        )
    )
    return _DEFAULT_ROAD_WIDTH_M


# 90° clockwise, used to name a corner plot's second road when the text does not.
_BY_DEGREES = {f.degrees: f for f in Facing}


def _parse_road_edges(text: str, assumptions: list[Assumption]) -> list[Facing]:
    """Which sides carry a road, primary first.

    Three cases, and the middle one is the reason this is not just `_parse_facing`.
    """
    stated = _match_facing(text)
    corner = bool(re.search(r"\bcorner\b", text, re.IGNORECASE))

    # "North-east corner plot" names both roads outright — north *and* east. Reading
    # it as a north-east-pointing frontage and then stepping 90° round put the second
    # road on the south-east, a side the user never mentioned.
    if corner and stated in _COMPOUND_EDGES:
        edges = list(_COMPOUND_EDGES[stated])
        assumptions.append(
            _assume(
                "facing",
                f"road on the {_spoken(edges[0])} edge, primary of two",
                f"'{_spoken(stated)} corner' names a road on both edges",
                0.8,
            )
        )
        return edges

    if stated is None:
        assumptions.append(
            _assume(
                "facing",
                "north",
                "not stated; every Vastu score depends on it",
                0.2,
            )
        )
        primary = Facing.NORTH
    else:
        primary = stated
        assumptions.append(
            _assume(
                "facing",
                f"road on the {_spoken(primary)} edge",
                "the road edge, not the door's orientation",
                0.85,
            )
        )

    if not corner:
        return [primary]

    # A bare "corner plot" says a second road exists without saying which side, and
    # the two readings put the extra setback on opposite edges. Naming the adjacent
    # one at low confidence keeps the fact and lets the clarifier ask.
    second = _BY_DEGREES[(primary.degrees + 90) % 360]
    assumptions.append(
        _assume(
            "road edges",
            f"roads on the {_spoken(primary)} and {_spoken(second)} edges",
            "'corner' stated but not which second side",
            0.3,
        )
    )
    return [primary, second]


# What a compound direction names when the plot is a corner: the two cardinal sides
# it is built from, primary first.
_COMPOUND_EDGES = {
    Facing.NORTH_EAST: (Facing.NORTH, Facing.EAST),
    Facing.NORTH_WEST: (Facing.NORTH, Facing.WEST),
    Facing.SOUTH_EAST: (Facing.SOUTH, Facing.EAST),
    Facing.SOUTH_WEST: (Facing.SOUTH, Facing.WEST),
}


def _spoken(facing: Facing) -> str:
    return facing.value.replace("_", " ")


def _parse_dimensions(
    text: str, assumptions: list[Assumption]
) -> tuple[float, float]:
    """Sides in metres, from an explicit pair if present, else from an area."""
    match = _DIMENSION_RE.search(text)
    if match:
        # A trailing unit qualifies both numbers: "30 x 40 ft" is feet on both sides.
        unit_token = match.group("ub") or match.group("ua")
        unit = LengthUnit.FOOT
        if unit_token:
            from app.ir.units import parse_length_unit

            unit = parse_length_unit(unit_token) or LengthUnit.FOOT
        width_m = to_metres(float(match.group("a")), unit)
        depth_m = to_metres(float(match.group("b")), unit)

        if _plausible(width_m) and _plausible(depth_m):
            if unit is LengthUnit.FOOT and not unit_token:
                assumptions.append(
                    _assume(
                        "plot size",
                        f"{width_m:.2f} x {depth_m:.2f} m",
                        f"'{match.group('a')}x{match.group('b')}' is feet by "
                        "Indian convention",
                        0.9,
                    )
                )
            # Which number is the road edge is a separate guess from what unit they
            # are in, and a louder one: swapping them rotates the plot, moves the
            # front setback to the long side and re-scores every Vastu sector.
            assumptions.append(
                _assume(
                    "frontage",
                    f"{match.group('a')} {unit.value} frontage, "
                    f"{match.group('b')} {unit.value} deep",
                    "the first number is the road edge by convention",
                    0.85,
                )
            )
            return width_m, depth_m
        assumptions.append(
            _assume(
                "plot size",
                f"'{match.group(0).strip()}' ignored",
                f"converts to {width_m:.1f} m x {depth_m:.1f} m, outside any "
                "plausible plot size",
                0.6,
            )
        )

    area_match = _AREA_RE.search(text)
    if area_match:
        from app.ir.units import parse_area_unit

        unit = parse_area_unit(area_match.group("unit")) or AreaUnit.SQ_FOOT
        area_sq_m = area_to_sq_m(float(area_match.group("value")), unit)
        width_m = math.sqrt(area_sq_m * _FRONTAGE_TO_DEPTH)
        depth_m = area_sq_m / width_m
        if _plausible(width_m) and _plausible(depth_m):
            assumptions.append(
                _assume(
                    "plot size",
                    f"{width_m:.2f} x {depth_m:.2f} m",
                    f"'{area_match.group(0).strip()}' at a 2:3 frontage ratio; "
                    "the text gave area only",
                    0.6,
                )
            )
            return width_m, depth_m
        assumptions.append(
            _assume(
                "plot size",
                f"'{area_match.group(0).strip()}' ignored",
                "implies a plot outside any plausible size",
                0.6,
            )
        )

    assumptions.append(
        _assume(
            "plot size",
            f"{_DEFAULT_WIDTH_M:.2f} x {_DEFAULT_DEPTH_M:.2f} m",
            "nothing in the text; 30x40 ft is the commonest site",
            0.2,
        )
    )
    return _DEFAULT_WIDTH_M, _DEFAULT_DEPTH_M


def _plausible(side_m: float) -> bool:
    """Mirrors the PlotSpec bounds so we can reject a reading before it raises."""
    return 2.0 < side_m < 200.0


def _match_facing(text: str) -> Facing | None:
    """The direction the text names, or None. Pure — the caller decides what it means,
    because "north east" reads differently on a corner plot than on an ordinary one."""
    for pattern, facing in _FACING_PATTERNS:
        if pattern.search(text):
            return facing
    return None


def _parse_road_width(text: str) -> float | None:
    match = _ROAD_RE.search(text)
    if not match:
        return None
    from app.ir.units import parse_length_unit

    unit = parse_length_unit(match.group("unit") or "ft") or LengthUnit.FOOT
    width_m = to_metres(float(match.group("value")), unit)
    return width_m if 1.0 < width_m < 60.0 else None


def _parse_program(
    text: str,
    assumptions: list[Assumption],
    *,
    plot: PlotSpec,
    locale: Locale,
) -> ProgramHints:
    # Confidences here are *local*: how sure this reading is given its inputs. The
    # ceiling that makes them honest about their inputs — bathrooms cannot outrank the
    # bedroom guess it came from — is applied once, from the DAG, in
    # `intent.cap_derived_confidence`. Doing it here missed `plot size` entirely.
    bedrooms = _parse_count(_BHK_RE, text)
    if bedrooms is None:
        bedrooms = 2
        assumptions.append(_assume("bedrooms", "2BHK", "not stated", 0.4))
    bedrooms = min(bedrooms, 8)

    bathrooms = _parse_count(_BATHROOM_RE, text)
    if bathrooms is None:
        bathrooms = _default_bathrooms(bedrooms)
        assumptions.append(
            _assume(
                "bathrooms",
                "1" if bathrooms == 1 else f"{bathrooms}, one en-suite",
                f"standard for {bedrooms}BHK",
                0.8,
            )
        )
    bathrooms = min(bathrooms, 8)

    occupants = _default_occupants(bedrooms)
    assumptions.append(
        _assume(
            "occupants", str(occupants), f"median for {bedrooms}BHK", 0.7
        )
    )

    bays, covered = _parse_parking(text, assumptions, locale=locale)
    extra_rooms = [kind for pattern, kind in _ROOM_PATTERNS if pattern.search(text)]
    if bays == 0:
        # "no parking" matches the car-parking room pattern on the word it is
        # declining. Listing the room and building zero bays is the same sentence
        # read two ways, and the explicit decline is the one that meant it.
        extra_rooms = [k for k in extra_rooms if k is not RoomKind.CAR_PARKING]
    floors = _parse_floors(text, assumptions, plot=plot, bedrooms=bedrooms)

    return ProgramHints(
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        extra_rooms=extra_rooms,
        floors=floors,
        occupants=occupants,
        parking_bays=bays,
        covered_parking=covered,
    )


def _default_bathrooms(bedrooms: int) -> int:
    """What a builder pencils in before anyone asks: one per two bedrooms, floored at
    two from 2BHK up, since a house with guests needs a second WC regardless."""
    if bedrooms <= 1:
        return 1
    return 2 if bedrooms <= 3 else 3


def _default_occupants(bedrooms: int) -> int:
    """Median Indian household for the bedroom count. Stage ③ sizes sanitary
    provision from it, so a plausible number beats a null it would have to invent."""
    return {1: 2, 2: 3, 3: 4}.get(bedrooms, 5)


# "no parking", "without car porch" — a decline, not silence.
_NO_PARKING_RE = re.compile(
    r"\b(?:no|without|skip|don'?t\s+(?:need|want))\s+(?:a\s+)?"
    r"(?:car\s*)?(?:park(?:ing)?|porch|garage)\b",
    re.IGNORECASE,
)


def _parse_parking(
    text: str, assumptions: list[Assumption], *, locale: Locale
) -> tuple[int, bool | None]:
    if _NO_PARKING_RE.search(text):
        return 0, None

    stated = any(
        pattern.search(text) for pattern, kind in _ROOM_PATTERNS if kind is RoomKind.CAR_PARKING
    )
    where = f"{locale.city} plot" if locale.city else "an Indian city plot"
    assumptions.append(
        _assume(
            "parking",
            "1 covered bay",
            "count not stated" if stated else f"default for {where}",
            0.75 if stated else 0.8,
        )
    )
    return 1, True


def _parse_count(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    if not match:
        return None
    token = match.group("n").lower()
    if token.isdigit():
        return max(1, int(token))
    return _WORD_NUMBERS.get(token)


def _parse_floors(
    text: str,
    assumptions: list[Assumption],
    *,
    plot: PlotSpec,
    bedrooms: int,
) -> int:
    match = _FLOORS_RE.search(text)
    if not match:
        # Roughly 25 m² of plot per bedroom is the point below which a single storey
        # stops holding the programme once setbacks are taken off.
        roomy = plot.area_sq_m >= 25.0 * bedrooms
        assumptions.append(
            _assume(
                "floors",
                "ground only",
                f"{plot.area_sq_m:.0f} m² fits a {bedrooms}BHK flat"
                if roomy
                else f"{plot.area_sq_m:.0f} m² is tight for a {bedrooms}BHK",
                0.9 if roomy else 0.5,
            )
        )
        return 1
    if match.group("gplus"):
        # "G+1" is ground plus one upper floor — two floors in total.
        return min(int(match.group("gplus")) + 1, 4)
    token = (match.group("n") or "").lower()
    value = int(token) if token.isdigit() else _WORD_NUMBERS.get(token, 1)
    return min(max(value, 1), 4)


def _parse_locale(text: str, assumptions: list[Assumption]) -> Locale:
    locality = next(
        (name for pattern, name, _ in _LOCALITY_PATTERNS if pattern.search(text)), None
    )
    stated_city = next(
        (city for pattern, city in _CITY_PATTERNS if pattern.search(text)), None
    )

    if stated_city:
        return Locale(city=stated_city, locality=locality)

    if locality:
        # The locality names the city, and stage ② needs a city to pick a ruleset at
        # all. High confidence, but recorded — the user is the one who knows whether
        # they meant the Whitefield near Bengaluru.
        city = _LOCALITIES[locality]
        assumptions.append(
            _assume(
                "city",
                city,
                f"{locality} is a {city} locality → {_AUTHORITY.get(city, 'municipal')}"
                " setbacks",
                0.95,
            )
        )
        return Locale(city=city, locality=locality)

    assumptions.append(
        _assume(
            "city",
            "left blank",
            "no city or locality recognised in the text",
            0.1,
        )
    )
    return Locale()


# The body whose bye-laws stage ② will look up. Named in the assumption because
# "Bengaluru" and "BBMP setbacks" are the same fact to us and different facts to the
# user — the second is the one they can check against their own plan.
_AUTHORITY = {
    "Bengaluru": "BBMP",
    "Bangalore": "BBMP",
    "Hyderabad": "GHMC",
    "Pune": "PMC",
    "Chennai": "CMDA",
    "Mumbai": "MCGM",
    "Delhi": "MCD",
    "Kochi": "GCDA",
    "Kolkata": "KMC",
}


# All four spellings in common use: vastu, vaastu, vasthu, vaasthu.
_VASTU = r"va+sth?u"


# Checked before the strict patterns, because "vastu is not important" contains
# "important" and would otherwise be read as the opposite of what it says.
_VASTU_DECLINED = (
    rf"\b(?:no|without|ignore|skip)\s+{_VASTU}\b"
    rf"|\bdon'?t\s+care\s+about\s+{_VASTU}\b"
    rf"|\b{_VASTU}\b[^.]{{0,20}}\b(?:not|isn'?t)\s+(?:very\s+)?"
    rf"(?:important|a\s+priority|required|needed)\b"
)

# "Vastu is very important" is how people actually say it. Requiring the word
# "strict" read that as a soft preference and silently downgraded the brief — the
# system overriding the person, which is the failure the spelling test guards too.
_VASTU_INSISTED = (
    rf"\b(?:strict(?:ly)?|mandatory|must|fully?|compliant)\b[^.]{{0,24}}\b{_VASTU}\b"
    rf"|\b{_VASTU}\b[^.]{{0,24}}\b(?:strict(?:ly)?|mandatory|must|compliant"
    rf"|very\s+important|important|critical|essential|a\s+priority|non[\s\-]?negotiable)\b"
)


def _parse_vastu(text: str, assumptions: list[Assumption]) -> VastuStance:
    if re.search(_VASTU_DECLINED, text, re.IGNORECASE):
        return VastuStance.IGNORE
    if re.search(_VASTU_INSISTED, text, re.IGNORECASE):
        return VastuStance.STRICT

    # Silence lands on `moderate`, which is a choice we made and not one they made —
    # so it is recorded. Asking for a pooja room is the strongest signal short of
    # saying the word that they expect the sectors to be respected.
    if re.search(rf"\b{_VASTU}\b", text, re.IGNORECASE):
        reason = "vastu mentioned without a strength"
    elif any(
        pattern.search(text) for pattern, kind in _ROOM_PATTERNS if kind is RoomKind.POOJA
    ):
        reason = "pooja room requested"
    else:
        reason = "not mentioned; kept soft"
    assumptions.append(_assume("vastu", "moderate", reason, 0.75))
    return VastuStance.MODERATE
