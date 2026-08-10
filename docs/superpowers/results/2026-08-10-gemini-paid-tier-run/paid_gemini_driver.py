"""One-off driver: re-run the checklist against Gemini using the PAID-tier API key
(GEMINI_API_KEY_PAID), to see whether the free tier's absolute 20-request cap
(docs/superpowers/results/2026-08-10-first-real-run/ANALYSIS.md) was specific to the
free tier.

Deliberately NOT a change to the default adapter wiring: orchestrator/providers/
gemini.py's GeminiAdapter.from_env() still reads GEMINI_API_KEY only, unchanged, so
every other run (python -m orchestrator.cli, the original answer_policy_driver.py)
keeps using the free-tier key by default. This script constructs a GeminiAdapter
directly with GEMINI_API_KEY_PAID's value, for this one experiment only -- it does not
touch orchestrator/, design/, or the original driver.

Reuses the exact same AI answer policy as the original driver (loaded by file path,
not duplicated) -- same reasoning, same categories, same conservative defaults. Only
the Gemini adapter factory differs.

    python docs/superpowers/results/2026-08-10-gemini-paid-tier-run/paid_gemini_driver.py \
        orchestrator/runs_gemini.yaml /tmp/themas.json

Exit codes are whatever orchestrator.cli._run returns.
"""

from __future__ import annotations

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

from orchestrator.cli import _run  # noqa: E402
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


def main(argv: list[str]) -> int:
    return _run(
        ["run", *argv],
        adapter_factories={"gemini": _paid_gemini_adapter, "groq": GroqAdapter.from_env},
        human_fns_factory=_original_driver._human_fns_factory,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
