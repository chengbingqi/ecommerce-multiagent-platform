import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchHealth, login, logout } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("fetchHealth", () => {
  it("使用同源相对地址并解析健康状态", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "ok",
          service: "api",
          environment: "test",
          components: { postgres: { status: "ok" }, redis: { status: "ok" } },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchHealth();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/health/ready",
      expect.objectContaining({ headers: { Accept: "application/json" } }),
    );
    expect(result.httpOk).toBe(true);
    expect(result.data.components.postgres.status).toBe("ok");
  });

  it("保留503响应中的组件故障信息", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: "unavailable",
            service: "api",
            environment: "test",
            components: {
              postgres: { status: "ok" },
              redis: { status: "unavailable" },
            },
          }),
          { status: 503, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const result = await fetchHealth();
    expect(result.httpOk).toBe(false);
    expect(result.data.components.redis.status).toBe("unavailable");
  });
});

describe("管理员安全会话API", () => {
  it("登录使用同源Cookie请求且注销携带会话绑定的CSRF Header", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            principal: { principal_id: "admin-001", role: "admin" },
            idle_expires_at: "2026-07-21T11:00:00Z",
            absolute_expires_at: "2026-07-21T18:00:00Z",
            security_policy_version: "stage7_security_v1.0.0",
            csrf_token: "test-only-csrf-token",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await login("platform_admin", "test-only-password");
    await logout();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/auth/login");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "POST", credentials: "include" });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
      username: "platform_admin",
      password: "test-only-password",
    });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/auth/logout");
    expect(new Headers(fetchMock.mock.calls[1][1].headers).get("X-CSRF-Token")).toBe(
      "test-only-csrf-token",
    );
  });
});
