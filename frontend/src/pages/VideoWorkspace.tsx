import { useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, Download, MessageSquareReply, ShieldAlert } from "lucide-react";
import { api } from "../api";
import { defaultFocus, orderStages } from "../lib/timeline";
import { stageCounts } from "../lib/status";
import { fmtDate, fmtUsd } from "../lib/format";
import { VideoActions } from "../components/VideoRow";
import { Timeline } from "../components/workspace/Timeline";
import { StagePanel } from "../components/workspace/StagePanel";
import { ResearchScriptView, StoryboardView } from "../components/workspace/Viewers";
import { AssetsView, FilesView, HistoryView, PreviewView } from "../components/workspace/Media";
import { Badge, cx, ErrorBox, KV, Loading, ProgressBar, Section, SimulatedBadge, StatusBadge, ZeroCostBadge } from "../components/ui";

const TABS = [
  { id: "timeline", name: "Production timeline" },
  { id: "script", name: "Research & Script" },
  { id: "storyboard", name: "Storyboard" },
  { id: "assets", name: "Assets" },
  { id: "preview", name: "Preview" },
  { id: "history", name: "History" },
  { id: "files", name: "Files" },
];

export default function VideoWorkspace() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "timeline";
  const q = useQuery({
    queryKey: ["video", id],
    queryFn: () => api.video(id),
    refetchInterval: (query) => (["running", "queued"].includes(query.state.data?.video.status ?? "") ? 4000 : 20_000),
  });
  const stages = useMemo(() => orderStages(q.data?.stages || []), [q.data?.stages]);
  const stageKey = params.get("stage") || defaultFocus(stages)?.key;
  const stage = stages.find((s) => s.key === stageKey);
  const setParam = (k: string, v: string) => {
    const n = new URLSearchParams(params);
    n.set(k, v);
    setParams(n, { replace: true });
  };
  useEffect(() => {
    if (q.data?.video.title) document.title = `${q.data.video.title} · SpaceStudio`;
  }, [q.data?.video.title]);

  if (q.isLoading) return <Loading label="Loading video…" />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.refetch()} title="Could not load this video" />;
  if (!q.data) return null;
  const { video, sessions, simulated } = q.data;
  const counts = stageCounts(stages);
  const waiting = stages.filter((s) => s.status === "needs_input" || s.status === "awaiting_approval");

  return (
    <div className="space-y-4">
      <div>
        <Link to={`/projects/${video.project_id}`} className="subtle hover:underline inline-flex items-center gap-1">
          <ArrowLeft className="h-3 w-3" /> Project
        </Link>
        <div className="flex flex-wrap items-start justify-between gap-3 mt-1">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="h1 truncate">{video.title}</h1>
              <StatusBadge status={video.paused && video.status !== "paused" ? "paused" : video.status} />
              {simulated && <SimulatedBadge />}
              <ZeroCostBadge on={video.config?.zero_cost !== false} />
              <Badge tone="slate">{video.mode === "native" ? `Native · ${video.primary_engine}` : "Collaborative"}</Badge>
            </div>
            <p className="text-sm text-muted mt-1 line-clamp-2">{video.idea}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <VideoActions video={video} />
            <a className="btn" href={api.exportUrl(video.id)}>
              <Download className="h-4 w-4" /> Workspace zip
            </a>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <div className="flex-1">
            <ProgressBar value={counts.total ? counts.done / counts.total : 0} tone="green" />
          </div>
          <span className="subtle tabular-nums">
            {counts.done}/{counts.total} stages done
          </span>
        </div>
        {simulated && (
          <p className="text-xs text-orange-700 dark:text-orange-300 mt-2">
            SIMULATED: this video ran with a test Claude binary. Its outputs are not real productions.
          </p>
        )}
        {video.error && <div className="mt-2"><ErrorBox error={new Error(video.error)} title="Video error" /></div>}
      </div>

      {waiting.length > 0 && (
        <div className="space-y-2">
          {waiting.map((s) => (
            <button
              key={s.key}
              onClick={() => {
                setParam("stage", s.key);
                setParam("tab", "timeline");
              }}
              className={cx(
                "w-full text-left rounded-xl border p-3 flex items-center gap-3",
                s.status === "needs_input" ? "border-violet-500/50 bg-violet-500/10" : "border-amber-500/50 bg-amber-500/10",
              )}
            >
              {s.status === "needs_input" ? <MessageSquareReply className="h-5 w-5 text-violet-500" /> : <ShieldAlert className="h-5 w-5 text-amber-500" />}
              <div className="flex-1 min-w-0">
                <div className="font-medium">
                  {s.status === "needs_input" ? "Needs your input" : "Waiting for approval"}: {s.name}
                </div>
                <div className="text-xs text-muted truncate">{s.status === "needs_input" ? s.question : s.summary || "Review the outputs and decide."}</div>
              </div>
              <span className="text-sm text-accent">Review →</span>
            </button>
          ))}
        </div>
      )}

      <div className="flex gap-1 overflow-x-auto border-b border-line">
        {TABS.map((t) => (
          <button key={t.id} className={cx("tab", tab === t.id && "tab-active")} onClick={() => setParam("tab", t.id)}>
            {t.name}
          </button>
        ))}
      </div>

      {tab === "timeline" && (
        <div className="grid xl:grid-cols-[1fr_460px] gap-4 items-start">
          <Section className="min-w-0">
            {stages.length ? (
              <Timeline stages={stages} selected={stage?.key} onSelect={(k) => setParam("stage", k)} />
            ) : (
              <p className="subtle">No stages yet. {video.status === "draft" ? "Start the video to plan its stages." : ""}</p>
            )}
          </Section>
          <div className="space-y-4 xl:sticky xl:top-0">
            {stage && (
              <Section>
                <StagePanel key={stage.key} videoId={video.id} stage={stage} />
              </Section>
            )}
            <Section title="Video">
              <KV k="ID"><code className="font-mono">{video.id}</code></KV>
              <KV k="Workspace"><code className="font-mono text-[11px]">{video.workspace}</code></KV>
              <KV k="Created">{fmtDate(video.created_at)}</KV>
              <KV k="Cost">{fmtUsd(video.cost_usd)}</KV>
              {sessions.length > 0 && (
                <div className="mt-2">
                  <div className="label">Claude sessions (one per lane)</div>
                  <ul className="text-xs space-y-1">
                    {sessions.map((s) => (
                      <li key={s.id} className="rounded bg-panel2 p-2">
                        <b>{s.lane}</b> <code className="font-mono text-muted">{s.session_id}</code>
                        <div className="text-muted">
                          {s.model || "default model"} · {fmtUsd(s.total_cost_usd)} · last used {fmtDate(s.last_used_at)}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <details className="mt-2 text-xs">
                <summary className="cursor-pointer text-muted">Config</summary>
                <pre className="mt-1 rounded-lg bg-panel2 p-2 overflow-auto font-mono max-h-80">{JSON.stringify(video.config, null, 2)}</pre>
              </details>
            </Section>
          </div>
        </div>
      )}
      {tab === "script" && <ResearchScriptView videoId={video.id} />}
      {tab === "storyboard" && <StoryboardView videoId={video.id} />}
      {tab === "assets" && <AssetsView videoId={video.id} />}
      {tab === "preview" && <PreviewView videoId={video.id} />}
      {tab === "history" && <HistoryView videoId={video.id} />}
      {tab === "files" && <FilesView videoId={video.id} />}
    </div>
  );
}
