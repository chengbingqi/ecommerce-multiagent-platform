from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChangeRequest(StrictModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class PrincipalView(StrictModel):
    principal_id: str
    role: str
    store_id: str | None
    customer_ref: str | None
    account_type: str = "normal"


class SessionView(StrictModel):
    principal: PrincipalView
    idle_expires_at: datetime
    absolute_expires_at: datetime
    security_policy_version: str


class LoginResponse(SessionView):
    csrf_token: str


class CsrfResponse(StrictModel):
    csrf_token: str


class MessageResponse(StrictModel):
    ok: bool = True


class AuthorizationUpdateRequest(StrictModel):
    role: str = Field(pattern=r"^(customer|store_operator|content_operator|approver|admin)$")
    store_id: str | None = Field(default=None, min_length=1, max_length=36)
