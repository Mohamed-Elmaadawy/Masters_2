# Implementation log

What changed, why, and what it changed. Newest first. One entry per change.

This file is deliberately **not** where reasoning lives — that is `design/DESIGN_NOTES.md`.
An entry records the *event*: what was touched, which decision it implements, and what
measurably differed afterwards. Link to the `DESIGN_NOTES.md` section rather than
restating it.

Entry format:

```
## YYYY-MM-DD — one-line summary

**Changed:** files/modules touched, concretely.
**Why:** the decision or finding this implements — link to DESIGN_NOTES.md / a Known
Limitation number / a run's RESULTS.md.
**Impact:** what behaves or measures differently now, and how that was verified (tests
run and their counts, a re-run's numbers, or "documentation only — no behavioural
change").
```

An entry with an empty or hand-waved **Impact** is worse than no entry: "improved the
prompt" records nothing. If the impact is unknown because it has not been measured yet,
say exactly that and name the measurement that would settle it.

---

## 2026-08-17 — Task 6 review round 4: two direct-call gaps, one documentation correction

**Changed:** `evaluation/runner.py` (`run_b2_resume` now compares
`checkpoint.requirement_set_hash` and all five checkpoint config fields directly
against its own arguments, before calling `_b2_answer_and_generate` — previously only
`main`'s CLI path checked these, so a direct caller bypassing `main` entirely was
unprotected), `evaluation/schemas.py` (`_awaiting_answers_checkpoint_is_valid`'s
retry-sequence check tightened from "no `success` before the last attempt" to "every
attempt before the last must be `failed` or `partial`", closing a gap where a `fatal`-
then-`success` shape — impossible for `_call_once` to actually produce, but not
rejected by the old condition — passed validation), `evaluation/arm_p_report.py`
(docstring corrected, no behavior change: states explicitly that only cost is resolved,
wall-clock is not, `None` must never be presented as satisfying the protocol's
wall-clock requirement, and adds a five-step manual timestamp-and-persist checklist for
the one real arm-P run before the frozen evaluation), and `evaluation/test_runner.py`
(4 new checks).

**Why:** a fourth review found two gaps in round 3's own fixes and one place round 3's
write-up overclaimed what it had done. (1) confirmed with a direct repro before fixing:
calling `run_b2_resume` directly with a checkpoint whose `requirement_set_hash`
didn't match the given `requirement_set` proceeded and made a real call — the CLI's
checks are not a substitute for the function's own contract when called directly. (2)
confirmed with a direct repro: `_awaiting_answers_checkpoint_is_valid` accepted a
`fatal`-then-`success` attempts list, a sequence `_call_once`'s own retry logic can
never produce (a `StageCallFatal` short-circuits with exactly one attempt, never
retries) but which a hand-edited or corrupted checkpoint file could still contain. (3)
round 3's `arm_p_report.py` docstring read as if building `compute_arm_p_cost` settled
Task 6 finding 3 entirely; it settles only the cost half — wall-clock for arm P remains
genuinely unmeasured for any run so far, and stays that way until a human times the one
real evaluation run by hand, per the review's explicit direction not to build timing
infrastructure for it.

**Impact:** `evaluation.test_runner` 145 (was 137) — two tests proving the direct-call
`run_b2_resume` rejections with a spy adapter, one confirming a genuinely matching
direct call still succeeds, two for the tightened retry-sequence check (fatal-then-
success rejected, partial-then-success still accepted). All other suites unchanged:
mechanical 57, blinding 50, config_parity 26, pricing 21, arm_p_report 13; nine
existing suites unchanged. Both new guarantees mutation-tested: the direct-call
requirement-set-hash check (broken → `IndexError: pop from empty list`, the spy
adapter's `.complete()` was actually reached), the tightened retry-sequence check
(broken → explicit assertion failure) — both reverted, confirmed green again. `git diff
--check` clean. No API call made
(`GEMINI_API_KEY`/`GEMINI_API_KEYS`/`GROQ_API_KEY`/`GROQ_API_KEYS` confirmed unset). No
frozen evaluation input, historical result, or `docs/EVALUATION_PROTOCOL.md` touched;
`requirements.txt` unchanged. Not committed — staged for another review.

---

## 2026-08-17 — Task 6 review round 3: five findings, kept thesis-scale on purpose

**Changed:** `evaluation/schemas.py` (`BaselineRunOutput` gains `requirement_set_hash`
(required) and `checkpoint_phase: Optional[Literal["awaiting_answers"]]`, plus a new
validator `_awaiting_answers_checkpoint_is_valid` enforcing the full structural shape a
pre-answer checkpoint must have), `evaluation/runner.py` (new `_requirement_set_hash()`
helper; new `_validate_no_path_collisions`; `run_b2`'s checkpoint construction sets
`checkpoint_phase="awaiting_answers"`; `run_b2_resume` checks it; `main`'s `b2-resume`
path checks checkpoint_phase first, compares all five config fields (was four —
`timeout_seconds` was missing), compares `requirement_set_hash`, and runs the
path-collision check, all before adapter construction), new `evaluation/arm_p_report.py`
(`compute_arm_p_cost` reusing `evaluation/pricing.py`'s frozen snapshot over a real
`DocumentRunRecord`; `arm_p_wall_clock_seconds` returns `None`, documented as to why),
and `evaluation/test_runner.py`/`test_pricing.py`/new `evaluation/test_arm_p_report.py`
updated/added to match.

**Why:** a third review found five gaps, scoped explicitly mid-review to "the smallest
local solution... this is research software, not production infrastructure" (recorded
in `design/DESIGN_NOTES.md`'s new entry, "Task 6 review round 3"): (1) a completed or
differently-failed B2 output could be resumed and generate again, since round 2's
checks never proved the file was actually the pre-answer state; (2) `doc_id` matching
does not prove requirement TEXT is unchanged, and is meaningless when both sides are
`None`; (3) `docs/EVALUATION_PROTOCOL.md` requires cost/wall-clock for all three arms,
and arm P had neither persisted — cost was recoverable from existing data and built;
wall-clock is not recoverable at all (`design/schemas.py`'s attempt types record no
duration, confirmed by reading the schema) and is documented as a genuine limitation
rather than fabricated or approximated; (4) the "five-field config" claim from round 2
was actually a four-field comparison — `timeout_seconds` was omitted; (5) nothing
stopped an output path from overwriting an input path.

**Impact:** all six `evaluation/` suites green — runner 137 (was 109), mechanical 57,
blinding 50, config_parity 26, pricing 21 (unchanged from round 2), new arm_p_report 13;
304 checks total (was 263). All nine existing suites unchanged: schemas 351, arch
diagrams 88, harness 489, CLI 65, stages 163, stage_fns 67, config 101, rotation 18.
Four central guarantees mutation-tested (checkpoint structural validity, requirement-set
content-hash binding, timeout comparison, path-collision check): each broken on purpose,
confirmed red, reverted, confirmed green — one mutation's first test attempt was itself
masked by a redundant check and had to be re-isolated before it correctly went red,
recorded as its own finding in `DESIGN_NOTES.md` since it is exactly what mutation
testing exists to catch. Also fixed while writing these tests: six CLI-level test
fixtures pointed `--answers-json` at a nonexistent file, relying on an earlier check to
reject first — harmless for the original passing tests, but would have silently
masked a real regression in whichever check ran first. `git diff --check` clean. No API
call made (`GEMINI_API_KEY`/`GEMINI_API_KEYS`/`GROQ_API_KEY`/`GROQ_API_KEYS` confirmed
unset throughout). No frozen evaluation input, historical result, or
`docs/EVALUATION_PROTOCOL.md` touched (one real historical run directory was read by
`test_arm_p_report.py`'s smoke test, never modified); `requirements.txt` unchanged. Not
committed — staged for another review.

---

## 2026-08-17 — Task 6 review round 2: eight more edge cases, several introduced by round 1's own fixes

**Changed:** `evaluation/mechanical_checks.py` (`check_technique_eligibility` gains
`known_requirement_ids`, skips ids A1 already reports unknown; `_PLACEHOLDER_EXACT_PHRASES`
added alongside the keyword list, registering `[specified user needs]` by exact text),
`evaluation/blinding.py` (`pool_and_blind` identity key changed to `(arm, doc_id)`, requires
`doc_id` on every entry once a pool spans more than one document; `seed: int` replaces the
`rng: random.Random` parameter, constructed internally and carried on `BlindingResult.seed`;
`write_blinding_result` loses its own `seed` parameter, reads `result.seed`),
`evaluation/schemas.py` (`BaselineRunOutput` gains `timeout_seconds`/`output_mode`),
`evaluation/config_parity.py` (`frozen_arm_p_config` now checks every resolved stage shares
one `(provider, model, temperature, output_mode)` tuple before returning one), `evaluation/
runner.py` (new `_b2_answer_and_generate` shared tail; new `run_b2_resume`; new
`_validate_output_destinations`; new `b2-resume` CLI subcommand with checkpoint/config/
doc_id/prompt-hash mismatch rejection before adapter construction; `--answers-json` file
now read before adapter construction, not after; final output write now atomic;
`KeyboardInterrupt` caught around the CLI dispatch), and all five `evaluation/test_*.py`
suites updated/extended to match.

**Why:** a second review of the previous entry's fixes (`design/DESIGN_NOTES.md`, "Task 6
review round 2: eight more edge cases in the first fix round itself") found eight real
gaps — five were genuinely new edge cases (blinding cross-document identity, seed
truthfulness, B2 resumability, output-destination/atomicity, config field completeness,
all-stage parity), two were regressions the FIRST fix round itself introduced (A2's
independent-label fix fighting A1's unknown-id handling; A3's keyword-only rule losing the
one real placeholder it was built to catch). Each confirmed with a failing case before
fixing — either a genuine pre-existing test break (mechanical_checks/blinding signature
changes), an explicit new test written to the new contract and run to confirm it matched, or
a discovered gap (the `--answers-json` pre-adapter timing issue, found while writing the
resumability end-to-end test) fixed alongside its discovery.

**Impact:** all five `evaluation/` suites green — runner 109 (was 72), mechanical_checks 57
(was 51), blinding 50 (was 40), config_parity 26 (was 22), pricing 21 (was 20); 263 checks
total (was 205). All nine existing suites unchanged: schemas 351, arch diagrams 88, harness
489, CLI 65, stages 163, stage_fns 67, config 101, rotation 18. Six guarantees
mutation-tested (A1/A2 coexistence, cross-document blinding identity, seed truthfulness,
no-second-question resume, atomic destination preflight, all-stage config parity): each
broken on purpose, confirmed the associated test/suite went red (explicit failures or an
uncaught exception/spy-adapter `AssertionError`, both valid red), reverted, confirmed green
again. `git diff --check` clean. No API call made anywhere
(`GEMINI_API_KEY`/`GEMINI_API_KEYS`/`GROQ_API_KEY`/`GROQ_API_KEYS` confirmed unset
throughout). No frozen evaluation input, historical result, or `docs/EVALUATION_PROTOCOL.md`
touched; `requirements.txt` unchanged. Not committed — staged for another review.

---

## 2026-08-17 — Task 6 (E2 baseline arms) offline machinery, plus a review round fixing seven gaps

**Changed:** new package `evaluation/` — `schemas.py` (`BaselineCallConfig`,
`BaselineAttempt`, `BaselineRunOutput`, `ClarificationQuestion`/`Answer`,
`BaselineTestCaseBatch`/`QuestionBatch`), `prompts/b1_baseline.txt` /
`b2_questions.txt` / `b2_generate.txt`, `runner.py` (`run_b1`/`run_b2`, CLI),
`mechanical_checks.py` (A1–A5, `run_part_a_checks`), `blinding.py` (`pool_and_blind`,
`write_blinding_result`, CLI), `config_parity.py` (`frozen_arm_p_config`,
`enforce_fair_config`), `pricing.py` (`FROZEN_PRICING_SNAPSHOT`, `compute_cost`),
`atomic_io.py` (`atomic_write_json`), and five test suites (`test_runner.py`,
`test_mechanical_checks.py`, `test_blinding.py`, `test_config_parity.py`,
`test_pricing.py`). No file outside `evaluation/` touched.

**Why:** handover doc Task 6 (E2), scoped and built for review per its own instruction
("write the B1/B2 prompts and the runner for review before executing anything"). A
first pass was reviewed and found seven real gaps — recorded in full, with the
reasoning for each fix, in `design/DESIGN_NOTES.md`'s new entry "Task 6 (E2 baseline
arms) offline machinery, and a review round that found seven real gaps in it": (1) A2
was scoring technique eligibility against arm P's own Classifier output instead of the
independently captured operator label; (2) A5 omitted zero-count combinations, making a
baseline's silent omission of a whole requirement invisible; (3) the blinding tool had
no operational writer — added atomic, separately-pathed scoring/mapping files with the
shuffle seed recorded only in the mapping; (4) nothing enforced B1/B2 using arm P's
actual frozen model/temperature/output_mode before constructing an adapter; (5) no
pricing snapshot or cost was persisted, and partial-attempt tokens weren't accounted
for; (6) a B2 answer-source failure (EOF, interruption) propagated as an uncaught
exception and discarded the already-successful questions call's data; (7) A3's
placeholder check flagged any `[...]` span, a real false-positive generator (`[0,1]`,
`[Ctrl+C]`, `[REQ-1]`).

**Impact:** all five `evaluation/` suites green — runner 72, mechanical_checks 51,
blinding 40, config_parity 22, pricing 20 (205 total, up from an initial 94 before the
review-fix round). All nine existing suites unchanged and green: schemas 351, arch
diagrams 88, harness 489, CLI 65, stages 163, stage_fns 67, config 101, rotation 18.
Four guarantees mutation-tested (A5's zero-count pre-population, A2's missing-key
rejection, `enforce_fair_config`'s temperature comparison, `write_blinding_result`'s
same-path rejection): each broken on purpose, confirmed the associated test went red,
reverted, confirmed green again. `git diff --check` clean. No API call made
(`GEMINI_API_KEY`/`GEMINI_API_KEYS`/`GROQ_API_KEY`/`GROQ_API_KEYS` confirmed unset
throughout). No frozen evaluation input, historical result, or
`docs/EVALUATION_PROTOCOL.md` touched; `requirements.txt` unchanged (no new
dependency). Not committed — staged for another review.

---

## 2026-08-17 — Task 5 (S4, human-supplied `NON_ATOMIC` splits) evaluated and deferred, documentation only

**Changed:** `design/DESIGN_NOTES.md` -- new subsection "S4 evaluated and deferred, not
built" appended after S4's existing "read the frequency count carefully" note. No code,
schema, or test changed.

**Why:** handover doc Task 5 gates on "Task 3 reports the genuine `NON_ATOMIC` frequency"
over the frozen evaluation corpus (`datasets/pure-extracted/`, 805 requirements). That
measurement was scoped in the prior session (script, manual-review split, ~$3/~54min cost
estimate) but deliberately not run: running the Quality Checker over the held-out
evaluation set to decide whether to build a feature would let evaluation data shape the
system under evaluation, the exact contamination the freeze boundary
(`docs/EVALUATION_PROTOCOL.md`) exists to prevent. The only frequency evidence on hand is
`1/34` from the project's own design-stage illustrative set, not a sample of the frozen
corpus -- generalizing it there would be an unverified claim, and using it to close the
Task 3 gate would be the substitution the handover explicitly guards against. Runtime
splitting also carries its own cost independent of the frequency number (new schema
fields, provenance/origin-id scheme, requirement-identity and resume-position changes,
dependency re-derivation across five call sites) and automatic/model-generated splitting
stays rejected on correctness grounds regardless of how common the case turns out to be.

**Impact:** documentation only -- no behavioral change. `orchestrator/pipeline.py`,
`design/schemas.py` unmodified; no new `RunOutcome` member, no `RefinerAnswer` field, no
original-to-fragment mapping field (the run schema has none). The practical workaround
(manual pre-pipeline splitting, traceability kept as an operator-maintained external
sidecar or source-document record, not a pipeline-recorded field) is now the recorded
standing default for use outside this thesis evaluation only -- it must not be applied to
the frozen 805-requirement evaluation subset, since splitting there after the freeze
would change requirement-set membership and every denominator computed against it. During
the evaluation run, a genuine `NON_ATOMIC` case is left unchanged and reported as a
documented limitation, not worked around. Provenance-preserving in-pipeline decomposition
remains an open research gap, to revisit after the evaluation freeze closes, not before.
No suite re-run needed (no code touched).

---

## 2026-08-16 — S3 review fixes, round 2: explicit schema-version allow-list, document-metadata resume check

**Changed:** `design/schemas.py` -- new `CURRENT_SCHEMA_VERSION = "1.3"` constant, used
by both `RunMetadata.schema_version`'s default and `_covers_every_stage`, which now
uses an explicit allow-list (`"1.2"` -> legacy eight stages, `CURRENT_SCHEMA_VERSION` ->
current `ALL_STAGES`, anything else -> rejected) instead of the previous "1.2 is legacy,
else current" branch that silently accepted any other version string.
`orchestrator/cli.py`'s `_do_resume` gains two checks right after `read_document_run`,
before any adapter is constructed: `record.metadata.schema_version`/stages must be
current (not just `run_config.json`'s), and `run_config.json`'s stages must agree with
`document.json`'s. Tests: four new `rejects()` cases in
`design/test_schemas.py::test_schema_version_1_2_legacy_compatibility` (`"1.1"`,
`"1.4"`, `"9.9"`, `"not-a-version"`, all paired with the full current stage set);
new `orchestrator/test_cli.py::test_resume_current_config_with_legacy_document_metadata_rejected`.
`design/DESIGN_NOTES.md`, new entry "S3 review fixes, round 2."

**Why:** a second independent review of the still-uncommitted S3 work. Both gaps
confirmed empirically before fixing: `schema_version="9.9"` with the current ten-stage
set validated successfully under the round-1 fix; a current `run_config.json` paired
with a legacy `document.json` reached `adapter_factories[provider]()` (a spy factory
that raises if called was observed to actually be called) before this round's fix.

**Impact:** all recorded suites green -- schemas 351 (was 347), generate_diagrams 13,
arch diagrams 88, harness 489, CLI 65 (was 62), stages 163, stage_fns 67, config 101,
rotation 18. The new document-metadata resume gate was mutation-tested (replaced with
`if False`, confirmed the new test's assertions went red, reverted). `git diff --check`
clean (pre-existing CRLF notices only). No historical `docs/superpowers/results/` file
modified; no API call made. Not committed -- reported for review first.

## 2026-08-16 — S3 review fixes: fresh-run gate, legacy schema compatibility, active configs, CLI summary

**Changed:** `orchestrator/pipeline.py` -- `run_document` now applies the same
`_pass_a_concluded` all-requirements gate `resume_document` already applied before
running the second document analysis (previously unconditional); `run_requirement_pass_a`
now resets a stale `outcome=ERROR` back to `IN_PROGRESS` before retrying a record (a
second, independent bug the gate fix's own test surfaced). `design/schemas.py` -- new
frozen constant `SCHEMA_VERSION_1_2_STAGES` (the original eight-stage set);
`RunMetadata.schema_version` bumped 1.2 -> 1.3; `_covers_every_stage` branches on
`schema_version` so pre-S3 (1.2) records are checked against the legacy eight-stage set,
not the current ten. `orchestrator/config.py` -- `ResolvedRunConfig._stages_cover_exactly_all_stages`
now accepts either the current `ALL_STAGES` or the legacy eight-stage shape, so a pre-S3
`run_config.json` loads for inspection. `orchestrator/cli.py` -- `_do_resume` explicitly
refuses to resume a run whose config has the legacy eight-stage shape, before any adapter
is constructed, naming the actual/current stage counts and saying a new run is required;
`_print_summary` now prints "Original analysis outcome"/"Refined analysis outcome"
separately instead of one "Document outcome" line. `orchestrator/example_run_config.yaml`,
`orchestrator/runs_gemini.yaml`, `orchestrator/runs_groq.yaml` gain
`consistency_checker_refined`/`dependency_mapper_refined` entries (stage overrides +
prompt paths, pointed at the already-existing copied prompt files) -- these are active,
reusable templates, distinct from the frozen `docs/superpowers/results/**/run_config.json`
files, which are untouched. New/updated tests:
`orchestrator/test_harness.py::test_run_document_gates_second_analysis_until_every_requirement_concludes_pass_a`
(mutation-tested), `design/test_schemas.py::test_schema_version_1_2_legacy_compatibility`
(synthetic fixtures both directions plus a real historical file),
`orchestrator/test_cli.py::test_resume_pre_s3_legacy_run_rejected_before_any_adapter`,
`orchestrator/test_cli.py::test_degraded_refined_analysis_reported_and_exits_1`,
`orchestrator/test_config.py::test_active_yaml_configs_resolve_with_all_ten_stages`.
`design/DESIGN_NOTES.md`, new entry "S3 review fixes."

**Why:** an independent review of the uncommitted S3 work (still on
`task-4-phase-pipeline`) found four real gaps, each verified empirically before fixing:
(1) `run_document` had no fresh-run equivalent of `resume_document`'s pass-A-completion
gate, so one requirement's pass-A failure could let the second analysis run over a mix
of refined and original text; (2) `ALL_STAGES` grew from eight to ten stages, but every
real run recorded before S3 is schema_version 1.2 with exactly eight -- confirmed by
actually trying to load a real historical `document.json`/`run_config.json` and watching
both fail; (3) the three active YAML configs under `orchestrator/` failed to resolve for
the same reason, confirmed the same way; (4) the CLI summary could print "Document
outcome: completed" while returning the stage-error exit code, because it never
mentioned the second (refined) analysis phase at all.

**Impact:** all recorded suites green -- schemas 347 (was 336), generate_diagrams 13,
arch diagrams 88, harness 489 (was 480), CLI 62 (was 55), stages 163, stage_fns 67,
config 101 (was 95), rotation 18. The fresh-run gate fix and its accompanying
outcome-reset fix were each mutation-tested (broke the guarantee, watched the new test
go red, reverted). No historical `docs/superpowers/results/` file modified. No API call
made. Not committed -- reported for review first.

## 2026-08-16 — Pipeline phased into two passes (Task 4 of handover, S3, implements Known Limitation 7 option B)

**Changed:** `design/schemas.py` -- `DocumentStage` gains `CONSISTENCY_CHECKER_REFINED`/
`DEPENDENCY_MAPPER_REFINED`; `DocumentRunRecord` gains `refined_consistency_report`,
`refined_dependency_report`, `refined_analysis_outcome`, and a computed
`refined_cycles` field; `DocumentOutcome`'s docstring corrected (describes phase 1
only); `RunOutcome.IN_PROGRESS` no longer forbids `cap_reason`.
`orchestrator/pipeline.py` -- `run_requirement` split into `run_requirement_pass_a`
(classifier through the cap decision) and `run_requirement_pass_b` (strategy
selection, test generation); `run_requirement` kept as a compatibility wrapper for
tests exercising one requirement's stage mechanics in isolation from the document
split; `run_document_stages` gained `consistency_stage`/`dependency_stage` parameters;
`run_document`/`resume_document` rewritten to run document analysis, pass A, document
analysis again (refined), pass B, in order, each step skippable on resume;
`retry_document_stage` now rejects the two refined stages with a clear `ValueError`.
`orchestrator/stage_fns.py` -- `StageFns` gains optional `check_consistency_refined`/
`map_dependencies_refined`. `orchestrator/cli.py`'s `_build_stage_fns` wires both from
their own resolved config. Two new prompt files, byte-identical copies of their
siblings. `design/generate_arch_diagrams.py`'s `STAGE_WIRING` gains two rows;
`validate_stage_wiring`'s field-name check changed from sequence to set equality
(dataclass field-ordering rules force the two new optional fields to the end,
diverging from `ALL_STAGES`' order). `design/DIAGRAMS.md` and `design/DESIGN_NOTES.md`
updated (new entry: "S3 implemented -- the pipeline is phased, option B under Known
Limitation 7"). Tests: `orchestrator/test_harness.py::test_resume_positions` updated
first per the task's own instruction (new case constructed, confirmed it failed under
the unmodified schema, then the schema change made it pass); `test_run_document_happy_path`'s
latent under-provisioned-mock bug fixed; two new tests added and mutation-tested
(broke each guarantee on purpose, confirmed red, reverted):
`test_phased_pipeline_pass_b_sees_refined_analysis_not_original` and
`test_resume_gates_second_analysis_until_every_requirement_concludes_pass_a`.

**Why:** `docs/superpowers/plans/2026-08-15-CLAUDE-CODE-HANDOVER.md` Task 4,
implementing S3. Resolves Known Limitation 7: document-level analysis previously ran
once, on the original text, and fed strategy selection/test generation the same stale
picture even after refinement changed the text (observed live on `PURE-THEMAS-R6-P`) --
a rewrite-created cycle also had no mechanism to be seen at all. Two real design gaps
were found by running the existing suite end to end, not by inspection: (1) `resume_at`
has no notion of an already-made cap decision, so an already-cap-decided record could be
sent back into the refine loop after the human chose to stop refining; (2) the pre-S3
design let the human re-decide "generate vs. stop" on any resume after a failed pass-B
stage call, which phasing nearly dropped by assuming pass B needed no `human_fns` at
all. Both are documented and fixed in the DESIGN_NOTES.md entry.

**Impact:** all recorded suites green -- schemas 336, generate_diagrams 13, arch
diagrams 88, harness 480 (was 464 before this task), CLI 55, stages 163, stage_fns 67,
config 95, rotation 18. The two new integration tests are each confirmed to actually
discriminate (mutation-tested: broke the guarantee, watched the suite go red, reverted),
not just constructed and left unverified. Built on branch `task-4-phase-pipeline`, off
the Task 0-3 checkpoint commit.

## 2026-08-16 — Corpus extraction and evaluation-subset freeze (Task 3 of handover, S1, in progress)

**Changed:** `tools/scan_pure_corruption.py` (new) -- scans a directory of PDF/`.doc`/
HTML/HTM/RTF/XML documents for Known Limitation 5's three corruption signatures
(`X = Y = Z` comparison chains, `T_LT`-shaped underscore tokens, surviving Unicode math),
best-effort text pull per format, diagnostic only (no `RequirementSet` output). Verified
against the known 18-file XML baseline (reproduces it exactly), then run over all 79
`pure-full/` documents -- 28/79 flagged, 0 unreadable, report written to
`docs/superpowers/pure-full-corruption-scan.json`. `tools/extract_dalpiaz.py` (new) --
1,677 requirements across 22 files, output in `datasets/dalpiaz-extracted/`.
`tools/extract_promise_nfr.py` (new) -- 625 requirements across 15 projects, output in
`datasets/promise-nfr-extracted/`. `requirements.txt` gains `pdfplumber>=0.11,<1` (used
by the corruption scanner; already installed in this environment, now pinned).
`design/DESIGN_NOTES.md`, new entry "S1 in progress -- evaluation-subset freeze, corpus
extraction, and what the boundary question actually looks like against real data",
records: the format-first extraction plan was proposed and rejected (selection-bias risk
-- would shape the evaluation subset by parsing convenience, not document properties);
the primary PURE evaluation corpus is instead the 5 already-extracted annotated documents
in `datasets/pure-extracted/` (805 requirements, zero new work needed); format-specific
extraction for `pure-full/`'s 79 PDF/DOC/HTML/RTF documents is deferred pending whether
the frozen subset ever needs to expand past those 5; Riaz is investigated and deferred
(its security-annotated sentences are often bullet-list fragments, not standalone
requirements -- same unsolved problem as PURE's unannotated prose, not a trivial parse);
modal-verb prevalence measured across the 5 real annotated PURE documents before any
boundary-heuristic decision (cctns 99%, gamma-j 94%, eirene-fun-7-2 83%, keepass 44%,
peering 4%) -- real data showing a modal-verb rule would fail badly on peering-shaped
documents, which is why no boundary heuristic has been chosen yet.

**Why:** `docs/superpowers/plans/2026-08-15-CLAUDE-CODE-HANDOVER.md` Task 3, implementing
S1. The scope and sequencing above followed a mid-task course correction from explicit
user direction: freeze which documents constitute the evaluation subset before building
any format-specific extraction, so subset membership isn't silently shaped by which
formats happen to be easy to parse.

**Impact:** `datasets/dalpiaz-extracted/`, `datasets/promise-nfr-extracted/`, and
`docs/superpowers/pure-full-corruption-scan.json` are new, real, on-disk artifacts,
verified by running each tool (not just written and assumed correct). No production code
(`design/schemas.py`, `orchestrator/`) touched, so no suite re-run needed there.
`requirements.txt` gains `pdfplumber>=0.11,<1`, recorded as an approved, narrowly-scoped
exception to the handover doc's "no new dependencies" rule (diagnostic tooling only,
never imported by `design/`/`orchestrator/`) -- not a silent addition.

**Corruption finding, stated conservatively:** no additional corruption was detected by
this diagnostic across the 79-document corpus (`1998 - themas.xml`, the one document
known to be corrupted, is excluded from evaluation regardless). This is not "no other
document is corrupted" -- the scan reads these PDFs via pdfplumber, a different
extraction path from whatever PURE's own PDF-to-XML converter did to produce the known
themas damage, so a clean result here cannot rule out damage that converter introduced
and this scan's own path does not surface. The single other flagged file's
`chained_comparisons` are non-word-shaped garbage (`'H9=H9=F6'`), most likely the scan's
own crude `.doc` text pull misfiring on binary noise rather than a second real
corruption -- that same file separately shows 27+24 cleanly-surviving `<=`/`>=` from this
scan's own extraction. **Still open, not yet decided:** the requirement-boundary rule for
any `pure-full/` document, if the frozen subset ever needs one, and the `NON_ATOMIC`
frequency measurement Task 3 asks for before
Task 5 (needs a real Quality Checker call; no API key available in this environment).

## 2026-08-16 — Quality Checker stability harness (Task 2 of handover, E3 level 1)

**Changed:** new `docs/superpowers/quality_checker_stability.py` -- a standalone,
throwaway-grade script (not a new orchestrator/ module, per the handover doc) that calls
only `make_check_quality_fn`'s closure, N times (`--calls`, default 5) on one requirement
named on the command line, with no Classifier call (a placeholder `Classification` is
built from a `--system-type` flag) and no refinement loop. Reports each call's
`(category, span)` issue set, `passed` flag, distinct-set count, and a majority-agreement
figure; writes the full JSON report to `--output` if given. A `StageCallFailed`/
`StageCallFatal`/`StageCallPartial` on one call is caught and counted, not fatal to the
other N-1 calls. Includes a `--self-test` mode (11 checks, scripted `FakeAdapter`, no
network/API key) covering: identical reports -> STABLE; opposite verdicts across calls
(Known Limitation 10's actual failure mode) -> UNSTABLE with the right distinct-set count;
one transport failure tolerated without losing the other calls; an unknown requirement id
raises a clean `ValueError` rather than crashing.

**Why:** handover doc Task 2. `design/DESIGN_NOTES.md` Known Limitation 10 records the
Quality Checker returning opposite verdicts on character-identical input in consecutive
rounds -- every per-category precision/recall figure in the evaluation is a single draw
until this is quantified, and it has to exist before the frozen run, not be reconstructed
after.

**Impact:** `python docs/superpowers/quality_checker_stability.py --self-test` -- 11/11
passed, verified in this change. No production code touched (`design/schemas.py`,
`orchestrator/`), so no suite re-run needed there. The actual stability MEASUREMENT (real
API calls against a real requirement) has NOT been run in this environment -- no
`GEMINI_API_KEY`/`GROQ_API_KEY` configured here; confirmed the script fails cleanly with
exit 2 and a clear message on that path. Running it for real, and recording the resulting
agreement figure, is separate follow-up work for whoever has API access before the freeze.

## 2026-08-15 — Operator system-type label capture (Task 1 of handover, implements S2)

**Changed:** `design/schemas.py`, `RequirementRunRecord` gains
`operator_system_type: Optional[SystemType] = None`, independent of `classification`, no
validator forcing agreement. `orchestrator/cli.py` gains a third subcommand,
`label-system-type RUN_DIR LABELS.json`, which reads a JSON `{requirement_id: system_type}`
object, validates every id against the run before writing anything, and rewrites each
matching `requirements/*.json` via the existing `write_requirement_run`; no adapter,
`StageFns`, or `HumanFns` call. Tests: `design/test_schemas.py::test_operator_system_type_capture`
(4 new checks); `orchestrator/test_cli.py`'s four `test_label_system_type_*` cases (16 new
checks, including the fixture helper `_completed_happy_run`). `design/DESIGN_NOTES.md`, new
entry "S2 implemented -- operator system-type label, as a run-record field plus a third CLI
subcommand", records where the field lives and why capture is a third subcommand rather than
a `resume` flag or a third `HumanFns` callable.

**Why:** `docs/superpowers/plans/2026-08-15-CLAUDE-CODE-HANDOVER.md` Task 1, implementing S2
(`docs/superpowers/plans/2026-08-15-system-changes-before-freeze.md`). The Classifier's
accuracy has had n=0 since no human label was ever collected, and it can't be reconstructed
after a run. `resume`'s own docstring rules out a fresh-input flag there, and Known
Limitation 9's discussion already flagged a new blocking `HumanFns` point as a real cost not
adopted — this capture mechanism is offline and blocks nothing, so neither objection applies.

**Impact:** `design/test_schemas.py` 326 → 330 checks; `orchestrator/test_cli.py` 39 → 55
checks; both green. `design.generate_diagrams`/`design.test_generate_diagrams` (13 checks)
and `design.test_generate_arch_diagrams` (88 checks) re-run clean — no new module/stage, so
no structural diagram change. No pipeline behaviour change: nothing downstream reads
`operator_system_type`; it is a record only, verified by the disagreement test asserting
`classification.system_type` is unchanged. Classifier-accuracy measurement itself is not yet
computed anywhere — that's evaluation-phase work (E1), not this task.

## 2026-08-15 — System-changes-before-freeze decisions recorded (Task 0 of handover)

**Changed:** `design/DESIGN_NOTES.md`, new dated section "System changes to make before
the evaluation freeze (2026-08-15)" appended after the "Multi-key rotation for free-tier
rate limits" entry — the full content of
`docs/superpowers/plans/2026-08-15-system-changes-before-freeze.md` (S1 extraction, S2
operator system-type label, S3 pipeline phasing, S4 human-supplied `NON_ATOMIC` splits,
plus the Declined list), reheaded to this file's existing style (H2 title with date, H3
subsections). Known Limitations section itself untouched. `docs/EVALUATION_PROTOCOL.md`
left where it is, not folded in — it is the measurement protocol, kept separate on
purpose.

**Why:** `docs/superpowers/plans/2026-08-15-CLAUDE-CODE-HANDOVER.md`, Task 0. Records the
decisions Tasks 1–5 will implement so they're traceable to a dated design note rather than
only living in a plan file.

**Impact:** documentation only — no code, schema, or prompt change. No suites re-run
(nothing executable touched).

## 2026-08-15 — Future-work item: cite a standard instead of inventing a threshold

**Changed:** `design/DESIGN_NOTES.md`, new entry "Future work, adjacent to Limitation 11 —
cite a standard instead of inventing a threshold" (documentation only, right after Known
Limitation 11's block); `docs/superpowers/results/2026-08-14-refiner-answerer-pilot/RESULTS.md`
cross-references it. No prompt, schema, or orchestrator change. No pipeline runs.

**Why:** the 2026-08-14 refiner-answerer pilot measured an LLM answerer inventing acceptance
thresholds (SUS≥70, 5/10/15-minute windows) that read as normal professional criteria with no
tell. The proposal: have the Refiner name a measurable property and a citable source standard
(ISO/IEC 25010:2023 characteristics, SUS as an instrument) instead of a number, target value
left explicitly unset. Deferred deliberately — implementing it mid-evaluation would make every
prior run (behaviour suite, live-answer session, v1/v2 comparison, this pilot) non-comparable
against a run using the new rewrite behaviour, and would need a prompt v3 with its own re-runs.
Includes the verified `STANDARDS_REFERENCE` table (ISO/IEC 25010:2023, cross-checked 2026-08-15
against two independent sources), the 25010:2023/25023:2016 version-mismatch caveat, and a note
that the commonly-quoted SUS≥68 benchmark has no peer-reviewed source and must not enter the
thesis unverified. Also records a threat-to-validity the same pilot exposed: PURE's source
authors are unreachable, so no answerer on that corpus can supply a real value — the banked
2026-08-14 live-answer comparison measures "does supplying a value help," not "does a human
help."

**Impact:** documentation only — no behavioural change.

---

## 2026-08-14 — Refiner-answerer pilot: human vs. LLM answering clarifying questions

**Changed:** new `docs/superpowers/results/2026-08-14-refiner-answerer-pilot/` —
`configs/run-a-human-v2.yaml`/`run-b-llm.yaml` (copies of the pure-peering-smoke
config, only `run_id`/`output_dir` changed), `input/pilot3.json` (3-requirement subset
of `datasets/pure-extracted/pure-gamma-j.json`: PURE-GAMMA-J-0033/0034/0042, the "easy
to use/learn/upgrade" vague-adjective family), `PREDICTIONS.md` (written before
running), `RESULTS.md`, and both run directories
(`configs/runs_run-a-human-v2/`, `configs/runs_run-b-llm/`). No code, prompt, schema or
config-shape change.

**Why:** a cheap manual pilot asked by the user — does it matter whether a human or an
LLM answers the Refiner's clarifying questions, and does having the source document
open change anything. Separate from, and cheaper than, the Q1/Q2 evaluation design in
`docs/superpowers/plans/2026-08-14-evaluation-design.md`.

**Impact:** Run A (human, fixed "I don't know" refusal policy, his own choice
mid-run): all 3 requirements hit the revision cap with byte-identical no-op rewrites
every round (Known Limitation 10, reproduced live), ending `cap_generated`. 46,368
tokens. Run B (me, source XML open, narrating doc-lookup-vs-judgement per answer): all
3 resolved in exactly one round with a rewritten, testable sentence, ending
`completed`. 34,765 tokens. Answer-content check: 1 of 3 of my answers was pure
invention, 2 of 3 pulled real facts from *sibling* requirements in the same document
(not the ones asked about) with an invented number layered on top — refuting the
predicted 0/3-retrieved outright, since sibling-requirement context wasn't considered
as a retrieval channel when the prediction was written. All 3 invented numeric
thresholds (SUS≥70, 5/10/15-minute windows) read as indistinguishable from genuine
domain knowledge once embedded in the rewritten text. See `RESULTS.md` for the
verdict: worth a bigger pilot with an explicit fabrication-flag mechanism, not worth
trusting as a build-it-now decision. n=3, one document, one prompt version.

---

## 2026-08-14 — Smoke test: pure-peering (24 reqs) through the full pipeline, clean

**Changed:** new `docs/superpowers/results/2026-08-14-pure-peering-smoke/` —
`configs/pure-peering-smoke.yaml` (copy of `scn-04-conflict-numeric-v2.yaml`'s shape,
only `run_id`/`output_dir` changed), `PREDICTIONS.md` (written and committed before the
run, commit `6a16957`), `RESULTS.md`, and the run directory itself
(`configs/runs_pure-peering-smoke/pure-peering-smoke/`, `document.json` +
24 `requirements/*.json`). No code, prompt, schema or config-shape change — this is a
run, not a modification.

**Why:** confirm a freshly extracted PURE document flows through the pipeline at all
before any Q1/Q2 evaluation work touches it (the task that requested this). This is a
smoke test, not an evaluation result — n=1, no ground truth, not blinded, and not part
of `docs/superpowers/plans/2026-08-14-evaluation-design.md`'s design. Ran
`datasets/pure-extracted/pure-peering.json` (24 requirements, ~700 tokens of
consistency-checker payload) — 3x the requirement count and ~2.75x the document-level
payload of the largest document run through this pipeline before today
(`themas-fischbach2022`, 8 requirements). PAID Gemini key, `gemini-3.6-flash`,
temperature 1.0, v2 prompts as currently committed, scripted reasoned-decline answer
policy, via `paid_gemini_driver.py` unchanged (same driver, same policy, as every
comparable real run to date).

**Impact:** ran clean end to end. Exit code 0. **Zero schema-validation failures,
zero transport failures, zero id-mismatches** across 192 real API calls (24
classifier, 70 quality_checker, 46 refiner_questioner, 46 refiner_rewriter, 2
strategy_selector, 2 test_generator, 2 document-level) and 310,542 tokens — every call
succeeded on its first attempt. Outcome mix: 22/24 `cap_stopped`, 2/24 `completed`, 0
`error`. Full prediction-vs-actual comparison in `RESULTS.md`: 2 of 6 pre-registered
predictions held (duplicate texts never flagged `inconsistent`; id agreement clean
across all 24), 1 held loosely (outcome mix), 3 refuted (first-pass Quality Checker
clean rate 0/24, not the predicted 2-5/24; token/attempt cost above the predicted
range on both counts; Classifier gave `web` to 7/24, not the predicted near-total
`other`).

**Two findings that reach beyond this smoke test, both flagged in `RESULTS.md` rather
than acted on here:** (1) the Classifier's 7/24 `web` result is new evidence against
Known Limitation 9's premise ("every real run classified `other`") — that premise as
currently worded in CLAUDE.md/`DESIGN_NOTES.md` is now measurably wrong and needs
correcting, separately from this run. (2) `PURE-PEERING-0012`'s `TestPlan` has a test
case citing dependency `PURE-PEERING-0013` (`dependency_report` independently reports
`0013 -> 0012`), with the test_strategy's own rationale naming the dependency by id —
direct evidence the Test Generator uses dependency context, making n=2 (was n=1) on
the open question Known Limitations 1/6/7 attach to. Neither finding is resolved here;
both are handed off as leads, not conclusions.

**Not scaled up.** Per instructions, this smoke test stops here rather than
progressing to `cctns` (115 reqs) or `eirene_fun` (583 reqs, ~88x anything run) without
first discussing the results above.

---

## 2026-08-14 — Extractor hardening: sibling-id guard, newline stability, corpus unchanged

**Changed:** `tools/extract_pure_xml.py` -- `strip_id_prefix` takes a third argument,
`other_ids` (every `<req id>` in the document, computed once in `extract_file`); it now
refuses to strip when `req_id` is a proper prefix of another id in the same document, on
top of the existing digit/dot guard. All three `write_text` call sites (`RequirementSet`,
manifest, summary) pass `newline="\n"` explicitly. `tools/test_extract_pure_xml.py`: the
"longer number NOT stripped by shorter id" case now carries an explicit sibling-id set as
its precondition, and a new case covers the roman-numeral shape (`"5.2.2i"` must not
strip from `"5.2.2ivThe system shall..."` when `"5.2.2iv"` is a sibling id).

**Why:** a second opinion requested during review of the previous entry found the
digit/dot guard was not the whole story -- eirene_fun's ids also carry roman-numeral
suffixes (`5.2.2i`, `5.2.2ii`, `5.2.2iii`, `5.2.2iv`, `5.2.2v`, ...), and a shorter one is
a proper string-prefix of a longer sibling exactly the way `11.2.1.1` prefixes
`11.2.1.10`, except the continuation character is a letter, which the digit/dot guard
does not see. Confirmed by grep across the real corpus: 32 roman-suffixed ids, 244
sibling-prefix pairs in `eirene_fun` alone. The gap had not fired -- 583/583 stripped
correctly in the five committed documents -- so this is a guard against a shape that
exists in the id-space, not a correction of a wrong prior extraction. Separately, the
previous entry's "byte-identical across runs" claim carried an unstated platform
caveat: `Path.write_text` defaults to `os.linesep`, so this Windows box produced CRLF
against LF-committed files (masked by `.gitattributes`' `eol=lf` on checkout, so never
a real bug, but a reproducibility claim in the evaluation methodology should not need
a footnote).

**Impact:** the extracted corpus is unchanged -- same 805 requirements, same 5 counts,
`pure-eirene-fun-7-2` still 583/583 id-prefix-stripped, zero strips newly blocked by the
sibling check. A fresh extraction on this machine is now byte-identical to the committed
`datasets/pure-extracted/*.json` with no CRLF caveat (previously identical only after
stripping `\r`). 106/106 tests pass (was 104; two new cases for the sibling guard).
Mutation-tested: removing the sibling-prefix check turns exactly the new roman-numeral
case red (105/106), nothing else; restored, 106/106. Neither change is an improvement to
the corpus -- one fixes a gap that never fired, the other fixes a platform-dependent byte
representation of the same content.

---

## 2026-08-14 — PURE XML extractor: 805 requirements across five untouched documents

**Changed:** new `tools/extract_pure_xml.py` and `tools/test_extract_pure_xml.py` (104
checks, plain-script convention, seven mutations run). New `datasets/pure-extracted/` —
five `<doc_id>.json` RequirementSets, five `<doc_id>.manifest.json` provenance sidecars,
and `extraction-summary.json`. Correction note appended to
`docs/superpowers/plans/2026-08-14-evaluation-design.md` section 5. Nothing under
`design/` or `orchestrator/` touched, so no diagram regeneration is due —
`generate_arch_diagrams.py`'s `INTERNAL_PACKAGES` is `("design", "orchestrator")` and
does not scan `tools/` (checked, not assumed).

**Why:** step 2 of `docs/superpowers/plans/2026-08-14-evaluation-design.md` section 7 —
the corpus the Q1 ablation runs against. Deterministic XML parse rather than LLM
extraction, per that plan's circularity argument; PURE's own annotators decide what counts
as a requirement, so this project does not have to. Exclusions are enforced by
`SPENT_DOCUMENTS` (section 5 item 4), not remembered.

**Impact:** 805 requirements emitted and validated as `RequirementSet` — eirene_fun 583,
cctns 115, gamma j 51, keepass 32, peering 24. `themas` and `ertms` skipped by the
exclusion list, with the reason printed and recorded in the summary. Extraction is
byte-identical across runs (asserted). Four findings that change what the plan assumed:

1. **The plan's counts were regex overcounts.** `<req id=` also appears inside
   commented-out empty template blocks (5 in cctns, 9 in gamma j), which no parser sees.
   Parsed totals are 115 not 120, and 51 not 60; the untouched corpus is **805, not 819**,
   and the six-document figure is 1,004 not 1,018. Pinned as test constants so it cannot
   drift back.

2. **`<req id>` is section-local, not document-unique**, so three of the five documents
   fail `RequirementSet._ids_are_unique` on their raw ids (cctns 24 repeats, gamma j 6,
   peering 4; ertms 8). Ids are therefore synthesised ordinally
   (`PURE-CCTNS-0001…0115`, document order). The section path is *not* a usable fallback:
   cctns's duplicates all sit inside one `<p id="">`. Provenance (source file, section
   path, original `<req id>`, file position, text hash) lives in the manifest sidecar, not
   on `Requirement` — decided against adding schema surface for extraction bookkeeping.

3. **`<itemize>`/`<enum>` bullets are preserved as newline + "- "** rather than
   space-joined into the sentence, for 75 of eirene_fun's 583. A space-join manufactures
   run-on requirements, which is precisely the shape the Quality Checker over-flags as
   `non_atomic` (Known Limitation 8) — the flag would then be measuring extraction, not
   the requirement.

4. **eirene_fun repeats its own section number at the head of every text_body**, stripped
   on exact match against the `<req id>` (583/583). Two have the number fused to the first
   word ("11.2.1.10It shall…"), so the delimiter is optional and a digit/dot guard stops a
   shorter id eating a longer number's leading digit. Both halves are mutation-tested.

**Two corpus quirks recorded as counts, flagged but never filtered, because they bear on
sampling and on Known Limitation 1:** eirene_fun carries **10 `Deleted.` tombstones** —
withdrawn clauses PURE still tags as `<req>` — and **31 verbatim-repeated texts** (71
requirements involved); peering has 3 repeated texts out of 24, cctns 1 of 115. Whoever
draws the evaluation sample now has numbers rather than a discovery during hand-scoring.

**Not measured yet:** plan section 5 item 5, the extraction-corruption scan over this
output. The cheap signals were clean on all five (no mojibake, no flattened comparison
operators — themas, the known-corrupt document, is excluded anyway), but that is not the
scan. Also unaddressed by choice: 6 of keepass's 32 texts begin with their own `REQ-n:`
label, which is the document's own prose and does not match the `<req id>`, so the
stripping rule correctly leaves it.


## 2026-08-14 — Prompt v2 batch: non_atomic definition + rewriter no-op rules, re-run on 4 scenarios

**Changed:** `orchestrator/example_prompts/quality_checker.txt` (non_atomic definition
tightened to "independently testable", one positive/one negative example added) and
`orchestrator/example_prompts/refiner_rewriter.txt` (three rules against inventing text
when an answer gives no concrete value, or confirms the requirement is already
correct/measurable), committed as `2178774`. New configs
`docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-{04,07,10,12}-*-v2.yaml`
(`prompt_version: v2`, distinct `run_id`/`output_dir`, baseline `runs_scn-*` dirs from
2026-08-13 untouched). New
`docs/superpowers/results/2026-08-11-behavior-scenarios/RESULTS-V2.md`.

**Why:** implements `docs/superpowers/plans/2026-08-14-prompt-v2-batch.md` — the two
prompt-only fixes proposed for Known Limitation 8 (non_atomic over-flagging causal chains)
and Known Limitation 11 (Rewriter inventing text where none should appear), batched into
one re-run per the plan's cost/attribution argument.

**Impact:** re-ran the four scenarios containing every affected requirement (15
requirement-slots, PAID Gemini key, refusing answer policy unchanged) and compared every
requirement's outcome, quality-report categories, and rewrite text against the 2026-08-13
baseline run records. Of six pre-registered predictions: 5 held, 1 refuted
(`PURE-ERTMS-R2` still flagged `non_atomic` under the tightened definition — the model's
own explanation judges the two movement types genuinely independent, a defensible read).
All three regression guards passed: zero new `VALIDATION_FAILURE`, zero outcome changes
across all 15 slots, zero `COMPLETED`↔cap flips. Net token cost +1.3% (153,871 → 155,822
across the four scenarios). No prompt change made in response to the refutation. Full
per-prediction detail and quoted model output in RESULTS-V2.md; folded into
`design/DESIGN_NOTES.md` Known Limitations 8 and 11.

**Amended same day, after counting rewrites directly in both sets of run records.** The
outcome-level guards passed, but the entry above understated the effect. Text-changing
rewrites went **5 -> 0** (rewrites 19 -> 18, no-ops 14 -> **18**): every text change vanished,
not only the three targeted ones. Collateral: `PURE-ERTMS-R2` lost "shall **be able to**
supervise" -> "shall supervise" (a genuine improvement) and `ACTAPP-R2-AC1` lost the addition
of a missing actor ("**The system** identifies…") alongside the placeholder that was meant to
go.

Logical rather than defective — new rule 2 fires universally under a policy that never
supplies a value — but it means **refusing-policy runs can no longer distinguish "the rules
work" from "the Rewriter is disabled"**. Next measurement, named: replay the frozen live
transcript (`docs/superpowers/results/2026-08-14-live-answers/answers.json`) against v2, the
only run where answers carry content and the rules can be selective.

Also unreconciled: this entry reports +1.3% token cost, while cost computed from the same
records is essentially flat ($0.3387 -> $0.3385) with attempts down 101 -> 98. Likely raw
tokens vs. weighted cost; settle which figure RESULTS-V2.md should carry.

## 2026-08-14 — Prompt v2 replayed against the frozen live-human transcript

**Changed:** new configs
`docs/superpowers/results/2026-08-14-live-answers/configs/scn-{08-clean,09-vague,
10-atomicity,04-conflict-numeric,11a-cap-generate,11b-cap-stop}-v2-live.yaml`
(`prompt_version: v2`, `run_id`/`output_dir` suffixed `-v2-live`, prompts unchanged from
the already-committed `orchestrator/example_prompts/`). New
`docs/superpowers/results/2026-08-14-live-answers/RESULTS-V2-LIVE.md`. No prompt,
schema, fixture, or driver edited.

**Why:** answers the question the same-day refusing-policy batch amendment raised —
under a policy that supplies no content, text-changing rewrites went 5→0, which cannot
distinguish "the rules work" from "the Rewriter is disabled." Replaying the same nine
requirement-slots' frozen live-human answers (`answers.json`, unchanged) against v2 is
the one comparison where answers carry real content and the rules can be selective. See
`design/DESIGN_NOTES.md` Known Limitation 11.

**Impact:** ran via `answering_policy_driver.py` (PAID Gemini key, transcript replay,
unchanged). Text-changing rewrites: 5/9 (v1-live) → 3/9 (v2-live) — not 5/9 → 0/9. All
three substantive v1 changes survived (`THEMAS-REQ-D`, `THEMAS-REQ-E`,
`PURE-THEMAS-R6-P`); both of the two named artifacts were suppressed (`AUTOGEN-US2`'s
deferral phrase, `PURE-THEMAS-R6`'s cosmetic reformat) — the rules are measurably
selective, not silencing. All 6 pre-registered predictions held (one, `THEMAS-REQ-E`,
with a caveat). Two regression guards tripped, both the same requirement and root
cause: 1 replay miss and 1 `COMPLETED`→`CAP_STOPPED` flip on `THEMAS-REQ-E`, traced to
v2's own round-1 rewrite introducing a new `incomplete` gap the frozen transcript has
no answer for — not a defect in either edited prompt, reported rather than patched
around. Zero new `VALIDATION_FAILURE`. Cost $0.2667 (v2-live) vs $0.2833 (v1-live,
`SESSION.md`), both measured from real token counts. Full per-prediction detail, quoted
model output, and three drift-warning examples in RESULTS-V2-LIVE.md; folded into
`design/DESIGN_NOTES.md` Known Limitation 11.

## 2026-08-14 — Live answer policy run: six scenarios, nine requirements, real human

**Changed:** new `docs/superpowers/results/2026-08-14-live-answers/` — `live_bridge_driver.py`
(file-bridge `HumanFns` calling `orchestrator/human_cli.py`'s real
`answer_questions_cli`/`decide_at_cap_cli` unchanged, with injected `input_fn`/`output_fn`
instead of a terminal), byte-identical copies of the six scenarios' configs/fixtures,
`extract_answers.py` (builds `answers.json` from the run records), `answers.json` itself,
`answering_policy_driver.py` (the replay driver, with `--self-test`), and `SESSION.md`.

**Why:** executes `docs/superpowers/plans/2026-08-14-live-answer-policy.md` — the measurement
that entry's "Impact" left open: what refinement does when a real human, not
`answer_policy_driver.py`'s refusal policy, answers. See Known Limitation 10 (downgraded
2026-08-13) and Known Limitation 11.

**Impact:** measured, not estimated. 9 requirement-slots: 4 `COMPLETED`, 4 `CAP_STOPPED`, 1
`CAP_GENERATED`. Text-change rate 5/9 (55.6%; 4/9 substantive, one is unit-format-only) vs.
the refusing-policy baseline on the identical fixtures, 4/9 (44.4%) — both well above the
full 47-item suite's 19%, confirming the plan's own "favorable ground" threat to validity.
One clean case where the live-human answer fixed a cross-requirement conflict the refusing
policy structurally cannot (`PURE-THEMAS-R6-P`, 5°F → 3°F, reached `COMPLETED`) and one case
where the refusing policy's "text changed" was bracket-placeholder insertion, not content
(`AUTOGEN-US3`, `AUTOGEN-US2` — Known Limitation 11's pattern, both directions now observed).
Cost: $0.2833 measured from real `prompt_tokens`/`completion_tokens` at $1.50/1M in +
$7.50/1M out, within the plan's $0.20–0.35 estimate. `answering_policy_driver.py --self-test`
replayed all 16 turns / 27 questions from this session's own records: 0 misses, 0 drift
warnings. Full breakdown, original/final text for every requirement, and two methodology
incidents (one briefly non-verbatim answer, caught and the affected run restarted; one
echoed-message glitch, caught and not recorded) are in `SESSION.md`.

---

## 2026-08-14 — Annotated-corpus check; glossary fix deferred on evidence; S9 result recorded

**Changed:** `docs/superpowers/plans/2026-08-14-evaluation-design.md` (two sections added on the
annotated XML subset and the glossary decision); `design/DESIGN_NOTES.md` Known Limitation 5
(S9 result).

**Why:** before building an extractor, checked what the committed corpus actually contains; and
before adding schema fields for the limitation-5 fix, checked whether the measurement gating it
had already been taken.

**Impact:** two findings, one of which cancelled a planned schema change.

- **Extraction is far cheaper than assumed.** 6 of 18 files in
  `datasets/requirements-xml/XMLZIPFile/` carry explicit `<req id>` elements — 1,018
  requirements, 819 of them in five documents not yet spent. "What counts as a requirement" is
  therefore PURE's own annotation decision, citable, with no inference and no loss rate. The
  79-document PDF corpus is not needed to start.
- **The glossary fix is deferred, not adopted.** 171 `<glossary_item>` term/meaning pairs exist,
  which would have made the proposed pre-pass cheap — but S9 shows `LO = T_LT` was **never
  flagged** across three rounds, so there is no wrong judgement for definitions to correct, and
  THEMAS's glossary does not contain `LO`/`LT` anyway. A free measurement is named instead: run
  ~20 requirements from `eirene_fun`/`gamma j` without a glossary and count whether
  glossary-defined domain terms get falsely flagged. The schema change (a `GlossaryTerm` model
  plus one optional `RequirementSet.glossary` field) is scoped in the plan but not made.

No code touched; `design/schemas.py` unchanged, so no diagram regeneration required.

---

## 2026-08-14 — Evaluation design and document-reanalysis plan written (design only)

**Changed:** two new plans —
`docs/superpowers/plans/2026-08-14-evaluation-design.md` and
`docs/superpowers/plans/2026-08-14-document-reanalysis-plan.md`.

**Why:** the behaviour work has established what the pipeline *does*; the evaluation phase has
to establish whether it *helps*, and needed a design before any corpus extraction begins.
Known Limitation 7's fix needed a decision recorded either way.

**Impact:** none — nothing run, no code touched. Decisions recorded:

- **The paper's primary claim is structural, not qualitative.** The pipeline produces test
  artifacts that are structurally valid, traceable and technique-grounded by construction;
  that is measurable automatically against the existing 326 schema checks, with no rubric and
  no rater. Content quality becomes a secondary, blinded, hand-scored layer on a smaller
  sample.
- **Baseline fairness constraint:** the one-shot arm must be given the *same output schema*,
  or the structural comparison is a trivial win and dismissible. Refinement trajectory and
  document-level context are to be reported as structural differences, not scored as wins.
- **Ablation, not cross-system comparison**, with the reasoning to be stated in the
  methodology rather than left as an unexplained omission.
- **Q2 (per-stage model sensitivity)** is a separate configuration-only study, scored against
  the existing scenario suite's ground truth, hypothesis pre-registered. ~$10–15.
- **Known Limitation 7: recommended NOT to build now.** The plan leads with that
  recommendation and, if overridden, specifies a branch, five separately-revertible commits,
  a config flag defaulting to off, and resume changes last with a mutation check.

---

## 2026-08-14 — PURE extraction-corruption scan: confined to one document, not corpus-wide

**Changed:** `design/DESIGN_NOTES.md`, Known Limitation 5 — measurement appended under the
live-session evidence.

**Why:** the live session found `LO = T_LT` to be a flattened `LO <= T <= LT`, which raised an
unmeasured risk that PDF-to-XML extraction had damaged comparisons across the PURE corpus.
Several fixtures are PURE-derived, so this had to be settled before more runs depend on them.

**Impact:** the risk is narrowed, and the alarm I raised is partly withdrawn. Scanned all 18
files of `datasets/requirements-xml/XMLZIPFile/` for comparison chains, underscore-joined
tokens, and surviving Unicode math:

- 6 of 18 documents contain any mathematical `=`; most SRS text is prose.
- `1998 - themas.xml` is the **only** file with the flattened-comparison signature.
- `2006 - eirene sys 15.xml` retains 7 Unicode math symbols, proving extraction can preserve
  them — so this is per-document (source PDF encoding), not systemic.
- `2007-ertms.xml` is unaffected, so the ERTMS fixtures are sound.

Explains why it looked systemic: THEMAS is the document this project has used for the schema
spot-check, all three 2026-08-10 runs, and several fixtures. Scope limit recorded: the 79-file
full corpus is unparsed, so nothing is known about it — the scan should be repeated when an
extractor for it is built.

---

## 2026-08-14 — Live-answer session results folded into the design notes

**Changed:** `design/DESIGN_NOTES.md` — Known Limitations 5, 7, 8, 10 and 11 extended with
live-session evidence (11 generalised from one defect into a three-variant pattern);
`docs/superpowers/results/2026-08-14-live-answers/SESSION.md` (provenance of
`OBSERVATIONS-DURING-SESSION.md` corrected — it was written by the assisting session at the
operator's request, not by an unknown background process).

**Why:** the live-answering run (same file, previous entry) settled several questions the
notes had recorded as open or reasoned-only.

**Impact:** documentation only; numbers verified directly against the run records rather than
taken from the run's own write-up (4 `COMPLETED` / 4 `CAP_STOPPED` / 1 `CAP_GENERATED`, 88
calls, $0.2833 — all confirmed). What changed in the record:

- **7** — now has a live demonstration instead of an argument: `PURE-THEMAS-R6-P` was fixed by
  the human's answer (5°F -> 3°F) and re-flagged `inconsistent` anyway from the pre-refinement
  consistency report. The loop exited only because the human set `user_confirms_resolved: True`,
  so the design currently depends on a person noticing its analysis has gone stale.
- **11** — generalised to one pattern with three variants: placeholder where a value exists
  (`LUITEL-R1`), deferral where none exists (`AUTOGEN-US2`), cosmetic edit where the human asked
  for none (`PURE-THEMAS-R6`). Each alters appearance without altering testability.
- **10** — the threat-to-validity note can now cite a run where the human *did* answer.
  Substantive change rate 4/9 for both policies; the difference is in substance, not rate. Also
  records that a refusing answer produced a **false** `COMPLETED` on `LUITEL-R7`, so earlier
  runs' success counts are inflated in the pipeline's favour.
- **8** — the one genuine `NON_ATOMIC` case now has a human on record confirming the split is
  correct and the pipeline unable to perform it; plus the observation that `NON_ATOMIC` flags
  structure rather than whether splitting is worth doing.
- **5** — three vague references all resolved from the source document, and `LO = T_LT` shown to
  be a corrupted `LO <= T <= LT` from PDF-to-XML extraction. Raises an unmeasured corpus risk:
  every inequality in that document is damaged.

**Named next measurement:** count the three limitation-11 variants and the PURE extraction
damage in the next suite; all are n=1 today.

---

## 2026-08-14 — Live answer policy planned (not yet run)

**Changed:** new `docs/superpowers/plans/2026-08-14-live-answer-policy.md`.

**Why:** Known Limitation 10's downgrade left the project unable to measure refinement
effectiveness at all — every run so far used an answer policy that declines to answer. See
`design/DESIGN_NOTES.md`, Known Limitation 10, "Suite result 2026-08-13".

**Impact:** none yet — plan only, nothing run. The plan fixes six scenarios / nine
requirements (~$0.20–0.35), the live-answering rules, the `answers.json` freeze format keyed
on `requirement_id::issue_category`, a replay driver whose fallback is explicit and counted,
and the metrics for the refusing-vs-answering comparison. The measurement that would make
this entry non-empty is that comparison; it has not happened.

Decision recorded here rather than in DESIGN_NOTES: the transcript is captured **from the run
records**, not from a parallel log, because `ClarifyingQuestion` already stores
`issue_id`/`issue_category`/`question_text` and a second copy could disagree with the record.

---

## 2026-08-14 — Known Limitations reconciled with the 2026-08-13 suite results

**Changed:** `design/DESIGN_NOTES.md` — "Suite result 2026-08-13" blocks appended to Known
Limitations 1, 3, 6, 7, 8, 9 and 10, plus new Known Limitation 11; `CLAUDE.md`
("Known-open, deliberately": limitation 10 downgraded, 11 added).

**Why:** the suite refuted or altered four previously recorded positions. Sources:
`docs/superpowers/results/2026-08-11-behavior-scenarios/` run records, tallied directly from
the JSON rather than from the run's own write-ups.

**Impact:** documentation only. Positions that changed, each now carrying the evidence:

- **7** — the qualification added earlier the same day is **withdrawn**. The Test Generator
  does use dependency context (`TC-13-PURE-ERTMS-R7-2` cites both ends of the `R8 -> R7`
  link), so the dependency half is the damaging one as originally written.
- **8** — 5 `non_atomic` flags, ~2 wrong, and `LUITEL-R7` correctly caught. The "all 14 flags
  are false positives" generalisation from THEMAS-only data was too strong; the definition fix
  is now polish rather than a rescue.
- **9** — the empirical half is dead: `mobile` 2 and `ai_system` 1 alongside `other` 31, and
  `INFEASIBLE_FOR_TYPE` fired zero times. The structural half (three members, one technique
  pool) stands and is now the whole limitation.
- **10** — downgraded from defect to threat to validity. 38/47 no-op rewrites all trace to the
  refusing answer policy; no no-op followed an informative answer, and all 9 text-changing
  rewrites inserted placeholders rather than invented facts. Fixes (b) and (c) dropped as
  unnecessary; fix (a) retained.
- **1, 3, 6** — annotated with measurements rather than reversed: spanning cases are now known
  to occur (1), `PERFORMANCE` selected zero times including where expected (3), and the strict
  `TestPlan` rule still never fired though the risk case is closer (6).
- **11 (new)** — the Rewriter tagged `LUITEL-R1` as needing a measurable value beside its own
  `5s` threshold. n=1; counting it in the next suite is the named next measurement.

---

## 2026-08-14 — Known Limitations reworked against real run data; two new limitations added

**Changed:** `design/DESIGN_NOTES.md` (~700 lines added across Known Limitations 1–3, 5–8,
plus new entries 9 and 10 and a "split is not a rewrite" analysis under 8); `CLAUDE.md`
("Known-open, deliberately" list, DESIGN_NOTES line count 2,500 → 3,100);
`docs/superpowers/plans/2026-08-11-behavior-scenarios-RUN-PROMPT.md` (four suite-wide
tallies, five pre-registered predictions, an S1 dependency check, an S12 note).

**Why:** a review of the three real runs of 2026-08-10 found that several limitations were
justified by reasoning that the project's own data contradicted — see `DESIGN_NOTES.md`,
Known Limitations 3 (embedded *is* in the corpus), 6 (the strict rule has never fired), 9
(new: three `SystemType` members share one technique pool) and 10 (new: no-op rewrites
accepted).

**Impact:** documentation only — no behavioural change. Verified that nothing in `design/`
or `orchestrator/` was touched: full suites green afterwards (schemas 326, arch diagrams
88, diagrams 13, harness 464, CLI 39, stages 163, rotation 18, config 95 = 1,206 checks),
all 14 scenario configs load, all 12 fixtures validate.

**Date correction, same day:** the notes added on this date were first labelled
`2026-08-11` inside `DESIGN_NOTES.md` (anchored on the behavior-scenario plan's filename
rather than the calendar). Fixed: 15 date markers in `design/DESIGN_NOTES.md`, 4 in
`CLAUDE.md`, and 1 in the run prompt now read `2026-08-14`. References to the
`plans/2026-08-11-*` and `results/2026-08-11-*` paths were deliberately left alone — those
are real filenames, not dates of authorship.

---

## 2026-08-13 — Behavior scenario suite executed against the paid Gemini API (first full pass)

**Changed:** no source changes. Added
`docs/superpowers/results/2026-08-11-behavior-scenarios/` — `run_driver.py`, `RESULTS.md`,
`ANALYSIS.md`, and 14 run directories under `configs/runs_scn-*/`.

**Why:** the suite exists to measure behaviour that Known Limitations 1, 3, 5, 6, 7, 8, 9
and 10 all depend on and that no test can supply. Plan:
`docs/superpowers/plans/2026-08-11-behavior-scenarios.md`.

**Impact:** 14/14 scenarios ran; 263 API calls; 345,235 prompt + 43,241 completion tokens;
**$0.84** (pre-run estimate was ~260 calls and $0.55–$1.05, so the estimate held). S13
correctly reached `DocumentOutcome.DEGRADED`. Results that change previously recorded
positions:

- **Test generation does use dependency context** — `TC-13-PURE-ERTMS-R7-2` cites both
  `PURE-ERTMS-R7` and `PURE-ERTMS-R8`, the first multi-requirement test case in the
  project. Refutes prediction 3 and unblocks Known Limitations 1, 6 and 7.
- **`non_atomic` is more accurate than the 2026-08-10 data suggested** — 5 flags, of which
  `LUITEL-R7` (three independent reports) is a correct catch; 2 remain conjunction-splits.
  Partly refutes prediction 1.
- **Classification is no longer uniformly `other`** — 31 `other`, 2 `mobile`, 1
  `ai_system`. Refutes the empirical half of Known Limitation 9.
- **`infeasible_for_type` never fired** across 34 requirements, so the 2026-08-10 false
  positive did not recur.
- **`PERFORMANCE` was still never selected**, including on `LUITEL-R1` where S12's ground
  truth expected it. Known Limitation 3's practical half stands.
- **38 of 47 rewrites (81%) changed nothing**, and every one traces to the scripted answer
  policy declining to supply information (3 human overrides, 35 refusals). No no-op
  followed an answer that carried real information, so this is **not** a Rewriter defect —
  it downgrades Known Limitation 10 to a threat to validity: refinement effectiveness
  cannot be measured with an answer policy that refuses to answer.
- **New defect found:** the Rewriter inserted `[TBD: measurable value]` into `LUITEL-R1`,
  which already stated `5s` — degrading a measurable requirement. Not invention (all 9
  text-changing rewrites inserted placeholders, never invented facts), but a real quality
  regression. Not yet written up in `DESIGN_NOTES.md`.
