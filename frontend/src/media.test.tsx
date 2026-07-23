import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import MediaPage, { ClipReview } from "./MediaPage";
import {
  MEDIA_PRINCIPAL,
  clipDownloadUrl,
  createMediaTask,
  getAutomatedMediaClips,
  getMediaAsset,
  getMediaClips,
  getMediaEvents,
  getPendingMediaClips,
  getMediaTask,
  getMediaTranscript,
  mediaEventsStreamUrl,
  originalMediaUrl,
  reviewMediaClip,
  uploadMedia,
} from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("live video clipping workbench", () => {
  it("renders the simplified-Chinese business workbench and its main regions", () => {
    const markup = renderToStaticMarkup(<MediaPage />);
    expect(markup).toContain("直播切片");
    expect(markup).toContain("上传视频");
    expect(markup).toContain("最大 200 MiB");
    expect(markup).toContain("任务状态与处理进度");
    expect(markup).toContain("候选片段");
    expect(markup).toContain("恢复现有任务");
    expect(markup).toContain("暂无候选片段");
    expect(markup).toContain("暂无待审核片段");
  });

  it("does not render or retain ground-truth and manual-adjustment frontend code", () => {
    const markup = renderToStaticMarkup(<MediaPage />);
    const pageSource = readFileSync(new URL("./MediaPage.tsx", import.meta.url), "utf8");
    const apiSource = readFileSync(new URL("./api.ts", import.meta.url), "utf8");
    const stylesSource = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

    for (const removedText of [
      "真值标注模式",
      "真值標註模式",
      "提交真值",
      "冻结标注",
      "正确区间",
      "正确字幕",
      "正确商品",
      "人工修正字幕",
    ]) {
      expect(markup).not.toContain(removedText);
    }
    expect(pageSource).not.toMatch(/GroundTruth|ground-truth|annotation_id|annotator|gt-v1/);
    expect(pageSource).not.toMatch(/ADJUSTED|adjust-row|手动调整|人工修正/);
    expect(apiSource).not.toMatch(/GroundTruth|ground-truth|saveGroundTruth|ADJUSTED|adjusted_start_seconds/);
    expect(stylesSource).not.toMatch(/ground-truth|annotation-|adjust-row|boundary-row/);
    expect(markup).not.toContain('role="tab"');
    expect(markup).not.toContain("切换标注模式");
  });

  it("uses same-origin controlled upload endpoints", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "12345678-1234-1234-1234-123456789abc" });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            upload_id: "upload-1",
            asset_id: "asset-1",
            status: "PENDING_UPLOAD",
            upload_path: "/api/v1/media/uploads/upload-1",
            max_bytes: 209715200,
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ received_bytes: 3 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ asset_id: "asset-1", status: "READY" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    await uploadMedia(
      new File(["abc"], "video.mp4", { type: "video/mp4" }),
      "store-1",
      "semantic_simulation",
    );
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/media/uploads");
    const createPayload = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(createPayload).toMatchObject({
      evaluation_scope: "human_quality",
      fixture_type: "semantic_simulation",
      human_review_eligible: true,
    });
    expect(fetchMock.mock.calls[1][0]).toContain("/api/v1/media/uploads/upload-1");
    expect(String(fetchMock.mock.calls[1][0])).not.toContain("minio");
    expect(fetchMock.mock.calls[2][0]).toContain("/complete");
  });

  it("creates explicit live-video task and never exposes internal storage", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "12345678-1234-1234-1234-123456789abc" });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ task_id: "task-1", status: "QUEUED" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await createMediaTask("asset-1", {
      keywords: ["耳机"],
      minSeconds: 8,
      maxSeconds: 30,
      maxCandidates: 10,
    });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/media/asset-1/clip-tasks");
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.principal_id).toBe(MEDIA_PRINCIPAL);
    expect(body.run_id).toContain("run-media-");
    expect(JSON.stringify(body)).not.toContain("object_key");
    expect(originalMediaUrl("asset-1")).toMatch(/^\/api\//);
    expect(clipDownloadUrl("clip-1")).toMatch(/^\/api\//);
  });

  it("uses REST snapshot, event cursor, transcript and candidate recovery endpoints", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })),
    );
    vi.stubGlobal("fetch", fetchMock);
    await Promise.all([
      getMediaTask("task-1"),
      getMediaAsset("asset-1"),
      getMediaEvents("task-1", 9),
      getMediaClips("task-1"),
      getMediaTranscript("task-1"),
      getPendingMediaClips(),
      getAutomatedMediaClips(),
    ]);
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls).toContain(`/api/v1/tasks/task-1?principal_id=${MEDIA_PRINCIPAL}`);
    expect(urls).toContain(`/api/v1/media/asset-1?principal_id=${MEDIA_PRINCIPAL}`);
    expect(urls).toContain(`/api/v1/tasks/task-1/events?principal_id=${MEDIA_PRINCIPAL}&after=9`);
    expect(urls).toContain(`/api/v1/tasks/task-1/clips?principal_id=${MEDIA_PRINCIPAL}`);
    expect(urls).toContain(`/api/v1/tasks/task-1/transcript?principal_id=${MEDIA_PRINCIPAL}`);
    expect(urls).toContain(`/api/v1/media/reviews/pending?principal_id=${MEDIA_PRINCIPAL}`);
    expect(urls).toContain(`/api/v1/media/reviews/automated?principal_id=${MEDIA_PRINCIPAL}`);
    expect(mediaEventsStreamUrl("task-1")).toMatch(/^\/api\//);
  });

  it("renders candidate preview, scores, approval, rejection and download without editing", () => {
    const markup = renderToStaticMarkup(
      <ClipReview
        onReviewed={() => undefined}
        clip={{
          clip_id: "clip-1",
          task_id: "task-1",
          start_seconds: "1.0",
          end_seconds: "8.0",
          duration_seconds: "7.0",
          score: "0.8500",
          score_components: { keyword: "1.0000" },
          qc: { passed: true },
          review_status: "PENDING",
          asset_id: "asset-1",
          source_filename: "owned-product-demo.mp4",
          evaluation_scope: "human_quality",
          fixture_type: "user_provided",
          is_fixture: false,
          human_review_eligible: true,
          transcript_text: "蓝牙耳机限时优惠",
          video_url: "/api/video",
          subtitle_url: "/api/subtitle",
          thumbnail_url: "/api/thumb",
        }}
      />,
    );
    expect(markup).toContain("<video");
    expect(markup).toContain("片段预览");
    expect(markup).toContain("QC 通过");
    expect(markup).toContain("通过");
    expect(markup).toContain("驳回");
    expect(markup).toContain("下载");
    expect(markup).toContain("owned-product-demo.mp4");
    expect(markup).toContain("下载 SRT");
    expect(markup).toContain("画面字幕");
    expect(markup).toContain("开始 1.0s");
    expect(markup).toContain("蓝牙耳机限时优惠");
    expect(markup).not.toContain("Adjust");
    expect(markup).not.toContain("开始<input");
    expect(markup).not.toContain("结束<input");
    expect(markup).not.toContain("minio");
  });

  it("renders synthetic automated fixture as read-only and excludes review actions", () => {
    const markup = renderToStaticMarkup(
      <ClipReview
        onReviewed={() => undefined}
        clip={{
          clip_id: "clip-fixture-1",
          task_id: "task-fixture-1",
          start_seconds: "0.0",
          end_seconds: "4.0",
          duration_seconds: "4.0",
          score: "0.7500",
          score_components: { total: "0.7500" },
          qc: { passed: true },
          review_status: "PENDING",
          asset_id: "asset-fixture-1",
          source_filename: "supported-01.mp4",
          evaluation_scope: "automated_pipeline",
          fixture_type: "synthetic_color",
          is_fixture: true,
          human_review_eligible: false,
          transcript_text: "fixture ASR output",
          video_url: "/api/video",
          subtitle_url: "/api/subtitle",
          thumbnail_url: "/api/thumb",
        }}
      />,
    );
    expect(markup).toContain("自动化测试样本");
    expect(markup).toContain("synthetic_color");
    expect(markup).not.toContain(">通过<");
    expect(markup).not.toContain(">驳回<");
    expect(markup).not.toContain(">下载<");
  });

  it("sends normal approval decisions through the controlled review API", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ review_id: "review-1", status: "APPROVED" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await reviewMediaClip("clip-1", "APPROVED");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/media/clips/clip-1/reviews");
    const payload = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(payload).toMatchObject({
      principal_id: MEDIA_PRINCIPAL,
      decision: "APPROVED",
    });
    expect(payload).not.toHaveProperty("adjusted_start_seconds");
    expect(payload).not.toHaveProperty("adjusted_end_seconds");
  });

  it("surfaces structured API failures without leaking internal storage", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { message: "媒体任务不可用" } }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(getMediaTask("task-failed")).rejects.toThrow("媒体任务不可用");
  });
});
