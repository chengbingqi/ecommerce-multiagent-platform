import json
import os

import pytest

from app.agents.operations.provider import DeepSeekOperationsLLM
from app.core.config import get_settings


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_controlled_operations_explanation_and_evidence() -> None:
    if os.getenv("RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("set RUN_LIVE_LLM_TESTS=1 for the controlled operations smoke")
    settings = get_settings()
    if not settings.deepseek_api_key.get_secret_value():
        pytest.fail("DEEPSEEK_API_KEY is not configured")
    provider = DeepSeekOperationsLLM(
        settings.model_copy(
            update={
                "llm_temperature": 0.1,
                "llm_max_tokens": min(settings.llm_max_tokens, 512),
                "llm_max_retries": 0,
                "llm_max_calls_per_run": 2,
            }
        )
    )
    metric_values = {"gmv": "4354.20", "stockout_sku_count": 2}
    draft = await provider.generate(
        {
            "metric_values": metric_values,
            "anomalies": [
                {
                    "anomaly_id": "OPS-STOCKOUT-001:stockout_spike",
                    "anomaly_type": "stockout_spike",
                    "rule_id": "OPS-STOCKOUT-001",
                    "severity": "high",
                    "observed_value": 2,
                    "threshold": ">= 1",
                    "explanation": "确定性缺货规则命中。",
                    "evidence_refs": ["db://inventories/simulated-001"],
                }
            ],
            "sop": {
                "name": "模拟运营复核SOP",
                "steps": [{"order": 1, "action": "复核模拟库存证据"}],
                "evidence_ref": "sop://simulated/v1",
            },
        }
    )
    assert draft.metric_echo == metric_values
    assert draft.explanation
    supplied_refs = {"db://inventories/simulated-001", "sop://simulated/v1"}
    assert all(set(row.evidence_refs).issubset(supplied_refs) for row in draft.recommendations)
    assert provider.call_count <= 2
    assert provider.usage
    print(
        json.dumps(
            {
                "calls": provider.call_count,
                "concurrency": 1,
                "model": provider.usage[0].model,
                "latency_ms": provider.usage[0].latency_ms,
                "total_tokens": provider.usage[0].total_tokens,
            },
            ensure_ascii=False,
        )
    )
