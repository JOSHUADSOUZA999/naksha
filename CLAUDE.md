# CLAUDE.md — naksha

AI floor-plan generation for Indian residential plots. Vastu-aware, NBC-compliant,
editable output. Target users: plot owners and small builders in tier-1/2 India.

**What exists right now: stages ① INTENT and ② ENVELOPE** (`text → Brief → buildable
rect`), plus the slice of `ir/` they need. Everything else below is designed but
unbuilt — **naksha has not yet produced a floor plan**. This file is how to work
here, not where we are: **`STATUS.md`** tracks what is done, blocked and next (start
there when resuming), and **`DECISIONS.md`** records why the code is shaped this way,
every bug found, and the open questions that need a human. When you add a stage, add it at its real path — the tree here matches the full
naksha layout so nothing has to move later.

---

## THE FOUR DECISIONS

Everything in this codebase follows from these. If a change contradicts one, stop
and flag it rather than working around it.

1. **The LLM emits a graph. The solver emits geometry.**
   LLMs reason well about relationships and badly about coordinates. The LLM
   decides *what rooms, how big, next to what, which compass sector*. A
   constraint solver turns that into metric geometry. The LLM never outputs x/y.

2. **Layout is two stages.**
   Stage A (slicing tree) produces gap-free tilings *by construction*. Stage B
   (CP-SAT) tunes dimensions with adjacency already fixed. Single-stage CP-SAT
   could not find a feasible 10-room plan in 20 seconds; two-stage does it in ~2 s.
   That is what makes the product interactive.

3. **User edits become Constraints, never coordinates.**
   Dragging the kitchen east writes `Constraint(sector=SE, hard=True)`, not `x=5.36`.
   Store coordinates and "regenerate" wipes the user's work.

4. **Rules are versioned data, not code.**
   Vastu schools disagree; setbacks differ per city; NBC gets amended. Rules live
   as JSON in Postgres with a version stamped into every plan's provenance.

---

## PIPELINE

```
text ──① INTENT ─────► Brief          schema-locked, 2 retries      ← BUILT
                        + Questions   tier-gated, ≤3, never blocking  ← BUILT
     ──② ENVELOPE ───► buildable rect  setback ruleset lookup       ← BUILT*
     ──③ PROGRAM ────► rooms + adjacency graph + sector prefs  (LLM)
     ──④ FEASIBILITY ─✗─► explain, re-plan ×2, STOP with options
     ──⑤ LAYOUT ─────► Stage A 200 topologies ~5ms → Stage B ×8 ~1s
     ──⑥ REFINE ─────► walls → doors → fixtures → windows → furniture → dims
     ──⑦ VALIDATE ───► geometry · NBC · circulation · vastu · fit
     ──⑧ CRITIC ─────► rerank + rationale (optional, behind a flag)
```

Only ①③⑧ use an LLM. Everything else is deterministic code.

---

## REPO

```
naksha/
  backend/app/
    config.py    settings + the .env bridge
    cli.py       naksha-intent entry point
    ir/          models.py · enums.py · units.py · envelope.py · plan.py
                                                       ← THE CONTRACT
    llm/         intent.py · fallback.py · client.py · trace.py · errors.py
                 prompts/    versioned, hashed into provenance
                 providers/  base.py (the seam) · anthropic_api
                             openai_api · claude_code
    rules/       clarify_v1 · setbacks_v1 · spaces_v1.json  ← VERSIONED DATA
    envelope/    geometry.py (pure) · __init__.py (lookup + build)
    program/     __init__.py  deterministic ③ expansion + feasibility
    feasibility/ __init__.py  ④ explain + measured options
    solver/      slicing.py (Stage A) · tuning.py (Stage B, CP-SAT) · score.py
    export/      svg.py                                   ← DXF/PDF still to come
  frontend/      Vite + React + react-konva viewer        ← NEEDS NODE 18+
    program/     __init__.py  deterministic ③ expansion + feasibility
  tests/         test_ir_brief · test_ir_plan · test_units · test_fallback
                 test_intent · test_providers · test_config · test_cli
                 test_clarify · test_envelope · test_schema_enforcement · golden/
```

Not yet built, at their eventual paths: `store/` · `refine/` · `validator/` · `api/`.

---

## RUNNING IT

Python **3.11+** is required (`StrEnum`, `datetime.UTC`, `Self`).

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env          # set one key, or NAKSHA_INTENT_PROVIDER=claude_code

pytest                        # 470 tests, no network, no key, and independent
                              # of whatever is in your .env — see conftest
pytest -m live                # real model; needs credentials

naksha-intent "30x40 east facing site in Whitefield, 3BHK with pooja room"
naksha-intent -i              # interactive
naksha-intent -s --fallback-only "…"   # offline parser only, instant
naksha-intent -p claude_code -i        # subscription instead of an API key
```

JSON goes to stdout and status to stderr, so `naksha-intent "…" | jq` works.

Without the venv activated, prefix with `.venv/bin/` — the console script and
`pytest` both live there. If `uv` is missing:
`curl -LsSf https://astral.sh/uv/install.sh | sh`.

---

## MODULE RULES

### `ir/` — build this first, in isolation, no AI, no solver

Everything downstream inherits its mistakes.

- All lengths **metres**, float. Angles degrees clockwise from north.
- Polygons counter-clockwise, **not closed** (first point ≠ last point).
- `model_config = ConfigDict(extra="forbid")` — unknown keys are bugs.
- Validate referential integrity on construction.
- `Provenance` records provider, model, prompt version, prompt hash, ruleset
  versions, solver seed. You cannot debug a non-deterministic pipeline without it.
- `BriefDraft` is what the model returns; `Brief` adds `raw_text`, which we attach
  ourselves. Keep that split — the model must not be able to paraphrase the user's
  own words, and it cannot if the schema it sees has no field for them.
- **Stage ① fills blanks rather than emitting nulls, and every fill is an
  `Assumption`** with a `confidence`. A null the user never chose is not neutrality:
  it pushes the same guess into a later stage where nobody can see it. Bathrooms,
  occupants, parking and floors all default; a locality that names its own city fills
  `city` at 0.95. What holds the line is that the guess is *visible* — the CLI prints
  the confidence column, and the selector in `intent.py` gates on it to decide what
  is worth asking. `Assumption` is structured for that reason; prose cannot be
  thresholded. Never write 1.0: a value you are certain of was read, not assumed.
- **`road_edges` is the stored field; `facing` and `corner_plot` are computed from
  it.** A plot can front two roads, and *every* road edge takes the larger road-side
  setback — a corner plot collapsed to one direction produces a wrong envelope, not a
  wrong label. `facing` is `road_edges[0]`, the primary frontage; `corner_plot` means
  two *adjacent* edges, and is false for a through plot (opposite edges), which is a
  different thing. Both are serialised so consumers and `| jq` still see them, and
  both are refused as inputs — a `facing` that disagrees with `road_edges[0]` raises
  rather than riding along stale. The model is never shown them: `computed_field`
  keeps them out of the validation schema.
- **`DerivedFieldsAreOutputOnly` is why the IR can read its own JSON.**
  `computed_field` and `extra="forbid"` contradict each other — derived values are
  serialised but rejected on input, so a strict model refuses its own output and the
  contract becomes write-only. Dropping is right for arithmetic (`area_sq_m` cannot
  meaningfully disagree; a mismatch means a stale file). `PlotSpec` keeps the stricter
  rule because `facing` names a *choice* someone might have hand-edited.
- **`plan.py` has no coordinates, and that is the architecture.** Decision 1: the
  LLM decides *what rooms, how big, next to what, which sector*; the solver decides
  where. An `x` on a `RoomSpec` moves the hardest part of the problem to the component
  worst equipped for it, so a test asserts their absence. `RoomSpec` carries **two**
  areas — `min_area_sq_m` is the legal floor, `target_area_sq_m` is what it should be
  — because a solver given one number produces either cramped plans or infeasible
  ones; the gap is its slack. `min_width_m` and `max_aspect` both exist: area alone
  permits a 1 m × 9 m bedroom.
- **`SpaceKind` is a superset of `RoomKind`, enforced by a test.** `RoomKind` is what
  a person types; hall, kitchen and corridors are implied or never mentioned. A
  `RoomKind` with no counterpart would be silently dropped in expansion — the user
  asks for a pooja room and does not get one.
- `units.py` is the **only** place a non-metre number becomes metres. A conversion
  anywhere else is a bug. Indian inputs arrive in feet, sq ft, and gaj/yards.

### `rules/` — versioned data, never constants in a module

Decision 4's home. Postgres is the eventual store; until `store/` exists these are
JSON files loaded like prompts — by name, with a sha256, so an edit without a version
bump is detectable rather than silent. Every ruleset that shaped a result is stamped
into `Provenance.ruleset_versions` as `name@hash`.

**For Bengaluru the operative document is the Karnataka Municipalities Model Building
Bye-Laws 2017** (UDD 14 TTP 2017 (P-4), 28-10-2017), not NBC 2016. NBC is the national
*model* code; Karnataka adopted and adapted it, and a BBMP sanction is checked against
the Karnataka version — citing NBC was one level too abstract. Eight figures in
`spaces_v1.json` are now `verified: true` with a clause reference, read first-hand from
BBMP's BPAS portal.

Two of them had been **stricter than the law**: the second bedroom's minimum width
(2.4 m against the statutory 2.1 m) and the hall's (3.0 m / 11 m² against the
habitable-room floor of 2.4 m / 9.5 m²). A living room *is* a habitable room by
cl. 5.1.2.3, so those were practice figures wearing a legal label, and they were
rejecting plans the law permits. **Minimums are legal floors; targets are practice** —
keeping a convention in the `min` column silently narrows what can be built.

Note cl. 1(3): FAR, setback, coverage and height come from the **Master Plan and Zonal
Regulations**, not the bye-laws. That is why `setbacks_v1.json` is a separate question
and remains entirely unverified.

`clarify_v1.json` decides which assumptions earn a question. It is data because the
judgment in it is a *product* judgment that changes without the model changing: which
fields are expensive to get wrong, which are derived from which, and how many
questions a person will tolerate. Tuning it must never require a deploy of `llm/`.

### `envelope/` — stage ②, deterministic

**\*BUILT, but its rule data is unverified and it refuses to run by default.** The
arithmetic is done and tested; the BBMP figures behind it are transcribed from
secondary sources and marked `verified: false`, so `build_envelope` raises
`RulesetUnverified` unless passed `allow_unverified=True`. A wrong setback looks
exactly like a right one, and the output is a number someone acts on.

Two frames, and conflating them rotates every plan ninety degrees: a `PlotSpec` is
**road-relative** (`width_m` is frontage), an `Envelope` is **compass-aligned** (x
east, y north). `geometry.plot_extent` is the only conversion.

It declines in four typed ways — `CityUnknown`, `NoRulesetForCity`,
`RulesetUnverified`, `EnvelopeInfeasible` — each carrying the numbers. **A
neighbouring city's bye-laws are not a conservative default; they are a different
answer.** "No envelope, because the city is unknown" is a finding stage ④ acts on.

**Road width is two constraints, not one.** RMP 2031 §5.2(iii) demotes the FAR band
when the abutting road is narrower than the band expects — a 500 m² plot on a 6 m road
earns a 120 m² plot's FAR — and §5.2(iv) caps storeys by road width *irrespective of
the FAR earned*. `Envelope.max_far` uses **`base_far`**, never `total_far`: the excess
requires buying TDR, and budgeting against it sizes a programme that cannot be built.

Known gaps, all recorded in `setbacks_v1.json`: **the FAR figures are read first-hand
from RMP 2031 Vol. 6 Tables 6/7, but every page of that document is stamped
'(Draft)'** — published for objections in 2017, notification not established. If it
was never notified, Bengaluru still sanctions against RMP 2015 and these numbers are
wrong. Three candidates exist for the same 30x40 plot: 0.75 (Bye-laws 2003), 1.50
(RMP 2031 draft, what is encoded), 1.75 (reported for RMP 2015). Separately: Table 4
setbacks key on site *depth*, not area, so those bands are an approximation; the FAR
tables split by Planning Zone A/B with no locality-to-zone map; and only Bengaluru is
mapped.

### `program/` — stage ③, deterministic floor

Same role `llm/fallback.py` plays for ①: a real expansion that runs with no model, no
network and no key. The LLM version reads nuance these rules cannot — "joint family",
an elderly parent near the entrance — but a user whose call failed should still see a
house.

- **Emits a graph, never coordinates.** Decision 1, at the stage most tempted to break
  it.
- **Bedrooms open off the corridor, never the hall.** That is what circulation is for,
  and why privacy survives the tiling.
- **Kitchen and every bathroom carry a hard `SEPARATED` edge.** A toilet sharing a
  kitchen wall is the one placement every Indian client objects to, Vastu or not.
- **`parking_bays` is authoritative, not `extra_rooms`.** The model lists
  `car_parking` in `extra_rooms` on some runs and not others for the same brief;
  building the bay from that choice swung the programme 14% between identical inputs.
  `extra_rooms` records what the user *asked for*; the count says what to build.

### `solver/` — stage ⑤ Stage A only

Slicing trees: a binary tree of cuts whose leaves are rooms. Placing it divides one
rectangle recursively, so **the tiling is gap-free by construction** — no solver is
asked to rediscover that rooms must not overlap, which is what made single-stage
CP-SAT fail decision 2's twenty-second test.

- **Ranking is lexicographic: `(unbuildable, penalty)`.** A room below its legal
  minimum is not a worse version of a misplaced one, and no quantity of satisfied
  preferences compensates. Weighting alone was not enough — hill-climbing traded two
  unbuildable rooms for enough Vastu wins to come out ahead on total, and produced a
  2.2 m² hall. A large weight is not a substitute: whatever is big enough today stops
  being big enough as the room count grows.
- **Allocation shrinks towards minimums, never scales targets.**
  `slicing.effective_areas` gives each room its minimum plus a share of the leftover
  slack. Scaling targets uniformly pushes a room whose target barely exceeds its
  minimum *below* it, in a programme feasibility had already passed — which is
  exactly what `RoomSpec` carrying two areas was for, and the solver ignored it for
  long enough to ship the bug.
- **Hill-climbing swaps occupants, never geometry.** The rectangles stay where the
  tree put them and only the room-to-rectangle assignment changes, so no swap can
  open a gap.
- **The shortlist is hill-climbed *then* re-ranked**, because a candidate that looks
  mediocre before climbing can win after it.
- **A sector-aware shuffle was tried and removed** — see `DECISIONS.md`. It measured
  as a win against the *broken* objective and vanished once ranking was fixed.
- **Stage B's cm grid must sit *inside* the envelope, and its outer edges snap back
  onto the envelope's exact floats.** `tune` solves on 1 cm integers; an envelope
  derived from feet does not land on that grid, so rounding to nearest left every tuned
  layout a fraction of a centimetre short of the wall — `Layout` called it a gap and
  `solve` discarded it silently. **Stage B was dead for every plot quoted in feet**,
  which is every brief the product is for. Ceil the minimum and floor the maximum so
  snapping outward can only *grow* a boundary room: rounding to nearest puts the grid
  outside and snapping back shaves a room the model just proved legal. Interior cuts
  stay on the grid and cancel between neighbours.
- **Hill-climb the tuned layout, not just the generated one.** The omission was
  invisible while the tuned path never ran, and fixing the grid alone made the largest
  brief measurably worse.

- **A room can also be too big, and `score` says so.** Exact tiling fixes the total
  area, so surplus the programme never asked for is forced into some room — 48% of the
  footprint on a 50x80, which produced a 30 m² bathroom. The penalty makes it visible
  in `violations`; it does not fix it, because every candidate carries the same
  surplus. Both a relative *and* an absolute test must fire, or a 5 m² pooja room
  against a 2.5 m² target gets flagged alongside it. See `DECISIONS.md` question 7.

`max_aspect` is per-kind data, not a constant. A corridor is *supposed* to be
elongated; flagging one as "a corridor, not a corridor" was the rule mistaking the
shape for the defect.

### `feasibility/` — stage ④, and the reason ⑤ is allowed to fail

*Explain, and offer options that were actually measured.* "Infeasible" is useless to a
plot owner; "your ground floor is 90% packed — moving the car porch into the setback
makes 11 of 60 layouts legal" is a decision they can take.

- **Options are measured, never guessed.** Each candidate change is applied and the
  solver run against it. Affordable only because Stage A generates in microseconds
  and Stage B *proves* infeasibility in milliseconds.
- **The verdict is a solve probability, not a hit rate.** 5 in 60 sounds dire and is
  not: ⑤ tries 24 topologies, so it lands a plan 88% of the time. 1 in 60 is 33%, and
  that is the number a user should hear.
- **Nothing is silently reduced.** A plot owner who asked for a separate dining room
  is told what dropping it costs, not quietly deprived of it.

### `llm/`

- Always schema-constrained output. Never parse free text.
- **The clarification selector lives in `intent.py`, not a stage of its own.** It
  runs after extraction, reads `brief.assumptions`, and usually returns nothing.
  **The tier gates; confidence only sorts what survives.** Each tier carries the bar
  under which it is worth interrupting — 0.7 blocking, 0.5 consequential, never for
  the rest. A brief stating its dimensions, orientation and bedroom count asks nothing
  at all. Taking the bottom N by confidence instead always finds something to ask,
  because there is always a lowest row; that is not the same as a row worth asking
  about, and it is how a clarifier turns into a form. **The budget is a ceiling on an
  already-gated set, not a quota to fill.** Questions never block — the Brief is
  complete before they are selected, and each carries the value that stands if it goes
  unanswered.
- **One graph, two uses, in `clarify_v1.json`.** `depends_on` records that a value was
  read off another. It supplies the **ceiling** (`cap_derived_confidence`: no
  inference outranks its source, transitively, by the minimum over parents — `floors`
  hangs off both `plot size` and `bedrooms`) and the **collapse** (a guess whose
  parent is also a guess is not worth its own question; answering the parent
  re-derives it). Both roots matter: patching this per-field at the site that produced
  it is what missed `plot size` the first time. The ceiling runs on the model's output
  as well as the parser's — the model launders too.
- Prompts are **versioned files** in `llm/prompts/`, referenced in provenance
  by name *and* sha256 — a silent prompt edit must be detectable.
- 2 retries on schema failure, then **deterministic fallback**. The LLM being
  down must never break generation entirely.
- **The suite is isolated from your `.env`.** An autouse fixture clears `NAKSHA_*`
  from `os.environ` *and* neutralises pydantic-settings' own `env_file` read — they
  are independent sources and clearing one fixes nothing. Without it a single
  `NAKSHA_INTENT_PROVIDER` in a developer's `.env` silently redirects every
  provider-inference test; eight failed exactly that way.
- **`allow_fallback=False` / `--no-fallback` opts out, and exists for one reason:**
  when you are evaluating the model rather than serving a user, a silent substitution
  is the worst outcome available — the output looks fine and answers a different
  question. Serving a user, the default stands. Check the stderr line either way:
  `[model] …` means the model answered, `[fallback] …` means it did not.
- Schema retries and transport retries are separate budgets. The SDK already
  retries 429/5xx twice; do not layer our schema retries on top of that.
- Every call traced when Langfuse is configured; a no-op when it isn't.

**Deviation from the original spec, on purpose:** the design doc named `instructor`
for structured output. We use each SDK's native structured outputs instead —
`messages.parse(output_format=…)` on Anthropic, `responses.parse(text_format=…)` on
OpenAI. Both enforce the JSON schema server-side and return a validated Pydantic
instance. Same guarantee, one fewer dependency. If you reintroduce `instructor`, say
why here.

### `llm/providers/` — the vendor seam

**Model-agnostic by requirement.** naksha runs on any supported model — Claude,
GPT, or anything served behind an OpenAI-compatible endpoint. This is a product
constraint, not a preference, and it is the reason an abstraction exists here when
the "no abstraction tax" rule kept LangChain out. The tax is paid once, at one seam,
for a stated requirement.

`StructuredCaller` is the whole interface: *give me an instance of this Pydantic
class, or raise one of four normalised errors.*

```
ProviderOutputInvalid   retryable — bad schema, truncated, or no parsed object
ProviderRefused         safety classifiers declined
ProviderUnavailable     auth, network, rate limit, 5xx
ProviderNotInstalled    the SDK for this provider isn't installed
```

- **`intent.py` names no SDK.** If a vendor exception reaches the stage, the seam
  has a hole — retry and fallback logic would then silently work for one SDK only.
  `tests/test_providers.py::TestAdaptersAgree` is the guard.
- **Routing is by model id**, because that is the one thing a user always knows:
  `claude-*` → anthropic, `gpt-*`/`o1`/`o3`/`o4` → openai. `NAKSHA_INTENT_PROVIDER`
  overrides for ids the table can't place.
- **`claude_code` is opt-in only and never inferred.** `claude-opus-5` means the
  API. Routing an API model id to a local subscription-backed install would change
  where the work runs, what it costs and which limits apply — that must be a
  decision, not a default.
- **Provider SDKs are extras** — `".[anthropic]"`, `".[openai]"`, `".[claude-code]"`,
  or `".[all]"`. Imports are deferred into the factories, so one installed SDK is
  enough and a missing one is a clear `ProviderNotInstalled`.
- **Provenance records `provider` alongside `model`.** Regressions are often
  provider-shaped rather than model-shaped.
- **Never guess an SDK's surface.** Both adapters were written against signatures
  read off the installed package. Vendors disagree in ways you will not predict —
  Anthropic raises a bare `TypeError` at *request* time for a missing key while
  OpenAI raises `OpenAIError` at *construction*, and both had to be normalised
  before the fallback path worked for either.
- Adding a provider: implement `StructuredCaller`, register a factory and a prefix.
  Nothing in `intent.py` changes.

**Do not add:** LangChain · image-generation models (they produce non-geometric
garbage — walls don't close) · a custom geometry kernel.

---

## CONVENTIONS

- Type hints everywhere. `from __future__ import annotations`.
- Pydantic models for all boundaries. No bare dicts crossing a module edge.
- `snake_case` Python. No magic numbers — dimensions come from `rules/` once it exists.
- Docstrings explain **why**, not what.
- `pytest` with `hypothesis` for anything geometric or numeric — property-based
  testing catches the invariant violations that example-based tests miss.
- Golden set (`tests/golden/`) runs on every prompt change. In a non-deterministic
  pipeline you cannot otherwise tell improvement from regression.
  **This is architecture, not QA.**
- Tests must pass with no network and no API key. Live-model tests are marked
  `@pytest.mark.live` and deselected by default.

---

## MODEL

Default `claude-opus-5`; set `NAKSHA_INTENT_MODEL` to anything supported. The
provider follows from the id, so switching vendors is one env var:

```
NAKSHA_INTENT_MODEL=claude-opus-5   + ANTHROPIC_API_KEY
NAKSHA_INTENT_MODEL=gpt-5.2         + OPENAI_API_KEY
NAKSHA_INTENT_PROVIDER=claude_code  + a Claude Code login   (dev only, no key)
```

`claude_code` runs against a Pro/Max subscription through a local Claude Code
install. It keeps the schema guarantee — `ClaudeAgentOptions.output_format` takes
the Messages API's `json_schema` shape and the result carries `structured_output` —
so it is a real provider, not a prose-parsing fallback. It is still development
only: a process per call, a CLI that must exist on the box, subscription limits
rather than API limits, and `asyncio.run` under the hood, so it refuses to run
inside an existing event loop. The Celery worker in phase 7 needs an API provider.

Construct clients with **no** `api_key` argument. Each SDK has its own resolution
chain — Anthropic reads `ANTHROPIC_API_KEY`, then `ANTHROPIC_AUTH_TOKEN`, then an
`ant auth login` profile; OpenAI reads `OPENAI_API_KEY` and `OPENAI_BASE_URL`.
Passing an explicit key breaks the profile path and the gateway path.

An unset env var does not mean there are no credentials. A *set but empty* one is
worse than unset: it outranks the profile and authenticates as empty.

**`.env` needs an explicit bridge.** `config.py` calls `load_dotenv` at import,
anchored to the project root so it works from any cwd. This is not redundant with
pydantic-settings' `env_file`: that only populates `Settings`, while the provider
SDKs read `os.environ`. Without the bridge a correctly-filled `.env` produces no
error and no key — every request fails auth and silently lands on the fallback.

Adaptive thinking is on by default on Claude Opus 5 — leave it on. Disabling it is
cheaper-looking but carries a documented failure mode where `<thinking>` tags leak
into visible output.

---

## SCOPE — v1

**In:** rectangular plots · **ground + 1** · 2–3BHK · Vastu scoring · wall drag +
numeric dimensions · door/window adjustment · DXF/PDF export · candidate gallery.

*Was "ground floor only", which contradicted the target market.* Measured: a 3BHK does
not fit one floor until **40x60 ft**, and even a 2BHK is 98% packed on a 30x40 — the
commonest site size in south-Indian layouts and `fallback.py`'s own default. Serving
30x40 and G+1 are the same decision. The cost lands on ⑥ REFINE: stairs, floor-to-floor
alignment, and a drawing per storey.

**Out:** irregular plots · multi-floor · 3D · BIM/IFC · collaboration · cost
estimation · structural analysis · curved walls.

The editor's job in v1 is **adjustment, not authoring**. The AI authors; the user
adjusts.

---

## LEGAL POSITIONING

In India, plan approval requires a registered architect's or engineer's stamp. naksha
produces **schematic / concept stage** drawings, not sanction drawings. This must
appear in the product UI, not only in the ToS.

Vastu output is **advisory**. Always show which ruleset version produced a score.
Schools genuinely disagree; the user or their consultant owns the verdict.
