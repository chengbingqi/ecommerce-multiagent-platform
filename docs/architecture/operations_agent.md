# 运营分析 Agent 架构

独立的 `operations` LangGraph 不与客服 Agent 自由通信；直接运行时必须显式提交 `agent_type=operations`。

## 节点与条件边

固定节点依次为：`validate_request`、`resolve_principal`、`normalize_store_and_period`、`classify_operations_topic`、`plan_allowed_tools`、`execute_operations_tools`、`validate_metric_evidence`、`detect_deterministic_anomalies`、`retrieve_relevant_sop`、`generate_grounded_explanation`、`validate_final_output`、`finalize_result`。

权限或永久错误会跳过后续工具和 LLM；只读 MCP 暂时性错误最多重试一次；SOP 部分失败保留确定性指标并产生 warning；模型超时、非法结构、修改数值或伪造引用时使用安全模板并设置 `need_human`。图受 `AGENT_MAX_STEPS`、`AGENT_DEADLINE_SECONDS` 和 LangGraph recursion limit 约束。

## 信任边界

- 服务端从 `principal_id` 解析 role/store scope，客户端不能声明或扩大权限。
- 工具白名单仅为 `get_operations_metrics` 与 `retrieve_sop`。
- SQL 只存在于隔离测试 Oracle；Agent、Prompt、MCP 入参与 API 均不接受 SQL。
- 指标和异常由确定性代码生成；LLM 只组织解释与建议。
- `validate_final_output` 对模型回显的全部指标做精确相等比较，并验证 anomaly/evidence 引用。
- 运行复用 `AgentRun`、`AgentEvent`、`AgentCheckpointLink` 和 PostgreSQL checkpointer；运营流程不创建 Approval、IdempotencyRecord 或业务写入。

## 持久化

运营图复用平台运行、事件和 checkpoint 表。每个 run、thread、trace 和 Principal 稳定关联，完成结果及证据可在 API 进程重启后查询。
