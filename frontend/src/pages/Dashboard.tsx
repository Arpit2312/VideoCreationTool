import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Film, PlusCircle } from "lucide-react";
import { api, fileUrl } from "../api";
import { bucketOf } from "../lib/status";
import { timeAgo } from "../lib/format";
import { VideoRow } from "../components/VideoRow";
import { Empty, ErrorBox, Loading, PageHeader, Section, SimulatedBadge, SlotMeter, Stat, StatusBadge, ZeroCostBadge } from "../components/ui";
import type { Video } from "../types";

export default function Dashboard() {
  const videos = useQuery({ queryKey: ["videos"], queryFn: () => api.videos(), refetchInterval: 15_000 });
  const queue = useQuery({ queryKey: ["queue"], queryFn: api.queue, refetchInterval: 10_000 });
  const health = useQuery({ queryKey: ["health"], queryFn: api.systemHealth, staleTime: 60_000, retry: 0 });

  const list = videos.data || [];
  const by = (b: string) => list.filter((v) => bucketOf(v.status) === b);
  const running = list.filter((v) => v.status === "running" || v.status === "queued");
  const approval = by("approval");
  const input = by("input");
  const failed = by("failed");
  const completed = by("completed").sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Studio overview"
        subtitle="What is running, what is waiting on you, and what has been produced."
        actions={
          <>
            {health.data?.claude?.simulated && <SimulatedBadge />}
            {health.data && <ZeroCostBadge on={health.data.zero_cost_ready} />}
            <Link to="/new" className="btn-primary">
              <PlusCircle className="h-4 w-4" /> New video
            </Link>
          </>
        }
      />
      {videos.error && <ErrorBox error={videos.error} onRetry={() => videos.refetch()} title="Could not load videos" />}
      {health.data?.claude?.simulated && (
        <div className="rounded-lg border border-orange-500/40 bg-orange-500/10 p-3 text-sm">
          <b>Simulated mode.</b> The configured Claude binary (<code className="font-mono">{health.data.claude.bin}</code>) is not the
          real Claude Code CLI, so stage outputs are test data. Change it in <Link className="underline" to="/settings">Settings</Link>.
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <Stat label="Running / queued" value={running.length} tone="blue" to="/jobs" />
        <Stat label="Needs approval" value={approval.length} tone="amber" to="/jobs" />
        <Stat label="Needs input" value={input.length} tone="violet" to="/jobs" />
        <Stat label="Failed" value={failed.length} tone="red" to="/jobs" />
        <Stat label="Completed" value={completed.length} tone="green" to="/projects" />
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-4">
          {(approval.length > 0 || input.length > 0) && (
            <Section title="Waiting on you">
              <div className="space-y-2">
                {[...input, ...approval].map((v) => (
                  <VideoRow key={v.id} video={v} />
                ))}
              </div>
            </Section>
          )}
          <Section title="Running now">
            {videos.isLoading ? (
              <Loading />
            ) : running.length ? (
              <div className="space-y-2">
                {running.map((v) => (
                  <VideoRow key={v.id} video={v} />
                ))}
              </div>
            ) : (
              <Empty>Nothing is running. <Link className="text-accent underline" to="/new">Create a video</Link> or start a batch.</Empty>
            )}
          </Section>
          {failed.length > 0 && (
            <Section title="Failed">
              <div className="space-y-2">
                {failed.map((v) => (
                  <VideoRow key={v.id} video={v} />
                ))}
              </div>
            </Section>
          )}
        </div>
        <div className="space-y-4">
          <Section title="Queue slots" actions={<Link className="subtle hover:underline" to="/queue">Render queue →</Link>}>
            {queue.error ? (
              <ErrorBox error={queue.error} />
            ) : queue.data ? (
              <div className="space-y-3">
                <SlotMeter label="Claude sessions" used={queue.data.claude_slots.used} max={queue.data.claude_slots.max} />
                <SlotMeter label="Render slots" used={queue.data.render_slots.used} max={queue.data.render_slots.max} />
                <div className="subtle">
                  {queue.data.running.length} running · {queue.data.queued.length} queued
                </div>
              </div>
            ) : (
              <Loading />
            )}
          </Section>
          <Section title="Recent outputs">
            <RecentOutputs videos={completed.slice(0, 6)} />
          </Section>
          <Section title="All videos">
            <ul className="divide-y divide-line">
              {list.slice(0, 12).map((v) => (
                <li key={v.id} className="py-2 flex items-center gap-2">
                  <Link to={`/videos/${v.id}`} className="truncate flex-1 hover:underline text-sm">
                    {v.title}
                  </Link>
                  <StatusBadge status={v.status} />
                </li>
              ))}
              {!list.length && !videos.isLoading && <li className="subtle py-2">No videos yet.</li>}
            </ul>
          </Section>
        </div>
      </div>
    </div>
  );
}

function RecentOutputs({ videos }: { videos: Video[] }) {
  if (!videos.length) return <Empty icon={<Film className="h-6 w-6 opacity-60" />}>Finished films appear here.</Empty>;
  return (
    <ul className="space-y-2">
      {videos.map((v) => (
        <li key={v.id} className="flex items-center gap-3">
          <video
            className="h-12 w-20 rounded bg-black object-cover shrink-0"
            src={fileUrl(v.id, "renders/final.mp4")}
            preload="metadata"
            muted
          />
          <div className="min-w-0">
            <Link to={`/videos/${v.id}?tab=preview`} className="text-sm font-medium hover:underline truncate block">
              {v.title}
            </Link>
            <div className="subtle">Finished {timeAgo(v.updated_at)}</div>
          </div>
        </li>
      ))}
    </ul>
  );
}
