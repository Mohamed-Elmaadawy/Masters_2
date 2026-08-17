"""Task 6 (E2 baseline arms) offline machinery -- B1/B2 prompts and runner, the Part A
mechanical checks, and the blinding tool (docs/EVALUATION_PROTOCOL.md sections 3, 6.1).

Deliberately separate from orchestrator/ and design/: nothing here is a pipeline stage,
a pipeline schema, or production prompt text. See this package's modules for the reason
each type is its own, not a reuse of a pipeline-scoped one.
"""
