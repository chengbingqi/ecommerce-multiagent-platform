# 对象存储设计

## 决策

本地媒体流水线使用固定版本的 S3 兼容 MinIO 服务。对象存储只连接内部网络，不发布管理端口，也不向浏览器返回 endpoint、凭据或 object key。

bucket 按职责分为 `media-original`、`media-derived` 和 `media-quarantine`。object key 由服务端生成；上传、预览和下载全部经过 FastAPI/Caddy 的权限代理。

## 数据与凭据

- 持久数据通过 `DATA_ROOT` 指向项目专用的外部数据目录。
- 凭据只从未提交的本地配置或 Secret 文件加载，禁止默认凭据和日志回显。
- API 与 Worker 应使用职责分离的最小权限账号；管理凭据不提供给应用服务。
- 备份只处理明确 bucket，并记录对象大小和 SHA-256。

## 限制

当前固定的 MinIO Community 上游版本已归档，不能视为持续获得安全维护的生产组件。生产采用前必须选择受维护的 S3 兼容实现或重新完成许可、漏洞、备份和恢复评估。本地选择不构成公网部署授权。
