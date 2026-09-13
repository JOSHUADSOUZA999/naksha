"""Stage ③ through a model, replayed offline from answers the model actually gave.

`tests/golden/program_drafts.json` holds real `ProgramDraft`s recorded from claude-opus-5
through the `claude_code` provider. Replaying them through a scripted provider tests the
merge on the shapes a model really produces — ids like `mbed` and `corr2`, its own floors
and sectors, a ground-floor bedroom for a joint family — with no network and no key.
Nothing ran this path offline before, which is how every defect below reached a drawing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app.envelope import build_envelope
from app.ir.enums import SpaceKind
from app.ir.plan import ProgramDraft
from app.llm import fallback
from app.llm.program import build_program
from app.program import PorchDoesNotFit, expand, spec_for

_FIXTURE = json.loads(
    (Path(__file__).parent / "golden" / "program_drafts.json").read_text(encoding="utf-8")
)
RECORDED = _FIXTURE["drafts"]
ENVELOPES = _FIXTURE["envelopes"]
BRIEF_30X40 = "30x40 east facing site in Whitefield, Bengaluru, 3BHK with pooja room"


class ScriptedProvider:
    """Implements StructuredCaller: returns each scripted step, or raises it."""

    name = "scripted"

    def __init__(self, *script: Any) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> BaseModel:
        self.calls.append(kwargs)
        if not self._script:
            raise AssertionError("stage ③ made more model calls than the test scripted")
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _setting(brief_text: str):
    brief = fallback.parse(brief_text)
    return brief, build_envelope(brief, allow_unverified=True)


def _replay(brief_text: str, **site):
    brief, envelope = _setting(brief_text)
    draft = ProgramDraft.model_validate(RECORDED[brief_text])
    program, how = build_program(brief, envelope, provider=ScriptedProvider(draft), **site)
    return program, how, draft


class TestEveryRoomIsBuiltByTheRules:
    """The merge built rooms by hand and copied one flag of five. Halls and corridors came
    out private rooms and neither the foyer nor the car bay needed the street, so all three
    plans the model path drew on 2026-09-13 had no front door."""

    @pytest.mark.parametrize("brief_text", list(RECORDED))
    def test_every_flag_is_the_one_its_kind_has(self, brief_text):
        program, how, _ = _replay(brief_text)
        assert how == "model"
        for room in program.rooms:
            rule = spec_for(room.kind, room.id, floor=room.floor)
            for field in (
                "needs_road_access", "needs_door", "is_through_route",
                "needs_exterior_wall", "min_area_sq_m", "min_width_m", "max_aspect",
            ):
                assert getattr(room, field) == getattr(rule, field), (room.id, field)

    @pytest.mark.parametrize("brief_text", list(RECORDED))
    def test_the_model_still_decides_ids_kinds_floors_and_sectors(self, brief_text):
        """Decision 1, from the other side: fixing the flags must not take a decision
        away from the model that is the model's to make."""
        program, _, draft = _replay(brief_text)
        asked = {r.id: (r.kind, r.floor, r.sector) for r in draft.rooms}
        built = {r.id: (r.kind, r.floor, r.sector) for r in program.rooms}
        assert built == asked


class TestTheUsersSiteChoicesReachTheModelPath:
    """`--stilt` and `--porch-in-setback` were read by the deterministic expansion only. The
    model path dropped both without a word — and a 30x40 3BHK needs the stilt to fit."""

    def test_a_stilt_leaves_only_the_car_the_door_and_the_stair_on_the_ground(self):
        program, how, _ = _replay(BRIEF_30X40, stilt=True)
        assert how == "model"
        ground = {room.kind for room in program.rooms if room.floor == 1}
        assert SpaceKind.STILT in ground
        assert ground <= {
            SpaceKind.CAR_PARKING, SpaceKind.FOYER, SpaceKind.STAIRCASE, SpaceKind.STILT,
        }

    def test_a_porch_the_setback_cannot_hold_is_refused_not_retried(self):
        """A refusal for the user, not a schema error: sending it back to the model as
        "that did not work" would spend both retries and then fall back silently."""
        brief, envelope = _setting(BRIEF_30X40)
        with pytest.raises(PorchDoesNotFit):
            expand(brief, envelope, porch_in_setback=True)  # the offline path refuses
        provider = ScriptedProvider(ProgramDraft.model_validate(RECORDED[BRIEF_30X40]))
        with pytest.raises(PorchDoesNotFit):
            build_program(brief, envelope, provider=provider, porch_in_setback=True)
        assert len(provider.calls) == 1

    def test_the_fallback_carries_the_site_choices_too(self):
        brief, envelope = _setting(BRIEF_30X40)
        program, how = build_program(
            brief, envelope, provider=ScriptedProvider(RuntimeError("network down")),
            stilt=True,
        )
        assert how != "model"
        assert SpaceKind.STILT in {room.kind for room in program.rooms if room.floor == 1}


class TestARecordedModelProgrammeIsAHouse:
    """The regression that matters. The three live runs of 2026-09-13 each drew a ground
    floor with no front door; replayed through the fixed merge, on the envelope the model's
    own reading of the brief produced, each one can be walked into."""

    @pytest.mark.parametrize("brief_text", list(RECORDED))
    def test_the_ground_floor_has_a_front_door_and_every_room_a_route(self, brief_text):
        from app.ir.envelope import Envelope
        from app.ir.enums import OpeningKind, Severity
        from app.refine import refine
        from app.solver import plan
        from app.validator import judge, validate

        brief = fallback.parse(brief_text)
        envelope = Envelope.model_validate(ENVELOPES[brief_text])
        draft = ProgramDraft.model_validate(RECORDED[brief_text])
        program, how = build_program(brief, envelope, provider=ScriptedProvider(draft))
        assert how == "model"

        bundle = plan(brief, envelope, program, seed=7, judge=judge(program, envelope))
        for layout in bundle.layouts:
            floor = refine(layout, program, envelope)
            if layout.floor == 1:
                assert any(o.kind is OpeningKind.ENTRANCE for o in floor.openings)
            errors = [
                f for f in validate(layout, program, floor).by_check("circulation")
                if f.severity is Severity.ERROR
            ]
            assert errors == [], errors[0].message if errors else ""
