# ADR：阶段5本地对象存储

- 状态：阶段5本地验收采用，阶段7前必须重评
- 日期：2026-07-19

## 决策

阶段5仅在 Docker backend 网络内使用固定的 MinIO Community 镜像：

`minio/minio:RELEASE.2025-09-07T16-13-09Z@sha256:a1a8bd4ac40ad7881a245bab97323e18f971e4d4cba2c2007ec1bedd21cbaba2`

已核对 linux/amd64 manifest digest。镜像使用 MinIO Community 的 AGPLv3 许可；上游仓库已经归档，不再视为持续获得安全维护的生产组件。该选择只用于阶段5的本机开发与评测，不代表阶段7公网生产方案。

## 约束

- 不发布 9000/9001，不启用 Console，只连接 backend 网络。
- 数据绑定到操作者在 `.env` 中通过 `DATA_ROOT` 指定的外部数据目录。
- 凭据仅从 Git 忽略且权限为 600 的本地 `.env` 注入；禁止默认凭据和日志回显。
- bucket 固定为 `media-original`、`media-derived`、`media-quarantine`。
- object key 由服务端生成，浏览器只使用 FastAPI/Caddy 受控代理。
- 阶段7前必须选择受维护的 S3 兼容对象存储，或取得适用的 AIStor 许可并重新完成安全评估。

## 风险

归档镜像的安全修复、供应链更新和长期兼容性均不可保证。因此生产 Compose 仍只用于配置检查，本 ADR 不授权公网部署。

本地验收已验证三个bucket健康、原始/派生对象写入、受控下载和SHA-256反查；9000/9001没有发布。该运行结果不降低上述归档与生产风险。
