// Status colour system, used everywhere a status is shown.
import type { AnyStatus, Stage } from "../types";

export type Tone = "grey" | "slate" | "blue" | "amber" | "violet" | "green" | "red" | "muted" | "orange";

export interface StatusMeta {
  label: string;
  tone: Tone;
  /** a live state (subtle pulse) */
  live: boolean;
  /** user action needed */
  attention: boolean;
}

const META: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "grey", live: false, attention: false },
  pending: { label: "Pending", tone: "grey", live: false, attention: false },
  queued: { label: "Queued", tone: "slate", live: false, attention: false },
  running: { label: "Running", tone: "blue", live: true, attention: false },
  paused: { label: "Paused", tone: "muted", live: false, attention: false },
  awaiting_approval: { label: "Waiting for approval", tone: "amber", live: false, attention: true },
  needs_input: { label: "Needs your input", tone: "violet", live: false, attention: true },
  completed: { label: "Completed", tone: "green", live: false, attention: false },
  failed: { label: "Failed", tone: "red", live: false, attention: true },
  interrupted: { label: "Interrupted", tone: "orange", live: false, attention: true },
  cancelled: { label: "Cancelled", tone: "muted", live: false, attention: false },
  skipped: { label: "Skipped", tone: "muted", live: false, attention: false },
};

export function statusMeta(status: AnyStatus | null | undefined): StatusMeta {
  const s = (status || "").toString();
  return (
    META[s] ?? {
      label: s ? s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()) : "Unknown",
      tone: "grey",
      live: false,
      attention: false,
    }
  );
}

/** Tailwind classes for a status chip (bg + text + ring). */
export const TONE_CHIP: Record<Tone, string> = {
  grey: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-300 ring-zinc-500/30",
  slate: "bg-slate-500/20 text-slate-700 dark:text-slate-200 ring-slate-400/40",
  blue: "bg-blue-500/15 text-blue-700 dark:text-blue-300 ring-blue-500/40",
  amber: "bg-amber-500/15 text-amber-800 dark:text-amber-300 ring-amber-500/40",
  violet: "bg-violet-500/15 text-violet-700 dark:text-violet-300 ring-violet-500/40",
  green: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 ring-emerald-500/40",
  red: "bg-red-500/15 text-red-700 dark:text-red-300 ring-red-500/40",
  orange: "bg-orange-500/15 text-orange-700 dark:text-orange-300 ring-orange-500/40",
  muted: "bg-zinc-500/10 text-zinc-500 dark:text-zinc-400 ring-zinc-500/20",
};

/** Solid colour for dots, timeline bars and strips. */
export const TONE_SOLID: Record<Tone, string> = {
  grey: "bg-zinc-400 dark:bg-zinc-600",
  slate: "bg-slate-400 dark:bg-slate-500",
  blue: "bg-blue-500",
  amber: "bg-amber-500",
  violet: "bg-violet-500",
  green: "bg-emerald-500",
  red: "bg-red-500",
  orange: "bg-orange-500",
  muted: "bg-zinc-300 dark:bg-zinc-700",
};

export const TONE_BORDER: Record<Tone, string> = {
  grey: "border-zinc-400/40",
  slate: "border-slate-400/60",
  blue: "border-blue-500/70",
  amber: "border-amber-500/80",
  violet: "border-violet-500/80",
  green: "border-emerald-500/60",
  red: "border-red-500/80",
  orange: "border-orange-500/70",
  muted: "border-zinc-500/20",
};

export type Bucket = "running" | "approval" | "input" | "failed" | "completed" | "queued" | "other";

/** Dashboard bucket for a video/job status. */
export function bucketOf(status: AnyStatus | null | undefined): Bucket {
  switch (status) {
    case "running":
      return "running";
    case "awaiting_approval":
      return "approval";
    case "needs_input":
      return "input";
    case "failed":
    case "interrupted":
      return "failed";
    case "completed":
      return "completed";
    case "queued":
      return "queued";
    default:
      return "other";
  }
}

export const ACTIVE_JOB_STATUSES = new Set(["queued", "running", "paused", "awaiting_approval", "needs_input", "interrupted"]);

/** Stage actions allowed for a given stage (mirrors workflow/stages.py TRANSITIONS + spec §4). */
export function allowedStageActions(stage: Pick<Stage, "status" | "required">): {
  approve: boolean;
  requestChanges: boolean;
  reject: boolean;
  retry: boolean;
  skip: boolean;
  answer: boolean;
} {
  const s = stage.status;
  return {
    approve: s === "awaiting_approval",
    requestChanges: s === "awaiting_approval" || s === "completed",
    reject: s === "awaiting_approval",
    retry: s === "failed" || s === "cancelled",
    skip: !stage.required && (s === "pending" || s === "failed" || s === "awaiting_approval" || s === "queued"),
    answer: s === "needs_input",
  };
}

/** Summarise stage statuses into counts, e.g. for progress strips. */
export function stageCounts(stages: Pick<Stage, "status">[]): Record<string, number> & { total: number; done: number } {
  const out: Record<string, number> = {};
  let done = 0;
  for (const st of stages) {
    out[st.status] = (out[st.status] || 0) + 1;
    if (st.status === "completed" || st.status === "skipped") done++;
  }
  return Object.assign(out, { total: stages.length, done });
}
