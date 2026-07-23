from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.customer_service.schemas import AgentError, AgentRunStatus


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperationsTopic(StrEnum):
    OVERVIEW = "OVERVIEW"
    SALES = "SALES"
    INVENTORY = "INVENTORY"
    REFUNDS = "REFUNDS"
    CONVERSION = "CONVERSION"
    LOGISTICS = "LOGISTICS"
    CAMPAIGN = "CAMPAIGN"
    UNKNOWN = "UNKNOWN"
    FORBIDDEN_REQUEST = "FORBIDDEN_REQUEST"


class OperationsRunRequest(StrictModel):
    agent_type: Literal["operations"]
    store_id: str = Field(min_length=1, max_length=64)
    start_date: date
    end_date: date
    topic: OperationsTopic = OperationsTopic.OVERVIEW
    granularity: Literal["day", "week"] = "day"
    comparison_mode: Literal["previous_period", "none"] = "none"
    user_question: str | None = Field(default=None, max_length=1000)
    principal_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    thread_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    trace_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    auth_session_digest: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_period(self) -> "OperationsRunRequest":
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        if (self.end_date - self.start_date).days > 90:
            raise ValueError("date range must not exceed 90 days")
        if self.user_question:
            self.user_question = self.user_question.strip()
        return self

    def utc_bounds(self) -> tuple[datetime, datetime]:
        return datetime.combine(self.start_date, time.min, tzinfo=UTC), datetime.combine(
            self.end_date, time.min, tzinfo=UTC
        )


class PeriodView(StrictModel):
    start: str
    end: str
    semantics: Literal["[start,end)"] = "[start,end)"


class MetricView(StrictModel):
    metric_key: str
    display_name: str
    value: int | str
    unit: str
    period: PeriodView
    comparison_value: int | str | None = None
    change: str | None = None
    evidence_refs: list[str]


class TrendPoint(StrictModel):
    bucket_start: str
    granularity: Literal["day", "week"]
    order_count: int
    gmv: str
    visitor_count: int
    conversion_rate: str
    evidence_refs: list[str]


class EvidenceRow(StrictModel):
    evidence_ref: str
    record_type: str
    record_id: str
    store_id: str
    observed: int | str
    scenario: str | None = None


class AnomalyView(StrictModel):
    anomaly_id: str
    anomaly_type: str
    rule_id: str
    severity: Literal["low", "medium", "high"]
    observed_value: int | str
    threshold: str
    explanation: str
    evidence_refs: list[str]


class RecommendationView(StrictModel):
    priority: Literal["low", "medium", "high"]
    recommendation: str
    based_on_anomaly_ids: list[str]
    evidence_refs: list[str]


class OperationsDraft(StrictModel):
    explanation: str = Field(min_length=1, max_length=1600)
    metric_echo: dict[str, int | str]
    recommendations: list[RecommendationView] = Field(default_factory=list, max_length=10)


class OperationsOutput(StrictModel):
    run_id: str
    thread_id: str
    trace_id: str
    status: AgentRunStatus
    store_id: str
    period: PeriodView
    granularity: Literal["day", "week"]
    metrics_version: str
    anomaly_rules_version: str
    prompt_version: str
    topic: OperationsTopic
    metrics: list[MetricView]
    trend_series: list[TrendPoint]
    anomalies: list[AnomalyView]
    evidence_rows: list[EvidenceRow]
    explanation: str
    recommendations: list[RecommendationView]
    no_data: bool
    warnings: list[str]
    need_human: bool
    error: AgentError | None


class OperationsState(TypedDict, total=False):
    principal_id: str
    requested_store_id: str
    store_id: str
    role: str
    start_at: str
    end_at: str
    topic: str
    granularity: str
    comparison_mode: str
    user_question: str | None
    run_id: str
    thread_id: str
    trace_id: str
    auth_session_digest: str | None
    step_count: int
    started_monotonic: float
    planned_tools: list[str]
    metrics: list[dict[str, Any]]
    trend_series: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    evidence_rows: list[dict[str, Any]]
    scenario_counts: dict[str, int]
    anomalies: list[dict[str, Any]]
    sop: dict[str, Any] | None
    explanation: str
    recommendations: list[dict[str, Any]]
    metric_echo: dict[str, int | str]
    no_data: bool
    warnings: list[str]
    need_human: bool
    error: dict[str, Any] | None
    llm_calls: int
