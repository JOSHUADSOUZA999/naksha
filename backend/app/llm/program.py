"""Stage ③ PROGRAM through a model — `Brief` + `Envelope` to a room graph.

Mirrors `llm/intent.py` exactly, because the contract is the same one: schema-locked
output, a versioned prompt hashed into provenance, two schema retries, then the
deterministic expansion in `app.program` as the floor.

**What the model adds over that floor is reading.** The rules expand "3BHK" correctly
and cannot do anything at all with "my parents are elderly" — that sentence survives
stage ① verbatim in `constraints` and then dies, because a regex has nowhere to put
it. Here it becomes a ground-floor bedroom near the entrance, with the reason attached.

**What the model is not asked for is sizes.** `ProgramDraft` has no area or width
field. NBC minimums come from `rules/spaces_v1.json`; a model inventing one would
produce an authoritative-looking wrong number in the one place that decides whether a
plan is legal.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings
from app.ir.envelope import Envelope
from app.ir.models import Brief
from app.ir.enums import Relation
from app.ir.plan import Program, ProgramDraft
from app.llm.client import Prompt, load_prompt
from app.llm.errors import SchemaRetriesExhausted
from app.llm.providers import (
    ProviderOutputInvalid,
    ProviderRefused,
    StructuredCaller,
    build_provider,
)
from app.llm.trace import trace_span
from app.program import apply_site_choices, expand, spec_for

PROMPT_VERSION = "program_v3"


def build_program(
    brief: Brief,
    envelope: Envelope | None = None,
    *,
    provider: StructuredCaller | None = None,
    settings: Settings | None = None,
    allow_fallback: bool = True,
    porch_in_setback: bool = False,
    stilt: bool = False,
) -> tuple[Program, str]:
    """The room graph, and how it was obtained — `"model"` or a fallback reason.

    Returns the deterministic expansion when the model is unreachable or never returns
    a graph that validates, on the same reasoning as stage ①: a degraded programme the
    user can correct beats a stack trace.
    """
    settings = settings or get_settings()
    prompt = load_prompt(PROMPT_VERSION)
    # The user's site decisions travel with every exit, the fallback's included.
    site = {"porch_in_setback": porch_in_setback, "stilt": stilt}

    if provider is None:
        try:
            provider = build_provider(settings)
        except Exception as exc:
            return _fallback(brief, envelope, f"provider: {exc}", allow_fallback, **site)

    messages: list[dict[str, Any]] = [{"role": "user", "content": _describe(brief, envelope)}]
    errors: list[str] = []

    for attempt in range(settings.max_schema_retries + 1):
        try:
            draft = _one_attempt(provider, settings, prompt, messages)
        except ProviderOutputInvalid as exc:
            errors.append(str(exc))
            messages = [
                messages[0],
                {"role": "user", "content": _correction(exc)},
            ]
            continue
        except ProviderRefused as exc:
            return _fallback(brief, envelope, f"refusal: {exc.category}", allow_fallback, **site)
        except Exception as exc:
            return _fallback(
                brief, envelope, f"{type(exc).__name__}: {exc}", allow_fallback, **site
            )

        try:
            program = _merge(draft)
        except Exception as exc:
            # The draft validated but the merge did not — a room kind with no rule, or
            # an adjacency the Program rejects. Retryable: the model can fix it.
            errors.append(f"merge: {exc}")
            messages = [messages[0], {"role": "user", "content": f"That did not work: {exc}"}]
            continue
        # After the merge and outside the retry: a porch the setback cannot hold is the
        # user's refusal to hear, not a schema error the model could correct.
        rooms = apply_site_choices(
            program.rooms, envelope, porch_in_setback=porch_in_setback, stilt=stilt
        )
        return Program(rooms=rooms, adjacencies=program.adjacencies), "model"

    exhausted = SchemaRetriesExhausted(settings.max_schema_retries + 1, errors)
    return _fallback(brief, envelope, f"schema: {exhausted}", allow_fallback, **site)


def _describe(brief: Brief, envelope: Envelope | None) -> str:
    """What the model needs to know, in words rather than a JSON dump.

    The envelope's *budget* matters — a programme has to fit — but its geometry does
    not, and handing over coordinates invites the model to start placing things.
    """
    hints = brief.program
    lines = [
        f'The user wrote: "{brief.raw_text}"',
        "",
        f"Bedrooms: {hints.bedrooms}",
        f"Bathrooms: {hints.bathrooms}",
        f"Floors allowed: {hints.floors}",
        f"Occupants: {hints.occupants}",
        f"Parking bays: {hints.parking_bays}",
        f"Rooms they named: {', '.join(r.value for r in hints.extra_rooms) or 'none'}",
        f"Vastu: {brief.vastu.value}",
    ]
    if brief.constraints:
        lines += ["", "In their own words, and not yet acted on:"]
        lines += [f"  - {c}" for c in brief.constraints]
    if envelope is not None:
        lines += [
            "",
            f"Each floor has about {envelope.max_footprint_sq_m:.0f} m² buildable, "
            f"and {envelope.max_built_area_sq_m:.0f} m² across at most "
            f"{envelope.max_floors} floors. Keep the programme inside that.",
        ]
    return "\n".join(lines)


def _one_attempt(provider, settings, prompt: Prompt, messages) -> ProgramDraft:
    with trace_span(
        "program.build",
        provider=getattr(provider, "name", "unknown"),
        model=settings.intent_model,
        prompt_version=prompt.version,
        prompt_sha256=prompt.sha256,
        messages=messages,
    ) as span:
        draft = provider.parse(
            model=settings.intent_model,
            system=prompt.text,
            messages=messages,
            output_model=ProgramDraft,
            max_tokens=settings.intent_max_tokens,
        )
        span["rooms"] = len(draft.rooms)
        return draft


def _merge(draft: ProgramDraft) -> Program:
    """The model's graph plus the rules' rooms. Neither side sees the other's job.

    **Every room is built by `spec_for`, as the deterministic expansion builds it.** This
    used to construct `RoomSpec`s by hand and copied one flag of five, which went unseen
    until the model path first ran end to end: halls and corridors were private rooms,
    the foyer and car bay never needed the street, and both plans tried had no front
    door. The model contributes a room's id, kind, floor and sector — nothing else.
    """
    rooms = [
        spec_for(request.kind, request.id, floor=request.floor).model_copy(
            update={"sector": request.sector}
        )
        for request in draft.rooms
    ]
    # `near` is a walk, and nobody walks to a room with no door into the house. The live
    # model asked for a car bay near the foyer beside the parents' bedroom; a porch is
    # entered from the street, so that request could never be met and would only be
    # reported. Dropped here rather than trusted to the prompt alone.
    walked = {room.id for room in rooms if room.needs_door}
    adjacencies = [
        edge for edge in draft.adjacencies
        if edge.relation is not Relation.NEAR or {edge.a, edge.b} <= walked
    ]
    return Program(rooms=rooms, adjacencies=adjacencies)


def _correction(exc: ProviderOutputInvalid) -> str:
    detail = (
        "\n".join(
            f"- {'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.validation_error.errors()
        )
        if exc.validation_error is not None
        else f"- {exc}"
    )
    return (
        "Your previous answer did not satisfy the schema:\n"
        f"{detail}\n\n"
        "The usual causes are an adjacency naming a room that is not in your room "
        "list, the same pair of rooms appearing twice, or a duplicate room id. "
        "Return a corrected room list and graph."
    )


def _fallback(
    brief: Brief,
    envelope: Envelope | None,
    reason: str,
    allow_fallback: bool,
    *,
    porch_in_setback: bool = False,
    stilt: bool = False,
) -> tuple[Program, str]:
    if not allow_fallback:
        from app.llm.intent import FallbackRefused

        raise FallbackRefused(reason, 1)
    return expand(
        brief, envelope, porch_in_setback=porch_in_setback, stilt=stilt
    ), reason
