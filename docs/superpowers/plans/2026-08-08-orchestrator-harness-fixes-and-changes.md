# Orchestrator + Simulation Harness — Fixes and Changes Log

Companion to `docs/superpowers/plans/2026-08-08-orchestrator-harness-plan.md`. Records
every bug found and fixed during implementation (plan-text bugs, self-caught
implementation bugs, review-found bugs) and every issue deliberately deferred or left
as documented tech debt, so none of it has to be rediscovered.

Merged to master @ `dcb1c80` (2026-08-08). Post-merge, an external review found two more
issues (§7) — final state after both: `python -m design.test_schemas` → 270 checks,
`python -m orchestrator.test_harness` → 105 checks.

---

## 1. Plan-authoring bugs (caught before or during dispatch, not by any subagent)

| Where | Bug | Fix |
|---|---|---|
| Task 12's plan text | Dead code: `first_fns` was built twice, the first assignment immediately discarded | Caught in the pre-flight conflict scan before Task 1 was dispatched; merged into one clean `StageFns` construction |
| Task 4's plan text | `CLAUDE.md`'s "A spec nobody executes drifts" rule cites `test_schemas.py::test_resume_positions` — Task 4 moves that test, so the citation would go stale | Added a step to Task 4 updating the citation to `orchestrator/test_harness.py::test_resume_positions` |
| Task 3's plan text | Commit step's `git add` list omitted the regenerated `.mermaid` files (deliberately tracked, not gitignored) | Fixed after Task 2's implementer independently caught the identical gap in Task 2's own commit step and fixed it forward |

## 2. Bugs implementers found and fixed forward (no review loop needed)

These were caught by the implementer while working the brief, fixed in the same
commit, and reported — not escalated, since they were self-contained corrections to
test fixtures or brief arithmetic, not production-code defects.

| Task | Bug | Fix |
|---|---|---|
| 2 | Same `git add` omission as above, independently found | Added `.mermaid` diffs to the commit |
| 3 | Brief's test literal asserted `== 220` for a token-sum check; correct value is `240` (200+40) | Corrected the literal; verified by direct arithmetic |
| 7 | Brief's own negative test ("caller bug still crashes") reused an already-exhausted `Scripted` fixture, so the crash it produced was `StageFailed` from inside `call_stage`, not the intended `AttributeError` from outside it | Gave that test a fresh, unconsumed fixture so the crash genuinely originates outside `call_stage` |
| 8 | Brief omitted the instruction to register the new test in `main()`'s run tuple | Added it — otherwise the new test would never execute |
| 10 | `run_requirement` called `_run_refine_loop` unconditionally, which crashes (`AttributeError` on `rewrite.refined_text`) when resuming a record already past the refine loop (`STRATEGY_SELECTOR`/`TEST_GENERATOR`) | Guarded the call with `if stage in (CLASSIFIER, QUALITY_CHECKER, REFINER):`; added a mutation-tested regression test |
| 10 | Scenario 3 fixture scripted a `ClarifyingQuestion.issue_id` (`"FRESH-2"`) that the real reconciliation logic would never produce — the reconciled report only ever contains `"FRESH-1"` (reused from round 1) | Corrected the fixture to reference the id reconciliation actually assigns |
| 10 | Scenario 4 fixture scripted a rewrite's `answers_used` with text (`"still working on it"`) that didn't match what the test's own `human_fns.answer_questions` actually returns (`"a"`) | Corrected the fixture text to match |
| 12 | Scenario 8 fixture: `RequirementSet(doc_id="one-req", ...)` contained a requirement whose `source_doc_id="harness-doc"` — violates `RequirementSet._requirements_belong_to_this_document` | Aligned the doc id throughout that test |

Several tasks (5, 6, 9) also found the brief's *predicted final check-count* was off by
one or two and reported the corrected, independently-verified number rather than
trusting the brief's arithmetic — no code changes, just accurate reporting.

## 3. Bugs found by task review, requiring a fix round

### Task 4 — `resume_at`'s terminal branch lost coverage in the move

Moving `resume_at`/`test_resume_positions` from `design/test_schemas.py` to
`orchestrator/`, one of the original 8 checks — the one covering the `return None`
("nothing left to resume") branch — was dropped rather than moved.

**Fix:** added the missing check to `orchestrator/test_harness.py`, built from that
file's own local fixtures (no import from `design/test_schemas.py`, preserving the
one-way import rule). Re-review: addressed, no new breakage.

### Task 10 — four Important findings in `run_requirement`

This was the highest-risk task in the plan and needed a full fix round.

1. **`_reconcile_issue_ids` didn't mint new ids for unmatched issues.** Contract item
   4 requires the orchestrator, not the LLM, to own `Issue.id`. The original code only
   rewrote ids for *matched* issues; an unmatched issue kept whatever id the LLM
   minted that round — which the LLM renumbers from 1 every round, so it reliably
   collided with an id already used earlier in the record. This produced an **uncaught
   `ValidationError`**, sometimes after stage 3/4 had already run and been paid for.
   **Fix:** unmatched issues now get a freshly minted id guaranteed not to collide
   with anything used anywhere in the record's history (not just the previous round).
2. **A checker re-flagging a suppressed issue crashed.** If reconciliation mapped a
   fresh LLM id back onto an id the human had confirmed resolved, the resulting round
   failed schema validation (`"suppresses [...] but its quality_report raises them
   anyway"`) instead of degrading gracefully — a real risk given `VAGUE_PRONOUN` is
   documented as expected to be noisy. **Fix:** reconciled issues whose id is in the
   round's `suppressed_issue_ids` are now dropped before constructing the report, and
   `passed` is recomputed from the post-drop list.
3. **Resuming at `TEST_GENERATOR` re-ran the strategy selector**, redoing already-
   succeeded work — a direct violation of contract item 6 ("nothing else is redone"),
   and it made the stored strategy nondeterministic across a resume. **Fix:** if
   `record.test_strategy` is already set, it's reused directly.
4. **The cap decision could construct a schema-invalid record, two ways:**
   - `max_revisions=1` could let the cap fire with zero rewrites ever produced,
     violating the schema's "at least one round must have produced a rewrite" rule.
     **Fix:** `run_requirement` now rejects `max_revisions < 2` at entry.
   - A `CAP_GENERATED` record that later failed at stage 3/4, then resumed and got a
     `CAP_STOPPED` decision, could retain `test_strategy`/`test_plan` or stage-3/4
     errors — both forbidden on `CAP_STOPPED`. **Fix:** the transition to
     `CAP_STOPPED` now strips that state before constructing the final record.

While fixing #4b, the implementer added a second guard as a precaution, then removed
it after a mutation test showed the suite stayed green without it — the existing
`n >= max_revisions` check already covered that path. Documented as dead code removed
per this project's own rule against unreachable checks, not left in "just in case."

All five items (4 findings + the self-caught over-fix removal) were independently
re-verified by re-review, including two properties no test directly covered
(collisions against rounds further back than the immediately-preceding one; two
unmatched issues minted in the same round) — checked via a live probe script, not
just by reading. Addressed, no new breakage.

## 4. Final whole-branch review — fix wave (before merge)

The final review found the individual task reviews structurally couldn't catch:
drift between a task's edit and a document owned by a different task, plus two test
checks that couldn't fail no matter what the code did.

**Important:**
1. `design/test_schemas.py` and `design/generate_diagrams.py`'s own module docstrings
   still told the reader to run them as bare scripts (`python design/test_schemas.py`)
   — a command Task 1 made obsolete (both packages are now `python -m` only). Running
   the old form now crashes with `ModuleNotFoundError`. Fixed both docstrings.
2. `design/ORCHESTRATOR_CONTRACT.md` still cited `resume_at`'s test at its pre-Task-4
   location. Fixed the citation.
3. Two test checks structurally couldn't fail:
   - Scenario 11's check asserted a property (`RunMetadata` covers every stage with a
     non-empty `prompt_hash`) that a schema validator already guarantees for *any*
     valid `RunMetadata` — it tested Pydantic, not the orchestrator, leaving contract
     item 12 with no real coverage. **Fixed** to assert something the orchestrator
     actually does: that the `metadata` object passed into `run_document` is the same
     one that ends up on the returned record.
   - A leftover check in the Task 8 `DEGRADED` test recomputed `DocumentOutcome`
     inline and asserted its own recomputation — no orchestrator code ran. **Deleted**
     (with a comment pointing at Task 12's `test_document_stage_retry_within_run`,
     which now provides real coverage of that derivation through the actual code path).

**Minor (bundled into the same fix wave):**
4. `CLAUDE.md`'s reading table still said the contract has "12 things" (now 14) and
   `test_schemas.py` has "265 checks" (now 270). Updated both.
5. `decide_at_cap`'s runtime guard validated the returned *outcome* but not
   `cap_reason` — a falsy reason surfaced later as an opaque `ValidationError` instead
   of a clear error naming the human function as the cause. Added a symmetric check.
6. `set(suppressed_ids)` was being rebuilt on every loop iteration. Hoisted out.
7. `write_document_run`'s `mkdir` call was missing `parents=True` (present in the
   sibling `write_requirement_run` call) — worked only because the parent directory
   happened to already exist. Made consistent.
8. Three unused imports, plus one test comparing `RunOutcome` by `.value == "completed"`
   (string) instead of enum identity like every other test in the file. Cleaned up.

Re-review: all 8 addressed, no new breakage.

## 5. Deliberately deferred (not fixed — next phase, not this one)

Flagged by the final review as reachable only once `orchestrator/stages.py` is wired
to a real, nondeterministic LLM — not before:

- **No test covers `_run_refine_loop`'s genuinely-mid-refinement resume branch**
  (a round where the human has been asked a question but the rewrite is still
  outstanding). Structurally distinct from the already-capped resume case Task 10
  covers. Add this scenario before real LLM calls can actually interrupt mid-round.
- **`QualityReport` is rebuilt with `requirement_id=req.id`, silently discarding
  whatever `raw_report.requirement_id` the LLM actually returned.** A checker
  answering about the wrong requirement is currently relabeled instead of failing —
  the same class of problem as `VAGUE_PRONOUN`'s documented noisiness (Known
  Limitation 4), but silent. Needs a named design decision once a real model is
  behind `check_quality`, not a quiet one-line fix now.

## 6. Reviewed and deliberately left as documented tech debt

Each of these was raised, considered, and explicitly ruled "leave" — either because
it's unreachable given current call patterns, or the cost of fixing now exceeds the
value versus fixing later if it ever becomes reachable.

- **`Requirement.id` is used unsanitized in on-disk filenames** (`requirements/<id>.json`).
  No character restriction on the schema type today; unreachable in practice since ids
  come from a curated corpus, not untrusted input. Worth a charset constraint on
  `Requirement.id` the next time `design/schemas.py` is touched for another reason.
- **Stale strategy reuse if `max_revisions` is raised between resumes of the same
  record.** Only reachable if a caller varies `max_revisions` across resumes of one
  run, which nothing in this codebase does.
- **`retry_document_stage`'s retry-count bump keeps the *original* failure's `kind`
  and `message`**, discarding the newer failure's detail if it failed for a different
  reason on retry (e.g. TRANSPORT then VALIDATION). Contract's "bump, don't duplicate"
  rule is silent on which detail should survive; this is a defensible reading, but it
  does lose one data point for the "how often does model X emit invalid output"
  question the thesis cares about.
- **`retry_document_stage`'s failure branch reorders the `errors` list** (moves the
  retried stage's entry to the end). `errors` is documented as a log with no ordering
  guarantee, and at most one entry ever exists per document stage, so this has no
  observable effect today.
- **`retry_document_stage` would force `DEGRADED`/`COMPLETED` even from an
  `IN_PROGRESS` record**, which is a state `run_document_stages` never actually
  produces (both document stages always run before the first write). Unreachable
  given current call patterns.
- **`run_document`/`resume_document` return a `model_copy` without re-running
  Pydantic's validators** on the final in-memory object (only the on-disk copies get
  re-validated via `write_document_run`/`write_requirement_run`). No failure mode is
  currently reachable from within these functions — verified against a real
  multi-requirement, multi-round integration probe during the final review — but it's
  an inconsistency worth a comment for anyone modifying these functions later.
- Scenario 8 (transport failure → error → resume → finish) happens to fail at the
  *first* stage (`CLASSIFIER`), so "resume doesn't redo earlier work" is vacuously
  true for that specific scenario. Real coverage of that property lives in Task 10's
  two dedicated resume-skip regression tests, not scenario 8 — noted so nobody
  mistakes scenario 8 alone as proving it.

## 7. Post-merge — external review (found by mutation testing, not a task/branch review)

Two findings surfaced after merge, both independently verified before fixing.

### Backoff had no test anywhere in the suite

Mutation-tested `orchestrator/pipeline.py`: 12 deliberate breakages, 11 caught.
The one that slipped through was the backoff guard itself:

```python
if attempt < max_attempts - 1:
    throttle.sleep_fn(backoff_seconds(attempt))
```

Every scenario in the suite passes `backoff_seconds=lambda a: 0.0`, so the schedule is
stubbed to zero everywhere and nothing observes the sleep calls. Deleting the guard
entirely (`if False:`) still passed all 103 harness checks and all 270 schema checks —
verified independently before fixing, not just taken on the report's word.

This mattered because the throttle+backoff design exists specifically to avoid 429
storms on free-tier quotas; a future edit that moved or dropped that line would have
caused retries to fire back-to-back with no delay, and nothing in the suite would have
noticed until a real run hit it.

**Fix:** added `test_backoff_timing`, mirroring `test_throttle`'s existing discipline
of asserting the actual recorded delay values (`[10.0, 20.0]` for a 3-attempt run with
a distinguishable schedule), not just that a sleep happened — covering both
`call_stage` and `call_document_stage`. Re-ran the same mutation afterward to confirm
the new test catches it: it does, failing exactly the two new checks and nothing else.

### Line-ending churn on tracked files

An external review reported 16 evaluation-dataset XMLs showing as modified with
byte-identical content after normalizing line endings, attributing it to "some tool"
since `core.autocrlf` read as unset.

**Root cause, found on verification:** `core.autocrlf=true` is set at the **system**
git config level (`C:/Program Files/Git/etc/gitconfig`), not local — a local-only check
reads it as unset and misses this. With `core.autocrlf=true`, git converts LF↔CRLF on
checkout/commit; the specific pattern this branch also hit was
`design/generate_diagrams.py` writing its output in Python's default text mode, which
uses the platform line separator (CRLF on Windows) regardless of git config — so every
`python -m design.generate_diagrams` run reintroduced churn on the five `.mermaid`
files, independent of the dataset-XML issue.

No datasets/ files or CLAUDE.md were actually dirty at the point this was checked
(clean tree at verification time) — the churn is real and reproducible, just transient
depending on what last touched the working tree.

**Fix:** added `.gitattributes` (`* text=auto eol=lf`) at the repo root, pinning line
endings explicitly regardless of the user's `core.autocrlf` setting. Verified by
running `git add --renormalize .` (no unexpected diffs beyond the pending real change)
and by regenerating diagrams twice in a row post-fix (zero diff the second time).
`design/generate_diagrams.py` itself was left unchanged — the generator's CRLF output
on Windows is real but harmless now that `.gitattributes` normalizes it back to LF on
the next `git add`; switching the generator to `newline="\n"` would remove the need for
that normalization step but wasn't part of what was asked.
