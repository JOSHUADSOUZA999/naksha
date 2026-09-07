"""Golden set against the real model.

Marked `live` and deselected by default (`addopts = -m 'not live'`), so CI never needs
credentials. Run deliberately after a prompt or model change:

    pytest -m live

The assertions are looser than the deterministic golden test in `test_fallback.py`.
That is the point: a model may legitimately read more out of a sentence than a regex
can, so this checks the things that must not vary — units, facing, bedroom count —
rather than pinning an exact Brief. Pinning exact output against a non-deterministic
model produces a suite everyone learns to ignore.
"""

from __future__ import annotations

import pytest

from app.llm.intent import extract_brief

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def results(golden_cases):
    return {case["id"]: (case, extract_brief(case["text"])) for case in golden_cases}


def test_model_answered_every_case(results):
    """If this fails with fallback_used, the credentials or the API are the problem."""
    fell_back = {
        cid: result.provenance.fallback_reason
        for cid, (_, result) in results.items()
        if result.provenance.fallback_used
    }
    assert not fell_back, f"fell back to the offline parser: {fell_back}"


def test_dimensions_are_metres_within_tolerance(results):
    """The single most damaging failure: feet left unconverted.

    Tolerance is generous (±0.5 m) because deriving sides from an area is a judgment
    call, but a foot/metre mixup is off by 3.3x and cannot hide inside it.
    """
    for cid, (case, result) in results.items():
        want = case["expected"]["plot"]
        assert result.brief.plot.width_m == pytest.approx(want["width_m"], abs=0.5), cid
        assert result.brief.plot.depth_m == pytest.approx(want["depth_m"], abs=0.5), cid


def test_facing_matches_when_stated(results):
    for cid, (case, result) in results.items():
        if "facing" in case.get("must_assume", []):
            continue
        assert result.brief.plot.facing.value == case["expected"]["plot"]["facing"], cid


def test_bedroom_count_is_exact_when_the_text_states_one(results):
    """Not a judgment call — '3BHK' means 3, and getting it wrong resizes the plan.

    Unless the text describes a household instead of a count, in which case reading it
    is the job and `model_expected` carries the floor instead.
    """
    for cid, (case, result) in results.items():
        floors = case.get("model_expected", {}).get("program", {})
        if "bedrooms_min" in floors:
            continue
        assert result.brief.program.bedrooms == case["expected"]["program"]["bedrooms"], cid


def test_model_reads_what_the_regex_cannot(results):
    """The reason a live set exists at all.

    `expected` is the deterministic floor — what a regex can defend. `model_expected`
    is the part only a reader gets: "joint family" is a programme, not a phrase. A
    model that scores identically to the fallback here has understood nothing, and
    that is a regression worth failing over even though both answers validate.
    """
    for cid, (case, result) in results.items():
        want = case.get("model_expected", {}).get("program", {})
        program = result.brief.program
        for field, floor in want.items():
            if not field.endswith("_min"):
                continue
            got = getattr(program, field.removesuffix("_min"))
            assert got is not None and got >= floor, (
                f"{cid}: {field.removesuffix('_min')} was {got}, needs >= {floor}"
            )


def test_constraints_reach_stage_three_verbatim(results):
    """Some requirements have no field and must not be dropped inventing one.

    "my mother lives with us" is an adjacency — a ground-floor bedroom near the entry
    — which stage ① has no schema for. Paraphrasing it into a room is worse than
    passing it through, so the contract is that the words survive.
    """
    for cid, (case, result) in results.items():
        blob = " ".join(result.brief.constraints).lower()
        for phrase in case.get("must_keep", []):
            assert phrase.lower() in blob, f"{cid}: dropped {phrase!r} from constraints"


def test_vastu_stance_matches(results):
    for cid, (case, result) in results.items():
        assert result.brief.vastu.value == case["expected"]["vastu"], cid


# Both spellings are in daily use and either is a correct answer. Only the renamed
# cities need this — a model writing "Bangalore" has not made a mistake.
_CITY_ALIASES = {
    "bengaluru": {"bangalore"}, "bangalore": {"bengaluru"},
    "mysuru": {"mysore"}, "mysore": {"mysuru"},
    "kochi": {"cochin"}, "cochin": {"kochi"},
    "gurugram": {"gurgaon"}, "gurgaon": {"gurugram"},
}


def test_city_is_right_or_absent(results):
    """A wrong city silently selects the wrong setback ruleset.

    Inferring one from a locality is allowed — "Whitefield" names Bengaluru as surely
    as the word does. What is not allowed is producing a city when nothing in the text
    implies one, which is what the null-expecting cases here pin down.
    """
    for cid, (case, result) in results.items():
        expected_city = case["expected"]["locale"]["city"]
        got = result.brief.locale.city
        if expected_city is None:
            assert got is None, f"{cid}: invented city {got!r}"
            continue
        want = expected_city.lower()
        assert got, f"{cid}: no city"
        accepted = {want} | _CITY_ALIASES.get(want, set())
        assert any(name in got.lower() for name in accepted), f"{cid}: got {got!r}"


def test_named_rooms_are_all_captured(results):
    """Extra rooms the model adds are allowed; dropping a requested one is not."""
    for cid, (case, result) in results.items():
        want = set(case["expected"]["program"]["extra_rooms"])
        got = {room.value for room in result.brief.program.extra_rooms}
        assert want <= got, f"{cid}: dropped {want - got}"


def test_inferences_are_disclosed(results):
    """The explainability contract: anything guessed has to be stated."""
    for cid, (case, result) in results.items():
        for topic in case.get("must_assume", []):
            blob = " ".join(
                f"{a.field} {a.value} {a.reason}" for a in result.brief.assumptions
            ).lower()
            assert topic.split()[0] in blob, f"{cid}: no assumption recorded for {topic}"


def test_raw_text_survives_verbatim(results):
    for cid, (case, result) in results.items():
        assert result.brief.raw_text == case["text"], cid
