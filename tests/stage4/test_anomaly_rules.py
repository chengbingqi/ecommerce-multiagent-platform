from app.agents.operations.anomaly_rules import ANOMALY_RULES_VERSION, detect_anomalies


def metric(key: str, value: int | str) -> dict:
    return {
        "metric_key": key,
        "display_name": key,
        "value": value,
        "evidence_refs": [f"db://test/{key}"],
    }


BASE = [
    metric("stockout_sku_count", 0),
    metric("refund_rate", "0.000000"),
    metric("delayed_logistics_rate", "0.000000"),
    metric("conversion_rate", "0.020000"),
    metric("live_campaign_count", 0),
]


def test_all_five_rules_positive_negative_and_boundary() -> None:
    positive = [
        metric("stockout_sku_count", 1),
        metric("refund_rate", "0.150000"),
        metric("delayed_logistics_rate", "0.100000"),
        metric("conversion_rate", "0.010000"),
        metric("live_campaign_count", 1),
    ]
    scenarios = {
        name: 1
        for name in (
            "stockout_spike",
            "refund_spike",
            "logistics_delay",
            "conversion_drop",
            "live_campaign",
        )
    }
    anomalies = detect_anomalies(positive, scenarios)
    assert len(anomalies) == 5
    assert {row.anomaly_type for row in anomalies} == set(scenarios)
    assert all(row.rule_id and row.threshold and row.evidence_refs for row in anomalies)
    assert detect_anomalies(BASE, scenarios) == []
    assert ANOMALY_RULES_VERSION == "operations_rules_v1.0.0"
