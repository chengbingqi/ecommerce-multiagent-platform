# 备份与恢复运行手册

本项目分别备份 PostgreSQL、MinIO 业务对象和非敏感配置。备份目标必须位于与 `DATA_ROOT` 不同的文件系统；脚本会拒绝同盘目标。

## 备份工具

- `deploy/backup/backup_postgres.sh`：执行 custom-format `pg_dump`，记录 SHA-256、Alembic revision、锁和原子完成标记。
- `deploy/backup/backup_minio.py`：只处理明确配置的媒体 bucket，记录对象键、大小和 SHA-256。
- `deploy/backup/backup_config.sh`：只打包非敏感文档、Prompt、规则、manifest、迁移和部署配置。

所有工具使用 partial 目录写入，校验完成后再原子发布备份，并执行明确的 retention 策略。Secret 必须由批准的秘密管理系统或加密介质单独备份，不能混入普通配置包。

## 恢复演练

1. 在隔离环境检查备份完成标记、清单和 SHA-256。
2. 使用 `restore_postgres_drill.sh` 创建精确命名的临时数据库并恢复。
3. 校验 Alembic revision、关键表和 AgentRun 到 Principal 的关联完整性。
4. 使用 `restore_minio_drill.py` 创建随机隔离 bucket，恢复代表对象并校验大小和 SHA-256。
5. 记录耗时、数据时点、失败原因和可复现命令；只有异机或外部目标的演练可作为灾备证据。
6. 演练结束只清理脚本创建的临时数据库或 bucket，不覆盖开发/生产数据。

禁止对主库执行 destructive downgrade、drop 或通配符清理。恢复失败时保留日志和 partial 目录，人工确认后再处理。
