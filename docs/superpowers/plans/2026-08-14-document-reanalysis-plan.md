# Re-running document-level analysis after refinement (Known Limitation 7)

**Read section 0 before section 1.** The recommendation in section 0 is *not* to build this
now. Sections 1 onward exist so that the decision is informed, and so the work is safe if the
decision goes the other way.

---

## 0. Should this be built at all, right now?

**Recommendation: no. Defer it, and write it up as a threat to validity.**

The reasoning, honestly stated:

- The pipeline works and is covered by 1,206 green checks. This change touches the three
  riskiest places in it: `run_document`'s control flow, `DocumentRunRecord`'s shape, and
  `resume_document`'s position logic — the last of which `CLAUDE.md` singles out as having
  drifted once before, which is why `test_harness.py::test_resume_positions` exists.
- The limitation is already documented to a standard most theses never reach: a prediction
  recorded in advance, then confirmed live on `PURE-THEMAS-R6-P`, with the mechanism traced and
  the manual override that rescued it identified. That is publishable material *as a
  limitation*.
- The measured impact is currently one requirement in one scenario. The damage scales with
  document size, which matters for the evaluation phase — but the evaluation phase is blocked
  on an extractor that does not exist yet, so this is not the critical path.
- Refinement rarely changes text at all (81% no-ops under the refusing policy; all no-ops under
  v2 prompts with a non-answering human). The stale-report window is therefore narrow in
  practice today.

**What deferring costs:** if a reviewer asks "does your consistency analysis describe the
refined requirements or the original ones?", the answer is "the original ones", and it has to
be declared rather than defended. That is a real but ordinary limitation, and the notes already
carry it.

**Build it only if** the evaluation phase produces documents where refinement changes text
often enough that stale dependencies visibly distort generated tests. That is measurable — see
section 4 — and it is the trigger to revisit.

---

## 1. If it is built anyway: the safety rules

Non-negotiable, in this order:

1. **Branch first.** `git checkout -b doc-reanalysis`. Never on the working branch.
2. **Full suite green before starting**, and recorded: `python -m design.test_schemas`,
   `design.test_generate_diagrams`, `design.test_generate_arch_diagrams`,
   `design.test_generate_arch_diagrams`'s test, `orchestrator.test_harness`,
   `orchestrator.test_cli`, `orchestrator.test_stages`, `orchestrator.test_config`,
   `orchestrator.test_rotating`. Write the counts down. Any later drop is a regression, and
   knowing the starting number is what makes that detectable.
3. **One step per commit**, each with its own green suite. Five steps below; five commits. A
   step that cannot be made green is reverted, not patched forward.
4. **Behaviour-preserving by default.** The second pass is *off* unless a new config flag turns
   it on. With the flag off, every existing run, fixture and recorded result must behave
   identically — that is the property the tests are checking, not the new feature.
5. **No schema change in the same commit as a control-flow change.** They are separately
   revertible only if they are separately committed.

## 2. The five steps

### Step 1 — record shape only (no behaviour)

Add optional fields to `DocumentRunRecord` for the second-generation reports, defaulting to
`None`: something like `consistency_report_final` / `dependency_report_final`, plus a
`reanalysis_skipped_reason` for the "text unchanged" case.

- Nothing reads them yet. Nothing writes them yet.
- `design/test_schemas.py` gains cases: absent (the default, every existing record still
  validates), present, and the mirror check at the requirement level if one applies.
- Then run `python -m design.generate_diagrams` and `python -m design.generate_arch_diagrams`
  per `CLAUDE.md` — they validate as they generate and are *meant* to fail on structural drift.

**Green gate:** every existing test passes untouched, because the fields are optional.

### Step 2 — the change detector, as a pure function

A standalone helper: given the original `RequirementSet` and the per-requirement final texts,
return whether any text actually differs. No API calls, no I/O, fully unit-testable.

- Tests: no records at all; all no-op rewrites; one changed; several changed; a requirement
  that errored before producing any text.

**Green gate:** new tests pass, nothing else changes.

### Step 3 — config flag, defaulting to off

Add the flag to `RunConfig`/`ResolvedRunConfig` (`orchestrator/config.py`), default `false`, so
every existing YAML keeps working unchanged.

- `orchestrator/test_config.py` gains: flag absent (defaults off), explicitly false, explicitly
  true.

**Green gate:** all fourteen behaviour-scenario configs still load. That check is cheap and it
is the one that proves nothing on disk broke.

### Step 4 — the second pass, gated

Split `run_document`'s loop: refine all requirements, consult the step-2 detector, re-run
`run_document_stages` only if the flag is on *and* text changed, then run strategy and test
generation with whichever reports are current.

- With the flag **off**, the code path must be identical to today's. Prove it: re-run one
  recorded scenario with the flag off and diff the run directory against the committed one.
- With the flag **on** and no text changed: no extra API calls, `reanalysis_skipped_reason`
  populated.
- With the flag **on** and text changed: two document-stage generations recorded.

**Green gate:** `orchestrator/test_harness.py` in full, plus a new test for each of the three
cases above using the existing fake stage functions — no real API calls in tests.

### Step 5 — resume, last and most carefully

`resume_document` must know about the second document-stage phase. This is the step most likely
to go wrong.

- Extend `test_resume_positions` *first*, with the new cases failing, then make them pass. The
  spec drifted here once before precisely because prose described it and nothing executed it.
- Cases: interrupted before the second pass; interrupted during it; interrupted after it;
  flag off entirely (must behave exactly as today).
- Mutation-check it: break the new position rule deliberately and confirm the suite goes red.
  `CLAUDE.md`'s own rule — a check that cannot fire is untested.

**Green gate:** full suite, plus the mutation run.

## 3. What must not be touched

- `design/schemas.py`'s existing fields and validators — additions only, no edits.
- The eight prompt files. A prompt edit here would confound every comparison already recorded.
- The fixtures, and the fourteen existing scenario configs.
- `answer_policy_driver.py` and `answers.json`.
- The recorded run directories under `docs/superpowers/results/`. They are evidence.

## 4. How to know whether it was worth it

Run one scenario where refinement actually changes text — `scn-04-conflict-numeric` with the
live transcript is the known case, since `PURE-THEMAS-R6-P` genuinely gets fixed — with the flag
on and off. Compare: does the second pass drop the stale `inconsistent` flag that forced the
manual `user_confirms_resolved` override?

If yes, that is the demonstration, and it is one paragraph in the thesis with a before/after.
If no, revert the branch; the change did not do what it was designed to do.

## 5. Fallback

If any step cannot be made green within a reasonable attempt: `git checkout` back to the working
branch and leave the limitation documented. Nothing in the thesis depends on this being fixed —
only on it being understood, and it already is.
