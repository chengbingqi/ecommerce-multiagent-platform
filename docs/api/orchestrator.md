# 多 Agent 主控 API 契约

## 创建统一运行

`POST /api/v1/runs` 使用 `agent_type=orchestrator` 创建复合任务；客服、运营和媒体的直接运行接口保持兼容。

```json
{
  "agent_type": "orchestrator",
  "request_text": "查询订单物流并分析店铺经营指标",
  "run_id": "run-unique-id",
  "thread_id": "thread-unique-id",
  "trace_id": "trace-unique-id",
  "order_id": "optional-order-id",
  "store_id": "optional-store-id",
  "start_date": "2026-06-24",
  "end_date": "2026-07-01",
  "topic": "OVERVIEW",
  "asset_id": null,
  "product_keywords": []
}
```

所有字段使用 `extra=forbid` 严格校验。身份来自服务端 Session；development/test 可显式传入测试 Principal，production 会拒绝该方式。

## 结构化响应

`OrchestratorOutput` 包含 run/thread/trace ID、`overall_status`、route、plan、subtasks、三个专业结果、`evidence_refs`、`partial_failures`、warnings、`need_human`、completion rule、error 和 versions。媒体结果固定显示实验性、人工审核和禁止自动发布标记。

## 查询、事件、取消与审批

- `GET /api/v1/runs/{run_id}`：查询主控、客服、运营或媒体运行。
- `GET /api/v1/runs/{run_id}/events?after_event_id=...`：读取结构化事件。
- `GET /api/v1/runs/{run_id}/events/stream`：SSE，支持 `Last-Event-ID`。
- `POST /api/v1/runs/{run_id}/cancel`：仅取消尚未执行的主控运行。
- `POST /api/v1/approvals/{approval_id}/decision`：审批客服写操作；若该运行属于主控，同时恢复父运行。

所有接口执行身份、所有权和 store scope 检查。事件与响应不包含完整原始工具输出、Secret 或模型隐藏思维。

## 兼容性

客服和运营直接运行、媒体上传/任务/SSE/审核/下载、媒体取消及独立 SRT 下载继续可用。统一工作台默认使用 Orchestrator，运营与媒体独立页面仍保留。
