import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { ACTIVE_JOB_STATUSES } from "../lib/status";
import { VideoRow } from "../components/VideoRow";
import { cx, Empty, ErrorBox, Loading, PageHeader } from "../components/ui";

const FILTERS = [
  { id: "active", name: "All active" },
  { id: "running", name: "Running" },
  { id: "queued", name: "Queued" },
  { id: "waiting", name: "Waiting on you" },
  { id: "failed", name: "Failed / interrupted" },
  { id: "paused", name: "Paused" },
];

export default function ActiveJobs() {
  const [filter, setFilter] = useState("active");
  const q = useQuery({ queryKey: ["videos"], queryFn: () => api.videos(), refetchInterval: 5000 });
  const all = q.data || [];
  const list = all.filter((v) => {
    switch (filter) {
      case "running":
        return v.status === "running";
      case "queued":
        return v.status === "queued";
      case "waiting":
        return v.status === "awaiting_approval" || v.status === "needs_input";
      case "failed":
        return v.status === "failed" || v.status === "interrupted";
      case "paused":
        return v.status === "paused" || v.paused;
      default:
        return ACTIVE_JOB_STATUSES.has(v.status) || v.status === "failed";
    }
  });
  return (
    <div>
      <PageHeader title="Active jobs" subtitle="Live list of videos in production. Click a stage strip segment to open that stage." />
      <div className="flex gap-1 overflow-x-auto mb-4 border-b border-line">
        {FILTERS.map((f) => (
          <button key={f.id} className={cx("tab", filter === f.id && "tab-active")} onClick={() => setFilter(f.id)}>
            {f.name}
          </button>
        ))}
      </div>
      {q.error && <ErrorBox error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading ? (
        <Loading />
      ) : list.length ? (
        <div className="space-y-3">
          {list.map((v) => (
            <VideoRow key={v.id} video={v} />
          ))}
        </div>
      ) : (
        <Empty>No jobs match this filter.</Empty>
      )}
    </div>
  );
}
