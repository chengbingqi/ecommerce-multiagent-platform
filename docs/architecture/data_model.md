# 数据模型

数据库结构由 Alembic 迁移管理，业务、知识、Agent 运行、安全和媒体状态统一保存在 PostgreSQL。

## 业务与知识

- `stores`、`principals`：`store_id` 是租户权限边界；角色和 scope 只由服务端记录解析。
- `products`、`skus`、`inventories`：SKU 与店铺唯一，库存按店铺和 SKU 隔离。
- `orders`、`order_items`、`logistics`、`refunds`：金额使用定点数；演示退款只写模拟状态。
- `sales_daily`、`campaigns`：提供可追溯的运营指标和确定性异常场景。
- `knowledge_documents`、`knowledge_chunks`、`sops`：保存版本、生效时间、内容哈希和稳定引用键。

模拟业务时间由版本化 manifest 的 UTC 时钟推导。公开 seed 不含真实姓名、电话或地址。

## Agent 与工具治理

- `agent_runs`、`agent_events`、`agent_checkpoint_links`：保存运行、事件和 checkpoint 关联。
- `orchestrator_subtasks`：保存主控父子任务、依赖、受限摘要、证据、重试和审批状态。
- `tool_call_audits`：记录成功、权限拒绝、审批拒绝、功能关闭和内部错误。
- `approvals`：绑定审批主体、工具、`action_digest`、状态和有效期。
- `idempotency_records`：以 `(tool_name, idempotency_key)` 唯一约束防止副作用重复执行。
- `seed_manifests`：记录模拟数据版本、seed、模拟时钟、规则版本和 checksum。

## 安全与媒体

- `auth_users`、`auth_sessions`、`security_audit_events`：保存登录身份、会话摘要和安全审计。
- 媒体资产、任务、候选和审核表只保存元数据与对象引用，不保存视频二进制或对象存储凭据。

模拟退款写入、幂等记录和成功审计位于同一事务；审计提交失败时业务写入回滚。
