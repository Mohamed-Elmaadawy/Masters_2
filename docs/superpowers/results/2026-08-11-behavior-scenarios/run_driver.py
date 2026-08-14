"""Driver for the 2026-08-11 behavior scenarios suite.

Reuses two things by import, not by copy:

- The PAID-key Gemini adapter construction pattern from
  docs/superpowers/results/2026-08-10-gemini-paid-tier-run/paid_gemini_driver.py:
  constructs GeminiAdapter directly from GEMINI_API_KEY_PAID and raises rather than
  falling back to GEMINI_API_KEY. The free tier has a measured 20-request/day cap and
  this suite is 172-300 requests.
- The scripted human answer policy from
  docs/superpowers/results/2026-08-10-first-real-run/answer_policy_driver.py: one
  reasoned answer per IssueCategory, applied unchanged. A second, different policy
  would make these results incomparable with the 2026-08-10 runs.

S11 is the one scenario needing a different cap decision between its two runs
(scn-11a: generate: scn-11b: stop) -- only the decide_at_cap branch changes; every
per-IssueCategory answer is the shared policy, unchanged.

Does not modify orchestrator/, design/, or the original driver. Drives everything
through orchestrator.cli._run's adapter_factories/human_fns_factory parameters, the
same seam orchestrator/test_cli.py and the existing drivers already use.

    python docs/superpowers/results/2026-08-11-behavior-scenarios/run_driver.py \
        CONFIG.yaml INPUT.json [--cap-decision generate|stop]

Exit codes are whatever orchestrator.cli._run returns (see its own docstring).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO_ROOT))

_ORIGINAL_DRIVER_PATH = (
    _REPO_ROOT / "docs" / "superpowers" / "results" / "2026-08-10-first-real-run"
    / "answer_policy_driver.py"
)
_spec = importlib.util.spec_from_file_location("answer_policy_driver", _ORIGINAL_DRIVER_PATH)
_original_driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_original_driver)

from design.schemas import RequirementRunRecord, RunOutcome  # noqa: E402
from orchestrator.cli import _run  # noqa: E402
from orchestrator.pipeline import HumanFns  # noqa: E402
from orchestrator.providers.base import ProviderAdapter  # noqa: E402
from orchestrator.providers.gemini import GeminiAdapter  # noqa: E402
from orchestrator.providers.groq import GroqAdapter  # noqa: E402


def _paid_gemini_adapter() -> ProviderAdapter:
    key = os.environ.get("GEMINI_API_KEY_PAID")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY_PAID is not set -- this driver is opt-in only, by design; "
            "it never falls back to GEMINI_API_KEY")
    return GeminiAdapter(api_key=key)


def _decide_at_cap_generate(record: RequirementRunRecord) -> tuple[RunOutcome, str]:
    # S11a only: the one deliberate deviation from the shared answer policy's
    # decide_at_cap_policy (which always stops). Every per-IssueCategory answer this
    # run gives is still the shared policy, unchanged.
    print(f"[policy] revision cap reached for {record.requirement.id} -- generating "
          "(scn-11a: human decision scripted to generate at the cap)")
    return (
        RunOutcome.CAP_GENERATED,
        "S11a: human decision scripted to generate tests from best-effort text at the "
        "revision cap, to exercise the CAP_GENERATED branch. Contrast with scn-11b, "
        "same fixture, human decision scripted to stop instead.",
    )


def _human_fns_factory_generate() -> HumanFns:
    return HumanFns(
        answer_questions=_original_driver.answer_questions_policy,
        decide_at_cap=_decide_at_cap_generate,
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("input")
    parser.add_argument("--cap-decision", choices=["generate", "stop"], default="stop",
                         help="S11 only: which decide_at_cap branch to use. Every other "
                              "scenario uses the shared stop-by-default policy regardless "
                              "of this flag, since the cap is not expected to fire.")
    args = parser.parse_args(argv)

    human_fns_factory = (
        _human_fns_factory_generate if args.cap_decision == "generate"
        else _original_driver._human_fns_factory
    )

    return _run(
        ["run", args.config, args.input],
        adapter_factories={"gemini": _paid_gemini_adapter, "groq": GroqAdapter.from_env},
        human_fns_factory=human_fns_factory,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
