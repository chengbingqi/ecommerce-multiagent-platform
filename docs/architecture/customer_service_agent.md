# 客服 Agent 架构

独立 `customer_service_graph` 由 10 个职责单一的节点组成：输入验证、主体解析、意图分类、客服白名单规划、MCP 执行、证据验证、grounded 回答、风险评估、审批/转人工、结果持久化。

条件边在错误、未知意图、缺少证据和正常路径之间路由。`AGENT_MAX_STEPS`、`AGENT_DEADLINE_SECONDS` 和 LangGraph recursion limit 共同阻止无限循环。只读 MCP 暂时性错误最多重试一次；权限、参数和业务错误不重试；副作用工具不盲目重试。

客服白名单固定为：`get_order`、`get_logistics`、`search_policy`、`get_document_chunk`、`check_refund_eligibility`、`create_refund_request`。禁止销售指标、运营库存分析、media、SQL、Shell、任意 HTTP/文件路径和 Docker Socket。用户文本、MCP 内容和知识内容均视为不可信数据，不能改变白名单或审批状态。

订单、物流、政策、退款资格和模拟退款结果必须来自 MCP 工具。模型仅分类、提取参数和组织回答；服务端校验每条 claim 的 `evidence_ref`。无有效证据、引用越界或模型与工具冲突时，拒绝模型事实并以确定性工具结果或转人工为准。

运行元数据存入 `agent_runs`、`customer_conversations`、`agent_events` 和 `agent_checkpoint_links`；LangGraph checkpoint 使用 PostgreSQL `AsyncPostgresSaver`。checkpoint 只保存必要摘要和引用，不保存 Key、Authorization 或大体量原始工具结果。
