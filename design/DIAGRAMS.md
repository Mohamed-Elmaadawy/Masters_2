# Diagrams

Thirteen Mermaid diagrams, all generated, none hand-edited. Start with
`overview.mermaid` — six boxes, the whole project at a glance — or `stage_flow.mermaid`
if you want the 8 LLM stages specifically, happy path only. The other eleven each pick
one layer and show it in full: five describe the **schema and the conceptual pipeline**,
six describe the **implementation** — the code that actually runs. (Thirteen total —
`overview` and `stage_flow` are the two "big picture" ones, the rest are the eleven
detailed ones.)

Regenerate both sets after any change to the code they describe:

```bash
python -m design.generate_diagrams        # the 5 schema/pipeline diagrams
python -m design.generate_arch_diagrams   # overview.mermaid + the 6 architecture diagrams
```

Both scripts **validate as well as generate** — a generation failure is a real signal,
not a nuisance. What each one checks is listed per diagram below. Their own suites:

```bash
python -m design.test_generate_diagrams
python -m design.test_generate_arch_diagrams
```

All thirteen `.mermaid` files below live in `design/diagrams/`. They render directly in
GitHub, VS Code (Markdown Preview Mermaid Support), and <https://mermaid.live> for
export to SVG/PNG.

---

## Start here

| If you want to know… | Read |
|---|---|
| what this project even is | `overview.mermaid` — six boxes, nothing else |
| the 8 LLM stages, no branches or config | `stage_flow.mermaid` |
| what the pipeline does, conceptually | `pipeline.mermaid` |
| what the data looks like | `models.mermaid` |
| what the code looks like | `repo_map.mermaid`, then `modules.mermaid` |
| how one requirement is actually processed | `runtime.mermaid` |
| what happens when a call fails | `call_stage.mermaid`, then `paths_failure.mermaid` |
| how a stage is configured and wired | `stage_wiring.mermaid`, `config_flow.mermaid` |

---

## The big picture — `design/generate_arch_diagrams.py`

### `overview.mermaid`
The whole project in six boxes: input → document-level checks → the per-requirement
pipeline (one box, not five) → LLM providers → human-in-the-loop → the run record on
disk. No field lists, no per-stage detail, no subgraphs.

*Mostly declared* — six boxes chosen by hand, the same way a one-paragraph project
summary is chosen by hand. The two stage counts in it (`2 stages`, `6 stages`) ARE pulled
live from `schemas.py`, so those numbers can't drift even though the boxes around them
are curated. If you need more than this diagram gives you, that's the other twelve's job.

### `stage_flow.mermaid`
The 8 LLM stages in the order they actually fire on a normal run — the two document-
level stages, then per requirement: classify → check quality → (passed? → strategy →
tests, or issues found → ask → human answers → rewrite → back to quality check). No
config, no retries, no revision cap, no notes.

*Declared, but the one fact worth getting right is checked against real code, not
hand-copied.* Which per-requirement stage receives `ConsistencyReport`/
`DependencyReport` is read off the real `StageFns` Protocol parameter names with
`inspect.signature` — the exact fact that `pipeline.mermaid` (below) currently draws
**wrong** (see the note on `pipeline.mermaid`).

---

## Schema and pipeline — `design/generate_diagrams.py`

### `pipeline.mermaid`
Stages 0–4 as boxes, with the schema type carried along each arrow, plus the notes that
explain why both branches converge on a plain `Requirement` and why `RefinedRequirement`
is an audit record rather than a transport type.

*Declared, validated:* every schema type named on an edge must still exist in
`schemas.py`. Does **not** catch a stage whose input type changed to another type that
also still exists — and it currently hasn't: this diagram draws `ConsistencyReport`/
`DependencyReport` flowing into the **Classifier**. That predates the 2026-08-08
document-context-wiring change, which moved that context onto the Quality Checker (both
reports) and the Strategy Selector/Test Generator (dependencies only) — see
`stage_flow.mermaid` for the corrected, code-checked version. Not fixed here yet;
flagged rather than silently patched.

### `models.mermaid`
Every model and enum with its fields, computed fields and methods, plus containment and
reference arrows.

*Fully introspected* from Pydantic's `model_fields` — cannot drift.

### `paths_requirement.mermaid`
Every route one requirement can take to a successful terminal outcome: the
check → question → answer → rewrite → re-check loop, the revision cap, and both cap
branches.

### `paths_document.mermaid`
Every route a document can take through the two document-level stages, including all
four combinations of the two independent failures.

### `paths_failure.mermaid`
What happens when a stage fails or the process is interrupted, and why a *successful*
retry leaves no trace while a failed one does.

*Declared, validated:* every `RunOutcome` and `DocumentOutcome` member must have a path
ending in it, across the three trees. Add an outcome without drawing its path and the
build fails.

---

## Architecture — `design/generate_arch_diagrams.py`

### `repo_map.mermaid`
Every directory and every module, with a one-line role for each and live file counts.

*Introspected tree, declared roles:* a `.py` module under `design/` or `orchestrator/`
with no declared role **fails the build**, and so does a role left behind for a deleted
file.

### `modules.mermaid`
Which module imports which, grouped into the schema layer, the runtime layer and the
test suites, with `pydantic` / `requests` / `pyyaml` drawn as third-party leaves.

*Fully introspected* with `ast.parse` — cannot drift. The one structural fact worth
reading off it: `design/` never imports `orchestrator/`.

### `stage_wiring.mermaid`
The eight stages end to end, one row each: YAML config key → prompt file →
`stages.py` factory → `StageFns` field and its Protocol → output model in `schemas.py`,
with every row converging on `ProviderAdapter.complete()` below and
`call_stage`/`call_document_stage` above.

*Declared rows, every cell checked* against the real `ALL_STAGES`, `StageFns` fields,
`stages.py` factories, Protocol classes, schema models and prompt files on disk. A ninth
stage anywhere — including a `make_*_fn` in `stages.py` that no row references — fails
the build until its row is added.

### `runtime.mermaid`
The orchestrator's call graph, `python -m orchestrator.cli` to disk: config load and
resolve, adapter construction, `StageFns`/`HumanFns` assembly, `run_document` →
`run_document_stages` / `run_requirement` → `_run_refine_loop`, the incremental writes,
and the two entry points that exist but have **no CLI wiring in v1**
(`resume_document`, `retry_document_stage`).

*Declared, validated by name:* every node names a real `module.attribute`, checked at
generation time. The **edges are not traced** — a call deleted while both functions
survive is not caught. Same residual gap `generate_diagrams.py` names for
`pipeline.mermaid`, recorded rather than hidden.

### `call_stage.mermaid`
One stage call in detail: throttle, the four exception branches
(`StageCallFatal` / `StageCallPartial` / `StageCallFailed` / anything else),
schema validation, the `requirement_id` check, `extra_check`, backoff, and exhaustion
into `StageFailed`. Each branch is labelled with the `AttemptResult` it records and the
`FailureKind` it raises.

*Declared, validated:* every `AttemptResult` and `FailureKind` member must appear
somewhere in it, and a branch naming a member the schema doesn't have fails the build.

### `config_flow.mermaid`
`run_config.yaml` → `RunConfig` → `resolve_run_config` (defaults + overrides, the
output-mode capability check, prompt hashing) → `ResolvedRunConfig` → the four helpers
that turn it into `run_document`'s arguments → what a finished run leaves on disk
(`run_config.json`, `document.json`, `requirements/<sha256>.json`).

*Declared, validated:* every model and function name it references is checked to still
exist.

---

## Conventions

- **Never edit a `.mermaid` file by hand.** Every one is overwritten on the next run;
  the header of each says so. Change the generator instead.
- Colour is meaning, and consistent across all thirteen: indigo = schema/data,
  teal = document-level stage, amber = per-requirement stage, pink = human in the loop,
  purple = provider adapter, red = failure, deep purple = terminal state,
  green = test suite, blue = file or I/O, grey = decision.
- Two generators, not one, on purpose. `generate_diagrams.py` imports `design.schemas`
  and nothing else, so the schema diagrams don't depend on the orchestrator importing
  cleanly; `generate_arch_diagrams.py` imports the orchestrator, walks the repository
  and parses source files. Different dependency footprints, different failure surfaces.
