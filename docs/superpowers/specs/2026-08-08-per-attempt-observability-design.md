# Per-attempt LLM call observability — Design

Date: 2026-08-08
Status: implemented (rev 2 — direct invocation linkage)

## Purpose

`call_stage`/`call_document_stage` used to record two disjoint things: a `TokenUsage`/
`DocumentTokenUsage` entry for every call that *returned* (success or validation
failure), and a `StageError`/`DocumentStageError` for the *final* attempt once retries
were exhausted. Neither recorded a failed attempt that a later retry overwrote — a
transient 429 followed by a successful retry left no trace at all (see
`ORCHESTRATOR_CONTRACT.md` item 7's "retries that succeeded on the first stage
invocation leave no trace" and item 15's "the rate is counted, not silent" — both
promises depended on a full attempt log that did not yet exist). This closes that gap:
every attempt of every LLM call is now a recorded row, success or failure, replacing
the token-only log with the complete one.

**Revision 2** replaces rev 1's order-based/aggregate matching between `StageError`/
`DocumentStageError` and the attempt log with a direct `invocation_id` reference on the
error itself, and makes document-level behavior append-only, symmetric with
requirement level. See "Revision history" at the end for exactly what changed and why.

## Decisions carried over unchanged from rev 1 (approved)

1. `RunMetadata.schema_version` bumps `"1.0"` → `"1.1"`. No real run files exist
   anywhere in this repo or on this machine (confirmed by searching for
   `document.json`/`runs/` on disk and in git history; `runs/` is `.gitignore`d and was
   never populated — `orchestrator/stages.py` is still an empty stub). Nothing to
   migrate; the bump exists purely so a future reader can tell two record shapes apart.
2. `TokenUsage`/`DocumentTokenUsage` are deleted, not kept alongside the attempt log —
   two logs both claiming to be "the tokens for this stage" is the exact
   two-fields-that-must-agree shape `CLAUDE.md` calls out as this project's biggest bug
   source.
3. New enum `AttemptResult` (`SUCCESS` / `TRANSPORT_FAILURE` / `VALIDATION_FAILURE` /
   `OTHER_FAILURE`), not a reuse of `FailureKind` — `FailureKind` has no success case and
   is scoped to "why did the stage *finally* fail," used only on the error summary.

## Model changes (`design/schemas.py`)

### `StageAttempt` / `DocumentStageAttempt` — unchanged from rev 1

```python
class StageAttempt(BaseModel):
    stage: PipelineStage
    invocation_id: NonEmptyStr   # groups every attempt of one logical call_stage() call
    attempt_number: int = Field(..., ge=1)
    result: AttemptResult
    error_message: Optional[str] = Field(None, min_length=1)
    prompt_tokens: Optional[int] = Field(None, ge=0)
    completion_tokens: Optional[int] = Field(None, ge=0)
```

`DocumentStageAttempt` identical apart from `stage: DocumentStage`. Per-result shape
rule (shared helper, both classes call it):

| result | error_message | tokens |
|---|---|---|
| `SUCCESS` | forbidden | required |
| `VALIDATION_FAILURE` | required | required (inference happened, output was rejected) |
| `TRANSPORT_FAILURE` | required | forbidden (rejected before inference) |
| `OTHER_FAILURE` | required | either — absent when unavailable, not forced either way |

`prompt_tokens`/`completion_tokens` are always both-present or both-absent regardless
of result. `RequirementRunRecord.usage`/`DocumentRunRecord.usage` are renamed to
`attempts: list[StageAttempt]` / `attempts: list[DocumentStageAttempt]`.
`total_tokens`/`document_stage_tokens` sum over attempts with tokens present.

### `StageError` / `DocumentStageError` — gain `invocation_id` (rev 2 change)

```python
class StageError(BaseModel):
    stage: PipelineStage
    invocation_id: NonEmptyStr   # the attempts-log invocation this error summarises
    kind: FailureKind
    message: NonEmptyStr
    retry_count: int = Field(0, ge=0)
```

`DocumentStageError` gets the identical field. This is the direct fix for the "which
attempt does this error summarise" question rev 1 answered indirectly (list-position
pairing, or an aggregate-across-invocations rule at document level). Every exhausted
`call_stage`/`call_document_stage` invocation produces exactly one error, linked to the
invocation that produced it — one cause, one record, at both levels.

### Invocation-shape validation (new in rev 2)

A shared helper groups a flat `attempts` list by `invocation_id` (dict keyed by
`invocation_id`, insertion order — attempts within one invocation are always appended
contiguously by `call_stage`/`call_document_stage`'s retry loop, so this needs no
sorting). `RequirementRunRecord`/`DocumentRunRecord` each validate, per invocation
group:

- **One stage per invocation id.** Every attempt sharing an `invocation_id` must carry
  the same `stage` — catches an id accidentally reused across a different logical call.
- **Attempt numbers are exactly `1..N`, in that order.** `[a.attempt_number for a in
  group] == list(range(1, len(group) + 1))`. This single list-equality check enforces
  "no gaps," "no duplicates," and "appears in numerical order" together — a group whose
  numbers are unordered, skip one, or repeat one all fail the same comparison.
- **At most one `SUCCESS`, and only as the last attempt.** A retry loop stops the
  moment a call succeeds, so a `SUCCESS` followed by more attempts under the same id, or
  two `SUCCESS`es, both indicate corruption rather than a real call sequence.
  **Post-implementation update:** implemented first as two separate checks ("count of
  SUCCESS <= 1" and "the first SUCCESS's index equals the last position"), then
  mutation-testing proved the first unreachable -- whenever 2+ successes exist, the
  smallest of their indices is provably less than the last position, so the second
  check always catches it first. Deleted the first, merged the reasoning into the
  second's docstring.
- **Attempts for one invocation are contiguous in the flat list.** Once the list moves
  on from an `invocation_id`, that id may not reappear later — real calls run their
  retry loop to completion before the caller can start a different invocation, so
  `A1, B1, A2` is not a shape either `call_stage`/`call_document_stage` or any resume
  path can produce. Checked by walking the list once: track the current run's id, and
  raise if an id that has already been closed out shows up again.

**Post-implementation update:** a dedicated `_require_unique` on `(invocation_id,
attempt_number)` was written, then deleted after mutation-testing proved it
unreachable -- any duplicate number inside one invocation already breaks that group's
numbers away from `range(1, len+1)`, so the numbering check was always the one
actually catching it. Kept as a one-line comment in `schemas.py` rather than dead code,
per CLAUDE.md ("don't write a check that can't fire"). The corresponding test in
`design/test_schemas.py` survives, relabelled to say what actually catches it.

### Error ↔ attempt agreement (rev 2 replaces rev 1's zip/aggregate rules)

Two directions, checked at both `RequirementRunRecord` and `DocumentRunRecord`:

**Forward — every error must reference a real, matching, failed invocation.** For each
`err` in `errors`: look up `attempts` grouped by `invocation_id`; `err.invocation_id`
must be present, its group's *last* attempt must:
  - carry the same `stage` as `err`;
  - not be `SUCCESS` (an error cannot summarise a call that ended in success);
  - map to `err.kind` via `AttemptResult → FailureKind`;
  - have `error_message` equal to `err.message`;
  - the group's length minus 1 must equal `err.retry_count`.

No skip when a stage "has no attempt data" — rev 1's escape hatch is gone. Under
schema version 1.1, a `StageError`/`DocumentStageError` with no backing invocation is
invalid, full stop. `errors` is also checked for `invocation_id` uniqueness
(`_require_unique`) — two errors cannot summarise the same invocation.

**Backward — every failed invocation must be summarised by some error**, with one
named exception. For each invocation whose last attempt is not `SUCCESS`: some `err`
in `errors` must reference it (`err.invocation_id == group's id`), **unless**
`self.outcome is RunOutcome.CAP_STOPPED` and the invocation's stage is
`STRATEGY_SELECTOR` or `TEST_GENERATOR`.

That carve-out is not a general escape — it is the one place the pipeline
*intentionally* deletes an error that used to exist. `run_requirement`'s CAP_STOPPED
path strips `errors` entries naming those two stages (required by the existing
`_outcome_matches_contents` rule: "`CAP_STOPPED` may not record failures in
`strategy_selector`/`test_generator` — the human stopped before stage 3"), but
`attempts` is an append-only log and is never stripped. So a CAP_STOPPED record can
legitimately contain a failed `STRATEGY_SELECTOR` invocation (from an earlier
`CAP_GENERATED` attempt the human later overrode) with no error backing it. No other
outcome strips `errors` — `COMPLETED`/`CAP_GENERATED`/`ERROR` all keep every failure on
record per contract item 7 — so no broader exception is needed or given.

At document level there is no equivalent stripping (no cap concept), so the backward
check there has **no exception**: every failed invocation must be summarised. Combined
with the forward check, requirement level and document level are now symmetric except
for that one named, narrow carve-out — not the asymmetric aggregate-vs-ordered rules
rev 1 had.

### Consequence: `DocumentRunRecord` loses its "at most one error per stage" rule

`_outcome_matches_contents` currently rejects two `DocumentStageError`s naming the same
stage (`design/schemas.py`, the `failed: set[DocumentStage]` loop). That rule existed
*because* document-level retries used to merge into one entry (see next section) — it
becomes actively wrong once retries append instead. The fix is narrow: keep building
the `failed` set (still needed for the DEGRADED "a missing report must have a recorded
failure explaining it" check just below), drop only the `raise` on a repeat:

```python
failed: set[DocumentStage] = {err.stage for err in self.errors}
```

This is a direct, necessary consequence of "make document-level match requirement-level
behavior" (RequirementRunRecord never had a per-stage uniqueness rule on `errors` to
begin with — Quality Checker/Refiner failures across rounds were already expected to
repeat).

## Orchestrator changes (`orchestrator/pipeline.py`)

- `call_stage`/`call_document_stage` gain a required `invocation_id: str` parameter (no
  default — same "a forgotten wire-up must fail loud" reasoning as `req_id`/`doc_id,`
  extending `test_id_check_parameters_have_no_default`) and `usage_sink` is renamed
  `attempt_sink`. Every loop iteration appends one attempt row — success, transport
  failure, validation failure (including a `requirement_id`/`doc_id` mismatch, still
  `VALIDATION_FAILURE`, unchanged), or other failure — instead of only appending on a
  call that returned. On exhaustion, the caller builds its `StageError`/
  `DocumentStageError` with `invocation_id=invocation_id` — the same id just threaded
  through, not something `StageFailed` needs to carry itself.
- Every call site that currently does `usage = list(record.usage); ...; call_stage(...,
  usage, ...)` generates one `invocation_id = uuid.uuid4().hex` immediately before the
  call and renames `usage`/`usage_sink` to `attempts`/`attempt_sink` throughout. This is
  what gives Quality Checker round 1 and round 2 distinct invocation ids (two separate
  `call_stage` calls, two fresh ids) while keeping one call's internal backoff retries
  under the same id. Same for `refine_questioner`/`refine_rewriter` (already separate
  call sites — this adds the id on top of the stage attribution they already have).
- **`retry_document_stage` stops merging (rev 2 change).** It currently looks up an
  existing `DocumentStageError` for the stage and bumps its `retry_count` on a repeat
  failure rather than appending. That lookup-and-merge logic is deleted; a manual retry
  mints its own `invocation_id` and, on failure, appends a new, independent
  `DocumentStageError` — the identical shape `run_requirement` already uses for
  requirement-level stage failures. This simplifies the function (removes the
  existing/merge branch entirely) as a side effect of making the two levels symmetric.
- No change to `StageFailed`, `StageCallResult`, `StageCallFailed`, `Throttle`, or the
  overall retry/backoff control flow — additive logging alongside it, not a rewrite.

## Conflicts and consequences found while revising (flagging per your request)

1. **`DocumentRunRecord`'s per-stage error uniqueness rule must be removed** (see
   above) — a real, existing schema behavior change, not just an addition. Direct
   consequence of point 3 of your revision; I'm not asking to keep it, just noting it's
   a schema behavior change beyond pure addition.
2. **`orchestrator/test_harness.py::test_document_stage_retry_within_run`'s second half
   currently asserts the merge behavior directly**: "still only one error entry for the
   stage after a second failure" and "retry_count bumped rather than reset" (lines
   ~1701–1704). Both assertions invert under rev 2 — a second failure now produces a
   *second* `DocumentStageError`, and no entry's `retry_count` gets bumped after the
   fact. This test needs rewriting, not just extending.
3. **Point 5 ("do not skip agreement validation... update isolated test fixtures with
   minimal matching attempts") touches every existing direct `StageError(...)`/
   `DocumentStageError(...)` construction that gets embedded in a full
   `RequirementRunRecord`/`DocumentRunRecord`** — found by grep, 11 call sites across
   `design/test_schemas.py` and `orchestrator/test_harness.py` (e.g.
   `test_resume_positions`'s `err(stage)` helper, `CE`/`DE` fixtures at
   `test_schemas.py:179-181`, `test_resumed_cap_generated_then_stopped_strips_stage34`'s
   `prior_error`). Each needs a minimal matching `StageAttempt`/`DocumentStageAttempt`
   added (right stage, right `invocation_id`, a failure result, matching message) —
   real work, enumerated here so it isn't discovered mid-implementation. **Not** a
   conflict with the design — this is exactly what point 5 asks for — but it is larger
   in scope than rev 1's version of this section implied.
4. **Bare `StageError`/`DocumentStageError` construction that is never embedded in a
   parent record** (e.g. `test_failure_kind`'s direct
   `StageError(stage=..., kind=..., message="x")` checks, testing `StageError`'s own
   field validation) needs only the new required `invocation_id` argument added — a
   one-line fixture change, not a matching-attempts fixture, since the
   forward/backward agreement checks live on the *parent* record and never fire for a
   standalone model instance. No conflict; noting the distinction so implementation
   doesn't over-apply point 5's fixture work where it isn't needed.
5. No conflict found with points 6–8 of your revision — 6 is the forward check above,
   7 is the backward check's named exception, 8 is the invocation-shape section above.

## Test plan

`design/test_schemas.py`: replace `test_token_usage` with attempt-shape tests (per-result
table), the three invocation-shape checks, `_require_unique` coverage on
`(invocation_id, attempt_number)` and on `errors`' `invocation_id`s, and both directions
of the agreement check (including the `CAP_STOPPED`/`STRATEGY_SELECTOR`|`TEST_GENERATOR`
exception and a case proving the exception does *not* extend to any other stage or
outcome). Update the 11 existing fixtures per "Conflicts" item 3/4 above.

`orchestrator/test_harness.py`: update every existing `call_stage`/`call_document_stage`
call site for the new `invocation_id` parameter; rewrite
`test_document_stage_retry_within_run`'s second half per "Conflicts" item 2; add the
eleven scenarios your original request listed (first-attempt success;
validation-then-success; wrong-id-then-success; transport-then-success; mixed failures
exhausting retries; token totals including rejected validation output; transport
contributing zero tokens; distinct invocation ids across refinement rounds; separate
Questioner/Rewriter attribution; JSON round trip; `StageError`-vs-final-attempt
agreement, now via direct `invocation_id` lookup rather than position).

## Docs to update after implementation

- `ORCHESTRATOR_CONTRACT.md` item 6's "Retrying a failed document-level stage"
  subsection and item 7's "a later manual retry... should bump that count rather than
  append a second entry for the same stage at document level" both describe the old
  merge behavior and need rewriting to the new append-only, symmetric one. Item 13's
  usage-log description also becomes false.
- `docs/superpowers/plans/2026-08-08-first-real-run-checklist.md`: the validation-rate
  section currently says it can only measure a lower bound (successful retries
  invisible) — that limitation is gone.
- `DESIGN_NOTES.md`: one new dated section, linking here rather than restating.

## Revision history

**Rev 1 → rev 2**, per your review:

- Added `invocation_id` to `StageError`/`DocumentStageError` (rev 1 deliberately left it
  off and used indirect pairing instead — you asked for the direct reference).
- Replaced rev 1's order-based `zip()` pairing at requirement level with direct
  `invocation_id` lookup.
- Replaced rev 1's document-level aggregate arithmetic (first-failure's kind/message,
  summed retry counts across merged invocations) with the same direct lookup used at
  requirement level, made possible by retiring the merge behavior in
  `retry_document_stage`.
- Removed rev 1's "skip agreement checking when a stage has no attempt data at all"
  escape hatch. Rev 1 needed it to avoid breaking fixtures that construct a bare
  `StageError` inside a full record with no matching attempts; rev 2 instead requires
  those fixtures to carry minimal matching attempts (Conflicts item 3).
- Added the invocation-shape validation section (one-stage-per-id, contiguous
  `1..N` numbering, at-most-one-terminal-success) — not present in rev 1.
- Added the backward (failed-invocation-needs-an-error) check with the narrow
  `CAP_STOPPED` carve-out — rev 1's zip-based approach tolerated *any* unmatched failed
  invocation, silently, everywhere; rev 2 requires an explicit reason.
