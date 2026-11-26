// Pure helpers for the production timeline (ordering, lanes, dependency depth).
import type { Stage } from "../types";

type S = Pick<Stage, "key" | "order" | "depends_on" | "lane" | "owner">;

/**
 * Order stages topologically by depends_on; ties (and cycles) fall back to `order`, then key.
 * Unknown dependencies are ignored.
 */
export function orderStages<T extends S>(stages: T[]): T[] {
  const byKey = new Map(stages.map((s) => [s.key, s]));
  const cmp = (a: T, b: T) => (a.order ?? 0) - (b.order ?? 0) || a.key.localeCompare(b.key, undefined, { numeric: true });
  const indeg = new Map<string, number>();
  const children = new Map<string, T[]>();
  for (const s of stages) {
    const deps = (s.depends_on || []).filter((d) => byKey.has(d) && d !== s.key);
    indeg.set(s.key, deps.length);
    for (const d of deps) {
      const arr = children.get(d) || [];
      arr.push(s);
      children.set(d, arr);
    }
  }
  const ready = stages.filter((s) => indeg.get(s.key) === 0).sort(cmp);
  const out: T[] = [];
  const seen = new Set<string>();
  while (ready.length) {
    const s = ready.shift()!;
    if (seen.has(s.key)) continue;
    seen.add(s.key);
    out.push(s);
    for (const c of children.get(s.key) || []) {
      const n = (indeg.get(c.key) || 0) - 1;
      indeg.set(c.key, n);
      if (n === 0) {
        ready.push(c);
        ready.sort(cmp);
      }
    }
  }
  // cycles / leftovers
  const rest = stages.filter((s) => !seen.has(s.key)).sort(cmp);
  return out.concat(rest);
}

/** Depth (column) of each stage in the dependency graph: 0 for roots. */
export function stageDepths<T extends S>(stages: T[]): Map<string, number> {
  const byKey = new Map(stages.map((s) => [s.key, s]));
  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  const visit = (k: string): number => {
    if (depth.has(k)) return depth.get(k)!;
    if (visiting.has(k)) return 0;
    visiting.add(k);
    const s = byKey.get(k);
    let d = 0;
    for (const dep of s?.depends_on || []) if (byKey.has(dep) && dep !== k) d = Math.max(d, visit(dep) + 1);
    visiting.delete(k);
    depth.set(k, d);
    return d;
  };
  for (const s of stages) visit(s.key);
  return depth;
}

/** Normalised lane id: "main", "engine:<id>" or "python". */
export function laneOf(s: Pick<Stage, "lane">): string {
  return s.lane || "main";
}

/** Lanes in display order: main, then engine lanes alphabetically, then python, then others. */
export function lanesOf(stages: Pick<Stage, "lane">[]): string[] {
  const set = new Set(stages.map(laneOf));
  const rank = (l: string) => (l === "main" ? 0 : l.startsWith("engine:") ? 1 : l === "python" ? 2 : 3);
  return [...set].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

export function laneLabel(lane: string): string {
  if (lane === "main") return "Main (Claude)";
  if (lane === "python") return "SpaceStudio (Python)";
  if (lane.startsWith("engine:")) return `Engine · ${lane.slice(7)}`;
  return lane;
}

/** The stage to focus by default: first needing attention, else running, else first not done. */
export function defaultFocus<T extends Pick<Stage, "key" | "status">>(ordered: T[]): T | undefined {
  return (
    ordered.find((s) => s.status === "needs_input" || s.status === "awaiting_approval") ||
    ordered.find((s) => s.status === "failed") ||
    ordered.find((s) => s.status === "running") ||
    ordered.find((s) => s.status !== "completed" && s.status !== "skipped") ||
    ordered[ordered.length - 1]
  );
}
