import { FormEvent, useEffect, useState } from "react";

import { createOperationsRun, currentSession, type OperationsOutput, type SessionInfo } from "./api";

const stores = [
  { id: "b39e17d7-c6aa-5aaf-adfc-b84c80d0f8f1", name: "模拟店铺 1" },
  { id: "8dd8607c-daf0-5de6-8e21-d0d227f48bc5", name: "模拟店铺 2" },
];

export default function OperationsPage() {
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "failed">("idle");
  const [result, setResult] = useState<OperationsOutput>();
  const [error, setError] = useState("");
  const [session, setSession] = useState<SessionInfo>();
  const isDemo = session?.principal.account_type === "public_demo";

  useEffect(() => {
    void currentSession().then(setSession).catch(() => undefined);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setStatus("loading");
    setError("");
    try {
      const output = await createOperationsRun({
        store_id: String(form.get("store_id")),
        start_date: String(form.get("start_date")),
        end_date: String(form.get("end_date")),
        topic: String(form.get("topic")),
        granularity: form.get("granularity") as "day" | "week",
        comparison_mode: "previous_period",
        user_question: String(form.get("user_question") || ""),
      });
      setResult(output);
      setStatus(output.status === "FAILED" ? "failed" : "success");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "运营分析请求失败");
      setStatus("failed");
    }
  }

  return (
    <main className="shell operations-shell">
      <nav><a href="/">系统状态</a><a href="/workbench">多 Agent 工作台</a><a aria-current="page" href="/operations">运营分析</a>{!isDemo && <a href="/media">直播切片</a>}</nav>
      <header>
        <p className="eyebrow">STAGE 4 · OPERATIONS ANALYSIS</p>
        <h1>运营分析看板</h1>
        <p className="subtitle">所有数值来自固定模拟数据的确定性工具；模型只解释，不计算指标。</p>
      </header>
      <form className="filters" onSubmit={(event) => void submit(event)}>
        <label>店铺<select name="store_id">{stores.filter((store) => !isDemo || store.name === "模拟店铺 1").map((store) => <option key={store.id} value={store.id}>{store.name}</option>)}</select></label>
        <label>开始日期<input name="start_date" type="date" defaultValue="2026-06-24" required /></label>
        <label>结束日期<input name="end_date" type="date" defaultValue="2026-07-01" required /></label>
        <label>主题<select name="topic" defaultValue="OVERVIEW"><option>OVERVIEW</option><option>SALES</option><option>INVENTORY</option><option>REFUNDS</option><option>CONVERSION</option><option>LOGISTICS</option><option>CAMPAIGN</option></select></label>
        <label>粒度<select name="granularity"><option value="day">日</option><option value="week">周</option></select></label>
        <label className="wide">分析问题（可选）<input name="user_question" maxLength={1000} placeholder="例如：解释本周期的异常并给出建议" /></label>
        <button type="submit" disabled={status === "loading"}>{status === "loading" ? "分析中…" : "提交分析"}</button>
      </form>
      {!result && status !== "failed" && (
        <p className="section-guide">结果将分为：确定性事实、规则识别的异常、模型解释、运营建议。</p>
      )}
      {status === "failed" && <div className="alert" role="alert"><strong>分析失败</strong><span>{error || result?.error?.message}</span></div>}
      {result?.no_data && <div className="alert no-data"><strong>当前范围无业务数据</strong><span>请调整店铺或日期范围。</span></div>}
      {result && (
        <div className="dashboard">
          <section><h2>1. 确定性事实</h2><p className="version">{result.metrics_version} · {result.period.semantics}</p><div className="kpi-grid">{result.metrics.map((metric) => <article key={metric.metric_key}><span className="label">{metric.display_name}</span><strong>{metric.value}</strong><small>{metric.unit}</small></article>)}</div></section>
          <section><h2>趋势</h2><div className="trend-list">{result.trend_series.map((point) => <div key={point.bucket_start}><span>{point.bucket_start}</span><b>GMV {point.gmv}</b><span>订单 {point.order_count}</span><span>转化 {point.conversion_rate}</span></div>)}</div></section>
          <section><h2>2. 规则识别的异常</h2><p className="version">{result.anomaly_rules_version}</p>{result.anomalies.length ? <table><thead><tr><th>异常</th><th>规则</th><th>观察值</th><th>阈值</th></tr></thead><tbody>{result.anomalies.map((row) => <tr key={row.anomaly_id}><td>{row.anomaly_type}</td><td>{row.rule_id}</td><td>{row.observed_value}</td><td>{row.threshold}</td></tr>)}</tbody></table> : <p>未识别到规则异常。</p>}</section>
          <section><h2>3. 模型解释</h2><p className="version">{result.prompt_version}</p><p>{result.explanation}</p></section>
          <section><h2>4. 运营建议</h2>{result.recommendations.length ? <ul>{result.recommendations.map((row, index) => <li key={`${row.priority}-${index}`}><b>{row.priority}</b> {row.recommendation}</li>)}</ul> : <p>暂无经过证据校验的建议。</p>}</section>
          <section><h2>证据查看</h2><div className="evidence-list">{result.evidence_rows.map((row) => <code key={row.evidence_ref}>{row.evidence_ref} · {String(row.observed)}</code>)}</div></section>
        </div>
      )}
    </main>
  );
}
