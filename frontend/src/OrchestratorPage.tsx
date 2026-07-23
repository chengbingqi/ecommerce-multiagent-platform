import { FormEvent, useEffect, useRef, useState } from "react";

import {
  createOrchestratorRun,
  currentSession,
  decideApproval,
  fetchDemoOrders,
  getCachedSession,
  getOrchestratorEvents,
  getOrchestratorRun,
  orchestratorEventsStreamUrl,
  type AgentEvent,
  type DemoOrderOption,
  type OrchestratorOutput,
  type SessionInfo,
} from "./api";
import {
  canUseDemoOrderPicker,
  canUseManualOrderInput,
  isPublicDemoSession,
} from "./orchestratorAccess";

const stores = [
  { id: "", name: "不指定店铺" },
  { id: "b39e17d7-c6aa-5aaf-adfc-b84c80d0f8f1", name: "模拟店铺 1" },
  { id: "8dd8607c-daf0-5de6-8e21-d0d227f48bc5", name: "模拟店铺 2" },
];

const agentNames: Record<string, string> = {
  customer_service: "客服 Agent",
  operations: "运营 Agent",
  media: "直播切片 Agent",
};

const terminal = new Set(["SUCCEEDED", "PARTIAL_SUCCESS", "FAILED", "CANCELLED"]);

function optional(form: FormData, name: string): string | undefined {
  const value = String(form.get(name) || "").trim();
  return value || undefined;
}

function CustomerResult({ value }: { value: Record<string, unknown> }) {
  const approval = value.approval as { approval_id?: string } | undefined;
  return (
    <section>
      <h2>客服结果</h2>
      <p>{String(value.answer || "客服子任务未返回文本结果。")}</p>
      <p className="version">意图：{String(value.intent || "未知")}</p>
      {approval?.approval_id && <p>审批编号：<code>{approval.approval_id}</code></p>}
    </section>
  );
}

export function DemoOrderPicker({
  orders,
  phase,
  selectedOrderId,
  onSelect,
  onClear,
}: {
  orders: DemoOrderOption[];
  phase: "idle" | "loading" | "ready" | "failed";
  selectedOrderId: string;
  onSelect: (orderId: string) => void;
  onClear: () => void;
}) {
  const selected = orders.find((order) => order.order_id === selectedOrderId);
  return (
    <div className="demo-order-picker">
      <label>订单编号<select aria-label="选择演示订单" name="order_id" onChange={(event) => onSelect(event.target.value)} value={selectedOrderId}>
        <option value="">{phase === "loading" ? "正在加载演示订单…" : "选择演示订单"}</option>
        {orders.map((order) => <option key={order.order_id} value={order.order_id}>{order.safe_label}｜订单 {order.masked_order_id}</option>)}
      </select></label>
      {phase === "failed" && <p className="field-error" role="alert">演示订单加载失败，请稍后重试。</p>}
      {selected && <p className="field-help">{selected.scenario_description}</p>}
      {selectedOrderId && <button type="button" className="secondary" onClick={onClear}>清除选择</button>}
    </div>
  );
}

export function DemoTaskExamples({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="demo-examples full" aria-label="用户任务示例">
      {[
        "查询该订单当前的物流状态。",
        "判断该订单是否存在物流延迟。",
        "查询该订单是否符合退款条件，但不要发起退款。",
        "查询订单物流，同时分析模拟店铺1本周的物流异常。",
      ].map((example) => <button key={example} type="button" onClick={() => onPick(example)}>{example}</button>)}
    </div>
  );
}

export default function OrchestratorPage() {
  const [phase, setPhase] = useState<"idle" | "loading" | "ready" | "failed">("idle");
  const [result, setResult] = useState<OrchestratorOutput>();
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [error, setError] = useState("");
  const [session, setSession] = useState<SessionInfo | undefined>(() => getCachedSession());
  const [demoOrders, setDemoOrders] = useState<DemoOrderOption[]>([]);
  const [demoOrderPhase, setDemoOrderPhase] = useState<"idle" | "loading" | "ready" | "failed">("idle");
  const [selectedOrderId, setSelectedOrderId] = useState("");
  const [requestText, setRequestText] = useState("");
  const [storeId, setStoreId] = useState("");
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => () => sourceRef.current?.close(), []);
  useEffect(() => {
    void currentSession().then(setSession).catch(() => undefined);
  }, []);
  const isDemo = isPublicDemoSession(session);
  const showDemoOrderPicker = canUseDemoOrderPicker(session);
  const showManualOrderInput = canUseManualOrderInput(session);

  useEffect(() => {
    if (!showDemoOrderPicker) return;
    setDemoOrderPhase("loading");
    void fetchDemoOrders()
      .then((data) => {
        setDemoOrders(data.orders);
        if (isDemo) setStoreId(data.operations_store_id);
        setDemoOrderPhase("ready");
      })
      .catch(() => setDemoOrderPhase("failed"));
  }, [isDemo, showDemoOrderPicker]);

  async function startEvents(runId: string) {
    const initial = await getOrchestratorEvents(runId);
    setEvents(initial);
    sourceRef.current?.close();
    const source = new EventSource(orchestratorEventsStreamUrl(runId));
    sourceRef.current = source;
    source.onmessage = (event) => {
      const row = JSON.parse(event.data) as AgentEvent;
      setEvents((current) => current.some((item) => item.event_id === row.event_id)
        ? current
        : [...current, row]);
    };
    source.addEventListener("run_state", async () => {
      const snapshot = await getOrchestratorRun(runId);
      setResult(snapshot);
      if (terminal.has(snapshot.overall_status)) source.close();
    });
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPhase("loading");
    setError("");
    setEvents([]);
    try {
      const output = await createOrchestratorRun({
        request_text: String(form.get("request_text") || ""),
        order_id: selectedOrderId || optional(form, "order_id"),
        store_id: storeId || optional(form, "store_id"),
        start_date: optional(form, "start_date"),
        end_date: optional(form, "end_date"),
        topic: optional(form, "topic"),
        asset_id: optional(form, "asset_id"),
        product_keywords: optional(form, "product_keywords")?.split(/[,，\s]+/).filter(Boolean),
      });
      setResult(output);
      setPhase(output.overall_status === "FAILED" ? "failed" : "ready");
      await startEvents(output.run_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "多 Agent 任务提交失败");
      setPhase("failed");
    }
  }

  async function refresh() {
    if (!result) return;
    const snapshot = await getOrchestratorRun(result.run_id);
    setResult(snapshot);
    setEvents(await getOrchestratorEvents(result.run_id));
    setPhase(snapshot.overall_status === "FAILED" ? "failed" : "ready");
  }

  async function approve(decision: "approved" | "rejected") {
    const approvalId = result?.subtasks.find((item) => item.approval_id)?.approval_id;
    if (!approvalId) return;
    setPhase("loading");
    try {
      await decideApproval(approvalId, decision);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "审批恢复失败");
      setPhase("failed");
    }
  }

  return (
    <main className="shell orchestrator-shell">
      <nav><a href="/">系统状态</a><a aria-current="page" href="/workbench">多 Agent 工作台</a><a href="/operations">运营分析</a>{!isDemo && <a href="/media">直播切片</a>}</nav>
      <header>
        <p className="eyebrow">STAGE 6 · MULTI-AGENT ORCHESTRATION</p>
        <h1>统一多 Agent 工作台</h1>
        <p className="subtitle">主控仅做结构化路由、调度和汇总；专业工具权限保持隔离。</p>
      </header>

      <form className="orchestrator-form" onSubmit={(event) => void submit(event)}>
        {isDemo && <div className="demo-notice full">当前为公共演示账号，仅支持模拟数据的只读查询和运营分析。退款审批、管理员功能和视频上传已禁用。</div>}
        <label className="full">用户任务<textarea name="request_text" required maxLength={2000} onChange={(event) => setRequestText(event.target.value)} placeholder="例如：查询订单物流，同时分析本周店铺经营异常" value={requestText} /></label>
        {showDemoOrderPicker && (
          <DemoOrderPicker
            orders={demoOrders}
            phase={demoOrderPhase}
            selectedOrderId={selectedOrderId}
            onClear={() => setSelectedOrderId("")}
            onSelect={setSelectedOrderId}
          />
        )}
        {showManualOrderInput && <label>手动输入订单编号<input name="order_id" maxLength={64} placeholder="可选" /></label>}
        <label>店铺<select name="store_id" disabled={isDemo} onChange={(event) => setStoreId(event.target.value)} value={storeId}>{stores.filter((store) => !isDemo || store.id === storeId).map((store) => <option key={store.id || "none"} value={store.id}>{store.name}</option>)}</select></label>
        <label>开始日期<input name="start_date" type="date" /></label>
        <label>结束日期<input name="end_date" type="date" /></label>
        <label>运营主题<select name="topic"><option value="">自动</option><option>OVERVIEW</option><option>SALES</option><option>INVENTORY</option><option>REFUNDS</option><option>CONVERSION</option><option>LOGISTICS</option><option>CAMPAIGN</option></select></label>
        {!isDemo && <label>媒体资产 ID<input name="asset_id" maxLength={64} placeholder="仅显式切片请求需要" /></label>}
        {!isDemo && <label className="full">商品关键词<input name="product_keywords" placeholder="可选，使用逗号分隔" /></label>}
        {isDemo && <DemoTaskExamples onPick={setRequestText} />}
        <button type="submit" disabled={phase === "loading"}>{phase === "loading" ? "执行中…" : "提交给主控"}</button>
      </form>

      {phase === "failed" && <div className="alert" role="alert"><strong>任务需要关注</strong><span>{error || result?.error?.message || "部分或全部子任务失败。"}</span></div>}

      {result && <div className="orchestrator-results">
        <section className="run-summary">
          <div><span className="label">整体状态</span><strong className={`run-status status-${result.overall_status.toLowerCase()}`}>{result.overall_status}</strong></div>
          <div><span className="label">路由置信度</span><strong>{Math.round(result.route.confidence * 100)}%</strong></div>
          <div><span className="label">执行规则</span><strong>{result.completion_rule}</strong></div>
          <button type="button" onClick={() => void refresh()}>刷新状态</button>
        </section>

        <section>
          <h2>主控路由</h2>
          <p>{result.route.agent_targets.length ? result.route.agent_targets.map((target) => agentNames[target]).join(" → 主控汇总 · ") : "未产生可执行路由"}</p>
          <p className="version">{result.route.route_version} · 风险 {result.route.risk_level}</p>
        </section>

        <section>
          <h2>子任务与依赖</h2>
          <div className="subtask-grid">{result.subtasks.map((task) => <article key={task.task_id}>
            <span className="label">{agentNames[task.agent_type]}</span>
            <strong>{task.status}</strong>
            <small>{task.duration_ms == null ? "等待计时" : `${task.duration_ms} ms`}</small>
            <p>依赖：{task.dependency_ids.length ? task.dependency_ids.join("、") : "无"}</p>
            {task.error_code && <p className="error-code">错误：{task.error_code}</p>}
          </article>)}</div>
        </section>

        {result.overall_status === "WAITING_APPROVAL" && <section className="approval-panel">
          <h2>等待审批</h2><p>仅恢复对应客服子任务，完成后继续同一主控运行。</p>
          <div><button type="button" onClick={() => void approve("approved")}>批准</button><button className="danger" type="button" onClick={() => void approve("rejected")}>拒绝</button></div>
        </section>}

        {result.customer_service_result && <CustomerResult value={result.customer_service_result} />}
        {result.operations_result && <section><h2>运营结果</h2><div className="kpi-grid">{result.operations_result.metrics.map((metric) => <article key={metric.metric_key}><span className="label">{metric.display_name}</span><strong>{metric.value}</strong><small>{metric.unit}</small></article>)}</div><h3>异常与建议</h3><p>{result.operations_result.explanation}</p></section>}
        {result.media_result && <section className="experimental-panel"><h2>直播切片任务</h2><p><strong>实验性能力，必须人工审核，不会自动发布。</strong></p><p>任务 {result.media_result.task_id} · 状态 {result.media_result.status} · 审核 {result.media_result.review_status}</p><a href="/media">前往直播切片审核页</a></section>}

        {!!result.partial_failures.length && <section><h2>部分失败</h2><ul>{result.partial_failures.map((item) => <li key={item.subtask_id}>{agentNames[item.agent_type] || item.agent_type}：{item.error_code}</li>)}</ul></section>}
        {!!result.evidence_refs.length && <section><h2>证据引用</h2><div className="evidence-list">{result.evidence_refs.map((ref) => <code key={ref}>{ref}</code>)}</div></section>}
        {!!result.warnings.length && <section><h2>警告与限制</h2><ul>{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></section>}
        <section><h2>实时执行事件</h2><ol className="event-list">{events.map((event) => <li key={event.event_id}><time>{new Date(event.timestamp).toLocaleTimeString("zh-CN", { hour12: false })}</time> · {event.event_type} · {event.status}</li>)}</ol></section>
      </div>}
    </main>
  );
}
