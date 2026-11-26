import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, Check, Lightbulb, Rocket, Sparkles } from "lucide-react";
import { api } from "../api";
import type { VideoConfig, VideoTypePreset } from "../types";
import { DEFAULT_CONFIG, isEngineUsable, likelyRoles } from "../lib/config";
import { CreativeSettings, EngineSettings } from "../components/ConfigForm";
import { Badge, cx, ErrorBox, Field, Loading, PageHeader, Section, Select, ZeroCostBadge } from "../components/ui";

const STEPS = ["Idea", "Video type", "Creative settings", "Engines", "Review"];
type IdeaMode = "single" | "multiple" | "topic" | "brief" | "references";

export interface IdeaDraft {
  title: string;
  idea: string;
}

export function titleFromIdea(idea: string): string {
  const first = idea.split(/[\n.!?]/)[0].trim();
  return first.length > 70 ? first.slice(0, 67).trimEnd() + "…" : first || "Untitled video";
}

export default function NewVideo() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [step, setStep] = useState(0);
  const presets = useQuery({ queryKey: ["presets"], queryFn: api.presets, staleTime: 300_000 });
  const engines = useQuery({ queryKey: ["engines"], queryFn: api.engines, staleTime: 60_000 });
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings, staleTime: 60_000 });

  const [ideaMode, setIdeaMode] = useState<IdeaMode>("single");
  const [title, setTitle] = useState("");
  const [idea, setIdea] = useState("");
  const [multi, setMulti] = useState("");
  const [topic, setTopic] = useState("");
  const [genCount, setGenCount] = useState(5);
  const [generated, setGenerated] = useState<(IdeaDraft & { picked: boolean })[]>([]);
  const [projectId, setProjectId] = useState<string>(params.get("project") || "");
  const [config, setConfig] = useState<VideoConfig>(DEFAULT_CONFIG);
  const [mode, setMode] = useState<"collaborative" | "native">("collaborative");
  const [primary, setPrimary] = useState<string | null>(null);
  const [startNow, setStartNow] = useState(true);
  const [initialised, setInitialised] = useState(false);

  // apply settings defaults once
  useEffect(() => {
    if (initialised || !settings.data) return;
    setConfig((c) => ({ ...c, ...(settings.data.default_video_config || {}), zero_cost: settings.data.zero_cost_default !== false }));
    setInitialised(true);
  }, [settings.data, initialised]);
  useEffect(() => {
    if (!projectId && projects.data?.length) setProjectId(projects.data[0].id);
  }, [projects.data, projectId]);

  const set = (p: Partial<VideoConfig>) => setConfig((c) => ({ ...c, ...p }));
  const types = presets.data?.video_types || [];
  const selectedType = types.find((t) => t.id === config.video_type);

  const ideas: IdeaDraft[] = useMemo(() => {
    switch (ideaMode) {
      case "single":
      case "brief":
      case "references":
        return idea.trim() ? [{ title: title.trim() || titleFromIdea(idea), idea: idea.trim() }] : [];
      case "multiple":
        return multi
          .split("\n")
          .map((l) => l.trim())
          .filter(Boolean)
          .map((l) => {
            const [t, ...rest] = l.split(" | ");
            return rest.length ? { title: t.trim(), idea: rest.join(" | ").trim() } : { title: titleFromIdea(l), idea: l };
          });
      case "topic":
        return generated.filter((g) => g.picked).map(({ title, idea }) => ({ title, idea }));
    }
  }, [ideaMode, idea, title, multi, generated]);

  const gen = useMutation({
    mutationFn: () => api.generateIdeas({ topic, count: genCount, video_type: config.video_type }),
    onSuccess: (r) => setGenerated((r.ideas || []).map((i) => ({ ...i, picked: true }))),
  });

  const create = useMutation({
    mutationFn: async () => {
      let pid = projectId;
      if (!pid) pid = (await api.createProject({ name: "My videos" })).id;
      const cfg: Partial<VideoConfig> = { ...config };
      const made = [];
      for (const it of ideas) {
        const v = await api.createVideo({ project_id: pid, title: it.title, idea: it.idea, config: cfg, mode, primary_engine: mode === "native" ? primary : null });
        if (startNow) await api.videoAction(v.id, "start");
        made.push(v);
      }
      return made;
    },
    onSuccess: (made) => {
      if (made.length === 1) nav(`/videos/${made[0].id}`);
      else nav("/jobs");
    },
  });

  const engineList = engines.data || [];
  const roles = likelyRoles(engineList, config);
  const canNext = [
    ideas.length > 0,
    true,
    true,
    mode === "collaborative" ? (roles.segments || []).length > 0 : !!primary,
    true,
  ];

  const applyType = (t: VideoTypePreset) => {
    setConfig((c) => ({ ...c, ...(t.defaults || {}), video_type: t.id, zero_cost: c.zero_cost, brief: c.brief, references: c.references }));
  };

  return (
    <div>
      <PageHeader title="New video" subtitle="A guided brief. Nothing runs until you press Create." actions={<ZeroCostBadge on={config.zero_cost} />} />
      <ol className="flex gap-1 overflow-x-auto mb-5">
        {STEPS.map((s, i) => (
          <li key={s} className="flex-1 min-w-[110px]">
            <button
              type="button"
              disabled={i > step && !canNext.slice(0, i).every(Boolean)}
              onClick={() => setStep(i)}
              className={cx(
                "w-full text-left rounded-lg border px-3 py-2 text-xs transition",
                i === step ? "border-accent bg-accent/10" : "border-line hover:bg-panel2",
                "disabled:opacity-40",
              )}
            >
              <span className="text-muted">Step {i + 1}</span>
              <div className="font-medium text-sm flex items-center gap-1">
                {i < step && <Check className="h-3.5 w-3.5 text-emerald-500" />} {s}
              </div>
            </button>
          </li>
        ))}
      </ol>

      {(presets.error || engines.error) && <ErrorBox error={presets.error || engines.error} title="Could not load presets or engines" onRetry={() => { presets.refetch(); engines.refetch(); }} />}

      {step === 0 && (
        <Section title="What is the video about?">
          <div className="flex flex-wrap gap-1.5 mb-4">
            {(
              [
                ["single", "Single idea"],
                ["multiple", "Multiple ideas"],
                ["topic", "Topic → ideas"],
                ["brief", "Detailed brief"],
                ["references", "Reference URLs"],
              ] as [IdeaMode, string][]
            ).map(([id, name]) => (
              <button key={id} type="button" className={cx("btn btn-sm", ideaMode === id && "ring-2 ring-accent")} onClick={() => setIdeaMode(id)}>
                {name}
              </button>
            ))}
          </div>
          {(ideaMode === "single" || ideaMode === "brief" || ideaMode === "references") && (
            <div className="space-y-3">
              <Field label="Title (optional)">
                <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={idea ? titleFromIdea(idea) : "e.g. How the Apollo guidance computer worked"} />
              </Field>
              <Field label={ideaMode === "brief" ? "Short idea / logline" : "Idea"}>
                <textarea className="input" rows={3} value={idea} onChange={(e) => setIdea(e.target.value)} placeholder="One or two sentences describing the film." />
              </Field>
              {ideaMode === "brief" && (
                <Field label="Detailed creative brief" hint="Structure, must-include facts, look and feel, do/don't. Saved as config.brief.">
                  <textarea className="input" rows={8} value={config.brief} onChange={(e) => set({ brief: e.target.value })} />
                </Field>
              )}
              {ideaMode === "references" && (
                <Field label="Reference URLs (one per line)" hint="Claude's research stage reads these first.">
                  <textarea
                    className="input font-mono"
                    rows={5}
                    value={config.references.join("\n")}
                    onChange={(e) => set({ references: e.target.value.split("\n").map((x) => x.trim()).filter(Boolean) })}
                  />
                </Field>
              )}
            </div>
          )}
          {ideaMode === "multiple" && (
            <Field label="One idea per line" hint="Optional: “Title | idea”. Each line becomes its own video with the same settings. For per-row overrides use Batch Studio.">
              <textarea className="input" rows={8} value={multi} onChange={(e) => setMulti(e.target.value)} />
            </Field>
          )}
          {ideaMode === "topic" && (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-2 items-end">
                <Field label="Topic" className="flex-1 min-w-[200px]">
                  <input className="input" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. forgotten inventions of the 1800s" />
                </Field>
                <Field label="How many">
                  <input type="number" min={1} max={20} className="input w-20" value={genCount} onChange={(e) => setGenCount(Number(e.target.value) || 1)} />
                </Field>
                <button className="btn-primary" type="button" disabled={!topic.trim() || gen.isPending} onClick={() => gen.mutate()}>
                  <Sparkles className="h-4 w-4" /> {gen.isPending ? "Asking Claude…" : "Generate ideas with Claude"}
                </button>
              </div>
              {gen.error && <ErrorBox error={gen.error} title="Idea generation failed" />}
              {generated.length > 0 && (
                <ul className="space-y-2">
                  {generated.map((g, i) => (
                    <li key={i} className="flex gap-2 items-start card p-3">
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={g.picked}
                        aria-label={`Use idea ${g.title}`}
                        onChange={(e) => setGenerated((l) => l.map((x, j) => (j === i ? { ...x, picked: e.target.checked } : x)))}
                      />
                      <div className="flex-1 space-y-1">
                        <input className="input font-medium" value={g.title} onChange={(e) => setGenerated((l) => l.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))} />
                        <textarea className="input" rows={2} value={g.idea} onChange={(e) => setGenerated((l) => l.map((x, j) => (j === i ? { ...x, idea: e.target.value } : x)))} />
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <div className="mt-4 grid sm:grid-cols-2 gap-3">
            <Field label="Project">
              <Select
                value={projectId}
                onChange={setProjectId}
                options={[...(projects.data || []).map((p) => ({ id: p.id, name: p.name })), ...(projects.data?.length ? [] : [{ id: "", name: "“My videos” (created automatically)" }])]}
              />
            </Field>
          </div>
          <p className="subtle mt-3 flex items-center gap-1">
            <Lightbulb className="h-3 w-3" /> {ideas.length} video{ideas.length === 1 ? "" : "s"} will be created.
          </p>
        </Section>
      )}

      {step === 1 && (
        <Section title="Video type">
          {presets.isLoading ? (
            <Loading />
          ) : types.length ? (
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {types.map((t) => (
                <button key={t.id} type="button" onClick={() => applyType(t)} className={cx("card p-4 text-left hover:border-muted/40", config.video_type === t.id && "ring-2 ring-accent")}>
                  <div className="font-medium">{t.name}</div>
                  {t.description && <p className="text-xs text-muted mt-1">{t.description}</p>}
                  <div className="flex flex-wrap gap-1 mt-2">
                    {(t.engines || []).map((e) => (
                      <Badge key={e} tone={engineList.find((x) => x.id === e && isEngineUsable(x, { ...config, engines: [] })) ? "blue" : "muted"}>
                        {e}
                      </Badge>
                    ))}
                    {!(t.engines || []).length && <span className="subtle">no engine declared</span>}
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <Field label="Video type id" hint="No presets were returned by /api/presets.">
              <input className="input" value={config.video_type} onChange={(e) => set({ video_type: e.target.value })} />
            </Field>
          )}
        </Section>
      )}

      {step === 2 && (
        <Section title="Creative settings">
          <CreativeSettings config={config} set={set} presets={presets.data} engines={engineList} />
        </Section>
      )}

      {step === 3 && (
        <Section title="Engines">
          {engines.isLoading ? (
            <Loading />
          ) : (
            <EngineSettings
              config={config}
              set={set}
              mode={mode}
              setMode={setMode}
              primary={primary}
              setPrimary={setPrimary}
              engines={engineList}
              presets={presets.data}
              supportedByType={selectedType?.engines}
            />
          )}
        </Section>
      )}

      {step === 4 && (
        <Section title="Review & create">
          <div className="grid lg:grid-cols-2 gap-4">
            <div>
              <div className="label">Videos ({ideas.length})</div>
              <ul className="space-y-1.5 mb-4">
                {ideas.map((i, k) => (
                  <li key={k} className="rounded-lg bg-panel2 p-2">
                    <div className="font-medium text-sm">{i.title}</div>
                    <div className="text-xs text-muted line-clamp-2">{i.idea}</div>
                  </li>
                ))}
              </ul>
              <div className="label">Engines</div>
              <p className="text-sm mb-1">{mode === "collaborative" ? "Collaborative" : `Native: ${primary}`}</p>
              {mode === "collaborative" && (
                <div className="text-xs text-muted">
                  assets → {roles.assets.join(", ") || "—"} · narration → {roles.narration.join(", ") || "—"} · segments → {roles.segments.join(", ") || "—"}
                </div>
              )}
            </div>
            <div>
              <div className="label">Settings</div>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                {(
                  [
                    ["Type", selectedType?.name || config.video_type],
                    ["Style / tone", `${config.visual_style} / ${config.tone}`],
                    ["Duration", `${config.duration_seconds}s`],
                    ["Format", `${config.aspect_ratio} · ${config.resolution} · ${config.fps}fps`],
                    ["Language", `${config.language} (narration ${config.narration_language})`],
                    ["Voiceover", config.voiceover + (config.voice_engine ? ` · ${config.voice_engine}` : "")],
                    ["Captions", config.captions ? `${config.subtitle_languages.join(", ")}${config.burn_captions ? " · burned in" : ""}` : "off"],
                    ["Music / SFX", `${config.music ? "yes" : "no"} / ${config.sfx ? "yes" : "no"}`],
                    ["Approval", config.approval_mode],
                    ["Cost", config.zero_cost ? "Zero-cost" : `Paid allowed: ${config.approved_paid_providers.join(", ") || "none approved"}`],
                  ] as [string, string][]
                ).map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-muted">{k}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </dl>
              <label className="flex items-center gap-2 mt-4 text-sm">
                <input type="checkbox" checked={startNow} onChange={(e) => setStartNow(e.target.checked)} /> Start production immediately
              </label>
            </div>
          </div>
          {create.error && <div className="mt-3"><ErrorBox error={create.error} title="Could not create the video" /></div>}
          <div className="mt-4 flex justify-end">
            <button className="btn-primary" disabled={!ideas.length || create.isPending || !canNext[3]} onClick={() => create.mutate()}>
              <Rocket className="h-4 w-4" /> {create.isPending ? "Creating…" : startNow ? `Create & start ${ideas.length > 1 ? `${ideas.length} videos` : ""}` : "Create as draft"}
            </button>
          </div>
          {!canNext[3] && <p className="text-xs text-red-600 dark:text-red-300 mt-2 text-right">{explainEngineBlock(mode)}</p>}
        </Section>
      )}

      <div className="flex justify-between mt-4">
        <button className="btn" disabled={step === 0} onClick={() => setStep((s) => s - 1)}>
          <ArrowLeft className="h-4 w-4" /> Back
        </button>
        {step < STEPS.length - 1 && (
          <button className="btn-primary" disabled={!canNext[step]} onClick={() => setStep((s) => s + 1)}>
            Next <ArrowRight className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}

function explainEngineBlock(mode: string) {
  return mode === "native" ? "Choose an engine for native mode." : "No usable engine can render scenes — include an engine or check Repository Health.";
}
