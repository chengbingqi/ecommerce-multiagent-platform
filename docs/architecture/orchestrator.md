# 多 Agent 主控架构

Orchestrator 负责结构化路由、子任务编排和结果汇总，不把三个专业 Agent 改造成可以自由互聊的网络。调用方向固定为：

```text
Orchestrator -> customer_service | operations | media -> Orchestrator
```

专业 Agent 不能创建下级 Agent、互相调用或回调主控。主控不执行 SQL、Shell、FFmpeg、文件系统、Docker 或任意 HTTP，也不持有专业工具权限。跨 Agent 只传递通过 Pydantic 校验的摘要、必要业务字段、状态和 `evidence_ref`。

## 执行图

1. `validate_request`：校验输入、控制字符和提示注入风险。
2. `resolve_principal`：从服务端会话解析 Principal、对象所有权和店铺范围。
3. `classify_intents`：生成严格结构化路由；模型失败时使用确定性安全降级。
4. `validate_required_inputs`：在创建子任务前返回缺参或不支持错误。
5. `build_structured_plan`：按稳定顺序生成父子关联、超时和重试策略。
6. `validate_plan_and_tool_scopes`：校验目标、数量、依赖环、跳数和白名单。
7. `create_subtasks`：按父运行与计划摘要幂等持久化。
8. `dispatch_ready_subtasks`：调用专业适配层，只对暂时性错误有限重试。
9. `collect_subtask_results`：收集状态、证据、审批和失败，不复制原始工具输出。
10. `resolve_waiting_or_partial_state`：处理中断审批和部分成功。
11. `aggregate_grounded_result`：保留每个成功结果、失败和冲突警告。
12. `validate_final_result` 与 `finalize_run`：确定最终状态、保存输出并追加事件。

## 硬限制与适配器

- 目标仅允许 `customer_service`、`operations`、`media`。
- 最多 3 个子任务、1 跳和 16 个主控步骤。
- 总 deadline 55 秒，默认单子任务超时 20 秒。
- 只有 `TIMEOUT`、`TRANSIENT_ERROR`、`MCP_UNAVAILABLE` 可重试一次；媒体创建和其他副作用不盲目重试。
- 客服沿用订单、物流、退款资格、政策和受审批退款工具；运营只使用 `get_operations_metrics` 与 `retrieve_sop`；媒体只创建受控异步任务。
- 仅在用户明确请求切片并提供合法 `asset_id` 时规划媒体任务。

媒体适配器的输出始终包含 `experimental=true`、`human_review_required=true` 和 `auto_publish=false`。

## 状态与持久化

主控复用 `AgentRun`、`AgentEvent`、`AgentCheckpointLink`、`Approval`、`MediaTask` 和 LangGraph `AsyncPostgresSaver`，并使用 `orchestrator_subtasks` 保存父子运行关系、依赖、受限输入摘要、输出引用、证据、工具范围、重试和审批状态。唯一约束 `(parent_run_id, idempotency_key)` 防止恢复时重复创建子任务。

```text
PENDING -> RUNNING -> SUCCEEDED
                  -> PARTIAL_SUCCESS
                  -> FAILED
                  -> WAITING_APPROVAL -> RUNNING -> SUCCEEDED | FAILED
PENDING -> CANCELLED
```

checkpoint 不保存完整工具输出、大段 ASR、媒体文件、Secret 或模型隐藏思维。已完成输出、计划、子任务和事件均以 PostgreSQL 为恢复真值；SSE 通过不透明事件 ID 和 `Last-Event-ID` 续流。

客服退款审批会绑定父运行、客服子任务、原 `thread_id`、`approval_id` 和 `action_digest`。审批后先恢复同一客服 checkpoint，再恢复同一主控 checkpoint；原有审批和退款幂等约束负责拦截重复执行。

## 汇总语义

- 全部必需子任务失败：`FAILED`。
- 至少一个成功且至少一个失败：`PARTIAL_SUCCESS`，成功输出不会被覆盖。
- 客服等待审批：`WAITING_APPROVAL`。
- 单独媒体任务创建成功：调度状态为 `SUCCEEDED`，内容仍待人工审核。
- 媒体参与复合任务并等待审核：主控为 `PARTIAL_SUCCESS`，其他专业结果保留。

只有确定性工具结果能够提供订单、政策、指标或媒体证据；主控只汇总事实，不生成业务事实。
