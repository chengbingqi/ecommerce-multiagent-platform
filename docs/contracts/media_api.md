# 阶段5媒体 API 契约

全部接口位于同源 `/api/v1`，正式认证留待阶段7；development/test 使用的 `principal_id` 只用于本地验收。

- `POST /media/uploads`：登记上传与幂等键。
- `PUT /media/uploads/{upload_id}`：流式写入请求体。
- `POST /media/uploads/{upload_id}/complete`：ffprobe 校验、SHA-256、隔离/READY。
- `GET /media/{asset_id}`、`GET /media/{asset_id}/content`：受控资产元数据和媒体代理。
- `POST /media/{asset_id}/clip-tasks`：创建独立 live_video run 并立即返回 202。
- `GET /tasks/{task_id}`：任务快照。
- `GET /tasks/{task_id}/events`、`GET /tasks/{task_id}/events/stream`：持久事件和支持 Last-Event-ID 的 SSE。
- `POST /tasks/{task_id}/cancel`：请求取消。
- `GET /tasks/{task_id}/transcript`、`GET /tasks/{task_id}/clips`：最小转写与候选结果。
- `GET /media/reviews/pending`：只列出当前 principal 有权访问且自动 QC 通过的待审候选。
- `GET /media/clips/{clip_id}/download?kind=video|subtitle|thumbnail`：权限代理下载。
- `POST /media/clips/{clip_id}/reviews`：APPROVED、REJECTED 或带合法时间窗的 ADJUSTED。

响应模型不包含 object key、MinIO 地址、凭据和宿主机路径。状态和事件以 PostgreSQL 为准；API 不同步执行完整媒体流水线。
