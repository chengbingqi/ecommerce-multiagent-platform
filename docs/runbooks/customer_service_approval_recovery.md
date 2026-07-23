# 模拟退款审批与恢复运行手册

1. 客户明确提交模拟退款，图先调用订单和资格工具。
2. 资格通过后，确定性代码生成 `action_digest` 和绑定 run/thread 的 Approval。
3. AgentRun 写为 `WAITING_APPROVAL`，LangGraph `interrupt` 将 checkpoint 持久化到 PostgreSQL。
4. 审批 API 校验主体、绑定关系、digest、有效期和使用状态。
5. `approved` 使用原 `thread_id` 执行 `Command(resume="approved")`，用稳定 idempotency key 调用阶段2 `create_refund_request`；最终状态只能是 `simulated_requested`。
6. `rejected` 从同一 thread 恢复但不调用副作用工具。

API/进程重启后，先用 GET run 确认仍为 `WAITING_APPROVAL`，再提交决定。不得创建新 thread 绕过原 checkpoint。重复批准应返回既有结果，数据库中 idempotency 和模拟退款各只有一条。

开发栈可用 `alembic upgrade head` 和 `python -m app.agents.customer_service.checkpoints setup` 复现 schema。验收结束使用 Compose `down`，不得加 `-v`。开发主库不得为测试执行 downgrade；回滚只在一次性测试数据库验证。
