# Run prompt — paste into Claude Code at the repo root

Two prompts. Run **Prompt A** first (builds and validates everything, spends $0), read
what it produces, then run **Prompt B** (spends API quota). Splitting them is
deliberate: a fixture bug found after 200 paid calls is a bug found too late.

---

## Prompt A — build the fixtures, spend nothing

```
Read docs/superpowers/plans/2026-08-11-behavior-scenarios.md in full before doing
anything. It specifies 13 behavior scenarios (S1-S13), their exact requirement text,
their ground truth, and the hard/soft expectation split. Build the fixtures and run
configs it describes. Do NOT make any API call in this task.

Create everything under a new directory: docs/superpowers/results/2026-08-11-behavior-scenarios/

For each scenario S1-S13:
  1. fixtures/scn-NN-slug.json  -- a RequirementSet, the exact shape
     orchestrator/cli.py's `run` subcommand takes as its INPUT argument. Requirement
     text verbatim from the spec doc. Source ids kept for verbatim text; planted text
     gets a -P suffix in its id.
  2. fixtures/scn-NN-slug.GROUND-TRUTH.md -- source of each requirement, what was
     planted and why, and the expected result: the hard assertions (deterministic) and
     the soft expectations (what the model should find), kept clearly apart.
  3. configs/scn-NN-slug.yaml -- a RunConfig. Copy orchestrator/runs_gemini.yaml and
     change ONLY run_id and output_dir. Both must be unique per scenario.

Hard constraints, all of which have a reason in the repo:

- Do not modify anything under orchestrator/, design/, or
  orchestrator/example_prompts/. The prompts especially: their fingerprints are the
  run's provenance record (ORCHESTRATOR_CONTRACT.md item 12), and editing one
  invalidates comparison with the 2026-08-10 runs.
- Requirement text is verbatim from the spec doc. If a requirement in the doc looks
  wrong to you, say so and stop -- do not silently "improve" it. The whole suite rests
  on its inputs being exactly what the ground-truth files claim.
- Every rate_limits entry needs its model key present with an explicit value or an
  explicit null. orchestrator/config.py treats a missing key as a config error on
  purpose, so a model can never be silently unthrottled.
- S13 is config-only: it reuses the S1 fixture unchanged and overrides just
  consistency_checker's model to a nonexistent name, so the adapter fails fatally.
  Its rate_limits must still carry that bogus model's key.
- S11 needs two configs (11a, 11b) with distinct run_ids: one where the human chooses
  to generate at the cap, one where they stop.

Then validate offline, without spending anything:

- Every fixture parses as design.schemas.RequirementSet.
- Every config loads through orchestrator/config.py's resolver, and every referenced
  prompt file exists and fingerprints cleanly.
- run_id and output_dir are unique across all 14 configs.
- Requirement ids are unique within each fixture, and every -P id is text the
  ground-truth file names as planted.

Finally, write RUNBOOK.md in that directory: the exact command per scenario, in the
spec doc's suggested order, with the expected cost per scenario from the doc's cost
section. Then stop and report. Do not run anything against an API.
```

---

## Prompt B — run it, paid key only

Run this **after** reviewing Prompt A's output.

```
Read docs/superpowers/plans/2026-08-11-behavior-scenarios.md and the RUNBOOK.md and
ground-truth files under docs/superpowers/results/2026-08-11-behavior-scenarios/.
Execute the scenarios against the real Gemini API.

Provider setup -- do not improvise this:

- Use the PAID key, GEMINI_API_KEY_PAID, via the exact pattern in
  docs/superpowers/results/2026-08-10-gemini-paid-tier-run/paid_gemini_driver.py: it
  constructs a GeminiAdapter directly and raises rather than falling back to
  GEMINI_API_KEY. The free tier has a measured 20-request/day absolute cap and this
  suite is 172-300 requests.
- Reuse the human answer policy from
  docs/superpowers/results/2026-08-10-first-real-run/answer_policy_driver.py by
  importing it, not by copying or rewriting it. One policy per IssueCategory, written
  once, already used by both 2026-08-10 runs -- a second, different policy would make
  these results incomparable with those.
- S11 is the one exception: it needs the cap DECISION to differ between run 11a
  (generate) and 11b (stop). Change only the decide_at_cap branch. Leave every
  per-IssueCategory answer identical to the shared policy, and say so in the results.
- Drive it through orchestrator.cli._run's adapter_factories / human_fns_factory
  parameters -- the seam orchestrator/test_cli.py and the existing driver both already
  use. Do not add a new seam to orchestrator/cli.py.

Order: follow the spec doc's suggested order exactly. It runs the controls (S2, S8)
first on purpose.

STOP and report before continuing if either control misbehaves -- S2 reporting
conflicts or dependencies, or S8 failing its quality check with a category other than
VAGUE_PRONOUN. If the controls are wrong, no later scenario is interpretable and there
is no point paying for them.

For each scenario, record in RESULTS.md:

- HARD: each assertion from the ground-truth file, pass or fail. A hard failure is an
  orchestrator bug -- stop and report it rather than running the next scenario.
- SOFT: hit / partial / miss, with the model's actual output quoted. A soft miss is a
  FINDING, not a failure. Do not adjust a fixture to make a soft expectation pass.
- Tokens (input and output separately, from the attempts log) and cost at $1.50/1M in,
  $7.50/1M out.
- Anything surprising that is not in either list.

Also tally across the whole suite, per
docs/superpowers/plans/2026-08-08-first-real-run-checklist.md -- follow its method,
don't restate it: wrong-requirement-id rate per stage, schema-validation-failure rate
(denominator: SUCCESS + VALIDATION_FAILURE attempts only), transport-failure rate
reported separately, and total tokens and cost.

Four further suite-wide tallies, added 2026-08-14 from a review of the three real runs
of 2026-08-10. These apply to EVERY scenario, not to one, and each answers an open
Known Limitation. Report each as a count with the requirement ids behind it:

- **No-op rewrites** — every round where `rewrite.refined_text == rewrite.original_text`.
  Separately, flag any requirement that reached `RunOutcome.COMPLETED` by that route.
  (Known Limitation 10; observed on THEMAS-REQ-A in the 2026-08-10 gemini run.)
- **Verdict flips on unchanged text** — any consecutive pair of rounds whose
  `text_checked` is identical but whose `quality_report.passed` differs, or whose issue
  set differs by anything other than that round's `suppressed_issue_ids`. This is the
  Quality Checker contradicting itself on identical input; record it as a measurement,
  not a bug to fix mid-suite.
- **Every `infeasible_for_type` issue**, quoted, with the `system_type` it was raised
  against. A flag raised against `other` is the false-positive shape found on
  THEMAS-REQ-C (Known Limitation 9).
- **Every `non_atomic` issue**, quoted, each marked *conjunction-split* (the span is a
  causal or sequential chain joined by "and"/"or", e.g. "identify X and output Y") or
  *genuine* (independently testable behaviours, e.g. three separate reports). All 14
  flags in the 2026-08-10 runs were conjunction-splits (Known Limitation 8).

### Predictions, recorded before the run

These are stated in advance so the suite confirms or refutes them rather than being read
in hindsight. A refuted prediction is a result, not a mistake -- record it plainly.

1. `non_atomic` will over-flag: most or all flags will be conjunction-splits of causal
   chains rather than genuinely independent behaviours.
2. `infeasible_for_type`, if it fires at all, may fire against a requirement classified
   `other`, reasoning from the vagueness of the label rather than from an excluded
   capability.
3. Dependency context may show no visible effect on generated test cases (S1) -- across
   every real run to date the Test Generator has received a dependency link exactly once.
4. No-op rewrites will occur, most likely where a human answer overrides an issue or
   declares it unfixable.
5. The Quality Checker will contradict itself on at least one unchanged text.

S12 additionally carries two firsts, and should not be skimmed: `LUITEL-R1` is the first
requirement expected to select `PERFORMANCE` (Known Limitation 3), and `ACTAPP-R2-AC1` is
the first expected to classify as anything other than `other` (Known Limitation 9 -- every
classification in every real run so far has been `other`).

Then write ANALYSIS.md: what the pipeline detected, what it missed, and what each miss
means. Specifically call out:

- S5: one three-way conflict, or two pairwise ones? Two pairwise is a real finding
  about whether whole-document checking is actually whole-document.
- S9: was "LO = T_LT" flagged? If yes, Known Limitation 5 is less severe than
  DESIGN_NOTES.md documents, and that note needs updating with this run cited.
- S10: read the refined text for LUITEL-R7 by hand. The schema cannot tell one
  requirement from three joined by newlines; nothing else in this project is looking.
- S12: did ACTAPP-R2-AC1 classify as AI_SYSTEM or MOBILE? Both are defensible;
  record which, and do not treat MOBILE as a failure.
- S1: does the dependency link change the test cases for PURE-ERTMS-R8 at all? Quote
  R8's cases and answer three things separately: (a) does any case list both R8 and R7
  in `requirement_ids`, (b) does any precondition or step mention the self-test or R7
  without citing it, (c) would these cases look different if `relevant_dependencies`
  had been empty. This is the measurement Known Limitations 1, 6 and 7 are all waiting
  on -- across every real run to date the Test Generator received a dependency link
  exactly once (n=1), so nothing is currently known about whether it uses one. Record
  the answer even if it is "no visible effect"; that is the useful result, not a
  failure.

Rules: report measured numbers only, never estimates. Mark anything unverified as
unverified. If a scenario produces nothing usable, say so plainly rather than
narrating around it. Do not modify orchestrator/, design/, or the prompts at any
point -- if a real bug surfaces, report it and stop; fixing it mid-suite would mean
half the results came from different code.
```

---

## Before you paste Prompt B

- `GEMINI_API_KEY_PAID` is set (it is in `.env`; confirm the shell actually has it).
- Prompt A's validation passed and you have read at least two ground-truth files
  against the spec doc yourself. The suite is only as good as its ground truth, and
  that is the one part no test can check.
- You accept ~$0.55–$1.05 for a single pass. If you want the n=3 repetition the spec
  doc argues for, say so in Prompt B — it is 3× the cost and 3× the wall-clock, and it
  is the difference between "the checker missed it" and "the checker missed it once".
