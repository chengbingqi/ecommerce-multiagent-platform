# 客服 Agent API 契约

接口经 Caddy 同源入口访问，不新增公开端口。

## `POST /api/v1/runs`

只接受 `agent_type=customer_service`。请求字段包括 `message`，以及可选的 `order_id`、`run_id`、`thread_id`、`trace_id`。响应为结构化 `CustomerServiceOutput`，包含 answer、intent、evidence、order_summary、refund_eligibility、risk、need_human、handoff_reason、approval、error、三个稳定 ID 和 status。

## `GET /api/v1/runs/{run_id}`

当前身份来自服务端 Session；仅 development/test 模式可显式传入测试 Principal。非所有者且非 admin 返回 `FORBIDDEN`。

## `POST /api/v1/approvals/{approval_id}/decision`

请求只接受 `approved` 或 `rejected`、审批主体和 trace ID。批准从同一 thread/checkpoint 恢复；拒绝不调用副作用工具；重复批准返回既有完成结果。

稳定错误包括 `INVALID_ARGUMENT`、`UNAUTHENTICATED`、`FORBIDDEN`、`NOT_FOUND`、`APPROVAL_REQUIRED`、`IDEMPOTENCY_CONFLICT`、`TIMEOUT`、`TRANSIENT_ERROR`、`INTERNAL_ERROR` 以及 LLM 边界错误。响应不包含 traceback。

`principal_id` 输入仅用于 development/test；`APP_ENV=production` 时该机制关闭。Caddy 不提供身份注入 Header，正式身份由安全 Session 解析。
