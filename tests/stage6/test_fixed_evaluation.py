import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "evals/stage6/orchestrator_cases_v1.json"
RUNNER = ROOT / "evals/stage6/run_orchestrator_eval.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("stage6_eval_runner", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_manifest_has_32_stable_unique_cases_and_required_coverage() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases = manifest["cases"]
    assert len(cases) == 32
    assert len({case["case_id"] for case in cases}) == 32
    categories = {case["category"] for case in cases}
    assert {
        "routing",
        "missing_input",
        "authorization",
        "security",
        "limits",
        "provider_failure",
        "retry",
        "partial",
        "recovery",
        "cancellation",
        "grounding",
    }.issubset(categories)


@pytest.mark.asyncio
async def test_fixed_evaluation_meets_stage6_thresholds() -> None:
    runner = load_runner()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    router = runner.DeterministicOrchestratorRouter()
    results = [await runner.evaluate(case, router) for case in manifest["cases"]]
    summary = runner.metrics(results, manifest["cases"])
    assert summary["intent_routing_accuracy_percent"] >= 90
    assert summary["executable_plan_rate_percent"] >= 85
    assert summary["unauthorized_tool_block_rate_percent"] == 100
    assert summary["traceability_rate_percent"] == 100
    assert summary["uncontrolled_loop_count"] == 0
    assert summary["critical_recovery_passed"] is True
    assert summary["partial_success_preserved"] is True
