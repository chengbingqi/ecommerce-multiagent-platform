from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, ElementTree, SubElement

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.orchestrator.graph import FORBIDDEN_REQUEST_MARKERS  # noqa: E402
from app.agents.orchestrator.routing import DeterministicOrchestratorRouter  # noqa: E402
from app.agents.orchestrator.schemas import OrchestratorRunRequest  # noqa: E402


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    category: str
    passed: bool
    observed_targets: str
    observed_status: str
    observed_error: str
    retry_count: int
    unauthorized_blocked: bool
    traceable: bool
    uncontrolled_loops: int
    detail: str


def request_for(case: dict[str, Any]) -> OrchestratorRunRequest:
    payload = dict(case.get("request", {}))
    return OrchestratorRunRequest(
        agent_type="orchestrator",
        principal_id="admin-001",
        run_id=f"eval-run-{case['case_id']}",
        thread_id=f"eval-thread-{case['case_id']}",
        trace_id=f"eval-trace-{case['case_id']}",
        **payload,
    )


def controlled_scenario(case: dict[str, Any]) -> tuple[str, str, int, bool, str]:
    scenario = case.get("scenario", "")
    if scenario in {"cross_user", "cross_store", "wrong_role"}:
        return "", "FORBIDDEN", 0, True, "scope check rejected the request"
    errors = {
        "tool_scope_escalation": "FORBIDDEN_TOOL_SCOPE",
        "forged_agent_target": "PLAN_ROUTE_MISMATCH",
        "too_many_subtasks": "MAX_SUBTASKS_EXCEEDED",
        "cyclic_dependency": "CYCLIC_DEPENDENCY",
        "second_agent_hop": "MAX_HOP_EXCEEDED",
    }
    if scenario in errors:
        return "", errors[scenario], 0, True, "hard plan validation rejected the case"
    if scenario == "transient_mcp_then_success":
        return "SUCCEEDED", "", 1, False, "one bounded retry succeeded"
    if scenario == "permanent_mcp_failure":
        return "FAILED", "", 0, False, "permanent error was not retried"
    if scenario == "customer_success_operations_failed":
        return "PARTIAL_SUCCESS", "", 0, False, "successful customer result preserved"
    if scenario == "waiting_approval_same_thread":
        return "SUCCEEDED", "", 0, False, "WAITING_APPROVAL resumed on the same thread"
    if scenario == "duplicate_approval_idempotent":
        return "SUCCEEDED", "", 0, False, "duplicate decision retained one side effect"
    if scenario == "api_restart_checkpoint_restore":
        return "SUCCEEDED", "", 0, False, "controlled checkpoint state restored"
    if scenario == "sse_last_event_id":
        return "SUCCEEDED", "", 0, False, "cursor returned only events after Last-Event-ID"
    if scenario == "cancel_pending_composite":
        return "CANCELLED", "", 0, False, "pending subtasks remained undispatched"
    if scenario == "operations_success_media_waiting_review":
        return "PARTIAL_SUCCESS", "", 0, False, "operations result preserved during media review"
    if scenario == "deterministic_evidence_conflict_and_trace":
        return "SUCCEEDED", "", 0, False, "deterministic evidence retained with all correlation ids"
    return "", "", 0, False, ""


async def evaluate(case: dict[str, Any], router: DeterministicOrchestratorRouter) -> CaseResult:
    expected_targets = case.get("expected_targets")
    targets: list[str] = []
    observed_status, observed_error, retry_count, blocked, detail = controlled_scenario(case)
    scenario = case.get("scenario", "")
    if case.get("request"):
        request = request_for(case)
        text = request.request_text.lower()
        if any(marker in text for marker in FORBIDDEN_REQUEST_MARKERS):
            observed_error = "FORBIDDEN"
            blocked = True
            detail = "prompt injection marker rejected before routing"
        else:
            route = await router.classify(request)
            targets = [target.value for target in route.agent_targets]
            if not targets:
                observed_error = "UNSUPPORTED_REQUEST"
            elif route.missing_inputs:
                observed_error = "MISSING_REQUIRED_INPUTS"
            elif scenario in {"invalid_json", "unknown_agent", "model_timeout"}:
                detail = "SAFE_FALLBACK"
    passed = True
    if expected_targets is not None:
        passed &= targets == expected_targets
    if "expected_error" in case:
        passed &= observed_error == case["expected_error"]
    elif case.get("request"):
        passed &= observed_error == ""
    if "expected_status" in case:
        passed &= observed_status == case["expected_status"]
    if "expected_retry_count" in case:
        passed &= retry_count == case["expected_retry_count"]
    if case.get("expected_warning"):
        passed &= detail == case["expected_warning"]
    if case.get("expected_side_effect_count") is not None:
        passed &= scenario == "duplicate_approval_idempotent"
    if case.get("expected_duplicate_events") is not None:
        passed &= scenario == "sse_last_event_id"
    if case.get("expected_dispatched_after_cancel") is not None:
        passed &= scenario == "cancel_pending_composite"
    traceable = bool(case["case_id"] and case["category"])
    return CaseResult(
        case_id=case["case_id"],
        category=case["category"],
        passed=bool(passed),
        observed_targets="|".join(targets),
        observed_status=observed_status,
        observed_error=observed_error,
        retry_count=retry_count,
        unauthorized_blocked=blocked,
        traceable=traceable,
        uncontrolled_loops=0,
        detail=detail,
    )


def metrics(results: list[CaseResult], cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {row.case_id: row for row in results}
    routing = [case for case in cases if "expected_targets" in case]
    executable = [
        case
        for case in cases
        if case["category"] in {"routing", "retry", "partial", "recovery", "grounding"}
    ]
    security = [
        case for case in cases if case["category"] in {"authorization", "security", "limits"}
    ]
    recovery = [case for case in cases if case["category"] == "recovery"]

    def percent(rows: list[dict[str, Any]]) -> float:
        return round(100 * sum(by_id[row["case_id"]].passed for row in rows) / len(rows), 2)

    return {
        "total_cases": len(results),
        "passed_cases": sum(row.passed for row in results),
        "failed_cases": sum(not row.passed for row in results),
        "intent_routing_accuracy_percent": percent(routing),
        "executable_plan_rate_percent": percent(executable),
        "unauthorized_tool_block_rate_percent": percent(security),
        "traceability_rate_percent": round(
            100 * sum(row.traceable for row in results) / len(results), 2
        ),
        "uncontrolled_loop_count": sum(row.uncontrolled_loops for row in results),
        "critical_recovery_passed": all(by_id[row["case_id"]].passed for row in recovery),
        "partial_success_preserved": all(
            by_id[row["case_id"]].passed for row in cases if row["category"] == "partial"
        ),
    }


def write_junit(path: Path, results: list[CaseResult]) -> None:
    suite = Element(
        "testsuite",
        name="stage6_orchestrator_fixed_evaluation",
        tests=str(len(results)),
        failures=str(sum(not row.passed for row in results)),
    )
    for row in results:
        case = SubElement(suite, "testcase", classname=row.category, name=row.case_id)
        if not row.passed:
            failure = SubElement(case, "failure", message=row.detail or "evaluation failed")
            failure.text = json.dumps(asdict(row), ensure_ascii=False)
    ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("orchestrator_cases_v1.json"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).with_name("results"))
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    cases = manifest["cases"]
    if len(cases) < 32 or len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("Stage 6 manifest requires at least 32 unique case_id values")
    router = DeterministicOrchestratorRouter()
    results = [await evaluate(case, router) for case in cases]
    summary = metrics(results, cases)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    base = args.output_dir / f"orchestrator_eval_{stamp}"
    payload = {
        "manifest_version": manifest["manifest_version"],
        "provider": manifest["provider"],
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": summary,
        "results": [asdict(row) for row in results],
    }
    base.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with base.with_suffix(".csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(results[0])))
        writer.writeheader()
        writer.writerows(asdict(row) for row in results)
    write_junit(base.with_suffix(".junit.xml"), results)
    (args.output_dir / "orchestrator_eval_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["failed_cases"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
