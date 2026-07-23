from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    SOP,
    KnowledgeChunk,
    KnowledgeDocument,
    Principal,
    SeedManifest,
)
from mcp_servers.common.contracts import Evidence, ToolResult
from mcp_servers.common.errors import ToolFailure
from mcp_servers.common.governance import execute_tool
from mcp_servers.common.registry import KNOWLEDGE_SPECS
from mcp_servers.knowledge.schemas import ChunkInput, PolicySearchInput, SOPInput


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _clock(session: AsyncSession) -> datetime:
    value = await session.scalar(
        select(SeedManifest.simulated_clock).order_by(SeedManifest.manifest_version)
    )
    if value is None:
        raise ToolFailure("TRANSIENT_ERROR", "simulated clock is unavailable", retryable=True)
    return _aware(value)


def _authorize_store(principal: Principal, store_id: str | None) -> str | None:
    if principal.role == "admin":
        return store_id
    if store_id is not None and store_id != principal.store_id:
        raise ToolFailure("FORBIDDEN", "store is outside the principal scope")
    return principal.store_id


def _document_evidence(document: KnowledgeDocument, chunk: KnowledgeChunk) -> Evidence:
    return Evidence(
        evidence_ref=f"doc://{document.id}/{document.version}/{chunk.id}",
        source_type="document",
        record_id=chunk.id,
        version=document.version,
    )


class KnowledgeTools:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def search_policy(self, request: PolicySearchInput) -> ToolResult:
        async def handler(
            session: AsyncSession, principal: Principal
        ) -> tuple[dict[str, Any], list[Evidence]]:
            store_id = _authorize_store(principal, request.store_id)
            clock = await _clock(session)
            statement = (
                select(KnowledgeChunk, KnowledgeDocument)
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .where(
                    KnowledgeDocument.effective_at <= clock,
                    or_(
                        KnowledgeDocument.expires_at.is_(None),
                        KnowledgeDocument.expires_at > clock,
                    ),
                    or_(
                        KnowledgeDocument.store_id.is_(None),
                        KnowledgeDocument.store_id == store_id,
                    ),
                    KnowledgeChunk.content.contains(request.query),
                )
                .order_by(
                    KnowledgeDocument.source_type,
                    KnowledgeDocument.title,
                    KnowledgeDocument.version.desc(),
                    KnowledgeChunk.ordinal,
                    KnowledgeChunk.id,
                )
                .limit(request.limit)
            )
            rows = (await session.execute(statement)).all()
            evidence = [_document_evidence(document, chunk) for chunk, document in rows]
            return (
                {
                    "query": request.query,
                    "results": [
                        {
                            "doc_id": document.id,
                            "chunk_id": chunk.id,
                            "title": document.title,
                            "section": chunk.section,
                            "version": document.version,
                            "source_type": document.source_type,
                            "content": chunk.content,
                            "evidence_ref": item.evidence_ref,
                        }
                        for (chunk, document), item in zip(rows, evidence, strict=True)
                    ],
                },
                evidence,
            )

        return await execute_tool(
            self.session_factory,
            KNOWLEDGE_SPECS["search_policy"],
            request.context,
            request.model_dump(exclude={"context"}, mode="json"),
            handler,
        )

    async def retrieve_sop(self, request: SOPInput) -> ToolResult:
        async def handler(
            session: AsyncSession, principal: Principal
        ) -> tuple[dict[str, Any], list[Evidence]]:
            store_id = _authorize_store(principal, request.store_id)
            clock = await _clock(session)
            sop = await session.scalar(
                select(SOP)
                .where(
                    SOP.name.contains(request.name),
                    SOP.effective_at <= clock,
                    or_(SOP.expires_at.is_(None), SOP.expires_at > clock),
                    or_(SOP.store_id.is_(None), SOP.store_id == store_id),
                )
                .order_by(SOP.version.desc(), SOP.id)
            )
            if sop is None:
                return {"name": request.name, "result": None}, []
            evidence = Evidence(
                evidence_ref=f"sop://{sop.id}/{sop.version}",
                source_type="sop",
                record_id=sop.id,
                version=sop.version,
            )
            return (
                {
                    "name": sop.name,
                    "version": sop.version,
                    "steps": sop.steps,
                    "evidence_ref": evidence.evidence_ref,
                },
                [evidence],
            )

        return await execute_tool(
            self.session_factory,
            KNOWLEDGE_SPECS["retrieve_sop"],
            request.context,
            request.model_dump(exclude={"context"}, mode="json"),
            handler,
        )

    async def get_document_chunk(self, request: ChunkInput) -> ToolResult:
        async def handler(
            session: AsyncSession, principal: Principal
        ) -> tuple[dict[str, Any], list[Evidence]]:
            clock = await _clock(session)
            row = (
                await session.execute(
                    select(KnowledgeChunk, KnowledgeDocument)
                    .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                    .where(KnowledgeChunk.id == request.chunk_id)
                )
            ).one_or_none()
            if row is None:
                raise ToolFailure("NOT_FOUND", "document chunk was not found")
            chunk, document = row
            _authorize_store(principal, document.store_id)
            if _aware(document.effective_at) > clock or (
                document.expires_at is not None and _aware(document.expires_at) <= clock
            ):
                raise ToolFailure("NOT_FOUND", "no current document chunk is available")
            evidence = _document_evidence(document, chunk)
            return (
                {
                    "doc_id": document.id,
                    "chunk_id": chunk.id,
                    "title": document.title,
                    "section": chunk.section,
                    "version": document.version,
                    "content": chunk.content,
                    "content_hash": document.content_hash,
                    "evidence_ref": evidence.evidence_ref,
                },
                [evidence],
            )

        return await execute_tool(
            self.session_factory,
            KNOWLEDGE_SPECS["get_document_chunk"],
            request.context,
            request.model_dump(exclude={"context"}, mode="json"),
            handler,
        )
