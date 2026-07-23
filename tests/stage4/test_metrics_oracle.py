from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import Store, ToolCallAudit
from mcp_servers.business.operations import METRICS_VERSION
from mcp_servers.business.schemas import OperationsMetricsInput
from mcp_servers.business.service import BusinessTools
from mcp_servers.common.contracts import CallContext
from tests.stage4.sql_oracle import metric_oracle

ORACLE_CASES = [
    (
        "ops_001_store1_full_day",
        0,
        datetime(2026, 6, 24, tzinfo=UTC),
        datetime(2026, 7, 1, tzinfo=UTC),
        "day",
    ),
    (
        "ops_002_store2_full_day",
        1,
        datetime(2026, 6, 24, tzinfo=UTC),
        datetime(2026, 7, 1, tzinfo=UTC),
        "day",
    ),
    (
        "ops_003_store1_single_day",
        0,
        datetime(2026, 6, 30, tzinfo=UTC),
        datetime(2026, 7, 1, tzinfo=UTC),
        "day",
    ),
    (
        "ops_004_store2_multi_week",
        1,
        datetime(2026, 6, 24, tzinfo=UTC),
        datetime(2026, 7, 1, tzinfo=UTC),
        "week",
    ),
    (
        "ops_005_empty_period",
        0,
        datetime(2026, 7, 10, tzinfo=UTC),
        datetime(2026, 7, 11, tzinfo=UTC),
        "day",
    ),
]


def context(case_id: str) -> CallContext:
    return CallContext(
        principal_id="admin-001",
        agent="operations",
        run_id=f"run-{case_id}",
        task_id=f"task-{case_id}",
        trace_id=f"trace-{case_id}",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_id", "store_index", "start_at", "end_at", "granularity"),
    ORACLE_CASES,
    ids=[row[0] for row in ORACLE_CASES],
)
async def test_operations_metrics_match_independent_sql_oracle(
    stage4_factory, case_id, store_index, start_at, end_at, granularity
) -> None:
    async with stage4_factory() as session:
        stores = (await session.scalars(select(Store).order_by(Store.scope_key))).all()
        expected = await metric_oracle(session, stores[store_index].id, start_at, end_at)
    result = await BusinessTools(stage4_factory).get_operations_metrics(
        OperationsMetricsInput(
            context=context(case_id),
            store_id=stores[store_index].id,
            start_at=start_at,
            end_at=end_at,
            granularity=granularity,
        )
    )
    assert result.ok
    assert result.meta.tool_version == "1.1.0"
    assert result.data["metrics_version"] == METRICS_VERSION
    actual = {row["metric_key"]: row["value"] for row in result.data["metrics"]}
    assert set(actual) == set(expected)
    for key, value in expected.items():
        assert actual[key] == value, f"{case_id}: {key}: expected={value}, actual={actual[key]}"
    assert all(point["granularity"] == granularity for point in result.data["trend_series"])
    refs = {item.evidence_ref for item in result.evidence}
    assert all(set(row["evidence_refs"]).issubset(refs) for row in result.data["metrics"])


@pytest.mark.asyncio
async def test_operations_tool_scope_audit_and_decimal_contract(stage4_factory) -> None:
    async with stage4_factory() as session:
        stores = (await session.scalars(select(Store).order_by(Store.scope_key))).all()
    denied = await BusinessTools(stage4_factory).get_operations_metrics(
        OperationsMetricsInput(
            context=CallContext(
                principal_id="operator-store-001",
                agent="operations",
                run_id="run-ops-006",
                task_id="task-ops-006",
                trace_id="trace-ops-006",
            ),
            store_id=stores[1].id,
            start_at=datetime(2026, 6, 24, tzinfo=UTC),
            end_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
    )
    assert not denied.ok and denied.error and denied.error.error_code == "FORBIDDEN"
    async with stage4_factory() as session:
        audit = await session.scalar(
            select(ToolCallAudit).where(ToolCallAudit.trace_id == "trace-ops-006")
        )
    assert audit and audit.permission_result == "allowed" and audit.error_code == "FORBIDDEN"
    assert "password" not in str(audit.input_summary).lower()
    assert Decimal("0") == Decimal("0.000000")
