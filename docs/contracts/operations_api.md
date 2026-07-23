# 运营分析 API 契约

## 创建运行

`POST /api/v1/runs`

请求必须显式包含：`agent_type=operations`、`store_id`、`start_date`、`end_date`、`topic`、`granularity`、`comparison_mode`、`principal_id`、`run_id`、`thread_id`、`trace_id`。日期采用 UTC 的 `[start,end)` 语义，`start_date < end_date`，最长 90 天。

topic 支持 `OVERVIEW`、`SALES`、`INVENTORY`、`REFUNDS`、`CONVERSION`、`LOGISTICS`、`CAMPAIGN`、`UNKNOWN` 与 `FORBIDDEN_REQUEST`。

响应包含 run/thread/trace/status、store/period/granularity、三个版本号、metrics、trend_series、anomalies、evidence_rows、explanation、recommendations、no_data、warnings、need_human 和结构化 error。事实、规则异常、模型解释及建议是独立字段。

## 查询运行

`GET /api/v1/runs/{run_id}?principal_id=...`

只允许运行所有者或 admin 查询。完成结果从 PostgreSQL checkpoint 恢复；跨用户返回稳定 `FORBIDDEN`，未知运行返回 `NOT_FOUND`。

development/test 可使用 `principal_id` 做本地验收，这不是认证。production 中 `ENABLE_DEV_PRINCIPAL_INPUT=false`，正式认证属于阶段7。

## 稳定版本

- 指标：`operations_metrics_v1.0.0`
- 异常规则：`operations_rules_v1.0.0`
- Prompt：`operations_analysis_v1.0.0`
- MCP：既有工具保持 `1.0.0`；新增只读 `get_operations_metrics` 为 `1.1.0`
