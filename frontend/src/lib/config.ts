// VideoConfig defaults (mirror interchange/schema.py) and engine-role helpers.
import type { EngineInfo, VideoConfig } from "../types";

export const DEFAULT_CONFIG: VideoConfig = {
  video_type: "educational-explainer",
  visual_style: "cinematic",
  tone: "educational",
  duration_seconds: 60,
  language: "en",
  narration_language: "en",
  aspect_ratio: "16:9",
  resolution: "1920x1080",
  fps: 24,
  platforms: ["youtube"],
  voiceover: "generated",
  voice_engine: null,
  narration_voice: null,
  user_voice_file: null,
  captions: true,
  burn_captions: false,
  subtitle_languages: ["en"],
  music: true,
  sfx: true,
  pacing: "medium",
  scene_density: "medium",
  branding: {},
  target_audience: "general",
  references: [],
  budget_usd: 0,
  zero_cost: true,
  approved_paid_providers: [],
  approval_mode: "manual",
  auto_stages: [],
  engines: [],
  excluded_engines: [],
  engine_options: {},
  claude_model: null,
  claude_effort: "high",
  max_claude_budget_usd: null,
  brief: "",
};

export const FALLBACK_ASPECTS = [
  { id: "16:9", resolution: "1920x1080" },
  { id: "9:16", resolution: "1080x1920" },
  { id: "1:1", resolution: "1080x1080" },
  { id: "4:5", resolution: "1080x1350" },
  { id: "4:3", resolution: "1440x1080" },
];

export const FALLBACK_DURATIONS = [
  { id: "short", name: "30 s", seconds: 30 },
  { id: "1m", name: "1 min", seconds: 60 },
  { id: "3m", name: "3 min", seconds: 180 },
  { id: "5m", name: "5 min", seconds: 300 },
  { id: "10m", name: "10 min", seconds: 600 },
];

export const ROLE_CAPS: { role: string; capability: string; label: string }[] = [
  { role: "research", capability: "research", label: "Research & fact-check" },
  { role: "assets", capability: "asset_acquisition", label: "Asset acquisition" },
  { role: "narration", capability: "narration", label: "Narration (voice)" },
  { role: "segments", capability: "scene_render", label: "Scene segments" },
];

export function isEngineUsable(e: EngineInfo, cfg: Pick<VideoConfig, "engines" | "excluded_engines" | "zero_cost">): boolean {
  if (!e.enabled || e.approved === false) return false;
  if (cfg.excluded_engines?.includes(e.id)) return false;
  if (cfg.engines?.length && !cfg.engines.includes(e.id)) return false;
  if (cfg.zero_cost && e.cost && e.cost.zero_cost === false) return false;
  return true;
}

/**
 * Which engines are likely to handle each collaborative role.
 * Research is always SpaceStudio's main Claude lane; other roles go to usable engines that support the capability.
 */
export function likelyRoles(
  engines: EngineInfo[],
  cfg: Pick<VideoConfig, "engines" | "excluded_engines" | "zero_cost" | "voice_engine" | "voiceover">,
): Record<string, string[]> {
  const usable = engines.filter((e) => isEngineUsable(e, cfg));
  const supports = (cap: string) => usable.filter((e) => e.capabilities?.[cap]?.supported).map((e) => e.id);
  const out: Record<string, string[]> = {};
  for (const r of ROLE_CAPS) {
    if (r.role === "research") out[r.role] = ["spacestudio"];
    else if (r.role === "narration") {
      if (cfg.voiceover === "none") out[r.role] = [];
      else if (cfg.voiceover === "user") out[r.role] = ["your file"];
      else if (cfg.voice_engine && usable.some((e) => e.id === cfg.voice_engine)) out[r.role] = [cfg.voice_engine];
      else out[r.role] = supports(r.capability);
    } else out[r.role] = supports(r.capability);
  }
  return out;
}

/** "styles" → "style", "pipelines" → "pipeline", "looks" → "look". */
export function singularKey(k: string): string {
  if (k.endsWith("ies")) return k.slice(0, -3) + "y";
  if (k.endsWith("s")) return k.slice(0, -1);
  return k;
}

/** Remove keys equal to the defaults so overrides stay small. */
export function diffConfig(cfg: Partial<VideoConfig>, base: Partial<VideoConfig>): Partial<VideoConfig> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(cfg)) {
    if (JSON.stringify(v) !== JSON.stringify((base as Record<string, unknown>)[k])) out[k] = v;
  }
  return out as Partial<VideoConfig>;
}
