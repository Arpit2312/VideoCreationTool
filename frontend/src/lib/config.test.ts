import { describe, expect, it } from "vitest";
import { DEFAULT_CONFIG, diffConfig, likelyRoles, singularKey } from "./config";
import type { EngineInfo } from "../types";

const eng = (id: string, caps: string[], zero = true, enabled = true): EngineInfo => ({
  id,
  name: id,
  description: "",
  license: "",
  enabled,
  approved: true,
  path: "",
  capabilities: Object.fromEntries(caps.map((c) => [c, { supported: true }])),
  options: {},
  cost: { zero_cost: zero, paid_env: [] },
  health: null,
  setup_steps: [],
});

describe("likelyRoles", () => {
  const engines = [
    eng("cinematic-skill", ["asset_acquisition", "scene_render", "narration"]),
    eng("lemo-opuscar", ["narration", "scene_render"]),
    eng("openmontage", ["asset_acquisition", "scene_render"], false),
    eng("off", ["scene_render"], true, false),
  ];
  it("assigns roles from capabilities, honouring zero-cost and enabled", () => {
    const r = likelyRoles(engines, { ...DEFAULT_CONFIG });
    expect(r.research).toEqual(["spacestudio"]);
    expect(r.assets).toEqual(["cinematic-skill"]);
    expect(r.segments).toEqual(["cinematic-skill", "lemo-opuscar"]);
  });
  it("honours voice engine, exclusions and paid mode", () => {
    const r = likelyRoles(engines, { ...DEFAULT_CONFIG, zero_cost: false, voice_engine: "lemo-opuscar", excluded_engines: ["cinematic-skill"] });
    expect(r.narration).toEqual(["lemo-opuscar"]);
    expect(r.assets).toEqual(["openmontage"]);
  });
  it("no narration when voiceover is none", () => {
    expect(likelyRoles(engines, { ...DEFAULT_CONFIG, voiceover: "none" }).narration).toEqual([]);
  });
});

describe("misc", () => {
  it("singularKey", () => {
    expect(singularKey("styles")).toBe("style");
    expect(singularKey("categories")).toBe("category");
    expect(singularKey("look")).toBe("look");
  });
  it("diffConfig keeps only changed keys", () => {
    expect(diffConfig({ tone: "dramatic", fps: 24 }, DEFAULT_CONFIG)).toEqual({ tone: "dramatic" });
  });
});
