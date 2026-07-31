# MCP 工具审计

每次进入工具执行内核的调用写入 `tool_call_audits`：tool call ID/name/version、run/task、agent、principal、store scope 摘要、白名单输入摘要、权限结果、起止时间、latency、status/error、retry count、evidence refs、side effect、approval、idempotency key 和 trace ID。

审计不保存密码、token、API key、连接串、电话或地址。输入摘要只从每个 `ToolSpec.audit_fields` 取值；自由文本知识 query 只保存 SHA-256 截断摘要。日志与数据库审计共用调用方提供或自动生成的 trace ID。

成功副作用与审计处于同一事务，审计持久化失败时业务写入回滚。权限拒绝、`APPROVAL_REQUIRED` 和 `FEATURE_NOT_ENABLED` 使用独立事务保存拒绝审计；不向调用方返回 traceback。
