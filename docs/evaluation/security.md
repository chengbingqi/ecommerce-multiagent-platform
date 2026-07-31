# 安全评测方法

固定清单 `evals/stage7/security_cases_v1.json` 使用唯一 `case_id` 描述控制域和检查类型。Runner 调用真实测试并从 JUnit 生成 JSON、CSV 和汇总；结果目录属于生成物，不提交到仓库。

## 覆盖范围

| 控制域 | 检查内容 |
| --- | --- |
| 身份 | 匿名访问、登录枚举、锁定、session fixation、撤销与过期、Cookie 属性 |
| 浏览器边界 | CSRF、Origin、CORS、Host 与可信代理 |
| 授权 | customer/store/media/SSE/download/approval/admin 的 RBAC 和对象范围 |
| Agent | Prompt 注入、工具越权、审批绑定、证据和部分成功 |
| 资源治理 | API、模型、上传、媒体和 SSE 限流；Redis 故障时 fail closed |
| 媒体输入 | MIME、扩展名、路径、大小、时长、codec、FFmpeg 参数和对象访问 |
| 信息暴露 | 日志、错误、前端 bundle、Compose Secret、开发接口和安全响应头 |
| 恢复与网络 | API/checkpoint 恢复、数据库与对象存储恢复、外部端口隔离 |

## 运行与判定

```bash
.venv/bin/python evals/stage7/run_security_eval.py
```

- 自动化用例必须由 Runner 产生结果，不手工填写 PASS。
- 缺少真实域名、外部备份目标或外部客户端时，相关环境用例记录为 `BLOCKED`，不能伪造为通过。
- `BLOCKED` 与 `SKIPPED` 必须保留原因，不能通过降低断言、删除用例或改写结果文件消除。
- 安全扫描只输出规则、路径和脱敏摘要，不把命中的 Secret 值写入报告。
- 公网可用性结论必须基于全部必需控制域，而不是仅依据本地通过率。
