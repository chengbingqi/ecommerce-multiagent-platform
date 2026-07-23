import { useCallback, useEffect, useState, type ReactNode } from "react";

import { currentSession, fetchHealth, type ReadyResponse, type SessionInfo } from "./api";
import OperationsPage from "./OperationsPage";
import MediaPage from "./MediaPage";
import OrchestratorPage from "./OrchestratorPage";
import LoginPage from "./LoginPage";
import "./styles.css";

type ViewState = "loading" | "success" | "error";

function StatusBadge({ status }: { status?: "ok" | "unavailable" }) {
  if (!status) return <span className="badge neutral">等待检查</span>;
  return (
    <span className={`badge ${status === "ok" ? "healthy" : "unhealthy"}`}>
      <span className="dot" aria-hidden="true" />
      {status === "ok" ? "正常" : "不可用"}
    </span>
  );
}

function StatusPage() {
  const [viewState, setViewState] = useState<ViewState>("loading");
  const [health, setHealth] = useState<ReadyResponse>();
  const [message, setMessage] = useState("");
  const [checkedAt, setCheckedAt] = useState<Date>();

  const check = useCallback(async (signal?: AbortSignal) => {
    setViewState("loading");
    setMessage("");
    try {
      const result = await fetchHealth(signal);
      setHealth(result.data);
      setCheckedAt(new Date());
      if (result.httpOk) {
        setViewState("success");
      } else {
        setViewState("error");
        setMessage("部分基础服务尚未就绪，请检查下方组件状态。");
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setHealth(undefined);
      setCheckedAt(new Date());
      setViewState("error");
      setMessage(error instanceof Error ? error.message : "无法连接后端服务");
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void check(controller.signal);
    return () => controller.abort();
  }, [check]);

  return (
    <main className="shell">
      <nav><a aria-current="page" href="/">系统状态</a><a href="/workbench">多 Agent 工作台</a><a href="/operations">运营分析</a><a href="/media">直播切片</a></nav>
      <header>
        <p className="eyebrow">STAGE 1 · SYSTEM STATUS</p>
        <h1>电商智能运营多 Agent 集群平台</h1>
        <p className="subtitle">工程骨架与基础依赖运行状态</p>
      </header>

      {viewState === "error" && (
        <div className="alert" role="alert">
          <strong>健康检查未通过</strong>
          <span>{message || "后端当前不可用，请稍后重试。"}</span>
        </div>
      )}

      <section className="status-card" aria-busy={viewState === "loading"}>
        <div className="summary-row">
          <div>
            <span className="label">整体状态</span>
            <h2>
              {viewState === "loading"
                ? "正在检查…"
                : viewState === "success"
                  ? "所有基础服务正常"
                  : "服务需要关注"}
            </h2>
          </div>
          <button type="button" onClick={() => void check()} disabled={viewState === "loading"}>
            {viewState === "loading" ? "检查中" : "重新检查"}
          </button>
        </div>

        <div className="service-grid">
          <article>
            <span className="label">API 存活状态</span>
            <StatusBadge status={health ? "ok" : undefined} />
          </article>
          <article>
            <span className="label">PostgreSQL</span>
            <StatusBadge status={health?.components.postgres.status} />
          </article>
          <article>
            <span className="label">Redis</span>
            <StatusBadge status={health?.components.redis.status} />
          </article>
        </div>

        <footer>
          <span>环境：{health?.environment ?? "未知"}</span>
          <span>
            最近检查：{checkedAt ? checkedAt.toLocaleString("zh-CN", { hour12: false }) : "尚未完成"}
          </span>
        </footer>
      </section>
    </main>
  );
}

export default function App() {
  if (window.location.pathname === "/login") return <LoginPage />;
  if (window.location.pathname === "/workbench") return <Protected page={<OrchestratorPage />} />;
  if (window.location.pathname === "/operations") return <Protected page={<OperationsPage />} />;
  if (window.location.pathname === "/media") return <Protected pageFor={(session) => (
    session.principal.account_type === "public_demo" ? <OrchestratorPage /> : <MediaPage />
  )} />;
  return <StatusPage />;
}

function Protected({
  page,
  pageFor,
}: {
  page?: ReactNode;
  pageFor?: (session: SessionInfo) => ReactNode;
}) {
  const [allowed, setAllowed] = useState(false);
  const [checked, setChecked] = useState(false);
  const [session, setSession] = useState<SessionInfo>();

  useEffect(() => {
    void currentSession()
      .then((value) => {
        setSession(value);
        setAllowed(true);
      })
      .catch(() => setAllowed(false))
      .finally(() => setChecked(true));
  }, []);

  if (!checked) return <main className="shell"><p>正在验证安全会话…</p></main>;
  if (!allowed || !session) {
    if (!import.meta.env.PROD) return page ?? null;
    return <LoginPage />;
  }
  return pageFor ? pageFor(session) : page;
}
