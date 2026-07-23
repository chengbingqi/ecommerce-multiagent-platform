# 阶段5受控媒体流水线

## 版本与边界

- 流水线：`media_pipeline_v1.1.0`
- 商品分段：`product_segmentation_v1.0.0`
- 中文ASR配置：`asr_config_zh_v1.1.0`
- 高光评分：`highlight_score_v1.0.0`
- 质量检查：`media_qc_v1.0.0`
- media MCP：`1.1.0`，保留阶段2 `1.0.0` 的 FEATURE_NOT_ENABLED 行为

API 只完成流式上传、权限检查、任务登记和入队。Celery `media` 队列的 `worker-media` 以并发 1 执行长任务。任务消息只含 `asset_id`、`media_task_id`、`run_id` 和 `pipeline_version`。

流水线阶段依次为 probe、extract_audio、transcribe、detect_scenes、build_candidates、score_highlights、render_clips、generate_subtitles、run_qc、persist_outputs 和 await_human_review。候选由绝对时间原子utterance按SKU/价格口播、中文转场、停顿和有限scene/VAD吸附形成可变长商品区间，不再按固定4/8秒padding。每个阶段写入持久化事件；PostgreSQL 是状态真值，Redis 只作 broker。

## 安全执行

FFmpeg/ffprobe 只在媒体实现中以参数数组启动，关闭 stdin，设置超时，不使用 `shell=True`。用户不能提交命令、滤镜、宿主机路径或 object key。工作目录必须是 `DATA_ROOT` 下 `temp/media-stage5/tasks/<task_id>` 的直属任务目录，结束时只清理该目录。

## 恢复与幂等

上传、任务和最终对象均使用服务端校验的幂等键。重复投递已成功任务直接返回已有结果；取消在阶段边界检查。事件包含稳定序号，SSE 支持 `Last-Event-ID`，刷新后应先读取 REST 快照再续接事件。

Celery prefork子进程会连续处理多个同步delivery；每个delivery通过`asyncio.run`建立独立事件循环，并在该循环关闭前dispose缓存的SQLAlchemy async engine，避免asyncpg连接跨事件循环复用。该行为有连续delivery回归测试和真实worker连续任务证据。

## 输出与 QC

候选保存MP4、SRT、缩略图、原始/吸附/最终边界、utterance证据、SHA-256和评分。字幕时间由绝对ASR时间减候选start得到片内相对时间。Adjust会从受控原片重新渲染视频/字幕/缩略图并重新QC，不再只更新数据库时间。未通过QC的候选不得被批准；自动化fixture不得进入正式人工审核。

2026-07-19固定ASR模型只读离线加载及10个纯色fixture自动化全链路10/10。2026-07-20在1段获授权开发视频和20段冻结真值上运行v1.1.0：20/20候选渲染/QC，区间Precision/Recall 95%，但中文CER 62.30%。保留验证视频仍为0/2，阶段5保持PARTIAL。
