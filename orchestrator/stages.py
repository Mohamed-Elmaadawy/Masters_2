"""Real LLM-calling stage functions -- one factory per `StageFns` field.

Each `make_<stage>_fn(adapter, stage_config, prompt_template)` returns a closure whose
signature matches exactly the corresponding Protocol in `orchestrator/stage_fns.py`
(`CheckConsistencyFn`, `MapDependenciesFn`, `ClassifyFn`, `CheckQualityFn`,
`RefineQuestionerFn`, `RefineRewriterFn`, `SelectStrategyFn`, `GenerateTestsFn`). The
future CLI run entrypoint builds a `StageFns` by calling all eight factories once per
run, each with its own `ResolvedStageConfig` (provider/model/temperature/timeout/
output_mode, already resolved from the YAML `RunConfig`) and its own prompt file's text
(`ResolvedStageConfig.prompt_path.read_text()`).

`prompt_template` is the COMPLETE static prompt for that stage -- instructions,
untrusted-content delimiters, and the literal output-schema JSON, all written into the
`.txt` file directly (see `orchestrator/example_prompts/`). This module never appends
any additional static instruction text at runtime: `_render_prompt` only substitutes
already-present `<<<FIELD:name>>>` placeholders with dynamic per-call values. This is
deliberate, not an oversight -- see design/DESIGN_NOTES.md, "Real stage functions --
prompt provenance": anything appended here instead of living in the file would be sent
to the model but invisible to `prompt_fingerprint()`, breaking the guarantee that two
runs sharing a `prompt_hash` sent the same text.

Every dynamic value (`Requirement.text`, ids, lists derived from a stage's own output)
is treated as untrusted and run through `_json_dynamic` before substitution: JSON-
encoded, then `<`/`>` escaped as `\\u003c`/`\\u003e` so no dynamic value can reproduce or
close a static `<<<...>>>` delimiter marker. See `_render_prompt`/`_json_dynamic` below
and design/DESIGN_NOTES.md for the adversarial reasoning and tests.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from pydantic import BaseModel

from design.schemas import (
    Classification, ConsistencyReport, DependencyLink, DependencyReport, ELIGIBLE_TECHNIQUES,
    OutputMode, QualityReport, RefinedRequirement, RefinerAnswer, RefinerTurn, Requirement,
    RequirementSet, TestPlan, TestStrategy,
)
from orchestrator.config import ResolvedStageConfig
from orchestrator.pipeline import test_case_id_prefix
from orchestrator.providers.base import ProviderAdapter
from orchestrator.stage_fns import (
    CheckConsistencyFn, CheckQualityFn, ClassifyFn, GenerateTestsFn, MapDependenciesFn,
    RefineQuestionerFn, RefineRewriterFn, SelectStrategyFn, StageCallPartial, StageCallResult,
)

# ---------------------------------------------------------------------------------
# Placeholder substitution -- one pass over the ORIGINAL template only.
# ---------------------------------------------------------------------------------

# Every recognized structural marker besides `<<<FIELD:name>>>` -- written statically
# into every prompt file, never substituted. Kept here (not just "known by convention")
# so _render_prompt can catch a typo'd/malformed marker-shaped token in a template file
# that would otherwise silently sit there unrecognized, neither substituted nor erroring.
_FIXED_MARKERS = frozenset({
    "<<<UNTRUSTED_CONTENT_START>>>", "<<<UNTRUSTED_CONTENT_END>>>",
    "<<<OUTPUT_SCHEMA_START>>>", "<<<OUTPUT_SCHEMA_END>>>",
})
_ANY_MARKER_RE = re.compile(r"<<<[^<>]*>>>")
_PLACEHOLDER_RE = re.compile(r"<<<FIELD:([A-Za-z0-9_]+)>>>")


def _render_prompt(template: str, **fields: str) -> str:
    """One-pass placeholder substitution, validated against the ORIGINAL template only.

    Why not sequential str.replace() (rejected design): an untrusted dynamic value could
    itself contain text that looks like a placeholder token, and a later replace() call
    in the sequence would then process THAT text as if it were template syntax -- e.g.
    substituting requirement_text first, then substituting doc_id, would re-scan
    whatever requirement_text just inserted for a literal '<<<FIELD:doc_id>>>' string
    and wrongly substitute it. re.sub's single left-to-right pass over the ORIGINAL
    template string (the `for token in _ANY_MARKER_RE.findall(template)` scan below,
    and the final `_PLACEHOLDER_RE.sub` call) never re-scans inserted replacement text
    for further matches, by construction -- both walk `template`, not the growing
    output.

    Why validation reads from `template`, never from the rendered result: the rendered
    result can legitimately contain text that LOOKS like a placeholder or marker (an
    adversarial or just unlucky requirement whose text happens to contain
    '<<<OUTPUT_SCHEMA_START>>>', say -- see the adversarial tests in test_stages.py).
    Scanning the rendered text for "did every placeholder get resolved" would either
    wrongly flag that untrusted content as an unresolved placeholder, or -- worse --
    wrongly treat it as one to substitute on a second pass. Validating against the
    template and substituting in one pass over the template makes "left unresolved"
    impossible by construction: every name this function finds in `template` is looked
    up in `fields` before any substitution happens, so the single `_PLACEHOLDER_RE.sub`
    call below always has a value ready for every match it will ever make.

    Raises ValueError (never a silent no-op or an assert) for:
      - a marker-shaped '<<<...>>>' token in the template that is neither a recognized
        fixed marker nor a well-formed '<<<FIELD:name>>>' placeholder (a typo'd marker
        would otherwise sit there unrecognized, neither substituted nor erroring);
      - a placeholder in the template with no matching field supplied;
      - a field supplied that no placeholder in the template references.
    """
    for token in _ANY_MARKER_RE.findall(template):
        if token in _FIXED_MARKERS or _PLACEHOLDER_RE.fullmatch(token):
            continue
        raise ValueError(f"unrecognized marker-like token in template: {token!r}")

    placeholder_names = {m.group(1) for m in _PLACEHOLDER_RE.finditer(template)}

    missing = placeholder_names - set(fields)
    if missing:
        raise ValueError(
            f"template references placeholder(s) with no supplied value: {sorted(missing)}")

    unused = set(fields) - placeholder_names
    if unused:
        raise ValueError(
            f"field(s) supplied but not referenced by any placeholder in the template: "
            f"{sorted(unused)}")

    def _substitute(match: "re.Match[str]") -> str:
        return fields[match.group(1)]

    return _PLACEHOLDER_RE.sub(_substitute, template)


def _json_dynamic(value) -> str:
    """JSON-encodes any dynamic value before it goes into a prompt template, then
    escapes '<'/'>' as Unicode JSON escapes (\\u003c/\\u003e) so untrusted content can
    never reproduce or close a static '<<<...>>>' delimiter marker, even after
    JSON-quoting -- a requirement whose text literally contains
    '<<<UNTRUSTED_CONTENT_END>>>' renders as an escaped, unremarkable JSON string, not
    as a marker. `\\u003c`/`\\u003e` are valid JSON escapes for '<'/'>' -- a JSON
    parser (or a model reading the prompt) decodes them back to the original characters
    as ordinary string content; they just can never be mistaken for template syntax by
    anything that scans for a literal '<<<' triple.

    Applied uniformly to EVERY dynamic value substituted into a prompt, not just ones
    that look risky -- a field assumed "obviously safe" (a bare id, a short enum value)
    is exactly the assumption that stops being true the next time this helper is reused
    for a new field. See design/DESIGN_NOTES.md, "Real stage functions -- prompt
    provenance", for the adversarial tests this defends against.
    """
    encoded = json.dumps(value)
    return encoded.replace("<", "\\u003c").replace(">", "\\u003e")


_NOT_AVAILABLE = "(not available -- the document-level stage that would have produced this failed)"


def _render_optional_list(value: Optional[list], encode) -> str:
    """Renders contract item 16's None-vs-[] distinction as text: None means the
    document-level stage that would have produced this failed (no information at all,
    not "checked, found nothing"); [] means it ran and found nothing naming this
    requirement. Both go through _json_dynamic like any other dynamic value."""
    if value is None:
        return _json_dynamic(_NOT_AVAILABLE)
    return _json_dynamic([encode(v) for v in value])


# ---------------------------------------------------------------------------------
# Model response parsing.
# ---------------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)


def _extract_json(text: str, prompt_tokens: int, completion_tokens: int) -> dict:
    """Parses a model's raw text response into a dict. Every prompt asks for ONLY a
    single JSON object with no markdown/extra text, in every output mode -- but a real
    model may still wrap it in a ```json fence or add stray prose, so this tries, in
    order: the whole (fence-stripped) text as JSON, then the substring from the first
    '{' to the last '}'. If neither parses, raises StageCallPartial rather than
    StageCallFailed: the adapter already guarantees `text` is real model output with
    tokens genuinely spent (CompletionResult's own docstring -- a truly empty/unusable
    candidate is StageCallPartial/StageCallFailed at the adapter layer, before this
    function ever sees it), so "spent tokens, produced unusable output" is exactly
    StageCallPartial's documented case, not a new failure mode. See
    orchestrator/stage_fns.py's StageCallPartial docstring."""
    stripped = text.strip()
    fence_match = _FENCE_RE.match(stripped)
    candidate = fence_match.group(1).strip() if fence_match else stripped

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(candidate[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise StageCallPartial(
        f"model output could not be parsed as JSON: {text[:200]!r}",
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)


def _complete_and_parse(
    adapter: ProviderAdapter, stage_config: ResolvedStageConfig, model_cls: type[BaseModel],
    prompt: str,
) -> StageCallResult:
    """Shared by all eight stage fns: request a completion at the configured
    output_mode (only JSON_SCHEMA mode needs response_schema/schema_name -- TEXT/
    JSON_OBJECT send no schema, per orchestrator/providers/base.py's
    require_response_schema), parse the response text into a dict, return a
    StageCallResult. StageCallFailed/StageCallFatal/StageCallPartial from the adapter,
    or StageCallPartial from _extract_json, propagate unchanged -- call_stage/
    call_document_stage already know how to handle all three.

    provider=groq + output_mode=JSON_SCHEMA is rejected for v1, but not here --
    orchestrator/providers/capabilities.py's supports_output_mode() returns False for
    that combination unconditionally, so resolve_run_config() (orchestrator/config.py)
    already refuses such a YAML config before a run starts, and each adapter's own
    complete() checks the same function defensively before sending any request. A
    second rejection here would just duplicate that gate one layer further from the
    single source of truth. See design/DESIGN_NOTES.md, "Real stage functions --
    prompt provenance".
    """
    response_schema = (
        model_cls.model_json_schema() if stage_config.output_mode is OutputMode.JSON_SCHEMA
        else None)
    result = adapter.complete(
        prompt, model=stage_config.model, temperature=stage_config.temperature,
        timeout_seconds=stage_config.timeout_seconds, output_mode=stage_config.output_mode,
        response_schema=response_schema,
        schema_name=model_cls.__name__ if response_schema is not None else None,
    )
    data = _extract_json(result.text, result.prompt_tokens, result.completion_tokens)
    return StageCallResult(raw=data, prompt_tokens=result.prompt_tokens,
                           completion_tokens=result.completion_tokens)


def _requirements_json(requirement_set: RequirementSet) -> str:
    return _json_dynamic([{"id": r.id, "text": r.text} for r in requirement_set.requirements])


def _doc_id_field(requirement_set: RequirementSet) -> str:
    return _json_dynamic(requirement_set.doc_id if requirement_set.doc_id is not None
                         else "(not provided)")


# ---------------------------------------------------------------------------------
# 1. Consistency Checker
# ---------------------------------------------------------------------------------

def make_check_consistency_fn(
    adapter: ProviderAdapter, stage_config: ResolvedStageConfig, prompt_template: str,
) -> CheckConsistencyFn:
    def check_consistency(requirement_set: RequirementSet) -> StageCallResult:
        prompt = _render_prompt(
            prompt_template,
            doc_id=_doc_id_field(requirement_set),
            requirements_json=_requirements_json(requirement_set),
        )
        return _complete_and_parse(adapter, stage_config, ConsistencyReport, prompt)
    return check_consistency


# ---------------------------------------------------------------------------------
# 1b. Dependency Mapper
# ---------------------------------------------------------------------------------

def make_map_dependencies_fn(
    adapter: ProviderAdapter, stage_config: ResolvedStageConfig, prompt_template: str,
) -> MapDependenciesFn:
    def map_dependencies(requirement_set: RequirementSet) -> StageCallResult:
        prompt = _render_prompt(
            prompt_template,
            doc_id=_doc_id_field(requirement_set),
            requirements_json=_requirements_json(requirement_set),
        )
        return _complete_and_parse(adapter, stage_config, DependencyReport, prompt)
    return map_dependencies


# ---------------------------------------------------------------------------------
# 2a. Classifier
# ---------------------------------------------------------------------------------

def make_classify_fn(
    adapter: ProviderAdapter, stage_config: ResolvedStageConfig, prompt_template: str,
) -> ClassifyFn:
    def classify(requirement: Requirement, requirement_set: RequirementSet) -> StageCallResult:
        prompt = _render_prompt(
            prompt_template,
            requirement_id=_json_dynamic(requirement.id),
            requirement_text=_json_dynamic(requirement.text),
            requirements_json=_requirements_json(requirement_set),
        )
        return _complete_and_parse(adapter, stage_config, Classification, prompt)
    return classify


# ---------------------------------------------------------------------------------
# 2b. Quality Checker
# ---------------------------------------------------------------------------------

def make_check_quality_fn(
    adapter: ProviderAdapter, stage_config: ResolvedStageConfig, prompt_template: str,
) -> CheckQualityFn:
    def check_quality(
        requirement: Requirement, classification: Classification,
        relevant_conflicts: Optional[list], relevant_dependencies: Optional[list],
        suppressed_issue_ids: list[str],
    ) -> StageCallResult:
        prompt = _render_prompt(
            prompt_template,
            requirement_id=_json_dynamic(requirement.id),
            requirement_text=_json_dynamic(requirement.text),
            system_type=_json_dynamic(classification.system_type.value),
            conflicts_json=_render_optional_list(
                relevant_conflicts,
                lambda c: {"requirement_ids": c.requirement_ids, "explanation": c.explanation}),
            dependencies_json=_render_optional_list(
                relevant_dependencies,
                lambda d: {"from_requirement_id": d.from_requirement_id,
                          "to_requirement_id": d.to_requirement_id, "explanation": d.explanation}),
            suppressed_issue_ids_json=_json_dynamic(suppressed_issue_ids),
        )
        return _complete_and_parse(adapter, stage_config, QualityReport, prompt)
    return check_quality


# ---------------------------------------------------------------------------------
# 2c. Refiner Questioner / Refiner Rewriter
# ---------------------------------------------------------------------------------

def make_refine_questioner_fn(
    adapter: ProviderAdapter, stage_config: ResolvedStageConfig, prompt_template: str,
) -> RefineQuestionerFn:
    def refine_questioner(
        requirement: Requirement, quality_report: QualityReport, revision_number: int,
    ) -> StageCallResult:
        issues_json = _json_dynamic([
            {"id": i.id, "category": i.category.value, "span": i.span, "explanation": i.explanation,
             "related_requirement_ids": i.related_requirement_ids}
            for i in quality_report.issues
        ])
        prompt = _render_prompt(
            prompt_template,
            requirement_id=_json_dynamic(requirement.id),
            requirement_text=_json_dynamic(requirement.text),
            revision_number=_json_dynamic(revision_number),
            issues_json=issues_json,
        )
        return _complete_and_parse(adapter, stage_config, RefinerTurn, prompt)
    return refine_questioner


def make_refine_rewriter_fn(
    adapter: ProviderAdapter, stage_config: ResolvedStageConfig, prompt_template: str,
) -> RefineRewriterFn:
    def refine_rewriter(
        requirement: Requirement, answers: list[RefinerAnswer], revision_number: int,
    ) -> StageCallResult:
        answers_json = _json_dynamic([
            {"question_id": a.question_id, "answer_text": a.answer_text,
             "user_confirms_resolved": a.user_confirms_resolved} for a in answers
        ])
        prompt = _render_prompt(
            prompt_template,
            requirement_id=_json_dynamic(requirement.id),
            requirement_text=_json_dynamic(requirement.text),
            revision_number=_json_dynamic(revision_number),
            answers_json=answers_json,
        )
        return _complete_and_parse(adapter, stage_config, RefinedRequirement, prompt)
    return refine_rewriter


# ---------------------------------------------------------------------------------
# 3. Test Design Strategy Selector
# ---------------------------------------------------------------------------------

def make_select_strategy_fn(
    adapter: ProviderAdapter, stage_config: ResolvedStageConfig, prompt_template: str,
) -> SelectStrategyFn:
    def select_strategy(
        requirement: Requirement, classification: Classification,
        relevant_dependencies: Optional[list[DependencyLink]],
    ) -> StageCallResult:
        eligible = sorted(t.value for t in ELIGIBLE_TECHNIQUES[classification.system_type])
        prompt = _render_prompt(
            prompt_template,
            requirement_id=_json_dynamic(requirement.id),
            requirement_text=_json_dynamic(requirement.text),
            system_type=_json_dynamic(classification.system_type.value),
            eligible_techniques_json=_json_dynamic(eligible),
            dependencies_json=_render_optional_list(
                relevant_dependencies,
                lambda d: {"from_requirement_id": d.from_requirement_id,
                          "to_requirement_id": d.to_requirement_id, "explanation": d.explanation}),
        )
        return _complete_and_parse(adapter, stage_config, TestStrategy, prompt)
    return select_strategy


# ---------------------------------------------------------------------------------
# 4. Test Case Generator
# ---------------------------------------------------------------------------------

def make_generate_tests_fn(
    adapter: ProviderAdapter, stage_config: ResolvedStageConfig, prompt_template: str,
) -> GenerateTestsFn:
    def generate_tests(
        requirement: Requirement, strategy: TestStrategy,
        relevant_dependencies: Optional[list[DependencyLink]],
    ) -> StageCallResult:
        prompt = _render_prompt(
            prompt_template,
            requirement_id=_json_dynamic(requirement.id),
            requirement_text=_json_dynamic(requirement.text),
            test_case_id_prefix=_json_dynamic(test_case_id_prefix(requirement.id)),
            allowed_techniques_json=_json_dynamic([t.value for t in strategy.techniques]),
            strategy_rationale=_json_dynamic(strategy.rationale),
            dependencies_json=_render_optional_list(
                relevant_dependencies,
                lambda d: {"from_requirement_id": d.from_requirement_id,
                          "to_requirement_id": d.to_requirement_id, "explanation": d.explanation}),
        )
        return _complete_and_parse(adapter, stage_config, TestPlan, prompt)
    return generate_tests
