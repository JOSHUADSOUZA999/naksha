"""Closed vocabularies stay closed — on every model, including ones not written yet.

The concern this answers is not about one field. If an enum can carry a value outside
its members, then stage ③'s room kinds and adjacency relations can too, and there a
bad value is a bad *plan* rather than a bad label. So this discovers the IR's models
and enums by inspection rather than listing them: a `RoomSpec` added next month is
covered the moment it imports a `StrEnum`, without anyone remembering to come back.

Two independent guarantees, because either alone is insufficient:
  1. the constraint is present in the JSON Schema handed to the provider, which is
     what stops a well-behaved model emitting a foreign value in the first place;
  2. the constraint is enforced on parse, which is what catches one that does anyway.
"""

from __future__ import annotations

import enum
import inspect
import pkgutil
from importlib import import_module

import pytest
from pydantic import BaseModel, ValidationError

import app.ir


def _ir_members() -> tuple[list[type[BaseModel]], list[type[enum.Enum]]]:
    models: list[type[BaseModel]] = []
    enums: list[type[enum.Enum]] = []
    for info in pkgutil.iter_modules(app.ir.__path__):
        module = import_module(f"app.ir.{info.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != module.__name__:
                continue  # imported from elsewhere; it is that module's to check
            if issubclass(obj, BaseModel):
                models.append(obj)
            elif issubclass(obj, enum.Enum):
                enums.append(obj)
    return models, enums


MODELS, ENUMS = _ir_members()
ENUMS_BY_NAME = {e.__name__: e for e in ENUMS}


def test_the_ir_was_actually_discovered():
    """A discovery bug would make every test below vacuously pass."""
    assert {m.__name__ for m in MODELS} >= {"PlotSpec", "BriefDraft", "Envelope"}
    assert {e.__name__ for e in ENUMS} >= {"VastuStance", "Facing", "RoomKind"}


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_enums_reach_the_provider_as_a_closed_set(model: type[BaseModel]):
    """The schema the model is handed must name every permitted value.

    Structured output is enforced server-side against this document. If a field
    arrives as a bare string, nothing upstream constrains what comes back.
    """
    for name, definition in model.model_json_schema().get("$defs", {}).items():
        source = ENUMS_BY_NAME.get(name)
        if source is None:
            continue
        assert "enum" in definition, f"{model.__name__}.{name} is not a closed set"
        assert set(definition["enum"]) == {m.value for m in source}


@pytest.mark.parametrize("enum_type", ENUMS, ids=lambda e: e.__name__)
def test_a_foreign_value_is_rejected_on_parse(enum_type: type[enum.Enum]):
    """Belt to the schema's braces — this is what catches a model that ignores it."""
    foreign = "definitely-not-a-member"
    assert foreign not in {m.value for m in enum_type}
    with pytest.raises(ValidationError):

        class _Probe(BaseModel):
            value: enum_type  # type: ignore[valid-type]

        _Probe(value=foreign)


def test_the_vocabularies_are_str_backed():
    """`StrEnum` is why these serialise as readable JSON rather than integer codes,
    and a model picks from `north_east` far more reliably than from `1`."""
    for enum_type in ENUMS:
        assert issubclass(enum_type, str), f"{enum_type.__name__} is not str-backed"
