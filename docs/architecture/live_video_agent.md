# 直播切片 Agent 架构

`live_video` 专业子图不调用客服或运营工具。独立运行时由客户端显式创建媒体任务；经主控调用时也必须有明确媒体意图和合法资产。

图节点为 validate_request → resolve_principal → authorize_asset → inspect_asset → create_pipeline_task → enqueue_media_worker → interrupt_while_processing → resume_from_task_result → validate_candidates_and_qc → await_human_review → finalize_result。

PostgreSQL checkpointer 在任务入队前建立，State 只保存 run/thread/trace、asset/task、pipeline 版本、candidate/evidence 引用、QC 和审核状态，不保存视频、完整转写、路径、object key 或秘密。worker 完成后使用同一 thread/checkpoint 恢复。

权限由服务端 Principal、owner 和 store_scope 决定。输入只有 asset_id、商品关键词、片长、候选数和字幕语言，不能扩大工具白名单或提供 SQL、Shell、URL、文件路径、Docker Socket、FFmpeg 参数。
