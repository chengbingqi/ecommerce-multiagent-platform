# 项目文档

这里收录适合公开阅读和复现的架构、接口、安全、评测与运行文档。项目概览和本地启动方式见[仓库 README](../README.md)。

## 系统与 Agent 架构

- [多 Agent 主控架构](architecture/orchestrator.md)
- [客服 Agent](architecture/customer_service_agent.md)
- [运营分析 Agent](architecture/operations_agent.md)
- [直播切片 Agent](architecture/live_video_agent.md)
- [数据模型](architecture/data_model.md)
- [媒体流水线](architecture/media_pipeline.md)
- [对象存储](architecture/object_storage.md)

## API 与前端

- API：[主控](api/orchestrator.md)、[客服](api/customer_service.md)、[运营](api/operations.md)、[媒体](api/media.md)、[MCP 工具](api/mcp_tools.md)
- 前端：[运营分析看板](frontend/operations.md)、[直播切片工作台](frontend/media.md)

## 安全与权限

- [安全架构](architecture/security.md)
- [认证、会话与 RBAC](security/auth_rbac.md)
- [工具权限矩阵](security/permissions.md)
- [工具审计](security/tool_audit.md)

## Prompt、评测与演示

- [客服 Prompt 版本](prompts/customer_service_v1.md)
- [运营指标](evaluation/operations_metrics.md)、[运营异常规则](evaluation/operations_rules.md)、[安全评测方法](evaluation/security.md)
- [公共演示订单](demo/public_demo_orders.md)
- [演示 GIF](assets/ecommerce-multiagent-demo.gif)

## 运行手册

- [客服审批与恢复](runbooks/customer_service_approval_recovery.md)
- [备份与恢复](runbooks/backup_restore.md)
- [事件响应与回滚](runbooks/incident_response.md)

后续能力边界与改进方向见[项目路线图](roadmap.md)。
