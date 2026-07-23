# 阶段6状态与Checkpoint

## 持久化对象

阶段6复用`AgentRun`、`AgentEvent`、`AgentCheckpointLink`、`Approval`、`MediaTask`和LangGraph `AsyncPostgresSaver`，新增最小表`orchestrator_subtasks`（Alembic `20260721_07`）。

子任务表保存父/子run、外部媒体task、Agent类型、依赖、经过约束的输入摘要、输出引用、安全输出摘要、证据引用、工具scope、timeout、重试策略/次数、错误、审批和时间戳。唯一约束`(parent_run_id, idempotency_key)`防止重启重复创建。checkpoint不保存完整工具原始输出、大段ASR、媒体文件、秘密或模型思维过程。

## 状态机

```text
PENDING -> RUNNING -> SUCCEEDED
                  -> PARTIAL_SUCCESS
                  -> FAILED
                  -> WAITING_APPROVAL -> RUNNING -> SUCCEEDED | FAILED
PENDING -> CANCELLED
```

仅尚未开始的`PENDING`父run允许安全取消；运行中或审批中的子任务不能由主控伪装成已取消。终态重复读取只返回持久化结果。

## 审批恢复

客服退款中断时同时保存：父run、客服subtask、child run/thread、`approval_id`和客服已有`action_digest`绑定。审批接口先恢复对应客服child；随后以`Command(resume=...)`恢复同一Orchestrator thread。主控刷新经过校验的客服输出，再继续汇总。重复批准由原阶段3审批与退款幂等约束拦截；开发栈实测重复调用前后对应退款记录数均为1。

## API重启与SSE

Orchestrator checkpoint namespace为`orchestrator`，专业子图使用各自既有namespace/thread。API重启不会重建已完成子任务；父输出从`AgentRun.output_summary`恢复，计划和子任务从PostgreSQL恢复。

运行事件使用不透明UUID。`GET /runs/{run_id}/events`按`created_at,id`稳定排序；合法`after_event_id`只返回游标后的事件。SSE把`Last-Event-ID`传给同一查询。开发栈重启后实测恢复1个父run、1个已完成子任务、客服结果和21条事件；SSE从首事件恢复得到后续20条、0重复。

## 部分成功

汇总不会因另一个子任务失败覆盖成功输出。开发栈使用不存在订单触发客服`NOT_FOUND`、有效运营任务成功，父run实测为`PARTIAL_SUCCESS`，运营结果和证据仍存在。媒体等待处理/人工审核也不会删除客服或运营结果。
