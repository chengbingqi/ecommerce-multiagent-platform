# MCP 工具契约

SDK 固定为官方 Python MCP `1.28.0`，传输为 stateless JSON Streamable HTTP，路径 `/mcp`。三个服务只在 Compose `backend` 内部网络可达，不经过 edge。

## 统一契约

每个 `ToolSpec` 固定包含 `tool_name`、`tool_version=1.0.0`、Pydantic `input_schema`、统一 `output_schema`、角色白名单、`side_effect`、超时、retry policy 和 error codes。官方 MCP `tools/list` 返回 wire schema，输入包装在 `request` 对象中。

统一结果字段为 `ok`、`data`、`evidence`、`error`、`meta`。`meta` 包含 tool/version、run/task/trace ID 和 latency；错误包含稳定 code、非敏感 message、retryable 和 trace ID。稳定错误码：

`INVALID_ARGUMENT`、`UNAUTHENTICATED`、`FORBIDDEN`、`NOT_FOUND`、`APPROVAL_REQUIRED`、`IDEMPOTENCY_CONFLICT`、`FEATURE_NOT_ENABLED`、`TIMEOUT`、`TRANSIENT_ERROR`、`INTERNAL_ERROR`。

## 工具清单

- business-mcp：`get_order`、`get_logistics`、`query_inventory`、`get_sales_metrics`、`check_refund_eligibility`、`create_refund_request`
- knowledge-mcp：`search_policy`、`retrieve_sop`、`get_document_chunk`
- media-mcp：`probe_media`、`submit_transcription`、`detect_scenes`、`score_highlights`、`render_clip`、`save_asset`

媒体工具的 `1.0.0` 契约保留兼容行为：除 `probe_media` 外返回并审计 `FEATURE_NOT_ENABLED`，不运行 FFmpeg/ASR/GPU，也不创建媒体文件。

`create_refund_request` 必须提供 `idempotency_key` 和匹配主体、工具、digest、有效期的 `approval_id`。重复 key+digest 返回原结果；key 相同而 digest 不同返回 `IDEMPOTENCY_CONFLICT`。它永远不会把状态伪装成真实退款成功。

证据格式使用 `db://table/id`、`doc://doc/version/chunk`、`sop://id/version` 或 `manifest://...`，可由服务端稳定反查。

## media-mcp `1.1.0` 兼容扩展

`1.1.0` 输入契约不改变 `1.0.0`：旧版 `probe_media` 继续读取固定 manifest，旧版其余媒体工具仍返回 `FEATURE_NOT_ENABLED`。新版工具只接收服务端资产/任务标识，不接收 URL、路径、object key、命令或凭据。

- `probe_media@1.1.0`：返回已授权资产的探测结果。
- `submit_transcription@1.1.0`：查询异步任务和 transcript_ref。
- `detect_scenes@1.1.0`：查询同一媒体任务的场景处理状态。
- `score_highlights@1.1.0`：查询确定性候选数量和处理状态。
- `render_clip@1.1.0`、`save_asset@1.1.0`：只验证/查询由受控 worker 执行的异步副作用，并要求 idempotency_key；不在 MCP 请求线程运行 FFmpeg、ASR 或上传对象。

新版权限白名单为 `content_operator`、`agent:media` 和 `admin`。服务端校验 owner/store_scope，拒绝发生在任务启动之前并进入工具审计。响应只包含公开状态、版本和 `media-asset://...` / `media-task://...` 证据，不返回 MinIO 凭据、内部地址或宿主机路径。
