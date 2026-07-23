import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import LoginPage from "./LoginPage";
import { GENERIC_LOGIN_ERROR, reduceLoginView } from "./loginState";

describe("本地管理员登录页面", () => {
  it("使用桌面双栏结构和完整简体中文文案", () => {
    const markup = renderToStaticMarkup(<LoginPage />);

    expect(markup).toContain('class="auth-card"');
    expect(markup).toContain('data-layout="desktop-split"');
    expect(markup).toContain('class="auth-brand-panel"');
    expect(markup).toContain('class="auth-form-panel"');
    expect(markup).toContain("电商智能运营");
    expect(markup).toContain("多 Agent 工作台");
    expect(markup).toContain("模拟数据环境");
    expect(markup).toContain("受控权限访问");
    expect(markup).not.toContain("HttpOnly");
  });

  it("提供可键盘提交且自动填充语义正确的表单", () => {
    const markup = renderToStaticMarkup(<LoginPage />);

    expect(markup).toContain("<form");
    expect(markup).toContain('autoComplete="username"');
    expect(markup).toContain('autoComplete="current-password"');
    expect(markup).toContain('type="submit"');
    expect(markup).toContain("登录后将建立安全会话");
    expect(markup).not.toContain("注册");
    expect(markup).not.toContain("忘记密码");
    expect(markup).not.toContain("找回密码");
  });

  it("保持登录中状态和不枚举账号的统一失败文案", () => {
    const start = reduceLoginView({ busy: false, message: "旧消息" }, { type: "start" });
    const failure = reduceLoginView(start, { type: "failure" });

    expect(start).toEqual({ busy: true, message: "" });
    expect(failure).toEqual({ busy: false, message: GENERIC_LOGIN_ERROR });
    expect(GENERIC_LOGIN_ERROR).not.toContain("不存在");
    expect(GENERIC_LOGIN_ERROR).not.toContain("已注册");
  });

  it("在760px以下切换单栏且保留无横向滚动约束", () => {
    const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

    expect(styles).toContain("@media (max-width: 760px)");
    expect(styles).toMatch(/\.auth-card\s*\{\s*grid-template-columns:\s*1fr/);
    expect(styles).toContain("width: calc(100% - 28px)");
    expect(styles).toContain("word-break: keep-all");
    expect(styles).toContain("white-space: nowrap");
  });
});
