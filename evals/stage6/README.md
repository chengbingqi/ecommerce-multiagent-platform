# Orchestrator 固定评测

`orchestrator_cases_v1.json` 固定 32 个 `case_id`，覆盖路由、缺参、权限、循环限制、有限重试、审批恢复、部分成功、SSE 续流、取消和媒体人工审核限制。

运行：

```bash
.venv/bin/python evals/stage6/run_orchestrator_eval.py
```

结果由 runner 自动写入 `evals/stage6/results/` 的 JSON、CSV、JUnit XML 和汇总 JSON；不得手工填写成功结果。
