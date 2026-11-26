import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Pause, Play, RotateCcw, Square } from "lucide-react";
import { api, explainError } from "../api";
import type { Video } from "../types";
import { orderStages } from "../lib/timeline";
import { statusMeta } from "../lib/status";
import { timeAgo } from "../lib/format";
import { EngineBadge, ProgressBar, SimulatedBadge, StageStrip, StatusBadge, ZeroCostBadge } from "./ui";

export function useVideoAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: "start" | "pause" | "resume" | "cancel" | "retry" }) => api.videoAction(id, action),
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ["videos"] });
      qc.invalidateQueries({ queryKey: ["video", v.id] });
      qc.invalidateQueries({ queryKey: ["queue"] });
    },
  });
}

export function VideoActions({ video, compact }: { video: Video; compact?: boolean }) {
  const m = useVideoAction();
  const s = video.status;
  const run = (action: "start" | "pause" | "resume" | "cancel" | "retry") => {
    if (action === "cancel" && !window.confirm(`Cancel "${video.title}"? Running stages will be stopped.`)) return;
    m.mutate({ id: video.id, action });
  };
  const cls = compact ? "btn btn-sm" : "btn";
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {s === "draft" && (
        <button className={compact ? "btn-primary btn-sm" : "btn-primary"} disabled={m.isPending} onClick={() => run("start")}>
          <Play className="h-3.5 w-3.5" /> Start
        </button>
      )}
      {["running", "queued", "awaiting_approval", "needs_input"].includes(s) && !video.paused && (
        <button className={cls} disabled={m.isPending} onClick={() => run("pause")}>
          <Pause className="h-3.5 w-3.5" /> Pause
        </button>
      )}
      {(s === "paused" || video.paused) && (
        <button className={cls} disabled={m.isPending} onClick={() => run("resume")}>
          <Play className="h-3.5 w-3.5" /> Resume
        </button>
      )}
      {(s === "failed" || s === "interrupted" || s === "cancelled") && (
        <button className={cls} disabled={m.isPending} onClick={() => run("retry")}>
          <RotateCcw className="h-3.5 w-3.5" /> Retry
        </button>
      )}
      {!["completed", "cancelled", "draft", "failed"].includes(s) && (
        <button className={compact ? "btn-danger btn-sm" : "btn-danger"} disabled={m.isPending} onClick={() => run("cancel")}>
          <Square className="h-3.5 w-3.5" /> Cancel
        </button>
      )}
      {m.error && <span className="text-xs text-red-600 dark:text-red-300">{explainError(m.error)}</span>}
    </div>
  );
}

/** A live row for one video: status, stage strip, current stage, actions. */
export function VideoRow({ video, withDetail = true }: { video: Video; withDetail?: boolean }) {
  const detail = useQuery({
    queryKey: ["video", video.id],
    queryFn: () => api.video(video.id),
    enabled: withDetail,
    refetchInterval: ["running", "queued"].includes(video.status) ? 5000 : false,
  });
  const stages = orderStages(detail.data?.stages || []);
  const current =
    stages.find((s) => s.status === "needs_input" || s.status === "awaiting_approval") ||
    stages.find((s) => s.status === "running") ||
    stages.find((s) => s.status === "failed") ||
    stages.find((s) => s.status === "queued");
  const v = detail.data?.video || video;
  return (
    <div className="card p-3 sm:p-4">
      <div className="flex flex-wrap items-start gap-2 justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link to={`/videos/${v.id}`} className="font-medium hover:underline truncate max-w-full">
              {v.title || v.id}
            </Link>
            <StatusBadge status={v.paused && v.status !== "paused" ? "paused" : v.status} />
            {detail.data?.simulated && <SimulatedBadge />}
            {v.config?.zero_cost !== false && <ZeroCostBadge />}
            <span className="subtle">{v.mode === "native" ? `native · ${v.primary_engine ?? ""}` : "collaborative"}</span>
          </div>
          <div className="subtle mt-0.5">Updated {timeAgo(v.updated_at)}</div>
        </div>
        <VideoActions video={v} compact />
      </div>
      {withDetail && (
        <div className="mt-3 space-y-2">
          <StageStrip stages={stages} videoId={v.id} />
          {current && (
            <Link
              to={`/videos/${v.id}?stage=${encodeURIComponent(current.key)}`}
              className="flex flex-wrap items-center gap-2 text-xs hover:underline"
            >
              <StatusBadge status={current.status} />
              <span className="font-medium">{current.name}</span>
              <EngineBadge owner={current.owner} />
              {current.status === "running" && (
                <span className="text-muted">
                  {current.last_activity ? `· ${current.last_activity}` : `· started ${timeAgo(current.started_at)}`}
                </span>
              )}
              {current.status === "needs_input" && current.question && <span className="text-muted truncate">· {current.question}</span>}
              {current.status === "failed" && current.error && (
                <span className="text-red-600 dark:text-red-300 truncate">· {current.error}</span>
              )}
              {current.status !== "running" && statusMeta(current.status).attention && <span className="text-accent">Open →</span>}
            </Link>
          )}
          {current?.status === "running" && typeof current.progress === "number" && <ProgressBar value={current.progress} />}
          {v.error && <div className="text-xs text-red-600 dark:text-red-300">{v.error}</div>}
        </div>
      )}
    </div>
  );
}
