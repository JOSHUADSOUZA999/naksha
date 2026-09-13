# naksha

AI floor-plan generation for Indian residential plots. Vastu-aware, bye-law-aware,
editable output. Built for plot owners and small builders in tier-1/2 India — the
30x40 and 40x60 sites that make up most south-Indian layouts.

> **Schematic, not sanction.** Plan approval in India requires a registered architect's
> or engineer's stamp. naksha produces concept-stage drawings. The Vastu output is
> advisory, and the ruleset version that produced any score travels with it.

---

## What actually works today

`text → Brief → buildable envelope → room graph → placed rooms → walls, doors, windows,
fixtures → a list of what is wrong with it`

```bash
naksha-intent -s -e -P --allow-unverified --fallback-only \
  --svg /tmp/plan.svg "40x60 3bhk in Bengaluru with pooja room"
```

Stages ①–⑦ run end to end and produce a drawing per storey: walls at their real
thickness, doors with swings, windows sized to a tenth of the floor they light,
sanitaryware and beds, and stage ⑦'s findings — every room reachable from the front door
without walking through a bedroom, every room above its statutory minimum *measured
inside its walls*. Only ⑧ CRITIC is unbuilt, and it is optional by design.

Plots from **25x40 up** come out legal. The **30x40 3BHK** — the commonest site in
south-Indian layouts — needs `--stilt`: the statutory 3.0 × 6.0 m car bay is a quarter
of its ground floor, and lifting the house off its parking is what makes it fit. A 20x30
is refused outright; 29.3 m² is buildable and the bay alone is 18.

**The rule data is the honest weak point.** Room minimums in `spaces_v1.json` are
verified first-hand against the Karnataka Model Building Bye-Laws 2017 with clause
references; the setback figures in `setbacks_v1.json` are **not**, so `build_envelope`
refuses to run without `--allow-unverified`. The FAR question is resolved — 1.75, read
first-hand from RMP-2015 Table 10 — but the setback bands and the window-area fraction
still need someone who has sanctioned a plan. `VERIFY.md` is the sheet for them.

This is **schematic, concept-stage** output. Nothing here is a sanction drawing, and
DXF/PDF export — what a registered architect would actually open — is not built yet.

---

## Reading order

| file | what it is |
|---|---|
| **`CLAUDE.md`** | How to work here. The four decisions, module rules, conventions. |
| **`STATUS.md`** | Where the build is, what is blocked, what is next. |
| **`DECISIONS.md`** | Why the code is shaped this way, every bug found and what it taught, and **the open questions that need a human**. |
| **`VERIFY.md`** | The sheet an architect would fill in to unblock the rule data. |

---

## Running it

Python **3.11+** (`StrEnum`, `datetime.UTC`, `Self`).

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env          # one API key, or NAKSHA_INTENT_PROVIDER=claude_code

pytest                        # 688 tests: no network, no key, independent of your .env
pytest -m live                # the golden set against a real model; needs credentials
```

JSON goes to stdout and status to stderr, so `naksha-intent "…" | jq` works.

---

## The four decisions

Everything in the codebase follows from these. They are argued in `CLAUDE.md`.

1. **The LLM emits a graph; the solver emits geometry.** Models reason well about
   relationships and badly about coordinates. The LLM never outputs x/y — a test
   asserts no coordinate leaks into `ir/plan.py`.
2. **Layout is two stages.** A slicing tree produces gap-free tilings *by
   construction*, then CP-SAT tunes the dimensions with adjacency already fixed.
   Single-stage CP-SAT with exact tiling could not find a feasible 10-room plan in
   20 seconds; two stages do it in under one.
3. **User edits become Constraints, never coordinates.** Dragging the kitchen east
   writes `Constraint(sector=SE, hard=True)`. Store coordinates and "regenerate"
   wipes the user's work.
4. **Rules are versioned data, not code.** Vastu schools disagree, setbacks differ per
   city, and codes get amended. Rules are JSON with a sha256 stamped into every plan's
   provenance, so an edit without a version bump is detectable rather than silent.

Only stages ①③⑧ use a model. Everything else is deterministic.

---

## Layout

```
backend/app/
  ir/           models · enums · units · envelope · plan · layout
                refined · validation                                ← THE CONTRACT
  llm/          intent · program · fallback · prompts/ · providers/
  rules/        clarify_v1 · setbacks_v1 · spaces_v1 · refine_v1    ← VERSIONED DATA
  envelope/     ② setback + FAR arithmetic, deterministic
  program/      ③ deterministic expansion, stacking, stilt
  feasibility/  ④ explain, with options that were actually measured
  solver/       ⑤ slicing.py (Stage A) · tuning.py (Stage B, CP-SAT) · score.py
  refine/       ⑥ walls · doors · windows · fixtures · setback spaces
  validator/    ⑦ circulation · access · sanitation · size · light · legality
  export/       svg.py                                ← DXF/PDF still to come
frontend/       Vite + React + react-konva viewer     ← runs on Node 20 (nvm)
```

`store/` and `api/` are not built. The tree matches the full intended layout so nothing
has to move later.

---

## Two things worth knowing before you change the solver

**Ranking is lexicographic, `(unbuildable, penalty)`.** A room below its legal minimum
is not a worse version of a misplaced one, and no quantity of satisfied Vastu
preferences compensates. A large weight is not a substitute — whatever is big enough
today stops being big enough as the room count grows.

**Measure the product, not the objective.** Three separate optimisations in this
codebase measured as wins and were reverted: a sector-aware shuffle that only helped
against a broken score, a graph-driven generator that was better per tree and worse
end to end, and a relative Stage B objective that was 2–2 on penalty while losing
adjacency. Worse: the adjacency ceiling was measured three times and blamed on the
representation before anyone noticed Stage B was not running at all. `DECISIONS.md`
records each one.
