import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from tests.stage4.sql_oracle import metric_oracle

from app.db.models import Store
from app.db.session import get_sessionmaker
from mcp_servers.business.schemas import OperationsMetricsInput
from mcp_servers.business.service import BusinessTools
from mcp_servers.common.contracts import CallContext

CASES = (
    (0, datetime(2026, 6, 24, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC), "day"),
    (1, datetime(2026, 6, 24, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC), "day"),
    (0, datetime(2026, 6, 30, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC), "day"),
    (1, datetime(2026, 6, 24, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC), "week"),
    (0, datetime(2026, 7, 10, tzinfo=UTC), datetime(2026, 7, 11, tzinfo=UTC), "day"),
)


def anomaly_oracle(metrics: list[dict], scenarios: dict[str, int]) -> set[str]:
    values = {row["metric_key"]: Decimal(str(row["value"])) for row in metrics}
    checks = {
        "stockout_spike": values["stockout_sku_count"] >= 1,
        "refund_spike": values["refund_rate"] >= Decimal("0.15"),
        "logistics_delay": values["delayed_logistics_rate"] >= Decimal("0.10"),
        "conversion_drop": values["conversion_rate"] <= Decimal("0.01"),
        "live_campaign": values["live_campaign_count"] >= 1,
    }
    return {name for name, matched in checks.items() if matched and scenarios.get(name, 0) > 0}


async def main() -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        stores = (await session.scalars(select(Store).order_by(Store.scope_key))).all()
    compared = 0
    anomalies: set[str] = set()
    for index, start_at, end_at, granularity in CASES:
        async with factory() as session:
            expected = await metric_oracle(session, stores[index].id, start_at, end_at)
        result = await BusinessTools(factory).get_operations_metrics(
            OperationsMetricsInput(
                context=CallContext(
                    principal_id="admin-001",
                    agent="operations",
                    run_id=f"isolated-{compared}",
                    task_id=f"oracle-{compared}",
                    trace_id=f"oracle-{compared}",
                ),
                store_id=stores[index].id,
                start_at=start_at,
                end_at=end_at,
                granularity=granularity,
            )
        )
        if not result.ok:
            raise RuntimeError("operations metric tool failed")
        actual = {row["metric_key"]: row["value"] for row in result.data["metrics"]}
        for key, value in expected.items():
            if actual[key] != value:
                raise AssertionError(
                    f"metric mismatch: key={key}, store={stores[index].id}, "
                    f"period={start_at.isoformat()}..{end_at.isoformat()}, "
                    f"expected={value}, actual={actual[key]}"
                )
            compared += 1
        anomalies.update(anomaly_oracle(result.data["metrics"], result.data["scenario_counts"]))
    expected_anomalies = {
        "stockout_spike",
        "refund_spike",
        "logistics_delay",
        "conversion_drop",
        "live_campaign",
    }
    if anomalies != expected_anomalies:
        raise AssertionError(f"anomaly mismatch: {sorted(anomalies)}")
    print(
        json.dumps(
            {
                "database": "ecommerce_agent_stage4_acceptance",
                "compared_fields": compared,
                "passed_fields": compared,
                "consistency_rate": "100%",
                "anomalies": sorted(anomalies),
                "anomaly_recall": "100%",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
