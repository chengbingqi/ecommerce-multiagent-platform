from decimal import Decimal
from typing import Any, Literal, cast

from app.agents.operations.schemas import AnomalyView

ANOMALY_RULES_VERSION = "operations_rules_v1.0.0"

RULES = (
    ("OPS-STOCKOUT-001", "stockout_spike", "stockout_sku_count", ">= 1", Decimal("1"), "high"),
    ("OPS-REFUND-001", "refund_spike", "refund_rate", ">= 0.150000", Decimal("0.15"), "high"),
    (
        "OPS-LOGISTICS-001",
        "logistics_delay",
        "delayed_logistics_rate",
        ">= 0.100000",
        Decimal("0.10"),
        "medium",
    ),
    (
        "OPS-CONVERSION-001",
        "conversion_drop",
        "conversion_rate",
        "<= 0.010000",
        Decimal("0.01"),
        "high",
    ),
    ("OPS-CAMPAIGN-001", "live_campaign", "live_campaign_count", ">= 1", Decimal("1"), "low"),
)


def detect_anomalies(
    metrics: list[dict[str, Any]], scenario_counts: dict[str, int]
) -> list[AnomalyView]:
    by_key = {row["metric_key"]: row for row in metrics}
    results: list[AnomalyView] = []
    for rule_id, anomaly_type, metric_key, threshold_text, threshold, severity in RULES:
        metric = by_key[metric_key]
        observed = Decimal(str(metric["value"]))
        comparator = observed <= threshold if "<=" in threshold_text else observed >= threshold
        if not comparator or scenario_counts.get(anomaly_type, 0) <= 0:
            continue
        results.append(
            AnomalyView(
                anomaly_id=f"{rule_id}:{anomaly_type}",
                anomaly_type=anomaly_type,
                rule_id=rule_id,
                severity=cast(Literal["low", "medium", "high"], severity),
                observed_value=metric["value"],
                threshold=threshold_text,
                explanation=(
                    f"确定性规则{rule_id}命中：{metric['display_name']}观察值为"
                    f"{metric['value']}，阈值为{threshold_text}。"
                ),
                evidence_refs=metric["evidence_refs"],
            )
        )
    return results
