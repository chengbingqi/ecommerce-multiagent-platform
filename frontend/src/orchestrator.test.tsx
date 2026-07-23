import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import OrchestratorPage, { DemoOrderPicker, DemoTaskExamples } from "./OrchestratorPage";
import { createOrchestratorRun, fetchDemoOrders } from "./api";
import { canUseDemoOrderPicker, canUseManualOrderInput } from "./orchestratorAccess";

afterEach(() => vi.unstubAllGlobals());

describe("统一多 Agent 工作台", () => {
  it("展示路由、子任务、审批和媒体限制信息", () => {
    const markup = renderToStaticMarkup(<OrchestratorPage />);
    expect(markup).toContain("统一多 Agent 工作台");
    expect(markup).toContain("主控仅做结构化路由、调度和汇总");
    expect(markup).toContain("媒体资产 ID");
    expect(markup).not.toContain("调整时间边界");
    expect(markup).not.toContain("真值标注");
  });

  it("默认提交 orchestrator 类型且使用同源 API", async () => {
    vi.stubGlobal("crypto", {
      randomUUID: () => "12345678-1234-1234-1234-123456789abc",
    });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ overall_status: "SUCCEEDED" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createOrchestratorRun({ request_text: "查询订单状态", order_id: "order-1" });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/runs",
      expect.objectContaining({ method: "POST" }),
    );
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.agent_type).toBe("orchestrator");
    expect(body.principal_id).toBe("admin-001");
    expect(body.asset_id).toBeUndefined();
  });

  it("demo账号显示演示订单选择、脱敏订单、场景说明和清除按钮", () => {
    const markup = renderToStaticMarkup(
      <DemoOrderPicker
        phase="ready"
        selectedOrderId="order-demo-001"
        onClear={() => undefined}
        onSelect={() => undefined}
        orders={[
          {
            demo_case_id: "logistics_delay",
            order_id: "order-demo-001",
            masked_order_id: "****0001",
            safe_label: "物流延迟演示｜运输中",
            order_status: "shipped",
            logistics_status: "in_transit",
            demo_scenario: "logistics_delay",
            description: "该订单可用于演示订单查询和物流延迟分析。",
            scenario_description: "该订单可用于演示订单查询和物流延迟分析。",
            store_id: "store-1",
            ordered_at: "2026-06-23T16:00:00Z",
            allowed_actions: ["get_order", "get_logistics"],
          },
        ]}
      />,
    );

    expect(markup).toContain("订单编号");
    expect(markup).toContain("选择演示订单");
    expect(markup).toContain("物流延迟演示｜运输中｜订单 ****0001");
    expect(markup).toContain("该订单可用于演示订单查询和物流延迟分析。");
    expect(markup).toContain("清除选择");
  });

  it("demo订单加载失败时显示简体中文错误提示", () => {
    const markup = renderToStaticMarkup(
      <DemoOrderPicker
        phase="failed"
        selectedOrderId=""
        onClear={() => undefined}
        onSelect={() => undefined}
        orders={[]}
      />,
    );

    expect(markup).toContain("演示订单加载失败，请稍后重试。");
    expect(markup).toContain('role="alert"');
  });

  it("demo任务示例只填充文本，不会提交任务", () => {
    const markup = renderToStaticMarkup(<DemoTaskExamples onPick={() => undefined} />);

    expect(markup).toContain("查询该订单当前的物流状态。");
    expect(markup).toContain("判断该订单是否存在物流延迟。");
    expect(markup).toContain("查询该订单是否符合退款条件，但不要发起退款。");
    expect(markup).toContain('type="button"');
    expect(markup).not.toContain('type="submit"');
  });

  it("演示订单接口使用受保护同源API", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ manifest_version: "public_demo_orders_v1", orders: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchDemoOrders();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/demo/orders",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("选择演示订单后提交完整order_id，普通账号手动输入能力保持在API契约中", async () => {
    vi.stubGlobal("crypto", {
      randomUUID: () => "12345678-1234-1234-1234-123456789abc",
    });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ overall_status: "SUCCEEDED" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createOrchestratorRun({
      request_text: "查询该订单当前物流状态",
      order_id: "558f22a4-e1fb-5f6a-992a-7cd07b88be01",
    });

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.order_id).toBe("558f22a4-e1fb-5f6a-992a-7cd07b88be01");
    expect(body.request_text).toBe("查询该订单当前物流状态");
  });

  it("管理员和store_operator显示演示订单下拉和手动输入，public_demo只显示下拉", () => {
    expect(canUseDemoOrderPicker({
      principal: { principal_id: "admin-001", role: "admin", account_type: "normal" },
      idle_expires_at: "",
      absolute_expires_at: "",
      security_policy_version: "stage7",
    })).toBe(true);
    expect(canUseManualOrderInput({
      principal: { principal_id: "admin-001", role: "admin", account_type: "normal" },
      idle_expires_at: "",
      absolute_expires_at: "",
      security_policy_version: "stage7",
    })).toBe(true);
    expect(canUseDemoOrderPicker({
      principal: {
        principal_id: "public-demo-001",
        role: "store_operator",
        account_type: "public_demo",
      },
      idle_expires_at: "",
      absolute_expires_at: "",
      security_policy_version: "stage7",
    })).toBe(true);
    expect(canUseManualOrderInput({
      principal: {
        principal_id: "public-demo-001",
        role: "store_operator",
        account_type: "public_demo",
      },
      idle_expires_at: "",
      absolute_expires_at: "",
      security_policy_version: "stage7",
    })).toBe(false);
  });
});
