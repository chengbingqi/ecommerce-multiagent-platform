# Stage7安全评测矩阵

固定manifest：`evals/stage7/security_cases_v1.json`，版本`stage7_security_cases_v1`，共58个唯一`case_id`。

| 控制域 | case_id | 当前结果 |
| --- | --- | --- |
| 匿名、登录、枚举、锁定、fixation、会话撤销/过期、Cookie | S7-001—S7-011 | PASS |
| CSRF、Origin、CORS、Host、可信代理 | S7-012—S7-018 | PASS |
| customer/store/media/SSE/download/approval/admin RBAC | S7-019—S7-029 | PASS |
| Prompt注入、工具越权、API/模型/上传/媒体限流、Redis故障 | S7-030—S7-037 | PASS |
| MIME、扩展名、路径、空/超限、codec、FFmpeg、MinIO对象 | S7-038—S7-046 | PASS |
| 日志/错误/bundle/Compose秘密、Swagger、安全头 | S7-047—S7-052 | PASS |
| 真实HTTPS跳转、外网SSE | S7-053—S7-054 | BLOCKED：无域名/Tunnel/外网客户端 |
| API/checkpoint恢复 | S7-055 | PASS |
| 外部PostgreSQL/MinIO恢复、真实外部端口 | S7-056—S7-058 | BLOCKED：无外部备份和外网条件 |

最新固定结果：53 PASS、0 FAIL、5 BLOCKED。所有本地可执行案例为53/53，但Stage7 P0总门禁不是100%，所以不得公网发布。

真实结果文件见`evals/stage7/results/stage7_security_summary.json`以及最新`stage7_security_20260721T083020Z`的JSON、CSV、JUnit XML。认证数据库集成和Redis故障测试另见`stage7_auth_rate_final.junit.xml`。
