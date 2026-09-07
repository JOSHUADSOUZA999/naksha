"""Unit conversion is where a silent bug does the most damage.

A metre/foot mixup does not crash — it produces a plan that is 3.3x too big and looks
entirely reasonable until someone tries to build it. Property tests, per CLAUDE.md,
because example-based tests miss exactly this class of drift.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.ir.units import (
    AreaUnit,
    LengthUnit,
    area_from_sq_m,
    area_to_sq_m,
    from_metres,
    parse_area_unit,
    parse_length_unit,
    to_metres,
)

finite_positive = st.floats(
    min_value=1e-3, max_value=1e6, allow_nan=False, allow_infinity=False
)


def test_known_conversions_are_exact():
    # The 1959 international agreement fixes these exactly; a "close enough" value
    # here would drift into every dimension chain downstream.
    assert to_metres(30, LengthUnit.FOOT) == pytest.approx(9.144, abs=1e-9)
    assert to_metres(40, LengthUnit.FOOT) == pytest.approx(12.192, abs=1e-9)
    assert to_metres(1, LengthUnit.YARD) == pytest.approx(0.9144, abs=1e-9)
    assert to_metres(15, LengthUnit.METRE) == 15.0


def test_known_area_conversions():
    assert area_to_sq_m(1200, AreaUnit.SQ_FOOT) == pytest.approx(111.484, abs=1e-3)
    assert area_to_sq_m(200, AreaUnit.SQ_YARD) == pytest.approx(167.225, abs=1e-3)
    assert area_to_sq_m(4, AreaUnit.CENT) == pytest.approx(161.874, abs=1e-3)


@given(value=finite_positive, unit=st.sampled_from(list(LengthUnit)))
def test_length_round_trip(value: float, unit: LengthUnit):
    assert from_metres(to_metres(value, unit), unit) == pytest.approx(value, rel=1e-12)


@given(value=finite_positive, unit=st.sampled_from(list(AreaUnit)))
def test_area_round_trip(value: float, unit: AreaUnit):
    assert area_from_sq_m(area_to_sq_m(value, unit), unit) == pytest.approx(
        value, rel=1e-12
    )


@given(value=finite_positive)
def test_length_conversion_is_monotonic_and_scale_free(value: float):
    """Doubling the input doubles the output — catches an accidental offset."""
    assert to_metres(2 * value, LengthUnit.FOOT) == pytest.approx(
        2 * to_metres(value, LengthUnit.FOOT), rel=1e-12
    )


@given(side=st.floats(min_value=1.0, max_value=500.0, allow_nan=False))
def test_area_and_length_units_agree(side: float):
    """A square measured in feet and in sq ft must land on the same square metres.

    This is the invariant that actually protects us: `units.py` has two independent
    conversion tables, and nothing else would notice if they disagreed.
    """
    via_length = to_metres(side, LengthUnit.FOOT) ** 2
    via_area = area_to_sq_m(side**2, AreaUnit.SQ_FOOT)
    assert via_length == pytest.approx(via_area, rel=1e-9)


@pytest.mark.parametrize(
    "token,expected",
    [
        ("ft", LengthUnit.FOOT),
        ("Feet", LengthUnit.FOOT),
        ("'", LengthUnit.FOOT),
        ("mtr", LengthUnit.METRE),
        ("METRES", LengthUnit.METRE),
        ("yd", LengthUnit.YARD),
    ],
)
def test_length_aliases(token: str, expected: LengthUnit):
    assert parse_length_unit(token) is expected


@pytest.mark.parametrize(
    "token,expected",
    [
        ("sqft", AreaUnit.SQ_FOOT),
        ("Sq Ft", AreaUnit.SQ_FOOT),
        ("square feet", AreaUnit.SQ_FOOT),
        ("gaj", AreaUnit.SQ_YARD),
        ("cents", AreaUnit.CENT),
    ],
)
def test_area_aliases(token: str, expected: AreaUnit):
    assert parse_area_unit(token) is expected


def test_gaj_is_area_not_length():
    """'gaj' is a real ambiguity: square yards as an area, yards as a side.

    Both tables claim the token, which is fine as long as callers check the area
    table first. This test exists so nobody "fixes" the duplication by deleting one.
    """
    assert parse_area_unit("gaj") is AreaUnit.SQ_YARD
    assert parse_length_unit("gaj") is LengthUnit.YARD


def test_unknown_unit_raises():
    with pytest.raises(ValueError, match="unknown length unit"):
        to_metres(1, "furlong")
    with pytest.raises(ValueError, match="unknown area unit"):
        area_to_sq_m(1, "acre")


def test_no_silent_rounding():
    """30 ft is 9.144 m, not 9.1 or 9.

    A "nicer" number here compounds through setbacks, wall centrelines and carpet
    area until the exported DXF is visibly wrong.
    """
    assert not math.isclose(to_metres(30, LengthUnit.FOOT), 9.1, abs_tol=1e-6)
    assert to_metres(30, LengthUnit.FOOT) == pytest.approx(9.144, abs=1e-12)
