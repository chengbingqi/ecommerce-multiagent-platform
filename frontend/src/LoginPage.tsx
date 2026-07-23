import { FormEvent, useReducer, useState } from "react";

import { login } from "./api";
import { reduceLoginView } from "./loginState";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [{ busy, message }, dispatch] = useReducer(reduceLoginView, {
    busy: false,
    message: "",
  });

  async function submit(event: FormEvent) {
    event.preventDefault();
    dispatch({ type: "start" });
    try {
      await login(username, password);
      dispatch({ type: "success" });
      window.location.assign("/workbench");
    } catch {
      // Keep the message deliberately generic to prevent account enumeration.
      dispatch({ type: "failure" });
    } finally {
      setPassword("");
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card" data-layout="desktop-split">
        <div className="auth-brand-panel">
          <p className="auth-eyebrow">安全访问 · SECURE ACCESS</p>
          <h1 className="auth-title">
            <span>电商智能运营</span>
            <span>多 Agent 工作台</span>
          </h1>
          <p className="auth-intro">统一调度客服、运营分析与直播切片能力</p>
          <div className="auth-tags" aria-label="环境说明">
            <span>模拟数据环境</span>
            <span>受控权限访问</span>
          </div>
          <p className="auth-brand-note">所有操作均受身份、范围与审计规则约束。</p>
        </div>

        <div className="auth-form-panel">
          <div className="auth-form-heading">
            <p className="auth-kicker">欢迎使用</p>
            <h2>登录</h2>
            <p>请输入管理员分配的账号和密码</p>
          </div>
          <form aria-busy={busy} onSubmit={(event) => void submit(event)}>
          <label>
            用户名
            <input
              autoComplete="username"
              autoFocus
              maxLength={120}
              onChange={(event) => setUsername(event.target.value)}
              required
              spellCheck={false}
              value={username}
            />
          </label>
          <label>
            密码
            <input
              autoComplete="current-password"
              maxLength={1024}
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </label>
            <div
              aria-live="polite"
              className={`auth-feedback${message ? " is-visible" : ""}`}
              role={message ? "alert" : "status"}
            >
              {message || "登录后将建立安全会话"}
            </div>
            <button className="auth-submit" disabled={busy} type="submit">
              {busy ? "正在登录…" : "登录"}
            </button>
          </form>
          <p className="auth-session-note">登录后将建立安全会话，请在使用完毕后安全退出。</p>
        </div>
      </section>
    </main>
  );
}
