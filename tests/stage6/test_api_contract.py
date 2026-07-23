from app.main import app


def test_stage6_openapi_contract_contains_orchestrator_recovery_endpoints() -> None:
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/runs" in paths
    assert "/api/v1/runs/{run_id}" in paths
    assert "/api/v1/runs/{run_id}/events" in paths
    assert "/api/v1/runs/{run_id}/events/stream" in paths
    assert "/api/v1/runs/{run_id}/cancel" in paths
    assert "/api/v1/approvals/{approval_id}/decision" in paths
    request_schema = paths["/api/v1/runs"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    assert "OrchestratorRunRequest" in str(request_schema)
