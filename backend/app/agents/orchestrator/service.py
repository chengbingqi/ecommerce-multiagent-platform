from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from app.agents.checkpoints import psycopg_database_url
from app.agents.customer_service.repository import RepositoryFailure
from app.agents.customer_service.schemas import AgentRunStatus
from app.agents.orchestrator.adapters import (
    DefaultOrchestratorAdapters,
    OrchestratorAdapters,
)
from app.agents.orchestrator.graph import build_orchestrator_graph
from app.agents.orchestrator.repository import OrchestratorRepository
from app.agents.orchestrator.routing import (
    DeepSeekOrchestratorRouter,
    DeterministicOrchestratorRouter,
    OrchestratorRouter,
)
from app.agents.orchestrator.schemas import (
    AgentEventView,
    OrchestratorOutput,
    OrchestratorRunRequest,
    OrchestratorState,
)
from app.core.config import Settings, get_settings
from app.db.session import get_sessionmaker


class OrchestratorService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        repository: OrchestratorRepository | None = None,
        router: OrchestratorRouter | None = None,
        adapters: OrchestratorAdapters | None = None,
        trusted_identity: bool = False,
    ) -> None:
        self.settings = settings or get_settings()
        self.repository = repository or OrchestratorRepository(get_sessionmaker())
        self.router = router
        self.adapters = adapters or DefaultOrchestratorAdapters(trusted_identity=trusted_identity)
        self.trusted_identity = trusted_identity

    async def create_run(self, request: OrchestratorRunRequest) -> OrchestratorOutput:
        self._require_development_identity()
        assert request.principal_id is not None
        await self.repository.create_run(request)
        state: OrchestratorState = {
            "request_text": request.request_text,
            "principal_id": request.principal_id,
            "run_id": request.run_id,
            "thread_id": request.thread_id,
            "trace_id": request.trace_id,
            "auth_session_digest": request.auth_session_digest,
            "order_id": request.order_id,
            "store_id": request.store_id,
            "start_date": request.start_date.isoformat() if request.start_date else None,
            "end_date": request.end_date.isoformat() if request.end_date else None,
            "topic": request.topic,
            "asset_id": request.asset_id,
            "product_keywords": request.product_keywords,
        }
        try:
            await self._invoke(state)
        except Exception:
            failed: OrchestratorState = {
                **state,
                "status": AgentRunStatus.FAILED.value,
                "route": self.repository.empty_route().model_dump(mode="json"),
                "plan": [],
                "subtasks": [],
                "evidence_refs": [],
                "partial_failures": [],
                "warnings": [],
                "need_human": True,
                "error": {
                    "error_code": "INTERNAL_ERROR",
                    "message": "主控流程发生内部错误。",
                    "retryable": False,
                    "trace_id": request.trace_id,
                },
            }
            await self.repository.persist_state(failed, AgentRunStatus.FAILED)
        return await self.repository.get_output(
            request.run_id, request.principal_id, request.auth_session_digest
        )

    async def get_run(
        self, run_id: str, principal_id: str, auth_session_digest: str | None = None
    ) -> OrchestratorOutput:
        self._require_development_identity()
        return await self.repository.get_output(run_id, principal_id, auth_session_digest)

    async def list_events(
        self,
        run_id: str,
        principal_id: str,
        after_event_id: str | None = None,
        auth_session_digest: str | None = None,
    ) -> list[AgentEventView]:
        self._require_development_identity()
        return await self.repository.list_events(
            run_id, principal_id, after_event_id, auth_session_digest
        )

    async def cancel(
        self, run_id: str, principal_id: str, auth_session_digest: str | None = None
    ) -> OrchestratorOutput:
        self._require_development_identity()
        return await self.repository.cancel(run_id, principal_id, auth_session_digest)

    async def resume_parent_for_child(
        self, child_run_id: str, principal_id: str
    ) -> OrchestratorOutput | None:
        self._require_development_identity()
        try:
            parent_run_id = await self.repository.parent_for_child(child_run_id)
        except RepositoryFailure as exc:
            if exc.code == "NOT_FOUND":
                return None
            raise
        parent = await self.repository.get_output(parent_run_id, principal_id)
        if parent.overall_status != AgentRunStatus.WAITING_APPROVAL:
            return parent
        async with AsyncPostgresSaver.from_conn_string(psycopg_database_url()) as checkpointer:
            await checkpointer.setup()
            graph = build_orchestrator_graph(
                repository=self.repository,
                router=self._router(),
                adapters=self.adapters,
                settings=self.settings,
                checkpointer=checkpointer,
            )
            config = self._config(parent.thread_id)
            command: Command[Any] = Command(resume={"child_run_id": child_run_id})
            await graph.ainvoke(command, config=config)
        return await self.repository.get_output(parent_run_id, principal_id)

    async def _invoke(self, state: OrchestratorState) -> None:
        async with AsyncPostgresSaver.from_conn_string(psycopg_database_url()) as checkpointer:
            await checkpointer.setup()
            graph = build_orchestrator_graph(
                repository=self.repository,
                router=self._router(),
                adapters=self.adapters,
                settings=self.settings,
                checkpointer=checkpointer,
            )
            await graph.ainvoke(state, config=self._config(state["thread_id"]))

    def _config(self, thread_id: str) -> RunnableConfig:
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "orchestrator",
            },
            "recursion_limit": self.settings.orchestrator_max_steps + 2,
        }

    def _router(self) -> OrchestratorRouter:
        if self.router is not None:
            return self.router
        if self.settings.agent_llm_mode == "fake":
            return DeterministicOrchestratorRouter(self.settings.orchestrator_route_version)
        return DeepSeekOrchestratorRouter(self.settings)

    def _require_development_identity(self) -> None:
        if not self.trusted_identity and not self.settings.enable_dev_principal_input:
            raise RepositoryFailure("UNAUTHENTICATED", "development principal input is disabled")
