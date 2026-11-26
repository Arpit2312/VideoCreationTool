import { useMemo, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Search } from "lucide-react";
import { api } from "../api";
import type { Asset } from "../types";
import { mediaKind } from "../lib/format";
import { MediaPreview } from "../components/workspace/Media";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Select } from "../components/ui";

export default function AssetsPage() {
  const videos = useQuery({ queryKey: ["videos"], queryFn: () => api.videos() });
  const list = (videos.data || []).filter((v) => v.status !== "draft");
  const results = useQueries({
    queries: list.map((v) => ({ queryKey: ["assets", v.id], queryFn: () => api.assets(v.id), retry: false, staleTime: 30_000 })),
  });
  const [kind, setKind] = useState("");
  const [video, setVideo] = useState("");
  const [licence, setLicence] = useState("");
  const [q, setQ] = useState("");
  const titles = new Map(list.map((v) => [v.id, v.title]));
  const all: Asset[] = useMemo(() => results.flatMap((r) => r.data || []), [results]);
  const kinds = Array.from(new Set(all.map((a) => a.kind))).sort();
  const licences = Array.from(new Set(all.map((a) => a.license || "unknown"))).sort();
  const shown = all.filter(
    (a) =>
      (!kind || a.kind === kind) &&
      (!video || a.video_id === video) &&
      (!licence || (a.license || "unknown") === licence) &&
      (!q || `${a.path} ${a.credit} ${a.source}`.toLowerCase().includes(q.toLowerCase())),
  );
  const loading = videos.isLoading || results.some((r) => r.isLoading);
  return (
    <div>
      <PageHeader title="Assets" subtitle="Every image, clip, audio file and subtitle acquired across your videos, with licence and credit." />
      {videos.error && <ErrorBox error={videos.error} onRetry={() => videos.refetch()} />}
      <div className="flex flex-wrap gap-2 mb-4">
        <div className="relative flex-1 min-w-[180px]">
          <Search className="h-3.5 w-3.5 absolute left-2.5 top-2.5 text-muted" />
          <input className="input pl-8" placeholder="Search path, credit, source" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Select className="w-auto" ariaLabel="Kind" value={kind} onChange={setKind} options={[{ id: "", name: "All kinds" }, ...kinds.map((k) => ({ id: k, name: k }))]} />
        <Select className="w-auto" ariaLabel="Licence" value={licence} onChange={setLicence} options={[{ id: "", name: "All licences" }, ...licences.map((k) => ({ id: k, name: k }))]} />
        <Select className="w-auto" ariaLabel="Video" value={video} onChange={setVideo} options={[{ id: "", name: "All videos" }, ...list.map((v) => ({ id: v.id, name: v.title }))]} />
      </div>
      {loading ? (
        <Loading />
      ) : !shown.length ? (
        <Empty>No assets match.</Empty>
      ) : (
        <>
          <p className="subtle mb-2">{shown.length} assets</p>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
            {shown.map((a) => (
              <div key={a.id} className="card overflow-hidden">
                <div className="aspect-video bg-panel2 grid place-items-center">
                  {["image", "video"].includes(mediaKind(a.path)) ? (
                    <MediaPreview videoId={a.video_id} path={a.path} className="h-full rounded-none" />
                  ) : mediaKind(a.path) === "audio" ? (
                    <div className="w-full p-3"><MediaPreview videoId={a.video_id} path={a.path} /></div>
                  ) : (
                    <span className="subtle">{a.kind}</span>
                  )}
                </div>
                <div className="p-3 text-xs space-y-1">
                  <div className="flex gap-1.5 items-center">
                    <Badge>{a.kind}</Badge>
                    {a.stage_key && <span className="text-muted font-mono">{a.stage_key}</span>}
                  </div>
                  <div className="font-mono truncate" title={a.path}>{a.path}</div>
                  <div><span className="text-muted">Licence:</span> {a.license || <span className="text-red-600 dark:text-red-300">unknown</span>}</div>
                  {a.credit && <div><span className="text-muted">Credit:</span> {a.credit}</div>}
                  {a.source && <div className="text-muted">{a.source}</div>}
                  <Link className="text-accent hover:underline" to={`/videos/${a.video_id}?tab=assets`}>{titles.get(a.video_id) || a.video_id}</Link>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
