"""Stage ① INTENT — free text to a validated `Brief`.

The contract, from CLAUDE.md:

- schema-constrained output, never free-text parsing
- versioned prompt, referenced in provenance by name *and* hash
- 2 retries on schema failure, then a deterministic fallback
- the LLM being down must never break generation entirely

This module names no SDK. It asks a `StructuredCaller` for a `BriefDraft` and handles
four normalised outcomes; which vendor answers is `providers/`' problem.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings
from app.ir.models import (
    Assumption,
    Brief,
    BriefDraft,
    IntentResult,
    Provenance,
    Question,
)
from app.llm import fallback
from app.llm.client import Prompt, load_prompt
from app.llm.errors import SchemaRetriesExhausted
from app.llm.providers import (
    ProviderOutputInvalid,
    ProviderRefused,
    StructuredCaller,
    build_provider,
    resolve_provider_name,
)
from app.llm.trace import trace_span
from app.rules import load_ruleset


class FallbackRefused(RuntimeError):
    """The model did not answer, and the caller asked not to degrade quietly.

    The default is still to fall back — a degraded brief the user can correct beats a
    stack trace, and CLAUDE.md is explicit that the LLM being down must never break
    generation. But when you are *testing the model*, a silent substitution is the
    worst outcome available: the output looks fine and is not what you asked for.
    """

    def __init__(self, reason: str, attempts: int) -> None:
        super().__init__(f"model produced no Brief after {attempts} attempt(s): {reason}")
        self.reason = reason
        self.attempts = attempts


PROMPT_VERSION = "intent_v1"
CLARIFY_RULES = "clarify_v1"


def extract_brief(
    text: str,
    *,
    provider: StructuredCaller | None = None,
    settings: Settings | None = None,
    allow_fallback: bool = True,
) -> IntentResult:
    """Turn what the user typed into a `Brief`.

    Returns a result even when the model fails: the deterministic parser fills in and
    `provenance.fallback_used` says so, because a degraded brief the user can correct
    beats a stack trace.

    Pass `allow_fallback=False` to raise `FallbackRefused` instead. That is for when
    you are evaluating the model rather than serving a user — a silent substitution
    then produces output that looks right and answers a different question.
    """
    settings = settings or get_settings()
    prompt = load_prompt(PROMPT_VERSION)

    if provider is None:
        try:
            provider = build_provider(settings)
        except Exception as exc:
            # Unknown model id, missing SDK, unusable credentials — all before a
            # request is ever made.
            return _fallback_result(
                text, prompt, settings, attempts=0, reason=f"provider: {exc}",
                allow_fallback=allow_fallback,
            )

    messages: list[dict[str, Any]] = [{"role": "user", "content": text}]
    schema_errors: list[str] = []
    attempts = 0

    # One initial call plus `max_schema_retries` corrections.
    for _ in range(settings.max_schema_retries + 1):
        attempts += 1
        try:
            draft = _one_attempt(provider, settings, prompt, messages)
        except ProviderOutputInvalid as exc:
            schema_errors.append(str(exc))
            messages = [
                {"role": "user", "content": text},
                {"role": "user", "content": _correction(exc)},
            ]
            continue
        except ProviderRefused as exc:
            return _fallback_result(
                text, prompt, settings, attempts, f"refusal: {exc.category}",
                allow_fallback=allow_fallback,
            )
        except Exception as exc:
            # Deliberately broad, and it earns its keep. Providers normalise what they
            # can predict; this catches what they cannot. The guarantee is that a
            # Brief always comes back, so the exception type is recorded in provenance
            # rather than allowed to escape.
            return _fallback_result(
                text, prompt, settings, attempts, f"{type(exc).__name__}: {exc}",
                allow_fallback=allow_fallback,
            )

        brief = _capped(_record_gaps(Brief.from_draft(draft, raw_text=text)))
        return IntentResult(
            brief=brief,
            provenance=Provenance(
                provider=_provider_name(provider, settings),
                model=settings.intent_model,
                prompt_version=prompt.version,
                prompt_sha256=prompt.sha256,
                attempts=attempts,
                fallback_used=False,
                ruleset_versions={"clarify": load_ruleset(CLARIFY_RULES).stamp},
            ),
            questions=select_questions(brief.assumptions),
        )

    exhausted = SchemaRetriesExhausted(attempts, schema_errors)
    return _fallback_result(
        text, prompt, settings, attempts, f"schema: {exhausted}",
        allow_fallback=allow_fallback,
    )


def _record_gaps(brief: Brief) -> Brief:
    """Add an assumption for a blocking field the model left null without comment.

    The clarifier can only ask about things that appear in `assumptions`. A field left
    silently null therefore produces no question — even when it is `blocking` and the
    next stage hard-fails on it. That is exactly what happened: "40x60 for a joint
    family" got no city, so stage ② raised `CityUnknown`, and the one question that
    would have unblocked it was never put to the user.

    Done in code rather than by asking the prompt for it, because the prompt correctly
    tells the model to leave `city` null when a locality does not place it — and
    because prompt adherence varies run to run in ways a hard failure downstream
    cannot afford to depend on. The offline parser has always recorded this gap; this
    makes the model path match.

    `city` is the only nullable blocking field: plot size, road edges and bedrooms are
    all required by the schema, so there is no gap for them to fall into.
    """
    if brief.locale.city is not None:
        return brief
    if any(a.field == "city" for a in brief.assumptions):
        return brief  # the model said something about it; do not talk over it
    gap = Assumption(
        field="city",
        value="left blank",
        reason="no city or locality recognised; stage \u2461 cannot pick a ruleset",
        confidence=0.1,
    )
    return brief.model_copy(update={"assumptions": [*brief.assumptions, gap]})


def _capped(brief: Brief) -> Brief:
    """Apply the ceiling to a Brief's assumptions, whoever produced them.

    Done here rather than in `fallback.py` so there is one implementation covering
    both paths. The parser used to cap `bedrooms`' children itself, which meant the
    model's identical mistake went uncorrected and `plot size` was missed on both.
    """
    return brief.model_copy(
        update={"assumptions": cap_derived_confidence(brief.assumptions)}
    )


def cap_derived_confidence(
    assumptions: list[Assumption], *, rules_version: str = CLARIFY_RULES
) -> list[Assumption]:
    """No inference can be more certain than what it was derived from.

    Reporting `floors: ground only, 0.75` on top of `plot size: 0.2` launders a guess
    into a fact one derivation later — you cannot know a 2BHK fits on one level when
    you do not know the plot. Every such pair was previously patched by hand at the
    site that produced it, which missed `plot size` entirely and would have missed the
    next root too. This applies the graph instead, so a new edge is a data change.

    The walk is transitive (a grandparent's doubt reaches down) and takes the
    **minimum** over parents, which is what `floors` needs — it hangs off both the
    plot size and the bedroom count, and the weaker of the two is what it is worth.

    Runs on the model's output as well as the parser's. The model launders too.
    """
    depends_on: dict[str, list[str]] = load_ruleset(rules_version).data["depends_on"]
    assumed = {a.field: a.confidence for a in assumptions}

    def ceiling(field: str, seen: frozenset[str]) -> float:
        cap = 1.0
        for parent in depends_on.get(field, ()):
            # A parent absent from `assumed` was read from the text, not guessed, so
            # it constrains nothing. `seen` guards a malformed ruleset with a cycle.
            if parent in seen or parent not in assumed:
                continue
            cap = min(cap, assumed[parent], ceiling(parent, seen | {field}))
        return cap

    capped: list[Assumption] = []
    for a in assumptions:
        limit = min(a.confidence, ceiling(a.field, frozenset()))
        capped.append(
            a if limit == a.confidence else a.model_copy(update={"confidence": limit})
        )
    return capped


def select_questions(
    assumptions: list[Assumption], *, rules_version: str = CLARIFY_RULES
) -> list[Question]:
    """Pick the few guesses worth interrupting for. Usually none.

    **The tier gates; confidence only sorts what survives.** Each tier carries the bar
    under which it is worth interrupting — 0.7 for blocking, 0.5 for consequential,
    never for the rest. A brief that states its dimensions, orientation and bedroom
    count has nothing above the bar and asks nothing at all. Taking the bottom N by
    confidence instead would always find something to ask, which is how a clarifier
    turns into a form.

    **The DAG stops one answer being asked twice.** Bathrooms, occupants and floors
    are read off the bedroom count; frontage and parking off the plot size. When a
    parent is itself a guess, its answer re-derives the children, so they drop out.

    **The budget is a ceiling on the result, not a quota to fill.** In practice the
    gate returns fewer.

    Nothing here blocks: the Brief is complete before this runs, and every question
    carries the value that stands if it goes unanswered.
    """
    rules = load_ruleset(rules_version).data
    tiers: dict[str, str] = rules["tiers"]
    default_tier: str = rules["default_tier"]
    ask_below: dict[str, float] = rules["ask_below"]
    rank: dict[str, int] = rules["rank"]
    depends_on: dict[str, list[str]] = rules["depends_on"]
    prompts: dict[str, str] = rules["questions"]

    assumed = {a.field for a in assumptions}
    gated: list[tuple[int, float, str, Assumption]] = []

    for a in assumptions:
        tier = tiers.get(a.field, default_tier)
        # The gate. `defaultable` and `never` sit at 0.0, which nothing can fall below.
        if a.confidence >= ask_below.get(tier, 0.0):
            continue
        if any(parent in assumed for parent in depends_on.get(a.field, ())):
            continue
        gated.append((rank.get(tier, 99), a.confidence, a.field, a))

    # Tier, then least certain, then name — the last so a tie is stable across runs
    # rather than dict-ordered. A selector that reorders itself cannot be tuned.
    gated.sort(key=lambda row: row[:3])

    return [
        Question(
            field=a.field,
            ask=prompts.get(a.field, f"What should {a.field} be?"),
            because=(
                f"{tiers.get(a.field, default_tier)} at {a.confidence:g} confidence "
                f"— {a.reason}"
            ),
            assumed=a.value,
            confidence=a.confidence,
        )
        for *_, a in gated[: rules["budget"]["ask"]]
    ]


def _one_attempt(
    provider: StructuredCaller,
    settings: Settings,
    prompt: Prompt,
    messages: list[dict[str, Any]],
) -> BriefDraft:
    with trace_span(
        "intent.extract_brief",
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
            output_model=BriefDraft,
            max_tokens=settings.intent_max_tokens,
        )
        span["brief"] = draft.model_dump(mode="json")
        return draft


def _correction(exc: ProviderOutputInvalid) -> str:
    """Retry feedback.

    Explicit about what failed, in the same spirit as the feasibility gate telling the
    model to "reduce by 25.6 m²" rather than just "infeasible". A model told which
    field broke and why fixes it; a model told "try again" repeats itself.
    """
    if exc.validation_error is not None:
        detail = "\n".join(
            f"- {'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.validation_error.errors()
        )
    else:
        detail = f"- {exc}"

    return (
        "Your previous answer did not satisfy the schema:\n"
        f"{detail}\n\n"
        "The most common cause is a unit mistake: plot sides are in metres, and a "
        "figure like 1200 is square feet, not a side length. Re-read the description "
        "above and return a corrected brief."
    )


def _provider_name(provider: StructuredCaller, settings: Settings) -> str:
    name = getattr(provider, "name", None)
    if name:
        return name
    try:
        return resolve_provider_name(settings.intent_model, settings.intent_provider)
    except Exception:
        return "unknown"


def _fallback_result(
    text: str,
    prompt: Prompt,
    settings: Settings,
    attempts: int,
    reason: str,
    allow_fallback: bool = True,
) -> IntentResult:
    if not allow_fallback:
        raise FallbackRefused(reason, attempts)
    try:
        provider_name = resolve_provider_name(
            settings.intent_model, settings.intent_provider
        )
    except Exception:
        provider_name = None

    # The offline parser guesses more, so it needs the questions more, not less.
    brief = _capped(_record_gaps(fallback.parse(text)))
    return IntentResult(
        brief=brief,
        provenance=Provenance(
            provider=provider_name,
            model=None,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
            attempts=attempts,
            fallback_used=True,
            fallback_reason=reason,
            ruleset_versions={"clarify": load_ruleset(CLARIFY_RULES).stamp},
        ),
        questions=select_questions(brief.assumptions),
    )
