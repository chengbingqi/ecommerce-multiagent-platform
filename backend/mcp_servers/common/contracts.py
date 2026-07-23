from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class CallContext(BaseModel):
    principal_id: str = Field(min_length=1, max_length=64)
    agent: str = Field(default="direct", min_length=1, max_length=80)
    run_id: str = Field(default_factory=lambda: f"run-{uuid4()}", max_length=80)
    task_id: str = Field(default_factory=lambda: f"task-{uuid4()}", max_length=80)
    trace_id: str = Field(default_factory=lambda: f"trace-{uuid4()}", max_length=80)


class Evidence(BaseModel):
    evidence_ref: str
    source_type: Literal["database", "document", "manifest", "policy", "sop"]
    record_id: str
    version: str | None = None


class ToolErrorData(BaseModel):
    error_code: str
    message: str
    retryable: bool
    trace_id: str


class ToolMeta(BaseModel):
    tool_name: str
    tool_version: str
    run_id: str
    task_id: str
    trace_id: str
    latency_ms: int


class ToolResult(BaseModel):
    ok: bool
    data: Any | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    error: ToolErrorData | None = None
    meta: ToolMeta


class ToolSpec(BaseModel):
    tool_name: str
    tool_version: str = "1.0.0"
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permissions: tuple[str, ...]
    side_effect: bool = False
    timeout_seconds: int = 5
    retry_policy: Literal["none", "safe_once"] = "none"
    error_codes: tuple[str, ...]
    audit_fields: tuple[str, ...] = ()
