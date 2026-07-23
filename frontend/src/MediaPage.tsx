import { FormEvent, useEffect, useState } from "react";

import {
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
  type MediaAsset,
  type MediaClip,
  type MediaEvent,
  type MediaTask,
  type MediaTranscript,
} from "./api";

const storeId = "b39e17d7-c6aa-5aaf-adfc-b84c80d0f8f1";

export function ClipReview({ clip, onReviewed }: { clip: MediaClip; onReviewed: () => void }) {
  const productLabel = clip.is_multi_product ? "多商品混合" : (clip.product_label || "未识别商品");
  const productSource = clip.product_source ?? "unknown";
  const sourceLabels: Record<string, string> = {
    catalog: "商品目录",
    transcript: "语音字幕",
    ocr: "画面文字",
    combined: "组合证据",
    unknown: "无可靠证据",
  };
  async function decide(decision: "APPROVED" | "REJECTED") {
    await reviewMediaClip(clip.clip_id, decision);
    onReviewed();
  }

  return (
    <article className="clip-card">
      <video aria-label={`片段预览 ${clip.clip_id.slice(0, 8)}`} controls preload="metadata" src={clipDownloadUrl(clip.clip_id, "video")} />
      <div><b>候选片段 {clip.clip_id.slice(0, 8)}</b><span>评分 {clip.score}</span></div>
      <p><b>来源：</b>{clip.source_filename} · {clip.is_fixture ? "测试素材" : "用户上传"}</p>
      <p className="version">{clip.evaluation_scope} / {clip.fixture_type}</p>
      <small>开始 {clip.start_seconds}s · 结束 {clip.end_seconds}s · 时长 {clip.duration_seconds}s · 技术 QC {clip.qc.passed ? "通过" : "失败"}</small>
      <p><b>商品标签：</b>{productLabel}{clip.is_multi_product && "（需要审核）"}</p>
      <p><b>标签置信度：</b>{clip.product_confidence ?? "0"} · <b>证据来源：</b>{sourceLabels[productSource] ?? productSource}</p>
      {(clip.product_evidence_spans ?? []).length > 0 && <details><summary>商品标签证据</summary><ul>{(clip.product_evidence_spans ?? []).map((span) => <li key={`${span.start}-${span.end}-${span.text}`}>{span.start}s–{span.end}s：{span.text}</li>)}</ul></details>}
      <p><b>画面字幕：</b>{clip.qc.burned_subtitles ? "已烧录" : "未烧录"}</p>
      <p><b>字幕：</b>{clip.corrected_transcript_text || clip.raw_transcript_text || clip.transcript_text || "暂无字幕"}</p>
      <p><b>语音识别置信度：</b>{clip.asr_confidence ?? "暂无数据"}</p>
      <p className="version">{clip.asr_config_version ?? "旧ASR配置"} · {clip.segmentation_version ?? "旧切片规则"}</p>
      <details><summary>切片详情</summary><pre>{JSON.stringify(clip.boundary_evidence ?? {}, null, 2)}</pre></details>
      <a href={originalMediaUrl(clip.asset_id)} target="_blank" rel="noreferrer">查看原视频</a>
      <details><summary>评分详情</summary><pre>{JSON.stringify(clip.score_components, null, 2)}</pre></details>
      {clip.human_review_eligible ? <div className="review-actions">
        <button type="button" onClick={() => void decide("APPROVED")}>通过</button>
        <button type="button" className="danger" onClick={() => void decide("REJECTED")}>驳回</button>
        <a href={clipDownloadUrl(clip.clip_id, "video")} download>下载成片</a>
        <a href={clipDownloadUrl(clip.clip_id, "subtitle")} download>下载 SRT</a>
      </div> : <p className="hint">自动化测试样本：仅用于流水线验证，不进入人工审核。</p>}
      <p>审核状态：{clip.review_status}</p>
    </article>
  );
}

export default function MediaPage() {
  const [phase, setPhase] = useState<"idle" | "uploading" | "processing" | "ready" | "failed">("idle");
  const [asset, setAsset] = useState<MediaAsset>();
  const [task, setTask] = useState<MediaTask>();
  const [events, setEvents] = useState<MediaEvent[]>([]);
  const [clips, setClips] = useState<MediaClip[]>([]);
  const [reviewQueue, setReviewQueue] = useState<MediaClip[]>([]);
  const [automatedQueue, setAutomatedQueue] = useState<MediaClip[]>([]);
  const [transcript, setTranscript] = useState<MediaTranscript>();
  const [error, setError] = useState("");
  const activeTaskId = task?.task_id;
  const activeTaskStatus = task?.status;

  async function refresh(taskId: string) {
    const snapshot = await getMediaTask(taskId);
    setTask(snapshot);
    const sourceAsset = await getMediaAsset(snapshot.asset_id);
    setAsset(sourceAsset);
    const rows = await getMediaEvents(taskId);
    setEvents(rows);
    if (snapshot.status === "SUCCEEDED") {
      const [candidateRows, transcriptResult] = await Promise.all([
        getMediaClips(taskId),
        getMediaTranscript(taskId),
      ]);
      setClips(candidateRows);
      setTranscript(transcriptResult);
      setPhase("ready");
    } else if (snapshot.status === "FAILED" || snapshot.status === "CANCELLED") {
      setPhase("failed");
      setError(snapshot.error_code || "媒体任务未完成");
    } else {
      setPhase("processing");
    }
  }

  useEffect(() => {
    const saved = localStorage.getItem("stage5-media-task");
    if (saved) void refresh(saved).catch(() => localStorage.removeItem("stage5-media-task"));
    void getPendingMediaClips().then(setReviewQueue).catch(() => undefined);
    void getAutomatedMediaClips().then(setAutomatedQueue).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!activeTaskId || !activeTaskStatus || ["SUCCEEDED", "FAILED", "CANCELLED"].includes(activeTaskStatus)) return;
    const stream = new EventSource(mediaEventsStreamUrl(activeTaskId));
    const update = () => void refresh(activeTaskId);
    for (const name of ["stage_progress", "task_terminal", "await_human_review"]) {
      stream.addEventListener(name, update);
    }
    stream.onerror = () => void refresh(activeTaskId);
    return () => stream.close();
  }, [activeTaskId, activeTaskStatus]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const file = form.get("video");
    if (!(file instanceof File) || !file.size) {
      setError("请选择 MP4 视频。");
      return;
    }
    setError("");
    setPhase("uploading");
    try {
      const fixtureType = String(form.get("fixture_type")) === "semantic_simulation"
        ? "semantic_simulation"
        : "user_provided";
      const uploaded = await uploadMedia(file, storeId, fixtureType);
      setAsset(uploaded);
      const created = await createMediaTask(uploaded.asset_id, {
        keywords: String(form.get("keywords") || "").split(/[，,]/).map((item) => item.trim()).filter(Boolean),
        minSeconds: Number(form.get("min_seconds")),
        maxSeconds: Number(form.get("max_seconds")),
        maxCandidates: Number(form.get("max_candidates")),
      });
      setTask(created);
      setPhase("processing");
      localStorage.setItem("stage5-media-task", created.task_id);
    } catch (reason) {
      setPhase("failed");
      setError(reason instanceof Error ? reason.message : "媒体任务失败");
    }
  }

  async function restore(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const taskId = String(form.get("existing_task_id") || "").trim();
    if (!taskId) return;
    setError("");
    try {
      await refresh(taskId);
      localStorage.setItem("stage5-media-task", taskId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法恢复现有媒体任务");
    }
  }

  return (
    <main className="shell media-shell">
      <nav><a href="/">系统状态</a><a href="/workbench">多 Agent 工作台</a><a href="/operations">运营分析</a><a aria-current="page" href="/media">直播切片</a></nav>
      <header>
        <p className="eyebrow">智能直播内容工作台</p>
        <h1>直播切片</h1>
        <p className="subtitle">上传直播视频，自动生成候选片段并完成预览、审核和下载。</p>
      </header>
      <form className="media-form" onSubmit={(event) => void submit(event)}>
        <h2>上传视频</h2>
        <label>视频文件<input name="video" type="file" accept="video/mp4" required /></label>
        <p className="hint">MP4 / H.264 / AAC，最大 200 MiB、10 分钟、1920×1080、60fps。</p>
        <label>素材声明<select name="fixture_type" defaultValue="user_provided"><option value="user_provided">本人拥有或已获授权的真实素材</option><option value="semantic_simulation">具有电商语义的模拟直播素材</option></select></label>
        <p className="hint">不得上传来源或授权不明的网络影片；模拟素材必须包含商品展示、口播、优惠信息和场景变化。</p>
        <label>商品关键词<input name="keywords" defaultValue="蓝牙耳机,优惠" maxLength={400} /></label>
        <div className="media-controls">
          <label>最短片长<input name="min_seconds" type="number" min="3" max="60" defaultValue="8" /></label>
          <label>最长片长<input name="max_seconds" type="number" min="5" max="120" defaultValue="30" /></label>
          <label>候选数量<input name="max_candidates" type="number" min="1" max="20" defaultValue="10" /></label>
        </div>
        <button disabled={phase === "uploading" || phase === "processing"} type="submit">{phase === "uploading" ? "上传中…" : phase === "processing" ? "处理中…" : "创建切片任务"}</button>
      </form>
      <form className="media-form restore-form" onSubmit={(event) => void restore(event)}>
        <h2>恢复现有任务</h2>
        <label>任务编号<input name="existing_task_id" placeholder="请输入任务编号" required /></label>
        <p className="hint">加载现有任务、原视频和字幕，不会重新上传或创建任务。</p>
        <button type="submit">加载任务</button>
      </form>
      {error && <div role="alert" className="alert"><strong>媒体任务失败</strong><span>{error}</span></div>}
      {asset && <section className="media-panel"><h2>原视频与资产</h2><video controls src={originalMediaUrl(asset.asset_id)} /><p>{asset.display_filename} · {asset.status} · {asset.duration_seconds}s</p></section>}
      <section className="media-panel"><h2>任务状态与处理进度</h2>{task ? <><progress max="100" value={task.progress} /><p>{task.stage} · {task.progress}% · {task.status}</p><ol className="event-list">{events.map((event) => <li key={event.event_id}>{event.stage} — {event.message}</li>)}</ol></> : <p className="hint">创建切片任务后可在这里查看处理进度。</p>}</section>
      {transcript && <section className="media-panel"><h2>ASR 文本与时间轴</h2><p className="version">{transcript.asr_model_version} · {transcript.asr_config_version ?? "历史配置"}</p><div className="transcript">{transcript.segments.map((segment) => <p key={`${segment.start_seconds}-${segment.end_seconds}`}><time>{segment.start_seconds}s</time><span>{segment.raw_text ?? segment.text}</span>{segment.corrected_text && <span> → {segment.corrected_text}</span>}<small> confidence {segment.confidence ?? "N/A"}</small></p>)}</div></section>}
      <section className="media-panel"><h2>候选片段</h2><p className="hint">预览 Agent 生成的片段和字幕，通过或驳回审核结果，也可直接下载。</p>{clips.length > 0 ? <div className="clip-grid">{clips.map((clip) => <ClipReview key={clip.clip_id} clip={clip} onReviewed={() => void refresh(clip.task_id)} />)}</div> : <p>暂无候选片段。</p>}</section>
      <section className="media-panel"><h2>待审核片段</h2><p className="hint">这里展示已完成自动处理并等待审核的候选片段。</p>{reviewQueue.length > 0 ? <div className="clip-grid">{reviewQueue.map((clip) => <ClipReview key={`queue-${clip.clip_id}`} clip={clip} onReviewed={() => void getPendingMediaClips().then(setReviewQueue)} />)}</div> : <p>暂无待审核片段。</p>}</section>
      {automatedQueue.length > 0 && <section className="media-panel"><details><summary>自动化测试样本（{automatedQueue.length}，不计入人工审核）</summary><p className="hint">这些测试候选仅验证媒体流水线。</p><div className="clip-grid">{automatedQueue.map((clip) => <ClipReview key={`automated-${clip.clip_id}`} clip={clip} onReviewed={() => undefined} />)}</div></details></section>}
    </main>
  );
}
