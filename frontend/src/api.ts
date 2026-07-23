export type ComponentState = "ok" | "unavailable";

const developmentPrincipalInput = import.meta.env.DEV;
let csrfToken: string | undefined;
let cachedSession: SessionInfo | undefined;

function withPrincipal(path: string, principalId: string): string {
  if (!developmentPrincipalInput || cachedSession) return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}principal_id=${encodeURIComponent(principalId)}`;
}

async function secureFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  let headers = init.headers;
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method) && csrfToken) {
    headers = new Headers(init.headers);
    headers.set("X-CSRF-Token", csrfToken);
  }
  return fetch(input, { ...init, headers, credentials: "include" });
}

export interface SessionPrincipal {
  principal_id: string;
  role: string;
  store_id?: string;
  customer_ref?: string;
  account_type?: "normal" | "public_demo";
}

export interface SessionInfo {
  principal: SessionPrincipal;
  idle_expires_at: string;
  absolute_expires_at: string;
  security_policy_version: string;
}

export async function login(username: string, password: string): Promise<SessionInfo> {
  const response = await secureFetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await jsonOrError<SessionInfo & { csrf_token: string }>(response);
  csrfToken = data.csrf_token;
  cachedSession = data;
  return data;
}

export async function currentSession(): Promise<SessionInfo> {
  const session = await jsonOrError<SessionInfo>(await secureFetch("/api/v1/auth/me"));
  const csrf = await jsonOrError<{ csrf_token: string }>(
    await secureFetch("/api/v1/auth/csrf"),
  );
  csrfToken = csrf.csrf_token;
  cachedSession = session;
  return session;
}

export async function logout(): Promise<void> {
  await jsonOrError(await secureFetch("/api/v1/auth/logout", { method: "POST" }));
  csrfToken = undefined;
  cachedSession = undefined;
}

export function getCachedSession(): SessionInfo | undefined {
  return cachedSession;
}

export interface ReadyResponse {
  status: ComponentState;
  service: "api";
  environment: string;
  components: {
    postgres: { status: ComponentState };
    redis: { status: ComponentState };
  };
}

export interface HealthResult {
  data: ReadyResponse;
  httpOk: boolean;
}

export type AgentRunStatus =
  | "PENDING"
  | "RUNNING"
  | "WAITING_APPROVAL"
  | "SUCCEEDED"
  | "PARTIAL_SUCCESS"
  | "FAILED"
  | "CANCELLED";

export interface OrchestratorSubtask {
  task_id: string;
  child_run_id?: string;
  external_task_id?: string;
  agent_type: "customer_service" | "operations" | "media";
  dependency_ids: string[];
  status: AgentRunStatus;
  duration_ms?: number;
  error_code?: string;
  approval_id?: string;
  evidence_refs: string[];
  tool_scope: string[];
}

export interface OrchestratorOutput {
  run_id: string;
  thread_id: string;
  trace_id: string;
  overall_status: AgentRunStatus;
  route: {
    intents: string[];
    confidence: number;
    risk_level: string;
    agent_targets: Array<"customer_service" | "operations" | "media">;
    required_inputs: string[];
    missing_inputs: string[];
    warnings: string[];
    route_version: string;
  };
  plan: Array<{
    task_id: string;
    agent_type: string;
    dependency_ids: string[];
    expected_output_schema: string;
  }>;
  subtasks: OrchestratorSubtask[];
  customer_service_result?: Record<string, unknown>;
  operations_result?: OperationsOutput;
  media_result?: {
    task_id: string;
    asset_id: string;
    status: string;
    experimental: true;
    human_review_required: true;
    auto_publish: false;
    review_status: string;
    warning: string;
    evidence_refs: string[];
  };
  evidence_refs: string[];
  partial_failures: Array<{ subtask_id: string; agent_type: string; error_code: string }>;
  warnings: string[];
  need_human: boolean;
  completion_rule: string;
  error?: { error_code: string; message: string };
  versions: Record<string, string>;
}

export interface OrchestratorRequest {
  request_text: string;
  order_id?: string;
  store_id?: string;
  start_date?: string;
  end_date?: string;
  topic?: string;
  asset_id?: string;
  product_keywords?: string[];
}

export interface DemoOrderOption {
  demo_case_id: string;
  order_id: string;
  masked_order_id: string;
  safe_label: string;
  order_status: string;
  logistics_status: string;
  demo_scenario: string;
  description: string;
  scenario_description: string;
  store_id: string;
  ordered_at: string;
  allowed_actions: string[];
}

export interface DemoOrdersResponse {
  manifest_version: string;
  account_type: "normal" | "public_demo";
  operations_store_id: string;
  orders: DemoOrderOption[];
}

export interface AgentEvent {
  event_id: string;
  run_id: string;
  event_type: string;
  status: string;
  payload_summary: Record<string, unknown>;
  trace_id: string;
  timestamp: string;
}

export const ORCHESTRATOR_PRINCIPAL = "admin-001";

export async function createOrchestratorRun(
  input: OrchestratorRequest,
): Promise<OrchestratorOutput> {
  const suffix = crypto.randomUUID().replaceAll("-", "");
  return jsonOrError<OrchestratorOutput>(
    await secureFetch("/api/v1/runs", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        agent_type: "orchestrator",
        ...(developmentPrincipalInput && !cachedSession
          ? { principal_id: ORCHESTRATOR_PRINCIPAL }
          : {}),
        run_id: `run-orch-${suffix}`,
        thread_id: `thread-orch-${suffix}`,
        trace_id: `trace-orch-${suffix}`,
        ...input,
      }),
    }),
  );
}

export async function getOrchestratorRun(runId: string): Promise<OrchestratorOutput> {
  return jsonOrError<OrchestratorOutput>(
    await secureFetch(withPrincipal(`/api/v1/runs/${runId}`, ORCHESTRATOR_PRINCIPAL)),
  );
}

export async function fetchDemoOrders(): Promise<DemoOrdersResponse> {
  return jsonOrError<DemoOrdersResponse>(await secureFetch("/api/v1/demo/orders"));
}

export async function getOrchestratorEvents(
  runId: string,
  afterEventId?: string,
): Promise<AgentEvent[]> {
  const after = afterEventId ? `&after_event_id=${encodeURIComponent(afterEventId)}` : "";
  const base = withPrincipal(`/api/v1/runs/${runId}/events`, ORCHESTRATOR_PRINCIPAL);
  return jsonOrError<AgentEvent[]>(
    await secureFetch(`${base}${developmentPrincipalInput ? after : after.replace("&", "?")}`),
  );
}

export function orchestratorEventsStreamUrl(runId: string): string {
  return withPrincipal(`/api/v1/runs/${runId}/events/stream`, ORCHESTRATOR_PRINCIPAL);
}

export async function decideApproval(
  approvalId: string,
  decision: "approved" | "rejected",
): Promise<void> {
  const suffix = crypto.randomUUID().replaceAll("-", "");
  await jsonOrError(
    await secureFetch(`/api/v1/approvals/${approvalId}/decision`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        ...(developmentPrincipalInput ? { principal_id: ORCHESTRATOR_PRINCIPAL } : {}),
        trace_id: `trace-approval-${suffix}`,
      }),
    }),
  );
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResult> {
  const response = await secureFetch("/api/v1/health/ready", {
    headers: { Accept: "application/json" },
    signal,
  });

  let data: ReadyResponse;
  try {
    data = (await response.json()) as ReadyResponse;
  } catch {
    throw new Error("后端返回了无法解析的响应");
  }

  if (!data.components?.postgres || !data.components?.redis) {
    throw new Error("后端健康响应缺少组件状态");
  }

  return { data, httpOk: response.ok };
}

export interface OperationsMetric {
  metric_key: string;
  display_name: string;
  value: number | string;
  unit: string;
  evidence_refs: string[];
}

export interface OperationsOutput {
  run_id: string;
  thread_id: string;
  trace_id: string;
  status: "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  store_id: string;
  period: { start: string; end: string; semantics: "[start,end)" };
  granularity: "day" | "week";
  metrics_version: string;
  anomaly_rules_version: string;
  prompt_version: string;
  metrics: OperationsMetric[];
  trend_series: Array<{
    bucket_start: string;
    gmv: string;
    order_count: number;
    conversion_rate: string;
    evidence_refs: string[];
  }>;
  anomalies: Array<{
    anomaly_id: string;
    anomaly_type: string;
    rule_id: string;
    severity: string;
    observed_value: number | string;
    threshold: string;
    explanation: string;
    evidence_refs: string[];
  }>;
  evidence_rows: Array<{
    evidence_ref: string;
    record_type: string;
    observed: number | string;
    scenario?: string;
  }>;
  explanation: string;
  recommendations: Array<{
    priority: string;
    recommendation: string;
    based_on_anomaly_ids: string[];
    evidence_refs: string[];
  }>;
  no_data: boolean;
  warnings: string[];
  need_human: boolean;
  error?: { error_code: string; message: string };
}

export interface OperationsRequest {
  store_id: string;
  start_date: string;
  end_date: string;
  topic: string;
  granularity: "day" | "week";
  comparison_mode: "previous_period" | "none";
  user_question?: string;
}

export async function createOperationsRun(input: OperationsRequest): Promise<OperationsOutput> {
  const suffix = crypto.randomUUID().replaceAll("-", "");
  const response = await secureFetch("/api/v1/runs", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({
      agent_type: "operations",
      ...(developmentPrincipalInput ? { principal_id: "agent-operations" } : {}),
      run_id: `run-ops-${suffix}`,
      thread_id: `thread-ops-${suffix}`,
      trace_id: `trace-ops-${suffix}`,
      ...input,
    }),
  });
  const data = (await response.json()) as OperationsOutput | { error?: { message?: string } };
  if (!response.ok) {
    throw new Error("error" in data && data.error?.message ? data.error.message : "运营分析请求失败");
  }
  return data as OperationsOutput;
}

export const MEDIA_PRINCIPAL = "admin-001";

export interface MediaUpload {
  upload_id: string;
  asset_id: string;
  status: string;
  upload_path: string;
  max_bytes: number;
}

export interface MediaAsset {
  asset_id: string;
  store_id: string;
  display_filename: string;
  status: string;
  content_hash?: string;
  size_bytes: number;
  duration_seconds?: string;
  width?: number;
  height?: number;
  fps?: string;
  evaluation_scope: string;
  fixture_type: string;
  human_review_eligible: boolean;
  pipeline_version: string;
}

export interface MediaTask {
  task_id: string;
  run_id: string;
  thread_id: string;
  trace_id: string;
  asset_id: string;
  status: string;
  stage: string;
  progress: number;
  pipeline_version: string;
  error_code?: string;
}

export interface MediaEvent {
  event_id: string;
  event_type: string;
  task_id: string;
  status: string;
  progress: number;
  stage: string;
  message: string;
  timestamp: string;
}

export interface MediaClip {
  clip_id: string;
  task_id: string;
  start_seconds: string;
  end_seconds: string;
  duration_seconds: string;
  score: string;
  score_components: Record<string, string>;
  qc: Record<string, unknown>;
  review_status: string;
  asset_id: string;
  source_filename: string;
  evaluation_scope: string;
  fixture_type: string;
  is_fixture: boolean;
  human_review_eligible: boolean;
  transcript_text: string;
  product_label?: string;
  product_confidence?: string;
  product_source?: string;
  product_evidence?: {
    canonical_name: string;
    confidence: string;
    source: string;
    evidence_spans: Array<{ start: string; end: string; text: string }>;
    matched_sku?: string;
    aliases: string[];
    conflicting_products: string[];
  };
  product_evidence_spans?: Array<{ start: string; end: string; text: string }>;
  is_multi_product?: boolean;
  raw_transcript_text?: string;
  corrected_transcript_text?: string;
  asr_confidence?: string;
  predicted_start_seconds?: string;
  predicted_end_seconds?: string;
  snapped_start_seconds?: string;
  snapped_end_seconds?: string;
  boundary_evidence?: Record<string, unknown>;
  segmentation_version?: string;
  asr_config_version?: string;
  video_url?: string;
  subtitle_url?: string;
  thumbnail_url?: string;
}

export interface MediaTranscript {
  transcript_ref: string;
  language: string;
  asr_model_version: string;
  asr_config_version?: string;
  correction_version?: string;
  segments: Array<{ start_seconds: string; end_seconds: string; text: string; raw_text?: string; corrected_text?: string; confidence?: string }>;
}

async function jsonOrError<T extends object>(response: Response): Promise<T> {
  const data = (await response.json()) as T | { error?: { message?: string } };
  if (!response.ok) {
    throw new Error("error" in data && data.error?.message ? data.error.message : "媒体请求失败");
  }
  return data as T;
}

export async function uploadMedia(
  file: File,
  storeId: string,
  fixtureType: "user_provided" | "semantic_simulation" = "user_provided",
): Promise<MediaAsset> {
  const suffix = crypto.randomUUID().replaceAll("-", "");
  const created = await jsonOrError<MediaUpload>(
    await secureFetch("/api/v1/media/uploads", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(developmentPrincipalInput ? { principal_id: MEDIA_PRINCIPAL } : {}),
        store_id: storeId,
        display_filename: file.name,
        idempotency_key: `upload-${suffix}`,
        evaluation_scope: "human_quality",
        fixture_type: fixtureType,
        human_review_eligible: true,
      }),
    }),
  );
  await jsonOrError(
    await secureFetch(withPrincipal(created.upload_path, MEDIA_PRINCIPAL), {
      method: "PUT",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    }),
  );
  return jsonOrError<MediaAsset>(
    await secureFetch(
      withPrincipal(`/api/v1/media/uploads/${created.upload_id}/complete`, MEDIA_PRINCIPAL),
      { method: "POST", headers: { Accept: "application/json" } },
    ),
  );
}

export async function createMediaTask(
  assetId: string,
  input: {
    keywords: string[];
    minSeconds: number;
    maxSeconds: number;
    maxCandidates: number;
  },
): Promise<MediaTask> {
  const suffix = crypto.randomUUID().replaceAll("-", "");
  return jsonOrError<MediaTask>(
    await secureFetch(`/api/v1/media/${assetId}/clip-tasks`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(developmentPrincipalInput ? { principal_id: MEDIA_PRINCIPAL } : {}),
        product_keywords: input.keywords,
        min_clip_seconds: input.minSeconds,
        max_clip_seconds: input.maxSeconds,
        max_candidates: input.maxCandidates,
        subtitle_language: "zh",
        idempotency_key: `media-task-${suffix}`,
        run_id: `run-media-${suffix}`,
        thread_id: `thread-media-${suffix}`,
        trace_id: `trace-media-${suffix}`,
      }),
    }),
  );
}

export async function getMediaTask(taskId: string): Promise<MediaTask> {
  return jsonOrError<MediaTask>(
    await secureFetch(withPrincipal(`/api/v1/tasks/${taskId}`, MEDIA_PRINCIPAL)),
  );
}

export async function getMediaAsset(assetId: string): Promise<MediaAsset> {
  return jsonOrError<MediaAsset>(
    await secureFetch(withPrincipal(`/api/v1/media/${assetId}`, MEDIA_PRINCIPAL)),
  );
}

export async function getMediaEvents(taskId: string, after = 0): Promise<MediaEvent[]> {
  const base = withPrincipal(`/api/v1/tasks/${taskId}/events`, MEDIA_PRINCIPAL);
  return jsonOrError<MediaEvent[]>(
    await secureFetch(`${base}${developmentPrincipalInput ? "&" : "?"}after=${after}`),
  );
}

export async function getMediaClips(taskId: string): Promise<MediaClip[]> {
  return jsonOrError<MediaClip[]>(
    await secureFetch(withPrincipal(`/api/v1/tasks/${taskId}/clips`, MEDIA_PRINCIPAL)),
  );
}

export async function getPendingMediaClips(): Promise<MediaClip[]> {
  return jsonOrError<MediaClip[]>(
    await secureFetch(withPrincipal("/api/v1/media/reviews/pending", MEDIA_PRINCIPAL)),
  );
}

export async function getAutomatedMediaClips(): Promise<MediaClip[]> {
  return jsonOrError<MediaClip[]>(
    await secureFetch(withPrincipal("/api/v1/media/reviews/automated", MEDIA_PRINCIPAL)),
  );
}

export async function getMediaTranscript(taskId: string): Promise<MediaTranscript> {
  return jsonOrError<MediaTranscript>(
    await secureFetch(withPrincipal(`/api/v1/tasks/${taskId}/transcript`, MEDIA_PRINCIPAL)),
  );
}

export async function reviewMediaClip(
  clipId: string,
  decision: "APPROVED" | "REJECTED",
): Promise<void> {
  await jsonOrError(
    await secureFetch(`/api/v1/media/clips/${clipId}/reviews`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(developmentPrincipalInput ? { principal_id: MEDIA_PRINCIPAL } : {}),
        decision,
        rubric: {},
      }),
    }),
  );
}

export function originalMediaUrl(assetId: string): string {
  return withPrincipal(`/api/v1/media/${assetId}/content`, MEDIA_PRINCIPAL);
}

export function clipDownloadUrl(clipId: string, kind = "video"): string {
  return withPrincipal(`/api/v1/media/clips/${clipId}/download?kind=${kind}`, MEDIA_PRINCIPAL);
}

export function mediaEventsStreamUrl(taskId: string): string {
  return withPrincipal(`/api/v1/tasks/${taskId}/events/stream`, MEDIA_PRINCIPAL);
}
