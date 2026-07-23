from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from app.agents.live_video.schemas import LiveVideoState


def build_live_video_graph(checkpointer: Any = None) -> Any:
    async def validate_request(state: LiveVideoState) -> dict[str, Any]:
        valid = state.get("agent_type") == "live_video" and bool(state.get("asset_id"))
        return {
            "status": "RUNNING" if valid else "FAILED",
            "error": None
            if valid
            else {"error_code": "INVALID_ARGUMENT", "message": "invalid live video request"},
        }

    async def resolve_principal(state: LiveVideoState) -> dict[str, Any]:
        return {"authorized": bool(state.get("principal_id") and state.get("store_id"))}

    async def authorize_asset(state: LiveVideoState) -> dict[str, Any]:
        if not state.get("authorized"):
            return {
                "status": "FAILED",
                "error": {"error_code": "FORBIDDEN", "message": "asset access denied"},
            }
        return {}

    async def inspect_asset(state: LiveVideoState) -> dict[str, Any]:
        return {"asset_ready": state.get("status") != "FAILED"}

    async def create_pipeline_task(state: LiveVideoState) -> dict[str, Any]:
        return {"task_created": bool(state.get("task_id"))}

    async def enqueue_media_worker(state: LiveVideoState) -> dict[str, Any]:
        return {"worker_enqueued": bool(state.get("task_created")), "status": "PROCESSING"}

    async def interrupt_while_processing(state: LiveVideoState) -> dict[str, Any]:
        return {"status": state.get("status", "PROCESSING")}

    async def resume_from_task_result(state: LiveVideoState) -> dict[str, Any]:
        return {"status": state.get("status", "SUCCEEDED")}

    async def validate_candidates_and_qc(state: LiveVideoState) -> dict[str, Any]:
        if state.get("status") != "SUCCEEDED":
            return {}
        if not state.get("candidate_refs"):
            return {
                "status": "FAILED",
                "error": {"error_code": "NO_CANDIDATES", "message": "no candidates produced"},
            }
        return {}

    async def await_human_review(state: LiveVideoState) -> dict[str, Any]:
        if state.get("status") == "SUCCEEDED":
            return {"review_status": "PENDING"}
        return {}

    async def finalize_result(state: LiveVideoState) -> dict[str, Any]:
        return {"progress": 100 if state.get("status") == "SUCCEEDED" else state.get("progress", 0)}

    def after_validate(state: LiveVideoState) -> Literal["resolve_principal", "finalize_result"]:
        return "finalize_result" if state.get("status") == "FAILED" else "resolve_principal"

    builder = StateGraph(LiveVideoState)
    builder.add_node("validate_request", validate_request)
    builder.add_node("resolve_principal", resolve_principal)
    builder.add_node("authorize_asset", authorize_asset)
    builder.add_node("inspect_asset", inspect_asset)
    builder.add_node("create_pipeline_task", create_pipeline_task)
    builder.add_node("enqueue_media_worker", enqueue_media_worker)
    builder.add_node("interrupt_while_processing", interrupt_while_processing)
    builder.add_node("resume_from_task_result", resume_from_task_result)
    builder.add_node("validate_candidates_and_qc", validate_candidates_and_qc)
    builder.add_node("await_human_review", await_human_review)
    builder.add_node("finalize_result", finalize_result)
    builder.add_edge(START, "validate_request")
    builder.add_conditional_edges("validate_request", after_validate)
    builder.add_edge("resolve_principal", "authorize_asset")
    builder.add_edge("authorize_asset", "inspect_asset")
    builder.add_edge("inspect_asset", "create_pipeline_task")
    builder.add_edge("create_pipeline_task", "enqueue_media_worker")
    builder.add_edge("enqueue_media_worker", "interrupt_while_processing")
    builder.add_edge("interrupt_while_processing", "resume_from_task_result")
    builder.add_edge("resume_from_task_result", "validate_candidates_and_qc")
    builder.add_edge("validate_candidates_and_qc", "await_human_review")
    builder.add_edge("await_human_review", "finalize_result")
    builder.add_edge("finalize_result", END)
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["interrupt_while_processing"],
        name="live_video_graph",
    )
