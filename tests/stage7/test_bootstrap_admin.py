from __future__ import annotations

import pytest

from app.security.password_policy import validate_admin_password


def test_admin_password_policy_accepts_long_three_category_password() -> None:
    # secret-scan: allow-test-fixture
    password = "LocalAdmin" + "9" + "!" + "secure"
    assert validate_admin_password(password) == password


@pytest.mark.parametrize(
    "password",
    [
        "short9!A",
        "onlylowercasepassword",
        "ONLYUPPERCASEPASSWORD",
        "1234567890123456",
    ],
)
def test_admin_password_policy_rejects_weak_passwords(password: str) -> None:
    with pytest.raises(RuntimeError, match="至少12位"):
        validate_admin_password(password)
