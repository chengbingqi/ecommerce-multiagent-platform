from datetime import date

import pytest
from pydantic import ValidationError

from app.agents.operations.schemas import OperationsRunRequest
from app.core.config import Settings
from app.main import app


def test_operations_openapi_and_request_contract() -> None:
    schema = app.openapi()
    request_body = schema["paths"]["/api/v1/runs"]["post"]["requestBody"]
    assert "application/json" in request_body["content"]
    request = OperationsRunRequest(
        agent_type="operations",
        store_id="store-001",
        start_date=date(2026, 6, 24),
        end_date=date(2026, 7, 1),
        topic="OVERVIEW",
        granularity="day",
        comparison_mode="none",
        principal_id="agent-operations",
        run_id="run-operations-001",
        thread_id="thread-operations-001",
        trace_id="trace-operations-001",
    )
    assert request.agent_type == "operations"
    with pytest.raises(ValidationError, match="start_date"):
        OperationsRunRequest(**{**request.model_dump(), "end_date": date(2026, 6, 24)})


def test_production_principal_gate_remains_closed() -> None:
    with pytest.raises(ValidationError, match="development principal input"):
        Settings(
            app_env="production",
            database_url="postgresql+asyncpg://example.invalid/db",
            redis_url="redis://example.invalid/0",
            agent_llm_mode="deepseek",
            enable_dev_principal_input=True,
        )
