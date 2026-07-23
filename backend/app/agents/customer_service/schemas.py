from enum import StrEnum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerIntent(StrEnum):
    ORDER_STATUS = "ORDER_STATUS"
    LOGISTICS_STATUS = "LOGISTICS_STATUS"
    POLICY_QUERY = "POLICY_QUERY"
    REFUND_ELIGIBILITY = "REFUND_ELIGIBILITY"
    REFUND_REQUEST = "REFUND_REQUEST"
    UNKNOWN = "UNKNOWN"
    FORBIDDEN_REQUEST = "FORBIDDEN_REQUEST"


class AgentRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AgentError(StrictModel):
    error_code: str
    message: str
    retryable: bool = False
    trace_id: str


class AgentEvidence(StrictModel):
    evidence_ref: str
    source_type: str
    record_id: str
    version: str | None = None


class ApprovalView(StrictModel):
    approval_id: str
    status: Literal["pending", "approved", "rejected", "used"]
    action_digest: str
    expires_at: str


class IntentClassification(StrictModel):
    intent: CustomerIntent
    order_id: str | None = Field(default=None, max_length=64)
    refund_amount: str | None = Field(default=None, pattern=r"^\d{1,12}(\.\d{1,2})?$")
    reason_code: str | None = Field(default=None, max_length=80)


class GroundedClaim(StrictModel):
    text: str = Field(min_length=1, max_length=400)
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)


class GroundedDraft(StrictModel):
    answer: str = Field(min_length=1, max_length=1600)
    claims: list[GroundedClaim] = Field(default_factory=list, max_length=12)


class CustomerServiceRunRequest(StrictModel):
    agent_type: Literal["customer_service"]
    message: str = Field(min_length=1, max_length=2000)
    principal_id: str = Field(min_length=1, max_length=64)
    order_id: str | None = Field(default=None, min_length=1, max_length=64)
    run_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    thread_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    trace_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    auth_session_digest: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("message")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 and character not in "\n\t" for character in value):
            raise ValueError("message contains unsupported control characters")
        return value.strip()


class ApprovalDecisionRequest(StrictModel):
    decision: Literal["approved", "rejected"]
    principal_id: str = Field(min_length=1, max_length=64)
    trace_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class CustomerServiceOutput(StrictModel):
    answer: str
    intent: CustomerIntent
    evidence: list[AgentEvidence]
    order_summary: dict[str, Any] | None
    refund_eligibility: dict[str, Any] | None
    risk: RiskLevel
    need_human: bool
    handoff_reason: str | None
    approval: ApprovalView | None
    error: AgentError | None
    run_id: str
    thread_id: str
    trace_id: str
    status: AgentRunStatus


class AgentState(TypedDict, total=False):
    message: str
    principal_id: str
    order_id: str | None
    run_id: str
    thread_id: str
    trace_id: str
    auth_session_digest: str | None
    started_monotonic: float
    step_count: int
    status: str
    intent: str
    role: str
    store_id: str | None
    customer_ref: str | None
    planned_tools: list[str]
    tool_results: dict[str, dict[str, Any]]
    evidence: list[dict[str, Any]]
    order_summary: dict[str, Any] | None
    refund_eligibility: dict[str, Any] | None
    refund_request: dict[str, Any] | None
    refund_amount: str | None
    reason_code: str | None
    answer: str
    claims: list[dict[str, Any]]
    risk: str
    need_human: bool
    handoff_reason: str | None
    approval: dict[str, Any] | None
    error: dict[str, Any] | None
    llm_calls: int
