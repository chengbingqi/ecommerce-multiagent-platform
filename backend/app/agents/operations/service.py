from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.checkpoints import psycopg_database_url
from app.agents.customer_service.repository import RepositoryFailure
from app.agents.customer_service.schemas import AgentRunStatus
from app.agents.operations.graph import build_operations_graph
from app.agents.operations.mcp_client import OperationsToolClient, StreamableOperationsToolClient
from app.agents.operations.provider import OperationsLLM, build_operations_llm
from app.agents.operations.repository import OperationsRepository
from app.agents.operations.schemas import OperationsOutput, OperationsRunRequest, OperationsState
from app.core.config import Settings, get_settings
from app.db.session import get_sessionmaker


class OperationsAgentService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        repository: OperationsRepository | None = None,
        llm: OperationsLLM | None = None,
        tools: OperationsToolClient | None = None,
        trusted_identity: bool = False,
    ) -> None:
        self.settings = settings or get_settings()
        self.repository = repository or OperationsRepository(get_sessionmaker())
        self.llm = llm
        self.tools = tools
        self.trusted_identity = trusted_identity

    async def create_run(self, request: OperationsRunRequest) -> OperationsOutput:
        self._require_development_identity()
        assert request.principal_id is not None
        start_at, end_at = request.utc_bounds()
        state: OperationsState = {
            "principal_id": request.principal_id,
            "requested_store_id": request.store_id,
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "topic": request.topic.value,
            "granularity": request.granularity,
            "comparison_mode": request.comparison_mode,
            "user_question": request.user_question,
            "run_id": request.run_id,
            "thread_id": request.thread_id,
            "trace_id": request.trace_id,
            "auth_session_digest": request.auth_session_digest,
        }
        await self.repository.create_run(state)
        try:
            await self._invoke(state)
        except Exception:
            failed: OperationsState = {
                **state,
                "explanation": "运营分析发生内部错误。",
                "metrics": [],
                "trend_series": [],
                "anomalies": [],
                "evidence_rows": [],
                "recommendations": [],
                "warnings": ["INTERNAL_ERROR"],
                "no_data": False,
                "need_human": True,
                "error": {
                    "error_code": "INTERNAL_ERROR",
                    "message": "运营分析发生内部错误。",
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
    ) -> OperationsOutput:
        self._require_development_identity()
        return await self.repository.get_output(run_id, principal_id, auth_session_digest)

    async def _invoke(self, state: OperationsState) -> None:
        async with AsyncPostgresSaver.from_conn_string(psycopg_database_url()) as checkpointer:
            await checkpointer.setup()
            graph = build_operations_graph(
                repository=self.repository,
                llm=self.llm or build_operations_llm(self.settings),
                tools=self.tools
                or StreamableOperationsToolClient(
                    self.settings.business_mcp_url, self.settings.knowledge_mcp_url
                ),
                settings=self.settings,
                checkpointer=checkpointer,
            )
            config: RunnableConfig = {
                "configurable": {"thread_id": state["thread_id"], "checkpoint_ns": "operations"},
                "recursion_limit": self.settings.agent_max_steps + 2,
            }
            await graph.ainvoke(state, config=config)

    def _require_development_identity(self) -> None:
        if not self.trusted_identity and not self.settings.enable_dev_principal_input:
            raise RepositoryFailure("UNAUTHENTICATED", "development principal input is disabled")
