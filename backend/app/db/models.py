from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Principal(Base):
    __tablename__ = "principals"
    __table_args__ = (Index("ix_principals_role_store", "role", "store_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    store_id: Mapped[str | None] = mapped_column(ForeignKey("stores.id"), nullable=True)
    customer_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AuthUser(Base):
    """Login identity linked to the existing Principal authorization identity."""

    __tablename__ = "auth_users"
    __table_args__ = (
        UniqueConstraint("username_normalized", name="uq_auth_users_username_normalized"),
        UniqueConstraint("principal_id", name="uq_auth_users_principal"),
        Index("ix_auth_users_locked", "locked_until"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    username_normalized: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    account_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="normal", server_default="normal"
    )
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthSession(Base):
    """Opaque server-side session. Raw session and CSRF tokens are never persisted."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_active", "user_id", "revoked_at"),
        Index("ix_auth_sessions_expiry", "absolute_expires_at"),
    )

    token_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False)
    csrf_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)


class SecurityAuditEvent(Base):
    """Security metadata only; secrets, cookies and request bodies are forbidden here."""

    __tablename__ = "security_audit_events"
    __table_args__ = (
        Index("ix_security_audit_actor_created", "actor_principal_id", "created_at"),
        Index("ix_security_audit_rule_created", "rule_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_principal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    client_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (Index("ix_products_store", "store_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)


class SKU(Base):
    __tablename__ = "skus"
    __table_args__ = (
        UniqueConstraint("store_id", "code", name="uq_skus_store_code"),
        Index("ix_skus_product", "product_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class Inventory(Base):
    __tablename__ = "inventories"
    __table_args__ = (
        UniqueConstraint("store_id", "sku_id", name="uq_inventory_store_sku"),
        Index("ix_inventory_store_on_hand", "store_id", "on_hand"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"), nullable=False)
    on_hand: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False)
    scenario: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_store_ordered", "store_id", "ordered_at"),
        Index("ix_orders_customer", "customer_ref"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    customer_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (Index("ix_order_items_order", "order_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class Logistics(Base):
    __tablename__ = "logistics"
    __table_args__ = (Index("ix_logistics_order", "order_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True, nullable=False)
    carrier: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    last_event: Mapped[str] = mapped_column(String(160), nullable=False)
    estimated_delivery_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scenario: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        Index("ix_refunds_order", "order_id"),
        Index("ix_refunds_store_created", "store_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    scenario: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SalesDaily(Base):
    __tablename__ = "sales_daily"
    __table_args__ = (UniqueConstraint("store_id", "sales_date", name="uq_sales_daily_store_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    sales_date: Mapped[date] = mapped_column(Date, nullable=False)
    sessions: Mapped[int] = mapped_column(Integer, nullable=False)
    conversions: Mapped[int] = mapped_column(Integer, nullable=False)
    order_count: Mapped[int] = mapped_column(Integer, nullable=False)
    gmv: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    scenario: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (Index("ix_campaigns_store_period", "store_id", "starts_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    campaign_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scenario: Mapped[str | None] = mapped_column(String(40), nullable=True)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("id", "version", name="uq_knowledge_document_version"),
        Index("ix_knowledge_documents_effective", "effective_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    store_id: Mapped[str | None] = mapped_column(ForeignKey("stores.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_key", name="uq_chunk_document_key"),
        Index("ix_chunks_document_ordinal", "document_id", "ordinal"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), nullable=False)
    chunk_key: Mapped[str] = mapped_column(String(64), nullable=False)
    section: Mapped[str] = mapped_column(String(120), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class SOP(Base):
    __tablename__ = "sops"
    __table_args__ = (Index("ix_sops_effective", "effective_at", "expires_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    store_id: Mapped[str | None] = mapped_column(ForeignKey("stores.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)


class ToolCallAudit(Base):
    __tablename__ = "tool_call_audits"
    __table_args__ = (
        Index("ix_tool_audits_trace", "trace_id"),
        Index("ix_tool_audits_principal_started", "principal_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(20), nullable=False)
    run_id: Mapped[str] = mapped_column(String(80), nullable=False)
    task_id: Mapped[str] = mapped_column(String(80), nullable=False)
    agent: Mapped[str] = mapped_column(String(80), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    store_scope_summary: Mapped[str | None] = mapped_column(String(80), nullable=True)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    permission_result: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_ref: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    side_effect: Mapped[bool] = mapped_column(Boolean, nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(80), nullable=False)


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("id", "action_digest", name="uq_approval_action_digest"),
        Index("ix_approvals_principal_tool", "principal_id", "tool_name"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    action_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    thread_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("tool_name", "idempotency_key", name="uq_idempotency_tool_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    action_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SeedManifest(Base):
    __tablename__ = "seed_manifests"

    manifest_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    simulated_clock: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rules_version: Mapped[str] = mapped_column(String(32), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    counts: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_thread_updated", "thread_id", "updated_at"),
        Index("ix_agent_runs_principal_created", "principal_id", "created_at"),
        Index("ix_agent_runs_trace", "trace_id"),
    )

    run_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(80), nullable=False)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    auth_session_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    need_human: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CustomerConversation(Base):
    __tablename__ = "customer_conversations"
    __table_args__ = (Index("ix_customer_conversations_principal", "principal_id"),)

    thread_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    last_run_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentEvent(Base):
    __tablename__ = "agent_events"
    __table_args__ = (
        Index("ix_agent_events_run_created", "run_id", "created_at"),
        Index("ix_agent_events_trace", "trace_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.run_id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentCheckpointLink(Base):
    __tablename__ = "agent_checkpoint_links"
    __table_args__ = (Index("ix_checkpoint_links_run", "run_id"),)

    thread_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.run_id"), nullable=False)
    checkpoint_namespace: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrchestratorSubTask(Base):
    __tablename__ = "orchestrator_subtasks"
    __table_args__ = (
        UniqueConstraint(
            "parent_run_id", "idempotency_key", name="uq_orchestrator_subtask_idempotency"
        ),
        Index("ix_orchestrator_subtasks_parent_status", "parent_run_id", "status"),
        Index("ix_orchestrator_subtasks_child_run", "child_run_id"),
        Index("ix_orchestrator_subtasks_external_task", "external_task_id"),
    )

    subtask_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    parent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.run_id"), nullable=False)
    child_run_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    external_task_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    dependency_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    required_inputs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_ref: Mapped[str | None] = mapped_column(String(240), nullable=True)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    expected_output_schema: Mapped[str] = mapped_column(String(80), nullable=False)
    tool_scope: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LiveSession(Base):
    __tablename__ = "live_sessions"
    __table_args__ = (Index("ix_live_sessions_store_created", "store_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    owner_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint("upload_id", name="uq_media_assets_upload_id"),
        UniqueConstraint("owner_id", "idempotency_key", name="uq_media_assets_owner_idempotency"),
        Index("ix_media_assets_store_status", "store_id", "status"),
        Index("ix_media_assets_hash", "content_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    upload_id: Mapped[str] = mapped_column(String(36), nullable=False)
    live_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("live_sessions.id"), nullable=True
    )
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    owner_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    display_filename: Mapped[str] = mapped_column(String(200), nullable=False)
    object_bucket: Mapped[str | None] = mapped_column(String(80), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(48), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    evaluation_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unclassified", server_default="unclassified"
    )
    fixture_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown", server_default="unknown"
    )
    human_review_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MediaTask(Base):
    __tablename__ = "media_tasks"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_media_tasks_owner_idempotency"),
        Index("ix_media_tasks_asset_created", "asset_id", "created_at"),
        Index("ix_media_tasks_store_status", "store_id", "status"),
        Index("ix_media_tasks_run", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.run_id"), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(80), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(48), nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pipeline_version: Mapped[str] = mapped_column(String(48), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Transcript(Base):
    __tablename__ = "transcripts"
    __table_args__ = (UniqueConstraint("task_id", name="uq_transcripts_task"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("media_tasks.id"), nullable=False)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    asr_model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    asr_config_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correction_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correction_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    segment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        UniqueConstraint("transcript_id", "ordinal", name="uq_transcript_segment_ordinal"),
        Index("ix_transcript_segments_transcript", "transcript_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transcript_id: Mapped[str] = mapped_column(ForeignKey("transcripts.id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(7, 6), nullable=True)
    word_timestamps: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)


class MediaGroundTruthAnnotation(Base):
    __tablename__ = "media_ground_truth_annotations"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "annotation_version",
            name="uq_media_ground_truth_asset_version",
        ),
        Index("ix_media_ground_truth_asset_created", "asset_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"), nullable=False)
    source_transcript_id: Mapped[str] = mapped_column(ForeignKey("transcripts.id"), nullable=False)
    annotation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    annotator_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    source_authorized: Mapped[bool] = mapped_column(Boolean, nullable=False)
    annotation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProductGroundTruthSegment(Base):
    __tablename__ = "product_ground_truth_segments"
    __table_args__ = (
        UniqueConstraint(
            "annotation_id",
            "product_segment_id",
            name="uq_product_ground_truth_annotation_segment",
        ),
        UniqueConstraint(
            "annotation_id",
            "ordinal",
            name="uq_product_ground_truth_annotation_ordinal",
        ),
        Index("ix_product_ground_truth_annotation", "annotation_id", "ordinal"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    annotation_id: Mapped[str] = mapped_column(
        ForeignKey("media_ground_truth_annotations.id"), nullable=False
    )
    product_segment_id: Mapped[str] = mapped_column(String(80), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    product_name: Mapped[str] = mapped_column(String(160), nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    corrected_transcript: Mapped[str] = mapped_column(Text, nullable=False)
    annotator_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClipCandidate(Base):
    __tablename__ = "clip_candidates"
    __table_args__ = (
        UniqueConstraint("task_id", "ordinal", name="uq_clip_candidates_task_ordinal"),
        Index("ix_clip_candidates_task_score", "task_id", "score"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("media_tasks.id"), nullable=False)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    product_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    predicted_start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    predicted_end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapped_start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapped_end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    boundary_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    segmentation_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    asr_config_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    score_components: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    video_bucket: Mapped[str | None] = mapped_column(String(80), nullable=True)
    video_object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    subtitle_bucket: Mapped[str | None] = mapped_column(String(80), nullable=True)
    subtitle_object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumbnail_bucket: Mapped[str | None] = mapped_column(String(80), nullable=True)
    thumbnail_object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_hashes: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    qc_result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClipReview(Base):
    __tablename__ = "clip_reviews"
    __table_args__ = (Index("ix_clip_reviews_clip_created", "clip_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    clip_id: Mapped[str] = mapped_column(ForeignKey("clip_candidates.id"), nullable=False)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    adjusted_start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adjusted_end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rubric: Mapped[dict[str, bool]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MediaTaskEvent(Base):
    __tablename__ = "media_task_events"
    __table_args__ = (
        UniqueConstraint("task_id", "sequence", name="uq_media_task_event_sequence"),
        Index("ix_media_task_events_task_created", "task_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("media_tasks.id"), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.run_id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(48), nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False)
    message: Mapped[str] = mapped_column(String(240), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_evidence_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    output_object_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(48), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
