import { useMemo, useState } from "react";
import { CircleDollarSign, Info, Search, ShieldCheck } from "lucide-react";
import type { EngineInfo, Presets, VideoConfig } from "../types";
import { optionOf, parseResolution } from "../lib/format";
import { FALLBACK_ASPECTS, FALLBACK_DURATIONS, isEngineUsable, likelyRoles, ROLE_CAPS, singularKey } from "../lib/config";
import { Badge, cx, Field, ListInput, Select, Toggle } from "./ui";

type SetCfg = (patch: Partial<VideoConfig>) => void;

const FALLBACK_STYLES = ["cinematic", "documentary", "whiteboard", "motion-graphics", "archival", "minimal"];
const FALLBACK_TONES = ["educational", "dramatic", "inspiring", "playful", "calm", "urgent"];
const FALLBACK_PLATFORMS = [
  { id: "youtube", name: "YouTube", aspect_ratio: "16:9" },
  { id: "shorts", name: "YouTube Shorts", aspect_ratio: "9:16" },
  { id: "tiktok", name: "TikTok", aspect_ratio: "9:16" },
  { id: "instagram", name: "Instagram", aspect_ratio: "4:5" },
];

function opts(list: unknown[] | undefined, fallback: string[]) {
  const l = Array.isArray(list) && list.length ? list : fallback;
  return l.map(optionOf).filter((o) => o.id);
}

export function CreativeSettings({ config, set, presets, engines }: { config: VideoConfig; set: SetCfg; presets?: Presets; engines: EngineInfo[] }) {
  const aspects = presets?.aspect_ratios?.length ? presets.aspect_ratios : FALLBACK_ASPECTS;
  const durations = presets?.durations?.length ? presets.durations : FALLBACK_DURATIONS;
  const platforms = presets?.platforms?.length ? presets.platforms : FALLBACK_PLATFORMS;
  const voiceEngines = engines.filter((e) => e.capabilities?.narration?.supported && isEngineUsable(e, { ...config, engines: [] }));
  const paidEngines = engines.filter((e) => (e.cost?.paid_env || []).length > 0);

  return (
    <div className="space-y-6">
      {/* Zero-cost first: prominent */}
      <div className={cx("rounded-xl border p-4", config.zero_cost ? "border-emerald-500/40 bg-emerald-500/10" : "border-amber-500/40 bg-amber-500/10")}>
        <Toggle
          checked={config.zero_cost}
          onChange={(v) => set(v ? { zero_cost: true, approved_paid_providers: [] } : { zero_cost: false })}
          label={
            <span className="inline-flex items-center gap-1.5">
              <ShieldCheck className="h-4 w-4" /> Zero-cost mode {config.zero_cost ? "ON" : "OFF"}
            </span>
          }
          description="When on, SpaceStudio strips every paid provider key (OpenAI, ElevenLabs, Runway, …) from each engine's environment and fails any stage that reports spend. Only free/local tools and public-domain or CC media are used. Claude Code usage itself is billed to your Claude plan as normal."
        />
        {!config.zero_cost && <PaidProviders config={config} set={set} engines={paidEngines} />}
      </div>

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <Field label="Visual style">
          <Select value={config.visual_style} onChange={(v) => set({ visual_style: v })} options={opts(presets?.visual_styles, FALLBACK_STYLES)} />
        </Field>
        <Field label="Tone">
          <Select value={config.tone} onChange={(v) => set({ tone: v })} options={opts(presets?.tones, FALLBACK_TONES)} />
        </Field>
        <Field label="Duration">
          <div className="flex gap-2">
            <Select
              className="flex-1"
              value={durations.find((d) => d.seconds === config.duration_seconds)?.id ?? ""}
              onChange={(v) => {
                const d = durations.find((x) => x.id === v);
                if (d) set({ duration_seconds: d.seconds });
              }}
              options={[{ id: "", name: "Custom" }, ...durations.map((d) => ({ id: d.id, name: d.name }))]}
            />
            <input
              aria-label="Duration in seconds"
              type="number"
              min={5}
              className="input w-24"
              value={config.duration_seconds}
              onChange={(e) => set({ duration_seconds: Number(e.target.value) || 0 })}
            />
          </div>
        </Field>
        <Field label="Language">
          <input className="input" value={config.language} onChange={(e) => set({ language: e.target.value })} placeholder="en" />
        </Field>
        <Field label="Narration language">
          <input className="input" value={config.narration_language} onChange={(e) => set({ narration_language: e.target.value })} />
        </Field>
        <Field label="Target audience">
          <input className="input" value={config.target_audience} onChange={(e) => set({ target_audience: e.target.value })} />
        </Field>
      </div>

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <Field label="Aspect ratio">
          <div className="flex flex-wrap gap-1.5">
            {aspects.map((a) => (
              <button
                type="button"
                key={a.id}
                onClick={() => set({ aspect_ratio: a.id as VideoConfig["aspect_ratio"], resolution: a.resolution })}
                className={cx("btn btn-sm", config.aspect_ratio === a.id && "ring-2 ring-accent")}
              >
                {a.id}
              </button>
            ))}
          </div>
        </Field>
        <Field label="Resolution" hint={parseResolution(config.resolution) ? undefined : "Use WIDTHxHEIGHT, e.g. 1920x1080"}>
          <input className="input font-mono" value={config.resolution} onChange={(e) => set({ resolution: e.target.value })} />
        </Field>
        <Field label="Frame rate">
          <Select value={String(config.fps)} onChange={(v) => set({ fps: Number(v) })} options={["24", "25", "30", "60"].map((x) => ({ id: x, name: `${x} fps` }))} />
        </Field>
        <Field label="Platforms" className="sm:col-span-2 lg:col-span-3">
          <div className="flex flex-wrap gap-1.5">
            {platforms.map((p) => {
              const on = config.platforms.includes(p.id);
              return (
                <button
                  type="button"
                  key={p.id}
                  className={cx("btn btn-sm", on && "ring-2 ring-accent")}
                  onClick={() => set({ platforms: on ? config.platforms.filter((x) => x !== p.id) : [...config.platforms, p.id] })}
                >
                  {p.name} <span className="text-muted">{p.aspect_ratio}</span>
                </button>
              );
            })}
          </div>
        </Field>
      </div>

      <div className="grid sm:grid-cols-2 gap-4">
        <Field label="Voiceover">
          <div className="flex gap-1.5">
            {(["generated", "user", "none"] as const).map((v) => (
              <button type="button" key={v} className={cx("btn btn-sm flex-1", config.voiceover === v && "ring-2 ring-accent")} onClick={() => set({ voiceover: v })}>
                {v === "generated" ? "Generated (TTS)" : v === "user" ? "My recording" : "None"}
              </button>
            ))}
          </div>
        </Field>
        {config.voiceover === "generated" && (
          <Field label="Voice engine" hint="Auto picks the best enabled free TTS (lemo Kokoro, OpenMontage Piper, cinematic say/espeak).">
            <Select
              value={config.voice_engine ?? ""}
              onChange={(v) => set({ voice_engine: v || null })}
              options={[{ id: "", name: "Auto" }, ...voiceEngines.map((e) => ({ id: e.id, name: e.name }))]}
            />
          </Field>
        )}
        {config.voiceover === "user" && (
          <Field label="Your voice file" hint="Workspace-relative path under assets/user/ (upload it to the video workspace after creating).">
            <input className="input font-mono" value={config.user_voice_file ?? ""} placeholder="assets/user/voice.wav" onChange={(e) => set({ user_voice_file: e.target.value || null })} />
          </Field>
        )}
      </div>

      <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <Toggle checked={config.captions} onChange={(v) => set({ captions: v, burn_captions: v ? config.burn_captions : false })} label="Captions" description="Write SRT subtitles from narration timing." />
        <Toggle checked={config.burn_captions} disabled={!config.captions} onChange={(v) => set({ burn_captions: v })} label="Burn in captions" description="Render captions into the picture." />
        <Toggle checked={config.music} onChange={(v) => set({ music: v })} label="Music" description="Free/CC music bed, ducked under narration." />
        <Toggle checked={config.sfx} onChange={(v) => set({ sfx: v })} label="Sound effects" />
        {config.captions && (
          <Field label="Subtitle languages" className="sm:col-span-2">
            <ListInput value={config.subtitle_languages} onChange={(v) => set({ subtitle_languages: v })} placeholder="en, es" />
          </Field>
        )}
      </div>

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <Field label="Pacing">
          <Select value={config.pacing} onChange={(v) => set({ pacing: v as VideoConfig["pacing"] })} options={["slow", "medium", "fast"].map((x) => ({ id: x, name: x }))} />
        </Field>
        <Field label="Scene density">
          <Select value={config.scene_density} onChange={(v) => set({ scene_density: v as VideoConfig["scene_density"] })} options={["low", "medium", "high"].map((x) => ({ id: x, name: x }))} />
        </Field>
        <Field label="Approval mode" hint="Engine-required gates always stop for approval.">
          <Select
            value={config.approval_mode}
            onChange={(v) => set({ approval_mode: v as VideoConfig["approval_mode"] })}
            options={[
              { id: "manual", name: "Manual — approve every gate" },
              { id: "auto_to_preview", name: "Auto until first preview" },
              { id: "auto_low_risk", name: "Auto-approve low-risk stages" },
            ]}
          />
        </Field>
        {config.approval_mode === "auto_low_risk" && (
          <Field label="Auto-approved stages" className="sm:col-span-2 lg:col-span-3">
            <ListInput value={config.auto_stages} onChange={(v) => set({ auto_stages: v })} placeholder="concept, asset_plan" />
          </Field>
        )}
      </div>

      <div className="grid sm:grid-cols-2 gap-4">
        <Field label="Branding" hint="Workspace-relative logo / intro / outro paths, and brand colours.">
          <div className="grid grid-cols-2 gap-2">
            {(["logo", "intro", "outro", "colors"] as const).map((k) => (
              <input
                key={k}
                className="input"
                placeholder={k}
                value={(config.branding?.[k] as string) ?? ""}
                onChange={(e) => {
                  const b = { ...(config.branding || {}) };
                  if (e.target.value) b[k] = e.target.value;
                  else delete b[k];
                  set({ branding: b });
                }}
              />
            ))}
          </div>
        </Field>
        <Field label="References (URLs, comma-separated)">
          <ListInput value={config.references} onChange={(v) => set({ references: v })} placeholder="https://…" />
        </Field>
        <Field label="Media budget (USD)" hint={config.zero_cost ? "Ignored in zero-cost mode." : "Maximum spend on approved paid providers."}>
          <input type="number" min={0} step="0.5" disabled={config.zero_cost} className="input" value={config.budget_usd} onChange={(e) => set({ budget_usd: Number(e.target.value) || 0 })} />
        </Field>
        <Field label="Claude budget cap (USD, optional)" hint="Passed as --max-budget-usd per stage.">
          <input
            type="number"
            min={0}
            step="0.5"
            className="input"
            value={config.max_claude_budget_usd ?? ""}
            onChange={(e) => set({ max_claude_budget_usd: e.target.value === "" ? null : Number(e.target.value) })}
          />
        </Field>
        <Field label="Claude model (optional)">
          <input className="input" placeholder="default" value={config.claude_model ?? ""} onChange={(e) => set({ claude_model: e.target.value || null })} />
        </Field>
        <Field label="Claude effort">
          <Select
            value={config.claude_effort ?? ""}
            onChange={(v) => set({ claude_effort: v || null })}
            options={[{ id: "", name: "default" }, ...["low", "medium", "high", "xhigh", "max"].map((x) => ({ id: x, name: x }))]}
          />
        </Field>
      </div>
    </div>
  );
}

function PaidProviders({ config, set, engines }: { config: VideoConfig; set: SetCfg; engines: EngineInfo[] }) {
  const [confirmed, setConfirmed] = useState(config.approved_paid_providers.length > 0);
  const providers = Array.from(new Set(engines.flatMap((e) => e.cost?.paid_env || [])));
  return (
    <div className="mt-4 space-y-3 text-sm">
      <div className="flex gap-2 items-start text-amber-800 dark:text-amber-200">
        <CircleDollarSign className="h-4 w-4 mt-0.5 shrink-0" />
        <span>Paid providers can cost real money. Only providers you tick below receive their API key, and only for this video.</span>
      </div>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={confirmed} onChange={(e) => { setConfirmed(e.target.checked); if (!e.target.checked) set({ approved_paid_providers: [] }); }} />
        I understand that enabling paid providers may incur charges on my own accounts.
      </label>
      {confirmed && (
        <div className="flex flex-wrap gap-2">
          {providers.length ? (
            providers.map((p) => {
              const on = config.approved_paid_providers.includes(p);
              return (
                <label key={p} className={cx("btn btn-sm font-mono", on && "ring-2 ring-amber-500")}>
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={on}
                    onChange={() => set({ approved_paid_providers: on ? config.approved_paid_providers.filter((x) => x !== p) : [...config.approved_paid_providers, p] })}
                  />
                  {p}
                </label>
              );
            })
          ) : (
            <span className="subtle">No engine declares paid providers.</span>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------- engines ----------------

export function EngineSettings({
  config,
  set,
  mode,
  setMode,
  primary,
  setPrimary,
  engines,
  presets,
  supportedByType,
}: {
  config: VideoConfig;
  set: SetCfg;
  mode: "collaborative" | "native";
  setMode: (m: "collaborative" | "native") => void;
  primary: string | null;
  setPrimary: (e: string | null) => void;
  engines: EngineInfo[];
  presets?: Presets;
  supportedByType?: string[];
}) {
  const roles = likelyRoles(engines, config);
  const toggleExclude = (id: string) => {
    const ex = config.excluded_engines.includes(id);
    set({ excluded_engines: ex ? config.excluded_engines.filter((x) => x !== id) : [...config.excluded_engines, id] });
  };
  return (
    <div className="space-y-5">
      <div className="grid sm:grid-cols-2 gap-3">
        <button type="button" onClick={() => setMode("collaborative")} className={cx("card p-4 text-left", mode === "collaborative" && "ring-2 ring-accent")}>
          <div className="font-medium">Collaborative <Badge tone="green">default</Badge></div>
          <p className="text-xs text-muted mt-1">
            Every stage goes to whichever engine (or SpaceStudio) is best for it, joined by neutral interchange files. One film can use cinematic for archival
            boards, lemo for whiteboard explainers and OpenMontage for footage — each segment's engine is chosen at the storyboard and shown there.
          </p>
        </button>
        <button type="button" onClick={() => setMode("native")} className={cx("card p-4 text-left", mode === "native" && "ring-2 ring-accent")}>
          <div className="font-medium">Native single-engine</div>
          <p className="text-xs text-muted mt-1">One engine runs its own end-to-end pipeline and gates; SpaceStudio adds setup, QA and export around it.</p>
        </button>
      </div>

      {mode === "collaborative" ? (
        <div className="card p-4">
          <div className="text-sm font-medium mb-2">Likely stage owners</div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {ROLE_CAPS.map((r) => (
              <div key={r.role} className="rounded-lg bg-panel2 p-3">
                <div className="subtle">{r.label}</div>
                <div className="flex flex-wrap gap-1 mt-1">
                  {(roles[r.role] || []).length ? (
                    roles[r.role].map((e) => (
                      <Badge key={e} tone={e === "spacestudio" ? "slate" : "blue"}>
                        {e === "spacestudio" ? "SpaceStudio (Claude)" : e}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-xs text-red-600 dark:text-red-300">{r.role === "narration" && config.voiceover === "none" ? "no narration" : "no usable engine"}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
          <p className="subtle mt-2">Script, storyboard, preview, mix, captions, QA and export are handled by SpaceStudio itself.</p>
        </div>
      ) : (
        <Field label="Engine">
          <Select
            value={primary ?? ""}
            onChange={(v) => setPrimary(v || null)}
            options={[{ id: "", name: "Choose an engine…" }, ...engines.filter((e) => isEngineUsable(e, { ...config, engines: [], excluded_engines: [] })).map((e) => ({ id: e.id, name: e.name }))]}
          />
        </Field>
      )}

      <div className="space-y-3">
        {engines.map((e) => {
          const excluded = config.excluded_engines.includes(e.id);
          const paidOnly = config.zero_cost && e.cost?.zero_cost === false;
          const disabled = !e.enabled || e.approved === false;
          const caps = Object.entries(e.capabilities || {}).filter(([, c]) => c?.supported);
          const fitsType = supportedByType?.includes(e.id);
          const active = mode === "native" ? primary === e.id : !excluded && !disabled && !paidOnly;
          return (
            <div key={e.id} className={cx("card p-4", !active && "opacity-70")}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{e.name}</span>
                <span className="subtle font-mono">{e.id}</span>
                {e.cost?.zero_cost ? <Badge tone="green">free</Badge> : <Badge tone="amber">may cost</Badge>}
                {fitsType && <Badge tone="violet">fits this video type</Badge>}
                {disabled && <Badge tone="muted">{e.approved === false ? "not approved" : "disabled"}</Badge>}
                {e.health && !e.health.ok && <Badge tone="red">health issues</Badge>}
                {mode === "collaborative" && !disabled && (
                  <div className="ml-auto">
                    <Toggle checked={!excluded} onChange={() => toggleExclude(e.id)} label={excluded ? "Excluded" : "Included"} disabled={paidOnly} />
                  </div>
                )}
              </div>
              <p className="text-xs text-muted mt-1">{e.description}</p>
              {paidOnly && <p className="text-xs text-amber-700 dark:text-amber-300 mt-1">Hidden in zero-cost mode: this engine is not zero-cost.</p>}
              {caps.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {caps.map(([k, c]) => (
                    <span key={k} title={c.detail} className="text-[11px] rounded bg-panel2 px-1.5 py-0.5 text-muted">
                      {k.replace(/_/g, " ")}
                    </span>
                  ))}
                </div>
              )}
              {active && <EngineOptionsPicker engine={e} config={config} set={set} presetOptions={presets?.engine_options?.[e.id]} />}
            </div>
          );
        })}
        {!engines.length && <p className="subtle">No engines registered. Check Repository Health.</p>}
      </div>
    </div>
  );
}

type Item = { id: string; name: string; description?: string; category?: string; zero_cost?: boolean };

function toItems(list: unknown[]): Item[] {
  return list
    .map((x) => {
      const o = optionOf(x);
      const r = o.raw;
      return {
        id: o.id,
        name: o.name,
        description: o.description,
        category: (r.category_en ?? r.category) as string | undefined,
        zero_cost: r.zero_cost === undefined ? undefined : Boolean(r.zero_cost),
      };
    })
    .filter((i) => i.id);
}

function EngineOptionsPicker({ engine, config, set, presetOptions }: { engine: EngineInfo; config: VideoConfig; set: SetCfg; presetOptions?: Record<string, unknown> }) {
  const source = (presetOptions && Object.keys(presetOptions).length ? presetOptions : engine.options) || {};
  const groups = Object.entries(source).filter(([, v]) => Array.isArray(v) && (v as unknown[]).length) as [string, unknown[]][];
  if (!groups.length) return null;
  const current = config.engine_options?.[engine.id] || {};
  const setOpt = (k: string, v: string) => {
    const next = { ...current };
    if (v) next[k] = v;
    else delete next[k];
    set({ engine_options: { ...config.engine_options, [engine.id]: next } });
  };
  return (
    <div className="mt-3 space-y-3 border-t border-line pt-3">
      {groups.map(([k, list]) => {
        const key = singularKey(k);
        const items = toItems(list).filter((i) => !(config.zero_cost && i.zero_cost === false));
        const hidden = toItems(list).length - items.length;
        return items.length > 12 || items.some((i) => i.category) ? (
          <SearchPicker key={k} label={key} items={items} value={String(current[key] ?? "")} onChange={(v) => setOpt(key, v)} hidden={hidden} />
        ) : (
          <Field key={k} label={`${engine.name} ${key}`} hint={hidden ? `${hidden} paid option(s) hidden in zero-cost mode.` : undefined}>
            <Select value={String(current[key] ?? "")} onChange={(v) => setOpt(key, v)} options={[{ id: "", name: "Auto (let the storyboard choose)" }, ...items]} />
          </Field>
        );
      })}
    </div>
  );
}

function SearchPicker({ label, items, value, onChange, hidden }: { label: string; items: Item[]; value: string; onChange: (v: string) => void; hidden: number }) {
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const cats = useMemo(() => Array.from(new Set(items.map((i) => i.category).filter(Boolean))) as string[], [items]);
  const shown = items.filter(
    (i) => (!cat || i.category === cat) && (!q || `${i.name} ${i.id} ${i.description ?? ""}`.toLowerCase().includes(q.toLowerCase())),
  );
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <span className="label mb-0 capitalize">{label}</span>
        <span className="subtle">{value ? `selected: ${value}` : "auto"}</span>
        {value && (
          <button type="button" className="btn-ghost btn-sm" onClick={() => onChange("")}>
            clear
          </button>
        )}
        {hidden > 0 && <span className="subtle">· {hidden} paid hidden</span>}
      </div>
      <div className="flex flex-wrap gap-2 mb-2">
        <div className="relative flex-1 min-w-[160px]">
          <Search className="h-3.5 w-3.5 absolute left-2.5 top-2.5 text-muted" />
          <input className="input pl-8" placeholder={`Search ${items.length} ${label}s`} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {cats.length > 0 && (
          <Select className="w-auto" value={cat} onChange={setCat} options={[{ id: "", name: "All categories" }, ...cats.map((c) => ({ id: c, name: c }))]} />
        )}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-1.5 max-h-56 overflow-y-auto pr-1">
        {shown.map((i) => (
          <button
            type="button"
            key={i.id}
            title={i.description}
            onClick={() => onChange(i.id)}
            className={cx("text-left rounded-lg border border-line px-2 py-1.5 text-xs hover:bg-panel2", value === i.id && "ring-2 ring-accent")}
          >
            <div className="font-medium truncate">{i.name}</div>
            {i.category && <div className="text-muted truncate">{i.category}</div>}
          </button>
        ))}
        {!shown.length && (
          <div className="subtle col-span-full flex items-center gap-1">
            <Info className="h-3 w-3" /> No match.
          </div>
        )}
      </div>
    </div>
  );
}
