# Stage7安全架构

## 边界

Stage7在Stage6业务图外建立统一安全边界，不改变Orchestrator输出、父子任务、checkpoint、SSE、审批恢复或`PARTIAL_SUCCESS`。Stage5算法、ASR、FFmpeg、GPU和媒体评分均未修改。

生产请求链路为：

```text
真实客户端 → Cloudflare HTTPS/Tunnel → edge(Caddy) → FastAPI安全中间件
                                           ↓
                         不透明Session → Principal → RBAC/对象范围
                                           ↓
                    Orchestrator/专业Agent/媒体异步任务
```

Cloudflare Tunnel是唯一计划公网路线。`cloudflared`仅连接`tunnel_net`并只能访问edge；edge通过`app_net`访问API；API通过`data_net`访问所需数据服务，并单独加入`api_egress`访问外部模型API。PostgreSQL、Redis、MinIO、MCP和Worker只加入内部`data_net`。生产合并配置不发布任何宿主机端口，公网流量必须经过Tunnel。

## 安全组件

- `AuthUser`只负责登录身份，并以一对一关系复用现有`Principal`授权身份。
- 密码使用Argon2id；数据库不存明文密码。
- `AuthSession`只存Session和CSRF Token的HMAC-SHA256摘要，带空闲/绝对过期、身份版本和撤销状态。
- `TrustedHostMiddleware`、显式CORS白名单、可信代理CIDR和Origin/Referer共同限定反向代理边界。
- Cookie鉴权的写请求由CSRF中间件统一校验。
- Redis Lua固定窗口和有租约的ZSET并发计数覆盖通用API、登录、run、模型预算、SSE、上传、媒体、审批和下载。
- Redis故障时，高成本入口返回结构化503，不无限放行。
- Caddy限制Host、隐藏路径、API请求体、上传请求体和SSE代理缓冲，并设置CSP等响应头。

## 生产状态门

当前仅完成本地代码、配置和测试，未启用Tunnel。当前Caddy配置没有启用HSTS；只有真实HTTPS和SSE连续稳定验证后，才能提出启用建议并再经用户确认。
