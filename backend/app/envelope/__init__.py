"""Stage ② ENVELOPE — `Brief` to the rectangle you may build inside.

Deterministic: no model, no network, no key. The judgment lives in `rules/`, which is
versioned data, and this module is the arithmetic that applies it.

Four ways it declines to answer, all of them deliberate. There is no ruleset for the
city; the ruleset is still unverified; the city was never given; or the margins leave
no rectangle. Each returns a typed error carrying the numbers, because "no envelope"
is a finding stage ④ acts on, not a failure to paper over with a default. A
neighbouring city's bye-laws are not a conservative substitute — they are simply a
different answer.
"""

from __future__ import annotations

from typing import Any

from app.ir.envelope import Envelope
from app.ir.models import Brief
from app.rules import load_ruleset

from . import geometry

ENVELOPE_RULES = "setbacks_v1"


class EnvelopeError(Exception):
    """Stage ② could not produce a buildable rectangle."""


class CityUnknown(EnvelopeError):
    """No city on the Brief, so no bye-laws to apply.

    Why `city` is tiered `blocking` in the clarifier: this is the wall it prevents.
    """


class NoRulesetForCity(EnvelopeError):
    def __init__(self, city: str, known: list[str]) -> None:
        super().__init__(
            f"no setback ruleset for {city!r}; have {', '.join(sorted(known)) or 'none'}"
        )
        self.city = city


class RulesetUnverified(EnvelopeError):
    """The figures have not been checked against the authority's own document.

    Refused by default because the output is a buildable envelope — a number someone
    acts on — and a wrong setback looks exactly like a right one.
    """

    def __init__(self, version: str, bands: list[str]) -> None:
        super().__init__(
            f"{version} has {len(bands)} unverified band(s): {', '.join(bands[:4])}"
            f"{', …' if len(bands) > 4 else ''}. "
            "Verify against the bye-laws, or pass allow_unverified=True to proceed anyway."
        )
        self.bands = bands


class EnvelopeInfeasible(EnvelopeError):
    """The margins consume the plot. A real answer about a real site, not a bug."""

    def __init__(self, *, east_west_m: float, north_south_m: float, setbacks: dict) -> None:
        margins = ", ".join(f"{e.value} {v}m" for e, v in setbacks.items())
        super().__init__(
            f"setbacks leave no buildable area on a {east_west_m:.2f} x "
            f"{north_south_m:.2f} m site ({margins})"
        )
        self.setbacks = setbacks


def build_envelope(
    brief: Brief,
    *,
    rules_version: str = ENVELOPE_RULES,
    allow_unverified: bool = False,
) -> Envelope:
    """The buildable rectangle for this Brief, under its city's bye-laws."""
    rules = load_ruleset(rules_version)
    if rules.unverified and not allow_unverified:
        raise RulesetUnverified(rules.version, rules.unverified)

    authority_name, authority = _authority_for(brief, rules.data)
    plot = brief.plot
    area = plot.area_sq_m

    road_width_m = plot.road_width_m or authority["default_road_width_m"]
    setback_band = _setback_band_for(plot, authority["setback_bands"])
    coverage_band = _band_for(area, authority["coverage_far_bands"])
    far_band = _demote_for_road(coverage_band, road_width_m, authority["coverage_far_bands"])
    height_band = _height_band_for(road_width_m, authority["height_bands"])

    setbacks = geometry.assign_setbacks(
        plot.road_edges, **_resolve(setback_band, plot)
    )
    x_min, y_min, x_max, y_max = geometry.buildable_rect(plot, setbacks)
    if x_max <= x_min or y_max <= y_min:
        east_west, north_south = geometry.plot_extent(plot)
        raise EnvelopeInfeasible(
            east_west_m=east_west, north_south_m=north_south, setbacks=setbacks
        )

    return Envelope(
        x_min_m=x_min,
        y_min_m=y_min,
        x_max_m=x_max,
        y_max_m=y_max,
        setbacks=setbacks,
        plot_area_sq_m=area,
        max_coverage=coverage_band["coverage"],
        # `base_far`, not `total_far`: the excess requires buying TDR, which a plot
        # owner building one house will not have done. Budgeting against the higher
        # number would size a programme that cannot legally be built.
        max_far=far_band["base_far"],
        max_floors=height_band["max_floors"],
        road_width_m=road_width_m,
        ruleset=rules.stamp,
        authority=authority_name,
    )


def _authority_for(brief: Brief, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    city = brief.locale.city
    if not city:
        raise CityUnknown("the Brief names no city, so no bye-laws apply")
    mapping = data["city_to_authority"]
    name = mapping.get(city)
    if name is None:
        raise NoRulesetForCity(city, list(mapping))
    return name, data["authorities"][name]


def _demote_for_road(
    band: dict[str, Any], road_width_m: float, bands: list[dict[str, Any]]
) -> dict[str, Any]:
    """RMP 2031 §5.2(iii): a road narrower than the band expects demotes the FAR.

    Verbatim: *"If the road width is lower than the road width for a particular site
    size, the FAR of the lower road width shall be applicable."* So plot size picks
    the row, and an under-width road drops you to the widest row the road does
    satisfy. Only the FAR moves — coverage stays with the plot-size row.

    This is why road width is not a cosmetic field: a 400 m² plot on a 9 m road earns
    the FAR of a 120 m² one.
    """
    if road_width_m >= band["min_road_m"]:
        return band
    eligible = [b for b in bands if road_width_m >= b["min_road_m"]]
    return eligible[-1] if eligible else bands[0]


def _height_band_for(road_width_m: float, bands: list[dict[str, Any]]) -> dict[str, Any]:
    """RMP 2031 §5.2(iv): the road caps the storeys whatever the FAR allows."""
    for band in bands:
        ceiling = band.get("max_road_m")
        if ceiling is None or road_width_m < ceiling:
            return band
    return bands[-1]


def _setback_band_for(plot, bands: list[dict[str, Any]]) -> dict[str, Any]:
    """RMP-2015 Table 8 bands on the site's own dimensions, not on its area.

    The smaller of width and depth selects the row, so a long thin plot is treated as
    the narrow site it is rather than averaged into a larger band by its area.
    """
    smaller = min(plot.width_m, plot.depth_m)
    for band in bands:
        ceiling = band.get("max_side_m")
        if ceiling is None or smaller <= ceiling:
            return band
    raise EnvelopeError(f"no setback band covers a {smaller:.1f} m site dimension")


def _resolve(band: dict[str, Any], plot) -> dict[str, Any]:
    """Fixed metres below 9 m, percentages of the site's dimensions above it.

    Which dimension each percentage applies to is the one thing Table 8 does not spell
    out. The reading here — sides off the width, front and rear off the depth — is
    recorded as unconfirmed in the ruleset and asked in VERIFY.md Q3, because getting
    it backwards on a long thin plot swaps two very different numbers.
    """
    if "front_m" in band:
        return {
            "front": band["front_m"],
            "rear": band["rear_m"],
            "sides": list(band["sides_m"]),
        }
    return {
        "front": plot.depth_m * band["front_pct_of_depth"] / 100,
        "rear": plot.depth_m * band["rear_pct_of_depth"] / 100,
        "sides": [plot.width_m * pct / 100 for pct in band["sides_pct_of_width"]],
    }


def _band_for(area_sq_m: float, bands: list[dict[str, Any]]) -> dict[str, Any]:
    """First band whose ceiling the plot fits under.

    `max_plot_sq_m` is an **inclusive** upper bound, so a plot of exactly 90 m² takes
    the "up to 90" row. A trailing `null` ceiling is the unbounded top band; a table
    without one is malformed, and falling off the end would silently apply the
    smallest margins to the largest plot.
    """
    for band in bands:
        ceiling = band.get("max_plot_sq_m")
        if ceiling is None or area_sq_m <= ceiling:
            return band
    raise EnvelopeError(f"no band covers {area_sq_m:.1f} m² — table lacks an open top band")
