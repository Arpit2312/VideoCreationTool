import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Plus, Save, Trash2 } from "lucide-react";
import { api } from "../api";
import type { Settings, VideoConfig } from "../types";
import { DEFAULT_CONFIG } from "../lib/config";
import { CreativeSettings } from "../components/ConfigForm";
import { ErrorBox, Field, ListInput, Loading, PageHeader, Section, Select, Toggle } from "../components/ui";

function KVEditor({ value, onChange, keyPlaceholder, valuePlaceholder }: { value: Record<string, string>; onChange: (v: Record<string, string>) => void; keyPlaceholder: string; valuePlaceholder: string }) {
  const entries = Object.entries(value || {});
  const update = (i: number, k: string, v: string) => {
    const next = entries.map((e, j) => (j === i ? [k, v] : e));
    onChange(Object.fromEntries(next));
  };
  return (
    <div className="space-y-1.5">
      {entries.map(([k, v], i) => (
        <div key={i} className="flex gap-2">
          <input className="input font-mono w-1/3" placeholder={keyPlaceholder} value={k} onChange={(e) => update(i, e.target.value, v)} />
          <input className="input font-mono flex-1" placeholder={valuePlaceholder} value={v} onChange={(e) => update(i, k, e.target.value)} />
          <button className="btn-ghost btn-sm" aria-label="Remove" onClick={() => onChange(Object.fromEntries(entries.filter((_, j) => j !== i)))}>
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      <button className="btn btn-sm" onClick={() => onChange({ ...value, "": "" })}>
        <Plus className="h-3.5 w-3.5" /> Add
      </button>
    </div>
  );
}

export default function SettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const engines = useQuery({ queryKey: ["engines"], queryFn: api.engines, staleTime: 60_000 });
  const presets = useQuery({ queryKey: ["presets"], queryFn: api.presets, staleTime: 300_000 });
  const [s, setS] = useState<Settings | null>(null);
  useEffect(() => {
    if (q.data) setS(q.data);
  }, [q.data]);
  const save = useMutation({
    mutationFn: (body: Settings) => {
      const clean = { ...body, engine_paths: dropEmpty(body.engine_paths), extra_env: dropEmpty(body.extra_env) };
      delete (clean as Record<string, unknown>).projects_dir;
      return api.saveSettings(clean);
    },
    onSuccess: (d) => {
      qc.setQueryData(["settings"], d);
      qc.invalidateQueries({ queryKey: ["health"] });
    },
  });
  if (q.isLoading || !s) return q.error ? <ErrorBox error={q.error} onRetry={() => q.refetch()} /> : <Loading />;
  const set = (p: Partial<Settings>) => setS((c) => (c ? { ...c, ...p } : c));
  const num = (k: keyof Settings, step = 1) => (
    <input type="number" step={step} className="input" value={Number(s[k] ?? 0)} onChange={(e) => set({ [k]: Number(e.target.value) } as Partial<Settings>)} />
  );
  const txt = (k: keyof Settings, mono = true) => (
    <input className={mono ? "input font-mono" : "input"} value={String(s[k] ?? "")} onChange={(e) => set({ [k]: e.target.value } as Partial<Settings>)} />
  );
  const dvc: VideoConfig = { ...DEFAULT_CONFIG, ...(s.default_video_config || {}) };
  const dirty = JSON.stringify(s) !== JSON.stringify(q.data);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Settings"
        subtitle="Saved to <data_dir>/settings.json. Environment variables (SPACESTUDIO_*) override these values."
        actions={
          <button className="btn-primary" disabled={!dirty || save.isPending} onClick={() => save.mutate(s)}>
            <Save className="h-4 w-4" /> {save.isPending ? "Saving…" : dirty ? "Save changes" : "Saved"}
          </button>
        }
      />
      {save.error && <ErrorBox error={save.error} title="Could not save settings" />}

      <Section title="Identity & paths">
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="App name">{txt("app_name", false)}</Field>
          <Field label="Data directory" hint="Restart the backend after changing.">{txt("data_dir")}</Field>
          <Field label="Engines directory" hint="Where the engine repositories are cloned.">{txt("engines_dir")}</Field>
          <Field label="Database URL">{txt("db_url")}</Field>
        </div>
        <div className="mt-3">
          <Field label="Per-engine repository paths (override engines directory)">
            <div className="space-y-1.5">
              {(engines.data || []).map((e) => (
                <div key={e.id} className="flex gap-2 items-center">
                  <span className="w-40 text-xs font-mono shrink-0">{e.id}</span>
                  <input
                    className="input font-mono"
                    placeholder={e.path || "default"}
                    value={s.engine_paths?.[e.id] ?? ""}
                    onChange={(ev) => set({ engine_paths: { ...(s.engine_paths || {}), [e.id]: ev.target.value } })}
                  />
                </div>
              ))}
              {!engines.data?.length && <p className="subtle">No engines loaded.</p>}
            </div>
          </Field>
        </div>
      </Section>

      <Section title="Binaries">
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <Field label="Claude binary" hint="Anything other than the real `claude` CLI is shown as SIMULATED.">{txt("claude_bin")}</Field>
          <Field label="Claude permission mode">
            <Select
              value={s.claude_permission_mode}
              onChange={(v) => set({ claude_permission_mode: v })}
              options={["acceptEdits", "default", "plan", "bypassPermissions", "dontAsk", "auto"].map((x) => ({ id: x, name: x }))}
            />
          </Field>
          <Field label="Python">{txt("python_bin")}</Field>
          <Field label="ffmpeg">{txt("ffmpeg")}</Field>
          <Field label="ffprobe">{txt("ffprobe")}</Field>
        </div>
        {s.claude_permission_mode === "bypassPermissions" && (
          <p className="text-xs text-amber-700 dark:text-amber-300 mt-2 flex gap-1 items-center">
            <AlertTriangle className="h-3 w-3" /> bypassPermissions lets Claude run any tool without asking. Use only in a sandbox.
          </p>
        )}
      </Section>

      <Section title="Concurrency & limits">
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <Field label="Max Claude sessions">{num("max_claude_sessions")}</Field>
          <Field label="Max render jobs">{num("max_render_jobs")}</Field>
          <Field label="Render workers (per job)">{num("render_workers")}</Field>
          <Field label="Min free RAM (GB)" hint="Stages wait while free RAM is below this.">{num("min_free_ram_gb", 0.5)}</Field>
          <Field label="Stage timeout (minutes)">{num("stage_timeout_minutes")}</Field>
        </div>
      </Section>

      <Section title="Cost & environment">
        <Toggle
          checked={s.zero_cost_default !== false}
          onChange={(v) => set({ zero_cost_default: v })}
          label="Zero-cost mode on by default for new videos"
          description="Paid provider keys are stripped from engine environments unless a video explicitly approves a provider."
        />
        <div className="grid sm:grid-cols-2 gap-3 mt-3">
          <Field label="Allowed free keys" hint="Free-tier API keys passed even in zero-cost mode, e.g. PEXELS_API_KEY, PIXABAY_API_KEY. Values come from your shell environment.">
            <ListInput value={s.allowed_free_keys || []} onChange={(v) => set({ allowed_free_keys: v })} placeholder="PEXELS_API_KEY" />
          </Field>
        </div>
        <div className="mt-3">
          <Field label="Extra environment (non-secret)">
            <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-xs mb-2 flex gap-1.5">
              <AlertTriangle className="h-3.5 w-3.5 text-amber-500 shrink-0" /> Stored in plain text in settings.json. Do not put API keys or passwords here — export them in your shell instead.
            </div>
            <KVEditor value={s.extra_env || {}} onChange={(v) => set({ extra_env: v })} keyPlaceholder="NAME" valuePlaceholder="value" />
          </Field>
        </div>
      </Section>

      <Section title="Default video settings">
        <CreativeSettings
          config={dvc}
          set={(p) => set({ default_video_config: { ...(s.default_video_config || {}), ...p } })}
          presets={presets.data}
          engines={engines.data || []}
        />
      </Section>
    </div>
  );
}

function dropEmpty(o?: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(o || {}).filter(([k, v]) => k.trim() && v !== ""));
}
