# Evaluation design — what gets measured, against what, and why

Status: design only. Nothing here has been run. Written 2026-08-14, after the behaviour
scenario suite, the live-answer session and the prompt v2 comparison established what the
pipeline *does*; this document is about establishing whether it *helps*.

---

## 1. The two questions, kept separate

They need different experiments and should not be run as one.

**Q1 — Does the pipeline structure help?** Compared with a naive use of the same model on the
same requirements.

**Q2 — Where in the pipeline does model strength actually matter?** Which of the eight stages
need a strong model and which run acceptably on a weak/free one.

Q1 is the thesis claim. Q2 is a separate, cheaper study that also produces a practical result
for anyone reproducing the work on no budget — fitting, given this project was built under
free-tier constraints throughout.

---

## 2. Q1 — ablation, not cross-system comparison

**Three arms, same requirements, same model, same temperature, same day:**

1. **One-shot** — requirement in, test cases out. Single prompt. No refinement, no document
   context, no technique selection. The floor.
2. **Pipeline minus refinement** — classification, technique selection and test generation, but
   the refine loop skipped entirely. Isolates refinement's contribution.
3. **Full pipeline** — as built.

The 1-vs-3 difference is the headline claim; 2-vs-3 isolates refinement specifically.

**Why not compare against another published system.** It would need their code running on these
inputs; the moment models, prompts or versions differ the comparison stops meaning anything,
and reviewers know it. An honest ablation is more defensible and costs a fraction of the
effort. State this reasoning in the methodology rather than leaving the omission unexplained.

**A fourth arm is already banked.** The refusing-vs-answering answer-policy comparison
(`docs/superpowers/results/2026-08-14-live-answers/`) is an ablation of the *human input*, with
run records and a frozen transcript. It cost nothing extra and answers "does the human in the
loop matter?" — reuse it rather than re-running it.

### Metrics for Q1

**Automatic, no labels required:**

- test cases per requirement; technique diversity per plan
- proportion of requirements that produce a `TestPlan` at all
- outcome mix (`COMPLETED` / cap / `ERROR`)
- tokens and cost per requirement
- schema-validation-failure rate per stage (denominator: SUCCESS + VALIDATION_FAILURE)
- stability: n=3 on a subset, reporting variance rather than a single run

**Hand-scored, and this is where the real result lives:** a stratified sample of ~30–50 test
cases across arms, **with the arm label hidden while scoring**, against a short fixed rubric —
is the case actually executable, does it match the requirement it claims to cover, is it a
duplicate of another case in the same suite. Three criteria, applied consistently, is standard
practice in requirements-engineering evaluation and beats a longer rubric applied unevenly.

Blinding matters more than rubric sophistication: the author of the pipeline scoring its output
is the obvious objection, and blinding is the cheap answer to it.

### The primary claim is structural, not qualitative (decided 2026-08-14)

"Better test cases" is a quality claim, and quality claims here rest on hand-scoring by a rater
who is also the pipeline's author — the weakest evidence this project can produce. There is a
stronger claim available, unique to this design and measurable automatically:

> The pipeline produces test artifacts that are **structurally valid, traceable and
> technique-grounded by construction**. A single model call does not.

Every element of that is already enforced and tested: 326 schema checks, `requirement_id`
agreement across stages, `TestPlan._cases_cover_this_requirement`, `ELIGIBLE_TECHNIQUES` gating
techniques by `SystemType` with ISTQB citations behind each pool, `_require_unique` on every
identified list, and the full `RefinementRound` trajectory recorded per requirement.

**So Q1 has two layers, and they carry different evidential weight:**

**Layer 1 — structural validity (primary, automatic, objective).** Parse each arm's output into
the schema and count failures:

- test cases citing `requirement_ids` that do not exist in the document
- plans that do not cover their own requirement
- techniques outside the eligible pool for the classified system type
- duplicate ids within a plan
- outputs that fail to parse into the schema at all
- `system_type` / `requirement_id` copies that disagree across stages

Every one of these is a check already written and mutation-tested. No rubric, no rater, no
judgement.

**Layer 2 — content quality (secondary, hand-scored, blinded).** The rubric described above, on
a smaller sample, reported with its limitations rather than as the headline.

### Fairness of the one-shot baseline — do not skip this

A reviewer will attack an unfair baseline before attacking anything else. The one-shot arm must
therefore be given **the same output schema** and the same requirement, and asked for the same
artifact — not free prose. Comparing structured pipeline output against unstructured prose would
make Layer 1 a trivial and meaningless win.

What the comparison then isolates is the contribution proper: staged decomposition, per-stage
validation with retries, technique gating, and cross-stage id agreement — versus asking one
model for all of it in a single call. That is a fair test, and it is the one worth winning.

Two dimensions must be **reported as structural differences rather than scored as wins**,
because the baseline cannot have them by construction: refinement trajectory, and any
document-level context (conflicts, dependencies). Claiming credit for those in a scored
comparison would be circular.

---

## 3. Q2 — per-stage model sensitivity

**Design: start weak, upgrade one stage at a time.** Run everything on the cheap model as the
floor, then eight runs each upgrading exactly one stage to the strong model. The interesting
result is which upgrades recover quality and which change nothing.

Upgrading is more informative than downgrading from all-strong, because "these two stages carry
the pipeline and the other six do not care" is the finding worth having.

**No code changes needed.** `orchestrator/config.py` already sets provider/model per stage; this
is a configuration study.

**The measuring instrument already exists.** The behaviour scenario suite has written ground
truth — planted conflicts (S3/S4/S5), a cycle (S6), a real dependency (S1), `LO = T_LT` (S9),
the genuine non-atomic case (S10), routing (S12). Run the suite under each configuration and
score against the same fixed ground truth. No new labelling, and the hard/soft split is already
defined. One full pass measured at $0.84, so the whole study is plausibly $10–15.

**Hypothesis, recorded before running** (a refuted hypothesis is a result):

- **Model-sensitive:** Quality Checker, Test Generator, Consistency Checker, Dependency Mapper.
- **Model-insensitive:** Classifier (its output is nearly always `other`, and three of its four
  values share one technique pool), Strategy Selector (the schema already constrains its
  choices via `ELIGIBLE_TECHNIQUES`).
- If the Classifier proves insensitive, that is a second independent argument for Known
  Limitation 9.

**Free capability signal:** schema-validation-failure rate per stage. Weaker models break
structured output more often, and `AttemptResult` already records it — no judgement involved.

---

## 4. Model policy for Q1

- **Hold the model constant across all three arms.** If arms differ in both structure and model,
  the ablation measures nothing.
- **Primary: `gemini/gemini-3.6-flash`**, the model behind every existing run — keeps new
  results comparable with the scenario suite, the live session and the v1/v2 prompt comparison.
  Paid tier, so no quota wall mid-evaluation. Groq is rejected as primary on this project's own
  evidence: 125 attempts for 8 requirements in the 2026-08-10 run, mostly transport failures.
- **Generalisation check, not a second full study:** ~10–15 requirements, full pipeline only, on
  a second model. Enough to report "the effect holds on a second model" or "unmeasured beyond
  one model" — both honest.
- **Record the model string *and* the run dates.** Hosted models change under a stable name;
  "`gemini-3.6-flash`, August 2026" is the reproducible claim. `run_config.json` and
  `prompt_hash` already capture this per run.
- **Temperature stays at 1.0.** Every prior run used it; changing it now breaks comparability.
  Handle nondeterminism by measuring it (n=3) rather than suppressing it — and note that
  variance is already a finding here, since the Quality Checker has been observed giving
  opposite verdicts on identical text.

---

## 5. Corpus decisions still open

These block extraction and should be settled first:

1. **What counts as a requirement** in an untagged SRS — sentence level, "shall" statements
   only, or numbered SRS items. Defines the corpus and needs justifying in the methodology.
2. **Deterministic or LLM extraction.** LLM extraction means an LLM pipeline evaluated on
   LLM-produced input — a circularity a reviewer will raise. Deterministic rules are defensible
   and lossy; measure the loss on a hand-checked sample and report it.
3. **Sample size and selection**, fixed in advance. Three arms over ~50 requirements is ~150
   pipeline runs and roughly $2–3 at measured rates — the hand-scoring is the expensive part,
   not the API. Deciding size after seeing results is selection bias.
4. **Exclusions enforced, not remembered:** `pure-themas-1998-full` and `pure-ertms-2007` are
   marked spent in `datasets/EVALUATION_DATASETS.md` and must be excluded by a list the
   extractor honours.
5. **Re-run the extraction-corruption scan** on whatever the extractor emits. The 2026-08-14
   scan covered the committed XML subset only; the 79-document corpus is unmeasured.

---

## 6. Threats to validity to declare

Most are already established by earlier work in this repository and should be carried forward
rather than rediscovered:

- **Single rater, who is also the author of the pipeline and the selector of the fixtures.**
  Unavoidable solo; mitigated by blinding the arm during scoring and by publishing the rubric.
- **The scripted answer policy refuses to answer**, so refinement effectiveness measured under
  it describes the policy as much as the pipeline (Known Limitation 10). The live-answer run is
  the mitigation and covers 9 requirement-slots only.
- **A refusing answer produced a false `COMPLETED`** on `LUITEL-R7`, so earlier success counts
  are inflated in the pipeline's favour.
- **Document-level analysis never re-runs after refinement** (Known Limitation 7), demonstrated
  live on `PURE-THEMAS-R6-P`.
- **One source document is corrupted**: `1998 - themas.xml` has flattened comparison operators;
  1 of 18 in the committed subset, unmeasured in the full corpus.
- **Model drift** over the evaluation window; mitigated by recording dates and `prompt_hash`.
- **Favourable-ground selection** in the behaviour subset — those scenarios were chosen where
  refinement was expected to help.

---

## 7. Suggested order

1. Settle section 5's corpus decisions (blocks everything).
2. Build the extractor; re-run the corruption scan on its output.
3. Q1 ablation, three arms, automatic metrics.
4. Hand-scoring pass, blinded.
5. Q2 model-sensitivity study using the existing scenario suite — independent of 2–4 and can be
   run at any point, including while extraction is still being built.
