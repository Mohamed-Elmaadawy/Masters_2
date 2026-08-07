"""Orchestrator control flow: stage sequencing, resume, retry, revision cap.

Every LLM call and human-interaction point is a parameter (StageFns, HumanFns), not a
hardcoded call -- see docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md.
orchestrator/test_harness.py wires in fixtures; orchestrator/stages.py (next phase)
wires in real ones. No control-flow logic is built twice.
"""

from __future__ import annotations

from typing import Optional

from design.schemas import PipelineStage, RequirementRunRecord


def resume_at(rec: RequirementRunRecord) -> Optional[PipelineStage]:
    """Where an interrupted or errored requirement record should resume.

    Moved here from design/test_schemas.py, where it lived "so it stays honest"
    (design/ORCHESTRATOR_CONTRACT.md item 6) because there was nowhere else for it --
    the schema deliberately does not encode pipeline ordering. Now that the orchestrator
    exists, this is the real implementation the orchestrator calls, not a copy kept in
    sync with one. Its test moved with it, to orchestrator/test_harness.py.
    """
    if rec.classification is None:
        return PipelineStage.CLASSIFIER
    if not rec.rounds:
        return PipelineStage.QUALITY_CHECKER
    last = rec.rounds[-1]
    if not last.quality_report.passed:
        # A round whose check failed but which already rewrote has finished refining;
        # the next step is checking that rewrite, i.e. the next round.
        return PipelineStage.REFINER if last.rewrite is None else PipelineStage.QUALITY_CHECKER
    if rec.test_strategy is None:
        return PipelineStage.STRATEGY_SELECTOR
    if rec.test_plan is None:
        return PipelineStage.TEST_GENERATOR
    return None
