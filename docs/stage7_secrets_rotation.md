# Stage7秘密轮换台账

本文件只记录状态和证据编号，不记录旧值、新值、部分字符或可猜测哈希。

| secret标识 | 状态 | 轮换时间 | 影响服务 | 旧凭据失效 | 证据编号 |
| --- | --- | --- | --- | --- | --- |
| DeepSeek API Key | PENDING_USER_ACTION | 未完成 | API | NOT_CONFIRMED | SEC-ROTATE-001 |
| MinIO Root用户名/密码 | PENDING_MAINTENANCE | 未完成 | MinIO管理 | NOT_CONFIRMED | SEC-ROTATE-002 |
| MinIO API最小权限凭据 | NOT_CREATED | 未完成 | API | N/A | SEC-ROTATE-003 |
| MinIO Worker最小权限凭据 | NOT_CREATED | 未完成 | media worker | N/A | SEC-ROTATE-004 |
| PostgreSQL应用凭据 | PENDING_ASSESSMENT | 未完成 | PostgreSQL/API/MCP/Worker | NOT_CONFIRMED | SEC-ROTATE-005 |
| Redis应用凭据 | PENDING_ASSESSMENT | 未完成 | Redis/API/MCP/Worker | NOT_CONFIRMED | SEC-ROTATE-006 |
| SESSION_SECRET | NOT_PROVISIONED | 未完成 | API | N/A | SEC-ROTATE-007 |
| CSRF_SECRET | NOT_PROVISIONED | 未完成 | API | N/A | SEC-ROTATE-008 |
| Cloudflare Tunnel Token | NOT_PROVISIONED | 未完成 | cloudflared | N/A | SEC-ROTATE-009 |
| 管理员初始密码 | NOT_PROVISIONED | 未完成 | API | N/A | SEC-ROTATE-010 |

当前脱敏扫描在被Git忽略的开发`.env`检测到P0规则命中，这与“旧DeepSeek/MinIO配置曾出现在终端会话”的事实一致。另有三个源码/测试规则命中，分别属于SKU字符串、Stage2测试fixture和Stage7认证测试fixture，需要人工确认但未发现生产bundle和三个新镜像中的秘密。

在DeepSeek控制台完成轮换并确认旧Key失效前，状态固定为：

```text
PUBLIC_DEPLOYMENT=BLOCKED_BY_SECRET_ROTATION
```

生产Secrets宿主目录必须0700、单文件0600；`deploy/runbooks/check_secret_permissions.sh`负责门禁。Compose通过`/run/secrets`和`*_FILE`加载，API、Worker与MinIO Root不共用对象存储凭据。
