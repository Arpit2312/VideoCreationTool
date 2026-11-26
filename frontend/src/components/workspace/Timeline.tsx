import { useMemo, useState } from "react";
import { ArrowRight, Columns3, Rows3, ShieldCheck } from "lucide-react";
import type { Stage } from "../../types";
import { laneLabel, laneOf, lanesOf, orderStages, stageDepths } from "../../lib/timeline";
import { statusMeta, TONE_BORDER, TONE_SOLID } from "../../lib/status";
import { timeAgo } from "../../lib/format";
import { cx, EngineBadge, ProgressBar, StatusBadge } from "../ui";

function StageCard({ s, selected, related, onSelect }: { s: Stage; selected: boolean; related: "dep" | "child" | null; onSelect: () => void }) {
  const m = statusMeta(s.status);
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cx(
        "w-full text-left rounded-lg border-l-4 border bg-panel p-2.5 transition hover:bg-panel2",
        TONE_BORDER[m.tone],
        selected && "ring-2 ring-accent",
        related === "dep" && "outline outline-1 outline-dashed outline-sky-400",
        related === "child" && "outline outline-1 outline-dashed outline-fuchsia-400",
        m.attention && "shadow-[0_0_0_1px] shadow-current",
      )}
      aria-pressed={selected}
    >
      <div className="flex items-center gap-1.5">
        <span className={cx("h-2 w-2 rounded-full shrink-0", TONE_SOLID[m.tone], m.live && "animate-softpulse")} />
        <span className="text-sm font-medium truncate flex-1">{s.name}</span>
        {s.gate && <ShieldCheck className="h-3.5 w-3.5 text-amber-500 shrink-0" aria-label="Approval gate" />}
      </div>
      <div className="flex flex-wrap items-center gap-1 mt-1.5">
        <EngineBadge owner={s.owner} />
        {s.attempt > 1 && <span className="text-[10px] text-muted">try {s.attempt}</span>}
      </div>
      <div className="text-[11px] text-muted mt-1 truncate">
        {s.status === "running"
          ? s.last_activity || `Running · started ${timeAgo(s.started_at)}`
          : s.status === "needs_input"
            ? "Needs your input"
            : s.status === "awaiting_approval"
              ? "Waiting for approval"
              : s.status === "failed"
                ? s.error || "Failed"
                : m.label}
      </div>
      {s.status === "running" && typeof s.progress === "number" && (
        <div className="mt-1.5">
          <ProgressBar value={s.progress} />
        </div>
      )}
    </button>
  );
}

export function Timeline({ stages, selected, onSelect }: { stages: Stage[]; selected?: string; onSelect: (k: string) => void }) {
  const [layout, setLayout] = useState<"lanes" | "list">("lanes");
  const ordered = useMemo(() => orderStages(stages), [stages]);
  const depths = useMemo(() => stageDepths(stages), [stages]);
  const lanes = useMemo(() => lanesOf(stages), [stages]);
  const maxDepth = Math.max(0, ...Array.from(depths.values()));
  const sel = stages.find((s) => s.key === selected);
  const relation = (s: Stage): "dep" | "child" | null => {
    if (!sel || sel.key === s.key) return null;
    if (sel.depends_on?.includes(s.key)) return "dep";
    if (s.depends_on?.includes(sel.key)) return "child";
    return null;
  };

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="flex rounded-lg border border-line overflow-hidden">
          <button className={cx("btn-ghost btn-sm rounded-none", layout === "lanes" && "bg-panel2")} onClick={() => setLayout("lanes")} aria-label="Lane view">
            <Columns3 className="h-3.5 w-3.5" /> Lanes
          </button>
          <button className={cx("btn-ghost btn-sm rounded-none", layout === "list" && "bg-panel2")} onClick={() => setLayout("list")} aria-label="List view">
            <Rows3 className="h-3.5 w-3.5" /> List
          </button>
        </div>
        <Legend />
        {sel && (
          <span className="text-[11px] text-muted ml-auto">
            <span className="text-sky-500">dashed blue</span> = depends on · <span className="text-fuchsia-500">dashed pink</span> = waits for this
          </span>
        )}
      </div>

      {layout === "lanes" ? (
        <div className="overflow-x-auto pb-2 -mx-1 px-1">
          <div className="grid gap-2" style={{ gridTemplateColumns: `140px repeat(${maxDepth + 1}, minmax(170px, 1fr))`, minWidth: 140 + (maxDepth + 1) * 180 }}>
            {lanes.map((lane) => (
              <div key={lane} className="contents">
                <div className="text-xs text-muted py-2 pr-2 sticky left-0 bg-bg z-10 flex items-start">
                  <span className={cx("rounded px-1.5 py-0.5", lane.startsWith("engine:") ? "bg-blue-500/10" : "bg-panel2")}>{laneLabel(lane)}</span>
                </div>
                {Array.from({ length: maxDepth + 1 }).map((_, col) => {
                  const cell = ordered.filter((s) => laneOf(s) === lane && (depths.get(s.key) ?? 0) === col);
                  return (
                    <div key={col} className="space-y-2 min-h-[1px]">
                      {cell.map((s) => (
                        <StageCard key={s.key} s={s} selected={s.key === selected} related={relation(s)} onSelect={() => onSelect(s.key)} />
                      ))}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <ol className="space-y-1.5">
          {ordered.map((s, i) => (
            <li key={s.key} className="flex gap-2 items-stretch">
              <div className="w-6 text-right text-xs text-muted pt-3 tabular-nums">{i + 1}</div>
              <div className="flex-1">
                <StageCard s={s} selected={s.key === selected} related={relation(s)} onSelect={() => onSelect(s.key)} />
                {s.depends_on?.length > 0 && (
                  <div className="text-[10px] text-muted mt-0.5 flex items-center gap-1 pl-1">
                    after <ArrowRight className="h-2.5 w-2.5" /> {s.depends_on.join(", ")} · lane {laneLabel(laneOf(s))}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-1.5">
      {["pending", "queued", "running", "awaiting_approval", "needs_input", "completed", "failed", "skipped"].map((s) => (
        <StatusBadge key={s} status={s} />
      ))}
    </div>
  );
}
