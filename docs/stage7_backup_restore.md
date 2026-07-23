# Stage7备份与恢复

## 实现

- `deploy/backup/backup_postgres.sh`：custom-format `pg_dump`、SHA-256、Alembic revision、锁、partial目录和原子完成标记。
- `deploy/backup/backup_minio.py`：只备份`media-original`和`media-derived`，记录对象、大小和SHA-256。
- `deploy/backup/backup_config.sh`：只备份非敏感文档、Prompt、规则、manifest、迁移和Compose/Caddy。
- `restore_postgres_drill.sh`：创建精确命名临时数据库、恢复、验证revision和AgentRun→Principal关联，退出时只删除该临时库。
- `restore_minio_drill.py`：创建随机隔离bucket，恢复代表对象并校验大小/SHA-256，最后只清理该bucket。
- 用户级systemd service/timer模板已提供，但在外部目标确认前未安装或启用。

所有备份脚本比较目标与`DATA_ROOT`的文件系统设备；相同文件系统立即以`BACKUP-EXTERNAL-001`失败。Secrets加密备份必须由用户批准的外部秘密管理或加密介质单独完成，不与普通配置包混放。

## 本轮实测

未提供合格异机/外部目标，因此Stage7备份门禁未通过。仅做同机技术演练：PostgreSQL备份821985字节，备份脚本记录1秒；隔离恢复成功，revision为`20260721_07`，孤立AgentRun关联为0。该副本位于同机临时文件系统，不计入RPO/RTO或合格外部备份。

MinIO外部备份与恢复未执行，原因是缺少批准外部目标和已轮换最小权限凭据。Qdrant为`NOT_APPLICABLE`：当前知识数据位于PostgreSQL及仓库源文档，没有运行Qdrant服务。
