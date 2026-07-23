# 阶段2数据库模型

Revision：`20260718_01`。迁移使用现有 SQLAlchemy metadata 创建阶段2表，并在 downgrade 中按外键逆序显式删除这些表，不触碰未知表。

## 业务与知识表

- `stores`、`principals`：`store_id` 是权限过滤边界；principal 的 role/scope 只由服务端记录解析。
- `products`、`skus`、`inventories`：SKU code 与 store 唯一，库存按 store+SKU 唯一。
- `orders`、`order_items`、`logistics`、`refunds`：金额为 `NUMERIC(14,2)`；退款只写 `simulated_requested`。
- `sales_daily`、`campaigns`：日指标可追溯订单；异常场景保存在 `scenario`。
- `knowledge_documents`、`knowledge_chunks`、`sops`：包含版本、生效/失效时间、内容 hash 和稳定引用键。

所有业务时间由 manifest 的 UTC `simulated_clock` 推导并写入带时区字段。模拟数据不含真实姓名、电话或地址。

## 工具治理表

- `tool_call_audits`：成功、权限拒绝、审批拒绝、功能关闭和内部错误的统一审计。
- `approvals`：审批主体、工具、`action_digest`、状态与失效时间。
- `idempotency_records`：唯一约束为 `(tool_name, idempotency_key)`；同 key 的 digest 不同即冲突。
- `seed_manifests`：manifest 版本、seed、模拟时钟、规则版本、记录数和 checksum。

模拟退款业务写入、幂等记录和成功审计处于同一事务；审计提交失败会回滚副作用。
