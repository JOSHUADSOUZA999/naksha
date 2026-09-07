"""The single place a non-metre number becomes metres.

The IR is metres throughout, but nobody in India describes a plot that way: sites are
sold as "30x40" (feet), "1200 sqft", "200 gaj", or "4 cents". Those conversions happen
here and nowhere else. A stray `* 0.3048` in a parser is a bug, because the moment two
modules each own a conversion they drift and the drift is invisible.
"""

from __future__ import annotations

from enum import StrEnum

# Exact by definition (international yard/foot agreement, 1959).
_METRES_PER_FOOT = 0.3048
_METRES_PER_YARD = 0.9144
_SQ_M_PER_SQ_FOOT = _METRES_PER_FOOT**2
_SQ_M_PER_SQ_YARD = _METRES_PER_YARD**2
# 1 cent = 1/100 acre. Standard in Karnataka, Kerala and Tamil Nadu listings.
_SQ_M_PER_CENT = 40.4685642


class LengthUnit(StrEnum):
    METRE = "m"
    FOOT = "ft"
    YARD = "yd"


class AreaUnit(StrEnum):
    SQ_METRE = "sq_m"
    SQ_FOOT = "sq_ft"
    SQ_YARD = "sq_yd"  # "gaj" in north-Indian listings
    CENT = "cent"


_LENGTH_TO_M: dict[LengthUnit, float] = {
    LengthUnit.METRE: 1.0,
    LengthUnit.FOOT: _METRES_PER_FOOT,
    LengthUnit.YARD: _METRES_PER_YARD,
}

_AREA_TO_SQ_M: dict[AreaUnit, float] = {
    AreaUnit.SQ_METRE: 1.0,
    AreaUnit.SQ_FOOT: _SQ_M_PER_SQ_FOOT,
    AreaUnit.SQ_YARD: _SQ_M_PER_SQ_YARD,
    AreaUnit.CENT: _SQ_M_PER_CENT,
}

# Spellings seen in real listings and in the way people type. Mapped to the canonical
# unit so a parser never has to carry its own synonym table.
_LENGTH_ALIASES: dict[str, LengthUnit] = {
    "m": LengthUnit.METRE,
    "mt": LengthUnit.METRE,
    "mtr": LengthUnit.METRE,
    "meter": LengthUnit.METRE,
    "meters": LengthUnit.METRE,
    "metre": LengthUnit.METRE,
    "metres": LengthUnit.METRE,
    "ft": LengthUnit.FOOT,
    "feet": LengthUnit.FOOT,
    "foot": LengthUnit.FOOT,
    "'": LengthUnit.FOOT,
    "yd": LengthUnit.YARD,
    "yard": LengthUnit.YARD,
    "yards": LengthUnit.YARD,
    "gaj": LengthUnit.YARD,
}

_AREA_ALIASES: dict[str, AreaUnit] = {
    "sqm": AreaUnit.SQ_METRE,
    "sq m": AreaUnit.SQ_METRE,
    "sqmt": AreaUnit.SQ_METRE,
    "sq metre": AreaUnit.SQ_METRE,
    "sq meter": AreaUnit.SQ_METRE,
    "square metre": AreaUnit.SQ_METRE,
    "square meter": AreaUnit.SQ_METRE,
    "sqft": AreaUnit.SQ_FOOT,
    "sq ft": AreaUnit.SQ_FOOT,
    "sq feet": AreaUnit.SQ_FOOT,
    "sft": AreaUnit.SQ_FOOT,
    "square feet": AreaUnit.SQ_FOOT,
    "square foot": AreaUnit.SQ_FOOT,
    "sqyd": AreaUnit.SQ_YARD,
    "sq yd": AreaUnit.SQ_YARD,
    "sq yard": AreaUnit.SQ_YARD,
    "square yard": AreaUnit.SQ_YARD,
    "gaj": AreaUnit.SQ_YARD,
    "cent": AreaUnit.CENT,
    "cents": AreaUnit.CENT,
}


def to_metres(value: float, unit: LengthUnit | str) -> float:
    """Convert a length to metres."""
    return value * _LENGTH_TO_M[_coerce_length(unit)]


def from_metres(value_m: float, unit: LengthUnit | str) -> float:
    """Convert metres back out to `unit`. Exists so round-trips are testable."""
    return value_m / _LENGTH_TO_M[_coerce_length(unit)]


def area_to_sq_m(value: float, unit: AreaUnit | str) -> float:
    """Convert an area to square metres."""
    return value * _AREA_TO_SQ_M[_coerce_area(unit)]


def area_from_sq_m(value_sq_m: float, unit: AreaUnit | str) -> float:
    """Convert square metres back out to `unit`."""
    return value_sq_m / _AREA_TO_SQ_M[_coerce_area(unit)]


def parse_length_unit(token: str) -> LengthUnit | None:
    """Resolve a user-typed unit token, or None if it isn't a length unit."""
    return _LENGTH_ALIASES.get(token.strip().lower().rstrip("."))


def parse_area_unit(token: str) -> AreaUnit | None:
    """Resolve a user-typed unit token, or None if it isn't an area unit.

    Checked before `parse_length_unit` by callers, because "gaj" is ambiguous: it
    means square yards when it qualifies an area and yards when it qualifies a side.
    """
    return _AREA_ALIASES.get(" ".join(token.strip().lower().rstrip(".").split()))


def _coerce_length(unit: LengthUnit | str) -> LengthUnit:
    if isinstance(unit, LengthUnit):
        return unit
    resolved = parse_length_unit(unit)
    if resolved is None:
        raise ValueError(f"unknown length unit: {unit!r}")
    return resolved


def _coerce_area(unit: AreaUnit | str) -> AreaUnit:
    if isinstance(unit, AreaUnit):
        return unit
    resolved = parse_area_unit(unit)
    if resolved is None:
        raise ValueError(f"unknown area unit: {unit!r}")
    return resolved
