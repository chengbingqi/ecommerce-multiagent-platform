# Stage7外部门禁执行清单

执行者：用户/域名提供商/共享主机维护负责人。不得把凭据粘贴到命令历史。

1. 在DeepSeek控制台轮换Key并撤销旧Key。
2. 在维护窗口轮换MinIO Root凭据，用管理工具创建API和Worker两个最小权限服务账号，验证旧Root/API凭据失败。
3. 创建`secrets/production`（0700）及所需文件（0600），运行`deploy/runbooks/check_secret_permissions.sh secrets/production`。
4. 提供外部备份挂载点，运行PostgreSQL、MinIO和配置备份及两个恢复drill。
5. 在Cloudflare创建真实hostname和Tunnel，将Token写入secret文件；Published application只配置edge。
6. 运行三份Compose `config --quiet`、`tests/check_stage7_compose_security.py`和Caddy validate。
7. 维护窗口确认没有PENDING/RUNNING媒体任务后，只启动/更新API、frontend/edge和cloudflared所需服务。
8. 使用真实外部网络完成`docs/stage7_public_deployment.md`列出的验收。
9. 全部通过后仅提出启用HSTS的变更建议；仍需用户再次明确确认，才能修改配置并执行独立回归。Stage7状态也只能在所有外部门禁证据齐全后更新。
