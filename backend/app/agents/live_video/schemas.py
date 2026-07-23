from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict


class LiveVideoState(TypedDict, total=False):
    agent_type: str
    asset_id: str
    task_id: str
    run_id: str
    thread_id: str
    trace_id: str
    principal_id: str
    store_id: str
    status: str
    progress: int
    pipeline_version: str
    transcript_ref: str | None
    candidate_refs: list[str]
    qc: list[dict[str, Any]]
    review_status: str
    warnings: list[str]
    error: dict[str, Any] | None
    authorized: bool
    asset_ready: bool
    task_created: bool
    worker_enqueued: bool


class LiveVideoOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_type: Literal["live_video"] = "live_video"
    run_id: str
    thread_id: str
    trace_id: str
    task_id: str
    asset_id: str
    pipeline_version: str
    status: str
    progress: int
    transcript_ref: str | None
    candidates: list[dict[str, Any]]
    timestamps: list[dict[str, str]]
    scores: list[dict[str, str]]
    qc: list[dict[str, Any]]
    review_status: str
    warnings: list[str]
    error: dict[str, Any] | None
