# Orchestrator Contract

What the orchestrator must do that `schemas.py` deliberately does **not** enforce.

The schema rejects a record where one of these was done wrong. It cannot do any of them
for you. Each item cites the `DESIGN_NOTES.md` section that decided it, so the reasoning
is one search away rather than restated here.

Written before implementation began, by collecting obligations that had accumulated
across ~1,500 lines of design notes in the order they were discovered.

---

## 1. Stage sequencing

Execution order is not recorded anywhere in the Pydantic models — nothing in
`QualityReport` says it runs after `Classification`. The orchestrator owns it.

```
0.  Input                     RequirementSet
1.  Consistency Checker       once per document
1b. Dependency Mapper         once per document
2.  Per requirement:
      a. Classifier
      b. Quality Checker
      c. Refiner (only if the check failed)  -- loops back to (b)
3.  Test Design Strategy Selector
4.  Test Case Generator
```

*(DESIGN_NOTES: "Generated diagrams" — why `pipeline.mermaid` is hand-declared.)*

## 2. The carrier between stages 2 and 3

Stages 3 and 4 take a plain `Requirement`, never a `RefinedRequirement`. Both paths
converge:

```python
if report.passed:
    current = req                       # clean -- pass the original through
else:
    refined = refine_loop(req, report)
    current = Requirement(id=req.id,
                          text=refined.refined_text,
                          source_doc_id=req.source_doc_id)
```

Do **not** read `record.final_text` to decide this at runtime — it is a reporting
convenience, and on an `IN_PROGRESS` or `ERROR` record it returns the original text for
a run that never reached stage 3. Guarding that is orchestrator logic.

*(DESIGN_NOTES: "Stages 3/4 take `Requirement`, not `RefinedRequirement`".)*

## 3. The revision cap

Enforce a maximum number of refinement rounds per requirement (e.g. 3). Without it, a
genuine disagreement between the checker and the human loops forever.

When the cap fires with issues outstanding, **ask the human**: generate tests from the
best-effort text anyway, or stop? Record the answer as the outcome
(`CAP_GENERATED` / `CAP_STOPPED`) plus a `cap_reason`. This is decision D1=c.

Note for the write-up: this makes the pipeline non-deterministic across runs, since a
human judgement sits inside it. Threats to validity.

*(DESIGN_NOTES: "2c continued", "Run outcome and stage failures".)*

## 4. Issue identity across rounds

Each round's `QualityReport` is a fresh LLM call minting its own ids. The orchestrator
must match a round's issues against the previous round's — on `(category, span)` — and
**reuse the id** when it is the same defect.

The schema rejects a record where one id carries two different defects. It cannot
perform the match.

Corollary: the orchestrator, not the LLM, should assign `Issue.id`. An id invented fresh
each round cannot be stable by construction.

*(DESIGN_NOTES: "Gap 6: issue identity across rounds".)*

## 5. Suppression

When the human sets `user_confirms_resolved` on an answer, the orchestrator must:

- tell the Quality Checker not to re-flag that issue on subsequent rounds, and
- record it in `RefinementRound.suppressed_issue_ids` for **every** later round.

Suppressions accumulate. Dropping one lets the issue reappear — the exact loop
`user_confirms_resolved` exists to break. The schema rejects a record that drops one.

*(DESIGN_NOTES: "Gap 6".)*

## 6. Resume after interruption

Resume position is derivable from which fields are populated — no stored field:

```python
def resume_at(rec):
    if rec.classification is None:                    return CLASSIFIER
    if not rec.rounds:                                return QUALITY_CHECKER
    last = rec.rounds[-1]
    if not last.quality_report.passed:
        # The last round failed its check. Which half of that round is unfinished?
        if last.rewrite is None:  return REFINER          # ask/answer/rewrite still to do
        else:                     return QUALITY_CHECKER  # rewrite done -> check it (next round)
    if rec.test_strategy is None:                     return STRATEGY_SELECTOR
    if rec.test_plan is None:                         return TEST_GENERATOR
    return None                                       # finished
```

The `last.rewrite` branch is easy to miss and was wrong in the first version of this
document: a round whose check failed **and** which already produced a rewrite has
finished its refinement, so the next step is checking that rewrite, not refining again.
Without the branch the Refiner re-runs on a round it already completed. Verified against
constructed records at all six failure points; the test lives in
`test_schemas.py::test_resume_positions`.

At document level, `DocumentRunRecord.pending_requirement_ids` gives everything that
still needs work: no record file, **or** a record whose outcome is `IN_PROGRESS`
(interrupted) or `ERROR` (a stage failed). Only `COMPLETED`, `CAP_GENERATED` and
`CAP_STOPPED` count as finished — see `TERMINAL_OUTCOMES`.

So a resume pass is simply: process everything in `pending_requirement_ids`, starting
each at its derived stage. A requirement that failed is picked up again automatically;
nothing else is redone.

*(DESIGN_NOTES: "Run outcome and stage failures", decision D2; "Retry without redoing
everything".)*

### Retrying a failed document-level stage

Do **not** start a new run to repair one failed document stage — completed requirement
records cannot be carried across runs (their `run_id` would not match), so every
requirement would be reprocessed.

Instead, retry the stage within the same run and write the report into the existing
record. Keep the `DocumentStageError` where it is: `errors` is a log of failed attempts,
not a statement of current state, so a stage may hold both an earlier failure and a
later report. The outcome then moves from `DEGRADED` to `COMPLETED` on its own, because
no report is missing any more.

If the same stage fails again, bump `retry_count` on the existing error rather than
appending a second entry for that stage.

*(DESIGN_NOTES: "Retry without redoing everything".)*

## 7. Retries and failures

Free-tier rate limits make retry-with-backoff the normal path, not an exception. On
final failure, append a `StageError` to `errors` naming the stage, the message, and
`retry_count` (retries attempted *before* giving up), and set `outcome=ERROR`.

`errors` is a **log**, at both levels. Keep entries when a later attempt succeeds — the
outcome moves to `COMPLETED` on its own once every stage has produced its output. So:

```python
# requirements that failed at least once but finished anyway
sum(1 for r in doc.requirement_records
    if r.outcome in TERMINAL_OUTCOMES and r.errors)

# documents in the same situation
doc.outcome is DocumentOutcome.COMPLETED and doc.errors
```

Two constraints the schema enforces:

- `CAP_STOPPED` may not record failures in `strategy_selector` or `test_generator` —
  the human stopped before stage 3, so those stages never ran.
- `ERROR` requires something to actually be missing. A record where every stage produced
  its output is `COMPLETED`, whatever failed on the way.

Within a `StageError`, `retry_count` covers the backoff loop of a single attempt. A
later manual retry that fails again should bump that count rather than append a second
entry for the same stage at document level (where each stage runs once). At requirement
level duplicates are allowed, since the Quality Checker and Refiner run once per round.

Retries that succeeded on the *first* stage invocation leave no trace — `retry_count`
describes calls that ultimately failed. Do not compute a success rate from it; the
denominator is not in the schema.

*(DESIGN_NOTES: "`StageError.retry_count`", "Retry without redoing everything",
"Requirement-level errors made symmetric".)*

**`FailureKind`** (added 2026-08-08, see
`docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md`): every `StageError`
and `DocumentStageError` now carries `kind: TRANSPORT | VALIDATION | OTHER`. `TRANSPORT`
is a rejected request (retry usually helps); `VALIDATION` is a model output that failed
schema validation (the call succeeded, tokens were spent, retrying may help since LLM
output is nondeterministic); `OTHER` is a caught-but-unanticipated failure and must never
be used for a bug in the orchestrator's own control flow, which should crash instead.

## 8. Document-level failure policy (D1=b)

If the Consistency Checker or Dependency Mapper fails, **continue** without it and mark
the document `DEGRADED`. Both can fail independently.

For analysis: a `DEGRADED` document's requirements were quality-checked without
inherited consistency flags, or had stages 3/4 run without dependency context. Do not
pool them with requirements from a `COMPLETED` document. Preferred practice is to re-run
degraded documents (two API calls) rather than analyse them, and report how many needed
it.

*(DESIGN_NOTES: "Document-level run record".)*

## 9. On-disk layout (D2=b)

```
<run_dir>/document.json                    DocumentRunRecord, requirement_records=[]
<run_dir>/requirements/THEMAS-REQ-A.json   one RequirementRunRecord each
```

The document file is written with an **empty** `requirement_records` list; the two are
assembled on load. Each requirement file is self-contained, which is why `Requirement`
text is duplicated between the set and each record.

Every `RequirementRunRecord` must carry `run_id` matching the document's
`metadata.run_id`.

*(DESIGN_NOTES: "Document-level run record", decision D2.)*

## 10. Re-validate before persisting

Pydantic validates at construction only. `record.rounds.append(...)` bypasses every
check in the file.

```python
RequirementRunRecord.model_validate(record.model_dump())   # re-runs everything
```

Loading from disk already validates, so a mutated record is caught on the next read —
re-validating on write just moves detection closer to the cause.

*(DESIGN_NOTES: "Self-review sweep", item 5.)*

## 11. Technique selection — Layer 2

Layer 1 (which techniques a `SystemType` may use) **is** enforced by the schema. Layer 2
is prompt guidance for the Strategy Selector, and is not enforceable:

- numeric range/threshold → equivalence partitioning / boundary value analysis
- multiple combined conditions → decision table
- a described sequence of states → state-based
- multi-step scenario, or a requirement appearing in `dependencies_for()` → use case
- probabilistic/ML output with no single correct value → metamorphic / statistical threshold
- security or robustness concern on an AI system → adversarial
- timing/latency/throughput constraint → performance (any system type)
- nothing clearly matches → exploratory

`TestStrategy.rationale` is the audit hook: a reviewer checks the stated reasoning
against these rules.

*(DESIGN_NOTES: "How techniques get selected", "Technique eligibility enforced".)*

## 12. Prompt provenance

Fill `StageConfig.prompt_hash` with `prompt_fingerprint(prompt_text)` for every stage,
and bump `prompt_version` by hand when prompts change. The hash is the safety net: two
runs labelled the same but hashed differently are visibly mislabelled.

If the exact prompt text needs recovering later, save each unique prompt once as
`prompts/<hash>.txt`. Not built — the records already carry the key.

*(DESIGN_NOTES: "Run provenance".)*

---

## Things the schema does NOT check, by design

Worth knowing so they are not assumed handled:

- **Domain truth.** The pipeline verifies testability structure, not whether a human's
  answer is factually correct for the system being specified. No design closes this
  without an independent domain oracle.
- **Whether `cap_reason` is meaningful.** It cannot be empty; it can say "n/a".
- **Whether the Quality Checker's judgement is right.** `VAGUE_PRONOUN` in particular is
  expected to be noisy — see Known Limitation 4.
- **Duplicate test cases across dependent requirements.** Known Limitation 1.
