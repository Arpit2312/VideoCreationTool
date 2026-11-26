import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Download, File, Folder, FolderOpen } from "lucide-react";
import { api, fileUrl } from "../../api";
import type { AssetItem, AssetManifest, FileEntry } from "../../types";
import { fmtBytes, fmtDate, isEditable, mediaKind } from "../../lib/format";
import { Badge, cx, Empty, ErrorBox, Loading, Section } from "../ui";
import { useWorkspaceFiles, useWorkspaceJson } from "./hooks";
import { RawEditor } from "./Viewers";

export function MediaPreview({ videoId, path, className }: { videoId: string; path: string; className?: string }) {
  const k = mediaKind(path);
  const src = fileUrl(videoId, path);
  if (k === "video") return <video className={cx("w-full rounded-lg bg-black", className)} src={src} controls preload="metadata" />;
  if (k === "image") return <img className={cx("w-full rounded-lg bg-black object-contain", className)} src={src} alt={path} loading="lazy" />;
  if (k === "audio") return <audio className="w-full" src={src} controls preload="none" />;
  return null;
}

// ---------------- assets ----------------

const KINDS = ["all", "image", "footage", "music", "sfx", "voice", "other"];

export function AssetsView({ videoId }: { videoId: string }) {
  const manifest = useWorkspaceJson<AssetManifest>(videoId, "assets/manifest.json");
  const rows = useQuery({ queryKey: ["assets", videoId], queryFn: () => api.assets(videoId), retry: false });
  const subs = useWorkspaceFiles(videoId, "subtitles");
  const [kind, setKind] = useState("all");
  const items: AssetItem[] = manifest.data?.items?.length
    ? manifest.data.items
    : (rows.data || []).map((a) => ({ id: a.id, kind: a.kind, path: a.path, source: a.source, license: a.license, credit: a.credit, status: "acquired" }));
  const shown = items.filter((i) => kind === "all" || i.kind === kind || (kind === "other" && !KINDS.includes(i.kind)));
  const missingLicence = items.filter((i) => i.status === "acquired" && !i.license).length;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1.5 items-center">
        {KINDS.map((k) => (
          <button key={k} className={cx("btn btn-sm capitalize", kind === k && "ring-2 ring-accent")} onClick={() => setKind(k)}>
            {k}
          </button>
        ))}
        {missingLicence > 0 && <Badge tone="red">{missingLicence} without licence</Badge>}
      </div>
      {manifest.isLoading || rows.isLoading ? (
        <Loading />
      ) : manifest.error ? (
        <ErrorBox error={manifest.error} />
      ) : !shown.length ? (
        <Empty>No assets yet. The asset plan writes assets/manifest.json; engines fill it in.</Empty>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          {shown.map((a) => (
            <AssetCard key={a.id} videoId={videoId} a={a} />
          ))}
        </div>
      )}
      {(subs.data || []).length > 0 && (
        <Section title="Subtitles">
          <ul className="flex flex-wrap gap-2">
            {subs.data!.filter((f) => !f.is_dir).map((f) => (
              <li key={f.path}>
                <a className="btn btn-sm font-mono" href={fileUrl(videoId, f.path)} target="_blank" rel="noreferrer">
                  {f.name}
                </a>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

export function AssetCard({ videoId, a }: { videoId: string; a: AssetItem }) {
  return (
    <div className="card overflow-hidden">
      <div className="aspect-video bg-panel2 grid place-items-center">
        {a.path && ["image", "video"].includes(mediaKind(a.path)) ? (
          <MediaPreview videoId={videoId} path={a.path} className="h-full rounded-none" />
        ) : a.path && mediaKind(a.path) === "audio" ? (
          <div className="p-3 w-full">
            <MediaPreview videoId={videoId} path={a.path} />
          </div>
        ) : (
          <span className="subtle capitalize">{a.kind}</span>
        )}
      </div>
      <div className="p-3 text-xs space-y-1">
        <div className="flex items-center gap-1.5">
          <b className="font-mono">{a.id}</b>
          <Badge tone="grey">{a.kind}</Badge>
          {a.status && <Badge tone={a.status === "acquired" ? "green" : a.status === "missing" ? "red" : a.status === "rejected" ? "muted" : "slate"}>{a.status}</Badge>}
          {a.paid && <Badge tone="amber">paid</Badge>}
        </div>
        {a.path && <div className="font-mono text-muted truncate" title={a.path}>{a.path}</div>}
        <div>
          <span className="text-muted">Licence:</span> {a.license || <span className="text-red-600 dark:text-red-300">unknown</span>}
        </div>
        {a.credit && (
          <div>
            <span className="text-muted">Credit:</span> {a.credit}
          </div>
        )}
        {(a.source || a.provider) && (
          <div className="text-muted">
            {a.source}
            {a.provider ? ` · via ${a.provider}` : ""}
            {a.source_url && (
              <>
                {" · "}
                <a className="text-accent hover:underline" href={a.source_url} target="_blank" rel="noreferrer">
                  source
                </a>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------- preview ----------------

const PREFERRED = ["renders/final_captioned.mp4", "renders/final.mp4", "renders/preview.mp4"];

export function PreviewView({ videoId }: { videoId: string }) {
  const renders = useWorkspaceFiles(videoId, "renders");
  const scenes = useWorkspaceFiles(videoId, "scenes");
  const outputs = useWorkspaceFiles(videoId, "outputs");
  const vids = (l?: FileEntry[]) => (l || []).filter((f) => !f.is_dir && mediaKind(f.path) === "video");
  const all = [...vids(outputs.data), ...vids(renders.data), ...vids(scenes.data)];
  const def = PREFERRED.find((p) => all.some((f) => f.path === p)) || all[0]?.path;
  const [sel, setSel] = useState<string | undefined>();
  const current = sel && all.some((f) => f.path === sel) ? sel : def;
  if (renders.isLoading || scenes.isLoading) return <Loading />;
  if (!all.length) return <Empty>No renders yet. Segment renders appear in scenes/, the preview and final film in renders/.</Empty>;
  const Group = ({ title, files }: { title: string; files: FileEntry[] }) =>
    files.length ? (
      <div>
        <div className="label">{title}</div>
        <ul className="space-y-1">
          {files.map((f) => (
            <li key={f.path}>
              <button className={cx("w-full text-left rounded-lg px-2 py-1.5 text-xs font-mono hover:bg-panel2", current === f.path && "bg-accent/15")} onClick={() => setSel(f.path)}>
                {f.name} <span className="text-muted">{fmtBytes(f.size)}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    ) : null;
  return (
    <div className="grid lg:grid-cols-[1fr_260px] gap-4">
      <div>
        {current && (
          <>
            <video key={current} className="w-full rounded-xl bg-black max-h-[70vh]" src={fileUrl(videoId, current)} controls preload="metadata" />
            <div className="flex items-center justify-between mt-2">
              <code className="text-xs text-muted">{current}</code>
              <a className="btn btn-sm" href={fileUrl(videoId, current)} download>
                <Download className="h-3.5 w-3.5" /> Download
              </a>
            </div>
          </>
        )}
      </div>
      <div className="space-y-3">
        <Group title="Deliverables (outputs/)" files={vids(outputs.data)} />
        <Group title="Renders" files={vids(renders.data)} />
        <Group title="Segments (scenes/)" files={vids(scenes.data)} />
      </div>
    </div>
  );
}

// ---------------- history ----------------

export function HistoryView({ videoId }: { videoId: string }) {
  const q = useQuery({ queryKey: ["history", videoId], queryFn: () => api.history(videoId) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.refetch()} />;
  const items = [...(q.data || [])].sort((a, b) => (b.at || "").localeCompare(a.at || ""));
  if (!items.length) return <Empty>No history yet.</Empty>;
  return (
    <ol className="relative border-l border-line ml-2 space-y-3">
      {items.map((h, i) => (
        <li key={i} className="ml-4">
          <span className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border-2 border-bg bg-accent" />
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <b>{h.title}</b>
            <Badge tone="slate">{h.kind}</Badge>
            <span className="subtle">{fmtDate(h.at)}</span>
          </div>
          {h.details !== null && h.details !== undefined && (typeof h.details !== "object" || Object.keys(h.details as object).length > 0) && (
            <details className="text-xs mt-0.5">
              <summary className="cursor-pointer text-muted">details</summary>
              <pre className="rounded bg-panel2 p-2 mt-1 overflow-auto font-mono">{typeof h.details === "string" ? h.details : JSON.stringify(h.details, null, 2)}</pre>
            </details>
          )}
        </li>
      ))}
    </ol>
  );
}

// ---------------- files ----------------

export function FilesView({ videoId }: { videoId: string }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="grid lg:grid-cols-[minmax(240px,340px)_1fr] gap-4">
      <div className="card p-2 max-h-[70vh] overflow-auto">
        <Dir videoId={videoId} path="" depth={0} onOpen={setOpen} selected={open} initiallyOpen />
      </div>
      <div>{open ? <FilePane videoId={videoId} path={open} /> : <Empty>Select a file to preview it.</Empty>}</div>
    </div>
  );
}

function Dir({ videoId, path, depth, onOpen, selected, initiallyOpen }: { videoId: string; path: string; depth: number; onOpen: (p: string) => void; selected: string | null; initiallyOpen?: boolean }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const q = useWorkspaceFiles(videoId, path, initiallyOpen || depth > 0);
  if (q.isLoading) return <div className="subtle px-2 py-1">loading…</div>;
  if (q.error) return <ErrorBox error={q.error} />;
  const list = [...(q.data || [])].sort((a, b) => Number(b.is_dir) - Number(a.is_dir) || a.name.localeCompare(b.name));
  if (!list.length && depth === 0) return <Empty>The workspace is empty.</Empty>;
  return (
    <ul>
      {list.map((f) => (
        <li key={f.path}>
          {f.is_dir ? (
            <>
              <button className="w-full flex items-center gap-1 rounded px-1 py-0.5 text-xs hover:bg-panel2" style={{ paddingLeft: depth * 12 + 4 }} onClick={() => setExpanded((e) => ({ ...e, [f.path]: !e[f.path] }))}>
                <ChevronRight className={cx("h-3 w-3 transition", expanded[f.path] && "rotate-90")} />
                {expanded[f.path] ? <FolderOpen className="h-3.5 w-3.5 text-amber-500" /> : <Folder className="h-3.5 w-3.5 text-amber-500" />}
                {f.name}
              </button>
              {expanded[f.path] && <Dir videoId={videoId} path={f.path} depth={depth + 1} onOpen={onOpen} selected={selected} />}
            </>
          ) : (
            <button
              className={cx("w-full flex items-center gap-1 rounded px-1 py-0.5 text-xs hover:bg-panel2", selected === f.path && "bg-accent/15")}
              style={{ paddingLeft: depth * 12 + 20 }}
              onClick={() => onOpen(f.path)}
            >
              <File className="h-3.5 w-3.5 text-muted" />
              <span className="truncate flex-1 text-left">{f.name}</span>
              <span className="text-muted">{fmtBytes(f.size)}</span>
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

function FilePane({ videoId, path }: { videoId: string; path: string }) {
  const k = mediaKind(path);
  const textual = k === "json" || k === "text";
  const [edit, setEdit] = useState(false);
  const q = useQuery({ queryKey: ["vfile-text", videoId, path], queryFn: () => api.fileText(videoId, path), enabled: textual });
  return (
    <div className="card p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <code className="text-xs flex-1 break-all">{path}</code>
        {textual && isEditable(path) && !edit && (
          <button className="btn btn-sm" onClick={() => setEdit(true)}>
            Edit
          </button>
        )}
        <a className="btn btn-sm" href={fileUrl(videoId, path)} target="_blank" rel="noreferrer">
          Open
        </a>
      </div>
      {textual ? (
        q.isLoading ? (
          <Loading />
        ) : q.error ? (
          <ErrorBox error={q.error} />
        ) : edit ? (
          <RawEditor videoId={videoId} path={path} value={q.data ?? ""} json={k === "json" && !path.endsWith(".jsonl")} onDone={() => { setEdit(false); q.refetch(); }} />
        ) : (
          <pre className="text-xs font-mono bg-panel2 rounded-lg p-2 max-h-[60vh] overflow-auto whitespace-pre-wrap break-all">{prettify(q.data ?? "", k)}</pre>
        )
      ) : k === "other" ? (
        <p className="subtle">No preview for this file type.</p>
      ) : (
        <MediaPreview videoId={videoId} path={path} />
      )}
    </div>
  );
}

function prettify(t: string, k: string) {
  if (k !== "json") return t;
  try {
    return JSON.stringify(JSON.parse(t), null, 2);
  } catch {
    return t;
  }
}

