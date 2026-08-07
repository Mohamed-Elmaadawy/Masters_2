# Candidate Evaluation Datasets — reserved for the post-implementation evaluation phase

**Do not pull from these to shape the schema, prompts, or agent design.** They're kept
untouched on purpose: if the pipeline's design gets tuned against examples from the
same dataset it's later evaluated on, the evaluation stops measuring real performance
and starts measuring how well the design memorized its own test data. Schema-design
spot-checks so far have used incidental example requirements quoted inside unrelated
papers (see `requirements_dataset.json`/`.xlsx`) — never one of the named corpora below.

Found while surveying the papers in the literature folder (2026-08-04). All are real,
citable, and either public or obtainable.

## 1. PURE (PUblic REquirements dataset)
Ferrari, A., Spagnolo, G. O., Gnesi, S. (2017). *PURE: A dataset of public requirements
documents.* RE'17, IEEE, pp. 502-505. https://doi.org/10.5281/zenodo.7118517 (19-document
XML subset) / http://nlreqdataset.isti.cnr.it (full 79-document list + PDFs).

~79 real, complete SRS documents, publicly available. Best fit for evaluating the full
pipeline end-to-end: whole documents map naturally onto `RequirementSet`, and it's
human-authored industrial/open-source specs, not illustrative or LLM-generated text.
Cited by four papers already in the literature folder (Fischbach/THEMAS, Luitel,
Alhoshan, Zhao), so results would be comparable to prior published work.

**Spot-check log (2026-08-04):** two documents were deliberately pulled out of the
79 for a one-time schema stress-test against real full SRS documents (see
`requirements_dataset.json`/`.xlsx`, `pure-themas-1998-full` and `pure-ertms-2007`,
16 requirements total) — **`1998 - themas.xml`** and **`2007-ertms.xml`**. These two
are considered spent for design purposes: **exclude them from whatever subset of
PURE is used in the real evaluation phase**, to keep that measurement honest. The
other ~77 documents remain untouched.

Spot-check finding worth carrying into the evaluation phase: reading full documents
(rather than the curated excerpts used elsewhere in this project, e.g. Fischbach et
al.'s isolated REQ D/E/F) showed that terms which look like dangling references or
undefined abbreviations in isolation (`"this file"`, `"DMI"`) often resolve cleanly
once the surrounding document context is included — real SRS documents tend to define
their own jargon nearby or in a glossary. This is evidence in favor of `VAGUE_PRONOUN`
and the Known Limitation #5 abbreviation gap being less severe at the full-document
level than the isolated-excerpt audits suggested; worth re-checking false-positive
rates on whichever PURE subset is used for the real evaluation.

## 2. PROMISE NFR dataset
Cleland-Huang, J., Mazrouee, S., Liguo, H., Port, D. (2007). *NFR.*

Individual labeled requirement sentences (functional vs. non-functional, with NFR
subclasses: security, usability, performance, etc.) from multiple real projects. Best
fit for evaluating the Classifier/Quality Checker at the single-requirement level
rather than whole-document flow. Cited by Alhoshan (2023) and Hey et al. (2020),
both in the literature folder.

## 3. Dalpiaz user story dataset
Dalpiaz, F. (2018). *Requirements data sets (user stories).* Mendeley Data.
https://data.mendeley.com/datasets/7zbk8zsd8y/1

~22 real projects' worth of "As a ___, I want ___" style requirements. Best fit for
checking the pipeline handles informal/agile-style input as well as formal "shall"
statements. Cited in Zhao et al. (2021).

## 4. SecReq
Knauss, E., Houmb, S., Schneider, K., Islam, S., Jürjens, J. (2011). *Supporting
requirements engineers in recognising security issues.* REFSQ'11, Springer, pp. 4-18.

Security-focused requirement collection. Niche — only relevant if a security-specific
evaluation angle is wanted. Cited in Alhoshan (2023).

## 5. Riaz's dataset
Riaz, M., King, J., Slankas, J., Williams, L. *Hidden in plain sight: Automatically
identifying security requirements from natural language artifacts.*

Another security-focused requirement collection, cited alongside SecReq in Alhoshan
(2023). Same niche use case.

## 6. Traceability dataset collection
Zogaan, W., Sharma, P., Mirahkorli, M., Arnaoudova, V. (2017). *Datasets from fifteen
years of automated requirements traceability research.*

Labeled for requirement-to-requirement traceability/dependency links. The one
candidate specifically useful for evaluating `DependencyReport`/`DependencyLink`
detection against ground truth, rather than requirement quality or test generation.
Cited in Zhao et al. (2021).

## Planned experiment: does whole-document consistency checking scale?

**Status: not run. Design only — no results, no numbers.** Recorded here during schema
design (2026-08-05) so the question isn't lost by the evaluation phase.

**The question.** The Consistency Checker takes the *whole document* in one call, by
design — conflicts can't be found per-requirement. Every document used so far is tiny
(largest: 8 requirements, ~274 tokens; see `requirements_dataset.json`), so nothing has
tested that assumption. Real PURE SRS documents are orders of magnitude larger.

**Why it needs measuring rather than assuming.** The obvious worry — a document too big
for the context window — is *not* the real risk. With a 1M-token context (Gemini 1.5/2.0)
a full SRS fits comfortably, so the call will not error and will not truncate. It will
return `conflicts: []` and look healthy. Finding contradictions across hundreds of
requirements requires reasoning over pairs and groups, and that degrades well before the
context window fills. The consequence for this pipeline specifically: a rate-limit
failure is visible (`DocumentOutcome.DEGRADED`, see `design/DESIGN_NOTES.md`), but a
long-context reasoning failure produces a clean, well-formed, empty report that no
schema validation can catch.

**Method.** Take one PURE document. Plant a known contradiction between two (and
separately, among three) requirements. Run the Consistency Checker over requirement
subsets of increasing size — 10, 25, 50, 100, 200 — with the planted conflict always
present. Record whether it is found. Repeat across a few documents and both planted
conflict types, since a single document's result won't generalise.

**Output.** A recall-vs-document-size curve. A drop-off point is a documented scale
limit of LLM-based whole-document consistency checking, and belongs in the results as a
finding, not filtered out as a nuisance. A flat curve across the tested range is equally
worth reporting — it would justify the whole-document design against the obvious
objection to it. Either way this costs a handful of API calls, and it is the only lens
that tests the whole-document assumption against real data rather than reasoning about
it (`design/SCHEMA_AUDIT_CHECKLIST.md`, lens 8).

**Dataset hygiene.** Use PURE documents from the ~77 reserved for evaluation, not
`pure-themas-1998-full` or `pure-ertms-2007` — those two were already pulled for the
schema spot-check and are burnt for measurement purposes.

**Only if the curve shows a real drop-off:** the fix is *not* chunking the document into
windows. Conflicts spanning window boundaries would be missed, and per the schema design
a conflict can involve 3+ requirements — exactly the ones most likely to span. That
trades a silent miss for a different silent miss. The defensible approach is candidate
pre-filtering: a cheap non-LLM pass (shared entities, keyword overlap, or embedding
similarity) proposes small groups of plausibly-related requirements, and the LLM judges
only those. Its recall is measurable independently, so the pipeline's blind spot can be
stated rather than guessed at. Not built now — it would be solving a problem not yet
demonstrated to exist. If a second strategy is ever added, reports from each must be
distinguishable in the records (a "how was this produced" marker on
`DocumentRunRecord`), or results from the two will pool indistinguishably.

## When to use these

Once the pipeline (agents, orchestrator) is implemented and ready for real evaluation,
not before. Suggested order: PURE for whole-pipeline end-to-end evaluation first
(it's the most-cited and most comparable to prior work); PROMISE NFR if
per-requirement classification/quality-check accuracy needs isolating; Zogaan et al.
if `DependencyReport` accuracy specifically needs ground truth; Dalpiaz and the
security datasets (SecReq, Riaz) only if the thesis scope grows to cover
agile-style input or security-specific requirements respectively.
