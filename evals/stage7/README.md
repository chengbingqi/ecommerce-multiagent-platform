# Stage 7 安全固定评测

`security_cases_v1.json` 固定安全案例标识和检查类型。运行：

```bash
.venv/bin/python evals/stage7/run_security_eval.py
```

Runner调用真实pytest并从JUnit生成JSON、CSV和汇总。依赖真实域名、异机备份或外部客户端的案例在条件缺失时记录为`BLOCKED`，不会伪造为通过。
