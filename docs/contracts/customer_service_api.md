# 客服 Agent API 契约

阶段3开发/测试接口经现有 Caddy 同源入口访问，不新增端口。

## `POST /api/v1/runs`

只接受 `agent_type=customer_service`。请求字段：`message`、`principal_id`、可选 `order_id`、`run_id`、`thread_id`、`trace_id`。响应为结构化 `CustomerServiceOutput`，包含 answer、intent、evidence、order_summary、refund_eligibility、risk、need_human、handoff_reason、approval、error、三个稳定 ID 和 status。

## `GET /api/v1/runs/{run_id}`

开发/测试模式通过查询参数 `principal_id` 验证当前主体后返回持久化结果。非所有者且非 admin 返回 `FORBIDDEN`。

## `POST /api/v1/approvals/{approval_id}/decision`

请求只接受 `approved` 或 `rejected`、审批主体和 trace ID。批准从同一 thread/checkpoint 恢复；拒绝不调用副作用工具；重复批准返回既有完成结果。

稳定错误包括 `INVALID_ARGUMENT`、`UNAUTHENTICATED`、`FORBIDDEN`、`NOT_FOUND`、`APPROVAL_REQUIRED`、`IDEMPOTENCY_CONFLICT`、`TIMEOUT`、`TRANSIENT_ERROR`、`INTERNAL_ERROR` 以及阶段3 LLM 边界错误。响应不包含 traceback。

`principal_id` 输入仅用于 development/test。`APP_ENV=production` 时该机制关闭，生产配置强制 DeepSeek 模式；Caddy 没有特殊身份注入 header 或入口。阶段7前这不是正式认证。
