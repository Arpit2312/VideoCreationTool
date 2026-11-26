import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Code2, ExternalLink, HelpCircle, Pencil, Save, XCircle } from "lucide-react";
import { api, fileUrl } from "../../api";
import type { AssetManifest, FactCheck, Research, Scene, Script, ScriptLine, Storyboard } from "../../types";
import { fmtDuration } from "../../lib/format";
import { Badge, cx, Empty, ErrorBox, Loading, Section, StatusBadge } from "../ui";
import { useWorkspaceFiles, useWorkspaceJson } from "./hooks";

// ---------------- generic raw editor ----------------

export function RawEditor({ videoId, path, value, json = true, onDone }: { videoId: string; path: string; value: string; json?: boolean; onDone?: () => void }) {
  const qc = useQueryClient();
  const [text, setText] = useState(value);
  const [parseErr, setParseErr] = useState<string | null>(null);
  useEffect(() => setText(value), [value]);
  const save = useMutation({
    mutationFn: () => api.saveFile(videoId, path, text),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["vfile", videoId] });
      qc.invalidateQueries({ queryKey: ["files", videoId] });
      qc.invalidateQueries({ queryKey: ["history", videoId] });
      onDone?.();
    },
  });
  const onSave = () => {
    if (json) {
      try {
        JSON.parse(text);
        setParseErr(null);
      } catch (e) {
        setParseErr((e as Error).message);
        return;
      }
    }
    save.mutate();
  };
  return (
    <div className="space-y-2">
      <textarea className="input font-mono text-xs" rows={18} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} aria-label={`Edit ${path}`} />
      {parseErr && <p className="text-xs text-red-600 dark:text-red-300">Invalid JSON: {parseErr}</p>}
      {save.error && <ErrorBox error={save.error} title="Save failed" />}
      <div className="flex gap-2 justify-end">
        {onDone && (
          <button className="btn" onClick={onDone}>
            Cancel
          </button>
        )}
        <button className="btn-primary" onClick={onSave} disabled={save.isPending}>
          <Save className="h-4 w-4" /> Save (creates a version)
        </button>
      </div>
      <p className="subtle">Saving does not re-run any stage. To have Claude revise it, use “Request changes” on the stage.</p>
    </div>
  );
}

// ---------------- research & script ----------------

const FC_ICON = {
  verified: <CheckCircle2 className="h-4 w-4 text-emerald-500" />,
  corrected: <Pencil className="h-4 w-4 text-sky-500" />,
  disputed: <XCircle className="h-4 w-4 text-red-500" />,
  unverified: <HelpCircle className="h-4 w-4 text-amber-500" />,
} as Record<string, JSX.Element>;

export function ResearchScriptView({ videoId }: { videoId: string }) {
  const research = useWorkspaceJson<Research>(videoId, "research/research.json");
  const fc = useWorkspaceJson<FactCheck>(videoId, "research/factcheck.json");
  const script = useWorkspaceJson<Script>(videoId, "scripts/script.json");
  const [rawEdit, setRawEdit] = useState<string | null>(null);

  const fcByClaim = new Map((fc.data?.items || []).map((i) => [i.claim_id, i]));
  const unresolved = (fc.data?.items || []).filter((i) => i.status !== "verified");

  return (
    <div className="grid xl:grid-cols-2 gap-4">
      <div className="space-y-4">
        <Section
          title="Research"
          actions={research.data && <button className="btn-ghost btn-sm" onClick={() => setRawEdit(rawEdit === "research" ? null : "research")}><Code2 className="h-3.5 w-3.5" /> JSON</button>}
        >
          {research.isLoading ? (
            <Loading />
          ) : research.error ? (
            <ErrorBox error={research.error} />
          ) : !research.data ? (
            <Empty>Research has not been produced yet (research/research.json).</Empty>
          ) : rawEdit === "research" ? (
            <RawEditor videoId={videoId} path="research/research.json" value={JSON.stringify(research.data, null, 2)} onDone={() => setRawEdit(null)} />
          ) : (
            <div className="space-y-3 text-sm">
              <p className="whitespace-pre-wrap">{research.data.summary}</p>
              {research.data.angles?.length > 0 && (
                <div>
                  <div className="label">Angles</div>
                  <ul className="list-disc pl-5 space-y-0.5">{research.data.angles.map((a, i) => <li key={i}>{a}</li>)}</ul>
                </div>
              )}
              {research.data.key_facts?.length > 0 && (
                <div>
                  <div className="label">Key facts</div>
                  <ul className="space-y-1">
                    {research.data.key_facts.map((f, i) => {
                      const c = f.id ? fcByClaim.get(f.id) : undefined;
                      return (
                        <li key={f.id ?? i} className="flex gap-2 items-start">
                          {c ? FC_ICON[c.status] : <span className="h-4 w-4" />}
                          <span className="flex-1">
                            {f.claim} {f.source_ids?.length ? <span className="text-muted text-xs">[{f.source_ids.join(", ")}]</span> : null}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
              <div>
                <div className="label">Sources ({research.data.sources?.length ?? 0})</div>
                <ul className="space-y-1 text-xs">
                  {(research.data.sources || []).map((s) => (
                    <li key={s.id}>
                      <span className="font-mono text-muted">{s.id}</span>{" "}
                      {s.url ? (
                        <a className="text-accent hover:underline inline-flex items-center gap-0.5" href={s.url} target="_blank" rel="noreferrer">
                          {s.title} <ExternalLink className="h-3 w-3" />
                        </a>
                      ) : (
                        s.title
                      )}
                      {s.publisher && <span className="text-muted"> · {s.publisher}</span>}
                      {s.accessed && <span className="text-muted"> · accessed {s.accessed}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </Section>
        <Section title="Fact-check" actions={unresolved.length > 0 && <Badge tone="amber">{unresolved.length} unresolved</Badge>}>
          {fc.isLoading ? (
            <Loading />
          ) : !fc.data ? (
            <Empty>No fact-check yet (research/factcheck.json).</Empty>
          ) : (
            <ul className="space-y-2 text-sm">
              {fc.data.items.map((i) => (
                <li key={i.claim_id} className={cx("rounded-lg p-2 flex gap-2", i.status === "verified" ? "bg-panel2" : "bg-amber-500/10 border border-amber-500/30")}>
                  {FC_ICON[i.status] ?? <HelpCircle className="h-4 w-4" />}
                  <div className="flex-1">
                    <div>{i.claim}</div>
                    <div className="text-xs text-muted">
                      <b className="capitalize">{i.status}</b>
                      {i.note && ` — ${i.note}`}
                      {i.source_ids?.length > 0 && ` [${i.source_ids.join(", ")}]`}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>
      <ScriptView videoId={videoId} script={script.data} loading={script.isLoading} error={script.error} fcWarn={new Set(unresolved.map((u) => u.claim_id))} />
    </div>
  );
}

function ScriptView({ videoId, script, loading, error, fcWarn }: { videoId: string; script?: Script | null; loading: boolean; error: unknown; fcWarn: Set<string> }) {
  const qc = useQueryClient();
  const [raw, setRaw] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<ScriptLine | null>(null);
  const [version, setVersion] = useState<string>("current");
  const versions = useWorkspaceFiles(videoId, "scripts");
  const vdirs = (versions.data || []).filter((f) => f.is_dir && /^v\d+$/.test(f.name)).sort((a, b) => Number(b.name.slice(1)) - Number(a.name.slice(1)));
  const old = useWorkspaceJson<Script>(videoId, `scripts/${version}/script.json`, version !== "current");
  const shown = version === "current" ? script : old.data;

  const save = useMutation({
    mutationFn: (next: Script) => api.saveFile(videoId, "scripts/script.json", JSON.stringify(next, null, 2)),
    onSuccess: () => {
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["vfile", videoId] });
      qc.invalidateQueries({ queryKey: ["files", videoId] });
    },
  });

  return (
    <Section
      title={shown ? `Script — ${shown.title}` : "Script"}
      actions={
        <div className="flex gap-1.5 items-center">
          {vdirs.length > 0 && (
            <select className="input py-1 text-xs w-auto" value={version} onChange={(e) => setVersion(e.target.value)} aria-label="Script version">
              <option value="current">current (v{script?.version ?? "?"})</option>
              {vdirs.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.name}
                </option>
              ))}
            </select>
          )}
          {script && version === "current" && (
            <button className="btn-ghost btn-sm" onClick={() => setRaw(!raw)}>
              <Code2 className="h-3.5 w-3.5" /> {raw ? "Lines" : "JSON"}
            </button>
          )}
        </div>
      }
    >
      {loading ? (
        <Loading />
      ) : error ? (
        <ErrorBox error={error} />
      ) : !shown ? (
        <Empty>No script yet (scripts/script.json).</Empty>
      ) : raw && version === "current" ? (
        <RawEditor videoId={videoId} path="scripts/script.json" value={JSON.stringify(script, null, 2)} onDone={() => setRaw(false)} />
      ) : (
        <div>
          <div className="subtle mb-2">
            {shown.lines.length} lines · {shown.word_count || "?"} words · ~{fmtDuration(shown.estimated_seconds)}
          </div>
          {save.error && <ErrorBox error={save.error} title="Save failed" />}
          <ol className="space-y-1.5">
            {shown.lines.map((l, idx) => {
              const warn = (l.fact_ids || []).some((f) => fcWarn.has(f));
              const showSection = l.section && l.section !== shown.lines[idx - 1]?.section;
              return (
                <li key={l.id}>
                  {showSection && <div className="text-xs uppercase tracking-wide text-muted mt-3 mb-1">{l.section}</div>}
                  {editing === l.id && draft ? (
                    <div className="rounded-lg border border-accent p-2 space-y-1.5">
                      <textarea className="input" rows={2} value={draft.text} onChange={(e) => setDraft({ ...draft, text: e.target.value })} aria-label="Line text" />
                      <input className="input text-xs" placeholder="Spoken form (optional)" value={draft.spoken ?? ""} onChange={(e) => setDraft({ ...draft, spoken: e.target.value })} />
                      <div className="flex gap-2 justify-end">
                        <button className="btn btn-sm" onClick={() => setEditing(null)}>
                          Cancel
                        </button>
                        <button
                          className="btn-primary btn-sm"
                          disabled={save.isPending}
                          onClick={() => script && save.mutate({ ...script, lines: script.lines.map((x) => (x.id === l.id ? draft : x)) })}
                        >
                          Save line
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className={cx("group flex gap-2 rounded-lg p-2 hover:bg-panel2", warn && "bg-amber-500/10")}>
                      <span className="font-mono text-[11px] text-muted w-9 shrink-0 pt-0.5">{l.id}</span>
                      <div className="flex-1 text-sm">
                        {l.text}
                        {l.spoken && l.spoken !== l.text && <div className="text-xs text-muted italic">spoken: {l.spoken}</div>}
                        {warn && (
                          <div className="text-xs text-amber-700 dark:text-amber-300 flex items-center gap-1 mt-0.5">
                            <AlertTriangle className="h-3 w-3" /> relies on an unverified claim ({l.fact_ids?.join(", ")})
                          </div>
                        )}
                      </div>
                      {version === "current" && (
                        <button className="btn-ghost btn-sm opacity-0 group-hover:opacity-100 focus:opacity-100" aria-label={`Edit line ${l.id}`} onClick={() => { setEditing(l.id); setDraft({ ...l }); }}>
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </Section>
  );
}

// ---------------- storyboard ----------------

export function StoryboardView({ videoId }: { videoId: string }) {
  const sb = useWorkspaceJson<Storyboard>(videoId, "storyboard/storyboard.json");
  const script = useWorkspaceJson<Script>(videoId, "scripts/script.json");
  const manifest = useWorkspaceJson<AssetManifest>(videoId, "assets/manifest.json");
  const [raw, setRaw] = useState(false);
  if (sb.isLoading) return <Loading />;
  if (sb.error) return <ErrorBox error={sb.error} />;
  if (!sb.data) return <Empty>No storyboard yet (storyboard/storyboard.json).</Empty>;
  const lines = new Map((script.data?.lines || []).map((l) => [l.id, l]));
  const assets = new Map((manifest.data?.items || []).map((a) => [a.id, a]));
  return (
    <div className="space-y-4">
      <Section
        title={`Segments (${sb.data.segments.length})`}
        actions={<button className="btn-ghost btn-sm" onClick={() => setRaw(!raw)}><Code2 className="h-3.5 w-3.5" /> {raw ? "Cards" : "JSON"}</button>}
      >
        {raw ? (
          <RawEditor videoId={videoId} path="storyboard/storyboard.json" value={JSON.stringify(sb.data, null, 2)} onDone={() => setRaw(false)} />
        ) : (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {sb.data.segments.map((s) => (
              <div key={s.id} className="rounded-lg bg-panel2 p-3 text-sm">
                <div className="flex items-center gap-2">
                  <b className="font-mono">{s.id}</b>
                  <Badge tone="blue">{s.engine}</Badge>
                  <span className="subtle">scenes {s.scene_numbers.join(", ")}</span>
                </div>
                {s.engine_options && Object.keys(s.engine_options).length > 0 && (
                  <div className="text-xs font-mono text-muted mt-1">{Object.entries(s.engine_options).map(([k, v]) => `${k}=${String(v)}`).join(" · ")}</div>
                )}
                {s.reason && <p className="text-xs mt-1">{s.reason}</p>}
              </div>
            ))}
          </div>
        )}
      </Section>
      {!raw && (
        <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-3">
          {sb.data.scenes.map((sc) => (
            <SceneCard key={sc.number} videoId={videoId} sc={sc} lines={lines} assets={assets} />
          ))}
        </div>
      )}
    </div>
  );
}

function SceneCard({ videoId, sc, lines, assets }: { videoId: string; sc: Scene; lines: Map<string, ScriptLine>; assets: Map<string, { id: string; kind: string; status?: string; path?: string | null }> }) {
  return (
    <div className="card overflow-hidden">
      <div className="aspect-video bg-black/80 grid place-items-center">
        {sc.preview ? (
          /\.(mp4|webm)$/i.test(sc.preview) ? (
            <video className="h-full w-full object-cover" src={fileUrl(videoId, sc.preview)} muted controls preload="metadata" />
          ) : (
            <img className="h-full w-full object-cover" src={fileUrl(videoId, sc.preview)} alt={`Scene ${sc.number} preview`} loading="lazy" />
          )
        ) : (
          <span className="text-xs text-zinc-400 px-4 text-center line-clamp-3">{sc.visual}</span>
        )}
      </div>
      <div className="p-3 space-y-1.5 text-sm">
        <div className="flex flex-wrap items-center gap-1.5">
          <b>Scene {sc.number}</b>
          <span className="font-mono text-xs text-muted">{sc.segment}</span>
          <Badge tone="blue">{sc.engine}</Badge>
          <span className="subtle ml-auto">{fmtDuration(sc.duration_seconds)}</span>
        </div>
        {sc.status && <StatusBadge status={sc.status === "planned" ? "pending" : sc.status} />}
        {sc.method && <div className="text-xs text-muted">Method: {sc.method}</div>}
        <div className="text-xs">
          <span className="text-muted">Visual:</span> {sc.visual}
        </div>
        <div className="text-xs space-y-0.5">
          {sc.line_ids.map((id) => (
            <div key={id} className="border-l-2 border-line pl-2">
              <span className="font-mono text-muted">{id}</span> {lines.get(id)?.text ?? ""}
            </div>
          ))}
        </div>
        {sc.asset_ids?.length ? (
          <div className="flex flex-wrap gap-1">
            {sc.asset_ids.map((a) => {
              const it = assets.get(a);
              return (
                <Badge key={a} tone={it?.status === "acquired" ? "green" : it?.status === "missing" ? "red" : "grey"} title={it ? `${it.kind} · ${it.status}` : "unknown asset"}>
                  {a}
                </Badge>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}
