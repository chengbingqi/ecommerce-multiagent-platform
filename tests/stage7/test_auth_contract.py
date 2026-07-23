from app.main import app


def test_auth_openapi_contract_exposes_only_safe_session_fields() -> None:
    schema = app.openapi()
    paths = schema["paths"]

    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/me" in paths
    assert "/api/v1/auth/csrf" in paths
    assert "/api/v1/auth/logout" in paths
    assert "/api/v1/auth/password" in paths
    assert paths["/api/v1/auth/login"]["post"]["requestBody"]["required"] is True
    serialized = str(schema)
    assert "password_hash" not in serialized
    assert "token_digest" not in serialized
    assert "csrf_digest" not in serialized
