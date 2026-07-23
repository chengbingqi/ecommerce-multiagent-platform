# 项目路线图（阶段0—阶段8）

状态说明：阶段0工程门禁为 `READY_FOR_STAGE_1`；阶段1至阶段4为 `COMPLETED`；阶段5为 `PARTIAL / FROZEN`；阶段6为 `COMPLETED_WITH_LIMITATIONS`；阶段7为 `PARTIAL / READY_FOR_EXTERNAL_GATE`；阶段8为 `NOT_STARTED`；公网发布为`BLOCKED`。后续阶段不得因路线图存在而视为已完成或获准执行。

## 阶段0：部署门禁、事实核查与项目基线

- 目标：形成主机、网络、权限、资源和部署安全基线。
- 范围：只读核查、ADR、路线图、审计报告和可重复采集脚本。
- 完成门槛：CPU、内存、磁盘、GPU、Docker、端口和双用户风险均明确；公网、域名、共享授权、LLM、ASR、备份均有结论；无未处理阻断项。
- 当前状态：工程开发 `READY_FOR_STAGE_1`；公网发布 `BLOCKED_UNTIL_STAGE_7`。

## 阶段1：工程骨架与开发基线

- 目标：建立 React/Vite/TypeScript 前端、FastAPI、PostgreSQL、Redis、三层 Compose、Caddy 与本地同源 HTTPS 的工程骨架。
- 范围：状态页、健康检查、配置契约、SQLAlchemy/Alembic 基础、质量工具和开发/生产配置分离；不得公网部署，不包含 Agent、MCP、Worker 或其他后续阶段能力。
- 完成门槛：代码质量、单元测试、前端构建、Compose 安全约束和本机 HTTP/HTTPS 运行冒烟全部通过。
- 当前状态：COMPLETED；静态质量、单元测试、前端构建、容器健康、同源 HTTP/本地 HTTPS、依赖故障恢复和配置安全检查全部通过。

## 阶段2：模拟业务数据与 MCP 工具中台

- 目标：建立可重复的固定模拟业务世界，以及 business、knowledge、media 三个内部 MCP 服务和工具治理内核。
- 范围：PostgreSQL 模拟数据、迁移、证据引用、服务端权限、工具审计、审批/幂等基础和 Streamable HTTP；不使用 Qdrant、MinIO 或真实媒体流水线。
- 完成门槛：确定性 checksum、迁移回滚、工具契约、权限负向、审计脱敏、内部容器调用和阶段1回归全部通过。
- 当前状态：COMPLETED；revision `20260718_01`、manifest `stage2_base_v1` 和 MCP 工具契约 `1.0.0` 已验收。

## 阶段3：客服 Agent

- 目标：基于阶段2稳定工具契约实现独立、受控的 AI 客服 Agent。
- 范围：订单、物流、当前政策、退款资格、转人工和模拟退款审批；使用客服专属 LangGraph 子图与 PostgreSQL checkpointer，不实现平台主控或运营能力。
- 完成门槛：正向、越权、无答案、提示注入、证据引用、审批中断与同 thread 恢复端到端测试通过。
- 当前状态：COMPLETED；独立客服 LangGraph、PostgreSQL checkpointer、MCP 证据、权限/注入防护、模拟退款审批恢复和受控 DeepSeek 冒烟均已验收；阶段3补充验收的30个稳定独立case全部通过。

## 阶段4：运营 Agent

- 目标：交付受控的运营分析 Agent。
- 范围：确定性指标、异常规则、证据和看板；数值只来自阶段2工具和可追溯数据。
- 完成门槛：指标与 SQL 一致率 100%，固定异常场景可解释且权限隔离通过。
- 当前状态：COMPLETED；独立运营 LangGraph、只读 `get_operations_metrics@1.1.0`、版本化指标/异常、证据约束、隔离 SQL Oracle、运营看板和受控 DeepSeek 冒烟已验收。阶段4固定集为34个不同case，SQL比较70/70，阶段1/2/3回归通过；没有实现主控或后续阶段能力。

## 阶段5：直播 Agent

- 目标：交付直播切片 Agent 和受控媒体流水线。
- 范围：MinIO、Celery、ASR、镜头、评分、渲染和质量检查；媒体并发与 GPU 使用受明确资源门禁约束。
- 完成门槛：受支持测试视频全流程通过，资源限制、失败恢复和人工复核有效。
- 当前状态：`PARTIAL / FROZEN`；直播切片固定为`EXPERIMENTAL / HUMAN_REVIEW_REQUIRED`，不得自动发布，也不参与默认复合任务。正式媒体Worker固定使用`Systran/faster-whisper-large-v3@edaa852ec7e145841d8ffdb056a99866b5f0a478`、GPU FP16、宿主机物理GPU 1、离线只读模型和并发1。阶段6没有修改ASR、分段、评分、字幕烧录、FFmpeg、GPU、MinIO或任何阶段5数据。

## 阶段6：多 Agent 协同

- 目标：建立主控路由、复合子任务和三个专业 Agent 的受控协同。
- 范围：主控 Orchestrator、结构化跨 Agent 摘要、结果汇总和 checkpoint；不允许专业 Agent 任意互发自然语言消息。
- 完成门槛：协同用例完成、无无限循环、重启可恢复且工具权限不能被扩大。
- 当前状态：`COMPLETED_WITH_LIMITATIONS`。独立主控图、严格路由/计划Schema、最多3个子任务、最大1跳、专业工具白名单、父子任务表、PostgreSQL checkpoint、审批同thread恢复、`PARTIAL_SUCCESS`、运行事件/SSE、显式异步媒体调度和统一工作台已实现。固定32例为32/32通过，路由/可执行计划/权限拦截/可追踪均为100%，失控循环0；API重启、审批幂等、SSE续流和成功结果保留已在开发栈验证。限制：直播切片仍实验性并必须人工审核；正式认证、RBAC、外部限流和公网发布属于阶段7。

## 阶段7：安全与公网

- 目标：完成正式认证、RBAC、限流、秘密、安全头、备份和受控公网入口。
- 范围：公网/域名条件满足后才启用 Cloudflare Tunnel 或批准的等价方案；公网仅允许 edge 的 80/443。
- 完成门槛：负向安全、备份恢复、共享主机和外部入口门禁全部通过后才公布域名。
- 当前状态：`PARTIAL / READY_FOR_EXTERNAL_GATE`。本地认证/Session/CSRF/RBAC、Redis限流、媒体外围安全、生产Secrets/Compose/Caddy模板、备份恢复工具和58例固定安全集已实现；53例本地门禁通过，5例因真实域名/Tunnel、秘密轮换、异机备份和外部网络验收条件缺失而阻断。生产与公网均未启动。

## 阶段8：评测交付

- 目标：形成固定评测、复现资料、演示和最终交付。
- 范围：逐用例评测结果、README、演示视频、故障演示和新环境复现脚本。
- 完成门槛：新环境能按文档启动并重跑评测，目标值与实测值严格区分。
- 当前状态：NOT_STARTED。
