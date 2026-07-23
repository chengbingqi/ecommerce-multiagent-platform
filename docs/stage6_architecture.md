# 阶段6多Agent协同架构

## 边界

阶段6新增独立Orchestrator，不把客服图、运营图或直播图改造成主控。调用方向固定为：

```text
Orchestrator -> customer_service | operations | media -> Orchestrator
```

专业Agent不能创建Agent子任务、不能互调，也不能回调主控。主控不执行SQL、Shell、FFmpeg、文件系统、Docker或任意HTTP，不持有专业工具能力；跨Agent只保存Pydantic校验后的结果摘要、必要业务字段、状态和`evidence_ref`。

## 主图节点

1. `validate_request`：控制字符、提示注入和请求基本约束。
2. `resolve_principal`：现有开发主体、所有权与store scope。
3. `classify_intents`：严格结构化路由；模型异常时确定性安全降级。
4. `validate_required_inputs`：在创建子任务前返回缺参或不支持错误。
5. `build_structured_plan`：稳定排序和哈希生成父子关联、Schema、timeout和重试策略。
6. `validate_plan_and_tool_scopes`：目标、数量、依赖环、最大hop和白名单硬校验。
7. `create_subtasks`：按父run和计划摘要幂等持久化。
8. `dispatch_ready_subtasks`：依次调用专业适配层；仅暂时性错误有限重试。
9. `collect_subtask_results`：收集状态、证据、审批和失败，不复制原始工具输出。
10. `resolve_waiting_or_partial_state`：保存审批中断并恢复对应客服子任务。
11. `aggregate_grounded_result`：保留每个成功结果、失败和冲突警告。
12. `validate_final_result`：确定性计算最终状态。
13. `finalize_run`：保存输出并追加运行事件。

## 路由与计划硬限制

- `agent_targets`仅允许`customer_service`、`operations`、`media`。
- `max_subtasks=3`，`max_agent_hops=1`，`max_steps=16`。
- 总deadline 55秒，默认单子任务timeout 20秒。
- 只有`TIMEOUT`、`TRANSIENT_ERROR`、`MCP_UNAVAILABLE`可重试一次。
- 媒体创建和其他副作用不盲目重试。
- 依赖必须存在且无环；父run不能等于child run。
- 子任务ID、child run/thread和幂等键由父run、Agent类型与计划版本稳定派生。

工具scope固定为：

- 客服：沿用阶段3的订单、物流、退款资格、政策检索和受审批退款工具。
- 运营：仅`get_operations_metrics`与`retrieve_sop`。
- 媒体：仅`create_existing_media_task`适配动作，不在API/LangGraph线程执行ASR或FFmpeg。

## 三个适配器

客服适配器调用既有`CustomerServiceAgentService`，保留原MCP白名单、证据、审批digest、幂等与checkpoint。运营适配器调用既有只读`OperationsAgentService`，模型不能重写确定性指标。媒体适配器调用既有`LiveVideoAgentService`创建Celery异步任务，输出始终包含：

```json
{
  "experimental": true,
  "human_review_required": true,
  "auto_publish": false,
  "pipeline_version": "media_pipeline_v1.2.1"
}
```

媒体只有在用户明确请求切片且提供合法`asset_id`时进入计划；普通客服/运营请求不会推断媒体目标。

## 汇总语义

- 全部必需子任务失败：`FAILED`。
- 至少一个成功、至少一个失败：`PARTIAL_SUCCESS`。
- 客服等待审批：父run为`WAITING_APPROVAL`。
- 单独媒体成功创建任务：调度为`SUCCEEDED`，内容仍待人工审核。
- 媒体参与复合请求且等待审核：父run为`PARTIAL_SUCCESS`，其他结果保留。
- 只有确定性工具结果可以提供订单、政策、指标或媒体证据；主控不生成这些事实。

前端只展示路由结论、节点事件、结构化结果、证据和错误码，不存储或展示模型隐藏思维过程。
