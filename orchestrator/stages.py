"""Real LLM-calling stage functions -- NOT built yet. This is the next phase.

orchestrator/test_harness.py is green (see its own header for the current check count)
and everything it depends on is built: orchestrator/stage_fns.py's ten Protocols say
exactly what each function here must accept and return; orchestrator/config.py resolves
a YAML RunConfig into per-stage provider/model/temperature/output_mode/prompt_hash;
orchestrator/providers/{gemini,groq}.py's GeminiAdapter/GroqAdapter.complete() make the
actual HTTP calls, already classifying failures into StageCallFailed/StageCallFatal/
StageCallPartial. None of that is "next phase" anymore -- only what's described below is.

What's left, for each of the eight stages named in design/schemas.py's ALL_STAGES
(check_consistency, map_dependencies, classify, check_quality, refine_questioner,
refine_rewriter, select_strategy, generate_tests -- eight independently configured LLM
calls, not seven: the Refiner is one conceptual step in the pipeline's prose but two of
these eight, each with its own model/prompt/config):

1. Write the actual prompt text (currently a placeholder per stage under
   orchestrator/example_prompts/, referenced -- and hashed for real -- from
   orchestrator/example_run_config.yaml).
2. A function matching the corresponding Protocol in orchestrator/stage_fns.py
   (CheckConsistencyFn, MapDependenciesFn, ClassifyFn, CheckQualityFn,
   RefineQuestionerFn, RefineRewriterFn, SelectStrategyFn, GenerateTestsFn) that: builds
   the prompt from its arguments, calls a ProviderAdapter.complete() (requesting
   OutputMode.JSON_SCHEMA with that stage's model_cls.model_json_schema() where the
   resolved config's capability table allows it, JSON_OBJECT or TEXT-plus-manual-parsing
   otherwise), and returns StageCallResult(raw=<parsed dict>, prompt_tokens=...,
   completion_tokens=...) -- or lets a StageCallFailed/StageCallFatal/StageCallPartial
   from the adapter propagate unchanged; call_stage/call_document_stage already know how
   to handle all three.

Also still open, deliberately not started here: the CLI run entrypoint that reads a
RunConfig, resolves it, builds a StageFns from the eight functions above and a HumanFns
from orchestrator/human_cli.py, and calls orchestrator.pipeline.run_document. See
design/DESIGN_NOTES.md's "Run config, provider adapters, CLI HumanFns" section and
design/ORCHESTRATOR_CONTRACT.md for what every piece here must and must not do.
"""
