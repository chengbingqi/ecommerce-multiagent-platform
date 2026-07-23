# 阶段6 API契约

## 创建统一运行

`POST /api/v1/runs`新增`agent_type=orchestrator`分支；原客服、运营和直播接口保持兼容。

```json
{
  "agent_type": "orchestrator",
  "request_text": "查询订单物流并分析店铺经营指标",
  "principal_id": "development-principal",
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

所有字段使用`extra=forbid`严格校验。development/test继续允许既有`principal_id`输入；production拒绝该方式，正式身份属于阶段7。

## 结构化响应

`OrchestratorOutput`包含`run_id/thread_id/trace_id`、`overall_status`、route、plan、subtasks、三个专业结果、`evidence_refs`、`partial_failures`、warnings、`need_human`、completion rule、error和versions。媒体结果固定显示实验性、人工审核和禁止自动发布标记。

## 查询、事件、取消与审批

- `GET /api/v1/runs/{run_id}?principal_id=...`：兼容查询Orchestrator、客服、运营和媒体run。
- `GET /api/v1/runs/{run_id}/events?principal_id=...&after_event_id=...`：结构化事件。
- `GET /api/v1/runs/{run_id}/events/stream?principal_id=...`：SSE，支持`Last-Event-ID`。
- `POST /api/v1/runs/{run_id}/cancel?principal_id=...`：仅取消尚未执行的主控run。
- `POST /api/v1/approvals/{approval_id}/decision`：保持原客服响应；若child属于Orchestrator，同时恢复对应父run。

所有接口继续执行现有主体、所有权和store scope检查。事件和响应不包含完整原始工具输出、秘密或模型思维过程。

## 兼容性

以下既有能力未删除：客服/运营直接运行、Stage5上传/任务/SSE/审核/下载、媒体取消、审核通过/驳回、独立SRT下载。统一工作台默认使用Orchestrator，原运营和直播页面保留。
