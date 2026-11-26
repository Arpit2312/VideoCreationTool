import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileUp, Pause, Play, Plus, RotateCcw, Square, Trash2 } from "lucide-react";
import { api, explainError } from "../api";
import type { Batch, ImportPreviewItem, VideoConfig } from "../types";
import { DEFAULT_CONFIG } from "../lib/config";
import { bucketOf } from "../lib/status";
import { fmtDate } from "../lib/format";
import { CreativeSettings } from "../components/ConfigForm";
import { VideoRow } from "../components/VideoRow";
import { cx, Empty, ErrorBox, Field, Loading, Modal, PageHeader, ProgressBar, Section, Select, StatusBadge } from "../components/ui";

interface Row {
  title: string;
  idea: string;
  overrides: Partial<VideoConfig>;
}

const blank = (): Row => ({ title: "", idea: "", overrides: {} });

export function batchProgress(b: Batch): { total: number; done: number; failed: number } {
  const vids = b.videos || [];
  if (vids.length) {
    return {
      total: vids.length,
      done: vids.filter((v) => v.status === "completed").length,
      failed: vids.filter((v) => bucketOf(v.status) === "failed").length,
    };
  }
  const p = b.progress;
  if (p && typeof p === "object") return { total: Number(p.total ?? 0), done: Number(p.completed ?? 0), failed: Number(p.failed ?? 0) };
  return { total: 0, done: 0, failed: 0 };
}

export default function BatchStudio() {
  const batches = useQuery({ queryKey: ["batches"], queryFn: api.batches, refetchInterval: 10_000 });
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  useEffect(() => {
    if (!selected && !creating && batches.data?.length) setSelected(batches.data[0].id);
  }, [batches.data, selected, creating]);
  return (
    <div>
      <PageHeader
        title="Batch Studio"
        subtitle="Produce many videos with shared settings and per-row overrides. The scheduler respects your concurrency limits."
        actions={
          <button className="btn-primary" onClick={() => { setCreating(true); setSelected(null); }}>
            <Plus className="h-4 w-4" /> New batch
          </button>
        }
      />
      <div className="grid lg:grid-cols-[260px_1fr] gap-4">
        <Section title="Batches">
          {batches.isLoading ? (
            <Loading />
          ) : batches.error ? (
            <ErrorBox error={batches.error} onRetry={() => batches.refetch()} />
          ) : (
            <ul className="space-y-1">
              {(batches.data || []).map((b) => {
                const p = batchProgress(b);
                return (
                  <li key={b.id}>
                    <button
                      className={cx("w-full text-left rounded-lg px-3 py-2 text-sm", selected === b.id ? "bg-accent/15" : "hover:bg-panel2")}
                      onClick={() => { setSelected(b.id); setCreating(false); }}
                    >
                      <div className="flex items-center gap-2">
                        <span className="truncate flex-1 font-medium">{b.name}</span>
                        <StatusBadge status={b.status} />
                      </div>
                      {p.total > 0 && (
                        <div className="mt-1.5">
                          <ProgressBar value={p.done / p.total} tone="green" />
                        </div>
                      )}
                      <div className="subtle mt-1">{fmtDate(b.created_at)}</div>
                    </button>
                  </li>
                );
              })}
              {!batches.data?.length && <li className="subtle">No batches yet.</li>}
            </ul>
          )}
        </Section>
        <div className="min-w-0">
          {creating ? (
            <NewBatch onCreated={(id) => { setCreating(false); setSelected(id); }} />
          ) : selected ? (
            <BatchDetail id={selected} />
          ) : (
            <Empty>Create a batch to get started.</Empty>
          )}
        </div>
      </div>
    </div>
  );
}

function BatchDetail({ id }: { id: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["batch", id], queryFn: () => api.batch(id), refetchInterval: 5000 });
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const act = useMutation({
    mutationFn: ({ action, ids }: { action: "start" | "pause" | "resume" | "cancel"; ids?: string[] }) => api.batchAction(id, action, ids ? { video_ids: ids } : undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["batch", id] });
      qc.invalidateQueries({ queryKey: ["batches"] });
      qc.invalidateQueries({ queryKey: ["videos"] });
    },
  });
  const retry = useMutation({
    mutationFn: async (ids: string[]) => {
      for (const v of ids) await api.videoAction(v, "retry");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["batch", id] }),
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.refetch()} />;
  const b = q.data!;
  const vids = b.videos || [];
  const p = batchProgress(b);
  const failed = vids.filter((v) => bucketOf(v.status) === "failed").map((v) => v.id);
  const toggle = (vid: string) => setPicked((s) => { const n = new Set(s); if (n.has(vid)) n.delete(vid); else n.add(vid); return n; });
  return (
    <div className="space-y-4">
      <Section
        title={
          <span className="flex items-center gap-2">
            {b.name} <StatusBadge status={b.status} />
          </span>
        }
        actions={
          <a className="btn btn-sm" href={api.batchExportUrl(b.id)}>
            <Download className="h-3.5 w-3.5" /> Download zip
          </a>
        }
      >
        <div className="flex items-center gap-3 mb-3">
          <div className="flex-1"><ProgressBar value={p.total ? p.done / p.total : 0} tone="green" /></div>
          <span className="subtle tabular-nums">{p.done}/{p.total} done{p.failed ? ` · ${p.failed} failed` : ""}</span>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-primary btn-sm" disabled={act.isPending} onClick={() => act.mutate({ action: "start" })}><Play className="h-3.5 w-3.5" /> Start all</button>
          <button className="btn btn-sm" disabled={act.isPending || !picked.size} onClick={() => act.mutate({ action: "start", ids: [...picked] })}><Play className="h-3.5 w-3.5" /> Start selected ({picked.size})</button>
          <button className="btn btn-sm" disabled={act.isPending} onClick={() => act.mutate({ action: "pause" })}><Pause className="h-3.5 w-3.5" /> Pause</button>
          <button className="btn btn-sm" disabled={act.isPending} onClick={() => act.mutate({ action: "resume" })}><Play className="h-3.5 w-3.5" /> Resume</button>
          <button className="btn-danger btn-sm" disabled={act.isPending} onClick={() => window.confirm("Cancel every video in this batch?") && act.mutate({ action: "cancel" })}><Square className="h-3.5 w-3.5" /> Cancel</button>
          <button className="btn btn-sm" disabled={retry.isPending || !failed.length} onClick={() => retry.mutate(failed)}><RotateCcw className="h-3.5 w-3.5" /> Retry failed ({failed.length})</button>
        </div>
        {(act.error || retry.error) && <div className="mt-2"><ErrorBox error={act.error || retry.error} /></div>}
      </Section>
      {vids.length ? (
        <div className="space-y-2">
          {vids.map((v) => (
            <div key={v.id} className="flex gap-2 items-start">
              <input type="checkbox" className="mt-5" aria-label={`Select ${v.title}`} checked={picked.has(v.id)} onChange={() => toggle(v.id)} />
              <div className="flex-1 min-w-0"><VideoRow video={v} /></div>
            </div>
          ))}
        </div>
      ) : (
        <Empty>This batch has no videos.</Empty>
      )}
    </div>
  );
}

function NewBatch({ onCreated }: { onCreated: (id: string) => void }) {
  const qc = useQueryClient();
  const presets = useQuery({ queryKey: ["presets"], queryFn: api.presets, staleTime: 300_000 });
  const engines = useQuery({ queryKey: ["engines"], queryFn: api.engines, staleTime: 60_000 });
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings, staleTime: 60_000 });
  const [name, setName] = useState(`Batch ${new Date().toLocaleDateString()}`);
  const [projectId, setProjectId] = useState("");
  const [rows, setRows] = useState<Row[]>([blank(), blank()]);
  const [shared, setShared] = useState<VideoConfig>(DEFAULT_CONFIG);
  const [showShared, setShowShared] = useState(false);
  const [preview, setPreview] = useState<ImportPreviewItem[] | null>(null);
  const [importErrors, setImportErrors] = useState<string[]>([]);
  const [inited, setInited] = useState(false);
  const [startNow, setStartNow] = useState(true);
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!settings.data || inited) return;
    setShared((c) => ({ ...c, ...(settings.data.default_video_config || {}), zero_cost: settings.data.zero_cost_default !== false }));
    setInited(true);
  }, [settings.data, inited]);
  useEffect(() => {
    if (!projectId && projects.data?.length) setProjectId(projects.data[0].id);
  }, [projects.data, projectId]);

  const imp = useMutation({
    mutationFn: (f: File) => api.importBatch(f),
    onSuccess: (r) => {
      setPreview(Array.isArray(r) ? r : r.items || []);
      setImportErrors(Array.isArray(r) ? [] : r.errors || []);
    },
  });
  const create = useMutation({
    mutationFn: async () => {
      let pid = projectId;
      if (!pid) pid = (await api.createProject({ name: "My videos" })).id;
      const items = rows.filter((r) => r.idea.trim()).map((r) => ({ title: r.title.trim() || r.idea.slice(0, 60), idea: r.idea.trim(), overrides: r.overrides }));
      const b = await api.createBatch({ project_id: pid, name, shared_config: shared, items });
      if (startNow) await api.batchAction(b.id, "start");
      return b;
    },
    onSuccess: (b) => {
      qc.invalidateQueries({ queryKey: ["batches"] });
      onCreated(b.id);
    },
  });
  const setRow = (i: number, p: Partial<Row>) => setRows((l) => l.map((r, j) => (j === i ? { ...r, ...p } : r)));
  const setOv = (i: number, k: keyof VideoConfig, v: unknown) =>
    setRows((l) =>
      l.map((r, j) => {
        if (j !== i) return r;
        const o = { ...r.overrides } as Record<string, unknown>;
        if (v === "" || v === undefined) delete o[k];
        else o[k] = v;
        return { ...r, overrides: o as Partial<VideoConfig> };
      }),
    );
  const styles = (presets.data?.visual_styles || []).map((x) => (typeof x === "string" ? { id: x, name: x } : { id: String((x as { id?: string }).id), name: String((x as { name?: string }).name ?? (x as { id?: string }).id) }));
  const valid = rows.filter((r) => r.idea.trim()).length;

  return (
    <div className="space-y-4">
      <Section title="New batch">
        <div className="grid sm:grid-cols-2 gap-3 mb-4">
          <Field label="Batch name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Project">
            <Select value={projectId} onChange={setProjectId} options={projects.data?.length ? projects.data.map((p) => ({ id: p.id, name: p.name })) : [{ id: "", name: "“My videos” (created automatically)" }]} />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <div className="label mb-0 flex-1">Videos ({valid})</div>
          <input ref={fileRef} type="file" accept=".csv,.txt,text/csv,text/plain" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) imp.mutate(f); e.target.value = ""; }} />
          <button className="btn btn-sm" onClick={() => fileRef.current?.click()} disabled={imp.isPending}><FileUp className="h-3.5 w-3.5" /> {imp.isPending ? "Reading…" : "Import CSV / TXT"}</button>
          <button className="btn btn-sm" onClick={() => setRows((r) => [...r, blank()])}><Plus className="h-3.5 w-3.5" /> Add row</button>
        </div>
        {imp.error && <ErrorBox error={imp.error} title="Import failed" />}
        <p className="subtle mb-2">CSV columns: <code>title,idea</code> plus optional override columns (e.g. <code>duration_seconds,visual_style,tone,aspect_ratio</code>). TXT: one idea per line.</p>
        <div className="space-y-2">
          {rows.map((r, i) => (
            <div key={i} className="rounded-lg border border-line p-2 grid gap-2 md:grid-cols-[1fr_2fr_auto]">
              <input className="input" placeholder="Title" value={r.title} onChange={(e) => setRow(i, { title: e.target.value })} aria-label={`Row ${i + 1} title`} />
              <input className="input" placeholder="Idea" value={r.idea} onChange={(e) => setRow(i, { idea: e.target.value })} aria-label={`Row ${i + 1} idea`} />
              <button className="btn-ghost btn-sm" aria-label="Remove row" onClick={() => setRows((l) => l.filter((_, j) => j !== i))}><Trash2 className="h-3.5 w-3.5" /></button>
              <div className="md:col-span-3 flex flex-wrap gap-2 items-center text-xs">
                <span className="text-muted">Overrides:</span>
                <input className="input w-24 py-1 text-xs" type="number" placeholder="secs" value={(r.overrides.duration_seconds as number | undefined) ?? ""} onChange={(e) => setOv(i, "duration_seconds", e.target.value ? Number(e.target.value) : "")} aria-label="Duration override" />
                <select className="input w-auto py-1 text-xs" value={(r.overrides.aspect_ratio as string) ?? ""} onChange={(e) => setOv(i, "aspect_ratio", e.target.value)} aria-label="Aspect override">
                  <option value="">aspect (shared)</option>
                  {["16:9", "9:16", "1:1", "4:5", "4:3"].map((a) => <option key={a}>{a}</option>)}
                </select>
                <select className="input w-auto py-1 text-xs" value={(r.overrides.visual_style as string) ?? ""} onChange={(e) => setOv(i, "visual_style", e.target.value)} aria-label="Style override">
                  <option value="">style (shared)</option>
                  {styles.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
                <input className="input w-28 py-1 text-xs" placeholder="tone" value={(r.overrides.tone as string) ?? ""} onChange={(e) => setOv(i, "tone", e.target.value)} aria-label="Tone override" />
                {Object.keys(r.overrides).length > 0 && <span className="text-accent">{Object.keys(r.overrides).length} override(s)</span>}
              </div>
            </div>
          ))}
        </div>
      </Section>
      <Section title="Shared settings" actions={<button className="btn btn-sm" onClick={() => setShowShared(!showShared)}>{showShared ? "Hide" : "Edit"}</button>}>
        {showShared ? (
          <CreativeSettings config={shared} set={(p) => setShared((c) => ({ ...c, ...p }))} presets={presets.data} engines={engines.data || []} />
        ) : (
          <p className="text-sm text-muted">
            {shared.duration_seconds}s · {shared.aspect_ratio} · {shared.visual_style} · {shared.tone} · {shared.zero_cost ? "zero-cost" : "paid allowed"} · approval {shared.approval_mode} · collaborative engines
          </p>
        )}
      </Section>
      <div className="flex flex-wrap items-center justify-end gap-3">
        <label className="text-sm flex items-center gap-2"><input type="checkbox" checked={startNow} onChange={(e) => setStartNow(e.target.checked)} /> Start all after creating</label>
        <button className="btn-primary" disabled={!valid || create.isPending || !name.trim()} onClick={() => create.mutate()}>
          {create.isPending ? "Creating…" : `Create batch (${valid})`}
        </button>
      </div>
      {create.error && <p className="text-sm text-red-600 dark:text-red-300 text-right">{explainError(create.error)}</p>}

      <Modal
        open={!!preview}
        onClose={() => setPreview(null)}
        title={`Import preview (${preview?.length ?? 0} rows)`}
        footer={
          <>
            <button className="btn" onClick={() => setPreview(null)}>Cancel</button>
            <button
              className="btn-primary"
              onClick={() => {
                const add = (preview || []).map((p) => ({ title: p.title || "", idea: p.idea || "", overrides: p.overrides || {} }));
                setRows((r) => [...r.filter((x) => x.idea.trim()), ...add]);
                setPreview(null);
              }}
            >
              Add {preview?.length ?? 0} rows
            </button>
          </>
        }
      >
        {importErrors.length > 0 && (
          <ul className="mb-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-xs space-y-0.5">
            {importErrors.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        )}
        <ul className="space-y-1.5 max-h-[50vh] overflow-auto">
          {(preview || []).map((p, i) => (
            <li key={i} className="rounded bg-panel2 p-2">
              <div className="font-medium">{p.title || <span className="text-muted">(no title)</span>}</div>
              <div className="text-xs text-muted">{p.idea}</div>
              {p.overrides && Object.keys(p.overrides).length > 0 && <div className="text-[11px] font-mono mt-0.5">{JSON.stringify(p.overrides)}</div>}
            </li>
          ))}
        </ul>
      </Modal>
    </div>
  );
}
