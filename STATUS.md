# STATUS — naksha

## Start here — resuming in a new session

Read in this order, then run the commands below:

1. **`CLAUDE.md`** — how to work here. The four decisions, module rules, conventions.
   Non-negotiable; if a change contradicts one, stop and flag it.
2. **`STATUS.md`** (this file) — where we are, what is blocked, what is next.
3. **`DECISIONS.md`** — why the code is shaped this way, every bug found and what it
   taught, corrections, and **the open questions that need a human**.

```bash
cd naksha
.venv/bin/pytest -q                                  # 470 tests, no network, no key

# The whole pipeline as it stands: brief → questions → envelope → room list
.venv/bin/python -m app.cli -s -e -P --allow-unverified --fallback-only \
  "30x40 east facing site in Whitefield, 3BHK with pooja room"

.venv/bin/python -m app.cli -i                       # interactive, multi-line ok
```

```bash
# The real model. Needs ANTHROPIC_API_KEY in .env, or -p claude_code for a subscription.
.venv/bin/python -m app.cli -s -e -P --allow-unverified --no-fallback \
  "40x60 for a joint family"
```

`--fallback-only` skips the model entirely; `--no-fallback` is its opposite — fail
loudly rather than quietly substitute the regex parser, which is what you want while
evaluating the model. **Always check the stderr line:** `[model] …` means Claude
answered, `[fallback] …` means you are looking at regex output. `--allow-unverified`
is required for `-e` because the bye-law figures are unchecked — see blocker 1.

**Briefs worth trying:** `40x60 4bhk g+1 in Bengaluru with study and car parking,
strict vastu` · `20x30 2bhk in Bengaluru` (tight) · `30x40 north facing corner plot
3bhk in Bengaluru` (two road setbacks) · `make me a house` (no city → no envelope) ·
`40x60 for a joint family` (needs the model; the regex reads nothing).

---

Where the build actually is.

**Last updated:** 2026-09-06 · 470 tests passing, offline, no key ·
~3,700 lines of app code, ~2,700 lines of tests

> **naksha has not yet produced a floor plan.** Rooms are sized but unplaced —
> nothing has coordinates. Two and a half of eight stages are built. The
> product's central claim — that a two-stage solver makes plans people accept — is
> asserted in the design doc and tested by nothing in this repo.

---

## Pipeline

| Stage | Status | Notes |
|---|---|---|
| ① INTENT | **built** | `text → Brief` + ≤3 clarifying questions |
| ② ENVELOPE | **built, gated** | Arithmetic done. Refuses to run — rule data unverified |
| ③ PROGRAM | **deterministic done** | LLM version not started — `"my mother lives with us"` goes nowhere |
| ④ FEASIBILITY | **done** | explains, and measures each option by running the solver |
| ⑤ LAYOUT | **done** | slicing tree (A) + CP-SAT (B). ~4 s a plan |
| ⑥ REFINE | not started | walls → doors → fixtures → windows → dims |
| ⑦ VALIDATE | not started | geometry · NBC · circulation · vastu · fit |
| ⑧ CRITIC | not started | rerank + rationale, behind a flag |

---

## ① INTENT — done

`text → Brief`, schema-constrained, 2 schema retries, then a deterministic offline
parser so a dead API is not a dead product. Versioned prompt hashed into provenance.
Model-agnostic behind a four-error provider seam (`anthropic` · `openai` ·
`claude_code`).

**Every guess is visible.** Stage ① fills blanks rather than emitting nulls, and each
fill is a structured `Assumption` with a confidence — a null nobody chose just pushes
the same guess into a stage where nobody can see it.

**A clarifier that usually asks nothing.** Tier gates, confidence only sorts what
survives: 0.7 for blocking fields, 0.5 for consequential, never for the rest. A
dependency DAG collapses questions one answer would settle, and caps a derived value's
confidence at its source's. Budget of 3 is a ceiling, not a quota.

```
30x60 east facing 2bhk in Pune            → asks nothing
make me a house                           → city, plot size, bedrooms
30x40 north facing corner plot in Bengaluru → road edges
```

## ② ENVELOPE — built, gated

`Brief → buildable rectangle + FAR/coverage/storey caps`. Deterministic; no model, no
network. Two frames kept apart deliberately: a `PlotSpec` is road-relative
(`width_m` is frontage), an `Envelope` is compass-aligned. Conflating them rotates
every plan 90°.

Declines in four typed ways, each carrying its numbers — `CityUnknown`,
`NoRulesetForCity`, `RulesetUnverified`, `EnvelopeInfeasible`. A neighbouring city's
bye-laws are not a conservative default; they are a different answer.

```
30x40 in Whitefield, 9m road   → 7.69 × 7.14 m · FAR 1.5 · ≤2 floors · built ≤167.2 m²
same plot, corner              → 5.14 × 7.69 m   (both roads take the front setback)
30x40 in Pune                  → NoRulesetForCity
```

---

## ⑤ LAYOUT — built, and Stage B was dead until 2026-09-06

Both stages exist and run. The finding of this session is that **Stage B was silently
producing nothing for every plot quoted in feet** — its CP-SAT model works on a 1 cm
grid, a feet-derived envelope does not land on it, so `Layout` rejected every tuned
result as a gap and `solve` fell through to Stage A without a word. Measured: 0 of 24
tuned layouts accepted on each feet brief, 23 of 24 on a metric one. Fixed by flooring
the grid inwards and snapping the outer edges back to the envelope's exact floats; see
`DECISIONS.md`. Hill-climbing was also missing from the tuned path, and adding it was
necessary — the grid fix alone made the largest brief worse.

Effect, four briefs, before → after:

| brief | before | after |
|---|---|---|
| 40x60 3BHK + pooja | 0 unbuildable, penalty 160, 5/10 edges | 0, penalty 130, 8/10 |
| 30x50 3BHK | **2 unbuildable**, penalty 360, 6/9 | **0**, penalty 200, 4/9 |
| 30x40 2BHK | **1 unbuildable**, penalty 210, 6/8 | **0**, penalty 70, 6/8 |
| 50x80 4BHK + study | 0, penalty 285, 9/12 | 0, penalty 130, 9/12 |

**Every brief now produces a legal plan.** That is new — two of the four were placing
rooms below their statutory minimums.

Two things are known and open, both traced to `Layout` requiring exact tiling of the
*maximum permitted* footprint: adjacency tops out well short of the graph (question 6)
and surplus area is forced into rooms, producing a 30 m² bathroom on a 50x80
(question 7). Neither is a tuning problem and both change the IR, so both are in
`DECISIONS.md` under "these need you, not me".

## Blockers

**1. FAR is unresolved, and it is ③'s room budget.** Three candidate figures for one
30×40 plot: **0.75** (BBMP Bye-laws 2003), **1.50** (RMP 2031 Vol. 6 Table 6 — what is
encoded, read first-hand from the BDA PDF), **1.75** (widely reported for RMP 2015).
Every page of the RMP 2031 document is stamped **"(Draft)"**; published for objections
in 2017, notification not established. If it was never notified, Bengaluru still
sanctions against RMP 2015 and the encoded numbers are wrong by ~2×. *Needs someone who
has sanctioned a plan recently.*

**2. All 19 rule bands are `verified: false`.** `build_envelope` raises
`RulesetUnverified` unless passed `allow_unverified=True`. Deliberate: a wrong setback
looks exactly like a right one, and the output is a number someone acts on.

**3. Only Bengaluru is mapped.** Pune, Hyderabad, Chennai, Mysuru all return
`NoRulesetForCity`.

**4. Secondary gaps.** BBMP Table 4 keys on site *depth*, not area — the area bands are
an approximation. RMP Tables 6/7 split by Planning Zone A/B with no locality-to-zone
map; Zone A assumed throughout.

**5. ~~`claude_code` adapter misclassifies.~~ Investigated — not a bug.** The SDK
documents `api_error_status` as carrying the HTTP status "when `is_error` is True and
`subtype` is 'success'", so `error result: success` is the CLI's shape for a *failed
API call* (429/500/529). `ProviderUnavailable` is the right classification and skipping
schema retries is right — no number of them fixes a rate limit. **③ inherits no hole.**
Only the wording was wrong, and is now translated: the raw text read "error result:
success", which sent a reader hunting for a bug in their own brief. Subscription rate
limits are the usual cause; use an API key provider for repeated runs.

**6. The live golden set has never been run end to end.** Three cases were added and
verified by hand; `pytest -m live` has not been executed as a suite.

---

## Decisions taken, and why

- **`road_edges` is stored; `facing` and `corner_plot` are computed.** A corner plot
  collapsed to one direction under-sets-back its second road — a wrong envelope, not a
  wrong label. Both are serialised for consumers but refused as inputs.
- **`base_far`, never `total_far`.** The excess needs TDR bought. Budgeting against it
  would size a programme that cannot legally be built.
- **`city` is `blocking`.** It was `defaultable` while nothing consumed it; ② cannot
  select a ruleset without it.
- **Rules are data with citations.** Each band names its source and carries
  `verified: false` until checked. `load_ruleset` warns while any remain.
- **Enum enforcement is proven, not assumed.** Closed vocabularies are enforced both in
  the schema sent to the provider and on parse. `test_schema_enforcement.py` discovers
  models and enums *by inspection*, so ③'s `RoomSpec` and `AdjacencySpec` are covered
  the moment they exist.

---

## Next

**Recommended: a vertical slice to a rendered plan.** One hardcoded brief, straight
through ③ → ⑤ → ⑥, to rectangles and walls on screen. Ugly is fine.

The reasoning: ① and ② are the *least* risky third of the pipeline. An LLM reading
"3BHK" is not where this product succeeds or fails. The real question is whether the
slicing-tree + CP-SAT approach produces plans Indian plot owners accept — and finding
that out costs one week now versus three months later.

Order:
1. ~~Fix the `claude_code` misclassification.~~ Done — it was not a
   misclassification; see blocker 5. Message translated, 3 tests.
2. ~~`ir/plan.py` — `RoomSpec`, `AdjacencySpec`, `Program`.~~ **Done.** Plus
   `SpaceKind` (20 members, superset of `RoomKind`), `Sector` (8 + brahmasthan) and
   `Relation`. 22 tests, including one asserting no coordinates leak into the model.
3. ~~`rules/spaces_v1.json` — NBC minimums.~~ **Done**, 20 spaces, all
   `verified: false`.
4. ~~③ deterministic expansion + feasibility gate.~~ **Done** — `-P` on the CLI.
   The LLM version of ③ is still to write.
5. ⑤ LAYOUT Stage A — slicing tree. **The real risk starts here.**
4. ⑤ LAYOUT Stage A only — slicing tree, gap-free by construction. Skip CP-SAT tuning.
5. ⑥ minimal — wall lines and a room-name label. SVG to a file.
6. Look at it. Decide whether the thesis holds.

**Alternative, if stage ① is itself the artefact** rather than a step toward a product:
verify the FAR figures, map the remaining cities, run the live golden set, and stop.
That is roughly a week and leaves something coherent and finished.

*This fork is still open and shapes everything after it.*
