from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from app.agents.checkpoints import psycopg_database_url
from app.agents.customer_service.graph import build_customer_service_graph
from app.agents.customer_service.llm import CustomerLLM, DeepSeekCustomerLLM, FakeCustomerLLM
from app.agents.customer_service.mcp_client import MCPToolClient, StreamableMCPToolClient
from app.agents.customer_service.repository import (
    CustomerServiceRepository,
    RepositoryFailure,
)
from app.agents.customer_service.schemas import (
    AgentRunStatus,
    AgentState,
    ApprovalDecisionRequest,
    CustomerServiceOutput,
    CustomerServiceRunRequest,
)
from app.core.config import Settings, get_settings
from app.db.session import get_sessionmaker


class CustomerServiceAgentService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        repository: CustomerServiceRepository | None = None,
        llm: CustomerLLM | None = None,
        tools: MCPToolClient | None = None,
        trusted_identity: bool = False,
    ) -> None:
        self.settings = settings or get_settings()
        self.repository = repository or CustomerServiceRepository(get_sessionmaker())
        self.llm = llm
        self.tools = tools
        self.trusted_identity = trusted_identity

    async def create_run(self, request: CustomerServiceRunRequest) -> CustomerServiceOutput:
        self._require_development_identity()
        assert request.principal_id is not None
        state: AgentState = {
            "message": request.message,
            "principal_id": request.principal_id,
            "order_id": request.order_id,
            "run_id": request.run_id,
            "thread_id": request.thread_id,
            "trace_id": request.trace_id,
            "auth_session_digest": request.auth_session_digest,
        }
        await self.repository.create_run(state)
        try:
            await self._invoke(state)
        except Exception:
            failed: AgentState = {
                **state,
                "intent": "UNKNOWN",
                "answer": "客服流程发生内部错误。",
                "need_human": True,
                "handoff_reason": "INTERNAL_ERROR",
                "risk": "MEDIUM",
                "error": {
                    "error_code": "INTERNAL_ERROR",
                    "message": "客服流程发生内部错误。",
                    "retryable": False,
                    "trace_id": request.trace_id,
                },
                "evidence": [],
            }
            await self.repository.persist_state(failed, AgentRunStatus.FAILED)
        return await self.repository.get_output(
            request.run_id, request.principal_id, request.auth_session_digest
        )

    async def get_run(
        self, run_id: str, principal_id: str, auth_session_digest: str | None = None
    ) -> CustomerServiceOutput:
        self._require_development_identity()
        return await self.repository.get_output(run_id, principal_id, auth_session_digest)

    async def decide(
        self, approval_id: str, request: ApprovalDecisionRequest
    ) -> CustomerServiceOutput:
        self._require_development_identity()
        assert request.principal_id is not None
        run_id, thread_id, decision = await self.repository.decide_approval(
            approval_id, request.decision, request.principal_id
        )
        owner_id = await self.repository.approval_owner(approval_id)
        current = await self.repository.get_output(run_id, owner_id)
        if current.status in {AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED}:
            return current
        await self._resume(thread_id, decision)
        return await self.repository.get_output(run_id, owner_id)

    async def principal_for_run(self, run_id: str) -> str:
        return await self.repository.run_principal(run_id)

    async def _invoke(self, state: AgentState) -> None:
        async with AsyncPostgresSaver.from_conn_string(psycopg_database_url()) as checkpointer:
            await checkpointer.setup()
            graph = build_customer_service_graph(
                repository=self.repository,
                llm=self._llm(),
                tools=self.tools
                or StreamableMCPToolClient(
                    self.settings.business_mcp_url, self.settings.knowledge_mcp_url
                ),
                settings=self.settings,
                checkpointer=checkpointer,
            )
            config: RunnableConfig = {
                "configurable": {"thread_id": state["thread_id"]},
                "recursion_limit": self.settings.agent_max_steps + 2,
            }
            await graph.ainvoke(state, config=config)

    async def _resume(self, thread_id: str, decision: str) -> None:
        async with AsyncPostgresSaver.from_conn_string(psycopg_database_url()) as checkpointer:
            await checkpointer.setup()
            graph = build_customer_service_graph(
                repository=self.repository,
                llm=self._llm(),
                tools=self.tools
                or StreamableMCPToolClient(
                    self.settings.business_mcp_url, self.settings.knowledge_mcp_url
                ),
                settings=self.settings,
                checkpointer=checkpointer,
            )
            config: RunnableConfig = {
                "configurable": {"thread_id": thread_id},
                "recursion_limit": self.settings.agent_max_steps + 2,
            }
            command: Command[Any] = Command(resume=decision)
            await graph.ainvoke(command, config=config)

    def _require_development_identity(self) -> None:
        if not self.trusted_identity and not self.settings.enable_dev_principal_input:
            raise RepositoryFailure("UNAUTHENTICATED", "development principal input is disabled")

    def _llm(self) -> CustomerLLM:
        if self.llm is not None:
            return self.llm
        if self.settings.agent_llm_mode == "fake":
            return FakeCustomerLLM()
        return DeepSeekCustomerLLM(self.settings)
