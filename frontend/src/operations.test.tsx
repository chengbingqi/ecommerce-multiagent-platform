import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import OperationsPage from "./OperationsPage";
import { createOperationsRun } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("operations dashboard", () => {
  it("renders independent filters and fact/anomaly/explanation/advice sections", () => {
    const markup = renderToStaticMarkup(<OperationsPage />);
    expect(markup).toContain("运营分析看板");
    expect(markup).toContain("确定性事实");
    expect(markup).toContain("规则识别的异常");
    expect(markup).toContain("模型解释");
    expect(markup).toContain("运营建议");
  });

  it("uses same-origin runs API and explicit operations type", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "12345678-1234-1234-1234-123456789abc" });
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "SUCCEEDED" }), { status: 202, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await createOperationsRun({ store_id: "store", start_date: "2026-06-24", end_date: "2026-07-01", topic: "OVERVIEW", granularity: "day", comparison_mode: "none" });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/runs", expect.objectContaining({ method: "POST" }));
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.agent_type).toBe("operations");
    expect(body.principal_id).toBe("agent-operations");
  });
});
