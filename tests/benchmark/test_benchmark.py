"""The 14-plan benchmark as tests. Deselected by default; run it with `pytest -m benchmark`.

Two tests that answer different questions and are meant to fail differently. **No plan
gets worse** guards the counts a person argues with — errors, warnings, Vastu zones, air,
ventilation — and should pass through every change. **The plans are unchanged** fails on
any change to the generated geometry, including one that was intended: that failure is
the moment to read the metrics and re-baseline deliberately, not a bug to silence.
"""

from __future__ import annotations

import pytest

from benchmark_plans import BASELINE, load_baseline, regressions, run_all

pytestmark = pytest.mark.benchmark


@pytest.fixture(scope="module")
def baseline():
    assert BASELINE.exists(), "no baseline: run tests/benchmark/benchmark_plans.py --write"
    return load_baseline()


@pytest.fixture(scope="module")
def results():
    return {result.id: result for result in run_all()}


def test_the_same_cases_are_measured(results, baseline):
    assert set(results) == set(baseline)


def test_no_plan_gets_worse(results, baseline):
    worse = {
        case: why
        for case, result in results.items()
        if case in baseline and (why := regressions(result, baseline[case]))
    }
    assert not worse, "\n".join(f"{case}: {'; '.join(why)}" for case, why in worse.items())


def test_the_plans_are_unchanged(results, baseline):
    changed = sorted(
        case for case, result in results.items()
        if case in baseline and result.geometry != baseline[case]["geometry"]
    )
    assert not changed, (
        f"generated plans changed: {', '.join(changed)}. If the change is intended, read "
        "the metrics with `.venv/bin/python tests/benchmark/benchmark_plans.py` and "
        "re-baseline with --write."
    )
