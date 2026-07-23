# Stage7事件响应与回滚

## 凭据事件

1. 禁用公网入口但不停止数据库/Redis/MinIO。
2. 在供应商控制台撤销受影响凭据。
3. 生成新secret文件，目录0700、文件0600。
4. 只重建依赖该secret的服务。
5. 验证新凭据有效、旧凭据失败，记录证据编号而非值。
6. 检查安全审计、限流和对象访问日志，日志只保留规则编号和关联ID。

## 应用回滚

仓库无提交，不能依赖`git reset`。切换前保存源码SHA-256 manifest、镜像ID、Compose结构摘要和Alembic revision。失败时先停止Tunnel入口，再回切明确的旧镜像tag和旧非敏感配置。已经执行的生产数据库迁移不做直接downgrade，以forward-fix处理。

本轮本地构建镜像：

- API `sha256:788277f22e714f2d6016a4a11ff57818412c8961b0a16379ae8b6c88b3ad0b8f`
- frontend `sha256:4f54a1361449af4cf4137f74a7ddf95121cc21c163844c91237c0cb243df47dd`
- edge `sha256:d0a90da70b4fa4dd87ddf903910f805cb4a5b6ad2607ed28974239faa929c1e3`

这些是本地构建证据，不是已部署生产digest。

## 数据恢复

只向精确命名的隔离数据库或bucket恢复并先校验SHA-256。不得覆盖开发/生产对象，不得drop主库，不得使用通配符清理。恢复失败时保留失败日志和partial目录，人工确认后再处理。
