// Typed client for the SpaceStudio REST API (docs/PHASE2_SPEC.md §6).
import type {
  Asset,
  AuditLog,
  Batch,
  EngineInfo,
  FileEntry,
  HealthReport,
  HistoryItem,
  ImportPreviewItem,
  Presets,
  Project,
  QueueState,
  Run,
  Settings,
  Stage,
  StageDetail,
  Video,
  VideoConfig,
  VideoCreate,
  VideoDetail,
} from "./types";

export const API_BASE = "/api";

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

/** Turn an error into a short message that tells the user what to do next. */
export function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0)
      return "Cannot reach the SpaceStudio backend. Start it (e.g. `spacestudio serve` in backend/) and make sure it listens on 127.0.0.1:8000.";
    if (err.status === 404) return `Not found: ${err.detail}`;
    if (err.status === 409) return `Not allowed right now: ${err.detail}`;
    if (err.status === 422) return `The request was rejected as invalid: ${err.detail}`;
    if (err.status >= 500) return `The backend hit an error (${err.status}): ${err.detail}. Check the backend log.`;
    return err.detail || `Request failed (${err.status})`;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}

function formatDetail(body: unknown): string {
  if (!body) return "";
  if (typeof body === "string") return body;
  const d = (body as { detail?: unknown }).detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d))
    return d
      .map((x) => {
        const o = x as { loc?: unknown[]; msg?: string };
        return `${(o.loc || []).join(".")}: ${o.msg ?? JSON.stringify(x)}`;
      })
      .join("; ");
  if (d) return JSON.stringify(d);
  return JSON.stringify(body);
}

async function request<T>(method: string, path: string, body?: unknown, init?: RequestInit): Promise<T> {
  let res: Response;
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  try {
    res = await fetch(API_BASE + path, {
      method,
      headers: body !== undefined && !isForm ? { "Content-Type": "application/json" } : undefined,
      body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
      ...init,
    });
  } catch {
    throw new ApiError(0, "network error");
  }
  const text = await res.text();
  let data: unknown = undefined;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      if (typeof data === "string" && /proxy|ECONNREFUSED/i.test(data)) throw new ApiError(0, "backend unreachable");
    }
    throw new ApiError(res.status, formatDetail(data) || res.statusText);
  }
  return data as T;
}

const get = <T>(p: string) => request<T>("GET", p);
const post = <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {});
const put = <T>(p: string, b?: unknown) => request<T>("PUT", p, b);
const patch = <T>(p: string, b?: unknown) => request<T>("PATCH", p, b);
const del = <T>(p: string) => request<T>("DELETE", p);

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : "";
}

const enc = encodeURIComponent;

export function fileUrl(videoId: string, path: string): string {
  return `${API_BASE}/videos/${enc(videoId)}/file${qs({ path })}`;
}

export const api = {
  health: () => get<{ ok: boolean; version: string }>("/health"),
  systemHealth: () => get<HealthReport>("/system/health"),

  settings: () => get<Settings>("/settings"),
  saveSettings: (s: Partial<Settings>) => put<Settings>("/settings", s),

  engines: () => get<EngineInfo[]>("/engines"),
  engine: (id: string) => get<EngineInfo>(`/engines/${enc(id)}`),
  checkEngine: (id: string) => post<EngineInfo>(`/engines/${enc(id)}/health`),
  patchEngine: (id: string, body: { enabled?: boolean; path?: string; pinned_commit?: string | null; approved?: boolean }) =>
    patch<EngineInfo>(`/engines/${enc(id)}`, body),
  setupEngine: (id: string, step: string) => post<Run>(`/engines/${enc(id)}/setup`, { confirm: true, step }),
  onboard: (url_or_path: string) => post<{ job_id: string }>("/engines/onboard", { url_or_path }),

  presets: () => get<Presets>("/presets"),
  generateIdeas: (body: { topic: string; count: number; video_type?: string }) =>
    post<{ ideas: { title: string; idea: string }[] }>("/ideas/generate", body),

  projects: () => get<Project[]>("/projects"),
  project: (id: string) => get<Project & { videos?: Video[] }>(`/projects/${enc(id)}`),
  createProject: (body: { name: string; description?: string }) => post<Project>("/projects", body),
  deleteProject: (id: string) => del<unknown>(`/projects/${enc(id)}`),

  videos: (q: { project_id?: string; batch_id?: string; status?: string } = {}) => get<Video[]>(`/videos${qs(q)}`),
  video: (id: string) => get<VideoDetail>(`/videos/${enc(id)}`),
  // POST /videos returns a VideoDetail ({video, stages, sessions, simulated}); callers want the Video.
  createVideo: async (body: VideoCreate) => {
    const r = await post<Video | { video: Video }>("/videos", body);
    return "video" in r && r.video ? r.video : (r as Video);
  },
  videoAction: (id: string, action: "start" | "pause" | "resume" | "cancel" | "retry") =>
    post<Video>(`/videos/${enc(id)}/${action}`),
  patchConfig: (id: string, cfg: Partial<VideoConfig>) => patch<Video>(`/videos/${enc(id)}/config`, cfg),

  stage: (id: string, key: string) => get<StageDetail>(`/videos/${enc(id)}/stages/${enc(key)}`),
  stageAction: (
    id: string,
    key: string,
    action: "approve" | "request-changes" | "reject" | "retry" | "skip" | "answer",
    body?: { feedback?: string; answer?: string },
  ) => post<Stage>(`/videos/${enc(id)}/stages/${enc(key)}/${action}`, body ?? {}),
  stageLogs: (id: string, key: string, tail = 500) =>
    get<{ lines: string[] }>(`/videos/${enc(id)}/stages/${enc(key)}/logs${qs({ tail })}`),

  files: (id: string, path = "") => get<FileEntry[]>(`/videos/${enc(id)}/files${qs({ path })}`),
  fileText: async (id: string, path: string): Promise<string> => {
    let res: Response;
    try {
      res = await fetch(fileUrl(id, path));
    } catch {
      throw new ApiError(0, "network error");
    }
    if (!res.ok) throw new ApiError(res.status, `${path}: ${res.statusText}`);
    return res.text();
  },
  fileJson: async <T>(id: string, path: string): Promise<T> => JSON.parse(await api.fileText(id, path)) as T,
  saveFile: (id: string, path: string, content: string) =>
    request<FileEntry>("PUT", `/videos/${enc(id)}/file${qs({ path })}`, { content }),
  assets: (id: string) => get<Asset[]>(`/videos/${enc(id)}/assets`),
  history: (id: string) => get<HistoryItem[]>(`/videos/${enc(id)}/history`),
  exportUrl: (id: string) => `${API_BASE}/videos/${enc(id)}/export`,

  batches: () => get<Batch[]>("/batches"),
  batch: (id: string) => get<Batch>(`/batches/${enc(id)}`),
  createBatch: (body: {
    project_id?: string | null;
    name: string;
    shared_config: Partial<VideoConfig>;
    items: { title: string; idea: string; overrides?: Partial<VideoConfig> }[];
    mode?: string;
  }) => post<Batch>("/batches", body),
  importBatch: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ items: ImportPreviewItem[]; errors?: string[] } | ImportPreviewItem[]>("POST", "/batches/import", fd);
  },
  batchAction: (id: string, action: "start" | "pause" | "resume" | "cancel", body?: { video_ids?: string[] }) =>
    post<Batch>(`/batches/${enc(id)}/${action}`, body ?? {}),
  batchExportUrl: (id: string) => `${API_BASE}/batches/${enc(id)}/export`,

  queue: () => get<QueueState>("/queue"),
  audit: (limit = 100) => get<AuditLog[]>(`/audit${qs({ limit })}`),
};

export type Api = typeof api;
