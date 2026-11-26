import { describe, expect, it } from "vitest";
import type { StageStatus } from "../types";
import { defaultFocus, laneLabel, lanesOf, orderStages, stageDepths } from "./timeline";

const st = (key: string, order: number, depends_on: string[] = [], lane = "main", status: StageStatus = "pending") => ({
  key,
  order,
  depends_on,
  lane,
  owner: "spacestudio",
  status,
});

describe("orderStages", () => {
  it("orders topologically, breaking ties by order", () => {
    const stages = [
      st("render", 9, ["preview"], "python"),
      st("segment:s2", 6, ["assets", "narration"], "engine:lemo"),
      st("segment:s1", 5, ["assets", "narration"], "engine:cinematic"),
      st("setup", 0, [], "python"),
      st("narration", 4, ["setup"], "engine:lemo"),
      st("assets", 3, ["setup"], "engine:cinematic"),
      st("preview", 8, ["segment:s1", "segment:s2"], "python"),
    ];
    expect(orderStages(stages).map((s) => s.key)).toEqual([
      "setup",
      "assets",
      "narration",
      "segment:s1",
      "segment:s2",
      "preview",
      "render",
    ]);
  });
  it("tolerates cycles and unknown deps", () => {
    const out = orderStages([st("a", 1, ["b"]), st("b", 0, ["a"]), st("c", 2, ["zzz"])]);
    expect(out.map((s) => s.key).sort()).toEqual(["a", "b", "c"]);
    expect(out[0].key).toBe("c");
  });
});

describe("stageDepths", () => {
  it("computes dependency depth", () => {
    const d = stageDepths([st("setup", 0), st("research", 1, ["setup"]), st("x", 2, ["setup", "research"])]);
    expect(d.get("setup")).toBe(0);
    expect(d.get("research")).toBe(1);
    expect(d.get("x")).toBe(2);
  });
});

describe("lanes", () => {
  it("sorts main, engines, python", () => {
    expect(lanesOf([{ lane: "python" }, { lane: "engine:lemo" }, { lane: "main" }, { lane: "engine:cinematic" }])).toEqual([
      "main",
      "engine:cinematic",
      "engine:lemo",
      "python",
    ]);
    expect(laneLabel("engine:lemo")).toBe("Engine · lemo");
  });
});

describe("defaultFocus", () => {
  it("prefers stages needing the user", () => {
    const s = [st("a", 0, [], "main", "completed"), st("b", 1, [], "main", "running"), st("c", 2, [], "main", "awaiting_approval")];
    expect(defaultFocus(s)?.key).toBe("c");
  });
});
