# 认证、会话与 RBAC

## 会话

- 登录成功始终生成新的随机Session ID，防止session fixation。
- Cookie生产名为`__Host-ecom_session`，设置`Secure`、`HttpOnly`、`SameSite=Lax`、`Path=/`且不设置Domain。
- 默认空闲过期30分钟，绝对过期8小时。
- 退出、密码修改、管理员修改角色或store scope后，服务端立即撤销会话。
- 登录错误统一返回“用户名或密码错误”，连续5次失败默认锁定15分钟。
- CSRF Token与Session绑定，只通过登录/独立CSRF响应返回，并由前端放入`X-CSRF-Token`；不进入URL、日志或SSE。

production 同时要求 `APP_ENV=production`、`ENABLE_DEV_PRINCIPAL_INPUT=false` 和 `ENFORCE_STAGE7_SECURITY=true`。客户端在请求体、查询或 Header 中自报 `principal_id` 会被拒绝；development/test 保留显式测试 Principal 方式。

## 权限矩阵

| 角色 | 允许范围 | 明确禁止 |
| --- | --- | --- |
| 匿名 | 静态项目说明、`/health/live`、登录 | 工作台、模型、订单、运营、媒体、审批、管理 |
| customer | 自己的模拟订单、物流、客服run | 运营、媒体、审批、管理、跨客户订单 |
| store_operator | 自身store的只读运营指标/run | 客服写动作、媒体、跨store、审批、管理 |
| content_operator | 自身owner/store的上传、媒体任务、候选、审核、下载 | 跨owner/store、自动发布、真值/边界调整 |
| approver | 明确授权store范围内、未过期且digest一致的一次性审批 | 跨store、普通工作台管理、评测 |
| admin | 用户授权、配额及必要管理操作 | 仍受审计、CSRF、限流和媒体禁止自动发布约束 |

对象归属由服务端针对`run_id/thread_id/subtask_id/order_id/store_id/approval_id/asset_id/task_id/candidate_id/download/SSE`检查。对象越权返回403或不泄露存在性的404。前端隐藏按钮不是授权机制。

管理员初始化脚本`backend/scripts/bootstrap_admin.py`只从权限0600的文件或隐藏输入读取密码，不接受密码命令行参数。
