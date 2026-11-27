// Types mirroring backend/spacestudio/models.py, interchange/schema.py and docs/PHASE2_SPEC.md §6.
// Fields the backend may omit are optional; the UI treats everything defensively.

export type StageStatus =
  | "pending"
  | "queued"
  | "running"
  | "awaiting_approval"
  | "needs_input"
  | "completed"
  | "failed"
  | "cancelled"
  | "skipped";

export type JobStatus =
  | "draft"
  | "queued"
  | "running"
  | "paused"
  | "awaiting_approval"
  | "needs_input"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export type AnyStatus = StageStatus | JobStatus | string;

export interface Project {
  id: string;
  name: string;
  description: string;
  created_at: string;
}

export interface Batch {
  id: string;
  project_id: string;
  name: string;
  shared_config: Partial<VideoConfig>;
  status: string;
  created_at: string;
  videos?: Video[];
  progress?: { total?: number; completed?: number; failed?: number; running?: number; [k: string]: unknown } | number;
}

export interface Video {
  id: string;
  project_id: string;
  batch_id?: string | null;
  title: string;
  idea: string;
  config: Partial<VideoConfig>;
  mode: "collaborative" | "native" | string;
  primary_engine?: string | null;
  status: JobStatus;
  paused?: boolean;
  workspace: string;
  created_at: string;
  updated_at: string;
  cost_usd: number;
  error?: string | null;
}

export interface Stage {
  id: string;
  video_id: string;
  key: string;
  name: string;
  description: string;
  owner: string;
  executor: string;
  lane: string;
  capability: string;
  depends_on: string[];
  gate: boolean;
  engine_gate?: boolean;
  required: boolean;
  status: StageStatus;
  attempt: number;
  order: number;
  started_at?: string | null;
  completed_at?: string | null;
  progress?: number | null;
  last_activity?: string | null;
  inputs: string[];
  outputs: string[];
  expected_outputs?: string[];
  params?: Record<string, unknown>;
  warnings: string[];
  error?: string | null;
  pending_feedback?: string | null;
  question?: string | null;
  summary?: string | null;
  cost_usd: number;
  session_id?: string | null;
}

export interface StageVersion {
  id: string;
  stage_id: string;
  version: number;
  created_at: string;
  files: string[];
  snapshot_dir: string;
  note: string;
  approved: boolean;
}

export interface Approval {
  id: string;
  stage_id: string;
  decision: "approve" | "request_changes" | "reject" | "skip" | "answer" | string;
  feedback: string;
  created_at: string;
}

export interface ClaudeSession {
  id: string;
  video_id: string;
  lane: string;
  session_id: string;
  model?: string | null;
  cwd: string;
  created_at: string;
  last_used_at: string;
  total_cost_usd: number;
}

export interface Run {
  id: string;
  stage_id: string;
  video_id: string;
  kind: "claude" | "native" | "setup" | string;
  pid?: number | null;
  started_at: string;
  ended_at?: string | null;
  exit_code?: number | null;
  cost_usd: number;
  log_path: string;
  state: "running" | "done" | "failed" | "cancelled" | "interrupted" | string;
  model?: string | null;
  simulated?: boolean;
}

export interface Asset {
  id: string;
  video_id: string;
  path: string;
  kind: string;
  stage_key: string;
  source: string;
  license: string;
  credit: string;
  created_at: string;
}

export interface AuditLog {
  id: number;
  at: string;
  actor: string;
  event: string;
  video_id?: string | null;
  details: Record<string, unknown>;
}

export interface VideoDetail {
  video: Video;
  stages: Stage[];
  sessions: ClaudeSession[];
  simulated: boolean;
}

export interface StageDetail {
  stage: Stage;
  versions: StageVersion[];
  approvals: Approval[];
  runs: Run[];
  log_tail: string[];
}

export interface HealthItem {
  id: string;
  name: string;
  status: "ok" | "warning" | "missing" | "error" | "failed" | "passed" | string;
  detail?: string;
  fix?: string | null;
}

export interface EngineHealth {
  engine: string;
  ok: boolean;
  zero_cost_ready: boolean;
  items: HealthItem[];
  commit?: string | null;
}

export interface SetupStep {
  id: string;
  title: string;
  command: string;
  cwd?: string;
}

export interface EngineInfo {
  id: string;
  name: string;
  description: string;
  license: string;
  enabled: boolean;
  approved: boolean;
  path: string;
  capabilities: Record<string, { supported: boolean; detail?: string }>;
  options: Record<string, unknown>;
  cost: { zero_cost: boolean | "partial"; paid_env: string[]; notes?: string };
  health: EngineHealth | null;
  setup_steps: SetupStep[];
}

export interface HealthReport {
  tools: HealthItem[];
  engines: EngineHealth[];
  claude: { bin: string; version?: string | null; simulated: boolean; ok: boolean; warning?: string };
  zero_cost_ready: boolean;
  engines_ready?: string[];
  engines_total?: number;
}

export interface QueueEntry {
  stage_id: string;
  video_id: string;
  video_title: string;
  key: string;
  started_at?: string | null;
  [k: string]: unknown;
}

export interface QueueState {
  claude_slots: { used: number; max: number };
  render_slots: { used: number; max: number };
  running: QueueEntry[];
  queued: QueueEntry[];
}

export interface FileEntry {
  path: string;
  name: string;
  is_dir: boolean;
  size: number;
  modified: string;
  kind: string;
}

export interface HistoryItem {
  at: string;
  kind: string;
  title: string;
  details: unknown;
}

export interface Settings {
  app_name: string;
  data_dir: string;
  db_url?: string;
  engines_dir: string;
  engine_paths: Record<string, string>;
  claude_bin: string;
  claude_permission_mode: string;
  ffmpeg: string;
  ffprobe: string;
  python_bin: string;
  max_claude_sessions: number;
  max_render_jobs: number;
  render_workers: number;
  min_free_ram_gb: number;
  stage_timeout_minutes: number;
  default_video_config: Partial<VideoConfig>;
  zero_cost_default: boolean;
  allowed_free_keys: string[];
  extra_env: Record<string, string>;
  [k: string]: unknown;
}

// ---- interchange/schema.py ----

export interface VideoConfig {
  video_type: string;
  visual_style: string;
  tone: string;
  duration_seconds: number;
  language: string;
  narration_language: string;
  aspect_ratio: "16:9" | "9:16" | "1:1" | "4:5" | "4:3";
  resolution: string;
  fps: number;
  platforms: string[];
  voiceover: "generated" | "user" | "none";
  voice_engine: string | null;
  user_voice_file: string | null;
  captions: boolean;
  burn_captions: boolean;
  subtitle_languages: string[];
  music: boolean;
  sfx: boolean;
  pacing: "slow" | "medium" | "fast";
  scene_density: "low" | "medium" | "high";
  branding: { logo?: string; intro?: string; outro?: string; colors?: string; [k: string]: unknown };
  target_audience: string;
  references: string[];
  budget_usd: number;
  zero_cost: boolean;
  approved_paid_providers: string[];
  approval_mode: "manual" | "auto_to_preview" | "auto_low_risk";
  auto_stages: string[];
  engines: string[];
  excluded_engines: string[];
  engine_options: Record<string, Record<string, unknown>>;
  claude_model: string | null;
  claude_effort: string | null;
  max_claude_budget_usd: number | null;
  brief: string;
}

export interface VideoCreate {
  project_id?: string | null;
  title: string;
  idea: string;
  config: Partial<VideoConfig>;
  mode: "collaborative" | "native";
  primary_engine?: string | null;
  start?: boolean;
}

export interface Source {
  id: string;
  title: string;
  url?: string | null;
  publisher?: string | null;
  accessed?: string | null;
}

export interface Research {
  summary: string;
  key_facts: { id?: string; claim?: string; source_ids?: string[] }[];
  sources: Source[];
  angles: string[];
}

export interface FactCheckItem {
  claim_id: string;
  claim: string;
  status: "verified" | "disputed" | "unverified" | "corrected";
  note: string;
  source_ids: string[];
}
export interface FactCheck {
  items: FactCheckItem[];
}

export interface ScriptLine {
  id: string;
  text: string;
  spoken?: string;
  section?: string;
  fact_ids?: string[];
}
export interface Script {
  version: number;
  title: string;
  lines: ScriptLine[];
  word_count?: number;
  estimated_seconds?: number;
}

export interface Scene {
  number: number;
  segment: string;
  line_ids: string[];
  visual: string;
  duration_seconds: number;
  engine: string;
  method?: string;
  asset_ids?: string[];
  status?: string;
  preview?: string | null;
}
export interface Segment {
  id: string;
  engine: string;
  engine_options?: Record<string, unknown>;
  scene_numbers: number[];
  reason?: string;
}
export interface Storyboard {
  scenes: Scene[];
  segments: Segment[];
}

export interface AssetItem {
  id: string;
  kind: string;
  path?: string | null;
  source?: string;
  source_url?: string | null;
  license?: string;
  credit?: string;
  provider?: string;
  paid?: boolean;
  status?: "planned" | "acquired" | "missing" | "rejected" | string;
}
export interface AssetManifest {
  items: AssetItem[];
}

export interface NarrationTiming {
  audio?: string;
  engine?: string;
  voice?: string;
  duration: number;
  lines: { id: string; start: number; end: number; text: string }[];
}

// ---- presets (shape is loose; treat defensively) ----

export interface PresetOption {
  id: string;
  name?: string;
  description?: string;
  [k: string]: unknown;
}
export interface VideoTypePreset {
  id: string;
  name: string;
  description?: string;
  engines?: string[];
  defaults?: Partial<VideoConfig>;
}
export interface Presets {
  video_types?: VideoTypePreset[];
  visual_styles?: unknown[];
  tones?: unknown[];
  durations?: { id: string; name: string; seconds: number }[];
  aspect_ratios?: { id: string; resolution: string }[];
  platforms?: { id: string; name: string; aspect_ratio: string }[];
  voiceover?: unknown[];
  engine_options?: Record<string, Record<string, unknown>>;
  [k: string]: unknown;
}

export interface ImportPreviewItem {
  title: string;
  idea: string;
  overrides?: Partial<VideoConfig>;
}
