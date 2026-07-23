from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agents.customer_service.schemas import AgentError, AgentRunStatus


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentTarget(StrEnum):
    CUSTOMER_SERVICE = "customer_service"
    OPERATIONS = "operations"
    MEDIA = "media"


class OrchestratorRisk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RouteDecision(StrictModel):
    intents: list[str] = Field(min_length=1, max_length=3)
    confidence: float = Field(ge=0, le=1)
    risk_level: OrchestratorRisk
    agent_targets: list[AgentTarget] = Field(max_length=3)
    required_inputs: list[str] = Field(default_factory=list, max_length=12)
    missing_inputs: list[str] = Field(default_factory=list, max_length=12)
    completion_rule: Literal["ALL_REQUIRED", "ALLOW_PARTIAL"]
    warnings: list[str] = Field(default_factory=list, max_length=12)
    route_version: str = Field(min_length=1, max_length=64)

    @field_validator("agent_targets")
    @classmethod
    def unique_targets(cls, values: list[AgentTarget]) -> list[AgentTarget]:
        if len(set(values)) != len(values):
            raise ValueError("agent targets must be unique")
        return values


class RetryPolicy(StrictModel):
    max_retries: int = Field(default=1, ge=0, le=1)
    retryable_error_codes: list[str] = Field(
        default_factory=lambda: ["TIMEOUT", "TRANSIENT_ERROR", "MCP_UNAVAILABLE"],
        max_length=6,
    )
    side_effect_retry: bool = False


class SubTaskPlan(StrictModel):
    task_id: str = Field(min_length=8, max_length=80)
    parent_run_id: str = Field(min_length=8, max_length=80)
    child_run_id: str = Field(min_length=8, max_length=80)
    child_thread_id: str = Field(min_length=8, max_length=80)
    agent_type: AgentTarget
    dependency_ids: list[str] = Field(default_factory=list, max_length=3)
    required_inputs: list[str] = Field(default_factory=list, max_length=12)
    expected_output_schema: str = Field(min_length=1, max_length=80)
    tool_scope: list[str] = Field(max_length=10)
    timeout_seconds: int = Field(ge=1, le=60)
    retry_policy: RetryPolicy
    status: AgentRunStatus = AgentRunStatus.PENDING
    idempotency_key: str = Field(min_length=8, max_length=128)


class SubTaskView(StrictModel):
    task_id: str
    parent_run_id: str
    child_run_id: str | None
    external_task_id: str | None
    agent_type: AgentTarget
    dependency_ids: list[str]
    required_inputs: list[str]
    expected_output_schema: str
    tool_scope: list[str]
    timeout_seconds: int
    retry_policy: RetryPolicy
    status: AgentRunStatus
    retry_count: int
    output_ref: str | None
    evidence_refs: list[str]
    error_code: str | None
    approval_id: str | None
    duration_ms: int | None


class MediaResult(StrictModel):
    task_id: str
    asset_id: str
    status: str
    pipeline_version: Literal["media_pipeline_v1.2.1"]
    experimental: Literal[True] = True
    human_review_required: Literal[True] = True
    auto_publish: Literal[False] = False
    review_status: str
    evidence_refs: list[str]
    warning: str = "实验性能力，结果必须人工审核，不会自动发布。"


class OrchestratorRunRequest(StrictModel):
    agent_type: Literal["orchestrator"]
    request_text: str = Field(min_length=1, max_length=2000)
    principal_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    thread_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    trace_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    auth_session_digest: str | None = Field(default=None, min_length=64, max_length=64)
    order_id: str | None = Field(default=None, min_length=1, max_length=64)
    store_id: str | None = Field(default=None, min_length=1, max_length=64)
    start_date: date | None = None
    end_date: date | None = None
    topic: str | None = Field(default=None, max_length=64)
    asset_id: str | None = Field(default=None, min_length=1, max_length=64)
    product_keywords: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("request_text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if any(ord(character) < 32 and character not in "\n\t" for character in value):
            raise ValueError("request_text contains unsupported control characters")
        return value.strip()

    @field_validator("product_keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            clean = value.strip()[:80]
            if clean and clean not in result:
                result.append(clean)
        return result

    @model_validator(mode="after")
    def validate_period(self) -> OrchestratorRunRequest:
        if (self.start_date is None) != (self.end_date is None):
            return self
        if self.start_date and self.end_date:
            if self.start_date >= self.end_date:
                raise ValueError("start_date must be before end_date")
            if (self.end_date - self.start_date).days > 90:
                raise ValueError("date range must not exceed 90 days")
        return self


class OrchestratorOutput(StrictModel):
    run_id: str
    thread_id: str
    trace_id: str
    overall_status: AgentRunStatus
    route: RouteDecision
    plan: list[SubTaskPlan]
    subtasks: list[SubTaskView]
    customer_service_result: dict[str, Any] | None
    operations_result: dict[str, Any] | None
    media_result: MediaResult | None
    evidence_refs: list[str]
    partial_failures: list[dict[str, str]]
    warnings: list[str]
    need_human: bool
    completion_rule: str
    error: AgentError | None
    versions: dict[str, str]

    @property
    def status(self) -> AgentRunStatus:
        return self.overall_status


class AgentEventView(StrictModel):
    event_id: str
    run_id: str
    event_type: str
    status: str
    payload_summary: dict[str, Any]
    trace_id: str
    timestamp: str


class OrchestratorState(TypedDict, total=False):
    request_text: str
    principal_id: str
    run_id: str
    thread_id: str
    trace_id: str
    auth_session_digest: str | None
    order_id: str | None
    store_id: str | None
    start_date: str | None
    end_date: str | None
    topic: str | None
    asset_id: str | None
    product_keywords: list[str]
    role: str
    started_monotonic: float
    step_count: int
    route: dict[str, Any]
    plan: list[dict[str, Any]]
    subtasks: list[dict[str, Any]]
    child_outputs: dict[str, dict[str, Any]]
    customer_service_result: dict[str, Any] | None
    operations_result: dict[str, Any] | None
    media_result: dict[str, Any] | None
    evidence_refs: list[str]
    partial_failures: list[dict[str, str]]
    warnings: list[str]
    need_human: bool
    approval_id: str | None
    status: str
    error: dict[str, Any] | None
