# Wiring document analysis into downstream stages — Design

Date: 2026-08-08
Status: implemented (rev 3 — frozen document context + regression coverage for the allowed-but-fails retry)

## Purpose

`run_document_stages` produces a `ConsistencyReport` and a `DependencyReport`, but
nothing downstream reads them. `run_requirement` already receives both as parameters
(threaded through since the Task 11 harness work), and its own docstring says so
plainly: "not yet consumed by any per-requirement stage call ... wiring them into the
quality checker / strategy selector is future work, not silently invented here." This
closes that gap for three stages: Quality Checker, Strategy Selector, Test Generator.
`design/DESIGN_NOTES.md` already committed to the *shape* of this for stages 3/4
("§3/4 ... each is now also given that one requirement's own dependency links
(`DependencyReport.dependencies_for(id)`), not the whole `DependencyReport`") — this
design extends the same shape to the Quality Checker and turns the committed-but-unwired
plan for 3/4 into an actual call.

No schema change. `StageFns` callables are already untyped (`Callable[..., StageCallResult]`);
`ConsistencyReport.conflicts_for`/`DependencyReport.dependencies_for` already exist.
This is purely an orchestrator-layer (`orchestrator/pipeline.py`) change, plus the fake
stage fns and new scenarios in `orchestrator/test_harness.py`, plus two stale doc
diagrams.

## Decisions

### 1. Filtered context, never the whole report

Every call site gets `report.conflicts_for(req.id)` / `report.dependencies_for(req.id)`,
never `consistency_report`/`dependency_report` themselves. This is not new policy — it's
already how stages 3/4 were specified in `DESIGN_NOTES.md` §3/4 — but it now also
applies to the Quality Checker, which the original pipeline-overview ASCII diagram
(schemas.py's module docstring, `DESIGN_NOTES.md`'s "Pipeline overview") shows taking
the whole `ConsistencyReport`/`DependencyReport`. That diagram predates the
filtered-context decision and is simply stale; §1 below fixes it. Handing a whole report
to a per-requirement LLM call would (a) cost more tokens for context the requirement in
question has no stake in, and (b) let requirement-B's conflict text leak into
requirement-A's prompt, at odds with "unrelated requirements' conflicts/dependencies
never leaking into the call" (Decision 6).

### 2. `None` vs. `[]` is load-bearing, computed once, per requirement

```python
relevant_conflicts = (
    consistency_report.conflicts_for(req.id) if consistency_report is not None else None
)
relevant_dependencies = (
    dependency_report.dependencies_for(req.id) if dependency_report is not None else None
)
```

Computed once in `run_requirement`, right after `req = record.requirement` — not
per-round inside the refine loop, and not separately for Strategy Selector vs. Test
Generator. `consistency_report`/`dependency_report` are the same object for the whole
call (they come from one document-level run and never change mid-requirement), so
recomputing per call site would be redundant work risking the two computations drifting
out of sync with each other — the exact "two things that must agree" shape CLAUDE.md
calls out as this project's biggest bug source, avoidable here by computing once and
passing the result down instead of passing the raw reports to every call site and
re-deriving.

`None` means "the document-level stage that would have produced this failed — this
requirement has NO consistency/dependency context available, and that absence is itself
information (contract D1=b's `DEGRADED` outcome)." `[]` means "the stage ran
successfully and this requirement is not involved in any conflict/dependency." Collapsing
these (e.g. defaulting a failed stage to `[]`) would make a `DEGRADED` run's Quality
Checker output indistinguishable from a clean one that genuinely has no conflicts — the
model would report "no consistency issues" for a reason invisible in its own output:
nobody actually checked. `StageFns.check_quality`/`select_strategy`/`generate_tests` must
accept `Optional[list[...]]` for these parameters, not `list[...]` with an empty-list
default.

### 3. Independent Consistency/Dependency failure stays independent

This isn't new — `run_document_stages` already runs Consistency Checker and Dependency
Mapper independently (contract D1=b) and already produces `Optional[ConsistencyReport]`,
`Optional[DependencyReport]` separately. This design doesn't touch that; it just
threads the two `Optional`s through Decision 2's None-preserving computation
independently, so one stage's failure never contaminates the other's context. A
`DEGRADED` document (consistency failed, dependency mapping succeeded) hands the Quality
Checker `relevant_conflicts=None, relevant_dependencies=[...]` — never both `None`, and
never a failure on one side silently forcing `None` on the other.

### 4. Context survives refinement and resume without extra plumbing — *given Decision 7 holds*

`relevant_conflicts`/`relevant_dependencies` are derived from `consistency_report`/
`dependency_report`, which `run_requirement` already receives as parameters (unchanged
by this design) and which don't change across refinement rounds — `resume_document`
reloads `record.consistency_report`/`record.dependency_report` from disk and passes the
*same* objects into `run_requirement` on resume (`orchestrator/pipeline.py:900`).
Because the filtered lists are computed once per `run_requirement` call from those same
inputs, a resumed call recomputes the identical `relevant_conflicts`/
`relevant_dependencies` a fresh call would — there is no extra field to add to
`RequirementRunRecord`/`RefinementRound` and nothing new to persist. The one piece of
new plumbing is that `_run_refine_loop` (called once per `run_requirement`, looping
internally across rounds) does not currently accept `consistency_report`/
`dependency_report` at all — it must gain `relevant_conflicts`/`relevant_dependencies`
parameters so every round's Quality Checker call inside the loop can use them, not just
the calls made directly in `run_requirement`.

**Rev 1 of this design claimed this unconditionally. It is false as stated** — see
Decision 7. `record.consistency_report`/`record.dependency_report` are not actually
immutable for the life of a run: `retry_document_stage` can rewrite them *after* some
requirements have already consumed the old value, so "a resumed call recomputes the
identical value a fresh call would" is only true because Decision 7 makes it true, by
construction, not because the inputs were already guaranteed stable. This section
describes the mechanism; Decision 7 is what keeps its premise honest.

### 5. Dependencies reach both Strategy Selector and Test Generator

Per `DESIGN_NOTES.md` §3/4 (already decided, just not wired): both stages get
`dependency_report.dependencies_for(req.id)` — the same filtered list, computed once
(Decision 2) and passed to both call sites. Consistency conflicts do **not** go to
either — only the Quality Checker needs conflict context; a conflict is a text-quality
concern the Refiner loop resolves upstream, not a test-design input. (If this project
later wants strategy/test-generation to be conflict-aware too, that's a new decision to
make explicitly, not something to smuggle in here.)

### 6. Unrelated requirements' context never leaks into the call

Automatic, not something requiring separate enforcement: `conflicts_for`/
`dependencies_for` already filter by exact requirement-id membership
(`design/schemas.py:180-182`, `:218-224`). Calling them with `req.id` for the requirement
currently being processed is the entire mechanism — there is no path in this design
where a different requirement's filtered list is computed and handed to the wrong call.
The one thing to verify (Test plan, below) is that a multi-requirement document's
Quality Checker call for requirement A never receives a conflict/dependency naming only
B and C.

### 7. Document context is frozen per run once any requirement has consumed it (rev 2)

**The problem, verified independently, not assumed from the review comment:**
`retry_document_stage` (existing, pre-dates this design) retries ONE failed
document-level stage "within the same run" and, on success, writes the recovered report
into `record.consistency_report`/`record.dependency_report` and moves
`outcome: DEGRADED → COMPLETED` (`design/ORCHESTRATOR_CONTRACT.md` §6, "Retrying a
failed document-level stage" — this is documented, deliberate, pre-existing behavior).
Before this design, that was harmless: nothing downstream read those fields, so
mutating them retroactively changed nothing about how any requirement was processed.
Once Decisions 1–6 wire `conflicts_for(id)`/`dependencies_for(id)` into every
per-requirement stage call, it stops being harmless: `run_document` writes
`document.json` and *then*, synchronously, loops over every requirement in one call
(`orchestrator/pipeline.py:798-819`) — so within one uninterrupted `run_document` call
every requirement sees the same snapshot and there is no bug. The gap is **across
separate process invocations of the same run** (`run_id`/`run_dir`), which the resume
design explicitly supports: the process can crash mid-requirement-loop, an operator can
call `retry_document_stage` before resuming, and `resume_document` will then hand the
*recovered* report to whichever requirements were still pending — while the requirements
already processed (and already written to disk) consumed the *old* value (`None` or an
earlier partial report). Two requirements in the same run, same `run_id`, would then have
been processed under provably different document-level context, purely as a function of
when the operator happened to run the retry — not a methodological choice, an accident
of timing. Confirmed reachable: `run_document`'s per-requirement loop is the slow part
(real network calls, retries, human-in-the-loop refinement rounds over possibly minutes),
the document-level stages are one or two quick calls — a crash lands mid-requirement-loop
far more often than mid-document-stage in practice, and the resume/retry machinery exists
specifically to make picking up mid-loop routine, not exceptional.

**Why "a degraded run stays degraded" cannot be implemented literally as stated.**
`DocumentOutcome.COMPLETED` *requires* `consistency_report`/`dependency_report` both
present (`_DOCUMENT_OUTCOME_RULES`, `design/schemas.py:1527-1529`); `DocumentOutcome.
DEGRADED` explicitly *forbids* both being present at once — the validator raises
`"outcome=degraded but both reports are present"` (`design/schemas.py:1699-1705`). So a
retry that *succeeds* and is still written into `consistency_report` cannot coexist with
an outcome that stays `DEGRADED` — the model would refuse to validate. Freezing the
*outcome* label while letting the *report fields* update is not a policy choice available
under the current schema; it's a contradiction the schema already rejects. The literal
instruction only has two schema-legal readings: (a) a successful retry is still written
in and the outcome still climbs to `COMPLETED`, but that report is prevented from ever
reaching a `run_requirement`/`_run_refine_loop` call for this run — or (b) a retry that
would succeed is refused outright once it could matter, so neither the fields nor the
outcome ever change for this run. Reading (a) means `document.json` can end up saying
`COMPLETED` while some already-processed requirements demonstrably ran under `DEGRADED`
context — a misleading record for exactly the kind of thesis-defensibility reason
CLAUDE.md cares about ("Never invent results" extends to never letting a stored outcome
claim more than what actually happened downstream). Reading (b) is what this design
adopts.

**Decision: guard `retry_document_stage`, don't remove it, don't let it silently
no-op.** At the top of `retry_document_stage`, before calling `call_document_stage` (so
no API quota is spent on a call whose result would be discarded):

```python
if record.requirement_records:
    raise ValueError(
        f"cannot retry {stage.value}: {len(record.requirement_records)} requirement(s) "
        "already processed under this run's document context. Retrying now would let "
        "some requirements see the old context and others see the recovered one, in the "
        "same run. Start a new run to pick up corrected consistency/dependency analysis."
    )
```

`record.requirement_records` is already reconstructed from the per-requirement files on
disk by `read_document_run` (`orchestrator/pipeline.py:928-937`, already called at the
top of `retry_document_stage`) — no new field, no new persisted state, no schema
change. The guard is joint across **both** document-level stages, not per-stage:
`relevant_conflicts`/`relevant_dependencies` are always consumed together as one
snapshot per requirement (Decision 2), so recovering *either* report after any
requirement has run risks the identical cross-requirement inconsistency the other one
would. A requirement record existing at all — regardless of which stage(s) it reached,
even one that failed at the Classifier before ever touching document context — locks
both stages: correctness over precision here costs nothing, since the one case this
excludes (a requirement that failed before consuming context) is not worth a second,
finer-grained check just to keep a rare recovery window open.

**Practical effect — this is not a token distinction from the user's original framing.**
`run_document` never leaves a window between writing `document.json` and starting the
requirement loop across *separate* invocations (it's one synchronous call); the only way
`retry_document_stage` is ever called with `requirement_records == []` is a crash in that
exact narrow gap, or a document.json written and never resumed at all before the retry.
In every realistic crash-and-resume scenario, the guard fires. So "a degraded run stays
degraded, and recovered analysis is used only in a new run" is true in practice for
essentially every real invocation of this function — the guard is the precise version of
that rule (provably no cross-requirement inconsistency is ever possible, in the narrow
case it does allow, rather than by fiat), not a weaker one. `retry_document_stage` keeps
its documented purpose for the one case where it remains genuinely safe, instead of being
deleted for a risk it can no longer create.

## Orchestrator changes (`orchestrator/pipeline.py`)

- **`_run_refine_loop`** gains two required parameters, `relevant_conflicts:
  Optional[list[ConsistencyConflict]]` and `relevant_dependencies:
  Optional[list[DependencyLink]]`, inserted after `record`. Its `stage_fns.check_quality`
  call site (currently `(current, record.classification, suppressed_ids)`) becomes
  `(current, record.classification, relevant_conflicts, relevant_dependencies,
  suppressed_ids)`.
- **`run_requirement`** computes `relevant_conflicts`/`relevant_dependencies` once
  (Decision 2), right after `req = record.requirement`, and:
  - passes both into its `_run_refine_loop` call;
  - passes `relevant_dependencies` (not conflicts) into its `stage_fns.select_strategy`
    call, alongside the existing `(current, record.classification)`;
  - passes `relevant_dependencies` into its `stage_fns.generate_tests` call, alongside
    the existing `(current, strategy)`.
- **`StageFns`**'s docstring gains a short note on the new call shapes for
  `check_quality`/`select_strategy`/`generate_tests`, matching how it already documents
  the `refine_questioner`/`refine_rewriter` split — so a reader of the dataclass alone
  (not just the call sites) can see the contract.
- No change to `run_document`, `run_document_stages`, `resume_document`, or any schema in
  `design/schemas.py` — this is purely additional arguments threaded through existing
  parameters, not a new data shape.
- **`retry_document_stage`** (rev 2, Decision 7) gains the `requirement_records`-non-empty
  guard above, raising `ValueError` before calling `call_document_stage` at all. Its
  docstring's existing "retry the stage within the same run" framing needs a caveat: that
  is only true when no requirement has been processed yet under this run's current
  document context.

## Docs to update after implementation

- `design/schemas.py`'s module docstring, line 10: currently
  `Quality Checker (Requirement, Classification, ConsistencyReport, DependencyReport -> QualityReport)`
  → `Quality Checker (Requirement, Classification, ConsistencyReport.conflicts_for(id),
  DependencyReport.dependencies_for(id) -> QualityReport)`, matching the filtered form
  already used for stages 3/4 two lines below it.
- `design/DESIGN_NOTES.md` "Pipeline overview" ASCII diagram (lines ~24-36): same fix for
  the Quality Checker line. While there: lines 33/35 currently say
  `Test Design Strategy Selector (RefinedRequirement, Classification, ...)` and
  `Test Case Generator (RefinedRequirement, TestStrategy, ...)` — stale, contradicts the
  already-corrected note directly below in `schemas.py`'s own docstring ("stages 3 and 4
  ... take a plain `Requirement`, NOT a `RefinedRequirement`"). Fix both `RefinedRequirement`
  → `Requirement` while editing this diagram, since it's the same block and the two
  files are supposed to be the same claim in two places.
- `design/DESIGN_NOTES.md` §2b (Quality Checker) or a new §2b-addendum: one short
  paragraph stating the Quality Checker now receives filtered conflict/dependency
  context, linking to this file rather than restating it, per CLAUDE.md ("Do not restate
  design reasoning in new files").
- `design/ORCHESTRATOR_CONTRACT.md`: worth a short new item stating the `None`-vs-`[]`
  contract from Decision 2 explicitly, since it's exactly the kind of orchestrator-level
  guarantee the schema doesn't and can't enforce (a `Optional[list[...]]` parameter type
  doesn't by itself stop a future stage_fn implementation from treating `None` and `[]`
  the same way — only a human reading the contract, or a test, catches that).
- `design/ORCHESTRATOR_CONTRACT.md` §6, "Retrying a failed document-level stage"
  **must be rewritten, not just extended (rev 2)**. It currently says unconditionally:
  "retry the stage within the same run and write the report into the existing record ...
  the outcome then moves from DEGRADED to COMPLETED on its own." That is now only true
  when `requirement_records` is empty. Add the guard's condition and its reasoning
  (Decision 7), and state plainly that once any requirement has been processed, the only
  way to use corrected document-level analysis is a new run.

## Test plan (`orchestrator/test_harness.py`)

`Scripted` fake stage fns already record every call's positional args
(`self.calls.append(args)`) and ignore their exact shape when returning behaviors, so no
*existing* test breaks from the added parameters — but new coverage is needed for the
behavior itself:

1. **Filtered, not whole-report, no leakage — three requirements, not two (rev 2 fix).**
   The original version of this item used a two-requirement document (A conflicting,
   B not). **That fixture cannot exist**: `ConsistencyConflict.requirement_ids` requires
   `min_length=2` (`design/schemas.py`), and with only two requirements in the whole
   `RequirementSet`, *any* conflict of length ≥2 drawn from a pool of two members must
   name both of them — there is no way to construct a conflict that involves A but not
   B when B is the only other requirement that exists. The "leakage" case this item is
   supposed to catch (an unrelated requirement's context bleeding into another's call)
   is unconstructible with two requirements — the test would pass by construction, not
   by correctness. Use **three** requirements, A/B/C: one `ConsistencyConflict` naming
   A and C (not B), one `DependencyLink` from A to C (not touching B). Assert:
   - A's and C's `check_quality` calls both carry the conflict and the dependency;
   - **B's `check_quality` call carries `relevant_conflicts=[]` and
     `relevant_dependencies=[]`** — the report is non-empty, B is simply not in it. This
     is the actual leakage proof: B is a genuine bystander with real conflicts/
     dependencies present in the *document*, correctly absent from *its own* call.
2. **`None` vs. `[]`:** one case where the consistency checker fails outright (`DEGRADED`,
   contract D1=b) and the dependency mapper succeeds with no links for this requirement —
   assert `check_quality` receives `relevant_conflicts=None` and
   `relevant_dependencies=[]` in the same call, not the reverse and not both `None`.
3. **Independent failure, both directions:** the mirror of #2 (dependency mapper fails,
   consistency checker succeeds) — Decision 3.
4. **Dependencies reach both stages:** assert `select_strategy` and `generate_tests`'
   recorded call args both contain the same `relevant_dependencies` list for a
   requirement with a real dependency link.
5. **Survives resume — no document-level retry in between:** a document interrupted
   after the document-level stages but before a requirement finishes (existing
   interruption/resume scenario shape), resumed with *no* `retry_document_stage` call in
   between — assert the resumed requirement's `check_quality`/`select_strategy` calls
   receive the same filtered conflicts/dependencies as they would have on an
   uninterrupted run, not `None` or `[]` by default.
6. **Refinement rounds:** a requirement that fails round 1 and passes round 2 — assert
   both rounds' `check_quality` calls carry the identical `relevant_conflicts`/
   `relevant_dependencies` (proving `_run_refine_loop` threads them through every
   iteration, not just the first).
7. **Retry allowed before any requirement has run, and it succeeds (Decision 7, allowed
   branch, happy path):** build a `DocumentRunRecord` via `run_document_stages` +
   `write_document_run` directly — *not* `run_document` — so `document.json` exists with
   `outcome=DEGRADED`, one recorded error (the original document-level failure), and zero
   requirement files on disk (the narrow crash-window case). Call `retry_document_stage`
   with a stage_fn that now succeeds; assert it succeeds, `outcome` climbs to
   `COMPLETED`, and the recovered report is written — same assertions the *existing*
   `test_document_stage_retry_within_run` makes today, but on a fixture that actually has
   zero requirement records, which the current one does not (see item 9).
8. **Retry allowed before any requirement has run, but it fails too (Decision 7, allowed
   branch, failure path — new coverage; the rewrite in item 9 would otherwise silently
   drop this).** Rewriting the old test to fix item 9 removes its "a second failure
   appends a second, distinct-`invocation_id` `DocumentStageError`, the original entry's
   `retry_count` is untouched, not merged" coverage (the current test's `fail_again_fns`
   section) *and* that coverage happened to sit in a fixture state this design now makes
   illegal to retry-in-place. Recreate it in a state that stays legal: same zero-
   requirement-record fixture as item 7 (one existing error from the original failure,
   `outcome=DEGRADED`, no requirement files), but this time `retry_document_stage`'s
   stage_fn fails again too. Assert:
   - the guard does **not** fire (`requirement_records` is empty, so the call proceeds
     to `call_document_stage` — this is still the *allowed* branch, just an allowed
     attempt that doesn't pan out);
   - **a second, independent `DocumentStageError` is appended for the stage — not a
     merge into the first** (`len(errors) == 2`, matching the pre-existing rev-2
     no-merge behavior this design must not regress);
   - **its `invocation_id` differs from the first error's** (two distinct invocations,
     per `docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md`);
   - **the first error's `retry_count` is untouched** (proving no merge/bump happened,
     the exact assertion the current test makes and the rewrite must keep);
   - **the new failure's attempts are recorded in `attempts` and linked to the new
     error via the shared `invocation_id`** (the forward-agreement check from the
     observability design: the new error's `invocation_id` resolves to a real,
     matching, failed attempt group);
   - **`outcome` stays `DEGRADED`** — the retry didn't help, so nothing about "both
     reports present" becomes true, and the schema still requires it.
9. **Retry blocked once any requirement has run (Decision 7, blocked branch — the
   scenario the current `test_document_stage_retry_within_run` gets wrong for rev 2).**
   The existing test's "first run" scripts `classify` to fail for the one requirement in
   `DOC`, so by the time it calls `retry_document_stage`, `requirement_records` already
   has one (`ERROR`-outcome) entry — under Decision 7 this retry must now raise, not
   succeed as the test currently asserts. Rewrite it: run `run_document` all the way
   (document-level stage fails, the one requirement processes — anywhere, even just
   through the Classifier), capture `read_document_run(tmp_path)` in full (the whole
   `DocumentRunRecord`, not a field subset) as `before`, then call
   `retry_document_stage` with a stage_fn that *would* succeed if it were ever called;
   assert it raises `ValueError`, that `fn.calls == []` on the `Scripted` stage_fn (proves
   the guard fires **before** `call_document_stage`, not after a wasted call whose result
   is merely discarded), and that `read_document_run(tmp_path) == before` as complete
   model equality (`.model_dump(mode="json")` compared whole, not `consistency_report`/
   `dependency_report`/`outcome`/`errors` checked field-by-field) — the strongest form of
   "nothing changed," covering every field including ones no one thought to name
   individually.
10. **End-to-end context consistency across resume + retry, both orders:** the scenario
   this whole decision exists to prevent, turned into an assertion. Two requirements;
   crash after the first is processed (document-level stage still `DEGRADED` at that
   point); attempt `retry_document_stage` — assert it raises (item 9's guard, since one
   requirement already ran); resume the second requirement — assert it *also* gets
   `relevant_conflicts=None` (the same as the first got), never the recovered value.
   Confirms the two requirements in one run never diverge, which is the property Decision
   7 exists to guarantee, not just that the guard raises in isolation.

## Conflicts and consequences found while designing

**Rev 1 said "none found." Wrong — rev 2 exists because of what surfaced under review:**
see Decision 7 for the late-retry/context-consistency problem (`retry_document_stage`
updating `consistency_report`/`dependency_report` after some requirements already
consumed the old value) and the schema-level reason "a degraded run stays degraded"
cannot be implemented as a label change alone (`DocumentOutcome.DEGRADED` and
`COMPLETED` are mutually exclusive on report presence, `design/schemas.py:1520-1533`,
`:1699-1705`). The existing `test_document_stage_retry_within_run` scenario also turned
out to already be exercising the now-forbidden path (item 9) — it needs rewriting, not
extending, exactly the kind of thing this section exists to flag before implementation
rather than after.

**Second pass found the rewrite itself would drop coverage.** Splitting the old test's
single scenario into "allowed" (item 7) and "blocked" (item 9) loses the old test's
second half — a failed retry appending its own new, distinct-`invocation_id`
`DocumentStageError` rather than merging — because that half happened to run in a
fixture state (`requirement_records` non-empty) this design now makes illegal to
retry-in-place at all. The fix is not to skip that coverage but to re-home it in a
fixture where it's still legal: item 8, a zero-requirement-record retry that itself
fails. Found by asking "what did the rewrite implicitly delete," not just "what does the
new behavior need" — the same category of gap as the `RequirementRunRecord`/
`DocumentRunRecord` twin-check CLAUDE.md already warns about, just between a test's old
and new shape instead of between two record types.

This remains additive plumbing on top of parameters (`consistency_report`,
`dependency_report`) and helper methods (`conflicts_for`, `dependencies_for`) that
already exist and are already unused for exactly this purpose — the design fills in a
gap the code's own comments already named, rather than introducing a new concept.

## `design/flowchart.mermaid` — not part of this design, not intentional project output

Checked: it is **not** one of the five files `python -m design.generate_diagrams`
produces (`models.mermaid`, `paths_document.mermaid`, `paths_failure.mermaid`,
`paths_requirement.mermaid`, `pipeline.mermaid` — CLAUDE.md: "rewrites the five .mermaid
files"). It is untracked (`git status`: `?? design/flowchart.mermaid`), has no commit
history, and isn't referenced anywhere in `CLAUDE.md`, `DESIGN_NOTES.md`, or
`ORCHESTRATOR_CONTRACT.md`. Its content carries `generationTime`/`references`
frontmatter in a shape none of this project's own tooling writes — that pattern (plus
the fact it appeared exactly when the file was opened in the IDE, per this session's own
system context) points to an editor/extension auto-generating a summary diagram on open,
not a project-authored artifact. It also already describes the *pre*-Decision-1–7
pipeline (no conflicts/dependencies flowing anywhere), so it would need regenerating (by
whatever produced it) the moment this design lands regardless.

Conclusion: it is out of scope for this change. Nothing in this design reads, writes, or
depends on it, and it should not be added to any commit for this work. Whether to keep,
gitignore, or delete it is a separate call for whoever/whatever owns that IDE tooling —
not something to fold into an orchestrator design doc, and not something to decide
unilaterally here since it's untracked and may be a deliberate personal artifact.
