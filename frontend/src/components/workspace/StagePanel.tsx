import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, FileText, MessageSquareReply, RotateCcw, SkipForward, ThumbsDown, Undo2 } from "lucide-react";
import { api, fileUrl } from "../../api";
import type { Stage } from "../../types";
import { allowedStageActions } from "../../lib/status";
import { fmtDate, fmtUsd, timeAgo } from "../../lib/format";
import { laneLabel } from "../../lib/timeline";
import { Badge, cx, EngineBadge, ErrorBox, KV, Loading, ProgressBar, StatusBadge } from "../ui";

type Action = "approve" | "request-changes" | "reject" | "retry" | "skip" | "answer";
type Denial = { tool: string; command: string; rules?: string[] };

export function StagePanel({ videoId, stage }: { videoId: string; stage: Stage }) {
  const qc = useQueryClient();
  const live = stage.status === "running" || stage.status === "queued";
  const [autoRefresh, setAutoRefresh] = useState(true);
  const detail = useQuery({
    queryKey: ["stage", videoId, stage.key],
    queryFn: () => api.stage(videoId, stage.key),
    refetchInterval: live && autoRefresh ? 3000 : false,
  });
  const [feedback, setFeedback] = useState("");
  const [answer, setAnswer] = useState("");
  const act = useMutation({
    mutationFn: ({ action, body }: { action: Action; body?: { feedback?: string; answer?: string } }) => api.stageAction(videoId, stage.key, action, body),
    onSuccess: () => {
      setFeedback("");
      setAnswer("");
      qc.invalidateQueries({ queryKey: ["video", videoId] });
      qc.invalidateQueries({ queryKey: ["stage", videoId] });
      qc.invalidateQueries({ queryKey: ["videos"] });
    },
  });
  const s = detail.data?.stage ?? stage;
  const allowed = allowedStageActions(s);
  const denials = ((s.params as Record<string, unknown> | undefined)?.permission_denials ?? []) as Denial[];
  const suggested = Array.from(new Set(denials.flatMap((d) => d.rules ?? [])));
  const [picked, setPicked] = useState<string[] | null>(null);
  const chosen = picked ?? suggested;
  const allow = useMutation({
    mutationFn: (rules: string[]) => api.allowTools(videoId, stage.key, rules),
    onSuccess: () => {
      setPicked(null);
      qc.invalidateQueries({ queryKey: ["video", videoId] });
      qc.invalidateQueries({ queryKey: ["stage", videoId] });
    },
  });
  const logRef = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [detail.data?.log_tail]);

  return (
    <div className="space-y-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-lg font-semibold">{s.name}</h3>
          <StatusBadge status={s.status} />
          <EngineBadge owner={s.owner} />
          {s.gate && <Badge tone="amber">approval gate</Badge>}
          {s.engine_gate && <Badge tone="amber">engine gate</Badge>}
          {!s.required && <Badge tone="muted">optional</Badge>}
        </div>
        <p className="text-sm text-muted mt-1">{s.description}</p>
      </div>

      {s.status === "running" && (
        <div className="rounded-lg border border-blue-500/30 bg-blue-500/10 p-3 text-sm">
          {typeof s.progress === "number" ? (
            <div className="space-y-1">
              <div className="flex justify-between text-xs">
                <span>Running</span>
                <span className="tabular-nums">{Math.round((s.progress <= 1 ? s.progress * 100 : s.progress))}%</span>
              </div>
              <ProgressBar value={s.progress} />
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-blue-500 animate-softpulse" /> Running
            </div>
          )}
          <div className="text-xs text-muted mt-1">
            {s.last_activity ? `Last activity: ${s.last_activity}` : `Started ${timeAgo(s.started_at)}`}
          </div>
        </div>
      )}


      {denials.length > 0 && ["needs_input", "failed", "queued"].includes(s.status) && (
        <div className="rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 space-y-2">
          <div className="font-medium text-amber-800 dark:text-amber-200 flex items-center gap-1.5">
            <AlertTriangle className="h-4 w-4" /> Claude was blocked from running these commands
          </div>
          <ul className="space-y-1">
            {denials.map((d, i) => (
              <li key={i} className="text-xs">
                <span className="text-muted">{d.tool}:</span> <code className="font-mono break-all">{d.command || "(no details)"}</code>
                {d.tool === "PowerShell" && <span className="text-muted"> (PowerShell is turned off; Claude will use Git Bash)</span>}
              </li>
            ))}
          </ul>
          {suggested.length > 0 ? (
            <>
              <p className="text-xs text-muted">
                Allow these command types for <b>this video only</b>? Only allow commands you expect for this stage (e.g. downloading free imagery or fonts).
              </p>
              <div className="flex flex-wrap gap-2">
                {suggested.map((r) => (
                  <label key={r} className="inline-flex items-center gap-1.5 text-xs">
                    <input
                      type="checkbox"
                      checked={chosen.includes(r)}
                      onChange={(e) => setPicked(e.target.checked ? [...chosen, r] : chosen.filter((x) => x !== r))}
                    />
                    <code className="font-mono">{r}</code>
                  </label>
                ))}
              </div>
              <button className="btn-primary" disabled={chosen.length === 0 || allow.isPending} onClick={() => allow.mutate(chosen)}>
                Allow selected & continue
              </button>
              {allow.error && <p className="text-xs text-red-600">{String((allow.error as Error).message)}</p>}
            </>
          ) : (
            <p className="text-xs text-muted">Nothing to approve here; Retry the stage.</p>
          )}
        </div>
      )}

      {s.status === "needs_input" && (
        <div className="rounded-lg border border-violet-500/50 bg-violet-500/10 p-3 space-y-2">
          <div className="font-medium text-violet-800 dark:text-violet-200 flex items-center gap-1.5">
            <MessageSquareReply className="h-4 w-4" /> Needs your input
          </div>
          <p className="text-sm whitespace-pre-wrap">{s.question || "The stage asked a question (see logs)."}</p>
          <textarea className="input" rows={3} value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="Your answer…" aria-label="Answer" />
          <button className="btn-primary" disabled={!answer.trim() || act.isPending} onClick={() => act.mutate({ action: "answer", body: { answer } })}>
            Send answer & continue
          </button>
        </div>
      )}

      {s.status === "awaiting_approval" && (
        <div className="rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 space-y-2">
          <div className="font-medium text-amber-800 dark:text-amber-200">Waiting for approval</div>
          {s.summary && <p className="text-sm whitespace-pre-wrap">{s.summary}</p>}
          <p className="text-xs text-muted">Review the outputs below (or the Research & Script / Storyboard tabs), then approve, request changes, or reject.</p>
        </div>
      )}

      {s.error && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-700 dark:text-red-200 flex gap-2">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <span className="whitespace-pre-wrap break-words">{s.error}</span>
        </div>
      )}
      {s.warnings?.length > 0 && (
        <ul className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs space-y-1">
          {s.warnings.map((w, i) => (
            <li key={i} className="flex gap-1.5">
              <AlertTriangle className="h-3 w-3 text-amber-500 shrink-0 mt-0.5" /> {w}
            </li>
          ))}
        </ul>
      )}
      {s.pending_feedback && (
        <div className="text-xs rounded-lg bg-panel2 p-2">
          <span className="text-muted">Feedback for next attempt:</span> {s.pending_feedback}
        </div>
      )}

      {/* Actions */}
      {(allowed.approve || allowed.requestChanges || allowed.reject || allowed.retry || allowed.skip) && (
        <div className="space-y-2">
          {(allowed.requestChanges || allowed.reject) && (
            <textarea
              className="input"
              rows={2}
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              placeholder="Feedback (required to request changes or reject)…"
              aria-label="Feedback"
            />
          )}
          <div className="flex flex-wrap gap-2">
            {allowed.approve && (
              <button className="btn-primary" disabled={act.isPending} onClick={() => act.mutate({ action: "approve" })}>
                <Check className="h-4 w-4" /> Approve
              </button>
            )}
            {allowed.requestChanges && (
              <button
                className="btn"
                disabled={act.isPending || !feedback.trim()}
                onClick={() => act.mutate({ action: "request-changes", body: { feedback } })}
                title={s.status === "completed" ? "Re-run this stage; completed later stages are reset to pending" : undefined}
              >
                <Undo2 className="h-4 w-4" /> Request changes
              </button>
            )}
            {allowed.reject && (
              <button className="btn-danger" disabled={act.isPending || !feedback.trim()} onClick={() => window.confirm("Reject this stage? It will be marked failed.") && act.mutate({ action: "reject", body: { feedback } })}>
                <ThumbsDown className="h-4 w-4" /> Reject
              </button>
            )}
            {allowed.retry && (
              <button className="btn" disabled={act.isPending} onClick={() => act.mutate({ action: "retry" })}>
                <RotateCcw className="h-4 w-4" /> Retry
              </button>
            )}
            {allowed.skip && (
              <button className="btn" disabled={act.isPending} onClick={() => window.confirm("Skip this optional stage?") && act.mutate({ action: "skip" })}>
                <SkipForward className="h-4 w-4" /> Skip
              </button>
            )}
          </div>
          {act.error && <ErrorBox error={act.error} title="Action failed" />}
        </div>
      )}

      <div className="grid sm:grid-cols-2 gap-x-4">
        <div>
          <KV k="Key">
            <code className="font-mono">{s.key}</code>
          </KV>
          <KV k="Owner engine">{s.owner}</KV>
          <KV k="Executor">{s.executor}</KV>
          <KV k="Lane">{laneLabel(s.lane)}</KV>
          <KV k="Capability">{s.capability}</KV>
          <KV k="Depends on">{s.depends_on?.join(", ") || "—"}</KV>
        </div>
        <div>
          <KV k="Attempt">{s.attempt}</KV>
          <KV k="Started">{fmtDate(s.started_at)}</KV>
          <KV k="Completed">{fmtDate(s.completed_at)}</KV>
          <KV k="Cost">{fmtUsd(s.cost_usd)}</KV>
          <KV k="Session">{s.session_id ? <code className="font-mono text-[11px]">{s.session_id}</code> : "—"}</KV>
        </div>
      </div>

      <FileList title="Inputs" videoId={videoId} files={s.inputs} />
      <FileList title="Outputs" videoId={videoId} files={s.outputs?.length ? s.outputs : []} expected={s.expected_outputs} />

      {s.params && Object.keys(s.params).length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted">Stage config / params</summary>
          <pre className="mt-1 rounded-lg bg-panel2 p-2 overflow-auto font-mono">{JSON.stringify(s.params, null, 2)}</pre>
        </details>
      )}

      <div>
        <div className="flex items-center justify-between mb-1">
          <div className="label mb-0">Live log (tail)</div>
          <label className="text-xs text-muted flex items-center gap-1">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} /> auto-refresh
          </label>
        </div>
        {detail.isLoading ? (
          <Loading />
        ) : detail.error ? (
          <ErrorBox error={detail.error} onRetry={() => detail.refetch()} />
        ) : (
          <pre ref={logRef} className="h-56 overflow-auto rounded-lg bg-black/90 text-zinc-200 p-2 text-[11px] font-mono leading-relaxed whitespace-pre-wrap break-all">
            {(detail.data?.log_tail || []).join("\n") || "No log output yet."}
          </pre>
        )}
      </div>

      {detail.data && detail.data.versions.length > 0 && (
        <div>
          <div className="label">Versions</div>
          <ul className="text-xs space-y-1">
            {detail.data.versions.map((v) => (
              <li key={v.id} className="flex flex-wrap gap-2 rounded bg-panel2 p-2">
                <b>v{v.version}</b>
                {v.approved && <Badge tone="green">approved</Badge>}
                <span className="text-muted">{fmtDate(v.created_at)}</span>
                {v.note && <span>{v.note}</span>}
                <span className="flex flex-wrap gap-1">
                  {v.files.map((f) => (
                    <a key={f} className="font-mono text-accent hover:underline" href={fileUrl(videoId, `${v.snapshot_dir ? v.snapshot_dir + "/" : ""}${f}`)} target="_blank" rel="noreferrer">
                      {f}
                    </a>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {detail.data && detail.data.approvals.length > 0 && (
        <div>
          <div className="label">Decisions</div>
          <ul className="text-xs space-y-1">
            {detail.data.approvals.map((a) => (
              <li key={a.id} className="rounded bg-panel2 p-2">
                <b className="capitalize">{a.decision.replace(/_/g, " ")}</b> <span className="text-muted">· {fmtDate(a.created_at)}</span>
                {a.feedback && <div className="mt-0.5 whitespace-pre-wrap">{a.feedback}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {detail.data && detail.data.runs.length > 0 && (
        <div>
          <div className="label">Runs</div>
          <ul className="text-xs space-y-1">
            {detail.data.runs.map((r) => (
              <li key={r.id} className="flex flex-wrap gap-2 rounded bg-panel2 p-2">
                <Badge tone={r.state === "done" ? "green" : r.state === "running" ? "blue" : r.state === "failed" ? "red" : "muted"}>{r.state}</Badge>
                <span>{r.kind}</span>
                {r.simulated && <Badge tone="orange">simulated</Badge>}
                {r.model && <span className="text-muted">{r.model}</span>}
                <span className="text-muted">{fmtDate(r.started_at)}</span>
                {r.exit_code !== null && r.exit_code !== undefined && <span>exit {r.exit_code}</span>}
                <span>{fmtUsd(r.cost_usd)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function FileList({ title, videoId, files, expected }: { title: string; videoId: string; files: string[]; expected?: string[] }) {
  const missing = (expected || []).filter((e) => !files.includes(e) && !e.includes("*"));
  if (!files?.length && !missing.length) return null;
  return (
    <div>
      <div className="label">{title}</div>
      <ul className="flex flex-wrap gap-1.5">
        {files.map((f) => (
          <li key={f}>
            <a href={fileUrl(videoId, f)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded bg-panel2 px-2 py-1 text-xs font-mono hover:underline">
              <FileText className="h-3 w-3" /> {f}
            </a>
          </li>
        ))}
        {missing.map((f) => (
          <li key={f} className={cx("inline-flex items-center gap-1 rounded border border-dashed border-line px-2 py-1 text-xs font-mono text-muted")} title="Expected, not produced yet">
            {f}
          </li>
        ))}
      </ul>
    </div>
  );
}
