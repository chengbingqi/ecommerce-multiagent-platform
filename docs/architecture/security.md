# 安全架构

安全边界位于业务图之外，不改变 Orchestrator 输出、checkpoint、SSE 或审批恢复语义。

```text
客户端 -> 受信任边缘代理 -> FastAPI 安全中间件
                              |
                    Session -> Principal -> RBAC / 对象范围
                              |
                    Orchestrator / 专业 Agent / 异步媒体任务
```

## 网络边界

- edge 是唯一对外应用入口；API、PostgreSQL、Redis、MinIO、MCP 和 Worker 仅位于内部网络。
- 反向代理负责 Host、请求体、隐藏路径、安全响应头和 SSE 缓冲策略。
- API 只信任显式配置的代理网段，客户端伪造的转发头不能直接成为可信来源地址。
- 外部模型访问与数据服务网络分离；容器不挂载 Docker Socket。
- 公网部署不是仓库默认运行模式，域名、证书和入口凭据必须在具体环境中单独配置和验收。

## 身份与应用安全

- `AuthUser` 负责登录身份，并以一对一关系复用 `Principal` 授权身份。
- 密码使用 Argon2id；数据库只保存 Session 和 CSRF Token 的 HMAC-SHA256 摘要。
- Session 具有空闲/绝对过期、身份版本和撤销状态；Cookie 写请求统一校验 CSRF 与 Origin/Referer。
- Trusted Host、显式 CORS 白名单和可信代理 CIDR 共同限定入口边界。
- Redis 原子限流和并发租约覆盖登录、API、run、模型预算、SSE、上传、媒体、审批和下载；高成本入口在 Redis 故障时 fail closed。
- 所有 run、订单、店铺、审批、资产、任务、候选、下载和 SSE 都在服务端做所有权或 scope 校验。

生产配置通过 Secret 文件注入敏感值，不把凭据写入环境示例、前端变量、文档或日志。详细权限见[认证与 RBAC](../security/auth_rbac.md)，验证方法见[安全评测](../evaluation/security.md)。
