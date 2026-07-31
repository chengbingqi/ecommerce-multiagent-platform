# 受控媒体流水线

当前流水线版本为 `media_pipeline_v1.2.1`。媒体能力属于实验功能，所有候选必须人工审核，不能自动发布。

## 处理流程

API 负责流式上传、权限检查、任务登记和入队；Celery `media` 队列以并发 1 执行长任务。任务消息只携带资产、任务、运行和流水线版本标识。

流水线依次执行 probe、extract_audio、transcribe、detect_scenes、build_candidates、score_highlights、render_clips、generate_subtitles、run_qc、persist_outputs 和 await_human_review。每个阶段写入持久事件；PostgreSQL 是状态真值，Redis 仅作为 broker。

候选生成基于带绝对时间的 ASR utterance、商品实体、价格/规格口播、中文转场、停顿和有限的 scene/VAD 吸附。商品状态机区分进入、持续、离开、转场和多商品状态；无充分证据时返回“未识别商品”，不能用普通句子猜测标签。

## ASR 与媒体输出

- 实现：`faster-whisper==1.1.1`。
- 模型：`Systran/faster-whisper-large-v3`，固定 revision `edaa852ec7e145841d8ffdb056a99866b5f0a478`。
- 正式媒体 Worker 使用离线只读模型、CUDA FP16 和显式分配的一张 GPU；未分配 GPU 时不得启用该 Worker。
- 候选输出包含 MP4、UTF-8 SRT、缩略图、边界、utterance 证据、商品实体证据、SHA-256、评分和 QC。
- ASS 字幕成功烧录后才持久化最终视频；独立 SRT 仍可受控下载。

模型路径和 GPU 设备仅通过环境配置传入。仓库不记录宿主机路径、GPU UUID、模型文件或运行数据。

## 安全与恢复

FFmpeg/ffprobe 仅以参数数组执行，关闭 stdin，设置超时，不使用 `shell=True`。用户不能提交命令、滤镜、宿主机路径、URL 或 object key。工作目录必须位于配置的 `DATA_ROOT` 临时子目录中，且只清理当前任务目录。

上传、任务和最终对象均使用服务端校验的幂等键。重复投递已成功任务会返回既有结果；取消在阶段边界检查。事件具有稳定顺序，刷新后先读取 REST 快照，再用 SSE `Last-Event-ID` 续接。

未通过 QC 的候选不得批准；自动化 fixture 不得进入正式人工审核。人工调整会从受控原片重新渲染视频、字幕和缩略图并重新执行 QC。
