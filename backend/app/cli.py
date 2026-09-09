"""Command-line entry point for stage ①.

    naksha-intent "30x40 east facing site in Whitefield, 3BHK with pooja room"
    naksha-intent -i                      # interactive, one brief per line
    echo "1200 sqft Pune 2bhk" | naksha-intent
    naksha-intent -p claude_code -s "..."  # override provider, summary output

Separate from `llm/intent.py` because `app.llm.__init__` imports that module, so
`python -m app.llm.intent` double-imports it and Python warns about unpredictable
behaviour. Nothing imports this module.
"""

from __future__ import annotations

import argparse
import json
import select
import sys
import textwrap
import warnings

from app.config import get_settings
from app.ir.models import Assumption, IntentResult, Question
from app.llm.intent import extract_brief
from app.llm.providers import known_providers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="naksha-intent",
        description="Turn a plot description into a validated Brief (pipeline stage ①).",
        epilog=(
            "With no TEXT and no pipe, drops into interactive mode. "
            "JSON goes to stdout and status to stderr, so `... | jq` works."
        ),
    )
    parser.add_argument("text", nargs="*", help="the plot description")
    parser.add_argument(
        "-i", "--interactive", action="store_true", help="loop, one brief per line"
    )
    parser.add_argument(
        "-s",
        "--summary",
        action="store_true",
        help="human-readable summary instead of JSON",
    )
    parser.add_argument(
        "-p",
        "--provider",
        choices=known_providers(),
        help="override the provider inferred from the model id",
    )
    parser.add_argument("-m", "--model", help="override NAKSHA_INTENT_MODEL")
    parser.add_argument(
        "-e",
        "--envelope",
        action="store_true",
        help="also run stage \u2461 and show the buildable rectangle",
    )
    parser.add_argument(
        "-P",
        "--program",
        action="store_true",
        help="also run stage \u2462 and show the expanded room list",
    )
    parser.add_argument(
        "-L",
        "--layout",
        metavar="FILE",
        help="solve stage \u2464 and write the plan bundle as JSON (use - for stdout)",
    )
    parser.add_argument(
        "--svg",
        metavar="FILE",
        help="solve stage \u2464 and draw each floor as SVG (one file per storey)",
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="layout seed; the same seed replays a plan"
    )
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="proceed on setback figures that have not been checked against the bye-laws",
    )
    parser.add_argument(
        "--fallback-only",
        action="store_true",
        help="skip the model and use the offline parser (fast, no credentials)",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="fail loudly if the model does not answer, instead of quietly "
        "substituting the offline parser (or set NAKSHA_ALLOW_FALLBACK=false)",
    )
    return parser


def _compact_rule_warnings() -> None:
    """One legible line per unverified ruleset, not a Python warning block.

    The default format leads with a file path and a source line, which buries the
    part that matters and reads as an internal error. This warning is aimed at the
    user — the figures behind their envelope have not been checked — so it should
    look like every other status line the CLI prints.
    """
    from app.rules import UnverifiedRuleset

    inherited = warnings.showwarning

    def show(message, category, filename, lineno, file=None, line=None):
        if isinstance(message, UnverifiedRuleset) or (
            isinstance(category, type) and issubclass(category, UnverifiedRuleset)
        ):
            print(f"[unverified] {message}", file=sys.stderr)
            return
        inherited(message, category, filename, lineno, file, line)

    warnings.showwarning = show


def main(argv: list[str] | None = None) -> int:
    _compact_rule_warnings()
    args = build_parser().parse_args(argv)

    settings = get_settings()
    overrides = {}
    if args.provider:
        overrides["intent_provider"] = args.provider
    if args.model:
        overrides["intent_model"] = args.model
    if overrides:
        settings = settings.model_copy(update=overrides)

    if args.text:
        return _run_one(" ".join(args.text), args, settings)

    # Piped input: treat the whole of stdin as one brief.
    if not sys.stdin.isatty():
        text = sys.stdin.read().strip()
        if not text:
            print("no input on stdin", file=sys.stderr)
            return 2
        return _run_one(text, args, settings)

    return _interactive(args, settings)


def _run_one(text: str, args: argparse.Namespace, settings) -> int:
    if args.fallback_only:
        from app.llm import fallback
        from app.llm.intent import cap_derived_confidence, select_questions

        brief = fallback.parse(text)
        brief = brief.model_copy(
            update={"assumptions": cap_derived_confidence(brief.assumptions)}
        )
        result = IntentResult(
            brief=brief,
            provenance=_offline_provenance(),
            questions=select_questions(brief.assumptions),
        )
    else:
        from app.llm.intent import FallbackRefused

        try:
            # The flag forces it on; the setting is the standing default, so a
            # .env can make "never substitute quietly" the mode for a whole project
            # without every command carrying the flag.
            result = extract_brief(
                text,
                settings=settings,
                allow_fallback=settings.allow_fallback and not args.no_fallback,
            )
        except FallbackRefused as exc:
            # Deliberately not a traceback: the cause is almost always credentials or
            # a rate limit, and `_hint` already knows how to say what to do about it.
            print(f"[no model] {exc}", file=sys.stderr)
            hint = _hint_for_reason(exc.reason, settings)
            if hint:
                print(f"           → {hint}", file=sys.stderr)
            return 1

    if args.summary:
        print(_summarise(result))
        if args.envelope:
            print(_envelope_summary(result, args))
        if args.program:
            print(_program_summary(result, args, settings))
    if args.layout or args.svg:
        _write_layout(result, args, settings)
    else:
        print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))

    print("\n" + _status(result), file=sys.stderr)
    hint = _hint(result, settings)
    if hint:
        print(f"          → {hint}", file=sys.stderr)
    return 0


def _interactive(args: argparse.Namespace, settings) -> int:
    # Always show the *resolved* provider, not just an explicit override. Showing
    # only the model made an inferred route invisible, so a run that was always
    # going to need an API key looked like it would use whatever you had logged in.
    print(
        f"naksha stage ① — {_route(settings)}\n"
        "Type a plot description and press enter. Ctrl-D or 'quit' to exit.\n"
        "Multi-line is fine — paste it, or end a line with \\ to continue.\n"
        "Try: 30x40 east facing site in Whitefield, 3BHK with pooja room\n",
        file=sys.stderr,
    )
    # Summary is the sane default here: a wall of JSON per line is unreadable when
    # you are iterating on phrasings, which is the whole point of this mode.
    args = argparse.Namespace(**{**vars(args), "summary": True})

    while True:
        try:
            text = _read_brief()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return 0
        if not text:
            continue
        if text in {"quit", "exit", ":q"}:
            return 0
        # Breathing room under the echoed input. On stderr so a redirected stdout
        # still starts at the first real line of the summary.
        print(file=sys.stderr)
        _run_one(text, args, settings)
        print(file=sys.stderr)


# A pasted brief arrives as several lines and `input()` hands back only the first.
# Reading just that silently truncated the brief: the words after the newline came
# back as a second, unrelated brief. Worse than an error, because both halves parse —
# a 4BHK joint-family brief split into a 4BHK with no pooja room and a fragment, and
# the "Vastu is very important" never reached the model at all.
_CONTINUATION = "\\"


def _read_brief(prompt: str = "brief> ") -> str:
    """One brief, however many lines it arrived on.

    Two ways to continue, covering typed and pasted input: a trailing backslash, and
    anything already sitting in the buffer behind the line just read.
    """
    lines = [input(prompt)]
    while True:
        if lines[-1].rstrip().endswith(_CONTINUATION):
            lines[-1] = lines[-1].rstrip().removesuffix(_CONTINUATION)
        elif not _input_pending():
            break
        lines.append(input("     … "))
    return " ".join(line.strip() for line in lines).strip()


def _input_pending() -> bool:
    """True when more input is already buffered — what a multi-line paste looks like.

    A typed line submits on Enter, because nobody types the next line inside the
    timeout. The timeout is short enough to be invisible and long enough for a
    terminal that delivers a paste in chunks.
    """
    try:
        return bool(select.select([sys.stdin], [], [], 0.05)[0])
    except (OSError, ValueError):
        # Not a selectable fd — piped, captured under pytest, or a Windows console.
        # Falling back to single-line reading is the old behaviour, not a crash.
        return False


def _offline_provenance():
    from app.llm.client import load_prompt
    from app.llm.intent import CLARIFY_RULES, PROMPT_VERSION
    from app.ir.models import Provenance
    from app.rules import load_ruleset

    prompt = load_prompt(PROMPT_VERSION)
    return Provenance(
        prompt_version=prompt.version,
        prompt_sha256=prompt.sha256,
        fallback_used=True,
        fallback_reason="--fallback-only",
        ruleset_versions={"clarify": load_ruleset(CLARIFY_RULES).stamp},
    )


def _summarise(result: IntentResult) -> str:
    brief = result.brief
    plot = brief.plot
    program = brief.program

    facing = plot.facing.value.replace("_", " ")
    # Both edges, when there are two: a corner plot's second road takes the same
    # larger setback, and "corner" alone does not say which side it is on.
    edges = (
        " · roads " + " + ".join(e.value.replace("_", " ") for e in plot.road_edges)
        if len(plot.road_edges) > 1
        else ""
    )
    baths = f"{program.bathrooms} bath" if program.bathrooms is not None else "bath not stated"
    rooms = ", ".join(r.value.replace("_", " ") for r in program.extra_rooms)
    where = ", ".join(
        p for p in (brief.locale.locality, brief.locale.city, brief.locale.state) if p
    ) or "not stated"

    programme = [f"{program.bedrooms}BHK", baths, _floors(program.floors)]
    if rooms:
        programme.append(rooms)

    lines = [
        f"  plot      {plot.width_m:.2f} x {plot.depth_m:.2f} m "
        f"({plot.area_sq_m:.1f} m²) · {facing} facing"
        f"{' · corner' if plot.corner_plot else ''}{edges}",
        f"  program   {' · '.join(programme)}",
        f"  location  {where}",
        f"  vastu     {brief.vastu.value}",
    ]
    if brief.constraints:
        lines.append("  kept      " + "; ".join(brief.constraints))
    if brief.assumptions:
        lines.append("")
        lines.append("  assumed")
        lines.extend(_assumption_table(brief.assumptions))
    if result.questions:
        lines.append("")
        lines.append("  ask")
        lines.extend(_question_list(result.questions))
    return "\n".join(lines)


def _envelope_summary(result: IntentResult, args: argparse.Namespace) -> str:
    """Stage \u2461, or the reason there isn't one.

    A refusal is printed as plainly as a result. "No envelope, because the city is
    unknown" is the finding — quietly substituting some other city's bye-laws would
    turn a legible gap into a wrong number.
    """
    from app.envelope import EnvelopeError, build_envelope

    try:
        env = build_envelope(result.brief, allow_unverified=args.allow_unverified)
    except EnvelopeError as exc:
        return f"\n  envelope  \u2014 {type(exc).__name__}: {exc}"

    margins = " ".join(f"{e.value[0].upper()}{v:g}" for e, v in env.setbacks.items())
    return (
        f"\n  envelope  {env.east_west_m:.2f} x {env.north_south_m:.2f} m "
        f"({env.area_sq_m:.1f} m\u00b2) \u00b7 setbacks {margins}"
        f"\n  caps      footprint \u2264 {env.max_footprint_sq_m:.1f} m\u00b2 \u00b7 "
        f"built \u2264 {env.max_built_area_sq_m:.1f} m\u00b2 over \u2264{env.max_floors} floors "
        f"(FAR {env.max_far:g}, coverage {env.max_coverage:.0%}, road {env.road_width_m:g}m)"
        f"\n  rules     {env.authority} \u00b7 {env.ruleset}"
    )


def _write_layout(result: IntentResult, args: argparse.Namespace, settings) -> None:
    """Solve and write the bundle. `-` means stdout, so it pipes into `jq`."""
    from app.envelope import EnvelopeError, build_envelope
    from app.program import expand
    from app.solver import plan

    try:
        envelope = build_envelope(result.brief, allow_unverified=args.allow_unverified)
    except EnvelopeError as exc:
        print(f"[no layout] {type(exc).__name__}: {exc}", file=sys.stderr)
        return

    program, how = _build_program(result.brief, envelope, args, settings)
    if how != "model":
        print(f"[program] deterministic expansion — {how}", file=sys.stderr)
    bundle = plan(result.brief, envelope, program, seed=args.seed)

    # Stage ④ only where stage ⑤ could not produce a legal plan. Assessing costs 60
    # solves a floor, which is not worth paying on the common path where it worked.
    from app.feasibility import assess

    for layout in bundle.layouts:
        if layout.unbuildable == 0:
            continue
        verdict = assess(program, envelope, floor=layout.floor)
        print(f"\n[why] {verdict.reason}", file=sys.stderr)
        for option in verdict.options:
            print(f"       \u00b7 {option}", file=sys.stderr)
    if args.layout:
        payload = json.dumps(bundle.model_dump(mode="json"), indent=2, ensure_ascii=False)
        if args.layout == "-":
            print(payload)
        else:
            from pathlib import Path

            Path(args.layout).write_text(payload, encoding="utf-8")
            floors = ", ".join(
                f"floor {lay.floor} score {lay.score:.0f}" for lay in bundle.layouts
            )
            print(f"[layout] {args.layout} \u2014 {floors}", file=sys.stderr)

    if args.svg:
        _write_svg(bundle, args.svg)


def _write_svg(bundle, path: str) -> None:
    """Draw each storey. Multi-floor briefs get one file per floor, never a merge.

    No `-` for stdout, unlike the JSON: an SVG on a pipe has nothing to receive it,
    and the bundle is already the machine-readable form. Storeys are separate files
    because they are separate drawings — a G+1 is two sheets, not one image.
    """
    from pathlib import Path

    from app.export.svg import render

    from app.refine import breaches, refine

    kinds = {room.id: room.kind.value for room in bundle.program.rooms}
    target = Path(path)
    for layout in bundle.layouts:
        # Stage ⑥. Refining is deterministic and cheap, so the drawing always gets
        # walls — a room outline is what stage ⑤ produces, not what a person asked for.
        floor = refine(layout, bundle.program)
        # Single-storey keeps the name the user typed; only a stack needs qualifying.
        out = (
            target
            if len(bundle.layouts) == 1
            else target.with_name(f"{target.stem}-floor{layout.floor}{target.suffix}")
        )
        title = f"{bundle.brief_text} \u2014 floor {layout.floor}"
        out.write_text(
            render(layout, kinds, title=title, refined=floor), encoding="utf-8"
        )
        flags = f", {layout.unbuildable} unbuildable" if layout.unbuildable else ""
        # Recheck legality on the clear floor, independently of the scorer. Silent
        # when the plan is clean; when it is not, these are the same rooms `score`
        # counted as unbuildable, restated in the dimension a person can measure.
        clear_breaches = breaches(floor, bundle.program)
        if clear_breaches:
            disagrees = "" if layout.unbuildable else " \u2014 and stage \u2464 called this plan clean, which is a bug"
            print(
                f"\n[walls] floor {layout.floor}: {len(clear_breaches)} room(s) below "
                f"a minimum, measured inside the walls{disagrees}",
                file=sys.stderr,
            )
            for breach in clear_breaches[:4]:
                print(f"        \u00b7 {breach}", file=sys.stderr)
            if len(clear_breaches) > 4:
                print(f"        \u00b7 \u2026and {len(clear_breaches) - 4} more", file=sys.stderr)

        doors = sum(1 for o in floor.openings if o.kind.value != "window")
        windows = len(floor.openings) - doors
        print(
            f"[svg] {out} \u2014 score {layout.score:.0f}{flags} \u00b7 "
            f"{len(floor.walls)} walls, {doors} doors, {windows} windows",
            file=sys.stderr,
        )


def _build_program(brief, envelope, args: argparse.Namespace, settings):
    """Stage ③ through the model unless the run is explicitly offline.

    `--fallback-only` means "no model anywhere", so it governs ③ as well as ①.
    """
    if args.fallback_only:
        from app.program import expand

        return expand(brief, envelope), "--fallback-only"

    from app.llm.program import build_program

    return build_program(
        brief,
        envelope,
        settings=settings,
        allow_fallback=settings.allow_fallback and not args.no_fallback,
    )


def _program_summary(result: IntentResult, args: argparse.Namespace, settings) -> str:
    """Stage \u2462: what "3BHK with a pooja room" actually means in rooms.

    The feasibility line is the point of showing it next to the envelope — a room list
    that does not fit its budget is the finding, and it is worth seeing before anyone
    tries to lay it out.
    """
    from app.envelope import EnvelopeError, build_envelope
    from app.program import fits

    try:
        envelope_for_program = build_envelope(
            result.brief, allow_unverified=args.allow_unverified
        )
    except EnvelopeError:
        envelope_for_program = None
    program, _ = _build_program(result.brief, envelope_for_program, args, settings)
    lines = [
        f"\n  program   {len(program.rooms)} rooms, {len(program.adjacencies)} "
        f"relationships \u00b7 min {program.min_area_sq_m:.1f} m\u00b2 \u00b7 "
        f"target {program.target_area_sq_m:.1f} m\u00b2"
    ]
    for room in program.rooms:
        sector = room.sector.value.replace("_", " ") if room.sector else ""
        lines.append(
            f"    {room.id:<12} {room.kind.value.replace('_', ' '):<15} "
            f"{room.target_area_sq_m:5.1f} m\u00b2  {sector}"
        )

    try:
        envelope = build_envelope(result.brief, allow_unverified=args.allow_unverified)
    except EnvelopeError as exc:
        lines.append(f"    \u2014 feasibility unknown: {type(exc).__name__}")
        return "\n".join(lines)

    ok, why = fits(program, envelope)
    lines.append(f"\n  fits      {'yes' if ok else 'NO'} \u2014 {why}")
    return "\n".join(lines)


def _question_list(questions: list[Question]) -> list[str]:
    """The two guesses worth interrupting for.

    Printed last, after the assumptions they were chosen from, because the ranking
    only makes sense once you have seen the field it ranked over. Each one carries
    what stands if it goes unanswered — a question the user can ignore is a question
    they are allowed to ignore.
    """
    lines: list[str] = []
    for n, q in enumerate(questions, 1):
        body = textwrap.wrap(q.ask, 64)
        lines.append(f"    {n}. {body[0]}")
        lines.extend(f"       {line}" for line in body[1:])
        lines.append(f"       meanwhile — {q.assumed}  ({q.field}, {q.confidence:g})")
    return lines


def _floors(floors: int) -> str:
    """'ground floor', not '1 floor(s)'.

    The people typing these briefs say "G+1", and reading their own vocabulary back
    to them is how they spot that we misread it.
    """
    return "ground floor" if floors == 1 else f"G+{floors - 1}"


# Caps for the two free-text columns, chosen so a full-width row lands inside 80
# columns. Both need one: the schema allows an 80-character value, and a single long
# one would otherwise set the column width for the whole table.
_VALUE_WIDTH = 26
_REASON_WIDTH = 26


def _assumption_table(assumptions: list[Assumption]) -> list[str]:
    """Four aligned columns: what was assumed, to what, why, and how sure.

    A table rather than sentences because the column that matters is confidence, and
    a number buried at the end of prose is a number nobody reads. Aligned, the 0.2 in
    a column of 0.9s is the first thing you see.
    """
    field_w = max(len(a.field) for a in assumptions)
    value_w = min(max(len(a.value) for a in assumptions), _VALUE_WIDTH)

    rows: list[str] = []
    for a in assumptions:
        values = textwrap.wrap(a.value, value_w) or [""]
        reasons = textwrap.wrap(a.reason, _REASON_WIDTH) or [""]
        for i in range(max(len(values), len(reasons))):
            # Field and confidence sit on the first line only. Repeating them down a
            # wrapped row would read as several assumptions rather than one.
            field = a.field if i == 0 else ""
            # `%g` so 0.8 prints as 0.8 rather than 0.80 — a trailing zero reads as
            # more precision than a hand-set default has.
            conf = f" {a.confidence:g}" if i == 0 else ""
            value = values[i] if i < len(values) else ""
            reason = reasons[i] if i < len(reasons) else ""
            rows.append(
                f"    {field:<{field_w}}   {value:<{value_w}}  "
                f"{reason:<{_REASON_WIDTH}}{conf}".rstrip()
            )
    return rows


def _route(settings) -> str:
    """The model *and* the provider it will actually reach."""
    from app.llm.providers import UnknownProvider, resolve_provider_name

    try:
        provider = resolve_provider_name(settings.intent_model, settings.intent_provider)
    except UnknownProvider:
        return f"model {settings.intent_model} · no provider resolved"
    return f"model {settings.intent_model} · provider {provider}"


def _hint(result: IntentResult, settings) -> str | None:
    """Turn a fallback into a next action.

    "Could not resolve authentication method" is an accurate diagnosis and useless
    advice. What a user needs is the command that fixes it — especially here, where
    the answer may be "you already have a Claude login, just point at it".
    """
    prov = result.provenance
    if not prov.fallback_used:
        return None
    return _hint_for_reason(prov.fallback_reason or "", settings, prov.provider)


def _hint_for_reason(reason: str, settings, provider: str | None = None) -> str | None:
    """Shared by the fallback path and `--no-fallback`, which need the same advice."""
    reason = reason.lower()
    if provider is None:
        from app.llm.providers import UnknownProvider, resolve_provider_name

        try:
            provider = resolve_provider_name(settings.intent_model, settings.intent_provider)
        except UnknownProvider:
            provider = None

    if "not installed" in reason or "claude-agent-sdk" in reason:
        return 'install it: pip install -e ".[claude-code]"'
    if "cannot tell which provider" in reason:
        return "set NAKSHA_INTENT_PROVIDER, or use -p"
    if not any(k in reason for k in ("credential", "authentication", "api key", "401")):
        return None

    if provider == "openai":
        return "set OPENAI_API_KEY in .env"
    if provider == "anthropic":
        return (
            "set ANTHROPIC_API_KEY in .env — or, if you have a Claude subscription, "
            "run with -p claude_code (no key needed)"
        )
    return None


def _status(result: IntentResult) -> str:
    prov = result.provenance
    if prov.fallback_used:
        return f"[fallback] {prov.fallback_reason or 'no reason recorded'}"
    # prompt_sha256 is `str | None` on the model. In practice stage ① always sets it
    # on success, but the status line is the last thing that should ever raise —
    # crashing while reporting a *successful* extraction would be absurd.
    digest = f"@{prov.prompt_sha256[:6]}" if prov.prompt_sha256 else ""
    tries = f"{prov.attempts} attempt" + ("" if prov.attempts == 1 else "s")
    # The provider is deliberately not here: it is in the JSON provenance, and in
    # interactive mode the header already prints the resolved route. Repeating it on
    # every line pushed the part that changes — the prompt hash — off the eye.
    return f"[model] {prov.model} · {prov.prompt_version}{digest} · {tries}"


if __name__ == "__main__":
    raise SystemExit(main())
