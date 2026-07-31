# 直播切片工作台

本地页面为 `/media`，保留状态页和运营页。页面通过同源 `/api/v1` 流式上传，创建任务后保存 task_id；刷新时先读取 REST 快照，再连接持久 SSE 事件。

页面展示资产状态、来源视频名称、fixture标记、处理阶段、转写时间轴、候选起止、播放器、确定性总分和子分数、QC、字幕入口，以及受控下载。正式队列只显示`human_review_eligible=true`候选；纯色自动化fixture位于独立只读入口，不显示APPROVED/REJECTED/ADJUSTED操作。浏览器不会收到MinIO地址、Console、凭据、object key或宿主机路径。

production 使用服务端 Session；development/test 的显式 Principal 只用于本地评测。页面明确提示结果不会发布到真实直播平台，只有通过 QC 且当前用户有权访问的候选才能进入人工审核。

上传时必须声明“本人拥有或已获授权的真实素材”或“具有电商语义的模拟直播素材”。前者保存为`user_provided`，后者保存为`semantic_simulation`；两者均属于`human_quality`。页面不提供把纯色自动化fixture标成正式质量样本的选项。
