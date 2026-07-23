# Customer Service Agent State

状态中的稳定标识为 `run_id`、`thread_id`、`trace_id` 和服务端解析的 `principal_id`。输入字段包括受长度限制的 `message` 和可选 `order_id`。

执行字段包括 `intent`、`role`、`store_id`、`customer_ref`、`planned_tools`、精简 `tool_results`、`evidence`、`step_count`、`started_monotonic` 和 `llm_calls`。输出字段包括 `answer`、`claims`、`order_summary`、`refund_eligibility`、`risk`、`need_human`、`handoff_reason`、`approval`、`error` 和 `status`。

状态终态为 `SUCCEEDED`、`FAILED` 或 `CANCELLED`；退款提交在 `WAITING_APPROVAL` 中断。恢复必须使用原 `thread_id` 和 PostgreSQL checkpoint，不能用新的会话代替。业务运行状态还支持 `PENDING` 和 `RUNNING`。

Agent State 不保存数据库完整行、电话、地址、密码、Token、API Key、Authorization header 或任意文件内容。MCP 原始返回会缩减为业务字段摘要和可反查的 `evidence_ref`。
