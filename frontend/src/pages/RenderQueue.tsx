import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { timeAgo } from "../lib/format";
import { Empty, ErrorBox, Loading, PageHeader, Section, SlotMeter, StatusBadge } from "../components/ui";
import type { QueueEntry } from "../types";

export default function RenderQueue() {
  const q = useQuery({ queryKey: ["queue"], queryFn: api.queue, refetchInterval: 5000 });
  return (
    <div className="space-y-4">
      <PageHeader
        title="Render queue"
        subtitle="Claude sessions and render slots are limited (see Settings → concurrency). Stages wait here for a free slot and enough free RAM."
      />
      {q.error && <ErrorBox error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading && <Loading />}
      {q.data && (
        <>
          <div className="grid sm:grid-cols-2 gap-4">
            <Section title="Claude sessions">
              <SlotMeter label="In use" used={q.data.claude_slots.used} max={q.data.claude_slots.max} />
            </Section>
            <Section title="Render slots">
              <SlotMeter label="In use" used={q.data.render_slots.used} max={q.data.render_slots.max} />
            </Section>
          </div>
          <Section title={`Running (${q.data.running.length})`}>
            <QueueTable rows={q.data.running} status="running" />
          </Section>
          <Section title={`Queued (${q.data.queued.length})`}>
            <QueueTable rows={q.data.queued} status="queued" />
          </Section>
        </>
      )}
    </div>
  );
}

function QueueTable({ rows, status }: { rows: QueueEntry[]; status: string }) {
  if (!rows.length) return <Empty>Nothing {status}.</Empty>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            <th className="py-1.5 pr-3 font-medium">Video</th>
            <th className="py-1.5 pr-3 font-medium">Stage</th>
            <th className="py-1.5 pr-3 font-medium">Status</th>
            <th className="py-1.5 font-medium">{status === "running" ? "Started" : "Waiting since"}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((r, i) => (
            <tr key={r.stage_id || i}>
              <td className="py-2 pr-3">
                <Link className="hover:underline" to={`/videos/${r.video_id}`}>
                  {r.video_title || r.video_id}
                </Link>
              </td>
              <td className="py-2 pr-3">
                <Link className="font-mono text-xs hover:underline" to={`/videos/${r.video_id}?stage=${encodeURIComponent(r.key)}`}>
                  {r.key}
                </Link>
              </td>
              <td className="py-2 pr-3">
                <StatusBadge status={status} />
              </td>
              <td className="py-2 text-muted text-xs">{timeAgo(r.started_at as string)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
