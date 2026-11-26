import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, CircleX, GitBranch, HelpCircle, RefreshCw, Terminal } from "lucide-react";
import { api, explainError } from "../api";
import type { EngineHealth, EngineInfo, HealthItem, SetupStep } from "../types";
import { Badge, cx, ErrorBox, Loading, Modal, PageHeader, Section, SimulatedBadge, Toggle, ZeroCostBadge } from "../components/ui";

function ItemIcon({ status }: { status: string }) {
  if (["ok", "passed", "ready", "found"].includes(status)) return <CheckCircle2 className="h-4 w-4 text-emerald-500 shrink-0" />;
  if (["warning", "warn", "optional"].includes(status)) return <CircleAlert className="h-4 w-4 text-amber-500 shrink-0" />;
  if (["missing", "error", "failed", "fail"].includes(status)) return <CircleX className="h-4 w-4 text-red-500 shrink-0" />;
  return <HelpCircle className="h-4 w-4 text-muted shrink-0" />;
}

function Items({ items }: { items: HealthItem[] }) {
  if (!items?.length) return <p className="subtle">No checks reported.</p>;
  return (
    <ul className="divide-y divide-line">
      {items.map((i) => (
        <li key={i.id} className="py-2 flex gap-2 items-start">
          <ItemIcon status={i.status} />
          <div className="flex-1 min-w-0 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{i.name}</span>
              <span className="subtle">{i.status}</span>
            </div>
            {i.detail && <div className="text-xs text-muted break-words">{i.detail}</div>}
            {i.fix && !["ok", "passed"].includes(i.status) && (
              <div className="text-xs mt-1 rounded bg-panel2 px-2 py-1">
                <b>Fix:</b> <span className="font-mono break-words">{i.fix}</span>
              </div>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

export default function RepoHealth() {
  const qc = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: api.systemHealth });
  const engines = useQuery({ queryKey: ["engines"], queryFn: api.engines });
  const [busy, setBusy] = useState(false);
  const recheck = async () => {
    setBusy(true);
    try {
      await Promise.allSettled((engines.data || []).map((e) => api.checkEngine(e.id)));
    } finally {
      await Promise.all([health.refetch(), engines.refetch()]);
      setBusy(false);
    }
  };
  const byId = new Map((health.data?.engines || []).map((e) => [e.engine, e]));
  return (
    <div className="space-y-4">
      <PageHeader
        title="Repository health"
        subtitle="Tools, Claude Code and each engine repository, with fixes for anything missing."
        actions={
          <button className="btn" onClick={recheck} disabled={busy}>
            <RefreshCw className={cx("h-4 w-4", busy && "animate-spin")} /> Re-check
          </button>
        }
      />
      {health.error && <ErrorBox error={health.error} onRetry={() => health.refetch()} />}
      {health.isLoading ? (
        <Loading label="Checking tools and engines…" />
      ) : (
        health.data && (
          <div className="grid lg:grid-cols-2 gap-4">
            <Section title="Claude Code" actions={health.data.claude.simulated && <SimulatedBadge />}>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <ItemIcon status={health.data.claude.ok ? "ok" : "missing"} />
                <code className="font-mono break-all">{health.data.claude.bin}</code>
                <span className="text-muted break-words min-w-0">{health.data.claude.version || "version unknown"}</span>
              </div>
              {!health.data.claude.ok && <p className="text-xs mt-2">Install Claude Code and sign in (<code>claude</code> in a terminal), or set the binary path in Settings.</p>}
              {health.data.claude.simulated && <p className="text-xs text-orange-700 dark:text-orange-300 mt-2">This binary is not the real Claude CLI. Every run is labelled SIMULATED.</p>}
              <div className="mt-3"><ZeroCostBadge on={health.data.zero_cost_ready} /> <span className="subtle">{(health.data.engines_ready?.length ?? 0)} of {health.data.engines_total ?? health.data.engines.length} enabled engines are installed and ready to run at zero cost.{!health.data.zero_cost_ready && " Fix the items below to enable zero-cost production."}</span></div>
            </Section>
            <Section title="Tools">
              <Items items={health.data.tools} />
            </Section>
          </div>
        )
      )}

      {engines.error && <ErrorBox error={engines.error} onRetry={() => engines.refetch()} />}
      {(engines.data || []).map((e) => (
        <EngineCard key={e.id} engine={e} health={e.health || byId.get(e.id) || null} onChanged={() => { qc.invalidateQueries({ queryKey: ["engines"] }); qc.invalidateQueries({ queryKey: ["health"] }); }} />
      ))}

      <Onboard />
    </div>
  );
}

function EngineCard({ engine: e, health, onChanged }: { engine: EngineInfo; health: EngineHealth | null; onChanged: () => void }) {
  const [step, setStep] = useState<SetupStep | null>(null);
  const patch = useMutation({ mutationFn: (b: { enabled?: boolean; approved?: boolean }) => api.patchEngine(e.id, b), onSuccess: onChanged });
  const check = useMutation({ mutationFn: () => api.checkEngine(e.id), onSuccess: onChanged });
  const setup = useMutation({ mutationFn: (s: string) => api.setupEngine(e.id, s), onSuccess: () => { setStep(null); onChanged(); } });
  return (
    <Section
      title={
        <span className="flex flex-wrap items-center gap-2">
          {e.name} <span className="subtle font-mono">{e.id}</span>
          {health ? health.ok ? <Badge tone="green">healthy</Badge> : <Badge tone="red">issues</Badge> : <Badge tone="grey">not checked</Badge>}
          {health && (health.zero_cost_ready ? <Badge tone="green">zero-cost ready</Badge> : <Badge tone="amber">not zero-cost ready</Badge>)}
          {e.approved === false && <Badge tone="amber">awaiting approval</Badge>}
        </span>
      }
      actions={
        <div className="flex items-center gap-3">
          <Toggle checked={e.enabled} onChange={(v) => patch.mutate({ enabled: v })} label={e.enabled ? "Enabled" : "Disabled"} disabled={patch.isPending} />
          <button className="btn btn-sm" onClick={() => check.mutate()} disabled={check.isPending}>
            <RefreshCw className={cx("h-3.5 w-3.5", check.isPending && "animate-spin")} /> Check
          </button>
        </div>
      }
    >
      <p className="text-sm text-muted">{e.description}</p>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs mt-2">
        <span><span className="text-muted">Path:</span> <code className="font-mono">{e.path || "—"}</code></span>
        <span><span className="text-muted">Licence:</span> {e.license}</span>
        {health?.commit && <span className="inline-flex items-center gap-1"><GitBranch className="h-3 w-3" /> <code className="font-mono">{health.commit.slice(0, 10)}</code></span>}
        <span><span className="text-muted">Cost:</span> {e.cost?.zero_cost === "partial" ? "free on its zero-cost pipelines only" : e.cost?.zero_cost ? "free" : "may cost"}{e.cost?.notes ? ` — ${e.cost.notes}` : ""}</span>
      </div>
      {e.approved === false && (
        <div className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm flex flex-wrap items-center gap-2">
          <span className="flex-1">This repository was onboarded and its draft engine.yaml needs your review before any video can use it.</span>
          <button className="btn-primary btn-sm" onClick={() => window.confirm(`Approve ${e.name}? Claude will be allowed to run it for your videos.`) && patch.mutate({ approved: true })}>Approve engine</button>
        </div>
      )}
      {(patch.error || check.error) && <div className="mt-2"><ErrorBox error={patch.error || check.error} /></div>}
      <div className="grid lg:grid-cols-2 gap-4 mt-3">
        <div>
          <div className="label">Checks</div>
          <Items items={health?.items || []} />
        </div>
        <div>
          <div className="label">Setup steps</div>
          {e.setup_steps?.length ? (
            <ul className="space-y-1.5">
              {e.setup_steps.map((s) => (
                <li key={s.id} className="rounded-lg bg-panel2 p-2 flex items-center gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="text-sm">{s.title}</div>
                    <code className="text-[11px] font-mono text-muted block truncate">{s.command}</code>
                  </div>
                  <button className="btn btn-sm" onClick={() => setStep(s)}><Terminal className="h-3.5 w-3.5" /> Run…</button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="subtle">No documented setup steps.</p>
          )}
          {setup.data && <p className="text-xs mt-2 text-muted">Started run <code>{setup.data.id}</code> ({setup.data.state}). Re-check when it finishes.</p>}
        </div>
      </div>
      <div className="mt-3">
        <div className="label">Capabilities</div>
        <div className="flex flex-wrap gap-1">
          {Object.entries(e.capabilities || {}).map(([k, c]) => (
            <span key={k} title={c.detail} className={cx("text-[11px] rounded px-1.5 py-0.5", c.supported ? "bg-blue-500/10 text-blue-700 dark:text-blue-300" : "bg-panel2 text-muted line-through")}>
              {k.replace(/_/g, " ")}
            </span>
          ))}
        </div>
      </div>
      <Modal
        open={!!step}
        onClose={() => setStep(null)}
        title={`Run setup: ${step?.title ?? ""}`}
        footer={
          <>
            <button className="btn" onClick={() => setStep(null)}>Cancel</button>
            <button className="btn-primary" disabled={setup.isPending} onClick={() => step && setup.mutate(step.id)}>
              {setup.isPending ? "Starting…" : "I've read it — run this command"}
            </button>
          </>
        }
      >
        <p className="mb-2">SpaceStudio will run exactly this command. It is logged in the audit trail.</p>
        <pre className="rounded-lg bg-black/90 text-zinc-100 p-3 text-xs font-mono whitespace-pre-wrap break-all">{step?.command}</pre>
        {step?.cwd && <p className="text-xs text-muted mt-2">Working directory: <code className="font-mono">{step.cwd}</code></p>}
        <p className="text-xs text-muted mt-2">It may download packages or models from the internet.</p>
        {setup.error && <div className="mt-2"><ErrorBox error={setup.error} title="Setup failed to start" /></div>}
      </Modal>
    </Section>
  );
}

function Onboard() {
  const [url, setUrl] = useState("");
  const m = useMutation({ mutationFn: () => api.onboard(url) });
  return (
    <Section title="Add a repository">
      <ol className="text-sm text-muted list-decimal pl-5 space-y-1 mb-3">
        <li>Give a Git URL or a local path to another video engine repository.</li>
        <li>A Claude job reads it (read-only) and drafts an <code>engine.yaml</code>: capabilities, setup commands, cost and licence.</li>
        <li>The engine appears here as <b>awaiting approval</b>. Review its manifest, run its setup steps, then approve it.</li>
        <li>Only approved and enabled engines are offered to videos.</li>
      </ol>
      <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (url.trim()) m.mutate(); }}>
        <input className="input flex-1 min-w-[220px] font-mono" placeholder="https://github.com/owner/repo or /path/to/repo" value={url} onChange={(e) => setUrl(e.target.value)} />
        <button className="btn-primary" disabled={!url.trim() || m.isPending}>{m.isPending ? "Starting…" : "Start onboarding"}</button>
      </form>
      {m.data && <p className="text-sm mt-2 text-emerald-700 dark:text-emerald-300">Onboarding job <code>{m.data.job_id}</code> started. The engine will appear above when the draft is ready.</p>}
      {m.error && <p className="text-sm mt-2 text-red-600 dark:text-red-300">{explainError(m.error)}</p>}
    </Section>
  );
}
