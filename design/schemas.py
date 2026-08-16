"""
Pydantic I/O schemas for the requirement-refinement / test-case-generation pipeline.

Pipeline:
    0. Input                    -> RequirementSet
    1. Consistency Checker      (RequirementSet -> ConsistencyReport)
    1b. Dependency Mapper       (RequirementSet -> DependencyReport)
    2. Per requirement:
         a. Classifier          (Requirement, RequirementSet -> Classification)
         b. Quality Checker     (Requirement, Classification, ConsistencyReport.conflicts_for(id),
                                  DependencyReport.dependencies_for(id) -> QualityReport)
         c. Refiner (only if QualityReport.passed is False)
                (Requirement, QualityReport -> RefinerTurn)    -- questions to human
                (RefinerAnswer[] -> RefinedRequirement)         -- human's answers back in
                loops back to Quality Checker
    3. Test Design Strategy Selector (Requirement, Classification,
                                       DependencyReport.dependencies_for(id) -> TestStrategy)
    4. Test Case Generator           (Requirement, TestStrategy,
                                       DependencyReport.dependencies_for(id) -> TestPlan)

Note on stages 3 and 4: they take a plain `Requirement`, NOT a `RefinedRequirement`.
A requirement that passes the Quality Checker on the first try correctly skips the
Refiner entirely, so no `RefinedRequirement` is ever produced for it -- if stages 3/4
demanded that type, the clean path would have nothing to hand forward. Both paths
therefore converge on a `Requirement`:

    if report.passed:
        current = req                          # clean -- use as-is
        refined = None
    else:
        refined = refine_loop(req, report)     # RefinedRequirement = audit record
        current = Requirement(id=req.id,       # same id, refined text
                              text=refined.refined_text,
                              source_doc_id=req.source_doc_id)

    strategy = select_strategy(current, ...)   # neither stage needs to know
    plan     = generate_tests(current, ...)    # which branch ran

`RefinedRequirement` is the record of *what changed and why* (original text, refined
text, the answers that drove the rewrite) -- it is not the pipeline's transport type.

Second human interaction point: if the Refiner hits the orchestrator's revision cap
with issues still outstanding, the human is asked whether to generate tests from the
best-effort text anyway or to stop. The answer is recorded as
RunOutcome.CAP_GENERATED / CAP_STOPPED so the decision lives in the data rather than
only in whoever was at the keyboard.

See DESIGN_NOTES.md for the reasoning behind each design choice below.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Annotated, NamedTuple, Optional

from pydantic import BaseModel, Field, computed_field, model_validator

# Every identifier and every piece of text an LLM fills in. The 2026-08-05 non-empty
# sweep covered explanation-style fields but never ids, titles, answers or list items,
# so `Requirement(id="")` and `TestCase(steps=[""])` both validated. An empty id is
# worse than an empty explanation: it makes every lookup and the whole issue-identity
# mechanism meaningless while looking like a populated record. Applied as a type alias
# rather than ~30 separate Field(min_length=1) calls, which is also what lets it reach
# inside list items (`list[NonEmptyStr]`). See DESIGN_NOTES.md.
NonEmptyStr = Annotated[str, Field(min_length=1)]


# ---------------------------------------------------------------------------
# Shared guard
# ---------------------------------------------------------------------------

def _require_unique(values: list, what: str, where: str) -> None:
    """Reject duplicate keys in a list that is semantically a set or a mapping.

    Used in nine places. Review passes kept finding the same shape one instance at a
    time -- a list whose entries are identified by something (an id, a stage, a pair of
    endpoints) with nothing stopping that identifier from repeating. A duplicate key is
    never merely redundant: it makes "the thing with id X" ambiguous, which silently
    breaks lookups, suppression by id, and any count taken over the list.
    See DESIGN_NOTES.md.
    """
    seen: set = set()
    dupes: list = []
    for v in values:
        if v in seen and v not in dupes:
            dupes.append(v)
        seen.add(v)
    if dupes:
        raise ValueError(f"{where} has duplicate {what}: {sorted(map(str, dupes))}")


def fields_carrying_requirement_id(record: BaseModel) -> list[str]:
    """Names of a record's directly-nested models that denormalise `requirement_id`.

    Discovered at runtime rather than listed, so a model added later is swept
    automatically instead of relying on someone remembering to extend a list. Reviews
    kept finding these one field at a time (`classification`, then `test_strategy`,
    then `test_plan`), which is the signal that enumerating them by hand does not hold.
    See DESIGN_NOTES.md.
    """
    return [
        name for name in type(record).model_fields
        if isinstance(getattr(record, name), BaseModel)
        and hasattr(getattr(record, name), "requirement_id")
    ]


# ---------------------------------------------------------------------------
# 0. Core input
# ---------------------------------------------------------------------------

class Requirement(BaseModel):
    id: NonEmptyStr = Field(..., description="Stable identifier, e.g. 'REQ-3'")
    text: NonEmptyStr = Field(..., description="The requirement statement as written/refined")
    source_doc_id: Optional[NonEmptyStr] = None


class RequirementSet(BaseModel):
    doc_id: Optional[NonEmptyStr] = None
    requirements: list[Requirement] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _ids_are_unique(self) -> "RequirementSet":
        # Every downstream lookup is by id (conflicts_for, dependencies_for, the
        # document record's by-id join). A repeat makes all of them silently ambiguous.
        _require_unique([r.id for r in self.requirements], "requirement id", "requirements")
        return self

    @model_validator(mode="after")
    def _requirements_belong_to_this_document(self) -> "RequirementSet":
        """A requirement may not name a source document other than this set's.

        Only checked when both ids are present. `source_doc_id=None` inside an
        attributed set is deliberately allowed: it means "this requirement's provenance
        wasn't recorded", which is a legitimate state and different from claiming the
        wrong provenance. Requiring it would also force every constructed Requirement to
        carry one, including throwaway ones.

        A set aggregating several source documents was considered and rejected: all ten
        documents in datasets/requirements_dataset.json are homogeneous, the pipeline
        treats one RequirementSet as one document throughout, and PURE documents are
        individual SRSs. If cross-document consistency checking ever comes into scope,
        drop this check then -- deliberately, rather than having it silently never have
        existed. See DESIGN_NOTES.md.
        """
        if self.doc_id is None:
            return self
        wrong = sorted({r.source_doc_id for r in self.requirements
                        if r.source_doc_id is not None and r.source_doc_id != self.doc_id})
        if wrong:
            raise ValueError(
                f"requirements name source document(s) {wrong}, but this set is "
                f"{self.doc_id!r}"
            )
        return self


# ---------------------------------------------------------------------------
# 1. Consistency Checker
# ---------------------------------------------------------------------------

class ConsistencyConflict(BaseModel):
    # 2+ requirements, not always pairwise -- see DESIGN_NOTES.md
    requirement_ids: list[NonEmptyStr] = Field(..., min_length=2)
    explanation: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _ids_are_distinct(self) -> "ConsistencyConflict":
        # min_length=2 alone is satisfied by ["R1", "R1"] -- a requirement conflicting
        # with itself, which is not a conflict.
        _require_unique(self.requirement_ids, "requirement id", "requirement_ids")
        return self


class ConsistencyReport(BaseModel):
    doc_id: Optional[NonEmptyStr] = None
    conflicts: list[ConsistencyConflict] = Field(default_factory=list)

    def conflicts_for(self, requirement_id: str) -> list[ConsistencyConflict]:
        """Conflicts a given requirement is part of (see DESIGN_NOTES.md)."""
        return [c for c in self.conflicts if requirement_id in c.requirement_ids]


# ---------------------------------------------------------------------------
# 1b. Dependency Mapper (distinct from Consistency Checker -- see DESIGN_NOTES.md)
# ---------------------------------------------------------------------------

class DependencyLink(BaseModel):
    # from_requirement_id's testability depends on to_requirement_id (behavioral,
    # not a mere cross-reference -- see DESIGN_NOTES.md)
    from_requirement_id: NonEmptyStr
    to_requirement_id: NonEmptyStr
    explanation: NonEmptyStr

    @model_validator(mode="after")
    def _not_self_referential(self) -> "DependencyLink":
        # A self-loop is a trivial 1-node cycle: find_cycles() would report it and the
        # Refiner would ask the human to break a dependency on itself.
        if self.from_requirement_id == self.to_requirement_id:
            raise ValueError(
                f"{self.from_requirement_id!r} cannot depend on itself")
        return self


class DependencyReport(BaseModel):
    doc_id: Optional[NonEmptyStr] = None
    dependencies: list[DependencyLink] = Field(default_factory=list)

    @model_validator(mode="after")
    def _links_are_unique(self) -> "DependencyReport":
        # One dependency relation per ordered pair. A repeat inflates the graph and
        # would make find_cycles() report the same cycle more than once.
        _require_unique([(d.from_requirement_id, d.to_requirement_id) for d in self.dependencies],
                        "dependency link", "dependencies")
        return self

    def dependencies_for(self, requirement_id: str) -> list[DependencyLink]:
        """Links a given requirement is part of, as either side (see DESIGN_NOTES.md)."""
        return [
            d
            for d in self.dependencies
            if requirement_id in (d.from_requirement_id, d.to_requirement_id)
        ]

    def find_cycles(self) -> list[list[str]]:
        """DFS cycle detection (see DESIGN_NOTES.md for why cycles route to the Refiner
        instead of being auto-resolved).

        Iterative rather than recursive: a recursive version raised RecursionError on a
        1200-link dependency chain, and real PURE documents are far larger than anything
        tested so far. Output is identical to the recursive version -- verified against
        it on the fixed cases and on randomised graphs before it was replaced.
        """
        graph: dict[str, list[str]] = {}
        for dep in self.dependencies:
            graph.setdefault(dep.from_requirement_id, []).append(dep.to_requirement_id)

        cycles: list[list[str]] = []
        visited: set[str] = set()

        for start in graph:
            if start in visited:
                continue
            visited.add(start)
            path: list[str] = [start]
            on_path: set[str] = {start}
            # Each frame is (node, iterator over its remaining neighbours).
            stack: list[tuple[str, object]] = [(start, iter(graph.get(start, [])))]
            while stack:
                _, neighbours = stack[-1]
                descended = False
                for neighbor in neighbours:  # type: ignore[union-attr]
                    if neighbor in on_path:
                        cycles.append(path[path.index(neighbor):])
                        continue
                    if neighbor in visited:
                        continue
                    visited.add(neighbor)
                    path.append(neighbor)
                    on_path.add(neighbor)
                    stack.append((neighbor, iter(graph.get(neighbor, []))))
                    descended = True
                    break
                if not descended:
                    stack.pop()
                    on_path.discard(path.pop())

        return cycles


# ---------------------------------------------------------------------------
# 2a. Classifier
# ---------------------------------------------------------------------------
# Input: (target: Requirement, context: RequirementSet) -- whole document given as
# context, output stays one Classification per requirement. See DESIGN_NOTES.md.

class SystemType(str, Enum):
    WEB = "web"
    MOBILE = "mobile"
    AI_SYSTEM = "ai_system"
    OTHER = "other"


class Classification(BaseModel):
    requirement_id: NonEmptyStr
    system_type: SystemType
    rationale: NonEmptyStr


# ---------------------------------------------------------------------------
# 2b. Quality Checker
# ---------------------------------------------------------------------------

class IssueCategory(str, Enum):
    AMBIGUOUS_TERM = "ambiguous_term"            # vague adjective/adverb, no measurable threshold
    NON_ATOMIC = "non_atomic"                    # bundles more than one testable behavior
    INCOMPLETE = "incomplete"                    # missing actor / trigger / condition
    NON_VERIFIABLE = "non_verifiable"            # unambiguous but no pass/fail criterion
    INFEASIBLE_FOR_TYPE = "infeasible_for_type"  # doesn't fit the classified system type
    INCONSISTENT = "inconsistent"                # conflicts with another requirement
    CIRCULAR_DEPENDENCY = "circular_dependency"  # part of a dependency cycle
    VAGUE_PRONOUN = "vague_pronoun"              # "these limits" / "this module" -- unresolved referent


# Categories that assert a relationship to other requirements, so cannot stand alone.
_RELATIONAL_CATEGORIES = frozenset({
    IssueCategory.INCONSISTENT,
    IssueCategory.CIRCULAR_DEPENDENCY,
})


class Issue(BaseModel):
    # Stable id (e.g. "REQ-3-ISSUE-1") so a specific issue instance -- not just its
    # category -- can be tracked across revisions. Needed because a requirement can
    # have two issues of the same category (e.g. two different vague terms), which a
    # bare category label can't tell apart. See DESIGN_NOTES.md.
    id: NonEmptyStr
    category: IssueCategory
    span: Optional[NonEmptyStr] = Field(None, description="Flagged phrase/quote, if applicable")
    explanation: NonEmptyStr
    related_requirement_ids: list[NonEmptyStr] = Field(
        default_factory=list,
        description="Set for INCONSISTENT/CIRCULAR_DEPENDENCY: other requirement id(s) involved",
    )

    @model_validator(mode="after")
    def _relationship_categories_name_the_other_side(self) -> "Issue":
        """INCONSISTENT and CIRCULAR_DEPENDENCY are claims *about other requirements*.
        Either without a counterpart is a relationship with nothing on the other end --
        and unactionable for the Refiner, which has nothing concrete to ask about."""
        if self.category in _RELATIONAL_CATEGORIES and not self.related_requirement_ids:
            raise ValueError(
                f"category={self.category.value} requires at least one entry in "
                "related_requirement_ids"
            )
        _require_unique(self.related_requirement_ids, "requirement id",
                        "related_requirement_ids")
        return self


class QualityReport(BaseModel):
    requirement_id: NonEmptyStr
    passed: bool
    issues: list[Issue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _passed_matches_issues(self) -> "QualityReport":
        # passed is derived, not independent: true iff there are no issues.
        # See DESIGN_NOTES.md for why, and for the "issue vs. warning" idea this
        # would need to be revisited for, if that's ever added.
        # Issue ids are identity: ClarifyingQuestion.issue_id points at one, and the
        # user_confirms_resolved override suppresses by one. A repeat makes both
        # ambiguous -- and can suppress a different issue than the human meant.
        _require_unique([i.id for i in self.issues], "issue id", "issues")
        if self.passed == (len(self.issues) == 0):
            return self
        raise ValueError(
            "QualityReport.passed must be True iff issues is empty "
            f"(got passed={self.passed}, {len(self.issues)} issue(s))"
        )


# ---------------------------------------------------------------------------
# 2c. Refiner (human-in-the-loop, resumable request/response -- see DESIGN_NOTES.md)
# ---------------------------------------------------------------------------

class ClarifyingQuestion(BaseModel):
    id: NonEmptyStr
    issue_id: NonEmptyStr  # which specific Issue.id this question addresses -- see DESIGN_NOTES.md
    issue_category: IssueCategory  # denormalized for convenience; bounded taxonomy, LLM phrases the text
    question_text: NonEmptyStr


class RefinerTurn(BaseModel):
    requirement_id: NonEmptyStr
    revision_number: int = 1
    questions: list[ClarifyingQuestion] = Field(..., min_length=1)  # a turn always carries at least one question


class RefinerAnswer(BaseModel):
    question_id: NonEmptyStr
    answer_text: NonEmptyStr
    user_confirms_resolved: bool = Field(
        False,
        description="Human override: 'I'm confident this is correct and complete, "
        "don't re-flag it even if the checker still has doubts.' See DESIGN_NOTES.md "
        "for why this exists.",
    )


class RefinedRequirement(BaseModel):
    # Audit record of a rewrite, not the pipeline's transport type -- see the note on
    # stages 3/4 in the module docstring above, and DESIGN_NOTES.md. Only ever exists
    # for requirements that actually failed a quality check and went through the human
    # Q&A loop; that is why `answers_used` can safely require min_length=1.
    requirement_id: NonEmptyStr
    original_text: NonEmptyStr
    refined_text: NonEmptyStr
    revision_number: int
    answers_used: list[RefinerAnswer] = Field(..., min_length=1)


class RefinementRound(BaseModel):
    """One pass of the refinement loop, self-contained.

    Replaces three parallel lists (`quality_reports`, `refiner_turns`,
    `refiner_answers`) that were linked only by position and by chasing ids. Grouping
    them removes the possibility of disagreement rather than adding validators to
    detect it -- there is nothing left for a round to disagree with. See DESIGN_NOTES.md.

    A round is: check `text_checked` -> if it fails, ask the human, take answers,
    rewrite. The rewrite becomes the NEXT round's `text_checked`, which is what makes
    the whole trajectory reconstructible and its continuity checkable.

    The final round has no `turn`/`answers`/`rewrite`: either its check passed, or the
    revision cap stopped the loop.
    """
    revision_number: int = Field(..., ge=1)
    text_checked: NonEmptyStr = Field(
        ..., description="The requirement text this round's check ran on")
    quality_report: QualityReport
    turn: Optional[RefinerTurn] = None
    answers: list[RefinerAnswer] = Field(default_factory=list)
    rewrite: Optional[RefinedRequirement] = None
    # Issue ids the Quality Checker was told not to re-flag this round, because the
    # human ticked user_confirms_resolved on them in an earlier round. Recorded rather
    # than left implicit in the orchestrator: without it, an issue that vanishes between
    # rounds is indistinguishable from one that was actually fixed. Cross-round rules
    # (the id must have been raised and confirmed earlier, and suppressions accumulate)
    # live in RequirementRunRecord -- a round cannot see its predecessors.
    suppressed_issue_ids: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _round_is_coherent(self) -> "RefinementRound":
        rid, n = self.quality_report.requirement_id, self.revision_number

        _require_unique(self.suppressed_issue_ids, "issue id",
                        f"revision {n} suppressed_issue_ids")
        if n == 1 and self.suppressed_issue_ids:
            raise ValueError(
                "revision 1 cannot suppress anything -- no earlier round raised it")
        still_flagged = ({i.id for i in self.quality_report.issues}
                         & set(self.suppressed_issue_ids))
        if still_flagged:
            raise ValueError(
                f"revision {n} suppresses {sorted(still_flagged)} but its quality_report "
                "raises them anyway"
            )

        if self.quality_report.passed:
            for name in ("turn", "answers", "rewrite"):
                if getattr(self, name):
                    raise ValueError(
                        f"revision {n} passed its quality check, so there is nothing to "
                        f"refine, but {name} is set"
                    )
            return self

        if self.answers and self.turn is None:
            raise ValueError(f"revision {n} has answers but no turn -- nothing was asked")

        if self.turn is not None:
            if self.turn.requirement_id != rid:
                raise ValueError(f"revision {n}: turn is for {self.turn.requirement_id!r}, "
                                 f"quality_report is for {rid!r}")
            if self.turn.revision_number != n:
                raise ValueError(f"revision {n}: turn says revision "
                                 f"{self.turn.revision_number}")
            asked: set[str] = set()
            for q in self.turn.questions:
                if q.id in asked:
                    raise ValueError(f"revision {n}: question id {q.id!r} asked twice")
                asked.add(q.id)
            # The mirror of the check above, on the answers side: one answer per
            # question per round. Two answers to the same question leave it ambiguous
            # which one the human actually gave -- and which one the rewrite used.
            _require_unique([a.question_id for a in self.answers],
                            "answer", f"revision {n} answers")
            for a in self.answers:
                if a.question_id not in asked:
                    raise ValueError(
                        f"revision {n}: answer references question {a.question_id!r}, "
                        "which was not asked in this round"
                    )
            # Every question asked must be traceable to an issue raised this round, and
            # must restate that issue's category correctly. `issue_category` is a
            # denormalised copy for convenience; without this it can silently disagree
            # with the taxonomy the Quality Checker actually assigned, which would
            # corrupt any per-category metric taken over the questions.
            by_id = {i.id: i for i in self.quality_report.issues}
            for q in self.turn.questions:
                issue = by_id.get(q.issue_id)
                if issue is None:
                    raise ValueError(
                        f"revision {n}: question {q.id!r} addresses issue "
                        f"{q.issue_id!r}, which this round's quality_report did not raise"
                    )
                if q.issue_category is not issue.category:
                    raise ValueError(
                        f"revision {n}: question {q.id!r} says its issue is "
                        f"{q.issue_category.value!r}, but issue {issue.id!r} is "
                        f"{issue.category.value!r}"
                    )

        # A requirement cannot be inconsistent with, or circularly dependent on, itself.
        for issue in self.quality_report.issues:
            if rid in issue.related_requirement_ids:
                raise ValueError(
                    f"revision {n}: issue {issue.id!r} lists {rid!r} as a related "
                    "requirement, but that is the requirement it is about"
                )

        if self.rewrite is not None:
            # No explicit "rewrite requires answers" check: `answers_used` has
            # min_length=1, so an empty `answers` list always trips the subset check
            # below instead. A separate check could never fire on its own -- untestable
            # in isolation, therefore untested. (Same reasoning that dropped the
            # redundant refiner_answers signal in the previous design.)
            if self.rewrite.requirement_id != rid:
                raise ValueError(f"revision {n}: rewrite is for a different requirement")
            if self.rewrite.revision_number != n:
                raise ValueError(f"revision {n}: rewrite says revision "
                                 f"{self.rewrite.revision_number}")
            if self.rewrite.original_text != self.text_checked:
                raise ValueError(
                    f"revision {n}: rewrite.original_text is not the text that was "
                    "checked this round"
                )
            for a in self.rewrite.answers_used:
                if a not in self.answers:
                    raise ValueError(
                        f"revision {n}: rewrite used an answer to {a.question_id!r} that "
                        "is not among this round's answers"
                    )
        return self


# ---------------------------------------------------------------------------
# 3. Test Design Strategy Selector
# ---------------------------------------------------------------------------
# Still one call, one output per requirement (not bulk) -- just given that one
# requirement's own dependency links as extra context, so e.g. a real precondition
# from another requirement can inform TestCase.preconditions below. See DESIGN_NOTES.md.

class TestTechnique(str, Enum):
    # ISTQB Foundation Level black-box techniques
    EQUIVALENCE_PARTITIONING = "equivalence_partitioning"
    BOUNDARY_VALUE_ANALYSIS = "boundary_value_analysis"
    DECISION_TABLE = "decision_table"        # rule combinations, e.g. business logic
    STATE_BASED = "state_based"              # state transition testing
    USE_CASE = "use_case"                    # multi-step user journey, main/alt/exception flows
    EXPLORATORY = "exploratory"              # experience-based
    # ISTQB CT-AI (AI-specific) techniques -- see DESIGN_NOTES.md
    METAMORPHIC = "metamorphic"
    ADVERSARIAL = "adversarial"
    STATISTICAL_THRESHOLD = "statistical_threshold"  # our label for CT-AI functional performance metrics
    # ISTQB CT-PT (Performance Testing) -- cross-cutting, not gated to one SystemType
    PERFORMANCE = "performance"


# Layer 1 of technique selection: a hard constraint from the classified system type.
# Documented in DESIGN_NOTES.md ("How techniques get selected") since the schema was
# first written, but enforced nowhere until now -- so nothing stopped the Strategy
# Selector picking ADVERSARIAL for a thermostat, which is the exact failure the layer
# exists to prevent.
#
# EXPLORATORY and PERFORMANCE are in every pool on purpose: experience-based testing
# applies to anything, and a timing/throughput constraint can appear in a requirement
# regardless of system type. The white-box and multi-run CT-AI techniques are absent
# from every pool -- see the "Deliberately left out" note in DESIGN_NOTES.md.
_NON_AI_TECHNIQUES = frozenset({
    TestTechnique.EQUIVALENCE_PARTITIONING,
    TestTechnique.BOUNDARY_VALUE_ANALYSIS,
    TestTechnique.DECISION_TABLE,
    TestTechnique.STATE_BASED,
    TestTechnique.USE_CASE,
    TestTechnique.EXPLORATORY,
    TestTechnique.PERFORMANCE,
})
_AI_TECHNIQUES = frozenset({
    TestTechnique.METAMORPHIC,
    TestTechnique.ADVERSARIAL,
    TestTechnique.STATISTICAL_THRESHOLD,
    TestTechnique.EXPLORATORY,
    TestTechnique.PERFORMANCE,
})
ELIGIBLE_TECHNIQUES: dict[SystemType, frozenset[TestTechnique]] = {
    SystemType.AI_SYSTEM: _AI_TECHNIQUES,
    SystemType.WEB: _NON_AI_TECHNIQUES,
    SystemType.MOBILE: _NON_AI_TECHNIQUES,
    SystemType.OTHER: _NON_AI_TECHNIQUES,
}


class TestStrategy(BaseModel):
    requirement_id: NonEmptyStr
    system_type: SystemType
    techniques: list[TestTechnique] = Field(..., min_length=1)  # never zero techniques
    rationale: NonEmptyStr

    @model_validator(mode="after")
    def _techniques_are_distinct(self) -> "TestStrategy":
        _require_unique([t.value for t in self.techniques], "technique", "techniques")
        return self

    @model_validator(mode="after")
    def _techniques_are_eligible(self) -> "TestStrategy":
        """Techniques must be drawn from the pool their system type allows.

        Checked here rather than at record level because a TestStrategy carries its own
        `system_type` and is a standalone stage output -- it should not be constructible
        in an invalid state even outside a run record. The separate record-level check
        that `classification.system_type` matches this one closes the chain: the pool is
        the Classifier's decision, not the Selector's preference.
        """
        ineligible = set(self.techniques) - ELIGIBLE_TECHNIQUES[self.system_type]
        if ineligible:
            raise ValueError(
                f"{sorted(t.value for t in ineligible)} not eligible for system_type="
                f"{self.system_type.value} (allowed: "
                f"{sorted(t.value for t in ELIGIBLE_TECHNIQUES[self.system_type])})"
            )
        return self


# ---------------------------------------------------------------------------
# 4. Test Case Generator
# ---------------------------------------------------------------------------

class TestCase(BaseModel):
    id: NonEmptyStr
    # A single test case can legitimately verify more than one requirement at once
    # (e.g. an end-to-end "add to cart, then checkout" test covers both the cart and
    # checkout requirements) -- same reasoning as ConsistencyConflict.requirement_ids.
    # See DESIGN_NOTES.md.
    requirement_ids: list[NonEmptyStr] = Field(..., min_length=1)
    technique_used: TestTechnique
    title: NonEmptyStr
    preconditions: Optional[NonEmptyStr] = None
    steps: list[NonEmptyStr] = Field(..., min_length=1)
    expected_result: NonEmptyStr

    @model_validator(mode="after")
    def _covered_requirements_are_distinct(self) -> "TestCase":
        # This list is the requirement-to-test traceability matrix; a repeat would
        # double-count coverage of one requirement.
        _require_unique(self.requirement_ids, "requirement id", "requirement_ids")
        return self


class TestPlan(BaseModel):
    # The requirement this plan was generated for (Test Case Generator still runs
    # once per requirement -- see DESIGN_NOTES.md). Its test_cases can each name
    # additional requirement_ids beyond this one.
    requirement_id: NonEmptyStr
    test_cases: list[TestCase] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _case_ids_are_unique(self) -> "TestPlan":
        _require_unique([c.id for c in self.test_cases], "test case id", "test_cases")
        return self

    @model_validator(mode="after")
    def _cases_cover_this_requirement(self) -> "TestPlan":
        """Every case in this plan must cover the requirement the plan is for.

        A `TestPlan` is "the plan produced while processing this one requirement", and
        its cases may name *additional* requirement ids beyond it (an end-to-end test
        legitimately spans several). Without this, a plan for REQ-1 could consist
        entirely of cases that test something else -- nominally REQ-1's tests, with
        nothing in it testing REQ-1.

        This is the strict reading ("every case"), matching the design note that cases
        name additional ids *beyond this one*. Risk worth knowing: if the generator ever
        legitimately emits a case for REQ-1's plan that covers only a dependency, this
        rejects it. That failure is loud and easy to loosen to "at least one case";
        the opposite failure -- a plan silently not testing its own requirement -- is
        not. See DESIGN_NOTES.md.
        """
        missing = [c.id for c in self.test_cases if self.requirement_id not in c.requirement_ids]
        if missing:
            raise ValueError(
                f"test case(s) {missing} are in the plan for {self.requirement_id!r} but "
                "do not cover it"
            )
        return self


# ---------------------------------------------------------------------------
# Run outcome and stage failures -- see DESIGN_NOTES.md
# ---------------------------------------------------------------------------

class PipelineStage(str, Enum):
    """Per-requirement stages only -- deliberately kept separate from DocumentStage so
    a RequirementRunRecord cannot express an error naming a document-level stage. Same
    reasoning as using an enum rather than a free string in the first place: make the
    nonsensical value impossible to write down, rather than merely wrong.

    REFINER_QUESTIONER and REFINER_REWRITER were one member (REFINER) until
    2026-08-08: two LLM calls with different inputs/outputs (Requirement, QualityReport
    -> RefinerTurn; requirement + RefinerAnswer[] -> RefinedRequirement) shared one
    stage identity, one model config, and one prompt hash, so neither could be
    configured, measured, or retried independently of the other. See DESIGN_NOTES.md,
    "Refiner split into REFINER_QUESTIONER / REFINER_REWRITER"."""
    CLASSIFIER = "classifier"
    QUALITY_CHECKER = "quality_checker"
    REFINER_QUESTIONER = "refiner_questioner"
    REFINER_REWRITER = "refiner_rewriter"
    STRATEGY_SELECTOR = "strategy_selector"
    TEST_GENERATOR = "test_generator"


class DocumentStage(str, Enum):
    """Stages that run once per document, before per-requirement processing begins."""
    CONSISTENCY_CHECKER = "consistency_checker"
    DEPENDENCY_MAPPER = "dependency_mapper"


class FailureKind(str, Enum):
    """Why a stage call ultimately failed. Distinguishes four cases that mean
    different things for retry policy and for the thesis's LLM-reliability numbers:
    a rejected request that's worth retrying (TRANSPORT), a schema-rejected model
    output (VALIDATION -- retrying may help, but "how often does this model produce
    invalid output" is itself a finding), a rejected request retrying can never fix
    (FATAL, added 2026-08-09 -- see its own paragraph below), and anything else caught
    but not anticipated (OTHER). See
    docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md.

    Not exhaustive of every possible mistake by construction, which is exactly why
    OTHER exists -- a two-value enum would force a 500 or an SDK bug into one of the
    other two buckets, corrupting both counts silently. OTHER is for a caught stage-call
    failure of unanticipated type; it is NOT for bugs in the orchestrator's own control
    flow, which must still crash rather than be filed here (see
    orchestrator/pipeline.py's call_stage).

    FATAL (2026-08-09): added for orchestrator/stage_fns.py's StageCallFatal -- a
    provider configuration/capability/authentication error where retrying with the same
    inputs cannot possibly succeed (bad credentials, an output mode the model doesn't
    support). Distinct from TRANSPORT, whose own meaning above is "retry usually helps" --
    reusing TRANSPORT for a fatal error would make the kind lie about what happened.
    call_stage/call_document_stage record exactly one attempt for a FATAL failure and
    stop, rather than spending the remaining retry budget on a request that cannot
    succeed. See design/ORCHESTRATOR_CONTRACT.md and design/DESIGN_NOTES.md.
    """
    TRANSPORT = "transport"
    VALIDATION = "validation"
    FATAL = "fatal"
    OTHER = "other"


class AttemptResult(str, Enum):
    """What happened on ONE attempt of a stage call -- distinct from FailureKind, which
    is scoped to "why did the stage *finally* fail" and is used only on the exhausted-
    stage summary (StageError/DocumentStageError). Every attempt needs a result,
    including the ones that succeeded, which FailureKind has no member for and was
    never meant to cover. See
    docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md.
    """
    SUCCESS = "success"
    TRANSPORT_FAILURE = "transport_failure"
    VALIDATION_FAILURE = "validation_failure"
    FATAL_FAILURE = "fatal_failure"
    OTHER_FAILURE = "other_failure"


# The four failure variants of AttemptResult map 1:1 onto FailureKind -- used by the
# StageError/DocumentStageError agreement checks below to compare a StageAttempt's
# result against the kind an error claims.
_ATTEMPT_RESULT_TO_FAILURE_KIND: dict[AttemptResult, FailureKind] = {
    AttemptResult.TRANSPORT_FAILURE: FailureKind.TRANSPORT,
    AttemptResult.VALIDATION_FAILURE: FailureKind.VALIDATION,
    AttemptResult.FATAL_FAILURE: FailureKind.FATAL,
    AttemptResult.OTHER_FAILURE: FailureKind.OTHER,
}


def _attempt_shape_error(
    result: AttemptResult, error_message: Optional[str],
    prompt_tokens: Optional[int], completion_tokens: Optional[int],
) -> Optional[str]:
    """Shared shape rule for StageAttempt/DocumentStageAttempt (identical body, two
    classes -- same PipelineStage/DocumentStage split reasoning as everywhere else in
    this file). Returns an error message, or None if the shape is fine."""
    if (prompt_tokens is None) != (completion_tokens is None):
        return "prompt_tokens and completion_tokens must both be set or both be absent"
    has_tokens = prompt_tokens is not None

    if result is AttemptResult.SUCCESS:
        if error_message is not None:
            return "a successful attempt must not carry an error_message"
        if not has_tokens:
            return "a successful attempt must record token counts"
    elif result is AttemptResult.VALIDATION_FAILURE:
        if error_message is None:
            return "a validation failure must carry an error_message"
        if not has_tokens:
            return ("a validation failure spent tokens on rejected output and must "
                    "record them")
    elif result is AttemptResult.TRANSPORT_FAILURE:
        if error_message is None:
            return "a transport failure must carry an error_message"
        if has_tokens:
            return ("a transport failure means the request was rejected before "
                    "inference -- it cannot carry token counts")
    elif result is AttemptResult.FATAL_FAILURE:
        if error_message is None:
            return "a fatal failure must carry an error_message"
        if has_tokens:
            return ("a fatal failure means the request was rejected before inference "
                    "-- it cannot carry token counts")
    else:  # OTHER_FAILURE
        if error_message is None:
            return "an other-failure attempt must carry an error_message"
        # Token counts are optional here, deliberately not forced either way -- an
        # unanticipated failure may or may not have happened after inference returned.
        # See FailureKind.OTHER's docstring.
    return None


class StageAttempt(BaseModel):
    """One attempt at one per-requirement stage call -- the complete record of every
    call_stage() try, success or failure, not just the ones that returned (contrast the
    old TokenUsage, which only recorded returns, and StageError, which records only the
    final exhausted attempt). invocation_id groups every attempt belonging to one
    logical call_stage() invocation: retries share it, a fresh call gets a new one --
    e.g. Quality Checker round 1 and round 2 are different invocations even though both
    use PipelineStage.QUALITY_CHECKER. See
    docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md.
    """
    stage: PipelineStage
    invocation_id: NonEmptyStr
    attempt_number: int = Field(..., ge=1)
    result: AttemptResult
    error_message: Optional[str] = Field(None, min_length=1)
    prompt_tokens: Optional[int] = Field(None, ge=0)
    completion_tokens: Optional[int] = Field(None, ge=0)

    @model_validator(mode="after")
    def _shape_matches_result(self) -> "StageAttempt":
        if msg := _attempt_shape_error(self.result, self.error_message,
                                       self.prompt_tokens, self.completion_tokens):
            raise ValueError(msg)
        return self


class DocumentStageAttempt(BaseModel):
    """Structurally identical to StageAttempt apart from the stage type -- same
    PipelineStage/DocumentStage split reasoning as TokenUsage/DocumentTokenUsage and
    StageError/DocumentStageError before it."""
    stage: DocumentStage
    invocation_id: NonEmptyStr
    attempt_number: int = Field(..., ge=1)
    result: AttemptResult
    error_message: Optional[str] = Field(None, min_length=1)
    prompt_tokens: Optional[int] = Field(None, ge=0)
    completion_tokens: Optional[int] = Field(None, ge=0)

    @model_validator(mode="after")
    def _shape_matches_result(self) -> "DocumentStageAttempt":
        if msg := _attempt_shape_error(self.result, self.error_message,
                                       self.prompt_tokens, self.completion_tokens):
            raise ValueError(msg)
        return self


def _group_attempts_by_invocation(attempts: list) -> dict[str, list]:
    """Groups a flat attempt log into per-invocation runs, keyed by invocation_id,
    preserving first-appearance order (plain dict insertion order). Attempts within one
    call_stage()/call_document_stage() invocation are always contiguous -- the retry
    loop runs straight through before any other invocation can be appended -- which is
    exactly what _attempts_are_well_formed (below) checks, not assumes."""
    groups: dict[str, list] = {}
    for a in attempts:
        groups.setdefault(a.invocation_id, []).append(a)
    return groups


def _attempts_are_well_formed(attempts: list, where: str) -> None:
    """Shared invariant for RequirementRunRecord.attempts / DocumentRunRecord.attempts:

    - one invocation_id names exactly one stage (catches an id reused across calls);
    - attempt numbers within an invocation are exactly 1..N, in that order (catches
      gaps, duplicates, and out-of-order entries with one comparison -- a dedicated
      `_require_unique` on (invocation_id, attempt_number) was tried and deleted after
      mutation-testing proved it unreachable: any duplicate number inside one group
      already breaks that group's list from equalling range(1, len+1), so this single
      comparison was always the one actually catching it. Per CLAUDE.md, "don't write
      a check that can't fire." See
      docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md);
    - at most one SUCCESS per invocation, and only as the last attempt (a retry loop
      stops the moment a call succeeds);
    - attempts for one invocation are contiguous in the flat list -- once the list
      moves on to a different invocation_id, the earlier one may not reappear, since no
      real call sequence can produce that interleaving.

    See docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md.
    """
    seen_ids: set[str] = set()
    prev_id: Optional[str] = None
    for a in attempts:
        if a.invocation_id != prev_id:
            if a.invocation_id in seen_ids:
                raise ValueError(
                    f"{where}: attempts for invocation_id {a.invocation_id!r} are not "
                    "contiguous -- another invocation's attempts appear in between"
                )
            seen_ids.add(a.invocation_id)
            prev_id = a.invocation_id

    for invocation_id, group in _group_attempts_by_invocation(attempts).items():
        stages = {a.stage for a in group}
        if len(stages) > 1:
            raise ValueError(
                f"{where}: invocation_id {invocation_id!r} names more than one stage: "
                f"{sorted(s.value for s in stages)}"
            )
        numbers = [a.attempt_number for a in group]
        if numbers != list(range(1, len(group) + 1)):
            raise ValueError(
                f"{where}: invocation_id {invocation_id!r} attempt_numbers are "
                f"{numbers}, expected 1..{len(group)} in order with no gaps"
            )
        # One check does the work of "at most one SUCCESS" AND "SUCCESS must be last"
        # combined: successes[0] is the FIRST success's index (enumerate gives sorted,
        # distinct indices), so it can only equal the final position len(group)-1 when
        # there is exactly one success in the group. A hand-written second check for
        # "len(successes) > 1" was tried and proved unreachable by mutation -- whenever
        # 2+ successes exist, the smallest of their indices is strictly less than the
        # last position, so THIS check always catches it first. Deleted per CLAUDE.md
        # ("don't write a check that can't fire"): unreachable means untestable, which
        # means untested. See
        # docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md.
        successes = [i for i, a in enumerate(group) if a.result is AttemptResult.SUCCESS]
        if successes and successes[0] != len(group) - 1:
            raise ValueError(
                f"{where}: invocation_id {invocation_id!r} has a SUCCESS attempt that "
                "is not the last one -- a retry loop stops the moment a call succeeds "
                "(this also covers more than one SUCCESS in the same invocation)"
            )


def _errors_agree_with_attempts(
    errors: list, attempts: list, where: str,
    backward_exempt_stages: frozenset = frozenset(),
    backward_exemption_active: bool = False,
) -> None:
    """Shared agreement check for StageError-vs-StageAttempt and
    DocumentStageError-vs-DocumentStageAttempt. Two directions:

    Forward -- every error must reference a real, matching, failed invocation: same
    stage, not a SUCCESS, kind/message/retry_count agree with that invocation's final
    attempt. No exceptions, at either level (schema version 1.1: a StageError with no
    backing invocation is invalid).

    Backward -- every failed invocation must be summarised by some error, UNLESS its
    stage is in backward_exempt_stages AND backward_exemption_active is True -- the one
    place the pipeline intentionally deletes an error that used to exist: a
    RequirementRunRecord with outcome=CAP_STOPPED strips errors naming
    STRATEGY_SELECTOR/TEST_GENERATOR (required by _outcome_matches_contents) but never
    strips the append-only attempts log. DocumentRunRecord calls this with
    backward_exempt_stages empty, since nothing strips DocumentStageError entries.

    See docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md.
    """
    _require_unique([e.invocation_id for e in errors], "invocation_id", f"{where} errors")

    by_invocation = _group_attempts_by_invocation(attempts)

    for err in errors:
        group = by_invocation.get(err.invocation_id)
        if group is None:
            raise ValueError(
                f"{where}: error for {err.stage.value!r} references invocation_id "
                f"{err.invocation_id!r}, which has no matching attempts"
            )
        final = group[-1]
        if final.stage != err.stage:
            raise ValueError(
                f"{where}: error claims stage {err.stage.value!r}, but invocation "
                f"{err.invocation_id!r} is for {final.stage.value!r}"
            )
        if final.result is AttemptResult.SUCCESS:
            raise ValueError(
                f"{where}: error references invocation {err.invocation_id!r}, whose "
                "final attempt succeeded -- an error cannot summarise a successful call"
            )
        expected_kind = _ATTEMPT_RESULT_TO_FAILURE_KIND[final.result]
        if err.kind != expected_kind:
            raise ValueError(
                f"{where}: error kind is {err.kind.value!r}, but invocation "
                f"{err.invocation_id!r}'s final attempt was {final.result.value!r} "
                f"(expected kind {expected_kind.value!r})"
            )
        if err.message != final.error_message:
            raise ValueError(
                f"{where}: error message does not match invocation "
                f"{err.invocation_id!r}'s final attempt"
            )
        if err.retry_count != len(group) - 1:
            raise ValueError(
                f"{where}: error retry_count is {err.retry_count}, but invocation "
                f"{err.invocation_id!r} has {len(group)} attempts (expected "
                f"retry_count={len(group) - 1})"
            )

    covered = {e.invocation_id for e in errors}
    for invocation_id, group in by_invocation.items():
        final = group[-1]
        if final.result is AttemptResult.SUCCESS or invocation_id in covered:
            continue
        if backward_exemption_active and final.stage in backward_exempt_stages:
            continue
        raise ValueError(
            f"{where}: invocation {invocation_id!r} ({final.stage.value!r}) ended in "
            f"failure but no error references it"
        )


class StageError(BaseModel):
    # An enum rather than a free string so "which stage fails most" stays countable --
    # "classifier" vs "Classifier" would silently split the tally. See DESIGN_NOTES.md.
    stage: PipelineStage
    # Which attempts-log invocation this error summarises -- the direct link that
    # replaces matching by list position or by aggregating across invocations. See
    # docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md.
    invocation_id: NonEmptyStr
    kind: FailureKind
    message: NonEmptyStr
    # Retries attempted *before* giving up and recording this error, so 0 means it
    # failed on the first attempt with no retry. A StageError only ever exists for a
    # call that ultimately failed -- retries that succeeded leave no trace here, so
    # this measures "how hard we tried before losing", not overall retry effectiveness.
    # Recorded because free-tier rate limits make retry-with-backoff the normal path,
    # and "did retrying help?" is otherwise unanswerable from the run records.
    retry_count: int = Field(0, ge=0)


class DocumentStageError(BaseModel):
    # Structurally identical to StageError apart from the stage type. Deliberately a
    # separate model rather than a shared base or a `PipelineStage | DocumentStage`
    # union: the union would let a RequirementRunRecord name a document stage again,
    # which is the exact thing the split exists to prevent. Two duplicated fields is a
    # cheaper price than that hole. See DESIGN_NOTES.md.
    stage: DocumentStage
    invocation_id: NonEmptyStr
    kind: FailureKind
    message: NonEmptyStr
    retry_count: int = Field(0, ge=0)


class RunOutcome(str, Enum):
    IN_PROGRESS = "in_progress"      # started, not finished (also the resume marker)
    COMPLETED = "completed"          # converged and produced a test plan
    CAP_GENERATED = "cap_generated"  # revision cap hit; human chose to generate anyway
    CAP_STOPPED = "cap_stopped"      # revision cap hit; human chose to stop
    ERROR = "error"                  # a stage call failed


# Outcomes after which a requirement needs no further work. IN_PROGRESS and ERROR are
# absent deliberately: both mean "come back to this one", which is what makes a failed
# requirement retryable without reprocessing the whole document.
TERMINAL_OUTCOMES = frozenset({
    RunOutcome.COMPLETED,
    RunOutcome.CAP_GENERATED,
    RunOutcome.CAP_STOPPED,
})


class _OutcomeRule(NamedTuple):
    """What a record must and must not contain for a given outcome to be honest.

    Declared as a table rather than a chain of ifs so the rule set can be *read off*
    and audited. The first version of this validator was hand-written conditionals and
    silently under-checked eight distinct contradictions -- the failure mode of ad-hoc
    conditionals is that nothing tells you which combinations you forgot. See
    DESIGN_NOTES.md.
    """
    required: tuple[str, ...] = ()        # must not be None
    forbidden: tuple[str, ...] = ()        # must be None
    non_empty: tuple[str, ...] = ()        # list field, must have >= 1 entry
    last_report_passed: Optional[bool] = None  # required state of quality_reports[-1]


_OUTCOME_RULES: dict[RunOutcome, _OutcomeRule] = {
    # Nothing has necessarily happened yet, so almost nothing is required. An
    # in-progress run has neither failed nor reached a cap.
    RunOutcome.IN_PROGRESS: _OutcomeRule(
        forbidden=("cap_reason",),
    ),
    # Converged: the last quality check must actually have passed, and stages 3/4 ran.
    # `final_requirement` is not in `required` because a requirement that was clean on
    # the first pass legitimately has none. That is only true while nothing refined it,
    # though -- once the record shows refinement happened, it becomes required. That
    # rule depends on the *contents* of other fields, which a static presence/absence
    # table cannot express, so it lives in the validator (see _refinement_is_recorded).
    RunOutcome.COMPLETED: _OutcomeRule(
        required=("classification", "test_strategy", "test_plan"),
        forbidden=("cap_reason",),
        non_empty=("rounds",),
        last_report_passed=True,
    ),
    # Cap hit, human chose to generate anyway. A cap is only reachable after the
    # Classifier ran, at least one refinement round happened (so a RefinedRequirement
    # exists -- without it `final_text` would silently report the ORIGINAL text as the
    # text tests were generated from), and the last check still failed.
    RunOutcome.CAP_GENERATED: _OutcomeRule(
        required=("classification", "test_strategy", "test_plan", "cap_reason"),
        non_empty=("rounds",),
        last_report_passed=False,
    ),
    # Cap hit, human chose to stop. The decision is taken before stage 3, so neither a
    # strategy nor a plan may exist.
    RunOutcome.CAP_STOPPED: _OutcomeRule(
        required=("classification", "cap_reason"),
        forbidden=("test_strategy", "test_plan"),
        non_empty=("rounds",),
        last_report_passed=False,
    ),
    # A stage can fail at any point, so almost nothing is constrained. `cap_reason` is
    # deliberately allowed: the human may have chosen "generate anyway" and the Test
    # Case Generator then hit a rate limit.
    # Like the document record, `errors` is a LOG of failed attempts, not a statement
    # of current state -- so any outcome may carry one. ERROR specifically means the run
    # stopped *because of* a failure, which is why it needs at least one.
    RunOutcome.ERROR: _OutcomeRule(
        non_empty=("errors",),
    ),
}


def _apply_outcome_rule(obj: BaseModel, label: str, rule: _OutcomeRule) -> None:
    """Shared by RequirementRunRecord and DocumentRunRecord. Checks presence/absence
    only; outcome-specific extras (last report state, error/report agreement) stay in
    each record's own validator."""
    for name in rule.required:
        if getattr(obj, name) is None:
            raise ValueError(f"outcome={label} requires {name}")
    for name in rule.forbidden:
        if getattr(obj, name) is not None:
            raise ValueError(f"outcome={label} must not have {name}")
    for name in rule.non_empty:
        if not getattr(obj, name):
            raise ValueError(f"outcome={label} requires at least one entry in {name}")


# ---------------------------------------------------------------------------
# Full per-requirement run record -- one JSON file per requirement per run
# (.model_dump_json(indent=2)); doubles as the evaluation dataset.
# ---------------------------------------------------------------------------

class RequirementRunRecord(BaseModel):
    requirement: Requirement
    # Which run produced this. Required, because under D2b each requirement is its own
    # file: without it a stray file is unattributable, and -- worse -- records from a
    # different run can be assembled into a document with nothing noticing. The full
    # RunMetadata is NOT duplicated here (it would repeat 7 stage configs per
    # requirement); DocumentRunRecord checks this matches its own metadata.run_id, so
    # the pointer is enough to find the provenance and enough to detect a mismatch.
    run_id: NonEmptyStr
    # Defaults to IN_PROGRESS so a record can be created the moment a requirement is
    # picked up, before any stage has run, and written out incrementally. That is what
    # makes an interrupted run resumable, and it is also why every stage output below
    # is Optional -- a stage that never ran (or failed) has nothing to store.
    outcome: RunOutcome = RunOutcome.IN_PROGRESS
    # A list, and allowed on any outcome: a stage that failed and then succeeded on a
    # retry keeps its failure on record, so "how many requirements needed a retry" stays
    # countable. Symmetric with DocumentRunRecord.errors -- both now allow more than one
    # error per stage (a document-level stage can be retried across multiple manual
    # `retry_document_stage` calls, each its own invocation; here the Quality Checker
    # and Refiner run once per round, so the same stage can legitimately fail more than
    # once in one requirement). Each error is linked to the invocation that produced it
    # via `invocation_id`; which round a failure happened in is derivable from that.
    errors: list[StageError] = Field(default_factory=list)
    # Required on both cap outcomes, forbidden on every other one -- this free text is
    # the audit trail the "ask the human at the cap" decision was chosen for, so a cap
    # record without it discards the only thing that makes that run interpretable.
    # min_length stops "" from satisfying the requirement vacuously. The schema cannot
    # judge whether the text is *useful*; that limitation is noted in DESIGN_NOTES.md.
    cap_reason: Optional[str] = Field(
        None, min_length=1,
        description="Why the human chose to generate anyway / stop at the revision cap",
    )
    classification: Optional[Classification] = None
    # The operator's own system-type label, captured for comparison against
    # classification.system_type -- the Classifier's accuracy has had n=0 since no human
    # label has ever been collected (design/DESIGN_NOTES.md, "System changes to make
    # before the evaluation freeze", S2). Deliberately NOT reconciled with
    # `classification`: no validator requires agreement, because disagreement here is
    # the measurement, not a bug. Provenance is the two field names themselves -- this
    # one is always operator-supplied (set out-of-band by
    # orchestrator/cli.py's `label-system-type` subcommand, never by a stage fn),
    # `classification.system_type` is always the Classifier's own stage output -- rather
    # than a separate "who set this" marker, per Known Limitation 9's discussion of this
    # capture. This is a RECORD, not an override: nothing downstream reads it, and it
    # cannot change what the pipeline already decided.
    operator_system_type: Optional[SystemType] = None
    # The refinement trajectory, in order: round 1 checks the original text, each
    # subsequent round checks the previous round's rewrite. Replaces the old parallel
    # quality_reports / refiner_turns / refiner_answers lists (see DESIGN_NOTES.md).
    # `[len(r.quality_report.issues) for r in rounds]` is the convergence curve.
    rounds: list[RefinementRound] = Field(default_factory=list)
    test_strategy: Optional[TestStrategy] = None
    test_plan: Optional[TestPlan] = None
    # The complete log of every call_stage() attempt for this requirement, success or
    # failure -- source of truth for token totals (see total_tokens) and for what each
    # StageError in `errors` summarises (see _errors_agree_with_attempts). Replaces the
    # old TokenUsage-only log. See
    # docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md.
    attempts: list[StageAttempt] = Field(default_factory=list)

    @model_validator(mode="after")
    def _attempts_well_formed(self) -> "RequirementRunRecord":
        _attempts_are_well_formed(self.attempts, "RequirementRunRecord.attempts")
        return self

    @model_validator(mode="after")
    def _errors_agree_with_final_attempts(self) -> "RequirementRunRecord":
        # CAP_STOPPED strips errors naming STRATEGY_SELECTOR/TEST_GENERATOR (required by
        # _outcome_matches_contents below) but never strips attempts, which is
        # append-only -- so a failed invocation of either stage surviving with no error
        # is expected specifically here, and nowhere else.
        _errors_agree_with_attempts(
            self.errors, self.attempts, "RequirementRunRecord",
            backward_exempt_stages=frozenset(
                {PipelineStage.STRATEGY_SELECTOR, PipelineStage.TEST_GENERATOR}),
            backward_exemption_active=self.outcome is RunOutcome.CAP_STOPPED,
        )
        return self

    @model_validator(mode="after")
    def _outcome_matches_contents(self) -> "RequirementRunRecord":
        """Same intent as QualityReport._passed_matches_issues: an outcome label that
        contradicts what the record actually holds is rejected where it is created, not
        discovered downstream. Rules live in _OUTCOME_RULES above."""
        o = self.outcome
        rule = _OUTCOME_RULES[o]
        _apply_outcome_rule(self, o.value, rule)

        if rule.last_report_passed is not None:
            actual = self.rounds[-1].quality_report.passed  # non_empty guarantees this
            if actual is not rule.last_report_passed:
                raise ValueError(
                    f"outcome={o.value} requires the last round's quality_report to have "
                    f"passed={rule.last_report_passed} (got passed={actual})"
                )

        # A cap can only be reached by exhausting revisions, so at least one round must
        # have produced a rewrite. (This replaces the old `final_requirement` entry in
        # the cap rule rows: final_requirement is now derived from rounds.)
        if o in (RunOutcome.CAP_GENERATED, RunOutcome.CAP_STOPPED):
            if not any(r.rewrite is not None for r in self.rounds):
                raise ValueError(
                    f"outcome={o.value} means the revision cap was reached, so at least "
                    "one round must have produced a rewrite"
                )

        # A stage whose output this outcome forbids never ran, so it cannot have failed.
        # CAP_STOPPED means the human stopped the run before stage 3.
        if o is RunOutcome.CAP_STOPPED:
            impossible = sorted({e.stage.value for e in self.errors}
                                & {PipelineStage.STRATEGY_SELECTOR.value,
                                   PipelineStage.TEST_GENERATOR.value})
            if impossible:
                raise ValueError(
                    f"outcome=cap_stopped records failures in {impossible}, but those "
                    "stages never ran -- the human stopped before stage 3"
                )

        # Mirror of the document record's "DEGRADED but both reports present" rule: if
        # every stage produced its output, nothing is missing, so the run did not stop
        # because of a failure. Recorded failures on such a record are earlier attempts
        # that were retried successfully -- which makes it COMPLETED, not ERROR.
        if o is RunOutcome.ERROR:
            if (self.classification is not None and self.rounds
                    and self.test_strategy is not None and self.test_plan is not None):
                raise ValueError(
                    "outcome=error but every stage produced its output -- a run whose "
                    "stages all eventually succeeded is not an error, even if some "
                    "failed on an earlier attempt"
                )
        return self

    @model_validator(mode="after")
    def _issue_identity_is_stable(self) -> "RequirementRunRecord":
        """An issue id must mean the same defect in every round it appears in.

        Each round's QualityReport is a fresh LLM call minting its own ids, so nothing
        inherently links round 2's `REQ-D-ISSUE-1` to round 1's. The concrete failure:
        round 1 raises ISSUE-1 (vague pronoun) and ISSUE-2 (non-verifiable); the human
        confirms ISSUE-1 resolved; round 2 finds only the non-verifiable one left and
        numbers it ISSUE-1, because it is the first issue *it* found. Suppressing
        "ISSUE-1" then drops a real, unresolved defect and the requirement passes.

        The orchestrator is responsible for matching a round's issues against the
        previous round's and reusing the id when it is the same defect (matching on
        category and span). This validator enforces the contract that makes that
        matching trustworthy: a reused id must carry the same category and span, and a
        suppression must point at something actually raised and actually confirmed.

        What no schema can fix: the matching itself is a heuristic. If the Refiner
        rewrites the sentence, the span text changes and the same defect may look new.
        That is inherent -- but under this design it is visible in the record (an id
        stops and a new one starts) rather than invisible.
        """
        seen: dict[str, tuple[IssueCategory, Optional[str]]] = {}
        raised_by: dict[str, int] = {}          # issue id -> first revision raised
        confirmed_by: dict[str, int] = {}       # issue id -> revision confirmed resolved
        carried: set[str] = set()               # suppressions active so far

        for rnd in self.rounds:
            n = rnd.revision_number

            for issue in rnd.quality_report.issues:
                identity = (issue.category, issue.span)
                previous = seen.get(issue.id)
                if previous is not None and previous != identity:
                    raise ValueError(
                        f"issue id {issue.id!r} was {previous[0].value!r}"
                        f"{f' on {previous[1]!r}' if previous[1] else ''} in an earlier "
                        f"revision but is {identity[0].value!r}"
                        f"{f' on {identity[1]!r}' if identity[1] else ''} in revision {n} "
                        "-- one id, two different defects"
                    )
                seen[issue.id] = identity
                raised_by.setdefault(issue.id, n)

            for suppressed in rnd.suppressed_issue_ids:
                # No separate "was it ever raised?" check: a question may only reference
                # an issue raised in its own round (_round_is_coherent), so a
                # confirmation implies the issue was raised. An unraised id is therefore
                # always caught here as unconfirmed, and a separate check could never
                # fire on its own -- untestable in isolation, therefore untested.
                if suppressed not in confirmed_by:
                    raise ValueError(
                        f"revision {n} suppresses {suppressed!r}, but no earlier revision "
                        "raised it and had the human confirm it resolved "
                        "(user_confirms_resolved)"
                    )
            # A suppression is the human's standing instruction not to re-flag, so it
            # must hold for every later round. Dropping one lets the issue reappear --
            # the exact loop user_confirms_resolved exists to break.
            missing = carried - set(rnd.suppressed_issue_ids)
            if missing:
                raise ValueError(
                    f"revision {n} stops suppressing {sorted(missing)}, confirmed "
                    "resolved earlier -- suppressions must carry forward"
                )
            carried |= set(rnd.suppressed_issue_ids)

            # Confirmations made this round take effect from the next one.
            if rnd.turn is not None:
                issue_of = {q.id: q.issue_id for q in rnd.turn.questions}
                for ans in rnd.answers:
                    if ans.user_confirms_resolved:
                        confirmed_by.setdefault(issue_of[ans.question_id], n)
        return self

    @model_validator(mode="after")
    def _denormalised_fields_agree(self) -> "RequirementRunRecord":
        """Everything on this record that restates something must restate it correctly.

        Three separate denormalisations, all previously unchecked:

          - `requirement_id`, repeated on classification / test_strategy / test_plan.
            Swept by discovery (see fields_carrying_requirement_id) rather than by a
            hand-written list, because reviews found these one field at a time.
          - `system_type`, carried from Classification into TestStrategy. TestStrategy
            keeps its own copy because it is a standalone stage output whose technique
            eligibility depends on it -- so the copy stays, and is checked.
          - `technique_used` on each TestCase, which must be one the strategy actually
            selected. Without this the technique-selection rules are decorative:
            TestStrategy.rationale can be audited against the rules while the generated
            cases quietly use something else entirely.
        """
        rid = self.requirement.id
        for name in fields_carrying_requirement_id(self):
            other = getattr(self, name).requirement_id
            if other != rid:
                raise ValueError(
                    f"{name}.requirement_id is {other!r}, but this record is for {rid!r}"
                )

        if self.classification is not None and self.test_strategy is not None:
            if self.classification.system_type is not self.test_strategy.system_type:
                raise ValueError(
                    f"test_strategy.system_type is "
                    f"{self.test_strategy.system_type.value!r}, but the Classifier said "
                    f"{self.classification.system_type.value!r}"
                )

        if self.test_strategy is not None and self.test_plan is not None:
            selected = set(self.test_strategy.techniques)
            for case in self.test_plan.test_cases:
                if case.technique_used not in selected:
                    raise ValueError(
                        f"test case {case.id!r} uses "
                        f"{case.technique_used.value!r}, which the strategy did not "
                        f"select ({sorted(t.value for t in selected)})"
                    )
        return self

    @model_validator(mode="after")
    def _trajectory_is_continuous(self) -> "RequirementRunRecord":
        """The rounds must form an unbroken chain from the original text onward.

        Each round is internally coherent on its own (see RefinementRound). This checks
        the joins between them: rounds are numbered 1..N with no gaps, round 1 checks
        the requirement's own text, and every later round checks exactly the text the
        previous round rewrote. Without this the trajectory could contain a round whose
        text came from nowhere -- which is what made the old flat lists unusable as a
        history in the first place.
        """
        for i, rnd in enumerate(self.rounds, start=1):
            if rnd.revision_number != i:
                raise ValueError(
                    f"rounds must be numbered 1..N in order; position {i} says "
                    f"revision {rnd.revision_number}"
                )
            if rnd.quality_report.requirement_id != self.requirement.id:
                raise ValueError(
                    f"revision {i} reports on {rnd.quality_report.requirement_id!r}, "
                    f"but this record is for {self.requirement.id!r}"
                )

        if not self.rounds:
            return self

        if self.rounds[0].text_checked != self.requirement.text:
            raise ValueError("revision 1 must check the requirement's original text")

        for prev, cur in zip(self.rounds, self.rounds[1:]):
            if prev.rewrite is None:
                raise ValueError(
                    f"revision {cur.revision_number} exists, but revision "
                    f"{prev.revision_number} produced no rewrite -- nothing generated "
                    "the text it checked"
                )
            if cur.text_checked != prev.rewrite.refined_text:
                raise ValueError(
                    f"revision {cur.revision_number} does not check the text revision "
                    f"{prev.revision_number} produced"
                )
        return self

    # -- The exact text stages 3/4 ran on, for whichever path this requirement took. --
    #
    # Two decorators, doing two different jobs (both are needed, order matters):
    #
    #   @property        Plain Python. Lets you write `rec.final_text` instead of
    #                    `rec.final_text()`. Recomputed on every access -- nothing is
    #                    stored -- and it is read-only, so it cannot be set to something
    #                    that disagrees with the rounds.
    #
    #   @computed_field  Pydantic v2 only. Pydantic serialises declared *fields*; a
    #                    plain @property is invisible to `.model_dump_json()`. This
    #                    decorator includes it in the dumped JSON too, which matters
    #                    here because these records double as the evaluation dataset
    #                    -- anyone reading them later gets the answer directly instead
    #                    of reimplementing the rule themselves.
    #
    # Consequences of being computed rather than stored: it cannot be passed to the
    # constructor (`RequirementRunRecord(final_text=...)` is ignored), and the return
    # type annotation below is required -- Pydantic reads it to build the JSON schema.
    #
    # With `rounds`, the old silent-fallback bug is structurally gone: the last round
    # records the exact text it checked, so there is no case where this can quietly
    # report the original text for a requirement that was rewritten. The only remaining
    # fallback is an empty rounds list, which means no check has run yet at all.
    @computed_field
    @property
    def final_text(self) -> str:
        if not self.rounds:
            return self.requirement.text
        last = self.rounds[-1]
        # A trailing rewrite means the cap fired after refining but before re-checking,
        # so that rewrite -- not the text it replaced -- is the latest version.
        return last.rewrite.refined_text if last.rewrite else last.text_checked

    # The most recent rewrite, or None if nothing was ever refined. Derived from rounds
    # rather than stored alongside them, so the two cannot disagree.
    @computed_field
    @property
    def final_requirement(self) -> Optional[RefinedRequirement]:
        for rnd in reversed(self.rounds):
            if rnd.rewrite is not None:
                return rnd.rewrite
        return None

    # Deliberately NOT a RunOutcome value. A run can be both completed and
    # human-overridden -- those are two independent axes, and an enum would force a
    # choice between them. It is also fully derivable from data already stored, so
    # making it a computed field means it cannot drift. See DESIGN_NOTES.md.
    @computed_field
    @property
    def used_human_override(self) -> bool:
        return any(a.user_confirms_resolved for r in self.rounds for a in r.answers)

    # The convergence curve: issues remaining after each round's check. This is the
    # number the refinement loop exists to move, and the reason `rounds` replaced the
    # old parallel lists -- it was previously only inferable from list positions.
    @computed_field
    @property
    def issues_per_round(self) -> list[int]:
        return [len(r.quality_report.issues) for r in self.rounds]

    # Which revisions each issue was raised in. With stable ids (see
    # _issue_identity_is_stable) this traces one defect's life: [1, 2, 3] survived three
    # rounds, [1] was gone by round 2. "How many rounds does a VAGUE_PRONOUN take to
    # resolve?" comes straight off this plus each issue's category.
    #
    # Read alongside each round's `suppressed_issue_ids`: an id that stops appearing
    # because the human suppressed it was NOT fixed, and pooling the two would overstate
    # how well the loop converges.
    @computed_field
    @property
    def issue_history(self) -> dict[str, list[int]]:
        history: dict[str, list[int]] = {}
        for rnd in self.rounds:
            for issue in rnd.quality_report.issues:
                history.setdefault(issue.id, []).append(rnd.revision_number)
        return history

    # Cost at any price table, at any point: tokens x price, computed by the caller.
    # A stage retried twice then succeeded shows 3 attempt rows here -- one per try,
    # including the two that failed. Only attempts that recorded tokens contribute
    # (SUCCESS and VALIDATION_FAILURE always do; TRANSPORT_FAILURE never does;
    # OTHER_FAILURE does only when they happened to be available).
    @computed_field
    @property
    def total_tokens(self) -> int:
        return sum(a.prompt_tokens + a.completion_tokens for a in self.attempts
                   if a.prompt_tokens is not None)


# ---------------------------------------------------------------------------
# Document-level run record -- see DESIGN_NOTES.md
# ---------------------------------------------------------------------------

class DocumentOutcome(str, Enum):
    """Describes the *document-level stage phase only* (Consistency Checker and
    Dependency Mapper), which finishes before per-requirement processing begins.

    It deliberately says nothing about whether every requirement has been processed.
    That would be a second, independent axis -- the same mistake `HUMAN_OVERRIDE` would
    have been on RunOutcome -- and it is derivable anyway: see pending_requirement_ids.
    """
    IN_PROGRESS = "in_progress"  # document-level stages not finished
    COMPLETED = "completed"      # both reports present (possibly after a retry)
    DEGRADED = "degraded"        # at least one report still missing; processing went on


_DOCUMENT_OUTCOME_RULES: dict[DocumentOutcome, _OutcomeRule] = {
    # Deliberately unconstrained, INCLUDING errors: the document-level stages run one
    # after another, so "the Consistency Checker failed but the Dependency Mapper has
    # not run yet" is a real state that must be writable to disk mid-run (decision D2b
    # writes incrementally). Forcing DEGRADED the moment an error appears would make
    # DEGRADED reachable before the phase is over, i.e. not terminal.
    DocumentOutcome.IN_PROGRESS: _OutcomeRule(),
    DocumentOutcome.COMPLETED: _OutcomeRule(
        required=("consistency_report", "dependency_report"),
    ),
    DocumentOutcome.DEGRADED: _OutcomeRule(
        non_empty=("errors",),
    ),
    # Both terminal outcomes additionally require every stage to be *accounted for* --
    # each one either failed or filed a report. A table row can only express presence
    # of one field, not agreement between two, so that check lives in the validator.
}


# Which report each document-level stage is responsible for producing.
_DOCUMENT_STAGE_REPORTS: dict[DocumentStage, str] = {
    DocumentStage.CONSISTENCY_CHECKER: "consistency_report",
    DocumentStage.DEPENDENCY_MAPPER: "dependency_report",
}


# ---------------------------------------------------------------------------
# Run provenance -- what produced a run. See DESIGN_NOTES.md
# ---------------------------------------------------------------------------

# Every stage in the pipeline, document-level first. RunMetadata.stages must cover
# exactly this set, so "which model ran the Classifier?" always has an answer.
ALL_STAGES: tuple[str, ...] = (
    tuple(s.value for s in DocumentStage) + tuple(s.value for s in PipelineStage)
)


def prompt_fingerprint(prompt_text: str) -> str:
    """Short stable hash of a prompt's actual text.

    Use this to fill StageConfig.prompt_hash. The point is that it cannot be forgotten:
    edit a prompt without bumping the human-readable version label and the hash changes
    anyway, so two runs labelled the same but hashed differently are visibly mislabelled.
    """
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:12]


class OutputMode(str, Enum):
    """How a stage call asks its provider to shape a response, before that response is
    validated against the stage's own Pydantic model. Lives here, not in
    orchestrator/providers/, so orchestrator/config.py can import it without ever
    importing anything that depends on `requests` just to validate a YAML config's shape
    (see design/DESIGN_NOTES.md, "Run config, provider adapters, CLI HumanFns")."""
    TEXT = "text"
    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"


class StageConfig(BaseModel):
    """What one stage was configured with.

    Model and prompt are grouped in one object rather than kept as two parallel
    dicts (`models` and `prompt_hashes`) because parallel dicts can end up with
    different key sets and silently disagree about which stages exist.

    prompt_version/temperature/output_mode (2026-08-09): moved here from being single
    run-level fields on RunMetadata, because orchestrator/config.py's RunConfig allows
    per-stage overrides of all three. Two independently-settable copies (one here, one on
    RunMetadata) could disagree -- the "two fields that must agree" failure pattern
    CLAUDE.md names as the cause of most bugs in this project. Removing the redundant
    RunMetadata-level copies, rather than adding a validator to police them, closes that
    gap by construction instead of by discipline. See DESIGN_NOTES.md.
    """
    model: NonEmptyStr = Field(..., description="e.g. 'gemini/gemini-2.0-flash'")
    prompt_hash: NonEmptyStr = Field(..., description="from prompt_fingerprint()")
    prompt_version: NonEmptyStr
    temperature: float = Field(1.0, ge=0.0, le=2.0)
    output_mode: OutputMode = OutputMode.TEXT


class RunMetadata(BaseModel):
    run_id: NonEmptyStr
    started_at: datetime
    # Per stage, because different stages can legitimately use different models --
    # e.g. a cheap one for classification and a stronger one for test generation.
    # temperature/prompt_version/output_mode live on each StageConfig, not here -- see
    # StageConfig's own docstring for why the run-level copies that used to live on this
    # model were removed rather than kept in sync with a validator.
    stages: dict[str, StageConfig]
    # 1.0 -> 1.1 (2026-08-08): RequirementRunRecord/DocumentRunRecord's usage field was
    # replaced by the per-attempt log (attempts: list[StageAttempt/DocumentStageAttempt]),
    # and StageError/DocumentStageError gained invocation_id.
    # 1.1 -> 1.2 (2026-08-09): StageConfig gained prompt_version/temperature/output_mode;
    # RunMetadata's own run-level temperature/prompt_version fields were removed (see
    # StageConfig's docstring); FailureKind gained FATAL and AttemptResult gained
    # FATAL_FAILURE (orchestrator/stage_fns.py's StageCallFatal). No real run predates
    # either bump -- nothing to migrate -- the version exists so a future reader can tell
    # the record shapes apart. See
    # docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md and
    # DESIGN_NOTES.md.
    schema_version: NonEmptyStr = "1.2"

    @model_validator(mode="after")
    def _started_at_is_timezone_aware(self) -> "RunMetadata":
        """Reject a naive datetime.

        `datetime.now()` returns a naive value and is the obvious thing to write, but
        comparing a naive datetime with an aware one raises TypeError -- so a run
        recorded naively and another recorded correctly cannot be ordered, and the
        failure surfaces at analysis time rather than at the run. Cheap to require
        `datetime.now(timezone.utc)` instead. No bound on the value itself: an
        implausible date is a different (and much less likely) mistake.
        """
        if self.started_at.tzinfo is None:
            raise ValueError(
                "started_at must be timezone-aware (use datetime.now(timezone.utc), "
                "not datetime.now())"
            )
        return self

    @model_validator(mode="after")
    def _covers_every_stage(self) -> "RunMetadata":
        given, expected = set(self.stages), set(ALL_STAGES)
        if missing := sorted(expected - given):
            raise ValueError(f"stages is missing config for: {missing}")
        if unknown := sorted(given - expected):
            raise ValueError(f"stages contains unknown stage name(s): {unknown}")
        return self


class DocumentRunRecord(BaseModel):
    """One per document per run.

    **On-disk layout (decision D2b).** The requirement records are written as separate
    files, so the persisted document file carries an EMPTY `requirement_records` list
    and the two are assembled on load:

        <run_dir>/document.json                  <- this model, requirement_records=[]
        <run_dir>/requirements/THEMAS-REQ-A.json <- one RequirementRunRecord each
        ...

    That is why nothing here requires `requirement_records` to be non-empty, even on a
    COMPLETED document: the on-disk document file must itself be valid. It also means
    each requirement file is self-contained -- `Requirement` text is duplicated between
    `requirement_set` and each record, which is intended, not an oversight.
    """

    requirement_set: RequirementSet
    # Required, not optional: a record without provenance cannot be attributed to a
    # model or prompt version afterwards, and these files are the evaluation dataset.
    # The full config is known before a run starts, so there is no point at which it
    # would legitimately be missing.
    metadata: RunMetadata
    outcome: DocumentOutcome = DocumentOutcome.IN_PROGRESS
    # A list: the Consistency Checker and Dependency Mapper run independently, so both
    # can fail in one run (cardinality audit, checklist lens 1) -- and, since
    # retry_document_stage no longer merges repeated failures into one entry, a single
    # stage retried more than once can also produce more than one error, each linked to
    # its own invocation via invocation_id. Symmetric with RequirementRunRecord.errors.
    errors: list[DocumentStageError] = Field(default_factory=list)
    consistency_report: Optional[ConsistencyReport] = None
    dependency_report: Optional[DependencyReport] = None
    requirement_records: list[RequirementRunRecord] = Field(default_factory=list)
    # The complete log of every call_document_stage() attempt, success or failure --
    # source of truth for token totals (see document_stage_tokens) and for what each
    # DocumentStageError in `errors` summarises. Replaces the old
    # DocumentTokenUsage-only log. See
    # docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md.
    attempts: list[DocumentStageAttempt] = Field(default_factory=list)

    @model_validator(mode="after")
    def _attempts_well_formed(self) -> "DocumentRunRecord":
        _attempts_are_well_formed(self.attempts, "DocumentRunRecord.attempts")
        return self

    @model_validator(mode="after")
    def _errors_agree_with_final_attempts(self) -> "DocumentRunRecord":
        # No exemption at document level -- nothing ever strips a DocumentStageError,
        # unlike RequirementRunRecord's CAP_STOPPED case.
        _errors_agree_with_attempts(self.errors, self.attempts, "DocumentRunRecord")
        return self

    @model_validator(mode="after")
    def _outcome_matches_contents(self) -> "DocumentRunRecord":
        o = self.outcome
        _apply_outcome_rule(self, o.value, _DOCUMENT_OUTCOME_RULES[o])

        # Duplicates are now allowed (see `errors` field docstring above) -- this set is
        # still needed for the DEGRADED "a missing report must have a recorded failure
        # explaining it" check just below, which only cares which stages failed at all.
        failed: set[DocumentStage] = {err.stage for err in self.errors}

        # `errors` is a LOG OF FAILED ATTEMPTS, not a statement of current state. A
        # stage may therefore hold both an error (it failed once) and a report (a later
        # retry succeeded) -- that is precisely what makes retrying one document-level
        # stage possible without either erasing the failure or re-running the whole
        # document. An earlier version treated `errors` as current state and rejected
        # that combination; see DESIGN_NOTES.md for why the meaning changed.
        #
        # The mirror rule still holds, and is what keeps the log honest: a stage with no
        # report must have a recorded failure explaining its absence.
        if o is DocumentOutcome.DEGRADED:
            missing = [f for s, f in _DOCUMENT_STAGE_REPORTS.items()
                       if getattr(self, f) is None]
            if not missing:
                raise ValueError(
                    "outcome=degraded but both reports are present -- a document whose "
                    "stages all eventually succeeded is completed, even if they failed "
                    "on an earlier attempt"
                )
            for stage, field in _DOCUMENT_STAGE_REPORTS.items():
                if getattr(self, field) is None and stage not in failed:
                    raise ValueError(
                        f"{field} is missing but the {stage.value} has no recorded "
                        "failure explaining why"
                    )

        # One record per requirement, and it must be the requirement the set declares.
        # The `Requirement` duplication between the set and each record file is
        # deliberate (D2b keeps requirement files self-contained), so these are the two
        # ways that duplication can go wrong: counted twice, or drifted out of sync.
        by_id = {r.id: r for r in self.requirement_set.requirements}
        seen_ids: set[str] = set()
        for rec in self.requirement_records:
            rid = rec.requirement.id
            if rid not in by_id:
                raise ValueError(
                    f"requirement_records contains {rid!r}, which is not in requirement_set"
                )
            if rid in seen_ids:
                raise ValueError(f"more than one requirement_record for {rid!r}")
            seen_ids.add(rid)
            if rec.requirement != by_id[rid]:
                raise ValueError(
                    f"requirement_record for {rid!r} does not match the requirement in "
                    "requirement_set (text or source_doc_id has drifted)"
                )
            # Catches requirement files from another run being assembled into this
            # document -- the failure mode the per-file layout (D2b) makes possible.
            if rec.run_id != self.metadata.run_id:
                raise ValueError(
                    f"requirement_record for {rid!r} has run_id {rec.run_id!r}, but this "
                    f"document is run {self.metadata.run_id!r}"
                )
        return self

    @model_validator(mode="after")
    def _references_resolve(self) -> "DocumentRunRecord":
        """Every requirement id mentioned anywhere must exist in this document's set.

        The document record is the only place holding both the requirement set and
        everything that points into it, so this is the only level at which the check is
        possible. It targets a failure mode specific to LLM stages rather than a coding
        slip: a model asked to find conflicts across a document can return a plausible
        but invented id ("REQ-12" in a document that stops at REQ-8). Nothing about such
        a report looks wrong -- it is well-formed, and every field is populated -- so it
        would flow through the whole pipeline and into the results as a real finding.

        Also checks that the reports were produced for this document at all, when they
        say which document they came from.
        """
        known = {r.id for r in self.requirement_set.requirements}
        doc_id = self.requirement_set.doc_id

        for name in ("consistency_report", "dependency_report"):
            report = getattr(self, name)
            if report is not None and doc_id is not None and report.doc_id is not None:
                if report.doc_id != doc_id:
                    raise ValueError(
                        f"{name}.doc_id is {report.doc_id!r}, but this document is "
                        f"{doc_id!r}"
                    )

        def check(ids: list[str], where: str) -> None:
            unknown = sorted(set(ids) - known)
            if unknown:
                raise ValueError(f"{where} references unknown requirement id(s): {unknown}")

        if self.consistency_report is not None:
            for c in self.consistency_report.conflicts:
                check(c.requirement_ids, "consistency_report conflict")
        if self.dependency_report is not None:
            for d in self.dependency_report.dependencies:
                check([d.from_requirement_id, d.to_requirement_id], "dependency link")
        for rec in self.requirement_records:
            for rnd in rec.rounds:
                for issue in rnd.quality_report.issues:
                    check(issue.related_requirement_ids,
                          f"{rec.requirement.id} issue {issue.id!r}")
            if rec.test_plan is not None:
                for case in rec.test_plan.test_cases:
                    check(case.requirement_ids, f"test case {case.id!r}")

        # Test case ids must be unique across the whole document, not just within one
        # plan. The plans are assembled into a single suite (see Known Limitation 1), so
        # two plans each numbering their first case "TC-1" makes a result untraceable to
        # the case that produced it. TestPlan only guards uniqueness within itself.
        _require_unique(
            [c.id for r in self.requirement_records
             if r.test_plan is not None for c in r.test_plan.test_cases],
            "test case id", "the document's assembled test suite")
        return self

    # Resume support (decision D2b): everything that still needs work -- no record at
    # all, or a record whose outcome is IN_PROGRESS (interrupted) or ERROR (a stage
    # failed). The obvious first definition, "requirements with no record file", was
    # wrong: a requirement that errored *has* a record, so it looked started and a
    # resume pass skipped it. That reported "nothing to do" on an incomplete run and
    # defeated the purpose of recording ERROR at all. See DESIGN_NOTES.md.
    #
    # Derived rather than stored so it cannot disagree with the records actually
    # present -- the whole point when recovering an interrupted run.
    @computed_field
    @property
    def pending_requirement_ids(self) -> list[str]:
        finished = {r.requirement.id for r in self.requirement_records
                    if r.outcome in TERMINAL_OUTCOMES}
        return [r.id for r in self.requirement_set.requirements if r.id not in finished]

    # Deliberately NOT named total_tokens. Under D2b, requirement_records arrives
    # empty in the on-disk document.json and is only populated after assembly from
    # requirements/*.json -- a field named total_tokens would silently return near-zero
    # on disk and a large number post-assembly, same name, two different answers
    # depending on when it's read. This sums only the two document-level stages.
    # Whole-document cost is document_stage_tokens + sum(r.total_tokens for r in
    # requirement_records), computed by the caller -- not implied by a field name.
    @computed_field
    @property
    def document_stage_tokens(self) -> int:
        return sum(a.prompt_tokens + a.completion_tokens for a in self.attempts
                   if a.prompt_tokens is not None)
