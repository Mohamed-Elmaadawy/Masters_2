"""Real LLM-calling stage functions -- NOT built this phase.

Every function here must eventually return an orchestrator.pipeline.StageCallResult,
matching the signature its StageFns/HumanFns field expects. Wiring these up is the
next phase, once orchestrator/test_harness.py is green against fakes.
"""
